# Focused, offline tests for infrastructure/lib/CandidateDeployLib.ps1 and the
# orchestration functions in infrastructure/deploy_candidate.ps1.
#
# No gcloud/bq/gsutil/network calls anywhere in this file. Cloud Run JSON
# fixtures below mirror the exact shape returned by a real
# `gcloud run services describe --format=json(...)` against this project's
# rateguard-api / rateguard-worker / rateguard-web services (captured
# read-only while diagnosing the partial candidate deployment).
#
# Run with: Invoke-Pester infrastructure/tests/CandidateDeployLib.Tests.ps1

$libPath = Join-Path $PSScriptRoot "..\lib\CandidateDeployLib.ps1"
. $libPath

$deployScriptPath = Join-Path $PSScriptRoot "..\deploy_candidate.ps1"

function New-ApiServiceInfoWithCandidateTag {
    '{
      "status": {
        "url": "https://rateguard-api-iqofutwtva-uc.a.run.app",
        "traffic": [
          { "percent": 100, "revisionName": "rateguard-api-00015-4tt" },
          { "revisionName": "rateguard-api-00016-vov", "tag": "candidate", "url": "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app" }
        ]
      },
      "spec": { "template": { "spec": { "containers": [ { "image": "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949", "env": [ { "name": "RATEGUARD_PUBSUB_TOPIC", "value": "assurance-runs-staging" } ] } ] } } }
    }' | ConvertFrom-Json
}

function New-WorkerServiceInfoWithCandidateTag {
    '{
      "status": {
        "url": "https://rateguard-worker-iqofutwtva-uc.a.run.app",
        "traffic": [
          { "percent": 100, "revisionName": "rateguard-worker-00008-w2p" },
          { "revisionName": "rateguard-worker-00009-luq", "tag": "candidate", "url": "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app" }
        ]
      },
      "spec": { "template": { "spec": { "containers": [ { "image": "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949" } ] } } }
    }' | ConvertFrom-Json
}

function New-WebServiceInfoNoCandidateTag {
    '{
      "status": {
        "url": "https://rateguard-web-iqofutwtva-uc.a.run.app",
        "traffic": [
          { "latestRevision": true, "percent": 100, "revisionName": "rateguard-web-00008-9v2" }
        ]
      }
    }' | ConvertFrom-Json
}

function New-WebServiceInfoWithCandidateTag {
    '{
      "status": {
        "url": "https://rateguard-web-iqofutwtva-uc.a.run.app",
        "traffic": [
          { "percent": 100, "revisionName": "rateguard-web-00008-9v2" },
          { "revisionName": "rateguard-web-00009-abc", "tag": "candidate", "url": "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" }
        ]
      }
    }' | ConvertFrom-Json
}

Describe "Get-CandidateTaggedUrl" {

    It "extracts the tagged URL from a realistic Cloud Run JSON describe" {
        $info = New-ApiServiceInfoWithCandidateTag
        $url = Get-CandidateTaggedUrl -ServiceInfo $info -Tag "candidate"
        $url | Should Be "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
    }

    It "returns null when no traffic entry carries the requested tag (missing tag)" {
        $info = New-WebServiceInfoNoCandidateTag
        $url = Get-CandidateTaggedUrl -ServiceInfo $info -Tag "candidate"
        $url | Should Be $null
    }

    It "a missing tag URL fails absolute-https validation" {
        $info = New-WebServiceInfoNoCandidateTag
        $url = Get-CandidateTaggedUrl -ServiceInfo $info -Tag "candidate"
        (Test-AbsoluteHttpsUrl -Url $url -RequiredHostFragment "rateguard-web" -RequireCandidateTagPrefix) | Should Be $false
    }

    It "extracts the untagged base URL separately from the tagged URL" {
        $info = New-WorkerServiceInfoWithCandidateTag
        (Get-UntaggedServiceUrl -ServiceInfo $info) | Should Be "https://rateguard-worker-iqofutwtva-uc.a.run.app"
    }
}

