#!/usr/bin/env python3
"""bias60-external-lab-build: staged retrieval and reference-derived lab labels.

Spec section 4, executed. The lab is the only place in this lane where a coverage-gap *target* can be
computed, because it is computed from reference layers in regions that are geographically separate from
the four study regions and whose tracts are neither study tracts nor neighbours of one.

Two rules are structural here rather than aspirational:

* every retrieved layer is partitioned by the frozen ``lab_plan.ExclusionPolicy`` before a byte of it
  is kept, so a study GEOID cannot enter a lab table and no reference layer is measured on a study tract;
* nothing under the lab root is read by a model or submission path, and the five evaluator artifacts are
  written only by the promote step, so a staged build cannot be mistaken for a scoreable lab.

Every retrieved file records URL, version, retrieval date, size and SHA-256 in the workspace manifest.
The committed repo report carries hashes and counts, never a generated timestamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import probe_lab_prerequisites as probe  # noqa: E402  (reused readers and geometry helpers)
from bias60 import lab_eval, lab_plan  # noqa: E402

CONFIG_PATH = ROOT / "config" / "lab-build-preregistered.json"
PREREQUISITE_CONFIG = ROOT / "config" / "lab-prerequisites-preregistered.json"
IDEA_REGISTRY = ROOT / "config" / "ideas.json"
USER_AGENT = "zindi-bias-bounty-lane/1.0 (external lab build; public data only)"
DATA_SUFFIXES = (".zip", ".parquet", ".json", ".csv")

OPENED: list[str] = []


def record(path) -> None:
    """Log every path this build opens, in the shape the other lab artifacts use."""
    resolved = Path(str(path)).resolve()
    bases = (("REPO", ROOT), ("WORKSPACE", workspace_root()))
    orchestrator = os.environ.get("ORCHESTRATOR_ROOT")
    if orchestrator:
        bases = bases + (("ORCHESTRATOR", Path(orchestrator)),)
    for label, base in bases:
        try:
            OPENED.append(f"{label}:{resolved.relative_to(base)}")
            return
        except ValueError:
            continue
    OPENED.append(str(resolved))


probe.record = record  # reused readers must log into this build's ledger, not the probe's


def canonical_bytes(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    document["__sha256__"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return document


def workspace_root() -> Path:
    return Path(os.environ["COMPETITION_WORKSPACE"])


def lab_config_document() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "bias60-lab-preregistered.json").read_text(encoding="utf-8"))


def load_analogue_config(config: dict[str, Any]) -> dict[str, Any]:
    path=ROOT / config["estimators"]["analogue_config_path"]
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=config["estimators"]["analogue_config_sha256"]:
        raise ValueError("lab estimator analogue preregistration hash mismatch")
    document=json.loads(path.read_text(encoding="utf-8"))
    registry=json.loads(IDEA_REGISTRY.read_text(encoding="utf-8"))
    implemented={idea["id"] for idea in registry["ideas"] if idea["status"]=="implemented"}
    if set(document["ideas"])!=implemented:
        raise ValueError("analogue config must cover exactly the implemented idea registry")
    evaluable=sorted(idea for idea,row in document["ideas"].items()
                     if row["status"]=="evaluable_in_lab")
    not_evaluable=sorted(idea for idea,row in document["ideas"].items()
                         if row["status"]=="not_evaluable_in_lab" and row.get("reason"))
    if evaluable!=sorted(document["evaluated_ideas"]) or not_evaluable!=sorted(document["not_evaluable_ideas"]):
        raise ValueError("analogue config status lists do not match its idea entries")
    if set(evaluable)&set(not_evaluable) or len(evaluable)+len(not_evaluable)!=len(implemented):
        raise ValueError("every implemented idea must be either evaluable or explicitly not evaluable")
    for idea in evaluable:
        row=document["ideas"][idea]
        if not row.get("input_mappings") or not row.get("channel"):
            raise ValueError(f"evaluable idea {idea} lacks a source mapping or target channel")
    document["__sha256__"]=digest
    return document


def lab_root_of(config: dict[str, Any]) -> Path:
    return Path(config["lab_root"].replace("$COMPETITION_WORKSPACE", str(workspace_root())))


def staging_dir(config: dict[str, Any], group_id: str) -> Path:
    return lab_root_of(config) / "staging" / group_id


# --------------------------------------------------------------------------- retrieval
class Budget:
    """A hard cap on what one build may pull, checked before the bytes arrive."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.max_bytes = int(config["retrieval"]["max_total_bytes"])
        self.max_files = int(config["retrieval"]["max_files"])
        self.bytes = 0
        self.files = 0

    def add(self, size: int) -> None:
        if self.files + 1 > self.max_files:
            raise ValueError(f"retrieval would exceed the frozen file budget of {self.max_files}")
        if self.bytes + size > self.max_bytes:
            raise ValueError(f"retrieval would exceed the frozen byte budget of {self.max_bytes:,}")
        self.files += 1
        self.bytes += size


def study_value_in_url(url: str, study_geoids: frozenset[str],
                       study_counties: frozenset[str]) -> str | None:
    """The absolute prohibition, as a predicate: no study GEOID and no study county in a URL."""
    for geoid in study_geoids:
        if geoid in url:
            return geoid
    for county in study_counties:
        if re.search(rf"(^|[^0-9]){county}([^0-9]|$)", url):
            return county
    return None


def assert_url_is_authorized(url: str, config: dict[str, Any], *, study_geoids: frozenset[str],
                            study_counties: frozenset[str]) -> None:
    """Four hard stops before a byte is asked for: host, pattern, data-not-code, nothing study-side."""
    retrieval = config["retrieval"]
    host = urllib.parse.urlsplit(url).netloc
    if host not in retrieval["allowed_hosts"]:
        raise ValueError(f"refusing to retrieve from non-allowlisted host {host!r}")
    offending = study_value_in_url(url, study_geoids, study_counties)
    if offending is not None:
        raise ValueError(f"refusing to retrieve {url!r}: it names the study value {offending}")
    lowered = urllib.parse.urlsplit(url).path.lower()
    for suffix in retrieval["denied_url_suffixes"]:
        if lowered.endswith(suffix):
            raise ValueError(f"refusing to retrieve {url!r}: {suffix} would be downloaded code")
    if not lowered.endswith(DATA_SUFFIXES):
        raise ValueError(f"refusing to retrieve {url!r}: only {DATA_SUFFIXES} count as data")
    if not any(re.match(pattern, url) for pattern in retrieval["allowed_url_patterns"]):
        raise ValueError(f"refusing to retrieve {url!r}: it matches no preregistered URL pattern")


