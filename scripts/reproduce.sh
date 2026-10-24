#!/usr/bin/env bash
# One-command, cold-cache reproduction of the current C0/P4 code-review pair.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${COMPETITION_WORKSPACE:?Set COMPETITION_WORKSPACE to an authorized writable workspace}"
RUN_WORKSPACE="${REPRO_WORKSPACE:-$COMPETITION_WORKSPACE/repro-code-review}"
VENV="${REPRO_VENV:-$COMPETITION_WORKSPACE/.venvs/bias-reproduce}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
TIME_FILE="$COMPETITION_WORKSPACE/reproduce-code-review-time.txt"

if [[ "${1:-}" == "--inner" ]]; then
  if [[ -n "$UV_BIN" ]]; then
    "$UV_BIN" run --python "$PYTHON_BIN" --no-project \
      --with-requirements "$ROOT/requirements.txt" \
      "$ROOT/scripts/reproduce_pipeline.py" --workspace "$RUN_WORKSPACE"
  else
    "$VENV/bin/python" "$ROOT/scripts/reproduce_pipeline.py" --workspace "$RUN_WORKSPACE"
  fi
  exit 0
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Missing $PYTHON_BIN (CPython 3.12 required); set PYTHON_BIN to a compatible executable." >&2
  exit 2
fi
if [[ -z "$UV_BIN" ]]; then
  # Standard venv/pip fallback for ordinary Python installations.
  "$PYTHON_BIN" -m venv "$VENV" || {
    echo "Python venv/ensurepip is unavailable; install python3.12-venv or set UV_BIN to public uv." >&2
    exit 2
  }
  "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt"
fi
mkdir -p "$(dirname "$TIME_FILE")"
rm -f "$TIME_FILE"
if ! /usr/bin/time -v -o "$TIME_FILE" env \
    PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \
    REPRO_WORKSPACE="$RUN_WORKSPACE" REPRO_VENV="$VENV" PYTHON_BIN="$PYTHON_BIN" UV_BIN="$UV_BIN" \
    bash "$ROOT/scripts/reproduce.sh" --inner; then
  echo "Reproduction failed; see preceding error output." >&2
  exit 1
fi
"$PYTHON_BIN" "$ROOT/scripts/record_reproduce_run.py" --time-file "$TIME_FILE"