Describe "Test-AbsoluteHttpsUrl" {

    It "rejects an empty URL" { Test-AbsoluteHttpsUrl -Url "" | Should Be $false }
    It "rejects a null URL" { Test-AbsoluteHttpsUrl -Url $null | Should Be $false }
    It "rejects a relative path" { Test-AbsoluteHttpsUrl -Url "/internal/pubsub/assurance" | Should Be $false }
    It "rejects a plain http URL" { Test-AbsoluteHttpsUrl -Url "http://candidate---rateguard-api-iqofutwtva-uc.a.run.app" | Should Be $false }
    It "accepts a well-formed https candidate URL" {
        Test-AbsoluteHttpsUrl -Url "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app" -RequiredHostFragment "rateguard-api" -RequireCandidateTagPrefix | Should Be $true
    }
    It "rejects a URL missing the required candidate tag prefix" {
        Test-AbsoluteHttpsUrl -Url "https://rateguard-api-iqofutwtva-uc.a.run.app" -RequireCandidateTagPrefix | Should Be $false
    }
    It "rejects a URL whose host does not contain the required service fragment" {
        Test-AbsoluteHttpsUrl -Url "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app" -RequiredHostFragment "rateguard-api" | Should Be $false
    }
}

Describe "Pub/Sub endpoint + OIDC audience construction" {

    It "builds the push endpoint from the candidate worker TAGGED url plus the fixed path" {
        $endpoint = New-PubSubPushEndpoint -CandidateWorkerTaggedUrl "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
        $endpoint | Should Be "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app/internal/pubsub/assurance"
    }

    It "throws when building a push endpoint from an invalid worker URL" {
        { New-PubSubPushEndpoint -CandidateWorkerTaggedUrl "" } | Should Throw
    }

    It "uses the UNTAGGED worker service URL as the OIDC audience" {
        $audience = Get-PubSubOidcAudience -UntaggedWorkerServiceUrl "https://rateguard-worker-iqofutwtva-uc.a.run.app"
        $audience | Should Be "https://rateguard-worker-iqofutwtva-uc.a.run.app"
    }

    It "refuses a traffic-TAG url as the OIDC audience" {
        { Get-PubSubOidcAudience -UntaggedWorkerServiceUrl "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app" } | Should Throw
    }

    It "refuses a url containing the endpoint path as the OIDC audience" {
        { Get-PubSubOidcAudience -UntaggedWorkerServiceUrl "https://rateguard-worker-iqofutwtva-uc.a.run.app/internal/pubsub/assurance" } | Should Throw
    }
}

Describe "Frontend Cloud Build substitutions" {

    It "builds one comma-separated substitutions string with exactly the two expected keys" {
        $subs = New-FrontendSubstitutionsArg -ImageTag "candidate-23cfc95b5949" -CandidateApiUrl "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
        $subs | Should Be "_IMAGE_TAG=candidate-23cfc95b5949,_NEXT_PUBLIC_RATEGUARD_API_URL=https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
        (Test-FrontendSubstitutionsShape -Substitutions $subs) | Should Be $true
    }

    It "throws on an empty image tag" {
        { New-FrontendSubstitutionsArg -ImageTag "" -CandidateApiUrl "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app" } | Should Throw
    }

    It "throws when the candidate API URL is not an absolute https URL" {
        { New-FrontendSubstitutionsArg -ImageTag "candidate-23cfc95b5949" -CandidateApiUrl "not-a-url" } | Should Throw
    }

    It "rejects a substitutions string that is not exactly the two expected keys" {
        (Test-FrontendSubstitutionsShape -Substitutions "_IMAGE_TAG=x") | Should Be $false
        (Test-FrontendSubstitutionsShape -Substitutions "_IMAGE_TAG=x,_NEXT_PUBLIC_RATEGUARD_API_URL=y,_EXTRA=z") | Should Be $false
        (Test-FrontendSubstitutionsShape -Substitutions "_WRONG_KEY=x,_NEXT_PUBLIC_RATEGUARD_API_URL=y") | Should Be $false
    }

    It "the generated image reference contains no spaces" {
        $image = "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949"
        (Test-ImageReferenceNoSpaces -ImageReference $image) | Should Be $true
    }

    It "detects a space-corrupted image reference" {
        (Test-ImageReferenceNoSpaces -ImageReference "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web candidate-23cfc95b5949") | Should Be $false
    }
}