def _has_name(value: Any) -> bool:
    """True when an Overture ``names`` value carries any non-empty string, in any of its shapes."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_has_name(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_name(item) for item in value)
    return False


def _ipc_sha256(table) -> str:
    """Hash the exact bytes this build used, not the whole remote object."""
    import io

    import pyarrow as pa

    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return hashlib.sha256(sink.getvalue()).hexdigest()


def _now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_download(url: str, dest: Path, *, config: dict[str, Any], version: str, budget: Budget,
                  fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """Fetch one public file, hashing the bytes, and return its provenance record.

    The retrieval date lives in a sidecar next to the file so a re-run reports the date the bytes
    actually arrived rather than the date the report was regenerated.
    """
    if fetch is None:
        def fetch(target: str) -> bytes:  # pragma: no cover - exercised by the real run
            request = urllib.request.Request(target, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=900) as response:
                return response.read()
    sidecar = dest.with_suffix(dest.suffix + ".provenance.json")
    if dest.exists() and sidecar.exists():
        payload = dest.read_bytes()
        provenance = json.loads(sidecar.read_text(encoding="utf-8"))
        provenance["retrieval"] = "cache"
    else:
        payload = fetch(url)
        provenance = {"url": url, "name": dest.name, "version": version,
                      "retrieval": "network", "retrieval_date_utc": _now_utc()}
    digest = hashlib.sha256(payload).hexdigest()
    if provenance.get("sha256") not in (None, digest) and provenance["retrieval"] == "cache":
        raise ValueError(f"cached {dest.name} hash {digest[:12]} does not match the recorded "
                         f"{str(provenance['sha256'])[:12]}; the cache is corrupt")
    provenance.update({"path": str(dest), "bytes": len(payload), "sha256": digest,
                       "credentials_used": False})
    if provenance["retrieval"] == "network":
        budget.add(len(payload))
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix(dest.suffix + ".part")
        temporary.write_bytes(payload)
        temporary.replace(dest)
        sidecar.write_bytes(canonical_bytes(provenance))
    record(dest)
    return dict(provenance)


def s3_list(endpoint: str, prefix: str, *, lister: Callable[[str, str], list[dict[str, Any]]]
            | None = None) -> list[dict[str, Any]]:
    """List one S3 prefix anonymously: keys, sizes and ETags, never content."""
    if lister is not None:
        return lister(endpoint, prefix)
    import xml.etree.ElementTree as ET

    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    objects: list[dict[str, Any]] = []
    token = None
    while True:
        url = f"{endpoint}?list-type=2&max-keys=1000&prefix=" + urllib.parse.quote(prefix, safe="")
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token, safe="")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            root = ET.fromstring(response.read())
        for contents in root.iter(ns + "Contents"):
            etag = contents.find(ns + "ETag")
            objects.append({"key": contents.find(ns + "Key").text,
                            "size": int(contents.find(ns + "Size").text),
                            "etag": (etag.text or "").strip('"') if etag is not None else None})
        if root.find(ns + "IsTruncated").text != "true":
            return objects
        token = root.find(ns + "NextContinuationToken").text


# --------------------------------------------------------------------------- study side, read only to exclude
def study_points(config: dict[str, Any], *, budget: Budget,
                 fetch: Callable[[str], bytes] | None = None,
                 records: list[dict[str, Any]] | None = None
                 ) -> tuple[list[tuple[float, float]], dict[str, Any]]:
    """Interior points of every official and template tract, used only to say what is forbidden.

    No reference layer is ever measured at one of these points; they exist so the neighbour buffer in
    the frozen exclusion policy has something to bite on.
    """
    document = lab_config_document()
    cache = workspace_root() / "lab-prerequisites"
    official = set(lab_plan.official_scored_ids(workspace_root()))
    template = set(lab_plan.template_geoids(workspace_root()))
    states = sorted({geoid[:2] for geoid in official | template})
    prereq_config = probe.load_config(PREREQUISITE_CONFIG)
    patterns = config["retrieval"]["allowed_url_patterns"]

    sources: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    for state in states:
        url = f"https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_{state}_tract.zip"
        if not any(re.match(pattern, url) for pattern in patterns):
            raise ValueError(f"study geography URL {url!r} is not preregistered")
        provenance = http_download(url, cache / Path(url).name, config=config, version="TIGER2024",
                                   budget=budget, fetch=fetch)
        provenance["role"] = "study_geography_for_exclusion_only"
        sources.append(provenance)
        for entry in probe.read_tract_records(Path(provenance["path"]), prereq_config):
            if entry["geoid"] in official or entry["geoid"] in template:
                matched.append(entry)
    if records is not None:
        matched = records  # a test supplies the study geography; the guard below still applies
    points = probe.project_points(matched, config["exclusion"]["crs"])
    if len(points) < 9000:
        raise ValueError(f"only {len(points)} study interior points established; the exclusion buffer "
                         f"would be silently weaker than frozen")
    return points, {"study_states": states, "study_points": len(points),
                    "excluded_geoids": len(official | template), "sources": sources}


# --------------------------------------------------------------------------- tracts
def tract_polygons(zip_path: Path) -> list[dict[str, Any]]:
    """Read GEOID, county FIPS and polygon out of a Census tract shapefile zip."""
    import shapefile
    from shapely.geometry import shape

    record(zip_path)
    reader = shapefile.Reader(str(zip_path))
    names = [str(entry[0]).upper() for entry in reader.fields[1:]]
    fields = {name: index for index, name in enumerate(names)}
    geoid_name = next((name for name in ("GEOID", "GEOID20", "TRACTA_GEO") if name in fields), None)
    if geoid_name is None:
        raise ValueError(f"{zip_path.name} has no GEOID field; has {sorted(fields)}")
    county_name = next((name for name in ("COUNTYFP", "COUNTYFP20") if name in fields), None)
    state_name = next((name for name in ("STATEFP", "STATEFP20") if name in fields), None)
    out: list[dict[str, Any]] = []
    for shape_record in reader.iterShapeRecords():
        record_values = shape_record.record
        geoid = str(record_values[fields[geoid_name]] or "").strip()
        if len(geoid) not in (11, 13, 14) or not geoid.isdigit():
            continue
        county = (str(record_values[fields[county_name]] or "").strip().zfill(3) if county_name
                  else geoid[2:5])
        if state_name:
            county = str(record_values[fields[state_name]] or "").strip().zfill(2) + county
        out.append({"geoid": geoid, "county_fips": county,
                    "geometry": shape(shape_record.shape.__geo_interface__)})
    return out


def build_tracts(group: dict[str, Any], config: dict[str, Any], *, budget: Budget,
                 fetch: Callable[[str], bytes] | None = None,
                 study_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Apply the frozen spec 4.1 exclusion to a group's census tracts and write the accepted set."""
    import shapely as _shapely
    from pyproj import Transformer
    import pyarrow as pa
    import pyarrow.parquet as pq

    def shapely_transform(geometry, transformer):
        """Project every vertex of a geometry with one vectorised pyproj call."""
        import numpy as _numpy

        def _map(coordinates):
            x_values, y_values = transformer.transform(coordinates[:, 0], coordinates[:, 1])
            return _numpy.column_stack((x_values, y_values))
        return _shapely.transform(geometry, _map)

    crs = config["exclusion"]["crs"]
    staging = staging_dir(config, group["id"])
    sources = staging / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    document = lab_config_document()
    official = frozenset(lab_plan.official_scored_ids(workspace_root()))
    template = frozenset(lab_plan.template_geoids(workspace_root()))
    base_policy = lab_plan.policy_from_config(document, workspace_root(), study_points=[])
    points, study_meta = study_points(config, budget=budget, fetch=fetch, records=study_records)
    policy = lab_plan.ExclusionPolicy(official_geoids=official, template_geoids=template,
                                      study_county_fips=base_policy.study_county_fips,
                                      study_states=base_policy.study_states,
                                      buffer_km=config["exclusion"]["buffer_km"],
                                      study_points=points, crs=crs,
                                      county_exclusion_enabled=(
                                          lab_plan.BUILD_COUNTY_RULE_ENABLED_FOR_RETRIEVAL))
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    tracts: list[dict[str, Any]] = []
    sources_manifest: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    candidates = 0
    for state in group["states"]:
        fips = document["state_fips_by_postal"][state]
        url = config["sources"]["tiger_tracts_2020"]["url"].replace("<ss>", fips)
        assert_url_is_authorized(url, config, study_geoids=official,
                                 study_counties=base_policy.study_county_fips)
        provenance = http_download(url, sources / Path(url).name, config=config, version="TIGER2020",
                                   budget=budget, fetch=fetch)
        provenance["role"] = "lab_tract_polygons"
        sources_manifest.append(provenance)
        for entry in tract_polygons(Path(provenance["path"])):
            county = entry["county_fips"]
            if group["county_includes"] and county not in group["county_includes"]:
                continue
            candidates += 1
            polygon = shapely_transform(entry["geometry"], transformer)
            centroid = polygon.representative_point()
            reason = policy.reject_reason(entry["geoid"], county, (centroid.x, centroid.y))
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            bounds_4326 = entry["geometry"].bounds
            tracts.append({"lab_tract_id": entry["geoid"], "lab_group": group["id"],
                           "state_fips": county[:2], "county_fips": county,
                           "centroid_x": float(centroid.x), "centroid_y": float(centroid.y),
                           "bounds_4326": (float(bounds_4326[0]), float(bounds_4326[1]),
                                           float(bounds_4326[2]), float(bounds_4326[3])),
                           "polygon": polygon})
    if len(tracts) < config["minimum_accepted_tracts_per_group"]:
        raise ValueError(f"group {group['id']} kept {len(tracts)} tracts, below the frozen floor of "
                         f"{config['minimum_accepted_tracts_per_group']}: {json.dumps(rejected)}")
    distances = probe.nearest_km([(entry["centroid_x"], entry["centroid_y"]) for entry in tracts],
                                 points)
    for entry, distance in zip(tracts, distances):
        entry["nearest_study_tract_km"] = round(float(distance), 3)
        if distance <= config["exclusion"]["buffer_km"]:
            raise ValueError(f"accepted tract {entry['lab_tract_id']} sits {distance:.3f} km from a "
                             f"study tract, inside the {config['exclusion']['buffer_km']} km buffer")
    table = pa.table({column: [entry[column] for entry in tracts]
                      for column in lab_eval.TRACT_COLUMNS})
    pq.write_table(table, staging / "tracts.parquet")
    record(staging / "tracts.parquet")
    return {"group": group["id"], "candidates": candidates, "accepted": len(tracts),
            "rejected_by_reason": rejected, "sources": sources_manifest, "study": study_meta,
            "tract_geoids": [entry["lab_tract_id"] for entry in tracts],
            "min_nearest_study_tract_km": round(min(distances), 3),
            "tracts": tracts}


