# RateGuard AI -- Candidate Deployment Helper Library
#
# Pure/testable helper functions used by infrastructure/deploy_candidate.ps1.
# Dot-sourced by the deploy script and by infrastructure/tests/*.Tests.ps1 so
# the parsing/validation/construction logic can be exercised offline, with no
# gcloud/bq/gsutil calls at all.
#
# Nothing in this file prints command output or environment-variable values.
# Callers are responsible for what (if anything) they choose to print.
#
# Deliberately no `Set-StrictMode`: Cloud Run traffic entries are
# heterogeneous JSON objects (the 100%-traffic entry has no .tag/.url, the
# candidate-tag entry has no .percent, etc.) -- PSCustomObject's default
# "missing property reads as $null" behavior is exactly what the parsing
# functions below rely on, and StrictMode -Version Latest would turn every
# normal, well-formed Cloud Run response into a PropertyNotFoundException.

function Invoke-NativeCommand {
    <#
    Runs a native executable and treats any nonzero exit code as fatal,
    regardless of $ErrorActionPreference (which native executables do not
    reliably honor). Always inspects $LASTEXITCODE immediately after the
    call, before any other statement can overwrite it.

    Never merges stderr into a captured output stream: in Windows
    PowerShell 5.1, `2>&1` on a native command wraps each stderr line in a
    NativeCommandError record and flips $?, even on a genuine exit code 0.
    stderr is left to stream directly to the console instead.

    Output-pollution guard: without -CaptureOutput, the native command's
    stdout is explicitly piped to Out-Null and this function executes NO
    `return` statement at all -- it produces zero objects on its own output
    stream. This matters because PowerShell functions implicitly emit
    everything not otherwise consumed: an unassigned/bare-statement call
    like `Invoke-NativeCommand -Operation X -FilePath gcloud -ArgumentList
    @(...)` (no -CaptureOutput, result not assigned) previously leaked
    gcloud's own stdout -- and even a bare `return $null` -- straight into
    the CALLING function's return value, silently turning a single expected
    string (e.g. a discovered Cloud Run URL) into a multi-element
    System.Object[]. Binding a System.Object[] (even one element) to a
    downstream `[string]` parameter fails with "Cannot process argument
    transformation ... Cannot convert value to type System.String." -- this
    is exactly the WebUrl postcondition failure this guard fixes.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [switch]$CaptureOutput
    )

    if ($CaptureOutput) {
        $output = & $FilePath @ArgumentList
    } else {
        & $FilePath @ArgumentList | Out-Null
    }

    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }

    if ($exitCode -ne 0) {
        throw "Native command failed: $Operation (exit code $exitCode)"
    }

    if ($CaptureOutput) { return $output }
}

function ConvertFrom-CloudRunServiceJson {
    <#
    Parses the JSON text produced by
    `gcloud run services describe SERVICE --format=json(...)`. Accepts
    either a single string or an array of lines (as returned by
    Invoke-NativeCommand -CaptureOutput) and raises a clear error if the
    text is not valid JSON, rather than letting a human-formatted-output
    regression fail silently downstream.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowNull()]$JsonLines,
        [Parameter(Mandatory = $true)][string]$ServiceName
    )
    $text = if ($JsonLines -is [array]) { $JsonLines -join "`n" } else { [string]$JsonLines }
    if ([string]::IsNullOrWhiteSpace($text)) {
        throw "Empty response describing Cloud Run service '$ServiceName'; expected JSON."
    }
    try {
        return $text | ConvertFrom-Json
    } catch {
        throw "Failed to parse JSON describing Cloud Run service '$ServiceName': $($_.Exception.Message)"
    }
}

function Get-CandidateTaggedUrl {
    <#
    Finds the traffic/status entry whose .tag equals the given tag and
    returns its .url. Returns $null (never throws) when the tag is absent
    -- callers decide whether a missing candidate tag is fatal. Never
    parses human-formatted `gcloud ... ` table output.
    #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$Tag
    )
    $traffic = $ServiceInfo.status.traffic
    if (-not $traffic) { return $null }
    $match = @($traffic) | Where-Object { $_.tag -eq $Tag } | Select-Object -First 1
    if (-not $match -or -not $match.url) { return $null }
    return [string]$match.url
}

function Test-CandidateTaggedUrlExists {
    <# Boolean, non-throwing check for "does at least one traffic entry
    carry this tag at all" -- used to distinguish "not deployed yet"
    (legitimately normal, e.g. during -ResumeCandidate's frontend check)
    from "deployed but something about it is wrong" (which
    Get-SingleCandidateTaggedUrl below reports precisely, by throwing). #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$Tag
    )
    $traffic = $ServiceInfo.status.traffic
    if (-not $traffic) { return $false }
    return (@(@($traffic) | Where-Object { $_.tag -eq $Tag })).Count -gt 0
}

