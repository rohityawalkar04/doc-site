"""Write secrets into .env without them passing through the chat or shell history.

Input is hidden (getpass), values are never echoed, and existing keys are
replaced in place instead of duplicated.

Usage:
    python scripts/set_env.py meta     # META_APP_ID, META_APP_SECRET, IG_USER_TOKEN
    python scripts/set_env.py r2       # Cloudflare R2 credentials for IG hosting
    python scripts/set_env.py set KEY  # any single value
    python scripts/set_env.py show     # which keys are set, values masked
"""
from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

GROUPS = {
    "meta": [
        ("META_APP_ID", "Meta app id (App settings > Basic)", False),
        ("META_APP_SECRET", "Meta app secret (App settings > Basic)", True),
        ("IG_USER_TOKEN", "Graph API Explorer user token (temporary; meta_setup consumes it)", True),
    ],
    "r2": [
        ("R2_ACCOUNT_ID", "Cloudflare account id", False),
        ("R2_ACCESS_KEY_ID", "R2 access key id", True),
        ("R2_SECRET_ACCESS_KEY", "R2 secret access key", True),
        ("R2_BUCKET", "bucket name", False),
        ("R2_PUBLIC_BASE", "public base URL, e.g. https://pub-xxxx.r2.dev", False),
    ],
    "youtube": [
        ("YT_CLIENT_SECRET", "path to the OAuth JSON (default secrets/yt_client_secret.json)", False),
    ],
}


def read_env() -> list[str]:
    if not ENV.exists():
        example = ROOT / ".env.example"
        return example.read_text().splitlines() if example.exists() else []
    return ENV.read_text().splitlines()


def write_key(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1].lower()

    lines = read_env()

    if cmd == "show":
        for line in lines:
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if not val:
                print(f"  {key:26} (empty)")
            else:
                shown = val if len(val) <= 8 else f"{val[:4]}...{val[-2:]}"
                print(f"  {key:26} {shown}  ({len(val)} chars)")
        return 0

    if cmd == "set":
        if len(sys.argv) < 3:
            print("usage: python scripts/set_env.py set KEY")
            return 2
        key = sys.argv[2]
        val = getpass.getpass(f"{key}: ")
        if not val:
            print("  nothing entered, unchanged")
            return 1
        ENV.write_text("\n".join(write_key(lines, key, val)) + "\n")
        print(f"  {key} set ({len(val)} chars)")
        return 0

    if cmd not in GROUPS:
        print(f"unknown group {cmd!r}; choose from {', '.join(GROUPS)}")
        return 2

    print(f"\nEntering {cmd} values. Input is hidden and nothing is echoed.\n")
    for key, hint, secret in GROUPS[cmd]:
        prompt = f"  {key}\n    {hint}\n    > "
        val = getpass.getpass(prompt) if secret else input(prompt)
        if not val.strip():
            print("    skipped")
            continue
        lines = write_key(lines, key, val.strip())
        print(f"    set ({len(val.strip())} chars)")

    ENV.write_text("\n".join(lines) + "\n")
    print(f"\n  written to {ENV}")
    print("  verify with: ./reelforge doctor\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
