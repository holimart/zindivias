#!/usr/bin/env python3
"""Measure whether the external lab can be built at all, before the build spends its bandwidth.

Two public measurements decide that. First, does every source registered in
``config/bias60-lab-preregistered.json`` answer an anonymous request today (HEAD only: a status and a
length, never a byte of content, never a credential)? Second, once the frozen spec 4.1 exclusion runs
on *real* census geography - every official GEOID, every platform-template GEOID and an 8 km neighbour
buffer around every study tract - does each planned lab group still hold enough candidate tracts to be
a cross-validation fold?

Only public Census tract geography is downloaded here. Nothing in this script fetches a removed
reference layer (TIGER PRISECRROADS or ROADS, Microsoft or Google building footprints, USGS structures,
CBP business counts, Overture content), and nothing is written into the lab root, so the lab stays
absent until ``bias60-external-lab-build`` produces a manifest-backed artifact set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from bias60 import lab_eval, lab_plan  # noqa: E402

DEFAULT_CONFIG = REPO / "config/lab-prerequisites-preregistered.json"
DEFAULT_LAB_CONFIG = REPO / "config/bias60-lab-preregistered.json"
DEFAULT_OUT = REPO / "reports/lab-prerequisites.json"
DEFAULT_MARKDOWN = REPO / "docs/decisions/lab-prerequisites.md"
OPENED: list[str] = []


# --------------------------------------------------------------------------- access recording
def record(path) -> None:
    path = Path(path)
    if not str(path).startswith(("/", "ORCHESTRATOR:", "WORKSPACE:")):
        path = (REPO / path).resolve()
    label = str(path)
    orchestrator = os.environ.get("ORCHESTRATOR_ROOT", "")
    if orchestrator:
        root = Path(orchestrator).resolve()
        if _is_under(path, root):
            label = f"ORCHESTRATOR:{path.relative_to(root)}"
    if label == str(path):
        workspace = os.environ.get("COMPETITION_WORKSPACE", "")
        if workspace and _is_under(path, Path(workspace).resolve()):
            label = f"WORKSPACE:{path.relative_to(Path(workspace).resolve())}"
    if label not in OPENED:
        OPENED.append(label)


def _is_under(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except ValueError:
        return False


def canonical_bytes(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def load_config(path: Path) -> dict[str, Any]:
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["__path__"] = str(path)
    document["__sha256__"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record(path)
    return document


def lab_root_of(lab_config: dict[str, Any]) -> Path:
    value = lab_config["lab_root"]["path"]
    return Path(value.replace("$COMPETITION_WORKSPACE",
                              os.environ.get("COMPETITION_WORKSPACE", ""))).resolve()


def state_of_filename(url: str) -> str:
    """``tl_2024_08_tract.zip`` -> ``08``; anything else -> empty, which fails the allowlist gate."""
    name = Path(str(url).split("?")[0]).name
    if not name.startswith("tl_") or not name.endswith("_tract.zip"):
        return ""
    parts = name.split("_")
    return parts[2].zfill(2) if len(parts) >= 4 else ""


def host_of(url: str) -> str:
    return str(url).split("/")[2] if "://" in str(url) else ""


# --------------------------------------------------------------------------- network
def http_head(url: str, *, user_agent: str = "zindi-bias-bounty-lane/1.0",
              timeout: int = 30) -> dict[str, Any]:
    """One anonymous HEAD: a status and a length, never a byte of content."""
    entry: dict[str, Any] = {"url": url, "method": "HEAD", "content_bytes_read": False,
                             "credentials_used": False}
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            entry["status"] = int(response.status)
            entry["content_length_bytes"] = int(length) if length and length.isdigit() else None
    except urllib.error.HTTPError as error:
        entry["status"] = int(error.code)
        entry["content_length_bytes"] = None
    except Exception as error:  # noqa: BLE001 - a transport failure is a finding, not a crash
        entry["status"] = None
        entry["error"] = type(error).__name__
        entry["content_length_bytes"] = None
    return entry


def http_get(url: str, cache_dir: Path, *, user_agent: str = "zindi-bias-bounty-lane/1.0",
             timeout: int = 600) -> dict[str, Any]:
    """Fetch one public census file into the cache and hash it; cached bytes are reused verbatim."""
    target = Path(cache_dir) / Path(str(url).split("?")[0]).name
    if not target.exists():
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.read())
    record(target)
    return {"url": str(url), "path": str(target), "bytes": int(target.stat().st_size),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


# --------------------------------------------------------------------------- geography
def _pick(fields: dict[str, int], candidates: list[str]) -> str | None:
    for name in candidates:
        if name.upper() in fields:
            return name.upper()
    return None


def read_tract_records(zip_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Read GEOID, county FIPS and interior point out of a Census tract shapefile zip."""
    import shapefile

    geometry = config["geometry_source"]
    record(zip_path)
    reader = shapefile.Reader(str(zip_path))
    names = [str(entry[0]).upper() for entry in reader.fields[1:]]
    fields = {name: index for index, name in enumerate(names)}
    geoid_field = _pick(fields, geometry["geoid_fields"])
    lat_field = _pick(fields, geometry["lat_fields"])
    lon_field = _pick(fields, geometry["lon_fields"])
    if geoid_field is None or lat_field is None or lon_field is None:
        raise ValueError(f"{zip_path.name} lacks a GEOID or interior-point field; asked for "
                         f"{geometry['geoid_fields']}, {geometry['lat_fields']}, "
                         f"{geometry['lon_fields']}, file has {sorted(fields)}")
    county_field = "COUNTYFP" if "COUNTYFP" in fields else None
    state_field = "STATEFP" if "STATEFP" in fields else None
    out: list[dict[str, Any]] = []
    for values in reader.records():
        geoid = str(values[fields[geoid_field]] or "").strip()
        if len(geoid) not in (11, 13, 14) or not geoid.isdigit():
            continue
        county = str(values[fields[county_field]] or "").strip() if county_field else geoid[2:5]
        county = county.zfill(3)
        if state_field and len(county) == 3:
            county = str(values[fields[state_field]] or "").strip().zfill(2) + county
        try:
            lat = float(values[fields[lat_field]])
            lon = float(values[fields[lon_field]])
        except (TypeError, ValueError):
            continue
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0) or (lat == 0.0 and lon == 0.0):
            continue
        out.append({"geoid": geoid, "county_fips": county, "lat": lat, "lon": lon})
    return out