function Get-SingleCandidateTaggedUrl {
    <#
    Strict candidate-tagged URL discovery. Guarantees the return value is
    exactly one nonempty scalar [string] or the function throws -- it never
    returns $null, an array, or a malformed URL. Validates, in order:
      - exactly one traffic entry carries the tag (zero or more than one is
        reported precisely rather than silently picking the first);
      - the URL is present, absolute, and HTTPS;
      - the URL's host belongs to the named service;
      - the URL's host begins with the candidate-tag hostname form
        ("candidate---...");
      - the tagged entry itself carries no production traffic (0%).
    #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $true)][string]$ServiceHostFragment
    )
    $traffic = $ServiceInfo.status.traffic
    $matches = @()
    if ($traffic) { $matches = @(@($traffic) | Where-Object { $_.tag -eq $Tag }) }

    if ($matches.Count -eq 0) {
        throw "No traffic entry with tag='$Tag' was found for the '$ServiceHostFragment' service."
    }
    if ($matches.Count -gt 1) {
        throw "$($matches.Count) traffic entries with tag='$Tag' were found for the '$ServiceHostFragment' service; expected exactly one. Refusing to guess which is authoritative."
    }

    $entry = $matches[0]
    $url = if ($entry.url) { [string]$entry.url } else { $null }

    if (-not (Test-AbsoluteHttpsUrl -Url $url -RequiredHostFragment $ServiceHostFragment -RequireCandidateTagPrefix)) {
        throw "The tag='$Tag' traffic entry for '$ServiceHostFragment' does not have a valid candidate URL (must be absolute HTTPS, on a 'candidate---' host, containing '$ServiceHostFragment'). Got: '$url'"
    }

    if ($entry.percent -and [int]$entry.percent -gt 0) {
        throw "The tag='$Tag' traffic entry for '$ServiceHostFragment' carries $($entry.percent)% production traffic; expected 0%."
    }

    return [string]$url
}

function Get-UntaggedServiceUrl {
    <# The base (untagged) Cloud Run service URL -- required as the Pub/Sub
    OIDC audience even when the push endpoint targets a specific tag. #>
    param([Parameter(Mandatory = $true)]$ServiceInfo)
    if ($ServiceInfo.status.url) { return [string]$ServiceInfo.status.url }
    if ($ServiceInfo.status.address -and $ServiceInfo.status.address.url) {
        return [string]$ServiceInfo.status.address.url
    }
    return $null
}

function Get-DeployedImageReference {
    <# The image reference (registry/repo:tag) baked into the service's
    current revision template. Used for backend image parity checks. #>
    param([Parameter(Mandatory = $true)]$ServiceInfo)
    $containers = $ServiceInfo.spec.template.spec.containers
    if (-not $containers) { return $null }
    $first = @($containers) | Select-Object -First 1
    if (-not $first) { return $null }
    return [string]$first.image
}

function Get-ServiceEnvVar {
    <# Looks up one env var by name from a service's container env array,
    without ever printing its value. Returns $null if absent. #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $containers = $ServiceInfo.spec.template.spec.containers
    if (-not $containers) { return $null }
    $first = @($containers) | Select-Object -First 1
    if (-not $first -or -not $first.env) { return $null }
    $entry = @($first.env) | Where-Object { $_.name -eq $Name } | Select-Object -First 1
    if (-not $entry) { return $null }
    return $entry.value
}

function Test-AbsoluteHttpsUrl {
    <#
    Validates a discovered URL is: nonempty, an absolute URI, HTTPS, and
    (optionally) has a host containing the expected service-name fragment
    and/or the "candidate---" tag prefix. Uses System.Uri, never a regex
    guess.
    #>
    param(
        [AllowEmptyString()][AllowNull()][string]$Url,
        [string]$RequiredHostFragment = $null,
        [switch]$RequireCandidateTagPrefix
    )
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }

    $uri = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$uri)) { return $false }
    if ($uri.Scheme -ne 'https') { return $false }
    if ([string]::IsNullOrWhiteSpace($uri.Host)) { return $false }
    if ($RequiredHostFragment -and ($uri.Host -notlike "*$RequiredHostFragment*")) { return $false }
    if ($RequireCandidateTagPrefix -and ($uri.Host -notlike 'candidate---*')) { return $false }
    return $true
}

