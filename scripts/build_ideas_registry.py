#!/usr/bin/env python3
"""Derive ``config/ideas.json`` (id, family, component, kind, inputs, cost) from the 60-idea spec.

The registry is generated, never hand-written: ids, families, components, costs and inputs come from
the operator-frozen memo and the spec's SHA-256 is pinned in the preregistration config, so a silent
edit of either is a hard failure.

Usage:
    COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" \\
      /home/martin/hddProjects/langywrap/uv run --quiet python scripts/build_ideas_registry.py
      [--check-only] [--out PATH] [--config PATH] [--spec PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bias60.harness import build_registry, canonical_bytes, validate_registry_document  # noqa: E402
from bias60.spec import parse_spec, sha256_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/bias60-harness-preregistered.json")
    parser.add_argument("--out", default="config/ideas.json")
    parser.add_argument("--spec", default=None)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--tamper", default=None,
                        choices=[None, "drop_idea", "retitle_family", "wrong_spec_hash"])
    args = parser.parse_args(argv)

    config_path = ROOT / args.config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["__path__"] = args.config
    config["__sha256__"] = hashlib.sha256(canonical_bytes(config)).hexdigest()

    spec_path = Path(args.spec or config["spec"]["resolved_path"])
    if args.tamper == "wrong_spec_hash":
        config["spec"]["sha256"] = "0" * 64
    try:
        spec_parse = parse_spec(spec_path)
    except FileNotFoundError as error:
        print(f"BLOCKED {error}")
        return 2
    if spec_parse["spec"]["sha256"] != config["spec"]["sha256"]:
        print("FAIL spec sha256 does not match the pinned value: "
              f"{spec_parse['spec']['sha256'][:16]} != {config['spec']['sha256'][:16]}")
        return 1

    document = build_registry(spec_parse)
    if args.tamper == "drop_idea":
        document["ideas"] = document["ideas"][1:]
        document["summary"]["ideas_total"] = len(document["ideas"])
    if args.tamper == "retitle_family":
        document["families"] = document["families"][:1]
    gates = validate_registry_document(document, config, spec_parse)
    gates["all_passed"] = all(gates.values())
    payload = canonical_bytes(document)
    out_path = ROOT / args.out
    if args.check_only:
        if out_path.is_file() and out_path.read_bytes() == payload:
            print(f"unchanged {out_path}")
            return 0 if gates["all_passed"] else 1
        print(f"CHANGED {out_path}")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)

    summary = {
        "gates_all_passed": gates["all_passed"],
        "ideas_total": document["summary"]["ideas_total"],
        "families_total": document["summary"]["families_total"],
        "implemented": document["summary"]["implemented"],
        "implemented_components": document["summary"]["implemented_components"],
        "by_component": document["summary"]["by_component"],
        "by_kind": document["summary"]["by_kind"],
        "by_cost": document["summary"]["by_cost"],
        "failed_gates": [name for name, value in gates.items() if not value],
        "registry_sha256": hashlib.sha256(payload).hexdigest(),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if gates["all_passed"]:
        print("PASS ideas registry: all 60 spec ids carry family, component, kind, inputs and cost, "
              "the three seed ideas are implemented across distinct components, and the spec hash "
              "matches its pin")
        return 0
    print("FAIL ideas registry: " + ", ".join(summary["failed_gates"]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