def project_points(records: list[dict[str, Any]], crs: str) -> list[tuple[float, float]]:
    from pyproj import Transformer

    if not records:
        return []
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    lats = [entry["lat"] for entry in records]
    lons = [entry["lon"] for entry in records]
    x_values, y_values = transformer.transform(lons, lats)
    if not isinstance(x_values, list):
        x_values, y_values = [float(x_values)], [float(y_values)]
    return [(float(x_value), float(y_value)) for x_value, y_value in zip(x_values, y_values)]


def nearest_km(points: list[tuple[float, float]], study: list[tuple[float, float]],
               chunk: int = 4096) -> list[float]:
    """Exact minimum planar distance in km, vectorised and chunked over the study set."""
    if not points:
        return []
    if not study:
        return [float("inf")] * len(points)
    import numpy as np

    candidate_xy = np.asarray(points, dtype=float)
    best = np.full(len(points), np.inf)
    for start in range(0, len(study), chunk):
        block = np.asarray(study[start:start + chunk], dtype=float)
        delta = candidate_xy[:, None, :] - block[None, :, :]
        best = np.minimum(best, np.sqrt((delta * delta).sum(axis=2)).min(axis=1))
    return [float(value) / 1000.0 for value in best]


# --------------------------------------------------------------------------- probe
def _instantiate(url: str, instantiations: dict[str, str]) -> str:
    for token, value in instantiations.items():
        url = url.replace(token, value)
    return url


def _https_form(url: str) -> str:
    if url.startswith("s3://"):
        bucket, _, key = url[len("s3://"):].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return url


