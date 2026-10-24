#!/usr/bin/env python3
"""Run public input fetch and the cold candidate rebuild in one pinned Python."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    commands = [
        [sys.executable, str(ROOT / "scripts/fetch_repro_inputs.py"), "--workspace", str(args.workspace)],
        [sys.executable, str(ROOT / "scripts/reproduce_candidates.py"), "--workspace", str(args.workspace)],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
