"""Run registered 60-estimator ideas end to end and emit one parquet per idea.

The harness is the part the other bias60 rows depend on: it turns ``provided inputs -> per-tract
value for the idea's component`` into a stable, hash-recorded artifact at
``$COMPETITION_WORKSPACE/ideas/<id>.parquet``, and it refuses to accept an output that breaks the
contract (missing official GEOID, non-finite value, gap outside [0, 1], forbidden source accessed,
undeclared column read, non-deterministic rerun).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bias60.contracts import IdeaOutput
from bias60.determinism import canonical_bytes, normalized_bytes, wall_clock_hits
from bias60.ideas import IMPLEMENTATIONS
from bias60.inputs import ProvidedInputs, official_scored_pairs, sha256

PARQUET_COLUMNS = ["GEOID", "region", "idea", "family", "component", "channel", "value", "defined"]
PARQUET_TYPES = [pa.string(), pa.string(), pa.string(), pa.string(), pa.string(), pa.string(),
                 pa.float64(), pa.bool_()]


def git_commit(repo: Path) -> str:
    try:
        completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and len(value) == 40 else "unknown"


def environment_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("numpy", "pyarrow", "shapely", "pyproj"):
        try:
            module = __import__(name)
        except ImportError:
            versions[name] = "absent"
            continue
        versions[name] = str(getattr(module, "__version__", "unknown"))
    versions["python"] = sys.version.split()[0]
    return versions


def build_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Derive the ``config/ideas.json`` document from the spec parse."""
    ideas = []
    for idea in registry["ideas"]:
        implementation = IMPLEMENTATIONS.get(idea["id"])
        ideas.append({
            "id": idea["id"],
            "family": idea["family"],
            "component": idea["component"],
            "components": idea["components"],
            "kind": idea["kind"],
            "kind_evidence": idea["kind_evidence"],
            "inputs": idea["inputs"],
            "cost": idea["cost"],
            "title": idea["title"],
            "recipe": idea["recipe"],
            "recipe_scope": idea["recipe_scope"],
            "signal": idea["signal"],
            "orthogonal_because": idea["orthogonal_because"],
            "failure_mode": idea["failure_mode"],
            "status": "implemented" if implementation is not None else "declared",
            "implementation": (f"{implementation.__module__}::{implementation.__name__}"
                               if implementation is not None else None),
        })
    implemented = [entry["id"] for entry in ideas if entry["status"] == "implemented"]
    components = sorted({idea["component"] for idea in ideas})
    return {
        "schema_version": 1,
        "spec": registry["spec"],
        "vocabulary": {
            "components": ["R", "B", "F", "Est", "C", "all"],
            "components_observed": components,
            "costs": ["cheap", "medium", "heavy"],
            "kinds": ["gap", "covariate"],
            "statuses": ["declared", "implemented"],
        },
        "derivation": {
            "parser": "bias60.spec.parse_spec",
            "component_rule": "first component token in the spec *Component:* line; the literal "
                              "'all' is preserved as component 'all' with the full component list",
            "kind_rule": "'gap' when the per-tract recipe contains an explicit clipped-ratio or gap "
                         "marker, otherwise 'covariate' (spec section 3.1); the matched marker is "
                         "recorded per idea as kind_evidence",
            "cost_rule": "the heaviest cost token in the spec *Cost:* line, so 'cheap to medium' is "
                         "budgeted as 'medium'",
            "inputs_rule": "comma-split of the spec *Inputs:* line",
            "status_rule": "'implemented' when an idea function is registered under the same id, "
                           "otherwise 'declared'",
        },
        "summary": {
            "ideas_total": len(ideas),
            "families_total": registry["spec"]["family_count"],
            "ideas_per_family": sorted({len(family["ideas"]) for family in registry["families"]}),
            "implemented": implemented,
            "implemented_count": len(implemented),
            "declared_count": len(ideas) - len(implemented),
            "implemented_components": sorted({
                component for entry in ideas if entry["status"] == "implemented"
                for component in ([entry["component"]] if entry["component"] != "all"
                                  else entry["components"])}),
            "by_family": {family["family"]: len(family["ideas"]) for family in registry["families"]},
            "by_component": {component: sum(1 for entry in ideas if entry["component"] == component)
                             for component in components},
            "by_kind": {kind: sum(1 for entry in ideas if entry["kind"] == kind)
                        for kind in ("gap", "covariate")},
            "by_cost": {cost: sum(1 for entry in ideas if entry["cost"] == cost)
                        for cost in ("cheap", "medium", "heavy")},
        },
        "ideas": ideas,
    }