Describe "Test-CandidateImageTagFormat" {

    It "accepts a well-formed candidate image tag" {
        Test-CandidateImageTagFormat -Tag "candidate-23cfc95b5949" | Should Be $true
    }

    It "rejects an empty or null tag" {
        Test-CandidateImageTagFormat -Tag "" | Should Be $false
        Test-CandidateImageTagFormat -Tag $null | Should Be $false
    }

    It "rejects a tag missing the candidate- prefix" {
        Test-CandidateImageTagFormat -Tag "23cfc95b5949" | Should Be $false
    }

    It "rejects a tag with too few or too many hex characters" {
        Test-CandidateImageTagFormat -Tag "candidate-23cfc95b594" | Should Be $false
        Test-CandidateImageTagFormat -Tag "candidate-23cfc95b59499" | Should Be $false
    }

    It "rejects a tag with uppercase or non-hex characters" {
        Test-CandidateImageTagFormat -Tag "candidate-23CFC95B5949" | Should Be $false
        Test-CandidateImageTagFormat -Tag "candidate-23cfc95b594g" | Should Be $false
    }

    It "rejects an arbitrary/unrelated string masquerading as a tag" {
        Test-CandidateImageTagFormat -Tag "latest" | Should Be $false
        Test-CandidateImageTagFormat -Tag "candidate-DIFFERENT_SHA" | Should Be $false
    }
}

Describe "New-CandidateBackendImageReference / New-CandidateFrontendImageReference" {

    It "build the expected backend and frontend image references from a tag" {
        $backend = New-CandidateBackendImageReference -Region "us-central1" -ProjectId "rateguard-ai" -ImageTag "candidate-23cfc95b5949"
        $frontend = New-CandidateFrontendImageReference -Region "us-central1" -ProjectId "rateguard-ai" -ImageTag "candidate-23cfc95b5949"
        $backend | Should Be "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949"
        $frontend | Should Be "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949"
    }
}

Describe "Test-ProductionTrafficUnchanged" {

    It "passes when the candidate tag carries 0% traffic" {
        $info = New-ApiServiceInfoWithCandidateTag
        (Test-ProductionTrafficUnchanged -ServiceInfo $info -CandidateTag "candidate") | Should Be $true
    }

    It "fails when the candidate tag somehow carries 100% traffic" {
        $info = '{ "status": { "traffic": [ { "percent": 100, "tag": "candidate", "revisionName": "x" } ] } }' | ConvertFrom-Json
        (Test-ProductionTrafficUnchanged -ServiceInfo $info -CandidateTag "candidate") | Should Be $false
    }
}

Describe "Test-CandidateEnvIsolation" {

    It "reports no failures when the expected staging key/value is present" {
        $info = New-ApiServiceInfoWithCandidateTag
        $failures = @(Test-CandidateEnvIsolation -ServiceInfo $info -ServiceLabel "rateguard-api" -ExpectedStagingValues @{ "RATEGUARD_PUBSUB_TOPIC" = "assurance-runs-staging" })
        $failures.Count | Should Be 0
    }

    It "reports a failure (naming only the key, never the value) when a key is missing" {
        $info = New-WebServiceInfoNoCandidateTag
        $failures = @(Test-CandidateEnvIsolation -ServiceInfo $info -ServiceLabel "rateguard-web" -ExpectedStagingValues @{ "RATEGUARD_GCS_BUCKET" = "rateguard-ai-artifacts-staging" })
        $failures.Count | Should Be 1
        $failures[0] | Should Match "RATEGUARD_GCS_BUCKET"
        $failures[0] | Should Not Match "rateguard-ai-artifacts-staging"
    }

    It "reports a failure when a key points at the wrong (e.g. production) value" {
        $info = New-ApiServiceInfoWithCandidateTag
        $failures = @(Test-CandidateEnvIsolation -ServiceInfo $info -ServiceLabel "rateguard-api" -ExpectedStagingValues @{ "RATEGUARD_PUBSUB_TOPIC" = "assurance-runs" })
        $failures.Count | Should Be 1
    }
}