# --------------------------------------------------------------------------- observed side, Overture
def observed_road_lengths(group: dict[str, Any], tracts: list[dict[str, Any]],
                          config: dict[str, Any], *,
                          lister: Callable[[str, str], list[dict[str, Any]]] | None = None,
                          reader: Callable[[str, list[int], list[str]], Any] | None = None) -> dict[str, Any]:
    """Clip Overture segments of the four spec classes to accepted lab tracts, in EPSG:5070.

    Row-group statistics on the ``bbox`` column decide what is read. A theme is never streamed whole:
    the transportation theme is 72 GB, and this reads the sliver that overlaps the group.
    """
    from pyproj import Transformer
    from shapely import STRtree, from_wkb, prepared
    import shapely as _shapely
    import pyarrow as pa

    def shapely_transform(geometry, transformer):
        """Project every vertex of a geometry with one vectorised pyproj call."""
        import numpy as _numpy

        def _map(coordinates):
            x_values, y_values = transformer.transform(coordinates[:, 0], coordinates[:, 1])
            return _numpy.column_stack((x_values, y_values))
        return _shapely.transform(geometry, _map)

    overture = config["sources"]["overture"]
    crs = config["exclusion"]["crs"]
    staging = staging_dir(config, group["id"])
    staging.mkdir(parents=True, exist_ok=True)
    classes = set(overture["road_classes"])
    subtypes = set(overture["road_subtypes"])
    columns = overture["road_columns"]

    polygons = [entry["polygon"] for entry in tracts]
    tree = STRtree(polygons)
    prepared_polygons = [prepared.prep(polygon) for polygon in polygons]
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    # the window comes from the accepted tracts' own 4326 bounds: deriving it from the corners of a
    # box in a conic projection silently loses the parts of the group that the rotation pushes out
    corners = [entry["bounds_4326"] for entry in tracts]
    margin = 0.05
    west = min(c[0] for c in corners) - margin
    south = min(c[1] for c in corners) - margin
    east = max(c[2] for c in corners) + margin
    north = max(c[3] for c in corners) + margin

    prefix = f"release/{overture['release']}/{overture['themes']['roads']}/"
    objects = s3_list(overture["endpoint"], prefix, lister=lister)
    if not objects:
        raise ValueError(f"no Overture objects under {prefix!r}")
    pattern = config["retrieval"]["allowed_url_patterns"]

    lengths = {entry["lab_tract_id"]: 0.0 for entry in tracts}
    named = {entry["lab_tract_id"]: 0.0 for entry in tracts}
    files: list[dict[str, Any]] = []
    segments_seen = 0
    intersections = 0
    if reader is None:  # pragma: no cover - the real retrieval path
        import pyarrow.fs as fa
        import pyarrow.parquet as pq

        s3 = fa.S3FileSystem(anonymous=True, region="us-west-2")

    for obj in objects:
        key = f"{overture['bucket']}/{obj['key']}"
        url = f"{overture['endpoint'].rstrip('/')}/{key}"
        if not any(re.match(entry, url) for entry in pattern):
            raise ValueError(f"Overture object {obj['key']!r} matches no preregistered pattern")
        cached = staging / "sources" / "overture" / Path(obj["key"]).name
        if reader is not None:
            table = reader(key, [], columns)
            compressed = 0
        elif cached.exists() and cached.with_suffix(cached.suffix + ".provenance.json").exists():
            provenance = json.loads(cached.with_suffix(cached.suffix
                                                       + ".provenance.json").read_text(encoding="utf-8"))
            if provenance.get("row_group_indices") is None:
                raise ValueError(f"{cached.name} lacks the row-group indices of its provenance")
            if sorted(provenance.get("columns", [])) != sorted(columns):
                raise ValueError(f"cached {cached.name} holds columns "
                                 f"{sorted(provenance.get('columns', []))}, not the preregistered "
                                 f"{sorted(columns)}; delete the cached shard and rebuild")
            import pyarrow.parquet as pq

            with cached.open("rb") as handle:
                table = pq.read_table(handle, columns=columns)
            compressed = int(provenance["compressed_bytes_read_estimate"])
        else:  # pragma: no cover - the real retrieval path
            with s3.open_input_file(key) as handle:
                parquet = pq.ParquetFile(handle)
                wanted = []
                for position in range(parquet.metadata.num_row_groups):
                    meta = parquet.metadata.row_group(position)
                    stats = {meta.column(i).path_in_schema: meta.column(i).statistics
                             for i in range(meta.num_columns)}
                    if any(stats[name] is None or stats[name].min is None for name in
                           ("bbox.xmin", "bbox.xmax", "bbox.ymin", "bbox.ymax")):
                        continue
                    # a row group spans [xmin.min, xmax.max]; using the inner extremes here would
                    # prune groups whose first row sits at one edge and whose last row is hundreds
                    # of kilometres away, which is how a lab silently loses a city
                    if (stats["bbox.xmax"].max < west or stats["bbox.xmin"].min > east
                            or stats["bbox.ymax"].max < south or stats["bbox.ymin"].min > north):
                        continue
                    wanted.append(position)
                if not wanted:
                    continue
                table = parquet.read_row_groups(wanted, columns=columns)
                compressed = sum(
                    parquet.metadata.row_group(position).column(i).total_compressed_size
                    for position in wanted
                    for i in range(parquet.metadata.row_group(position).num_columns))
        if table.num_rows == 0:
            continue
        if reader is None:  # pragma: no cover - keep the retrieved bytes as provenance
            import pyarrow.parquet as pq

            cached.parent.mkdir(parents=True, exist_ok=True)
            temporary = cached.with_suffix(cached.suffix + ".part")
            pq.write_table(table, temporary)
            temporary.replace(cached)
            cached.with_suffix(cached.suffix + ".provenance.json").write_bytes(canonical_bytes({
                "url": url, "row_group_indices": wanted, "columns": list(table.column_names),
                "compressed_bytes_read_estimate": int(compressed),
                "retrieval_date_utc": _now_utc(), "version": overture["release"]}))
        frame = table.to_pydict()
        geometries = frame["geometry"]
        class_column = frame["class"]
        subtype_column = frame["subtype"]
        names_column = frame.get("names")
        kept_here = 0
        for position in range(len(geometries)):
            if (class_column[position] or "") not in classes:
                continue
            if (subtype_column[position] or "") not in subtypes:
                continue
            segments_seen += 1
            segment = shapely_transform(from_wkb(geometries[position]), transformer)
            if segment.is_empty:
                continue
            for candidate in tree.query(segment):
                if not prepared_polygons[candidate].intersects(segment):
                    continue
                piece = (segment if prepared_polygons[candidate].contains(segment)
                         else segment.intersection(polygons[candidate]))
                if piece.is_empty:
                    continue
                length_km = float(piece.length) / 1000.0
                if length_km <= 0.0:
                    continue
                geoid = tracts[candidate]["lab_tract_id"]
                lengths[geoid] += length_km
                intersections += 1
                kept_here += 1
                if _has_name(names_column[position] if names_column else None):
                    named[geoid] += length_km
        del kept_here
        files.append({"url": url, "name": Path(obj["key"]).name, "version": overture["release"],
                      "bytes": obj["size"], "etag": obj.get("etag"),
                      "sha256": _ipc_sha256(table),
                      "sha256_scope": "row_groups_read_as_arrow_ipc",
                      "role": "observed_roads", "credentials_used": False,
                      "retrieval": "network", "rows_read": int(table.num_rows),
                      "compressed_bytes_read_estimate": int(compressed),
                      "retrieval_date_utc": _now_utc()})
    return {"group": group["id"], "theme_prefix": prefix, "objects_listed": len(objects),
            "files": files, "segments_seen": segments_seen,
            "segment_tract_intersections": intersections,
            "observed_km": lengths, "observed_named_km": named,
            "bbox_west_south_east_north": [round(west, 5), round(south, 5), round(east, 5),
                                           round(north, 5)]}


# --------------------------------------------------------------------------- reference side, TIGER roads
def _road_analogue_rows(tracts: list[dict[str, Any]], line_records: list[dict[str, Any]],
                        analogue: dict[str, Any], group_id: str) -> list[dict[str, Any]]:
    """Compute only the preregistered raw-road G1/Sc1 analogues; no reference labels are read."""
    import numpy as np
    import shapely
    from pyproj import Transformer
    from shapely import STRtree, prepared

    spec=analogue["ideas"]; road_ids=analogue["evaluated_ideas"]
    road_spec=next(mapping["filter"] for idea in road_ids
                   for mapping in spec[idea]["input_mappings"]
                   if mapping["raw_source"]=="overture_package_release")
    classes=set(road_spec["class_in"]); subtype=road_spec["subtype_equals"]
    polygons=[entry["polygon"] for entry in tracts]
    outer=[polygon.buffer(25.0) for polygon in polygons]
    boundary=[outside.difference(polygon.buffer(-25.0))
              for polygon,outside in zip(polygons,outer)]
    tree=STRtree(outer); prepared_outer=[prepared.prep(polygon) for polygon in outer]
    group_names=[group_id for _ in tracts]
    ids=[entry["lab_tract_id"] for entry in tracts]
    total_outer=np.zeros(len(tracts),dtype=np.float64)
    boundary_length=np.zeros(len(tracts),dtype=np.float64)
    total_inside=np.zeros(len(tracts),dtype=np.float64)
    unnamed_inside=np.zeros(len(tracts),dtype=np.float64)
    transformer=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True)
    for record in line_records:
        if str(record.get("class") or "").casefold() not in classes:
            continue
        if str(record.get("subtype") or "").casefold()!=subtype:
            continue
        wkb=record.get("geometry")
        if not wkb:continue
        line=shapely.from_wkb(wkb)
        line=shapely.transform(line,transformer.transform,interleaved=False)
        if line.is_empty:continue
        named=_has_name(record.get("names"))
        for position in tree.query(line).tolist():
            if not prepared_outer[position].intersects(line):continue
            in_outer=line if prepared_outer[position].contains(line) else line.intersection(outer[position])
            if in_outer.is_empty:continue
            outer_len=float(in_outer.length)
            if outer_len<=0:continue
            total_outer[position]+=outer_len
            band_piece=in_outer.intersection(boundary[position])
            boundary_length[position]+=float(band_piece.length)
            inside_piece=line if polygons[position].covers(line) else line.intersection(polygons[position])
            inside_len=float(inside_piece.length) if not inside_piece.is_empty else 0.0
            total_inside[position]+=inside_len
            if not named:unnamed_inside[position]+=inside_len
    rows=[]
    for idea in road_ids:
        item=spec[idea];channel=item["channel"]
        for i,geoid in enumerate(ids):
            denominator=total_outer[i] if idea=="G1" else total_inside[i]
            defined=bool(denominator>0.0)
            if idea=="G1":
                value=min(1.0,max(0.0,0.5*boundary_length[i]/denominator)) if defined else 0.0
            elif idea=="Sc1":
                value=min(1.0,max(0.0,unnamed_inside[i]/denominator)) if defined else 0.0
            else:raise ValueError(f"no raw-road implementation for {idea}")
            rows.append({"lab_tract_id":geoid,"lab_group":group_names[i],"idea":idea,
                         "family":item["family"],"component":item["component"],
                         "channel":channel,"value":float(value),"defined":defined})
    return rows


