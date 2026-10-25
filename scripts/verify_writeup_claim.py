#!/usr/bin/env python3
"""Verify one writeup claim against its named evidence and exit 0 when it holds.

This is the per-claim command recorded in the claim inventory. It reads only the
committed writeup document, the committed inventory and the evidence artifacts
named by that single claim, so it is fast enough for the audit to execute once
per claim.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_writeup_claim_evidence", ROOT / "scripts/audit_writeup_claim_evidence.py")
audit = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(audit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim", required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "config/writeup-claim-evidence-preregistered.json")
    parser.add_argument("--inventory", type=Path,
                        default=ROOT / "reports/writeup-claim-inventory.json")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    audit.configure(audit.load_config(arguments.config))
    payload = json.loads((arguments.inventory if arguments.inventory.is_absolute()
                          else ROOT / arguments.inventory).read_text(encoding="utf-8"))
    claim = next((entry for entry in payload["claims"] if entry["claim_id"] == arguments.claim),
                 None)
    if claim is None:
        print(f"FAIL unknown claim {arguments.claim}")
        return 2

    pool = audit.EvidencePool()
    pool.load()
    result = audit.verify_claim(claim, pool)
    if arguments.json:
        print(json.dumps(result, indent=1))
    else:
        print(f"{'PASS' if result['passed'] else 'FAIL'} claim {arguments.claim}: "
              f"{result['tokens_covered']}/{result['tokens_total']} numeric tokens covered by "
              f"{len(claim['evidence'])} named evidence artifact(s); anchor={result['anchor_verbatim']} "
              f"hashes={result['evidence_hashes_verified']} pointer={result['primary_pointer_hit']} "
              f"accuracy_ok={result['accuracy_ok']} prohibited_ok={result['prohibited_ok']} "
              f"hedge_ok={result['hedge_ok']}")
        for token in result["tokens_uncovered"]:
            print(f"  uncovered numeric token: {token}")
        if result["pointer_reason"]:
            print(f"  pointer: {result['pointer_reason']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