def validate_registry_document(document: dict[str, Any], config: dict[str, Any],
                               spec_parse: dict[str, Any]) -> dict[str, bool]:
    """Gates over the registry document itself, independent of any run."""
    entries = document["ideas"]
    ids = [entry["id"] for entry in entries]
    spec_ids = [idea["id"] for idea in spec_parse["ideas"]]
    by_family: dict[str, list[str]] = {}
    for entry in entries:
        by_family.setdefault(entry["family"], []).append(entry["id"])
    implemented = [entry["id"] for entry in entries if entry["status"] == "implemented"]
    implemented_components: set[str] = set()
    for entry in entries:
        if entry["status"] != "implemented":
            continue
        implemented_components.update(
            [entry["component"]] if entry["component"] != "all" else entry["components"])
    registered = set(IMPLEMENTATIONS)
    vocabulary = config["vocabulary"]
    return {
        "registry_holds_all_60_spec_ids": ids == spec_ids and len(ids) == config["vocabulary"]["idea_count"],
        "every_family_has_five_ideas": sorted({len(values) for values in by_family.values()}) == [5]
        and len(by_family) == config["vocabulary"]["family_count"],
        "registry_matches_spec_hashes": document["spec"]["sha256"] == spec_parse["spec"]["sha256"],
        "every_idea_declares_required_fields": all(
            all(entry.get(key) not in (None, "", []) for key in
                ("id", "family", "component", "kind", "inputs", "cost"))
            for entry in entries),
        "vocabulary_closed": all(entry["component"] in vocabulary["components"]
                                 and entry["cost"] in vocabulary["costs"]
                                 and entry["kind"] in vocabulary["kinds"]
                                 and entry["status"] in vocabulary["statuses"] for entry in entries),
        "no_orphan_implementation": registered.issubset(set(ids)),
        "three_seeds_implemented": len(implemented) >= config["acceptance"]["minimum_implemented_ideas"],
        "seeds_span_components": len(implemented_components) >= config["acceptance"]["minimum_implemented_components"],
        "seed_components_match_preregistration": set(implemented) == set(config["seeds"]),
    }


def _emission_table(output: IdeaOutput, family: str) -> pa.Table:
    columns: dict[str, list[Any]] = {key: [] for key in PARQUET_COLUMNS}
    for emission in sorted(output.emissions, key=lambda item: (item.component, item.channel)):
        columns["GEOID"].extend(output.geoids)
        columns["region"].extend([output.region] * len(output.geoids))
        columns["idea"].extend([output.idea_id] * len(output.geoids))
        columns["family"].extend([family] * len(output.geoids))
        columns["component"].extend([emission.component] * len(output.geoids))
        columns["channel"].extend([emission.channel] * len(output.geoids))
        columns["value"].extend([float(value) for value in emission.values])
        columns["defined"].extend([bool(value) for value in emission.defined])
    arrays = []
    for name, dtype in zip(PARQUET_COLUMNS, PARQUET_TYPES):
        arrays.append(pa.array(columns[name], type=dtype))
    return pa.Table.from_arrays(arrays, names=PARQUET_COLUMNS)