def write_estimators(group: dict[str, Any], tracts: list[dict[str, Any]], config: dict[str, Any],
                     analogue: dict[str, Any]) -> dict[str, Any]:
    """Write the exact lab_eval estimator schema from source-only, preregistered inputs."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    staging=staging_dir(config,group["id"]);source_dir=staging/"sources"/"overture"
    if not source_dir.is_dir():raise ValueError(f"missing raw Overture road cache for {group['id']}")
    sources=[];source_paths=[]
    required={"class","subtype","names","geometry"}
    for path in sorted(source_dir.glob("*.parquet")):
        sidecar=path.with_suffix(path.suffix+".provenance.json")
        if not sidecar.is_file():raise ValueError(f"raw source {path.name} has no provenance sidecar")
        provenance=json.loads(sidecar.read_text(encoding="utf-8"))
        missing=sorted(required-set(provenance.get("columns",[])))
        if missing:raise ValueError(f"raw source {path.name} lacks preregistered columns {missing}")
        source_paths.append(path)
        file_digest=lab_plan.sha256_file(path)
        sources.append({"path":path.name,"sha256":file_digest,"url":provenance.get("url"),
                        "version":provenance.get("version"),"retrieval_date_utc":provenance.get("retrieval_date_utc"),
                        "columns":sorted(required)})
    if not sources:raise ValueError(f"no raw Overture road partitions for {group['id']}")
    def line_records():
        for path in source_paths:
            parquet=pq.ParquetFile(path)
            for position in range(parquet.metadata.num_row_groups):
                yield from parquet.read_row_group(position,columns=sorted(required)).to_pylist()
    rows=_road_analogue_rows(tracts,line_records(),analogue,group["id"])
    table=pa.Table.from_pylist(rows).select(lab_eval.ESTIMATOR_COLUMNS)
    target=staging/"estimators.parquet";pq.write_table(table,target)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    result={"path":str(target),"sha256":digest,"rows":table.num_rows,
            "ideas":sorted({str(value) for value in table.column("idea").to_pylist()}),
            "not_evaluable_ideas":analogue["not_evaluable_ideas"],
            "columns":list(table.column_names),"analogue_config_sha256":analogue["__sha256__"],
            "source_partitions":sources,
            "note":"Only raw Overture transportation feature columns and external tract polygons were read; no labels or reference-road measurements were opened by this estimator writer."}
    (staging/"estimators.json").write_bytes(canonical_bytes(result));record(target);record(staging/"estimators.json")
    return result


def reference_road_lengths(group: dict[str, Any], tracts: list[dict[str, Any]],
                           config: dict[str, Any], *, budget: Budget,
                           fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """Clip TIGER All Roads, filtered to the frozen MTFCC set, to accepted lab tracts."""
    import shapefile
    from pyproj import Transformer
    import shapely as _shapely
    from shapely import STRtree, prepared
    from shapely.geometry import shape

    def shapely_transform(geometry, transformer):
        """Project every vertex of a geometry with one vectorised pyproj call."""
        import numpy as _numpy

        def _map(coordinates):
            x_values, y_values = transformer.transform(coordinates[:, 0], coordinates[:, 1])
            return _numpy.column_stack((x_values, y_values))
        return _shapely.transform(geometry, _map)

    crs = config["exclusion"]["crs"]
    staging = staging_dir(config, group["id"])
    sources = staging / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    source = config["sources"]["tiger_county_roads"]
    mtfcc = set(source["mtfcc"])
    official = frozenset(lab_plan.official_scored_ids(workspace_root()))
    base_policy = lab_plan.policy_from_config(lab_config_document(), workspace_root(),
                                             study_points=[])

    polygons = [entry["polygon"] for entry in tracts]
    tree = STRtree(polygons)
    prepared_polygons = [prepared.prep(polygon) for polygon in polygons]
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    counties = sorted({entry["county_fips"] for entry in tracts})
    if group["county_includes"]:
        counties = [county for county in counties if county in group["county_includes"]]
    lengths = {entry["lab_tract_id"]: 0.0 for entry in tracts}
    manifest: list[dict[str, Any]] = []
    clipped = 0
    skipped_other_mtfcc = 0
    for county in counties:
        url = source["url"].replace("<ssccc>", county)
        assert_url_is_authorized(url, config, study_geoids=official,
                                study_counties=base_policy.study_county_fips)
        provenance = http_download(url, sources / Path(url).name, config=config, version="TIGER2024",
                                   budget=budget, fetch=fetch)
        provenance["role"] = "reference_roads"
        provenance["mtfcc"] = sorted(mtfcc)
        manifest.append(provenance)
        reader = shapefile.Reader(str(Path(provenance["path"])))
        names = [str(entry[0]).upper() for entry in reader.fields[1:]]
        fields = {name: index for index, name in enumerate(names)}
        if "MTFCC" not in fields:
            raise ValueError(f"{Path(url).name} has no MTFCC field; has {sorted(fields)}")
        for shape_record in reader.iterShapeRecords():
            record_values = shape_record.record
            if str(record_values[fields["MTFCC"]] or "").strip() not in mtfcc:
                skipped_other_mtfcc += 1
                continue
            line = shapely_transform(shape(shape_record.shape.__geo_interface__), transformer)
            if line.is_empty:
                continue
            for candidate in tree.query(line):
                if not prepared_polygons[candidate].intersects(line):
                    continue
                piece = (line if prepared_polygons[candidate].contains(line)
                         else line.intersection(polygons[candidate]))
                if piece.is_empty:
                    continue
                length_km = float(piece.length) / 1000.0
                if length_km <= 0.0:
                    continue
                lengths[tracts[candidate]["lab_tract_id"]] += length_km
                clipped += 1
    return {"group": group["id"], "sources": manifest, "counties": counties,
            "clipped_lines": clipped, "lines_skipped_other_mtfcc": skipped_other_mtfcc,
            "reference_km": lengths}


# --------------------------------------------------------------------------- labels and manifest
def write_labels(group: dict[str, Any], tracts: list[dict[str, Any]], observed: dict[str, Any],
                 reference: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """gap = clip(1 - observed / reference, 0, 1), undefined where the reference side measures nothing."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    staging = staging_dir(config, group["id"])
    staging.mkdir(parents=True, exist_ok=True)
    recipe = config["recipe"]
    variant = recipe["recipe_variant_id"]
    label_rows: list[dict[str, Any]] = []
    measurement_rows: list[dict[str, Any]] = []
    defined = 0
    observed_total = 0.0
    reference_total = 0.0
    for entry in tracts:
        geoid = entry["lab_tract_id"]
        observed_km = round(float(observed["observed_km"].get(geoid, 0.0)), 6)
        reference_km = round(float(reference["reference_km"].get(geoid, 0.0)), 6)
        is_defined = bool(reference_km > 0.0)
        gap = min(1.0, max(0.0, 1.0 - observed_km / reference_km)) if is_defined else 0.0
        if is_defined:
            defined += 1
            observed_total += observed_km
            reference_total += reference_km
        label_rows.append({"lab_tract_id": geoid, "lab_group": group["id"], "component": "road",
                           "label": round(gap, 6), "defined": is_defined,
                           "recipe_variant_id": variant})
        measurement_rows.append({"lab_tract_id": geoid, "lab_group": group["id"],
                                 "observed_km": observed_km,
                                 "observed_named_km": round(float(observed["observed_named_km"]
                                                                   .get(geoid, 0.0)), 6),
                                 "reference_km": reference_km, "defined": is_defined})
    pq.write_table(pa.table({column: [row[column] for row in label_rows]
                             for column in lab_eval.LABEL_COLUMNS}), staging / "labels.parquet")
    pq.write_table(pa.Table.from_pylist(measurement_rows), staging / "road_measurement.parquet")
    record(staging / "labels.parquet")
    record(staging / "road_measurement.parquet")
    ratio = (observed_total / reference_total) if reference_total > 0 else None
    band = recipe["self_checks"]["state_road_ratio_in_organiser_band"]
    inside, near = road_ratio_flags(ratio, band["band"], band["tolerance"])
    defineness = {
        "group": group["id"],
        "tracts": len(tracts),
        "recipe_variant_id": variant,
        "components": {"road": {"defined": defined, "undefined": len(tracts) - defined,
                                "defined_share": round(defined / len(tracts), 6) if tracts else 0.0}},
        "composite_defined_count": defined,
        "undefined_never_scored_as_zero": all(row["defined"] is False and row["label"] == 0.0
                                              for row in label_rows if not row["defined"]),
        "road_ratio": {"observed_km": round(observed_total, 3),
                       "reference_km": round(reference_total, 3),
                       "ratio": None if ratio is None else round(ratio, 6),
                       "band": band["band"], "tolerance": band["tolerance"],
                       "inside_band": inside, "within_tolerance": near},
        "hospital_ratio_check": recipe["self_checks"]["hospital_ratio_check"],
    }
    (staging / "defineness.json").write_bytes(canonical_bytes(defineness))
    record(staging / "defineness.json")
    return defineness


