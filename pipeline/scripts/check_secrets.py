"""Fail the build if a credential is about to be committed.

Run before any push to a public remote:

    PYTHONPATH=src .venv/bin/python scripts/check_secrets.py

Exits 1 on any hit. Scans git's staged files, or the whole tracked tree with
`--all`. Deliberately pattern-based: it catches the shapes real credentials
take, and refuses to guess about anything else.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

PATTERNS: dict[str, str] = {
    "google client secret (GOCSPX-)": r"GOCSPX-[A-Za-z0-9_-]{10,}",
    "google access token (ya29.)": r"ya29\.[A-Za-z0-9_\-]{20,}",
    "google refresh token (1//)": r"\b1//0[A-Za-z0-9_\-]{20,}",
    "meta/facebook token (EAA...)": r"\bEAA[A-Za-z0-9]{40,}",
    "aws access key (AKIA/ASIA)": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "slack token": r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    "assigned secret literal": r"(?i)(secret|token|password|api_key)\s*[=:]\s*[\"'][A-Za-z0-9_\-\.]{20,}[\"']",
    "long base64 blob": r"[\"']([A-Za-z0-9+/]{60,}={0,2})[\"']",
}

# Credential-shaped files that must never be tracked at all.
# Note `.env.example` is intentionally committed: it is the template.
FORBIDDEN_PATHS = re.compile(
    r"(^|/)(\.env|\.env\.local|\.env\.production)$|(^|/)secrets?/|\.(pem|p12|key)$"
)


def tracked(mode: str) -> list[str]:
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--name-only"]
    else:
        cmd = ["git", "ls-files"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("not a git repository", file=sys.stderr)
        sys.exit(2)
    return [f for f in res.stdout.split() if f]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="scan the whole tracked tree")
    args = ap.parse_args()

    files = tracked("all" if args.all else "staged")
    hits = 0

    for f in files:
        if FORBIDDEN_PATHS.search(f):
            print(f"  !! forbidden path is tracked: {f}")
            hits += 1
            continue
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, pat in PATTERNS.items():
            for m in re.finditer(pat, text):
                line = text[: m.start()].count("\n") + 1
                print(f"  !! {name}\n     {f}:{line}  {m.group(0)[:70]!r}")
                hits += 1

    print(f"\n{len(files)} files scanned, {hits} potential secret(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
