"""Fail if src/ imports a third-party package that requirements.txt does not install.

This exists because of a real CI failure: Pillow was added to pyproject.toml but
not to requirements.txt, CI installs only requirements.txt, and `ModuleNotFound`
appeared at runtime on the runner while working fine locally (the package had
been pip-installed by hand). Two manifests drift; one check prevents it.

    PYTHONPATH=src .venv/bin/python scripts/check_deps.py

Exit 1 lists packages to add.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "reelforge"
REQ = ROOT / "requirements.txt"

STDLIB = {
    "__future__", "argparse", "asyncio", "base64", "collections", "concurrent",
    "dataclasses", "datetime", "enum", "functools", "getpass", "hashlib", "html",
    "itertools", "json", "math", "os", "pathlib", "random", "re", "shutil",
    "sqlite3", "subprocess", "sys", "textwrap", "time", "typing", "urllib",
    "wave", "io", "tempfile", "struct", "unicodedata", "traceback",
    # xml.etree.ElementTree parses the channel's RSS feed in publish/youtube.py.
    # Stdlib, but absent from this list, which is how the 18 Nov run failed at the
    # import gate instead of at the upload.
    "xml",
}

# import name -> distribution name, where the two differ.
DIST = {
    "PIL": "pillow",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "edge_tts": "edge-tts",
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "google_auth_httplib2": "google-auth-httplib2",
    "google": "google-api-python-client",
    "boto3": "boto3",
    "requests": "requests",
    "piper": "piper-tts",
}

# Lazy imports for optional backends. Deliberately not in requirements.txt:
# installing torch/diffusers in CI would add minutes for a backend CI never uses.
OPTIONAL = {"torch", "torchvision", "diffusers", "transformers", "accelerate",
            "safetensors", "kokoro", "soundfile", "numpy"}


def declared() -> set[str]:
    if not REQ.exists():
        return set()
    names = set()
    for line in REQ.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        names.add(re.split(r"[<>=!~\[;]", line)[0].strip().lower())
    return names


def imported() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [(node.module or "").split(".")[0]] if node.level == 0 else []
            else:
                continue
            for m in mods:
                if not m or m in STDLIB:
                    continue
                found.setdefault(m, set()).add(f"{py.relative_to(ROOT)}")
    return found


def main() -> int:
    have = declared()
    missing: list[tuple[str, str, set[str]]] = []

    for module, files in sorted(imported().items()):
        if module in OPTIONAL:
            continue
        dist = DIST.get(module, module.replace("_", "-").lower())
        if dist.lower() not in have:
            missing.append((module, dist, files))

    if not missing:
        print(f"dependencies OK: {len(have)} declared, all imports covered")
        return 0

    print("MISSING from requirements.txt:\n")
    for module, dist, files in missing:
        print(f"  import '{module}'  ->  add '{dist}'")
        for f in sorted(files):
            print(f"      {f}")
    print("\nCI installs requirements.txt only, so these fail on the runner.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
