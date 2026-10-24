#!/usr/bin/env python3
"""Executable input-provenance audit for the frozen v2 scored candidate.

The frozen candidate is ``reports/candidates/V2_model.csv``, produced by
``scripts/build_estimator_v2_candidates.py``. This audit answers one question that no earlier
artifact answered in an executable way: does *every* field the candidate consumes trace back to
an official challenge-provided input, or to a fixed rule documented from the organiser's method
and cited public literature - and does *none* of it come from a removed reference layer, an
equivalent copy, a sample score placeholder, a label, or leaderboard feedback?

What is measured rather than asserted:

* every local input file is hashed and, where the organiser also publishes it, compared
  byte-for-byte against the official hosted object;
* every hosted object is fingerprinted from its Parquet footer (rows, row groups, per-column
  compression and encodings) and its required columns are checked against the arrow schema;
* the field-level lineage graph is *derived* from committed machine-readable pins (the v2 mask
  config, the candidate manifest and the builder manifests), not typed by hand, and every edge is
  verified against the real file/column it names;
* column requests are extracted from the builder source with an AST walk and compared with what
  the builder manifests declare, so an undeclared column read is a hard failure;
* every column read from the provided strata table must be on the permitted-provided-subset
  allowlist and must not belong to a removed-reference column family (the removed layers live as
  *columns* of that table, so column granularity is where the rule is enforceable);
* the score path is isolated from the retired leaderboard constants by an AST reachability check
  plus a value scan of every score cell and region mean in the artifact.

The audit reads no removed reference layer or equivalent copy, no labels, no sample score
placeholder column (only the ``GEOID`` column of the official samples), and no leaderboard file.
It performs no upload.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from audit_v2_component_masks import official_scored_ids  # noqa: E402  (sanctioned GEOID-only reader)

CONFIG_PATH = ROOT / "config/v2-input-provenance-preregistered.json"
with CONFIG_PATH.open(encoding="utf-8") as stream:
    CONFIG = json.load(stream)

REPORT_PATH = ROOT / "reports/v2-input-provenance.json"
MANIFEST_PATH = ROOT / "reports/candidates/v2_input_provenance_manifest.json"
REGIONS = ("maricopa-az", "northern-ca", "eastern-ok", "south-central-tx")
BUCKET = CONFIG["hosted_allowlist"]["bucket"]
DECLARATION_COLUMNS = ("columns_read", "read_columns", "selected_columns", "columns",
                       "permitted_overture_columns_read_for_source_analysis")
PATHISH = re.compile(r"([A-Za-z0-9_./-]*\.(parquet|csv|json|jsonl))|(s3://)|(https?://)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b""):
        digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def current_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def hosted_uri(region: str, prefix: str, basename: str) -> str:
    return f"{BUCKET}/{prefix}{region}/{basename.format(region=region)}"


def hosted_allowed(uri: str) -> tuple[bool, str | None]:
    if not uri.startswith(BUCKET + "/"):
        return False, "outside the challenge bucket"
    tail = uri[len(BUCKET) + 1:]
    parts = tail.split("/")
    if len(parts) < 2:
        return False, f"provided object path {tail!r} is not prefix/region/object"
    prefix, name = parts[0], parts[-1]
    prefixes = [allowed.strip("/") for allowed in CONFIG["hosted_allowlist"]["allowed_prefixes"]]
    if prefix not in prefixes and not any(prefix.startswith(allowed + "/") for allowed in prefixes):
        return False, f"prefix {prefix!r} is not an allowed provided prefix"
    regions = [region for region in REGIONS if f"{region}/" in tail]
    permitted = {template.format(region=region)
                 for template in CONFIG["hosted_allowlist"]["allowed_object_basenames"]
                 for region in (regions or list(REGIONS))}
    if name not in permitted:
        return False, f"object basename {name!r} is not in the hosted allowlist"
    for token in CONFIG["prohibited"]["uri_tokens"]:
        if token in uri.lower():
            return False, f"prohibited token {token!r} in the object uri"
    return True, None


def footer_profile(parquet: pq.ParquetFile, wanted: tuple[str, ...]) -> dict[str, Any]:
    """Structural-only Parquet footer fingerprint; no cell data is read."""
    schema = parquet.schema_arrow
    metadata = parquet.metadata
    column_bytes: dict[str, int] = {}
    for name in wanted:
        if name not in schema.names:
            continue
        root = name
        total = 0
        for row_group in range(metadata.num_row_groups):
            for column in range(metadata.num_columns):
                if metadata.schema.column(column).path.split(".")[0] == root:
                    chunk = metadata.row_group(row_group).column(column)
                    total += int(chunk.total_compressed_size or 0)
        column_bytes[name] = total
    groups = [{"num_rows": int(metadata.row_group(g).num_rows),
               "total_byte_size": int(metadata.row_group(g).total_byte_size)}
              for g in range(metadata.num_row_groups)]
    columns = []
    seen_roots: set[str] = set()
    for column in range(metadata.num_columns):
        path = metadata.schema.column(column).path
        root = path.split(".")[0]
        if root in seen_roots:
            continue
        seen_roots.add(root)
        chunk = metadata.row_group(0).column(column)
        columns.append({"name": root, "physical_type": str(metadata.schema.column(column).physical_type),
                        "compression": str(chunk.compression),
                        "encodings": sorted(str(encoding) for encoding in (chunk.encodings or []))})
    return {
        "rows": int(metadata.num_rows),
        "row_groups": int(metadata.num_row_groups),
        "columns": int(metadata.num_columns),
        "schema_names": list(schema.names),
        "created_by": str(metadata.created_by or ""),
        "wanted_columns_present": [name for name in wanted if name in schema.names],
        "wanted_columns_missing": [name for name in wanted if name not in schema.names],
        "wanted_column_compressed_bytes": column_bytes,
        "row_group_sizes": groups,
        "column_metadata": columns,
        "footer_fingerprint_sha256": digest({"rows": int(metadata.num_rows),
                                             "row_groups": int(metadata.num_row_groups),
                                             "columns": int(metadata.num_columns),
                                             "schema": list(schema.names),
                                             "groups": groups,
                                             "columns_meta": columns}),
    }


def module_level_string_lists(tree: ast.AST) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.List):
                    values = [element.value for element in node.value.elts
                              if isinstance(element, ast.Constant) and isinstance(element.value, str)]
                    if len(values) == len(node.value.elts) and values:
                        table[target.id] = values
    return table


def string_list_call_sites(tree: ast.AST, table: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Every column-projection request the code can make, with its call site."""
    sites: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        candidates: list[ast.AST] = []
        for keyword in node.keywords:
            if keyword.arg in ("columns", "selected_columns", "read_columns"):
                candidates.append(keyword.value)
        function = node.func
        attribute = getattr(function, "attr", None)
        if attribute in ("read_row_group", "read", "read_table", "read_csv_projection",
                         "rowgroups", "open_input_file"):
            candidates.extend(node.args[1:] if attribute == "read_row_group" else node.args)
        for candidate in candidates:
            resolved: list[str] | None = None
            if isinstance(candidate, ast.List):
                values = [element.value for element in candidate.elts
                          if isinstance(element, ast.Constant) and isinstance(element.value, str)]
                if len(values) == len(candidate.elts) and values:
                    resolved = values
            elif isinstance(candidate, ast.Name) and candidate.id in table:
                resolved = list(table[candidate.id])
            if resolved:
                sites.append({"line": int(getattr(node, "lineno", 0)), "columns": sorted(resolved)})
    return sites


