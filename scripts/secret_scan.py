#!/usr/bin/env python3
"""Lightweight repository secret scan (tracked + untracked-not-ignored files).

Fails (exit 1) on private-key blocks, Google API keys / OAuth tokens, service-
account JSON, JWT-shaped bearer tokens and hard-coded secret assignments. A line
carrying `pragma: allowlist secret` (or inside the fixed dummy-value allowlist)
is skipped. Never prints the matched value - only file, line and rule.

    python scripts/secret_scan.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("google-oauth-token", re.compile(r"\bya29\.[0-9A-Za-z_\-]{30,}")),
    ("service-account-json", re.compile(r'"type"\s*:\s*"service_account"')),
    ("private-key-json-field", re.compile(r'"private_key(_id)?"\s*:\s*"[^"]{16,}"')),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}")),
    ("hardcoded-secret", re.compile(
        r"""(?i)\b(api[_-]?key|secret|passwd|password|client_secret)\b\s*[:=]\s*["'][A-Za-z0-9/+_\-]{20,}["']""")),
]
SKIP_SUFFIXES = {".png", ".jpg", ".ico", ".xlsx", ".xlsm", ".pdf", ".zip", ".woff", ".woff2", ".lock", ".csv"}
SKIP_PARTS = {"node_modules", ".next", "__pycache__", "package-lock.json"}
# Deliberately fake values used by tests/docs.
DUMMY_HINTS = ("e2e-signature", "abcdefghijklmnopqrstuvwxyz", "pragma: allowlist secret", "example", "placeholder",
               "REDACTED", "xxxxxxxx")


def files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    return [ROOT / f for f in out if (ROOT / f).is_file()]


def main() -> int:
    hits: list[str] = []
    for path in files():
        if path.suffix.lower() in SKIP_SUFFIXES or any(p in path.parts for p in SKIP_PARTS):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(h in line for h in DUMMY_HINTS):
                continue
            for rule, pattern in RULES:
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {rule}")
    if hits:
        print("SECRET SCAN FAILED:\n" + "\n".join(hits))
        return 1
    print(f"secret scan clean ({len(files())} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