Describe "Get-CandidateCorsOrigins" {

    It "adds a tag-prefixed candidate origin alongside the unchanged production origin" {
        $result = @(Get-CandidateCorsOrigins -ProductionOrigins @("http://localhost:3000", "https://rateguard-web-iqofutwtva-uc.a.run.app") -CandidateTag "candidate")
        $result.Count | Should Be 3
        ($result -contains "http://localhost:3000") | Should Be $true
        ($result -contains "https://rateguard-web-iqofutwtva-uc.a.run.app") | Should Be $true
        ($result -contains "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app") | Should Be $true
    }

    It "never generates a candidate origin for localhost or loopback" {
        $result = @(Get-CandidateCorsOrigins -ProductionOrigins @("http://localhost:3000", "http://127.0.0.1:3000") -CandidateTag "candidate")
        $result.Count | Should Be 2
    }

    It "never emits a wildcard origin" {
        $result = @(Get-CandidateCorsOrigins -ProductionOrigins @("https://rateguard-web-iqofutwtva-uc.a.run.app") -CandidateTag "candidate")
        ($result -contains "*") | Should Be $false
    }

    It "does not add a duplicate candidate origin when the same production origin appears twice" {
        $result = @(Get-CandidateCorsOrigins -ProductionOrigins @("https://rateguard-web-iqofutwtva-uc.a.run.app", "https://rateguard-web-iqofutwtva-uc.a.run.app") -CandidateTag "candidate")
        (@($result | Where-Object { $_ -eq "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" })).Count | Should Be 1
    }
}

Describe "ConvertTo-CompactJsonStringArray" {

    It "round-trips through ConvertFrom-Json back to the same values" {
        $values = @("http://localhost:3000", "https://rateguard-web-iqofutwtva-uc.a.run.app", "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app")
        $json = ConvertTo-CompactJsonStringArray -Values $values
        $json | Should Be '["http://localhost:3000","https://rateguard-web-iqofutwtva-uc.a.run.app","https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"]'
        $parsedRaw = ConvertFrom-Json -InputObject $json
        $parsed = @($parsedRaw)
        $parsed.Count | Should Be 3
    }

    It "produces a valid array literal for a single-element input (the PS 5.1 single-element pitfall)" {
        $json = ConvertTo-CompactJsonStringArray -Values @("https://rateguard-web-iqofutwtva-uc.a.run.app")
        $json | Should Be '["https://rateguard-web-iqofutwtva-uc.a.run.app"]'
    }
}

Describe "Invoke-NativeCommand fail-fast" {

    It "returns normally and captures output on a zero exit code" {
        $out = Invoke-NativeCommand -Operation "cmd echo" -FilePath "cmd.exe" -ArgumentList @("/c", "echo", "hello") -CaptureOutput
        ($out -join "") | Should Match "hello"
    }

    It "throws immediately on a nonzero exit code, naming the operation" {
        { Invoke-NativeCommand -Operation "deliberate failure" -FilePath "cmd.exe" -ArgumentList @("/c", "exit", "7") } | Should Throw "deliberate failure"
    }

    It "a nonzero native command aborts a calling script block before later statements run" {
        $script = {
            Invoke-NativeCommand -Operation "step one" -FilePath "cmd.exe" -ArgumentList @("/c", "exit", "3")
            $global:__reached_after_failure = $true
        }
        $global:__reached_after_failure = $false
        try { & $script } catch { }
        $global:__reached_after_failure | Should Be $false
        Remove-Variable -Name __reached_after_failure -Scope Global -ErrorAction SilentlyContinue
    }

    It "REGRESSION (WebUrl postcondition failure): without -CaptureOutput, a chatty native command's stdout does not leak into the calling function's return value" {
        # Reproduces the exact failure mode observed after the frontend
        # deploy step: a real native process that prints several stdout
        # lines (standing in for gcloud build/deploy progress output),
        # invoked as a bare/unassigned statement (exactly how
        # Deploy-CandidateFrontend calls the Cloud Build and Cloud Run
        # deploy steps), followed by a `return` of a single URL-like string.
        # Before the fix, the calling function's return value was a
        # System.Object[] containing the leaked lines plus the URL; binding
        # THAT to a downstream [string] parameter is exactly what threw
        # "Cannot process argument transformation on parameter 'WebUrl'.
        # Cannot convert value to type System.String."
        $wrapper = {
            Invoke-NativeCommand -Operation "chatty build-like command" -FilePath "cmd.exe" -ArgumentList @(
                "/c", "echo Creating tarball...& echo Uploading...& echo Build succeeded"
            )
            Invoke-NativeCommand -Operation "chatty deploy-like command" -FilePath "cmd.exe" -ArgumentList @(
                "/c", "echo Deploying container...& echo Service URL noise"
            )
            return "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"
        }

        $result = & $wrapper

        $result.GetType().FullName | Should Be "System.String"
        @($result).Count | Should Be 1
        $result | Should Be "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"

        function Consume-WebUrlLikeCompleteCandidateDeployment {
            param([Parameter(Mandatory = $true)][string]$WebUrl)
            return $WebUrl
        }
        { Consume-WebUrlLikeCompleteCandidateDeployment -WebUrl $result } | Should Not Throw
    }

    It "without -CaptureOutput, the function itself produces zero pipeline objects (not even `$null`)" {
        $script = { Invoke-NativeCommand -Operation "silent success" -FilePath "cmd.exe" -ArgumentList @("/c", "exit", "0") }
        $emitted = @(& $script)
        $emitted.Count | Should Be 0
    }
}