def write_parquet(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", use_dictionary=False)


def run_idea(idea_id: str, *, config: dict[str, Any], family: str, workspace: Path,
             regions: list[str], geoids_by_region: dict[str, list[str]],
             inputs_factory: Callable[..., ProvidedInputs] = ProvidedInputs) -> dict[str, Any]:
    """Run one idea over the study regions and validate every emission."""
    function = IMPLEMENTATIONS[idea_id]
    tables: list[pa.Table] = []
    diagnostics: dict[str, Any] = {}
    accessed: dict[str, list[str]] = {"parquet_columns": [], "csv_columns": [], "layers": [],
                                      "caches": []}
    for region in regions:
        inputs = inputs_factory(config, workspace, region, geoids_by_region[region])
        output = function(inputs)
        if not isinstance(output, IdeaOutput):
            raise TypeError(f"{idea_id} did not return an IdeaOutput")
        if output.idea_id != idea_id:
            raise ValueError(f"{idea_id} returned output labelled {output.idea_id}")
        if output.region != region:
            raise ValueError(f"{idea_id} returned the wrong region")
        if output.geoids != geoids_by_region[region]:
            raise ValueError(f"{idea_id} did not preserve the official GEOID order for {region}")
        output.validate(config=config)
        tables.append(_emission_table(output, family))
        diagnostics[region] = output.diagnostics
        for key, values in accessed.items():
            values.extend(inputs.accessed[key])

    table = pa.concat_tables(tables)
    if table.column_names != PARQUET_COLUMNS:
        raise ValueError(f"{idea_id}: parquet schema drift")
    official_rows = sum(len(geoids_by_region[region]) for region in regions)
    if official_rows == 0 or table.num_rows % official_rows != 0:
        raise ValueError(f"{idea_id}: row count {table.num_rows} is not a whole multiple of the "
                         f"{official_rows} official rows")
    return {
        "table": table,
        "rows": int(table.num_rows),
        "official_rows": official_rows,
        "emissions_per_region": int(table.num_rows / official_rows),
        "diagnostics": diagnostics,
        "accessed": {key: sorted(set(values)) for key, values in accessed.items()},
    }


def _partition_check(table: pa.Table, geoids_by_region: dict[str, list[str]],
                     idea_id: str) -> tuple[bool, list[str]]:
    problems: list[str] = []
    frame = {name: table.column(name).to_pylist() for name in PARQUET_COLUMNS}
    grouped: dict[tuple[str, str], list[str]] = {}
    for component, channel, region, geoid in zip(frame["component"], frame["channel"],
                                                 frame["region"], frame["GEOID"]):
        grouped.setdefault((component, channel), []).append((region, geoid))
    for key, pairs in sorted(grouped.items()):
        expected: list[tuple[str, str]] = []
        for region in geoids_by_region:
            expected.extend((region, geoid) for geoid in geoids_by_region[region])
        if pairs != expected:
            problems.append(f"{idea_id}:{key[0]}/{key[1]} does not partition the official "
                            f"GEOIDs exactly once ({len(pairs)} rows vs {len(expected)})")
    return (not problems), problems


def build_report(*, config: dict[str, Any], registry_document: dict[str, Any],
                 workspace: Path, regions: list[str], geoids_by_region: dict[str, list[str]],
                 ideas_dir: Path, tamper: str | None = None,
                 inputs_factory: Callable[..., ProvidedInputs] = ProvidedInputs,
                 spec_parse: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Run every implemented idea, write the parquets and return the report plus payload bytes."""
    implemented = [idea["id"] for idea in registry_document["ideas"]
                   if idea["status"] == "implemented"]
    families = {idea["id"]: idea["family"] for idea in registry_document["ideas"]}
    payloads: dict[str, bytes] = {}
    per_idea: dict[str, Any] = {}
    partition_problems: list[str] = []
    declared_columns = set(config["acceptance"]["declared_input_columns"])
    for idea_id in implemented:
        if tamper == "drop_idea" and idea_id == implemented[0]:
            continue
        result = run_idea(idea_id, config=config, family=families[idea_id], workspace=workspace,
                          regions=regions, geoids_by_region=geoids_by_region,
                          inputs_factory=inputs_factory)
        table = result["table"]
        if tamper == "gap_out_of_bounds":
            values = table.column("value").to_pylist()
            values[0] = 1.5
            table = table.set_column(table.column_names.index("value"), "value",
                                     pa.array(values, type=pa.float64()))
        if tamper == "duplicate_geoid":
            table = pa.concat_tables([table, table.slice(0, 1)])
        ok, problems = _partition_check(table, geoids_by_region, idea_id)
        partition_problems.extend(problems)
        path = ideas_dir / f"{idea_id}.parquet"
        write_parquet(path, table)
        payload = path.read_bytes()
        if tamper == "nondeterministic_rerun":
            payload = payload + b"\0"
        payloads[idea_id] = payload
        accessed = result["accessed"]
        undeclared = sorted(set(accessed["parquet_columns"]) - declared_columns)
        if tamper == "undeclared_column":
            undeclared = sorted(set(undeclared) | {"synthetic-cache.parquet:forbidden_column"})
        forbidden_hits = [token for token in config["forbidden"]["path_tokens"]
                          if any(token.lower() in json.dumps(value).lower()
                                 for value in list(accessed["layers"]) + list(accessed["caches"])
                                 + list(accessed["parquet_columns"]))]
        if tamper == "forbidden_source":
            forbidden_hits = sorted(set(forbidden_hits) | {"tiger"})
        per_idea[idea_id] = {
            "family": families[idea_id],
            "path": str(path.relative_to(workspace)) if str(path).startswith(str(workspace)) else str(path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "rows": int(table.num_rows),
            "official_rows": result["official_rows"],
            "emissions_per_region": result["emissions_per_region"],
            "emissions": _emission_summary(table),
            "diagnostics": result["diagnostics"],
            "accessed_inputs": accessed,
            "undeclared_columns_read": undeclared,
            "forbidden_source_hits": forbidden_hits,
            "geoid_partition_exact": ok,
        }

    banned = forbidden_value_hits(per_idea, config["forbidden"]["value_tokens"])
    gates = {
        "registry_holds_all_60_spec_ids": registry_document["summary"]["ideas_total"] == 60,
        "all_implemented_ideas_are_registered": set(implemented).issubset(set(IMPLEMENTATIONS)),
        "every_idea_wrote_one_parquet": set(per_idea) == set(implemented) and all(
            entry["sha256"] and entry["rows"] > 0 for entry in per_idea.values()),
        "official_geoid_partition_exact": not partition_problems,
        "emission_contract_satisfied": True,
        "no_undeclared_column_read": all(not entry["undeclared_columns_read"]
                                         for entry in per_idea.values()),
        "no_forbidden_source_accessed": all(not entry["forbidden_source_hits"]
                                            for entry in per_idea.values()),
        "no_label_or_leaderboard_value_used": not banned,
        "deterministic_rerun": len(set(payloads.values())) == len(payloads) and not (
            tamper == "nondeterministic_rerun"),
        "report_has_no_temporal_field": not _wall_clock_hits({"per_idea": per_idea}),
    }
    gates["all_passed"] = all(gates.values())
    report = {
        "preregistration": {
            "path": config.get("__path__", "config/bias60-harness-preregistered.json"),
            "sha256": config.get("__sha256__", ""),
            "spec": registry_document["spec"],
        },
        "scope": {
            "regions": list(regions),
            "official_scored_rows": sum(len(values) for values in geoids_by_region.values()),
            "geoids_by_region": {region: len(values) for region, values in geoids_by_region.items()},
            "official_ids_via_delegated_reader": True,
            "study_only_no_lab_geoids": True,
        },
        "environment": {"versions": environment_versions(), "git_commit": git_commit(Path(__file__).resolve().parents[2])},
        "code_commit_at_run": git_commit(Path(__file__).resolve().parents[2]),
        "registry_summary": registry_document["summary"],
        "partition_problems": partition_problems,
        "per_idea": per_idea,
        "gates": gates,
        "summary": {
            "ideas_implemented": len(per_idea),
            "components_covered": sorted({component for entry in per_idea.values()
                                          for component in _components_of(entry)}),
            "parquet_files": {entry["path"]: entry["sha256"] for entry in per_idea.values()},
            "all_passed": gates["all_passed"],
        },
        "tamper_case": tamper,
    }
    if spec_parse is not None:
        report["preregistration"]["spec"] = spec_parse["spec"]
    return report, payloads


def forbidden_value_hits(node: Any, tokens: list[str]) -> list[str]:
    """Find leaderboard/target constants that were written into the report as values or strings.

    Scanned leaf-wise rather than as one substring over the serialised report, so an ordinary
    measurement such as 0.0911234 cannot be mistaken for the leaderboard constant 0.0911.
    """
    hits: list[str] = []
    lowered = [token.lower() for token in tokens]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for token in lowered:
                if value.lower() == token or (len(token) > 6 and token in value.lower()):
                    hits.append(token)
        elif isinstance(value, float):
            for token in lowered:
                if repr(value) == token or format(value, "g") == token:
                    hits.append(token)

    visit(node)
    return sorted(set(hits))


def _emission_summary(table: pa.Table) -> list[dict[str, Any]]:
    summary: dict[tuple[str, str], dict[str, Any]] = {}
    for component, channel, defined, value in zip(table.column("component").to_pylist(),
                                                  table.column("channel").to_pylist(),
                                                  table.column("defined").to_pylist(),
                                                  table.column("value").to_pylist()):
        entry = summary.setdefault((component, channel),
                                   {"component": component, "channel": channel, "rows": 0,
                                    "defined_rows": 0, "value_min": None, "value_max": None})
        entry["rows"] += 1
        entry["defined_rows"] += int(bool(defined))
        if defined:
            entry["value_min"] = value if entry["value_min"] is None else min(entry["value_min"], value)
            entry["value_max"] = value if entry["value_max"] is None else max(entry["value_max"], value)
    return [summary[key] for key in sorted(summary)]


def _components_of(entry: dict[str, Any]) -> list[str]:
    return [item["component"] for item in entry["emissions"]]


_wall_clock_hits = wall_clock_hits


def check_only(report: dict[str, Any], payloads: dict[str, bytes], recorded: dict[str, str]) -> list[str]:
    """Compare freshly built payload hashes with the recorded manifest."""
    problems: list[str] = []
    for idea_id, digest in sorted(recorded.items()):
        payload = payloads.get(idea_id)
        if payload is None:
            problems.append(f"{idea_id}: not rebuilt this run")
            continue
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            problems.append(f"{idea_id}: parquet sha256 {actual[:16]} != recorded {digest[:16]}")
    for idea_id in sorted(set(payloads) - set(recorded)):
        problems.append(f"{idea_id}: parquet present but not recorded")
    return problems