function New-PubSubPushEndpoint {
    <# The push endpoint is always the candidate WORKER TAGGED URL plus the
    fixed internal path -- never the untagged worker URL, never the API
    URL. #>
    param([Parameter(Mandatory = $true)][string]$CandidateWorkerTaggedUrl)
    if (-not (Test-AbsoluteHttpsUrl -Url $CandidateWorkerTaggedUrl)) {
        throw "New-PubSubPushEndpoint: CandidateWorkerTaggedUrl is not an absolute https URL."
    }
    return "$($CandidateWorkerTaggedUrl.TrimEnd('/'))/internal/pubsub/assurance"
}

function Get-PubSubOidcAudience {
    <#
    Cloud Run requires the OIDC token audience to be the plain service URL,
    even when the push endpoint targets a specific traffic tag. Returning
    the tag URL, or the URL with the endpoint path appended, produces a
    token Cloud Run will reject. This function returns the untagged
    service URL, unmodified except for a trailing slash.
    #>
    param([Parameter(Mandatory = $true)][string]$UntaggedWorkerServiceUrl)
    if (-not (Test-AbsoluteHttpsUrl -Url $UntaggedWorkerServiceUrl)) {
        throw "Get-PubSubOidcAudience: UntaggedWorkerServiceUrl is not an absolute https URL."
    }
    if ($UntaggedWorkerServiceUrl -like '*---*') {
        throw "Get-PubSubOidcAudience: refusing to use a traffic-tag URL ('---') as the OIDC audience."
    }
    if ($UntaggedWorkerServiceUrl -like '*/internal/pubsub*') {
        throw "Get-PubSubOidcAudience: refusing to use a URL containing the endpoint path as the OIDC audience."
    }
    return $UntaggedWorkerServiceUrl.TrimEnd('/')
}

function New-FrontendSubstitutionsArg {
    <#
    Builds the single comma-separated --substitutions value Cloud Build
    expects. PowerShell has no bare-word array-splitting hazard here
    because this returns one scalar string; the caller must pass it as one
    argument (e.g. "--substitutions=$value"), never as several separate
    array elements.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ImageTag,
        [Parameter(Mandatory = $true)][string]$CandidateApiUrl
    )
    if ([string]::IsNullOrWhiteSpace($ImageTag)) {
        throw "New-FrontendSubstitutionsArg: ImageTag must be nonempty."
    }
    if (-not (Test-AbsoluteHttpsUrl -Url $CandidateApiUrl)) {
        throw "New-FrontendSubstitutionsArg: CandidateApiUrl must be an absolute https URL."
    }
    $substitutions = "_IMAGE_TAG=$ImageTag,_NEXT_PUBLIC_RATEGUARD_API_URL=$CandidateApiUrl"
    if ($substitutions -match '\s') {
        throw "New-FrontendSubstitutionsArg: generated substitutions string contains whitespace."
    }
    if (-not (Test-FrontendSubstitutionsShape -Substitutions $substitutions)) {
        throw "New-FrontendSubstitutionsArg: generated substitutions string does not contain exactly the two expected keys."
    }
    return $substitutions
}

function Test-FrontendSubstitutionsShape {
    <# Exactly two comma-separated KEY=VALUE parts, the expected two keys,
    in the expected order. #>
    param([Parameter(Mandatory = $true)][string]$Substitutions)
    $parts = $Substitutions -split ','
    if ($parts.Count -ne 2) { return $false }
    if ($parts[0] -notmatch '^_IMAGE_TAG=.+$') { return $false }
    if ($parts[1] -notmatch '^_NEXT_PUBLIC_RATEGUARD_API_URL=.+$') { return $false }
    return $true
}

function Test-ImageReferenceNoSpaces {
    param([Parameter(Mandatory = $true)][string]$ImageReference)
    if ([string]::IsNullOrWhiteSpace($ImageReference)) { return $false }
    return ($ImageReference -notmatch '\s')
}

function Test-CandidateImageTagFormat {
    <#
    Strict format for an EXISTING candidate application image tag supplied
    to -ResumeCandidate: candidate-<12 lowercase hex chars>, matching
    exactly what `git rev-parse --short=12` + the "candidate-" prefix
    produces. Deliberately strict -- this tag selects which already-built
    image gets treated as authoritative during resume, so a loosely
    validated value could let an arbitrary/unbuilt image reference bypass
    the image-existence and revision-parity checks entirely.
    #>
    param([AllowEmptyString()][AllowNull()][string]$Tag)
    if ([string]::IsNullOrWhiteSpace($Tag)) { return $false }
    return ($Tag -cmatch '^candidate-[0-9a-f]{12}$')
}