Describe "Get-SingleCandidateTaggedUrl / Test-CandidateTaggedUrlExists" {

    It "returns exactly one nonempty scalar string for a well-formed single candidate-tag entry" {
        $info = New-WebServiceInfoWithCandidateTag
        $url = Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web"
        $url.GetType().FullName | Should Be "System.String"
        @($url).Count | Should Be 1
        $url | Should Be "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"
    }

    It "throws when no traffic entry carries the tag" {
        $info = New-WebServiceInfoNoCandidateTag
        { Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web" } | Should Throw "No traffic entry"
    }

    It "Test-CandidateTaggedUrlExists is false when absent and true when present (non-throwing, used to decide missing-vs-broken)" {
        (Test-CandidateTaggedUrlExists -ServiceInfo (New-WebServiceInfoNoCandidateTag) -Tag "candidate") | Should Be $false
        (Test-CandidateTaggedUrlExists -ServiceInfo (New-WebServiceInfoWithCandidateTag) -Tag "candidate") | Should Be $true
    }

    It "throws when more than one traffic entry carries the tag, rather than silently picking the first" {
        $info = '{
            "status": {
                "traffic": [
                    { "revisionName": "rateguard-web-00009-abc", "tag": "candidate", "url": "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" },
                    { "revisionName": "rateguard-web-00010-kup", "tag": "candidate", "url": "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" }
                ]
            }
        }' | ConvertFrom-Json
        { Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web" } | Should Throw "expected exactly one"
    }

    It "throws on a relative/malformed URL rather than returning it" {
        $info = '{ "status": { "traffic": [ { "tag": "candidate", "url": "/internal/pubsub/assurance" } ] } }' | ConvertFrom-Json
        { Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web" } | Should Throw
    }

    It "throws when the candidate-tagged entry carries nonzero production traffic" {
        $info = '{ "status": { "traffic": [ { "tag": "candidate", "percent": 10, "url": "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" } ] } }' | ConvertFrom-Json
        { Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web" } | Should Throw "production traffic"
    }

    It "throws when the URL belongs to a different service" {
        $info = New-ApiServiceInfoWithCandidateTag
        { Get-SingleCandidateTaggedUrl -ServiceInfo $info -Tag "candidate" -ServiceHostFragment "rateguard-web" } | Should Throw
    }
}

Describe "ConvertFrom-CloudRunServiceJson" {

    It "throws a clear error on empty/human-formatted (non-JSON) input instead of silently returning nothing" {
        { ConvertFrom-CloudRunServiceJson -JsonLines @() -ServiceName "rateguard-api" } | Should Throw
        { ConvertFrom-CloudRunServiceJson -JsonLines @("NAME    REGION    URL", "rateguard-api  us-central1  https://x") -ServiceName "rateguard-api" } | Should Throw
    }
}

Describe "deploy_candidate.ps1 orchestration" {

    # Dot-sourcing runs the module-load path only: $MyInvocation.InvocationName
    # is '.' when dot-sourced, so Invoke-CandidateDeploymentEntryPoint is never
    # auto-invoked and no gcloud/git side effects beyond `git rev-parse` occur.
    . $deployScriptPath

    It "-DeployCandidate and -ResumeCandidate are mutually exclusive" {
        { Assert-MutuallyExclusiveModes -DeployCandidate -ResumeCandidate } | Should Throw "mutually exclusive"
    }

    It "does not throw when only -DeployCandidate is set" {
        { Assert-MutuallyExclusiveModes -DeployCandidate } | Should Not Throw
    }

    It "does not throw when only -ResumeCandidate is set" {
        { Assert-MutuallyExclusiveModes -ResumeCandidate } | Should Not Throw
    }

    It "the success banner cannot be reached when postconditions fail" {
        Mock Confirm-CandidatePostconditions { return @("simulated postcondition failure") }
        Mock Write-SuccessBanner { $global:__banner_printed = $true }
        $global:__banner_printed = $false

        $urls = [pscustomobject]@{
            ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
            WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
            WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
        }

        {
            Complete-CandidateDeployment -Urls $urls -WebUrl "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" `
                -ExpectedBackendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949" `
                -ExpectedFrontendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949" `
                -ImageTag "candidate-23cfc95b5949"
        } | Should Throw

        $global:__banner_printed | Should Be $false
        Remove-Variable -Name __banner_printed -Scope Global -ErrorAction SilentlyContinue
    }

    It "the success banner IS reached when every postcondition passes" {
        Mock Confirm-CandidatePostconditions { return @() }
        Mock Write-SuccessBanner { $global:__banner_printed = $true }
        $global:__banner_printed = $false

        $urls = [pscustomobject]@{
            ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
            WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
            WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
        }

        {
            Complete-CandidateDeployment -Urls $urls -WebUrl "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" `
                -ExpectedBackendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949" `
                -ExpectedFrontendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949" `
                -ImageTag "candidate-23cfc95b5949"
        } | Should Not Throw
        $global:__banner_printed | Should Be $true
        Remove-Variable -Name __banner_printed -Scope Global -ErrorAction SilentlyContinue
    }

    It "REGRESSION: Deploy-CandidateFrontend returns exactly one nonempty scalar string, end to end, and it binds cleanly into Complete-CandidateDeployment's [string]`$WebUrl" {
        # Mirrors the observed failing run: backend/API/worker already good,
        # frontend build+deploy succeed, rateguard-web-00010-kup ends up
        # candidate-tagged at 0% traffic. Deploy-CandidateFrontend's own
        # Cloud Build / Cloud Run deploy calls are unassigned bare
        # statements (not -CaptureOutput) -- exactly the shape that leaked
        # before the Invoke-NativeCommand fix.
        Mock Invoke-NativeCommand {
            param($Operation, $FilePath, $ArgumentList, [switch]$CaptureOutput)
            if ($CaptureOutput) { return @("sha256:0f998617ca6426dba06d8b5b8d8617934305170c9c07e62b94acb222dfc3f715") }
        }
        Mock Get-CandidateServiceInfo { New-WebServiceInfoWithCandidateTag }
        # This test isolates the WebUrl type/binding fix specifically; full
        # postcondition semantics are already covered by the two tests above.
        Mock Confirm-CandidatePostconditions { return @() }
        Mock Write-SuccessBanner { }

        $webUrl = Deploy-CandidateFrontend -CandidateApiUrl "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app" `
            -ImageTag "candidate-23cfc95b5949" -FrontendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949"

        $webUrl.GetType().FullName | Should Be "System.String"
        @($webUrl).Count | Should Be 1
        $webUrl | Should Be "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app"

        {
            Complete-CandidateDeployment -Urls ([pscustomobject]@{
                    ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
                    WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
                    WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
                }) -WebUrl $webUrl `
                -ExpectedBackendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-23cfc95b5949" `
                -ExpectedFrontendImage "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-web:candidate-23cfc95b5949" `
                -ImageTag "candidate-23cfc95b5949"
        } | Should Not Throw
    }

    It "resume requires -CandidateImageTag (rejected at the CLI-argument gate before any resume logic runs)" {
        { Assert-ValidResumeArguments -ResumeCandidate -CandidateImageTag "" } | Should Throw "required"
        { Assert-ValidResumeArguments -ResumeCandidate } | Should Throw "required"
        { Assert-ValidResumeArguments -ResumeCandidate -CandidateImageTag $null } | Should Throw "required"
    }

    It "Resume-Candidate itself also declares -CandidateImageTag as Mandatory (defense in depth, not just the CLI gate)" {
        $param = (Get-Command Resume-Candidate).Parameters['CandidateImageTag']
        $mandatoryAttr = $param.Attributes | Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] }
        $mandatoryAttr.Mandatory | Should Be $true
    }

    It "a malformed -CandidateImageTag is rejected before any Artifact Registry or revision check runs" {
        Mock Invoke-NativeCommand { throw "Invoke-NativeCommand should not have been called for a malformed tag" }
        Mock Get-ValidatedCandidateBackendUrls { throw "Get-ValidatedCandidateBackendUrls should not have been called for a malformed tag" }

        { Resume-Candidate -CandidateImageTag "not-a-valid-tag" } | Should Throw "not a valid candidate image tag"
    }

    It "a missing Artifact Registry image is rejected" {
        Mock Invoke-NativeCommand {
            param($Operation, $FilePath, $ArgumentList, [switch]$CaptureOutput)
            if ($ArgumentList -contains "artifacts") {
                throw "Native command failed: Describe supplied candidate backend image (exit code 1)"
            }
            return @()
        }
        Mock Get-ValidatedCandidateBackendUrls { throw "should not reach URL discovery when the image itself cannot be verified" }

        { Resume-Candidate -CandidateImageTag "candidate-23cfc95b5949" } | Should Throw
    }

    It "-ResumeCandidate stops on a backend image mismatch instead of silently rebuilding" {
        Mock Invoke-NativeCommand { return @("sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef") }
        Mock Get-ValidatedCandidateBackendUrls {
            $apiInfo = New-ApiServiceInfoWithCandidateTag
            $apiInfo.spec.template.spec.containers[0].image = "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-DIFFERENT_SHA"
            $workerInfo = New-WorkerServiceInfoWithCandidateTag
            $workerInfo.spec.template.spec.containers[0].image = "us-central1-docker.pkg.dev/rateguard-ai/rateguard/rateguard-api:candidate-DIFFERENT_SHA"
            [pscustomobject]@{
                ApiInfo           = $apiInfo
                WorkerInfo        = $workerInfo
                ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
                ApiUntaggedUrl    = "https://rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
            }
        }

        { Resume-Candidate -CandidateImageTag "candidate-23cfc95b5949" } | Should Throw "Mismatch"
    }

    It "clean -DeployCandidate still derives its image tag from the current git HEAD" {
        # $GIT_SHA/$IMAGE_TAG/$BACKEND_IMAGE are computed unconditionally at
        # script load time from `git rev-parse HEAD`, independent of which
        # switch (if any) is passed -- this is what -DeployCandidate uses.
        # Resume-Candidate must NOT read these globals (verified by the
        # tests above, which pass an unrelated -CandidateImageTag and never
        # reference $BACKEND_IMAGE).
        $IMAGE_TAG | Should Be "candidate-$GIT_SHA"
        $expectedBackendImage = New-CandidateBackendImageReference -Region $REGION -ProjectId $PROJECT_ID -ImageTag $IMAGE_TAG
        $BACKEND_IMAGE | Should Be $expectedBackendImage
    }

    It "-ResumeCandidate accepts a valid prior application tag even when current git HEAD differs, and skips backend rebuild + frontend redeploy when everything already matches" {
        # Deliberately use a tag that is NOT the current git HEAD's tag (this
        # test's dot-sourced $GIT_SHA is whatever this repo's HEAD actually
        # is, e.g. the deployment-script-only commit bde7927 -- the supplied
        # tag below intentionally does not need to equal it).
        $suppliedTag = "candidate-23cfc95b5949"
        $suppliedTag | Should Not Be $IMAGE_TAG

        $expectedBackendImage = New-CandidateBackendImageReference -Region $REGION -ProjectId $PROJECT_ID -ImageTag $suppliedTag

        Mock Get-ValidatedCandidateBackendUrls {
            $apiInfo = New-ApiServiceInfoWithCandidateTag
            $apiInfo.spec.template.spec.containers[0].image = $expectedBackendImage
            $workerInfo = New-WorkerServiceInfoWithCandidateTag
            $workerInfo.spec.template.spec.containers[0].image = $expectedBackendImage
            [pscustomobject]@{
                ApiInfo           = $apiInfo
                WorkerInfo        = $workerInfo
                ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
                ApiUntaggedUrl    = "https://rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
            }
        }
        Mock Initialize-StagingPubSub { }
        Mock Initialize-StagingDataResources { }
        Mock Get-CandidateServiceInfo {
            param($ServiceName)
            if ($ServiceName -eq "rateguard-web") { return New-WebServiceInfoWithCandidateTag }
            return New-ApiServiceInfoWithCandidateTag
        }
        Mock Deploy-CandidateFrontend { $global:__frontend_deployed = $true; return "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" }
        Mock Confirm-CandidatePostconditions { return @() }
        Mock Write-SuccessBanner { }
        Mock Invoke-NativeCommand { return @("sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef") }
        $global:__frontend_deployed = $false

        { Resume-Candidate -CandidateImageTag $suppliedTag } | Should Not Throw

        # The candidate web tag already exists in the fixture returned by the
        # mocked Get-CandidateServiceInfo, so the frontend must NOT be rebuilt.
        $global:__frontend_deployed | Should Be $false
        Remove-Variable -Name __frontend_deployed -Scope Global -ErrorAction SilentlyContinue
    }

    It "frontend resume uses the supplied existing candidate tag for the frontend image, not the HEAD-derived tag" {
        $suppliedTag = "candidate-23cfc95b5949"
        $expectedBackendImage = New-CandidateBackendImageReference -Region $REGION -ProjectId $PROJECT_ID -ImageTag $suppliedTag
        $expectedFrontendImage = New-CandidateFrontendImageReference -Region $REGION -ProjectId $PROJECT_ID -ImageTag $suppliedTag

        Mock Get-ValidatedCandidateBackendUrls {
            $apiInfo = New-ApiServiceInfoWithCandidateTag
            $apiInfo.spec.template.spec.containers[0].image = $expectedBackendImage
            $workerInfo = New-WorkerServiceInfoWithCandidateTag
            $workerInfo.spec.template.spec.containers[0].image = $expectedBackendImage
            [pscustomobject]@{
                ApiInfo           = $apiInfo
                WorkerInfo        = $workerInfo
                ApiTaggedUrl      = "https://candidate---rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerTaggedUrl   = "https://candidate---rateguard-worker-iqofutwtva-uc.a.run.app"
                ApiUntaggedUrl    = "https://rateguard-api-iqofutwtva-uc.a.run.app"
                WorkerUntaggedUrl = "https://rateguard-worker-iqofutwtva-uc.a.run.app"
            }
        }
        Mock Invoke-NativeCommand { return @("sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef") }
        Mock Initialize-StagingPubSub { }
        Mock Initialize-StagingDataResources { }
        # No candidate tag yet on rateguard-web -- the frontend must be built.
        Mock Get-CandidateServiceInfo { New-WebServiceInfoNoCandidateTag }
        Mock Deploy-CandidateFrontend { return "https://candidate---rateguard-web-iqofutwtva-uc.a.run.app" }
        Mock Confirm-CandidatePostconditions { return @() }
        Mock Write-SuccessBanner { }

        Resume-Candidate -CandidateImageTag $suppliedTag

        Assert-MockCalled Deploy-CandidateFrontend -Times 1 -Exactly -ParameterFilter {
            $ImageTag -eq $suppliedTag -and $FrontendImage -eq $expectedFrontendImage
        }
    }
}