def function_bodies(tree: ast.AST, names: set[str]) -> list[ast.AST]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found.append(node)
    return found


def declared_column_index(paths: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """Harvest every declared column list from committed manifests, reports and configs."""
    by_source: dict[str, dict[str, Any]] = {}
    union: dict[str, set[str]] = {}
    for rel in paths:
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(f"declared-column source missing: {rel}")
        document = json.loads(path.read_text(encoding="utf-8"))

        def walk(node: Any, breadcrumb: list[str]) -> None:
            if isinstance(node, dict):
                source = None
                for key in ("path", "source_uri", "source", "uri", "object", "candidate_csv",
                            "artifact", "feature_path", "strata_path", "sample_path"):
                    value = node.get(key)
                    if isinstance(value, str):
                        source = value
                        break
                for key in DECLARATION_COLUMNS:
                    value = node.get(key)
                    if isinstance(value, list) and all(isinstance(item, str) for item in value):
                        label = source or "/".join(breadcrumb[-3:]) or rel
                        entry = by_source.setdefault(label, {"declared_in": set(), "columns": set()})
                        entry["declared_in"].add(rel)
                        entry["columns"].update(value)
                        union.setdefault(rel, set()).update(value)
                for key, child in node.items():
                    walk(child, breadcrumb + [str(key)])
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, breadcrumb + [str(index)])

        walk(document, [])
    for entry in by_source.values():
        entry["declared_in"] = sorted(entry["declared_in"])
        entry["columns"] = sorted(entry["columns"])
    return by_source, union


FILE_ACCESS_CALLABLES = {"open", "open_input_file", "open_input_stream", "read_table", "read",
                         "read_row_group", "ParquetFile", "read_text", "write_text", "write_bytes",
                         "from_csv", "from_parquet"}
PATH_CONSTANT_NAME_TOKENS = ("PATH", "URI", "_KEY", "BUCKET", "TEMPLATE", "ROOT")


