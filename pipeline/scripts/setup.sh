#!/usr/bin/env bash
# Creates the venv, installs dependencies, and wires up the `reelforge` command.
# Safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  echo "-> creating .venv ($(python3 -V 2>&1))"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip wheel setuptools
python -m pip install --quiet -r requirements.txt

# Deliberately not `pip install -e .`: setuptools' editable shim proved
# unreliable here and fails with "No module named reelforge". An explicit
# .pth plus a PYTHONPATH wrapper is boring and always works.
SP="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$ROOT/src" > "$SP/reelforge_src.pth"

cat > .venv/bin/reelforge <<EOF
#!/usr/bin/env bash
PYTHONPATH="$ROOT/src" exec "$ROOT/.venv/bin/python" -m reelforge.cli "\$@"
EOF
chmod +x .venv/bin/reelforge

echo
echo "-> installed. Next:"
echo "   source .venv/bin/activate"
echo "   reelforge doctor"