function New-CandidateBackendImageReference {
    param(
        [Parameter(Mandatory = $true)][string]$Region,
        [Parameter(Mandatory = $true)][string]$ProjectId,
        [Parameter(Mandatory = $true)][string]$ImageTag
    )
    return "$Region-docker.pkg.dev/$ProjectId/rateguard/rateguard-api:$ImageTag"
}

function New-CandidateFrontendImageReference {
    param(
        [Parameter(Mandatory = $true)][string]$Region,
        [Parameter(Mandatory = $true)][string]$ProjectId,
        [Parameter(Mandatory = $true)][string]$ImageTag
    )
    return "$Region-docker.pkg.dev/$ProjectId/rateguard/rateguard-web:$ImageTag"
}

function Test-CandidateEnvIsolation {
    <#
    Compares a service's container env array against the expected staging
    key/value pairs. Returns an array of human-readable failure reasons
    (empty array = fully isolated). Failure messages name the KEY only,
    never the actual value on either side, so this is safe to print even
    though none of these particular keys are secret.
    #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$ServiceLabel,
        [Parameter(Mandatory = $true)][hashtable]$ExpectedStagingValues
    )
    $failures = @()
    foreach ($key in $ExpectedStagingValues.Keys) {
        $actual = Get-ServiceEnvVar -ServiceInfo $ServiceInfo -Name $key
        if ($null -eq $actual) {
            $failures += "${ServiceLabel}: $key is not set"
        } elseif ($actual -ne $ExpectedStagingValues[$key]) {
            $failures += "${ServiceLabel}: $key does not reference the isolated staging resource"
        }
    }
    return $failures
}

function Get-CandidateCorsOrigins {
    <#
    Derives the candidate API's CORS allow-list from the production origins
    already declared in infrastructure/runtime-env.yaml's
    RATEGUARD_CORS_ORIGINS: every production origin is kept unchanged (never
    replaced, never wildcarded), and one candidate origin is added per
    non-localhost production origin by inserting the Cloud Run traffic-tag
    prefix ("<tag>---") ahead of the host -- exactly how Cloud Run forms a
    --tag candidate's own URL from the service's base URL (e.g.
    "https://rateguard-web-iqofutwtva-uc.a.run.app" ->
    "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"). Pure
    function: takes the already-parsed production origins list, returns the
    candidate list; no file I/O, no gcloud/network call. Mirrors
    infrastructure/deploy_candidate.sh's get_candidate_cors_origins().
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$ProductionOrigins,
        [Parameter(Mandatory = $true)][string]$CandidateTag
    )
    $result = [System.Collections.Generic.List[string]]::new()
    foreach ($origin in $ProductionOrigins) { $result.Add($origin) | Out-Null }
    foreach ($origin in $ProductionOrigins) {
        if ($origin -notmatch '^(https?)://(.+)$') { continue }
        $scheme = $Matches[1]
        $hostPart = $Matches[2]
        if ($hostPart.StartsWith('localhost') -or $hostPart.StartsWith('127.')) { continue }
        $candidateOrigin = "${scheme}://${CandidateTag}---${hostPart}"
        if (-not $result.Contains($candidateOrigin)) { $result.Add($candidateOrigin) | Out-Null }
    }
    # Plain return (no leading comma): matches this file's established
    # list-returning convention (see Test-CandidateEnvIsolation's $failures)
    # -- callers wrap the call in @(...) to collect it back into one array.
    return $result.ToArray()
}

function ConvertTo-CompactJsonStringArray {
    <#
    Builds a compact JSON array-of-strings literal without relying on
    ConvertTo-Json's pipeline-unwrapping behavior for single-element arrays
    (Windows PowerShell 5.1 has no -AsArray switch). Values here are always
    simple https:// origins, so only '"' and '\' need escaping.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Values)
    $escaped = @($Values | ForEach-Object { '"' + ($_ -replace '\\', '\\\\' -replace '"', '\"') + '"' })
    return '[' + ($escaped -join ',') + ']'
}

function Test-ProductionTrafficUnchanged {
    <#
    Confirms the service's 100%-traffic entry (if any) is not the
    candidate tag -- i.e. the candidate revision is receiving 0% traffic.
    A service with no 100% entry yet (never deployed) is treated as
    "unchanged" (nothing to protect).
    #>
    param(
        [Parameter(Mandatory = $true)]$ServiceInfo,
        [Parameter(Mandatory = $true)][string]$CandidateTag
    )
    $traffic = $ServiceInfo.status.traffic
    if (-not $traffic) { return $true }
    $fullTraffic = @($traffic) | Where-Object { $_.percent -eq 100 }
    foreach ($entry in $fullTraffic) {
        if ($entry.tag -eq $CandidateTag) { return $false }
    }
    return $true
}