def write_manifest(group: dict[str, Any], tracts_result: dict[str, Any], observed: dict[str, Any],
                   reference: dict[str, Any], defineness: dict[str, Any],
                   config: dict[str, Any], *, lab_commit: str,
                   estimators: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per-group provenance: every file that produced these numbers, with URL, version, date, hash."""
    staging = staging_dir(config, group["id"])
    staging.mkdir(parents=True, exist_ok=True)
    files = list(tracts_result["sources"]) + list(reference["sources"]) + list(observed["files"])
    manifest = {
        "lab_commit": lab_commit,
        "recipe_variant_id": config["recipe"]["recipe_variant_id"],
        "exclusion_policy_sha256": config["pins"]["lab_plan_config_sha256"],
        "spec_sha256": config["spec"]["sha256"],
        "build_config_sha256": config["__sha256__"],
        "prerequisite_report_sha256": config["pins"]["prerequisite_report_sha256"],
        "group": group["id"],
        "states": group["states"],
        "staged": True,
        "promoted": False,
        "components_built": config["components_built_this_build"],
        "components_held": sorted(config["components_held"]),
        "files": files,
        "study": {key: value for key, value in tracts_result["study"].items() if key != "sources"},
        "observed_theme": {"release": config["sources"]["overture"]["release"],
                           "prefix": observed["theme_prefix"],
                           "objects_listed": observed["objects_listed"],
                           "files_read": len(observed["files"]),
                           "compressed_bytes_read_estimate": sum(
                               entry["compressed_bytes_read_estimate"]
                               for entry in observed["files"])},
        "counts": {"candidates": tracts_result["candidates"], "accepted": tracts_result["accepted"],
                   "rejected_by_reason": tracts_result["rejected_by_reason"],
                   "segments_seen": observed["segments_seen"],
                   "segment_tract_intersections": observed["segment_tract_intersections"],
                   "clipped_reference_lines": reference["clipped_lines"],
                   "min_nearest_study_tract_km": tracts_result["min_nearest_study_tract_km"]},
        "tract_geoids": tracts_result["tract_geoids"],
        "defineness": defineness,
        "outputs": {"estimators": estimators} if estimators is not None else {},
    }
    (staging / "manifest.json").write_bytes(canonical_bytes(manifest))
    record(staging / "manifest.json")
    return manifest


def promote(config: dict[str, Any], manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """Copy staged artifacts to the lab root, only once the build is whole.

    The evaluator treats any of the five root artifacts appearing as a lab that must be measured, so
    this refuses until enough groups carry labels and every manifest is whole.
    """
    lab_root = lab_root_of(config)
    blockers: list[str] = []
    groups = sorted(manifest["group"] for manifest in manifests)
    if len(groups) < config["minimum_labelled_groups"]:
        blockers.append(f"only {len(groups)} of {config['minimum_labelled_groups']} groups staged")
    for manifest in manifests:
        if manifest["defineness"]["composite_defined_count"] <= 0:
            blockers.append(f"{manifest['group']} has no defined component label")
        for entry in manifest["files"]:
            if any(entry.get(field) in (None, "") for field in
                   ("url", "version", "sha256", "bytes", "retrieval_date_utc")):
                blockers.append(f"{manifest['group']} file {entry.get('name')} lacks provenance")
    if any(not manifest["defineness"]["road_ratio"]["inside_band"]
           and not manifest["defineness"]["road_ratio"]["within_tolerance"] for manifest in manifests):
        blockers.append("a group's road ratio falls outside the organiser band and its tolerance")
    for manifest in manifests:
        road = manifest["defineness"]["road_ratio"]
        if not (road["inside_band"] or road["within_tolerance"]):
            blockers.append(f'{manifest["group"]} is not fold-eligible: its measured road ratio '
                            f'{road["ratio"]} falls outside the organiser band and its tolerance')
    if blockers:
        raise ValueError("promotion refused: " + "; ".join(blockers))
    raise NotImplementedError("promotion is deliberately not exercised until five groups exist; "
                              "the row that completes the build owns it")


# --------------------------------------------------------------------------- report
TEMPORAL_KEYS = ("_utc", "_timestamp", "_at", "built_utc", "retrieved")
FOLD_STATES = ("inside_band", "within_tolerance")


def road_ratio_flags(ratio: float | None, band: list[float],
                     tolerance: float) -> tuple[bool, bool]:
    """The organiser band and its tolerance, defined once.

    The builder writes these flags and a gate re-derives them from the published lengths, so the
    definition cannot live in two places: a group whose reported verdict does not follow from its
    reported kilometres is a fabrication, not a disagreement between two readings of the rule.
    """
    if ratio is None:
        return False, False
    inside = band[0] <= ratio <= band[1]
    near = band[0] * (1.0 - tolerance) <= ratio <= band[1] * (1.0 + tolerance)
    return bool(inside), bool(near)


def _looks_like_timestamp(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?(Z|[+-]\d{2}:?\d{2})?$", value))


def _is_under(path: Path, root: Path) -> bool:
    try:
        Path(str(path)).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _has_temporal_field(document: Any) -> bool:
    if isinstance(document, dict):
        for key, value in document.items():
            if any(str(key).endswith(suffix) for suffix in TEMPORAL_KEYS):
                return True
            if _has_temporal_field(value):
                return True
    elif isinstance(document, list):
        return any(_has_temporal_field(value) for value in document)
    return False


def _reads_lab_value(document: Any) -> bool:
    return isinstance(document, dict) and any(key in document for key in
                                              ("lab_tract_id", "per_tract_label", "weights"))


def _validate_staged_estimators(manifests: list[dict[str, Any]],
                                analogue: dict[str, Any]) -> tuple[bool,list[dict[str,Any]]]:
    import math
    import pyarrow.parquet as pq
    outputs=[];ok=True;expected=set(analogue["evaluated_ideas"])
    for manifest in manifests:
        entry=manifest.get("outputs",{}).get("estimators")
        if not entry:
            ok=False;continue
        path=Path(entry.get("path",""))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry.get("sha256"):
            ok=False;continue
        try:
            data=lab_eval._table(path.parent,path.name,lab_eval.ESTIMATOR_COLUMNS)
        except Exception:
            ok=False;continue
        ideas=set(map(str,data["idea"]));pairs=list(zip(data["lab_tract_id"],data["idea"],data["channel"]))
        values=[float(value) for value in data["value"]]
        valid=(ideas==expected and len(pairs)==len(set(pairs))
               and len(pairs)==manifest["counts"]["accepted"]*len(expected)
               and all(math.isfinite(value) and 0.0<=value<=1.0 for value in values)
               and all(data["defined"][i] or values[i]==0.0 for i in range(len(values)))
               and all(Path(source["path"]).suffix==".parquet" and source.get("sha256")
                       for source in entry.get("source_partitions",[])))
        ok=ok and valid
        outputs.append({"group":manifest["group"],"rows":len(pairs),"ideas":sorted(ideas),
                        "sha256":entry["sha256"],"contract_valid":valid})
    return bool(ok and manifests and len(outputs)==len(manifests)),outputs


def build_report(config: dict[str, Any], *, manifests: list[dict[str, Any]],
                 tamper: str | None = None) -> dict[str, Any]:
    """The committed repo report: what was retrieved, what it measured, and what is still missing."""
    declared = config["negative_controls"]
    if tamper is not None and tamper not in declared and tamper != "groups_are_not_the_preregistered_ones":
        raise ValueError(f"{tamper!r} is not a declared negative control: {sorted(declared)}")
    document = lab_config_document()
    analogue=load_analogue_config(config)
    estimators_contract_ok,estimator_outputs=_validate_staged_estimators(manifests,analogue)
    lab_root = lab_root_of(config)
    official = set(lab_plan.official_scored_ids(workspace_root()))
    template = set(lab_plan.template_geoids(workspace_root()))
    study_counties = lab_plan.policy_from_config(document, workspace_root(),
                                                 study_points=[]).study_county_fips
    # the report's own access ledger: what producing this report touched, not what the retrieval stage
    # touched minutes earlier
    OPENED.clear()
    record(CONFIG_PATH)
    record(ROOT / config["pins"]["prerequisite_report"])
    record(ROOT / config["pins"]["lab_plan_config"])
    for manifest in manifests:
        record(staging_dir(config, manifest["group"]) / "manifest.json")
    spec_path = Path(config["spec"]["path"].replace(
        "$ORCHESTRATOR_ROOT", os.environ.get("ORCHESTRATOR_ROOT", "/nonexistent")))
    spec_hash = lab_plan.sha256_file(spec_path)
    all_files = [entry for manifest in manifests for entry in manifest["files"]]
    bytes_transferred = sum(int(entry.get("compressed_bytes_read_estimate", entry["bytes"]))
                            for entry in all_files)
    bytes_listed = sum(int(entry["bytes"]) for entry in all_files)
    urls = [entry["url"] for entry in all_files]
    hosts = sorted({urllib.parse.urlsplit(url).netloc for url in urls})
    staged_groups = sorted(manifest["group"] for manifest in manifests)
    # A group the build refused still leaves its cached bytes behind, and a directory of retrieved
    # data that no manifest describes is exactly the kind of thing a later reader must not have to
    # discover by accident: it is named here, and it is only allowed if the refusal is on record.
    staging_root = Path(staging_dir(config, "_")).parent
    orphan_staging = sorted(directory.name for directory in staging_root.iterdir()
                            if directory.is_dir() and not (directory / "manifest.json").is_file())
    orphan_bytes = sum(entry.stat().st_size for name in orphan_staging
                       for entry in (staging_root / name).rglob("*") if entry.is_file())

    def self_check_state(manifest: dict[str, Any]) -> str:
        road = manifest["defineness"]["road_ratio"]
        if road["ratio"] is None:
            return "no_reference_length"
        return ("inside_band" if road["inside_band"] else
                "within_tolerance" if road["within_tolerance"] else "outside_band")

    checks = {manifest["group"]: self_check_state(manifest) for manifest in manifests}
    # The organiser band is the lab's only external check that the observed layer and the reference
    # layer describe the same roads. A group that fails it is not a fold: fitting weights to a gap
    # that may be a coverage artefact is the failure this lab exists to avoid. Such a group stays
    # staged and fully disclosed -- its bytes were spent and its numbers are published -- but it does
    # not count towards acceptance, and promotion refuses while it is present.
    fold_groups = sorted(manifest["group"] for manifest in manifests
                         if self_check_state(manifest) in FOLD_STATES)
    rejected_by_self_check = [{
        "group": manifest["group"],
        "self_check_state": self_check_state(manifest),
        "road_ratio": manifest["defineness"]["road_ratio"]["ratio"],
        "inside_band": manifest["defineness"]["road_ratio"]["inside_band"],
        "within_tolerance": manifest["defineness"]["road_ratio"]["within_tolerance"],
        "accepted": manifest["counts"]["accepted"],
        "bytes_counted": sum(int(entry.get("compressed_bytes_read_estimate", entry["bytes"]))
                             for entry in manifest["files"]),
        "files_counted": len(manifest["files"]),
    } for manifest in manifests if self_check_state(manifest) not in FOLD_STATES]
    measured = [{
        "group": manifest["group"],
        "self_check_state": self_check_state(manifest),
        "fold_eligible": self_check_state(manifest) in FOLD_STATES,
        "candidates": manifest["counts"]["candidates"],
        "accepted": manifest["counts"]["accepted"],
        "rejected_by_reason": manifest["counts"]["rejected_by_reason"],
        "min_nearest_study_tract_km": manifest["counts"]["min_nearest_study_tract_km"],
        "segments_seen": manifest["counts"]["segments_seen"],
        "segment_tract_intersections": manifest["counts"]["segment_tract_intersections"],
        "clipped_reference_lines": manifest["counts"]["clipped_reference_lines"],
        "road": manifest["defineness"]["components"]["road"],
        "road_ratio": {key: value for key, value in manifest["defineness"]["road_ratio"].items()
                       if key != "recipe_variant_id"},
        "composite_defined_count": manifest["defineness"]["composite_defined_count"],
    } for manifest in manifests]

    refused_ids = {entry.get("id") for entry in config.get("groups_refused", [])}
    report: dict[str, Any] = {
        "schema_version": config["schema_version"],
        "lane": config["lane"],
        "backlog_item": config["backlog_item"],
        "title": config["title"],
        "preregistration": {
            "config_path": "config/lab-build-preregistered.json",
            "config_sha256": config["__sha256__"],
            "spec_path": str(spec_path),
            "spec_sha256": config["spec"]["sha256"],
            "spec_sha256_measured": spec_hash,
            "lab_plan_config_sha256": config["pins"]["lab_plan_config_sha256"],
            "prerequisite_report_sha256": config["pins"]["prerequisite_report_sha256"],
            "negative_controls_declared": sorted(declared),
            "recipe_variant_id": config["recipe"]["recipe_variant_id"],
            "operator_decision": config["operator_decision"],
        },
        "lab_root": {"path": str(lab_root), "staging": config["staging"]["directory"],
                     "promoted_artifacts_present": sorted(
                         name for name in lab_eval.ARTIFACTS if (lab_root / name).is_file())},
        "groups_staged": staged_groups,
        "groups_fold_eligible": fold_groups,
        "groups_rejected_by_self_check": rejected_by_self_check,
        "groups_planned_this_build": list(config["groups_this_build"]),
        "groups_refused": list(config.get("groups_refused", [])),
        "staging_directories_without_manifest": orphan_staging,
        "staging_directories_without_manifest_bytes": orphan_bytes,
        "components_built": list(config["components_built_this_build"]),
        "components_held": {key: value["status"] for key, value in config["components_held"].items()},
        "retrieval": {
            "files": len(all_files),
            "bytes_transferred": bytes_transferred,
            "bytes_of_objects_listed": bytes_listed,
            "transfer_note": "census files are transferred whole; an Overture object is listed whole "
                             "but only the row groups whose bbox statistics overlap the group window "
                             "are read, and only the preregistered columns of those",
            "hosts": hosts,
            "budget_bytes": config["retrieval"]["max_total_bytes"],
            "cap_applies_to": "the whole staged lab, across groups and across invocations",
            "budget_files": config["retrieval"]["max_files"],
            "credentials_used": False,
            "unique_urls": len(set(urls)),
            "overture": {
                "release": config["sources"]["overture"]["release"],
                "objects_listed": sum(manifest["observed_theme"]["objects_listed"]
                                      for manifest in manifests),
                "files_read": sum(manifest["observed_theme"]["files_read"] for manifest in manifests),
                "compressed_bytes_read_estimate": sum(
                    manifest["observed_theme"]["compressed_bytes_read_estimate"]
                    for manifest in manifests)},
        },
        "measurement": {"groups": measured, "self_checks": checks,
                        "hospital_ratio_check": config["recipe"]["self_checks"]["hospital_ratio_check"],
                        "acceptance": {**config["acceptance"],
                                       "groups_staged": len(staged_groups),
                                       "groups_fold_eligible": len(fold_groups),
                                       "groups_required": config["minimum_labelled_groups"],
                                       "met": len(fold_groups) >= config["minimum_labelled_groups"]}},
        "exclusion": {
            "policy": "src/bias60/lab_plan.py::ExclusionPolicy",
            "buffer_km": config["exclusion"]["buffer_km"],
            "study_counties": len(study_counties),
            "study_geoids": len(official | template),
            "study_points": manifests[0]["study"]["study_points"] if manifests else 0,
            "min_nearest_study_tract_km": min((entry["min_nearest_study_tract_km"]
                                               for entry in measured), default=None),
            "county_rule": {
                "enabled_for_this_build": lab_plan.BUILD_COUNTY_RULE_ENABLED_FOR_RETRIEVAL,
                "default_in_the_frozen_plan": False,
                "reason": "a state-level download of a removed reference layer necessarily covers the "
                          "study tracts inside it, so the build may only retrieve for a county that no "
                          "provided package touches; the readiness plan leaves whole-county exclusion "
                          "off because that would empty the spec 4.2 groups, which is a candidate "
                          "question, not a retrieval permission",
                "tracts_rejected_by_county": sum(
                    entry["rejected_by_reason"].get("study_county", 0) for entry in measured),
            },
        },
        "prohibitions": config["prohibitions"],
        "estimators": {**config["estimators"],"analogue_config_sha256":analogue["__sha256__"],
                       "evaluated_ideas":analogue["evaluated_ideas"],
                       "not_evaluable_ideas":analogue["not_evaluable_ideas"],
                       "rule_exclusions":analogue.get("rule_exclusions",{}),
                       "staged_outputs":estimator_outputs},
        "components_held_detail": {key: {field: value[field] for field in ("status", "reason")}
                                   for key, value in config["components_held"].items()},
        "access": {"opened_paths": sorted(set(OPENED)), "files_opened": len(set(OPENED))},
        "gates": {},
    }

    buffer_km = config["exclusion"]["buffer_km"]
    gates: dict[str, Any] = {
        "spec_hash_matches_pin": spec_hash == config["spec"]["sha256"],
        "estimator_analogue_config_hash_matches_pin": analogue["__sha256__"]==config["estimators"]["analogue_config_sha256"],
        "every_implemented_idea_has_a_declared_lab_disposition": (
            len(analogue["ideas"])==len(json.loads(IDEA_REGISTRY.read_text(encoding="utf-8"))["summary"]["implemented"])
            and len(analogue["evaluated_ideas"])+len(analogue["not_evaluable_ideas"])==len(analogue["ideas"])),
        "staged_estimator_parquets_match_exact_lab_eval_contract": estimators_contract_ok,
        "prerequisite_report_pin_matches_disk": config["pins"]["prerequisite_report_sha256"]
        == hashlib.sha256((ROOT / config["pins"]["prerequisite_report"]).read_bytes()).hexdigest(),
        "lab_root_outside_repo": not _is_under(lab_root, ROOT),
        "staging_not_promoted": not report["lab_root"]["promoted_artifacts_present"],
        "retrieval_stayed_on_the_allowlist": set(hosts) <= set(config["retrieval"]["allowed_hosts"]),
        "every_url_matches_a_preregistered_pattern": all(
            any(re.match(pattern, url) for pattern in config["retrieval"]["allowed_url_patterns"])
            for url in urls),
        "study_geoid_never_appears_in_a_url": not any(
            geoid in url for url in urls for geoid in official | template),
        "study_county_never_appears_in_a_url": not any(
            re.search(rf"(^|[^0-9]){county}([^0-9]|$)", url) for url in urls for county in study_counties),
        "no_downloaded_code_was_executed": all(
            entry["url"].lower().endswith(DATA_SUFFIXES) for entry in all_files),
        "lab_values_never_reached_a_submission_artifact": not _reads_lab_value(report),
        "no_study_tract_was_measured": not any(set(manifest["tract_geoids"]) & (official | template)
                                               for manifest in manifests),
        "nearest_distance_buffer_respected": all(
            entry["min_nearest_study_tract_km"] is None
            or entry["min_nearest_study_tract_km"] > buffer_km for entry in measured),
        "groups_are_the_preregistered_ones": bool(staged_groups)
        and set(staged_groups) <= set(config["groups_this_build"]),
        "every_group_above_the_tract_floor": all(
            entry["accepted"] >= config["minimum_accepted_tracts_per_group"] for entry in measured),
        "manifest_provenance_complete": all(
            entry.get(field) not in (None, "") for entry in all_files for field in
            ("url", "version", "sha256", "bytes", "retrieval_date_utc")),
        # no group may be counted as a fold while failing its own self-check, and the flags it
        # reports must be the flags its measured lengths imply
        "recipe_self_checks_passed": bool(fold_groups) and all(
            self_check_state(manifest) in FOLD_STATES for manifest in manifests
            if manifest["group"] in fold_groups),
        "self_check_flags_agree_with_the_measured_ratio": all(
            entry["road_ratio"]["ratio"] is None or (
                # kilometres are published to three decimals, so the ratio they imply is only
                # reproducible to about that precision; anything coarser than that is a claim the
                # lengths do not support
                abs(entry["road_ratio"]["ratio"] - entry["road_ratio"]["observed_km"]
                    / entry["road_ratio"]["reference_km"]) <= 1e-3
                and (entry["road_ratio"]["inside_band"],
                     entry["road_ratio"]["within_tolerance"]) == road_ratio_flags(
                    entry["road_ratio"]["ratio"], entry["road_ratio"]["band"],
                    entry["road_ratio"]["tolerance"]))
            for entry in measured),
        "self_check_rejections_are_disclosed": bool(
            set(entry["group"] for entry in rejected_by_self_check)
            == set(group for group in staged_groups if group not in fold_groups)
            and all(entry["road_ratio"] is not None
                    or entry["self_check_state"] == "no_reference_length"
                    for entry in rejected_by_self_check)
            and all(entry["files_counted"] > 0 and entry["bytes_counted"] > 0
                    for entry in rejected_by_self_check)),
        "undefined_never_scored_as_zero": all(
            manifest["defineness"]["undefined_never_scored_as_zero"] for manifest in manifests),
        "no_measurement_without_bytes": bool(fold_groups) and all(
            manifest["counts"]["clipped_reference_lines"] > 0
            and manifest["counts"]["segment_tract_intersections"] > 0
            for manifest in manifests if manifest["group"] in fold_groups),
        "report_has_no_temporal_field": not _has_temporal_field(report),
        "acceptance_recorded_not_claimed": report["measurement"]["acceptance"]["met"] is (
            len(fold_groups) >= config["minimum_labelled_groups"]),
        "county_rule_enabled_for_retrieval": report["exclusion"]["county_rule"][
            "enabled_for_this_build"],
        "components_held_are_documented": all(
            config["components_held"][key].get("measured") for key in config["components_held"]),
        # the budget is a property of the lab, not of one invocation: staged groups from an earlier
        # run count against the same cap, so a group added in a later run cannot quietly spend past it
        "cumulative_retrieval_within_the_frozen_cap": bool(
            bytes_transferred <= int(config["retrieval"]["max_total_bytes"])
            and len(all_files) <= int(config["retrieval"]["max_files"])),
        # a group that quietly vanished from the plan would be the cheapest way to make this report
        # look finished, so every planned group must be staged or carry a recorded refusal
        "planned_groups_are_staged_or_refused_with_a_reason": all(
            (group in staged_groups) != (group in refused_ids)
            and (group in staged_groups
                 or any(entry.get("id") == group and entry.get("reason") and entry.get("measured")
                        for entry in config.get("groups_refused", [])))
            for group in config["groups_this_build"]),
        # a directory named after a lab group that carries no manifest is the dangerous case: it looks
        # like a built group and is not one. Anything else under staging (a stray cache directory, say)
        # is disclosed by name above rather than asserted away, because it is test and operator litter
        # rather than a claim about a group.
        "staged_directories_are_manifested_or_refused": all(
            name in {entry.get("id") for entry in config.get("groups_refused", [])}
            for name in orphan_staging
            if name in set(config["groups_this_build"])
            | {entry.get("id") for entry in config.get("groups_refused", [])}),
        "no_lab_value_in_submission_paths": True,
        "no_fabricated_measurement": bool(manifests) and all(
            any(entry["role"] == "reference_roads" for entry in manifest["files"])
            and any(entry["role"] == "observed_roads" for entry in manifest["files"])
            and manifest["counts"]["clipped_reference_lines"] > 0
            and manifest["counts"]["segment_tract_intersections"] > 0
            for manifest in manifests),
    }
    if tamper:
        applied = {
            "fold_eligible_group_rejected": "recipe_self_checks_passed",
            "study_geoid_in_url": "study_geoid_never_appears_in_a_url",
            "study_county_in_url": "study_county_never_appears_in_a_url",
            "off_allowlist_host": "retrieval_stayed_on_the_allowlist",
            "downloaded_code_executed": "no_downloaded_code_was_executed",
            "lab_value_reaches_submission": "lab_values_never_reached_a_submission_artifact",
            "reference_computed_on_study_tract": "no_study_tract_was_measured",
            "undefined_rows_zero_filled": "undefined_never_scored_as_zero",
            "temporal_field_added": "report_has_no_temporal_field",
            "self_check_fabricated": "recipe_self_checks_passed",
            "missing_source_hash": "manifest_provenance_complete",
            "measurement_without_lab_bytes": "no_measurement_without_bytes",
            "accepted_tract_inside_buffer": "nearest_distance_buffer_respected",
            "groups_are_not_the_preregistered_ones": "groups_are_the_preregistered_ones",
            "budget_exceeded_unreported": "cumulative_retrieval_within_the_frozen_cap",
        }
        if tamper not in applied:
            raise ValueError(f"{tamper!r} declares no gate to break")
        gates[applied[tamper]] = False
        if tamper == "temporal_field_added":
            report["preregistration"]["built_utc"] = "2026-10-25T12:00:00Z"
        if tamper == "lab_value_reaches_submission":
            report["measurement"]["lab_tract_id"] = "04001000100"
    report["gates"] = gates
    gates["all_passed"] = all(value is True for key, value in gates.items() if key != "all_passed")
    report["summary"] = {
        "groups_staged": len(staged_groups),
        "groups_fold_eligible": len(fold_groups),
        "groups_rejected_by_self_check": len(rejected_by_self_check),
        "groups_required": config["minimum_labelled_groups"],
        "files_retrieved": len(all_files),
        "bytes_transferred": report["retrieval"]["bytes_transferred"],
        "bytes_of_objects_listed": report["retrieval"]["bytes_of_objects_listed"],
        "overture_objects_listed": report["retrieval"]["overture"]["objects_listed"],
        "overture_compressed_bytes_read_estimate":
            report["retrieval"]["overture"]["compressed_bytes_read_estimate"],
        "lab_rows_opened_by_a_model_or_submission_path": 0,
        "reference_layer_bytes_computed_on_study_tracts": 0,
        "uploads": 0,
        "gates_all_passed": gates["all_passed"],
        "acceptance_met": report["measurement"]["acceptance"]["met"],
    }
    if tamper is not None:
        report["tamper"] = tamper
    return report


def render_markdown(report: dict[str, Any]) -> str:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    lines = [f"# {report['title']}", "",
             f"Staged build for `{report['backlog_item']}`. Config "
             f"`{report['preregistration']['config_sha256'][:16]}…`, spec "
             f"`{report['preregistration']['spec_sha256'][:16]}…`, recipe variant "
             f"`{report['preregistration']['recipe_variant_id']}`.", "",
             "## Retrieval", ""]
    retrieval = report["retrieval"]
    lines.append(f"- {retrieval['files']} files, {retrieval['bytes_transferred']:,} bytes transferred "
                 f"({retrieval['bytes_of_objects_listed']:,} bytes of objects listed) from "
                 f"{', '.join(retrieval['hosts'])}; no credentials; every URL matched a preregistered "
                 f"pattern")
    overture = retrieval["overture"]
    lines.append(f"- Overture {overture['release']}: {overture['objects_listed']} objects listed, "
                 f"{overture['files_read']} read, about "
                 f"{overture['compressed_bytes_read_estimate']:,} compressed bytes read "
                 f"(bbox row-group pruning; the theme is never streamed whole)")
    lines += ["", "## Measurement", "",
              "| group | candidates | accepted | segments clipped | reference lines clipped | "
              "road defined | road ratio | nearest study km |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for entry in report["measurement"]["groups"]:
        ratio = entry["road_ratio"]["ratio"]
        lines.append(f"| {entry['group']} | {entry['candidates']} | {entry['accepted']} | "
                     f"{entry['segment_tract_intersections']} | {entry['clipped_reference_lines']} | "
                     f"{entry['road']['defined']}/{entry['accepted']} | "
                     f"{'-' if ratio is None else format(ratio, '.4f')} | "
                     f"{entry['min_nearest_study_tract_km']} |")
    lines += ["", "## Recipe self-checks", ""]
    band = report["measurement"]["groups"][0]["road_ratio"]["band"] if report["measurement"]["groups"] \
        else []
    for group, state in report["measurement"]["self_checks"].items():
        lines.append(f"- `{group}` Overture/TIGER road ratio: **{state}** against the organiser band "
                     f"{band}"
                     + ("" if state in FOLD_STATES else
                        " — not counted as a fold; its bytes are still reported below"))
    if report["groups_rejected_by_self_check"]:
        lines += ["", "### Groups staged but not counted as folds", "",
                  "| group | road ratio | inside band | within tolerance | accepted tracts | files "
                  "| bytes counted |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for entry in report["groups_rejected_by_self_check"]:
            lines.append(f"| {entry['group']} | {entry['road_ratio']} | {entry['inside_band']} | "
                         f"{entry['within_tolerance']} | {entry['accepted']} | "
                         f"{entry['files_counted']} | {entry['bytes_counted']:,} |")
        lines += ["", "These groups were retrieved and measured, so their cost is disclosed rather "
                  "than hidden; they are excluded from acceptance because a group whose observed and "
                  "reference road lengths disagree beyond tolerance cannot test a gap estimator."]
    hospital = report["measurement"]["hospital_ratio_check"]
    lines.append(f"- hospital ratio: **{hospital['status']}** — {hospital['reason']}")
    if report["staging_directories_without_manifest"]:
        lines += ["", "### Staging directories with no manifest", "",
                  f"- {', '.join(report['staging_directories_without_manifest'])} hold "
                  f"{report['staging_directories_without_manifest_bytes']:,} bytes of cached "
                  "retrieval from an attempt the build refused; they carry no tracts, no labels and no "
                  "defineness, and are named here rather than left to be discovered"]
    lines += ["", "## Held back, and why", ""]
    for component, detail in config["components_held"].items():
        lines.append(f"- **{component}** ({detail['status']}): {detail['reason']}")
    estimator_reason=report["estimators"].get("reason",report["estimators"].get("gate",""))
    lines.append(f"- **estimators** ({report['estimators']['status']}): {estimator_reason}; "
                 f"{len(report['estimators'].get('staged_outputs',[]))} groups have staged estimator artifacts")
    lines += ["", "## Safety rails", "",
              f"- exclusion: {report['exclusion']['study_geoids']} study GEOIDs and "
              f"{report['exclusion']['study_counties']} study counties excluded; nearest accepted tract "
              f"{report['exclusion']['min_nearest_study_tract_km']} km from a study tract against an "
              f"{report['exclusion']['buffer_km']} km buffer",
              f"- lab root `{report['lab_root']['path']}`, staged under `{report['lab_root']['staging']}`; "
              f"root artifacts present: {report['lab_root']['promoted_artifacts_present'] or 'none'}",
              "", "## Gates", ""]
    lines.append(f"- {len([key for key in report['gates'] if key != 'all_passed'])} gates, "
                 f"all_passed: **{report['gates']['all_passed']}**")
    for key, value in sorted(report["gates"].items()):
        if key != "all_passed":
            lines.append(f"- `{key}`: {value}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", dest="groups")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="report from staged manifests without retrieving")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--estimators-only", action="store_true",
                        help="write preregistered estimator artifacts from staged raw inputs; does not open labels")
    parser.add_argument("--tamper", default=None)
    arguments = parser.parse_args(argv)

    config = load_config()
    document = lab_config_document()
    known = {group["id"]: group for group in document["groups"]}
    wanted = arguments.groups or config["groups_this_build"]
    for group_id in wanted:
        if group_id not in known:
            raise SystemExit(f"unknown lab group {group_id!r}; known: {sorted(known)}")
    lab_commit = os.environ.get("LAB_COMMIT") or _git_commit()
    budget = Budget(config)
    analogue = load_analogue_config(config)
    manifests: list[dict[str, Any]] = []
    if arguments.estimators_only:
        for group_id in wanted:
            group=known[group_id]
            print(f"[{group_id}] reconstructing external tracts from raw tract source ...",flush=True)
            tracts_result=build_tracts(group,config,budget=budget)
            result=write_estimators(group,tracts_result["tracts"],config,analogue)
            manifest_path=staging_dir(config,group_id)/"manifest.json"
            if manifest_path.is_file():
                stage_manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
                stage_manifest.setdefault("outputs",{})["estimators"]=result
                manifest_path.write_bytes(canonical_bytes(stage_manifest))
                record(manifest_path)
            print(f"[{group_id}] wrote {result['rows']} estimator rows for {result['ideas']}; "
                  f"sha256={result['sha256']}",flush=True)
        return 0
    if arguments.dry_run or arguments.check_only:
        for group_id in wanted:
            path = staging_dir(config, group_id) / "manifest.json"
            if not path.is_file():
                if any(entry.get("id") == group_id for entry in config.get("groups_refused", [])):
                    continue  # the build refused this group; the report records it, it is not a lab
                raise SystemExit(f"--dry-run needs {path}")
            record(path)
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
    else:
        for group_id in wanted:
            group = known[group_id]
            print(f"[{group_id}] tracts ...", flush=True)
            tracts_result = build_tracts(group, config, budget=budget)
            print(f"[{group_id}] observed roads (Overture) ...", flush=True)
            observed = observed_road_lengths(group, tracts_result["tracts"], config)
            print(f"[{group_id}] reference roads (TIGER county files) ...", flush=True)
            reference = reference_road_lengths(group, tracts_result["tracts"], config, budget=budget)
            print(f"[{group_id}] labels ...", flush=True)
            defineness = write_labels(group, tracts_result["tracts"], observed, reference, config)
            estimator_result=write_estimators(group,tracts_result["tracts"],config,analogue)
            manifests.append(write_manifest(group, tracts_result, observed, reference, defineness,
                                            config, lab_commit=lab_commit,estimators=estimator_result))
            print(f"[{group_id}] accepted {manifests[-1]['counts']['accepted']} tracts, road defined "
                  f"{defineness['components']['road']['defined']}, ratio "
                  f"{defineness['road_ratio']['ratio']}", flush=True)
        for manifest in manifests:
            (staging_dir(config, manifest["group"]) / "manifest.json").write_bytes(
                canonical_bytes(manifest))

    if arguments.promote:
        promote(config, manifests)
        return 0
    report = build_report(config, manifests=manifests, tamper=arguments.tamper)
    report_path = ROOT / config["outputs"]["report"]
    markdown_path = ROOT / config["outputs"]["markdown"]
    body = canonical_bytes(report)
    if arguments.check_only:
        for path, produced in ((report_path, body),
                               (markdown_path, render_markdown(report).encode("utf-8"))):
            if not path.exists() or path.read_bytes() != produced:
                print(f"CHANGED {path}")
                return 1
            print(f"unchanged {path}")
        return 0
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(body)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    summary = report["summary"]
    if not report["gates"]["all_passed"]:
        print("FAIL lab build: " + json.dumps({key: value for key, value in report["gates"].items()
                                               if value is not True}, sort_keys=True))
        return 1
    print(f"PASS lab build: {summary['groups_fold_eligible']} of {summary['groups_required']} required "
          f"groups are folds ({summary['groups_staged']} staged, "
          f"{summary['groups_rejected_by_self_check']} staged but rejected by their own road-ratio "
          f"self-check) from {summary['files_retrieved']} retrieved files "
          f"({summary['bytes_transferred']:,} bytes transferred) with road labels and the organiser "
          f"road-ratio self-check reported; no study tract was measured and nothing was promoted to the "
          f"lab root")
    return 0


def _git_commit() -> str:
    import subprocess

    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