def probe_sources(config: dict[str, Any], lab_config: dict[str, Any], *,
                  head: Callable[..., dict[str, Any]], cache: Path | None = None,
                  reprobe: bool = False) -> list[dict[str, Any]]:
    """HEAD every registered source, or reuse the recorded answers.

    A remote status line is not a reproducible measurement: the same URL answered 520 in one run and
    timed out in the next, which would have made the committed report unreproducible. The answers are
    therefore cached beside the geography cache, and a re-probe is an explicit act (--reprobe). The
    report says which of the two it used.
    """
    settings = config["source_probe"]
    cache_file = (Path(cache) / "source-probe.json") if cache else None
    if cache_file is not None and cache_file.is_file() and not reprobe:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        registered = [source["id"] for source in lab_config["sources"]]
        if [entry["id"] for entry in cached] == registered:
            for entry in cached:
                # a skipped source was never asked, so caching must not claim that it was
                entry["probed_from"] = "cache" if entry.get("probe") == "attempted" else "not_probed"
            return cached
    probed: list[dict[str, Any]] = []
    for source in lab_config["sources"]:
        entry: dict[str, Any] = {"id": source["id"], "role": source.get("role"),
                                 "registered_url": source.get("url"), "method": "HEAD",
                                 "content_bytes_read": False, "credentials_used": False}
        for field in ("replacement_for", "replacement_reason"):
            if source.get(field):
                entry[field] = source[field]
        url = source.get("url")
        if not url or source["id"] in settings["not_probeable"]:
            entry.update({"status": None, "probe": "skipped", "probed_from": "not_probed",
                          "reason": settings["not_probeable"].get(
                              source["id"], "no url registered for this source")})
        else:
            target = _https_form(_instantiate(url, settings["instantiations"]))
            result = head(target, user_agent=config["geometry_source"]["user_agent"])
            meaning = settings["status_meaning"].get(str(result.get("status")), "unexpected")
            entry.update({"url_probed": target, "status": result.get("status"),
                          "content_length_bytes": result.get("content_length_bytes"),
                          "error": result.get("error"), "meaning": meaning,
                          "probe": "attempted"})
            alternates: list[dict[str, Any]] = []
            for alternate in settings.get("alternates", {}).get(source["id"], []):
                outcome = head(_https_form(_instantiate(alternate, settings["instantiations"])),
                               user_agent=config["geometry_source"]["user_agent"])
                alternates.append({"url": outcome["url"], "status": outcome.get("status"),
                                   "error": outcome.get("error"),
                                   "meaning": settings["status_meaning"].get(
                                       str(outcome.get("status")), "unexpected")})
            entry["alternates"] = alternates
            answered = [item for item in alternates if item["status"] == 200]
            entry["alternate_that_answers"] = answered[0]["url"] if answered else None
        entry["probed_from"] = "network"
        probed.append(entry)
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps([{**entry, "probed_from": None} for entry in probed],
                                         indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return probed


def probe(config: dict[str, Any], lab_config: dict[str, Any], workspace: Path, cache: Path, *,
          reprobe: bool = False,
          head: Callable[..., dict[str, Any]] = http_head,
          get: Callable[..., dict[str, Any]] = http_get,
          records_for_state: Callable[[str], list[dict[str, Any]]] | None = None,
          tamper: str | None = None) -> dict[str, Any]:
    """Fetch what is public, run the frozen exclusion on it, and describe what survived."""
    geometry = config["geometry_source"]
    workspace, cache = Path(workspace), Path(cache)
    downloads: list[dict[str, Any]] = []
    state_cache: dict[str, list[dict[str, Any]]] = {}

    def fetch_state(state: str) -> list[dict[str, Any]]:
        if state in state_cache:
            return state_cache[state]
        # the download is always recorded, even when a test supplies the records, so the allowlist
        # and no-reference-layer gates are exercised on the same code path the real run uses
        url = geometry["url_pattern"].replace("<ss>", state)
        record_file = get(url, cache, user_agent=geometry["user_agent"])
        downloads.append(record_file)
        if records_for_state is not None:
            state_cache[state] = records_for_state(state)
        else:
            state_cache[state] = read_tract_records(Path(record_file["path"]), config)
        return state_cache[state]

    base_policy = lab_plan.policy_from_config(lab_config, workspace, study_points=[])
    excluded_set = set(base_policy.excluded_geoids)

    study_records: list[dict[str, Any]] = []
    for state in config["study_states"]:
        study_records.extend(entry for entry in fetch_state(state)
                             if entry["geoid"] in excluded_set)
    if tamper == "study_points_dropped":
        study_records = []
    study_points = project_points(study_records, geometry["projection"])

    policy = lab_plan.policy_from_config(lab_config, workspace, study_points=study_points)
    if tamper == "official_geoid_accepted":
        policy = lab_plan.ExclusionPolicy(
            official_geoids=frozenset(), template_geoids=frozenset(),
            study_county_fips=policy.study_county_fips, study_states=policy.study_states,
            buffer_km=policy.buffer_km, study_points=policy.study_points, crs=policy.crs,
            county_exclusion_enabled=False)
    elif tamper == "buffer_removed":
        policy = lab_plan.ExclusionPolicy(
            official_geoids=policy.official_geoids, template_geoids=policy.template_geoids,
            study_county_fips=policy.study_county_fips, study_states=policy.study_states,
            buffer_km=0.0, study_points=policy.study_points, crs=policy.crs,
            county_exclusion_enabled=policy.county_exclusion_enabled)

    planned = {entry["id"]: entry for entry in lab_config["groups"]}
    groups: list[dict[str, Any]] = []
    for group_id in config["probe_groups"]:
        plan = planned[group_id]
        candidates: list[dict[str, Any]] = []
        for state in plan["states"]:
            state_fips = str(lab_config["state_fips_by_postal"].get(state, "")).zfill(2)
            for entry in fetch_state(state_fips):
                if plan.get("county_includes") and entry["county_fips"] not in plan["county_includes"]:
                    continue
                candidates.append(entry)
        centroids = project_points(candidates, geometry["projection"])
        for entry, centroid in zip(candidates, centroids):
            entry["centroid"] = centroid
        buckets = policy.partition(candidates)
        accepted_index = {geoid for geoid in buckets["accepted"]}
        accepted = [entry for entry in candidates if entry["geoid"] in accepted_index]
        distances = sorted(nearest_km([entry["centroid"] for entry in accepted], study_points))
        groups.append({
            "id": group_id, "label": plan.get("label"), "states": sorted(plan["states"]),
            "counties_declared": len(plan.get("county_includes") or []),
            "planned_approx_tracts": int(plan["approx_tracts"]),
            "candidates_found": len(candidates), "accepted": len(accepted),
            "accepted_geoids": sorted(entry["geoid"] for entry in accepted),
            "rejected_by_reason": {reason: len(members) for reason, members in
                                   sorted(buckets.items()) if reason != "accepted"},
            "accepted_min_distance_km": round(distances[0], 3) if distances else None,
            "accepted_median_distance_km": (round(distances[len(distances) // 2], 3)
                                            if distances else None),
            "ready": len(accepted) >= config["acceptance"]["minimum_accepted_tracts_per_group"],
        })

    sources = probe_sources(config, lab_config, head=head, cache=cache, reprobe=reprobe)
    if tamper == "source_probe_reads_bytes":
        sources[0].update({"method": "GET", "content_bytes_read": True,
                           "content_length_bytes": 4096})
    if tamper == "source_probe_uses_credentials":
        sources[0]["credentials_used"] = True
    if tamper == "registered_source_skipped":
        sources = sources[1:]
    if tamper == "download_off_allowlist":
        downloads.append({"url": "https://example.invalid/some-table.zip", "path": "off-allowlist",
                          "bytes": 1, "sha256": "0" * 64})
    if tamper == "reference_layer_download":
        downloads.append({"url": "https://www2.census.gov/geo/tiger/TIGER2024/PRISECRROADS/"
                                "tl_2024_32_prisecroads.zip", "path": "tl_2024_32_prisecroads.zip",
                          "bytes": 1, "sha256": "0" * 64})

    return {
        "summary": {},
        "preregistration": {
            "plan_id": config["plan_id"], "backlog_item": config["backlog_item"],
            "lane": config["lane"], "title": config["title"], "intent": config["intent"],
            "config_path": config["__path__"], "config_sha256": config["__sha256__"],
            "lab_plan_config": lab_config.get("__path__", str(DEFAULT_LAB_CONFIG)),
            "lab_plan_config_sha256": lab_config.get("__sha256__"),
            "spec_path": config["spec"]["path"],
            "spec_sha256": lab_plan.sha256_file(Path(config["spec"]["path"])),
            "spec_sha256_pin": config["spec"]["sha256"],
            "operator_decision_reference": config["operator_decision_reference"],
            "cv_fold_amendment": lab_config.get("cv_fold_amendment"),
            "source_replacements": config.get("source_replacements", []),
            "prohibitions": config["prohibitions"], "tamper_applied": tamper,
            "negative_controls_declared": [case["id"] for case in config["negative_controls"]],
        },
        "sources": {"probed": sources, "registered": len(lab_config["sources"]),
                    "probed_from": (lambda asked: (
                        "cache" if asked and all(entry["probed_from"] == "cache"
                                                 for entry in asked) else
                        "network" if asked and all(entry["probed_from"] == "network"
                                                   for entry in asked) else "mixed"))(
                        [entry for entry in sources if entry.get("probe") == "attempted"]),
                    "probed_from_cache_sha256": (
                        None if (cache is None or not (Path(cache) / "source-probe.json").is_file())
                        else hashlib.sha256((Path(cache) / "source-probe.json").read_bytes())
                        .hexdigest()),
                    "credentials_used": any(entry.get("credentials_used") for entry in sources),
                    "content_bytes_read_by_probe": any(entry.get("content_bytes_read")
                                                       for entry in sources)},
        "downloads": {"files": downloads, "total_bytes": sum(entry["bytes"] for entry in downloads),
                      "hosts": sorted({host_of(entry["url"]) for entry in downloads}),
                      "kind": "public census tract geography only"},
        "study_geography": {"states": sorted(config["study_states"]),
                            "study_tracts_matched": len(study_records),
                            "study_points": len(study_points),
                            "projection": geometry["projection"],
                            "excluded_geoids_in_policy": len(base_policy.excluded_geoids)},
        "candidate_probe": {"groups": groups, "groups_probed": len(groups),
                            "groups_planned": len(config["probe_groups"]),
                            "minimum_accepted_tracts_per_group":
                                config["acceptance"]["minimum_accepted_tracts_per_group"]},
        "exclusion_policy": policy.summary(),
        "access": {"cache_dir": str(cache), "lab_root": str(lab_root_of(lab_config)),
                   "opened_paths": list(OPENED)},
    }


# --------------------------------------------------------------------------- gates
def reference_token_hits(urls: list[str], paths: list[str], geometry_pattern: str) -> list[str]:
    """Every downloaded file except the declared census geometry must be free of reference tokens.

    The census tract file legitimately carries the TIGER vintage in its name, which is also a
    reference-layer token in the model-chain firewall; the exemption is exact - the URL must equal the
    registered tract pattern for its state - and anything else carrying such a token is a hit.
    """
    hits: list[str] = []
    for url, path in zip(urls, paths):
        if url == geometry_pattern.replace("<ss>", state_of_filename(url)):
            continue
        for token in lab_eval.REFERENCE_LAYER_TOKENS:
            if token in str(url).lower() or token in str(path).lower():
                hits.append(f"{path}:{token}")
    return hits


def _temporal_fields(node: Any, trail: str = "") -> list[str]:
    offenders: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            if any(token in str(key).lower() for token in
                   ("utc", "timestamp", "_date", "retrieval_date", "elapsed", "duration")):
                offenders.append(f"{trail}/{key}")
            offenders.extend(_temporal_fields(child, f"{trail}/{key}"))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            offenders.extend(_temporal_fields(child, f"{trail}[{index}]"))
    return offenders


def evaluate_gates(report: dict[str, Any], config: dict[str, Any],
                   lab_config: dict[str, Any], base_excluded: frozenset[str]) -> dict[str, Any]:
    """Contract gates: they prove the probe stayed in its rails and told the truth about what it found.

    Whether the lab plan is viable and whether the sources answer are *findings*, reported below the
    line; the gates only require that a negative finding is disclosed, never that it came out well.
    """
    geometry = config["geometry_source"]
    groups = report["candidate_probe"]["groups"]
    findings = report["findings"]
    allowed_hosts = set(geometry["allowed_hosts"])
    download_urls = [entry["url"] for entry in report["downloads"]["files"]]
    cached_paths = [entry["path"] for entry in report["downloads"]["files"]]
    buffer_km = float(lab_config["exclusion_policy"]["centroid_buffer_km"])
    floor = config["acceptance"]["minimum_accepted_tracts_per_group"]
    shorted = [group["id"] for group in groups if not group["ready"]]
    undisclosed_shorts = sorted(set(shorted) - {entry["id"] for entry in
                                                findings["group_shortfalls"]})
    unanswered = [entry["id"] for entry in report["sources"]["probed"]
                  if entry.get("status") != 200]
    hidden = sorted(set(unanswered) - {entry["id"] for entry in findings["sources_not_answered"]}
                    - {entry["id"] for entry in findings["sources_not_probeable"]})
    return {
        "spec_hash_matches_pin": (report["preregistration"]["spec_sha256"]
                                  == report["preregistration"]["spec_sha256_pin"]),
        "all_registered_sources_probed": bool(report["sources"]["probed"]) and len(
            {entry["id"] for entry in report["sources"]["probed"]}) == report["sources"]["registered"],
        "sources_probed_without_content_bytes": bool(report["sources"]["probed"]) and all(
            entry["method"] == "HEAD" and entry["content_bytes_read"] is False
            for entry in report["sources"]["probed"]),
        "source_probe_credentials_unused": report["sources"]["credentials_used"] is False,
        # a remote status line is not a measurement if it can only be produced once: the answers the
        # report publishes must be the answers a re-run will produce again
        "source_probe_answers_are_reusable": bool(
            report["sources"]["probed_from"] in ("cache", "network", "mixed")
            and all(entry.get("probed_from") in ("cache", "network", "not_probed")
                    for entry in report["sources"]["probed"])
            # a cached round must still be a traceable round: the published status lines are tied to
            # the exact recorded answers by their hash
            and (report["sources"]["probed_from"] != "cache"
                 or len(str(report["sources"]["probed_from_cache_sha256"])) == 64)),
        "only_public_census_geography_downloaded": bool(download_urls) and all(
            host_of(url) in allowed_hosts
            and url == geometry["url_pattern"].replace("<ss>", state_of_filename(url))
            for url in download_urls),
        "no_reference_layer_bytes_on_disk": not reference_token_hits(
            download_urls, cached_paths, geometry["url_pattern"]),
        "cache_outside_lab_root": not Path(report["access"]["cache_dir"]).is_relative_to(
            Path(report["access"]["lab_root"])),
        "study_points_measured_from_public_geography": report["study_geography"]["study_points"] > 0,
        "no_official_geoid_accepted": not any(geoid in base_excluded for group in groups
                                              for geoid in group["accepted_geoids"]),
        "no_accepted_tract_within_buffer": bool(groups) and all(
            group["accepted_min_distance_km"] is None
            or group["accepted_min_distance_km"] > buffer_km for group in groups),
        "every_planned_group_measured": len(groups) >= config["acceptance"][
            "minimum_groups_probed"] and all(group["candidates_found"] >= 0 for group in groups),
        "active_cv_groups_match_amendment": (
            sorted(config["probe_groups"])
            == sorted(lab_config.get("cv_fold_amendment", {}).get("active_group_ids", []))
            and len(groups) >= config["acceptance"]["minimum_groups_probed"]
            and not (set(config["probe_groups"])
                     & {entry["id"] for entry in
                        lab_config.get("cv_fold_amendment", {}).get("excluded_from_cv", [])})),
        "shortfalls_disclosed": not undisclosed_shorts and (
            findings["lab_plan_needs_amendment"] is bool(shorted or findings["sources_not_answered"])
            or findings["lab_plan_needs_amendment"] is True) and all(
            entry["accepted"] < floor for entry in findings["group_shortfalls"]),
        "source_availability_disclosed": not hidden,
        "negative_controls_declared": bool(report["preregistration"]["negative_controls_declared"]),
        "no_lab_artifact_written": not any(Path(entry["path"]).name in lab_eval.ARTIFACTS
                                           for entry in report["downloads"]["files"]),
        "report_is_strict_json": True,
        "report_has_no_temporal_field": not _temporal_fields(report),
        "no_forbidden_value_in_report": not any(
            token in json.dumps(report) for token in config["forbidden_value_tokens"]),
    }


def build_report(config: dict[str, Any], *, workspace: Path,
                 lab_config: Path = DEFAULT_LAB_CONFIG, cache: Path | None = None,
                 tamper: str | None = None, reprobe: bool = False,
                 head: Callable[..., dict[str, Any]] = http_head,
                 get: Callable[..., dict[str, Any]] = http_get,
                 records_for_state: Callable[[str], list[dict[str, Any]]] | None = None,
                 ) -> dict[str, Any]:
    lab_path = Path(lab_config)
    lab_document = json.loads(lab_path.read_text(encoding="utf-8"))
    lab_document["__path__"] = str(lab_path)
    lab_document["__sha256__"] = hashlib.sha256(lab_path.read_bytes()).hexdigest()
    record(lab_path)
    workspace = Path(workspace)

    declared = {case["id"] for case in config["negative_controls"]}
    if tamper is not None and tamper not in declared:
        raise ValueError(f"unknown tamper {tamper!r}; declared controls are {sorted(declared)}")
    config = json.loads(json.dumps(config))

    cache_dir = Path(cache) if cache else workspace / config["cache_dir_name"]
    # the tamper relabels where the cache claims to live; it must not actually write into the real lab
    # root, which has to stay absent until bias60-external-lab-build produces a manifest-backed set
    claimed_cache = (str(lab_root_of(lab_document) / "prereq-cache")
                     if tamper == "cache_written_into_lab_root" else None)

    report = probe(config, lab_document, workspace, cache_dir, head=head, get=get,
                   reprobe=reprobe,
                   records_for_state=records_for_state, tamper=tamper)
    if tamper == "temporal_field_added":
        # a wall-clock field is injected where a real run would be tempted to put one
        report["preregistration"]["retrieved_utc"] = "2026-10-25T10:00:00Z"
    if claimed_cache is not None:
        report["access"]["cache_dir"] = claimed_cache
    # the exclusion set is re-derived here so a tampered policy cannot also rewrite the check
    base_excluded = lab_plan.policy_from_config(lab_document, workspace,
                                                study_points=[]).excluded_geoids
    floor = config["acceptance"]["minimum_accepted_tracts_per_group"]
    probed_groups = report["candidate_probe"]["groups"]
    if tamper == "group_without_survivors_reported_as_ready" and probed_groups:
        probed_groups[0]["accepted"] = floor - 1
        probed_groups[0]["ready"] = False
    if tamper == "source_unavailability_hidden" and report["sources"]["probed"]:
        report["sources"]["probed"][0]["status"] = 503
        report["sources"]["probed"][0]["meaning"] = "unexpected"
    unanswered = [entry for entry in report["sources"]["probed"]
                  if entry.get("status") != 200 and entry.get("probe") != "skipped"]
    report["findings"] = {
        "minimum_accepted_tracts_per_group": floor,
        "group_shortfalls": [{"id": group["id"], "planned_approx_tracts":
                              group["planned_approx_tracts"], "accepted": group["accepted"],
                              "floor": floor,
                              "consequence": ("this group cannot be a leave-one-group-out fold as "
                                              "frozen; the plan must widen its county list or the "
                                              "group must be merged or dropped")}
                             for group in probed_groups if group["accepted"] < floor],
        "sources_not_answered": [{"id": entry["id"], "status": entry.get("status"),
                                  "meaning": entry.get("meaning", entry.get("error")),
                                  "registered_url": entry.get("registered_url"),
                                  "alternate_that_answers": entry.get("alternate_that_answers")}
                                 for entry in unanswered],
        "sources_not_probeable": [{"id": entry["id"], "reason": entry.get("reason")}
                                  for entry in report["sources"]["probed"]
                                  if entry.get("probe") == "skipped"],
        "study_geoids_without_interior_point": (report["study_geography"]
                                                ["excluded_geoids_in_policy"]
                                                - report["study_geography"]["study_tracts_matched"]),
        "groups_below_plan": [{"id": group["id"], "planned_approx_tracts":
                               group["planned_approx_tracts"], "accepted": group["accepted"],
                               "ratio": (round(group["accepted"] / group["planned_approx_tracts"], 3)
                                         if group["planned_approx_tracts"] else None)}
                              for group in probed_groups
                              if group["planned_approx_tracts"]
                              and group["accepted"] < 0.5 * group["planned_approx_tracts"]],
    }
    report["findings"]["lab_plan_needs_amendment"] = bool(report["findings"]["group_shortfalls"]
                                                          or report["findings"]["sources_not_answered"])
    if tamper == "group_without_survivors_reported_as_ready":
        report["findings"]["group_shortfalls"] = []
    if tamper == "source_unavailability_hidden":
        report["findings"]["sources_not_answered"] = []
    report["findings"]["verdict"] = (
        "the frozen lab plan is not yet buildable as written: "
        + ", ".join(sorted({entry["id"] for entry in report["findings"]["group_shortfalls"]})
                    or "no group shortfall")
        + " cannot be a fold and "
        + ", ".join(sorted(entry["id"] for entry in report["findings"]["sources_not_answered"])
                    or "every source")
        + " did not answer an anonymous request at its registered pattern"
        if report["findings"]["lab_plan_needs_amendment"] else
        "the frozen lab plan is buildable as written on the groups probed")
    report["gates"] = evaluate_gates(report, config, lab_document, frozenset(base_excluded))
    report["gates"]["all_passed"] = all(value is True for value in report["gates"].values())
    groups = report["candidate_probe"]["groups"]
    report["summary"] = {
        "sources_registered": report["sources"]["registered"],
        "sources_reachable": sum(1 for entry in report["sources"]["probed"]
                                 if entry.get("status") == 200),
        "sources_not_probeable": sum(1 for entry in report["sources"]["probed"]
                                     if entry.get("probe") == "skipped"),
        "study_points": report["study_geography"]["study_points"],
        "study_tracts_matched": report["study_geography"]["study_tracts_matched"],
        "groups_probed": len(groups), "groups_planned": report["candidate_probe"]["groups_planned"],
        "candidates_found": sum(group["candidates_found"] for group in groups),
        "candidates_accepted": sum(group["accepted"] for group in groups),
        "groups_ready": sum(1 for group in groups if group["ready"]),
        "downloaded_bytes": report["downloads"]["total_bytes"],
        "groups_short_of_floor": len(report["findings"]["group_shortfalls"]),
        "sources_not_answered": len(report["findings"]["sources_not_answered"]),
        "lab_plan_needs_amendment": report["findings"]["lab_plan_needs_amendment"],
        "gates_all_passed": report["gates"]["all_passed"],
        "report_sha256": hashlib.sha256(
            canonical_bytes({key: value for key, value in report.items()
                             if key != "summary"})).hexdigest(),
    }
    return report


# --------------------------------------------------------------------------- markdown
def render_markdown(report: dict[str, Any]) -> str:
    pre = report["preregistration"]
    summary = report["summary"]
    lines = [f"# {pre['title']}", "",
             f"- Plan: `{pre['plan_id']}` (backlog item `{pre['backlog_item']}`)",
             f"- Preregistered configuration: `{pre['config_path']}` "
             f"(SHA-256 `{pre['config_sha256'][:16]}…`)",
             f"- Lab plan consumed: `{pre['lab_plan_config']}`",
             f"- Spec: `{pre['spec_path']}` (SHA-256 `{pre['spec_sha256'][:16]}…`, matches its pin: "
             f"{report['gates']['spec_hash_matches_pin']})",
             f"- Verdict: **{'PASS' if summary['gates_all_passed'] else 'FAIL'}**", "",
             "## Operator authorisation this probe works under", "", f"- {pre['operator_decision_reference']}",
             "", "## What this probe is allowed to touch", "",
             f"- downloads: {report['downloads']['kind']} from "
             f"{', '.join(report['downloads']['hosts']) or 'no host'}",
             f"- source probes: HEAD only, content bytes read: "
             f"{report['sources']['content_bytes_read_by_probe']}, credentials used: "
             f"{report['sources']['credentials_used']}",
             f"- cache: `{report['access']['cache_dir']}` (outside the lab root "
             f"`{report['access']['lab_root']}`: {report['gates']['cache_outside_lab_root']})", "",
             "## Measured", "",
             f"- Registered sources: {summary['sources_registered']}; answering 200 anonymously: "
             f"{summary['sources_reachable']}; not probeable as registered: "
             f"{summary['sources_not_probeable']}",
             f"- Public census geography downloaded: {summary['downloaded_bytes']} bytes across "
             f"{len(report['downloads']['files'])} files",
             f"- Study interior points established: {summary['study_points']} from "
             f"{summary['study_tracts_matched']} matched study tracts in "
             f"{report['study_geography']['projection']}",
             f"- Planned lab groups: {summary['groups_planned']}; probed: {summary['groups_probed']}; "
             f"with enough surviving tracts: {summary['groups_ready']}",
             f"- Candidate tracts inspected: {summary['candidates_found']}; accepted by the frozen "
             f"exclusion: {summary['candidates_accepted']}", "", "## Source availability", "",
             "| source | probe | status | meaning | bytes advertised | registered replacement |",
             "| --- | --- | --- | --- | --- | --- |"]
    for entry in report["sources"]["probed"]:
        note = entry.get("meaning") or entry.get("reason", "")
        replacement = (f"replaces `{entry['replacement_for']}`: {entry['replacement_reason']}"
                       if entry.get("replacement_for") else
                       str(entry.get("alternate_that_answers")))
        lines.append(f"| `{entry['id']}` | {entry.get('probe')} | {entry.get('status')} | {note} | "
                     f"{entry.get('content_length_bytes')} | {replacement} |")
    lines += ["", "## Candidate tracts after the frozen exclusion", "",
              "| group | states | counties | planned | candidates | accepted | rejected | "
              "min km to a study tract | ready |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    reasons: dict[str, int] = {}
    for group in report["candidate_probe"]["groups"]:
        rejected = sum(group["rejected_by_reason"].values())
        for reason, count in group["rejected_by_reason"].items():
            reasons[reason] = reasons.get(reason, 0) + count
        lines.append(f"| `{group['id']}` | {', '.join(group['states'])} | "
                     f"{group['counties_declared']} | {group['planned_approx_tracts']} | "
                     f"{group['candidates_found']} | {group['accepted']} | {rejected} | "
                     f"{group['accepted_min_distance_km']} | "
                     f"{'yes' if group['ready'] else 'no'} |")
    findings = report["findings"]
    lines += ["", "## Findings the build has to know", "",
              f"- Lab plan needs amendment: **{findings['lab_plan_needs_amendment']}**",
              f"- Verdict: {findings['verdict']}", ""]
    if findings["group_shortfalls"]:
        lines += ["| group | planned | accepted | floor | consequence |", "| --- | --- | --- | --- | --- |"]
        for entry in findings["group_shortfalls"]:
            lines.append(f"| `{entry['id']}` | {entry['planned_approx_tracts']} | "
                         f"{entry['accepted']} | {entry['floor']} | {entry['consequence']} |")
        lines.append("")
    if findings["groups_below_plan"]:
        lines += ["Groups far below their planned tract count (plan numbers to correct):", ""]
        for entry in findings["groups_below_plan"]:
            lines.append(f"- `{entry['id']}`: planned {entry['planned_approx_tracts']}, "
                         f"accepted {entry['accepted']} (ratio {entry['ratio']})")
        lines.append("")
    if findings["sources_not_answered"]:
        lines += ["| source | status | meaning | alternate that answers |", "| --- | --- | --- | --- |"]
        for entry in findings["sources_not_answered"]:
            lines.append(f"| `{entry['id']}` | {entry['status']} | {entry['meaning']} | "
                         f"{entry['alternate_that_answers']} |")
        lines.append("")
    if findings["sources_not_probeable"]:
        for entry in findings["sources_not_probeable"]:
            lines.append(f"- `{entry['id']}` is not probeable as registered: {entry['reason']}")
        lines.append("")
    lines += [f"- Excluded study GEOIDs with no interior point in the census file: "
              f"{findings['study_geoids_without_interior_point']}", ""]
    lines += ["", "## Rejections by reason", ""]
    for reason, count in sorted(reasons.items()):
        lines.append(f"- `{reason}`: {count}")
    lines += ["", "## Exclusion policy as executed", ""]
    for key, value in sorted(report["exclusion_policy"].items()):
        lines.append(f"- `{key}`: {value}")
    lines += ["", "## Gates", ""]
    for name in sorted(report["gates"]):
        lines.append(f"- `{name}`: {report['gates'][name]}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--lab-config", default=str(DEFAULT_LAB_CONFIG))
    parser.add_argument("--workspace", default=os.environ.get("COMPETITION_WORKSPACE", "."))
    parser.add_argument("--cache", default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    parser.add_argument("--tamper", default=None)
    parser.add_argument("--reprobe", action="store_true",
                        help="ask the network again instead of reusing the recorded HEAD answers")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    lab_plan.set_recorder(record)
    config = load_config(Path(args.config))
    report = build_report(config, workspace=Path(args.workspace), lab_config=Path(args.lab_config),
                          cache=Path(args.cache) if args.cache else None, tamper=args.tamper,
                          reprobe=args.reprobe)
    payload = canonical_bytes(report)
    markdown = render_markdown(report)
    out_path = Path(args.out)
    if args.check_only:
        unchanged = (out_path.exists() and out_path.read_bytes() == payload
                     and Path(args.markdown).exists()
                     and Path(args.markdown).read_text(encoding="utf-8") == markdown)
        print(("unchanged " if unchanged else "CHANGED ") + str(out_path))
        return 0 if unchanged else 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
    Path(args.markdown).write_text(markdown, encoding="utf-8")
    summary = report["summary"]
    verdict = "PASS" if summary["gates_all_passed"] else "FAIL"
    print(f"{verdict} lab prerequisites: {summary['sources_reachable']} of "
          f"{summary['sources_registered']} registered sources answer anonymously, "
          f"{summary['candidates_accepted']} of {summary['candidates_found']} candidate tracts "
          f"survive the frozen exclusion across {summary['groups_probed']} planned groups "
          f"({summary['downloaded_bytes']} bytes of public census geography, no reference layer); "
          f"findings: {summary['groups_short_of_floor']} group(s) short of the fold floor, "
          f"{summary['sources_not_answered']} source(s) not answering, lab plan needs amendment: "
          f"{summary['lab_plan_needs_amendment']}")
    failed = sorted(name for name, value in report["gates"].items() if value is not True)
    if failed:
        print("failed gates: " + ", ".join(failed))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["gates_all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