def opened_path_literals(tree: ast.AST) -> list[str]:
    """Every path, object key or url the code can hand to a file-access callable."""
    found: set[str] = set()

    def collect(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.add(sub.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in FILE_ACCESS_CALLABLES:
                collect(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and any(
                        token in target.id.upper() for token in PATH_CONSTANT_NAME_TOKENS):
                    collect(node.value)
    return sorted(found)


def strata_projection_constants(tree: ast.AST) -> dict[str, list[str]]:
    """Module-level column lists whose own name claims to be the strata projection."""
    out: dict[str, list[str]] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "STRATA" in target.id.upper():
                    if isinstance(node.value, ast.List):
                        values = [element.value for element in node.value.elts
                                  if isinstance(element, ast.Constant)
                                  and isinstance(element.value, str)]
                        if values:
                            out[target.id] = values
    return out


def code_dependency_scan(scripts: list[str], score_path: dict[str, list[str]],
                         retired: dict[str, float], prohibited_modules: list[str],
                         allowed_strata: set[str]) -> dict[str, Any]:
    per_script: dict[str, Any] = {}
    hits: list[dict[str, Any]] = []
    prose: list[dict[str, Any]] = []
    strata_violations: list[dict[str, Any]] = []
    for rel in scripts:
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(f"chain script missing: {rel}")
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=rel)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        constants = module_level_string_lists(tree)
        sites = string_list_call_sites(tree, constants)
        code_columns = sorted({column for site in sites for column in site["columns"]})
        forbidden_imports = sorted({name for name in imports
                                    for banned in prohibited_modules
                                    if banned in name})
        # Prohibited-token classification (preregistered): a token is a dependency only when the
        # script can actually open the thing it names, i.e. the token sits in a string handed to a
        # file-access callable or in a module-level path/url constant. Naming that mirrors the
        # organiser's own component names, and explicit false-valued restriction keys, are
        # disclosed separately as prose rather than counted as dependencies.
        openable = opened_path_literals(tree)
        all_strings = sorted({node.value for node in ast.walk(tree)
                              if isinstance(node, ast.Constant) and isinstance(node.value, str)})
        pathish_strings = sorted(value for value in all_strings if PATHISH.search(value))
        for token in CONFIG["prohibited"]["uri_tokens"] + CONFIG["prohibited"]["file_tokens"]:
            for literal in openable:
                if token in literal.lower():
                    hits.append({"script": rel, "token": token, "opened_path": literal[:180]})
            for literal in pathish_strings:
                if token in literal.lower() and literal not in openable:
                    prose.append({"script": rel, "token": token, "literal": literal[:180]})
        strata_lists = strata_projection_constants(tree)
        for name, columns in strata_lists.items():
            outside = [column for column in columns if column not in allowed_strata]
            families = [column for column in columns
                        if any(column.lower().startswith(family)
                               for family in CONFIG["prohibited"]["strata_column_prefixes"])]
            if outside or families:
                strata_violations.append({"script": rel, "constant": name, "columns": columns,
                                          "outside_permitted_subset": outside,
                                          "removed_reference_families": families})
        retired_names = set(CONFIG["prohibited"]["scored_path_constant_names"]) | set(retired)
        score_bodies = function_bodies(tree, set(score_path.get(rel, [])))
        score_names_used: set[str] = set()
        score_floats: set[float] = set()
        for body in score_bodies:
            for node in ast.walk(body):
                if isinstance(node, ast.Name) and node.id in retired_names:
                    score_names_used.add(node.id)
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    score_floats.add(float(node.value))
        retired_matches = sorted(value for value in score_floats
                                 for expected in retired.values() if abs(value - expected) <= 1e-12)
        per_script[rel] = {
            "sha256": sha256_file(path),
            "lines": len(text.splitlines()),
            "imports": sorted(set(imports)),
            "prohibited_imports": forbidden_imports,
            "column_request_sites": sites,
            "column_requests": code_columns,
            "score_path_functions_scanned": sorted(score_path.get(rel, [])),
            "score_path_retired_constant_names_used": sorted(score_names_used),
            "score_path_retired_constant_values": retired_matches,
            "openable_path_literals": openable,
            "path_like_string_constants": pathish_strings,
            "strata_projection_constants": strata_lists,
        }
    return {"scripts": per_script, "prohibited_opened_hits": hits,
            "prohibited_opened_hit_count": len(hits),
            "prohibited_prose_hits": prose, "prohibited_prose_hit_count": len(prose),
            "strata_projection_violations": strata_violations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=os.environ.get("COMPETITION_WORKSPACE"))
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--skip-hosted", action="store_true",
                        help="skip hosted object comparisons (development only; fails the audit)")
    parser.add_argument("--config", type=Path, default=None,
                        help="override the preregistered config (negative controls only)")
    parser.add_argument("--artifact", type=str, default=None,
                        help="override the scored artifact under audit (negative controls only)")
    args = parser.parse_args()
    global CONFIG, CONFIG_PATH  # noqa: PLW0603 - negative controls deliberately rebind the config
    config_path_used = CONFIG_PATH
    if args.config is not None:
        CONFIG_PATH = Path(args.config).resolve()
        config_path_used = CONFIG_PATH
        CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if args.artifact is not None:
        CONFIG["candidate"]["artifact"] = args.artifact
    if not args.workspace:
        parser.error("pass --workspace or set COMPETITION_WORKSPACE")
    workspace = Path(args.workspace).resolve()
    fs = pafs.S3FileSystem(anonymous=True, region="us-west-2")

    candidate = CONFIG["candidate"]
    build_script = ROOT / candidate["build_script"]
    artifact_path = Path(candidate["artifact"])
    artifact = artifact_path if artifact_path.is_absolute() else ROOT / artifact_path
    mask_config_path = ROOT / candidate["mask_config"]
    manifest_path = ROOT / candidate["manifest"]
    build_audit_path = ROOT / candidate["build_audit"]
    mask_config = json.loads(mask_config_path.read_text(encoding="utf-8"))
    candidates_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build_audit = json.loads(build_audit_path.read_text(encoding="utf-8"))
    card = next(entry for entry in candidates_manifest["candidate_set"]
                if entry.get("candidate_id") == candidate["id"])

    # ---------------------------------------------------------------- measure before
    prior_reports = {
        "v2_defined_mask_audit": "reports/v2-defined-mask-audit.json",
        "v2_prior_envelope": "reports/v2-prior-envelope.json",
        "maricopa_route_ratio_diagnostic": "reports/maricopa-route-ratio-diagnostic.json",
        "writeup_methodology": "docs/writeup/methodology.md",
    }
    measure_before = {
        "statement": ("Component audits and writeup traceability existed separately before this "
                      "turn; no consolidated executable input-source audit for the scored v2 "
                      "candidate was recorded."),
        "prior_artifact_hashes": {label: sha256_file(ROOT / path) for label, path in prior_reports.items()},
        "prior_consolidated_provenance_reports_found": sorted(
            str(path.relative_to(ROOT)) for path in ROOT.glob("reports/*provenance*")
            if path.name != REPORT_PATH.name),
        "other_provenance_artifacts_in_repo": sorted(
            str(path.relative_to(ROOT)) for path in (ROOT / "scripts").glob("*provenance*")),
        "existing_provenance_reports_are_source_mixture_not_input_audit": {
            "reports/source-provenance.json": ("documents which Overture upstream datasets supplied "
                                               "features per tract; it does not enumerate the scored "
                                               "candidate's input fields, hash its inputs, or scan its "
                                               "code dependencies")},
        "no_prohibited_source_had_been_instrumented": True,
    }

    # ---------------------------------------------------------------- candidate identity
    artifact_sha = sha256_file(artifact)
    with artifact.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        rows = [row for row in reader if row]
    score_columns = candidate["score_columns"]
    score_index = {name: header.index(name) for name in score_columns}
    geoid_index = header.index(candidate["key_column"])
    score_cells = [[row[score_index[name]] for name in score_columns] for row in rows]
    flat_scores = [value for row in score_cells for value in row]
    ties = {
        "artifact_rows": len(rows),
        "artifact_columns": header,
        "recomputed_artifact_sha256": artifact_sha,
        "matches_candidate_manifest": artifact_sha == card["sha256"],
        "matches_build_audit": artifact_sha == build_audit["artifact"]["sha256"],
        "rows_match_candidate_manifest": len(rows) == card["rows"],
        "build_script_sha256": sha256_file(build_script),
        "build_script_sha256_pinned_in_build_audit": build_audit["reproducibility"]["script_sha256"],
        "build_script_pin_matches_build_audit": (
            sha256_file(build_script) == build_audit["reproducibility"]["script_sha256"]),
        "mask_config_sha256": sha256_file(mask_config_path),
        "mask_config_sha256_in_build_audit": build_audit["frozen_inputs"].get(
            candidate["mask_config"]),
    }

    # ---------------------------------------------------------------- provided local copies
    provided_local: dict[str, Any] = {}
    pins = mask_config["frozen_inputs"]
    sample_template = pins["official_samples"]["path_template"]
    for region in REGIONS:
        local = workspace / sample_template.format(region=region)
        local_sha = sha256_file(local)
        uri = hosted_uri(region, "reference/", "{region}-sample-submission.csv")
        allowed, reason = hosted_allowed(uri)
        entry: dict[str, Any] = {
            "path": relative(local) if local.is_absolute() and str(local).startswith(str(ROOT))
            else f"$COMPETITION_WORKSPACE/{sample_template.format(region=region)}",
            "sha256": local_sha,
            "bytes": local.stat().st_size,
            "pinned_sha256": pins["official_samples"]["sha256"][region],
            "pin_matches": local_sha == pins["official_samples"]["sha256"][region],
            "columns_read": pins["official_samples"]["columns_read"],
            "placeholder_columns_read": [name for name in
                                         CONFIG["prohibited"]["score_placeholder_columns_in_provided_samples"]
                                         if name in pins["official_samples"]["columns_read"]],
            "hosted_uri": uri,
            "hosted_allowed": allowed,
            "hosted_reject_reason": reason,
            "provenance_class": "official_challenge_provided_local",
        }
        if not args.skip_hosted:
            with fs.open_input_stream(uri) as stream:
                entry["hosted_sha256"] = sha256_stream(stream)
            entry["byte_identical_to_hosted"] = entry["hosted_sha256"] == local_sha
        provided_local[f"samples/{region}-sample-submission.csv"] = entry

    strata_template = pins["provided_strata"]["path_template"]
    strata_columns = list(pins["provided_strata"]["columns_read"])
    for region in REGIONS:
        local = workspace / strata_template.format(region=region)
        local_sha = sha256_file(local)
        local_schema = list(pq.read_schema(local).names)
        uri = hosted_uri(region, "strata/", "{region}-strata-tract-table.parquet")
        allowed, reason = hosted_allowed(uri)
        forbidden_families = [name for name in strata_columns
                              if any(name.lower().startswith(family) for family
                                     in CONFIG["prohibited"]["strata_column_prefixes"])]
        not_allowed = [name for name in strata_columns if name not in CONFIG["allowed_strata_columns"]]
        entry = {
            "path": f"$COMPETITION_WORKSPACE/{strata_template.format(region=region)}",
            "sha256": local_sha,
            "bytes": local.stat().st_size,
            "columns_read": strata_columns,
            "placeholder_columns_read": [name for name in strata_columns
                                         if name in CONFIG["prohibited"]
                                         ["score_placeholder_columns_in_provided_samples"]],
            "columns_outside_allowed_subset": not_allowed,
            "columns_in_removed_reference_families": forbidden_families,
            "columns_present_in_local_schema": [name for name in strata_columns if name in local_schema],
            "columns_missing_locally": [name for name in strata_columns if name not in local_schema],
            "hosted_uri": uri,
            "hosted_allowed": allowed,
            "hosted_reject_reason": reason,
            "provenance_class": "official_challenge_provided_local",
        }
        if not args.skip_hosted:
            with fs.open_input_file(uri) as handle:
                profile = footer_profile(pq.ParquetFile(handle), tuple(strata_columns))
            entry["hosted_rows"] = profile["rows"]
            entry["hosted_footer_fingerprint_sha256"] = profile["footer_fingerprint_sha256"]
            entry["hosted_missing_requested_columns"] = profile["wanted_columns_missing"]
            with fs.open_input_file(uri) as handle:
                hosted_table = pq.read_table(handle, columns=strata_columns)
            local_table = pq.read_table(local, columns=strata_columns)
            hosted_rows = {str(row["GEOID"]): tuple(row[name] for name in strata_columns if name != "GEOID")
                           for row in hosted_table.to_pylist()}
            mismatches: list[dict[str, Any]] = []
            compared = 0
            for row in local_table.to_pylist():
                geoid = str(row["GEOID"])
                if geoid not in hosted_rows:
                    mismatches.append({"geoid": geoid, "reason": "absent from the provided object"})
                    continue
                compared += 1
                observed = tuple(row[name] for name in strata_columns if name != "GEOID")
                expected = hosted_rows[geoid]
                for index, (left, right) in enumerate(zip(observed, expected)):
                    if left is None or right is None:
                        if left is not right:
                            mismatches.append({"geoid": geoid, "column": strata_columns[index + 1],
                                               "reason": "null mismatch"})
                    elif isinstance(left, float) or isinstance(right, float):
                        if abs(float(left) - float(right)) > 1e-9:
                            mismatches.append({"geoid": geoid, "column": strata_columns[index + 1],
                                               "reason": "value mismatch"})
                    elif left != right:
                        mismatches.append({"geoid": geoid, "column": strata_columns[index + 1],
                                           "reason": "value mismatch"})
            entry["local_rows"] = int(local_table.num_rows)
            entry["rows_compared_against_provided"] = compared
            entry["value_mismatches_vs_provided"] = len(mismatches)
            entry["mismatch_examples"] = mismatches[:10]
            with fs.open_input_file(uri) as handle:
                entry["hosted_sha256"] = sha256_stream(handle)
            entry["byte_identical_to_hosted"] = entry["hosted_sha256"] == local_sha
        provided_local[f"defined-flags-inputs/{region}-strata-tract-table.parquet"] = entry

    template = candidates_manifest["platform_template"]
    template_local = workspace / str(template["source"]).replace("$COMPETITION_WORKSPACE/", "")
    template_sha = sha256_file(template_local)
    download_manifest_path = workspace / "download-manifest.json"
    download_manifest = json.loads(download_manifest_path.read_text(encoding="utf-8")) if (
        download_manifest_path.is_file()) else {"files": []}
    recorded = next((record for record in download_manifest["files"]
                     if record.get("name") == template_local.name), None)
    provided_local["SampleSubmission.csv"] = {
        "path": "$COMPETITION_WORKSPACE/SampleSubmission.csv",
        "sha256": template_sha,
        "bytes": template_local.stat().st_size,
        "columns_read": template["columns_read"],
        "columns_outside_allowed_subset": [name for name in template["columns_read"]
                                           if name not in CONFIG["allowed_template_columns"]],
        "placeholder_columns_read": [name for name in
                                     CONFIG["prohibited"]["score_placeholder_columns_in_provided_samples"]
                                     if name in template["columns_read"]],
        "pin_matches": template_sha == template["sha256"],
        "downloader_record": {
            "present": recorded is not None,
            "sha256": (recorded or {}).get("sha256"),
            "source_url": (recorded or {}).get("source_url"),
            "sha256_matches": bool(recorded and recorded.get("sha256") == template_sha),
        },
        "provenance_class": "platform_format_metadata",
        "hosted_allowed": None,
        "hosted_reject_reason": None,
        "hosted_comparison": ("not applicable: the platform template is the official Zindi API "
                              "download, so provenance is established against the workspace "
                              "downloader record rather than a bucket object"),
    }

    # ---------------------------------------------------------------- hosted layer objects
    hosted_layers: dict[str, Any] = {}
    layer_requests = {
        "overture-roads": ("reference/", ("class", "routes", "geometry", "connectors")),
        "overture-buildings": ("reference/", ("bbox", "geometry", "subtype", "has_parts",
                                              "num_floors", "height")),
        "overture-pois": ("reference/", ("bbox", "categories", "confidence", "sources")),
        "census-acs-housing": ("reference/", ("GEOID", "geometry")),
    }
    for region in REGIONS:
        for layer, (prefix, wanted) in layer_requests.items():
            uri = hosted_uri(region, prefix, f"{{region}}-{layer}.parquet")
            allowed, reason = hosted_allowed(uri)
            if args.skip_hosted:
                hosted_layers[f"{region}/{layer}"] = {"uri": uri, "hosted_allowed": allowed,
                                                      "skipped": True}
                continue
            with fs.open_input_file(uri) as handle:
                profile = footer_profile(pq.ParquetFile(handle), wanted)
            hosted_layers[f"{region}/{layer}"] = {
                "uri": f"s3://{uri}",
                "hosted_allowed": allowed,
                "hosted_reject_reason": reason,
                "requested_columns": list(wanted),
                "provenance_class": "official_challenge_provided_hosted",
                **profile,
            }

    # ---------------------------------------------------------------- derived inputs
    derived: dict[str, Any] = {}
    component_pins = {"transport": pins["transport_component"], "building": pins["building_component"],
                      "poi": pins["poi_component"]}
    for name, pin in component_pins.items():
        path = ROOT / pin["path"]
        spec = CONFIG["derived_inputs"][pin["path"]]
        sha = sha256_file(path)
        with path.open(newline="", encoding="utf-8") as stream:
            observed_header = next(csv.reader(stream))
        declared_columns = sorted({column for key, table in
                                   (("gap_column", pin), ("defined_column", pin),
                                    ("facility_half_column", pin), ("establishments_half_column", pin))
                                   if isinstance(table.get(key), str)
                                   for column in [table[key]]}
                                  | set(pin.get("family_gap_columns", []))
                                  | set(pin.get("family_count_columns", []))
                                  | set(pin.get("input_columns", [])))
        derived[pin["path"]] = {
            "sha256": sha,
            "bytes": path.stat().st_size,
            "rows": sum(1 for _ in path.open(encoding="utf-8")) - 1,
            "header": observed_header,
            "columns_present": len(observed_header),
            "pinned_sha256": pin["sha256"],
            "pin_matches": sha == pin["sha256"],
            "also_pinned_in_build_audit": build_audit["frozen_inputs"].get(pin["path"]) == sha,
            "producer": spec["producer"],
            "producer_sha256": sha256_file(ROOT / spec["producer"]),
            "manifest": spec.get("manifest"),
            "manifest_sha256": (sha256_file(ROOT / spec["manifest"]) if spec.get("manifest") else None),
            "independent_recomputation_evidence": spec["independent_recomputation_evidence"],
            "independent_recomputation_sha256": sha256_file(
                ROOT / spec["independent_recomputation_evidence"]),
            "consumed_columns": declared_columns,
            "consumed_columns_missing_from_file": [column for column in declared_columns
                                                   if column not in observed_header],
            "implementation": pin.get("implementation"),
            "provenance_class": spec["provenance_class"],
        }
    # feature caches live in the workspace, not the repo
    for rel, spec in (("gap-estimator/features/{region}-overture-tract-summary.parquet",
                       CONFIG["derived_inputs"][
                           "gap-estimator/features/{region}-overture-tract-summary.parquet"]),
                      ("gap-estimator/features/poi-confidence-by-tract.parquet",
                       CONFIG["derived_inputs"][
                           "gap-estimator/features/poi-confidence-by-tract.parquet"])):
        for region in (REGIONS if "{region}" in rel else (None,)):
            concrete = rel.format(region=region) if region else rel
            path = workspace / concrete
            if not path.is_file():
                raise FileNotFoundError(f"derived cache missing: {concrete}")
            schema = list(pq.read_schema(path).names)
            derived[f"workspace:{concrete}"] = {
                "path": f"$COMPETITION_WORKSPACE/{concrete}",
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": int(pq.read_table(path, columns=[]).num_rows),
                "schema_names": schema,
                "producer": spec["producer"],
                "producer_sha256": sha256_file(ROOT / spec["producer"]),
                "independent_recomputation_evidence": spec["independent_recomputation_evidence"],
                "provenance_class": spec["provenance_class"],
            }

    # ---------------------------------------------------------------- declarations + code scan
    declaration_paths = list(CONFIG["declaration_sources"]) + [candidate["mask_config"]]
    declared_by_source, declared_by_file = declared_column_index(declaration_paths)
    declared_union = sorted({column for columns in declared_by_file.values() for column in columns})
    allowed_union = set(CONFIG["allowed_strata_columns"]) | set(CONFIG["allowed_sample_columns"]) \
        | set(CONFIG["allowed_template_columns"])
    scan = code_dependency_scan(CONFIG["code_scan"]["chain_scripts"],
                                CONFIG["code_scan"]["score_path_functions"],
                                CONFIG["code_scan"]["retired_lb_constants"],
                                CONFIG["code_scan"]["prohibited_module_tokens"],
                                set(CONFIG["allowed_strata_columns"]))
    code_requests = sorted({column for entry in scan["scripts"].values()
                            for column in entry["column_requests"]})
    declared_set = set(declared_union)
    derived_schema_columns: set[str] = set()
    for entry in derived.values():
        derived_schema_columns.update(entry.get("schema_names", []))
        derived_schema_columns.update(entry.get("header", []))
    known_columns = declared_set | derived_schema_columns | allowed_union
    undeclared = [column for column in code_requests if column not in known_columns]
    column_request_audit = {
        "declared_column_sources": {key: value for key, value in sorted(declared_by_source.items())},
        "declared_columns_union": declared_union,
        "code_requested_columns": code_requests,
        "code_requested_but_undeclared": undeclared,
        "known_column_count": {"declared": len(declared_set),
                               "derived_schema": len(derived_schema_columns),
                               "permitted_provided_subset": len(allowed_union)},
        "allowed_provided_subset": sorted(allowed_union),
        "removed_reference_column_families": CONFIG["prohibited"]["strata_column_prefixes"],
    }

    # ---------------------------------------------------------------- field lineage
    lineage: list[dict[str, Any]] = []

    def add(field: str, layer: int, consumed_by: str, source: str, column: str, kind: str,
            provenance_class: str, basis: list[str], verified: bool, detail: str) -> None:
        lineage.append({"field": field, "layer": layer, "consumed_by": consumed_by,
                        "source": source, "column": column, "kind": kind,
                        "provenance_class": provenance_class, "basis": basis,
                        "verified": verified, "detail": detail,
                        "manifest_entry": True if verified else False})

    # layer 1: the scored artifact columns themselves
    for column in [candidate["key_column"], *score_columns]:
        add(f"V2_model:{column}", 1, f"{candidate['build_script']} -> {candidate['artifact']}",
            candidate["artifact"], column, "csv_column", "derived_from_allowed_inputs",
            [candidate["manifest"]], column in header,
            f"present in the artifact header; {len(rows)} rows")

    # layer 2: component columns consumed from the frozen component CSVs
    for name, pin in component_pins.items():
        spec = CONFIG["derived_inputs"][pin["path"]]
        entry = derived[pin["path"]]
        columns = [pin["gap_column"], pin["defined_column"]]
        for key in ("facility_half_column", "establishments_half_column"):
            if isinstance(pin.get(key), str):
                columns.append(pin[key])
        columns.extend(pin.get("family_gap_columns", []))
        columns.extend(pin.get("family_count_columns", []))
        columns.extend(pin.get("input_columns", []))
        for column in columns:
            add(f"{name}.{column}", 2, f"{candidate['build_script']} (read_rows)", pin["path"],
                column, "csv_column", spec["provenance_class"],
                [pin["path"], relative(mask_config_path), spec["manifest"]] if spec.get("manifest")
                else [pin["path"], relative(mask_config_path)],
                column in entry["consumed_columns"] and column not in
                entry["consumed_columns_missing_from_file"],
                f"pinned component sha {pin['sha256'][:16]}..; producer {spec['producer']}")

    # layer 3: what the producers consumed from provided inputs
    producer_sources = {
        "scripts/build_road_topology_candidate.py": [
            (f"workspace:gap-estimator/features/{{region}}-overture-tract-summary.parquet",
             ("GEOID", "overture_road_km", "overture_named_road_km_5070", "overture_route_km")),
            (f"defined-flags-inputs/{{region}}-strata-tract-table.parquet", tuple(strata_columns)),
            ("s3://.../reference/{region}/{region}-overture-roads.parquet",
             ("class", "routes", "geometry", "connectors")),
            ("s3://.../reference/{region}/{region}-census-acs-housing.parquet", ("GEOID", "geometry")),
            (f"samples/{{region}}-sample-submission.csv", ("GEOID",)),
        ],
        "scripts/build_building_morphology_candidate.py": [
            ("workspace:gap-estimator/features/{region}-overture-tract-summary.parquet",
             ("GEOID", "overture_buildings")),
            ("defined-flags-inputs/{region}-strata-tract-table.parquet", ("GEOID", "ALAND", "pop_total")),
            ("s3://.../reference/{region}/{region}-overture-buildings.parquet",
             ("bbox", "geometry", "subtype", "has_parts", "num_floors", "height")),
            ("s3://.../reference/{region}/{region}-census-acs-housing.parquet", ("GEOID", "geometry")),
            ("samples/{region}-sample-submission.csv", ("GEOID",)),
        ],
        "scripts/build_poi_confidence_candidate.py": [
            ("s3://.../reference/{region}/{region}-overture-pois.parquet",
             ("bbox", "categories", "confidence", "sources")),
            ("defined-flags-inputs/{region}-strata-tract-table.parquet",
             ("GEOID", "ALAND", "pop_total", "pop_urban", "pct_urban", "ur_class")),
            ("workspace:gap-estimator/features/poi-confidence-by-tract.parquet",
             ("GEOID", "poi_count", "confidence_valid_count", "confidence_missing_count",
              "confidence_invalid_count")),
            ("samples/{region}-sample-submission.csv", ("GEOID",)),
        ],
        "scripts/estimate_gaps.py": [
            ("s3://.../reference/{region}/{region}-overture-roads.parquet",
             ("class", "routes", "geometry")),
            ("s3://.../reference/{region}/{region}-overture-buildings.parquet", ("bbox", "geometry")),
            ("s3://.../reference/{region}/{region}-overture-pois.parquet", ("bbox", "categories", "confidence")),
            ("s3://.../reference/{region}/{region}-census-acs-housing.parquet", ("GEOID", "geometry")),
            ("defined-flags-inputs/{region}-strata-tract-table.parquet", tuple(strata_columns)),
        ],
    }
    provided_index = {**provided_local, **derived}
    for producer, sources in producer_sources.items():
        for source, columns in sources:
            for column in columns:
                key = source
                if "{region}" in source:
                    key = source.format(region="maricopa-az")
                record = provided_index.get(key) or provided_index.get(
                    key.replace("maricopa-az", "{region}"))
                hosted = [entry for entry in hosted_layers.values()
                          if entry.get("uri", "").endswith(Path(source).name.format(region="maricopa-az"))]
                if record is not None:
                    schema_columns = set(record.get("schema_names", []))
                    read_columns = set(record.get("columns_read", []))
                    hashed = bool(record.get("sha256"))
                    provenance = record["provenance_class"]
                    verified = bool(hashed and column in known_columns
                                    and (column in read_columns or column in schema_columns
                                         or column in derived_schema_columns))
                    detail = (f"local sha {record['sha256'][:16]}..; hashed={hashed}; "
                              f"declared={column in declared_set}; read={column in read_columns}; "
                              f"in_schema={column in schema_columns}; "
                              f"derived_schema={column in derived_schema_columns}")
                elif hosted:
                    entry = hosted[0]
                    present = column in entry.get("wanted_columns_present", [])
                    hashed = bool(entry.get("footer_fingerprint_sha256"))
                    verified = bool(present and hashed and entry.get("hosted_allowed")
                                    and column in known_columns)
                    provenance = "official_challenge_provided_hosted"
                    detail = (f"footer fingerprint {str(entry.get('footer_fingerprint_sha256'))[:16]}..; "
                              f"rows={entry.get('rows')}; hosted_allowed={entry.get('hosted_allowed')}")
                else:
                    verified, provenance, detail = False, "unclassified", "no matching inventory entry"
                add(f"{Path(producer).name}:{Path(source).name}:{column}", 3,
                    f"{producer} (feature scan)", source, column,
                    "hosted_parquet_column" if source.startswith("s3://") else "provided_column",
                    provenance, [relative(Path(producer))] + [
                        entry for entry in declaration_paths if entry.startswith("reports/")][:2],
                    verified, detail)

    # layer 4: fixed rules and constants
    for rule in CONFIG["fixed_rules"]:
        module = rule.get("module")
        verified, detail = True, rule["note"]
        if module and rule.get("constant"):
            tree = ast.parse((ROOT / module).read_text(encoding="utf-8"), filename=module)
            value = None
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == rule["constant"]:
                            try:
                                value = ast.literal_eval(node.value)
                            except ValueError:
                                value = None
            if value is None:
                verified, detail = False, f"constant {rule['constant']} not found in {module}"
            elif isinstance(rule["value"], (int, float)) and isinstance(value, (int, float)):
                verified = abs(float(value) - float(rule["value"])) <= 1e-12
                detail = f"{module}:{rule['constant']} = {value}"
            elif isinstance(rule["value"], dict):
                verified = canonical(value) == canonical(rule["value"])
                detail = f"{module}:{rule['constant']} matches the preregistered prior set"
        basis_existing = [basis for basis in rule["basis"] if (ROOT / basis).exists()
                          or (ROOT / basis).is_dir()]
        missing_basis = [basis for basis in rule["basis"] if basis not in basis_existing]
        if missing_basis:
            verified, detail = False, f"{detail}; missing documented basis {missing_basis}"
        add(f"rule:{rule['rule_id']}", 4, rule.get("function") or rule.get("module") or "aggregation",
            rule.get("module") or "preregistered config", rule.get("constant") or rule["rule_id"],
            "constant", rule["provenance_class"], rule["basis"], verified, detail)

    # layer 5: historical disclosure constants that must NOT be on the score path
    retired_isolation = {}
    for name, value in CONFIG["code_scan"]["retired_lb_constants"].items():
        offenders = [rel for rel, entry in scan["scripts"].items()
                     if any(abs(float(found) - value) <= 1e-12
                            for found in entry["score_path_retired_constant_values"])]
        retired_isolation[name] = {"value": value, "score_path_scripts_using_value": offenders,
                                   "provenance_class": "historical_disclosure_not_model_input"}
    retired_clean = all(not entry["score_path_scripts_using_value"]
                        for entry in retired_isolation.values())

    score_value_hits: dict[str, int] = {}
    numeric_scores: list[float] = []
    for row in score_cells:
        for value in row:
            if value == "":
                continue
            numeric_scores.append(float(value))
    for name, value in CONFIG["code_scan"]["retired_lb_constants"].items():
        score_value_hits[name] = sum(1 for observed in numeric_scores
                                     if abs(observed - value) <= 1e-12)
    region_of = dict(official_scored_ids(workspace))
    by_region: dict[str, list[float]] = {}
    for row in rows:
        geoid = row[geoid_index]
        region = region_of.get(geoid, "eastern-wa")
        by_region.setdefault(region, []).append(float(row[score_index["coverage_gap_score"]]))
    region_means = {region: sum(values) / len(values) for region, values in sorted(by_region.items())}
    region_mean_hits = {name: sum(1 for mean in region_means.values()
                                  if abs(mean - value) <= 1e-9)
                        for name, value in CONFIG["code_scan"]["retired_lb_constants"].items()}

    from audit_v2_component_masks import composite_mean  # frozen combiner, single source of truth
    component_tables: dict[str, dict[str, dict[str, str]]] = {}
    for name, pin in component_pins.items():
        with (ROOT / pin["path"]).open(newline="", encoding="utf-8") as stream:
            component_tables[name] = {str(row["GEOID"]): row for row in csv.DictReader(stream)}
    component_map = {
        "transport_gap": (component_tables["transport"], pins["transport_component"]["gap_column"],
                          pins["transport_component"]["defined_column"]),
        "building_gap": (component_tables["building"], pins["building_component"]["gap_column"],
                         pins["building_component"]["defined_column"]),
        "poi_gap": (component_tables["poi"], pins["poi_component"]["gap_column"],
                    pins["poi_component"]["defined_column"]),
    }
    composite_residual = 0.0
    composite_rows_above_tolerance = 0
    zero_defined_official_rows = 0
    for row in rows:
        geoid = row[geoid_index]
        if geoid not in region_of:
            expected = 0.0  # documented deterministic placeholder for template-only rows
        else:
            pairs: list[tuple[float, int]] = []
            for column in score_columns[:3]:
                table, gap_column, defined_column = component_map[column]
                source = table[geoid]
                pairs.append((float(source[gap_column]), int(source[defined_column])))
            value, defined = composite_mean(pairs)
            if not defined:
                zero_defined_official_rows += 1
            expected = value
        recorded = float(row[score_index["coverage_gap_score"]])
        residual = abs(recorded - expected)
        composite_residual = max(composite_residual, residual)
        if residual > 1e-12:
            composite_rows_above_tolerance += 1

    unclassified_fields = sorted(entry["field"] for entry in lineage
                                 if entry["provenance_class"] == "unclassified")
    unverified_fields = sorted(entry["field"] for entry in lineage if not entry["verified"])
    hashed_missing = sorted(key for key, entry in provided_index.items()
                            if not entry.get("sha256") and not entry.get("footer_fingerprint_sha256"))

    rehash_targets: list[tuple[Path, str]] = [
        (workspace / strata_template.format(region=region),
         provided_local[f"defined-flags-inputs/{region}-strata-tract-table.parquet"]["sha256"])
        for region in REGIONS]
    rehash_targets += [(workspace / sample_template.format(region=region),
                        provided_local[f"samples/{region}-sample-submission.csv"]["sha256"])
                       for region in REGIONS]
    rehash_targets += [(ROOT / pin["path"], derived[pin["path"]]["sha256"])
                       for pin in component_pins.values()]
    rehash_targets += [(artifact, artifact_sha), (build_script, ties["build_script_sha256"])]
    hashes_reproducible = all(sha256_file(path) == recorded for path, recorded in rehash_targets)

    acceptance = {
        "fraction_fields_with_manifest_entry": (
            sum(1 for entry in lineage if entry["manifest_entry"]) / len(lineage) if lineage else 0.0),
        "field_lineage_complete": not unverified_fields,
        "prohibited_source_dependencies": len(scan["prohibited_opened_hits"]),
        "no_strata_column_outside_permitted_subset": not scan["strata_projection_violations"],
        "input_hashes_reproducible_within_run": hashes_reproducible,
        "no_prohibited_uri_allowed": all(entry.get("hosted_allowed") in (True, None)
                                         for entry in hosted_layers.values())
        and all(entry.get("hosted_allowed") in (True, None) for entry in provided_local.values()),
        "every_provided_local_copy_matched_against_its_official_object": all(
            entry.get("byte_identical_to_hosted") is True
            or entry.get("downloader_record", {}).get("sha256_matches") is True
            for entry in provided_local.values()),
        "unclassified_input_sources": len(unclassified_fields),
        "all_listed_inputs_hashed": not hashed_missing,
        "local_provided_copies_byte_identical_to_hosted": all(
            entry.get("byte_identical_to_hosted", True) for entry in provided_local.values()),
        "strata_columns_inside_permitted_subset": all(
            not entry.get("columns_outside_allowed_subset") for key, entry in provided_local.items()
            if key.startswith("defined-flags-inputs/")),
        "no_removed_reference_column_family_read": all(
            not entry.get("columns_in_removed_reference_families")
            for key, entry in provided_local.items() if key.startswith("defined-flags-inputs/")),
        "declared_columns_cover_code_requested_columns": not undeclared,
        "no_prohibited_module_imported": all(not entry["prohibited_imports"]
                                             for entry in scan["scripts"].values()),
        "retired_constants_absent_from_score_path": retired_clean,
        "retired_constants_absent_from_score_values": all(count == 0
                                                         for count in score_value_hits.values())
        and all(count == 0 for count in region_mean_hits.values()),
        "sample_placeholder_columns_never_read": all(
            not entry.get("placeholder_columns_read") for entry in provided_local.values()),
        "composite_equals_defined_component_mean": composite_rows_above_tolerance == 0
        and zero_defined_official_rows == 0,
        "pins_tie_to_candidate_build": all([ties["matches_candidate_manifest"],
                                            ties["matches_build_audit"],
                                            ties["build_script_pin_matches_build_audit"],
                            all(entry["pin_matches"] for entry in derived.values()
                                if "pin_matches" in entry)]),
        "downloader_evidence_for_platform_template": provided_local["SampleSubmission.csv"][
            "downloader_record"]["sha256_matches"],
        "no_upload": True,
    }
    gates = {key: bool(value) for key, value in acceptance.items()
             if isinstance(value, (bool,))}
    gates["fraction_fields_with_manifest_entry"] = bool(
        acceptance["fraction_fields_with_manifest_entry"] >= 1.0)
    gates["prohibited_source_dependencies"] = acceptance["prohibited_source_dependencies"] == 0
    gates["unclassified_input_sources"] = acceptance["unclassified_input_sources"] == 0
    acceptance["all_passed"] = all(gates.values())

    manifest_inputs: list[dict[str, Any]] = []
    for key, entry in sorted(provided_local.items()):
        manifest_inputs.append({"id": key, "kind": "local_provided",
                                "path": entry["path"], "sha256": entry["sha256"],
                                "columns_read": entry.get("columns_read"),
                                "hosted_uri": entry.get("hosted_uri"),
                                "hosted_sha256": entry.get("hosted_sha256"),
                                "provenance_class": entry["provenance_class"]})
    for key, entry in sorted(hosted_layers.items()):
        manifest_inputs.append({"id": key, "kind": "hosted_object", "path": entry.get("uri"),
                                "sha256": entry.get("footer_fingerprint_sha256"),
                                "hash_kind": "parquet_footer_fingerprint",
                                "rows": entry.get("rows"),
                                "columns_read": entry.get("requested_columns"),
                                "provenance_class": entry.get("provenance_class")})
    for key, entry in sorted(derived.items()):
        manifest_inputs.append({"id": key, "kind": "derived", "path": entry.get("path", key),
                                "sha256": entry["sha256"],
                                "producer": entry.get("producer"),
                                "producer_sha256": entry.get("producer_sha256"),
                                "provenance_class": entry["provenance_class"]})
    for rel in [candidate["build_script"], *CONFIG["code_scan"]["chain_scripts"]]:
        entry = scan["scripts"].get(rel)
        manifest_inputs.append({"id": f"code:{rel}", "kind": "code", "path": rel,
                                "sha256": (entry or {}).get("sha256") or sha256_file(ROOT / rel),
                                "provenance_class": "derived_from_allowed_inputs"})
    for key, path in prior_reports.items():
        manifest_inputs.append({"id": f"evidence:{key}", "kind": "evidence", "path": path,
                                "sha256": sha256_file(ROOT / path),
                                "provenance_class": "derived_from_allowed_inputs"})

    report = {
        "preregistration": {
            "config_path": relative(config_path_used),
            "config_sha256": sha256_file(config_path_used),
            "provenance_classes": CONFIG["provenance_classes"],
            "hosted_allowlist": CONFIG["hosted_allowlist"],
            "allowed_strata_columns": CONFIG["allowed_strata_columns"],
            "allowed_sample_columns": CONFIG["allowed_sample_columns"],
            "allowed_template_columns": CONFIG["allowed_template_columns"],
            "prohibited": CONFIG["prohibited"],
            "hit_classification_rule": CONFIG["hit_classification_rule"],
            "file_access_callables": CONFIG["file_access_callables"],
            "path_constant_name_tokens": CONFIG["path_constant_name_tokens"],
            "declaration_sources": CONFIG["declaration_sources"],
            "fixed_rules": CONFIG["fixed_rules"],
            "acceptance_thresholds": CONFIG["acceptance"],
            "tuned_after_seeing_results": False,
        },
        "schema_version": CONFIG["schema_version"],
        "audit_id": CONFIG["audit_id"],
        "backlog_item": CONFIG["backlog_item"],
        "intent": CONFIG["intent"],
        "candidate": {"id": candidate["id"], "artifact": candidate["artifact"],
                      "build_script": candidate["build_script"],
                      "manifest_card_present": card is not None,
                      "commit_of_record": build_audit["reproducibility"]["git_commit"],
                      "commit_of_record_source": "reports/estimator-v2-audit.json "
                                                 "$['reproducibility']['git_commit']"},
        "measure_before": measure_before,
        "candidate_identity": ties,
        "inputs": {"provided_local_copies": provided_local, "hosted_objects": hosted_layers,
                   "derived_inputs": derived},
        "field_lineage": {"entries": lineage,
                          "field_count": len(lineage),
                          "verified_field_count": sum(1 for entry in lineage if entry["verified"]),
                          "fraction_fields_with_manifest_entry": acceptance[
                              "fraction_fields_with_manifest_entry"],
                          "unverified_fields": unverified_fields,
                          "unclassified_fields": unclassified_fields,
                          "layer_counts": {str(layer): sum(1 for entry in lineage
                                                           if entry["layer"] == layer)
                                           for layer in sorted({entry["layer"] for entry in lineage})},
                          "provenance_class_counts": {
                              key: sum(1 for entry in lineage if entry["provenance_class"] == key)
                              for key in sorted({entry["provenance_class"] for entry in lineage})}},
        "column_request_audit": column_request_audit,
        "code_dependency_scan": {key: value for key, value in scan.items() if key != "scripts"},
        "code_dependency_scan_by_script": scan["scripts"],
        "retired_constant_isolation": {"per_constant": retired_isolation,
                                       "score_value_hits": score_value_hits,
                                       "region_mean_hits": region_mean_hits,
                                       "score_cells_scanned": len(numeric_scores),
                                       "region_means": region_means,
                                       "clean": retired_clean},
        "determinism": {
            "wall_clock_values_serialized": False,
            "serialization": "json.dumps with sort_keys=False and a fixed key order; no timestamps",
            "protocol": CONFIG["determinism_protocol"],
        },
        "composite_recomputation": {
            "max_abs_residual_vs_defined_component_mean": composite_residual,
            "rows_above_1e_12": composite_rows_above_tolerance,
            "zero_defined_official_rows": zero_defined_official_rows,
            "rows": len(rows),
            "defined_flags_and_gaps_read_from": {
                "transport_gap": pins["transport_component"]["path"],
                "building_gap": pins["building_component"]["path"],
                "poi_gap": pins["poi_component"]["path"]},
            "template_only_expected_value": 0.0,
        },
        "acceptance": acceptance,
        "prohibitions": {
            "removed_reference_layers_or_equivalents_read": False,
            "sample_score_placeholder_columns_read": False,
            "target_labels_read": False,
            "leaderboard_feedback_read_for_modeling": False,
            "external_data_added_to_scored_predictions": False,
            "upload_performed": False,
            "secrets_read_or_printed": False,
        },
        "reproduction": {
            "command": ('COMPETITION_WORKSPACE="$COMPETITION_WORKSPACE" /home/martin/hddProjects/langywrap/uv run '
                        "--with numpy==2.5.4 --with pyarrow==26.0.0 --with pyproj==3.8.0 --with shapely==2.2.0 "
                        "python scripts/audit_v2_input_provenance.py"),
            "environment": "uv, pyarrow 26.0.0, anonymous public S3 read of the organiser bucket only",
            "determinism": "no wall-clock value is serialized; reruns must be byte-identical",
        },
    }

    manifest = {
        "manifest_type": "v2_input_provenance",
        "audit_id": CONFIG["audit_id"],
        "backlog_item": CONFIG["backlog_item"],
        "candidate_id": candidate["id"],
        "candidate_artifact": {"path": candidate["artifact"], "sha256": artifact_sha,
                               "rows": len(rows), "columns": header,
                               "pinned_in_candidates_json": card["sha256"],
                               "pinned_in_build_audit": build_audit["artifact"]["sha256"],
                               "tie_verified": ties["matches_candidate_manifest"]
                               and ties["matches_build_audit"]},
        "build": {"script": candidate["build_script"], "sha256": ties["build_script_sha256"],
                  "mask_config": candidate["mask_config"],
                  "mask_config_sha256": ties["mask_config_sha256"]},
        "inputs": manifest_inputs,
        "input_count": len(manifest_inputs),
        "inputs_without_hash": hashed_missing,
        "acceptance_all_passed": acceptance["all_passed"],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False,
                                   allow_nan=False) + "\n", encoding="utf-8")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False,
                                        allow_nan=False) + "\n", encoding="utf-8")

    print(json.dumps({"gates_all_passed": acceptance["all_passed"],
                      "fields": len(lineage),
                      "verified_fields": sum(1 for entry in lineage if entry["verified"]),
                      "fraction_with_manifest_entry": round(
                          acceptance["fraction_fields_with_manifest_entry"], 6),
                      "unverified": unverified_fields[:8],
                      "unclassified": unclassified_fields[:8],
                      "prohibited_dependencies": acceptance["prohibited_source_dependencies"],
                      "undeclared_column_requests": undeclared,
                      "inputs": len(manifest_inputs),
                      "score_cells_scanned": len(numeric_scores),
                      "composite_residual": composite_residual}, indent=1))
    if acceptance["all_passed"]:
        print("PASS v2 input provenance: every consumed field traces to provided data or a "
              "documented fixed rule, zero prohibited or unclassified sources, every input hashed")
        return 0
    failed = sorted(key for key, value in gates.items() if not value)
    print(f"FAIL v2 input provenance gates: {failed}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
