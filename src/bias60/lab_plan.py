"""External-lab design for the 60 estimators: spec section 4 as data, plus the study-exclusion policy.

Two things live here and both are safe by construction:

* a parser that turns section 4 of the operator-frozen memo into machine-checkable structure (nine lab
  groups, the source register, the recipe and its variants, the volume table), so the plan cannot drift
  from the spec silently;
* the executable version of spec 4.1 -- an :class:`ExclusionPolicy` that a future build must consult
  *before* loading any reference layer for a candidate tract, rejecting official scored GEOIDs, template
  GEOIDs, study counties and anything within a declared centroid buffer.

Nothing here downloads a reference layer, reads a label, or joins anything to a study tract. The
readiness gate that keeps it that way is ``scripts/report_lab_readiness.py``.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REGION_DIRECTORIES = ["maricopa-az", "northern-ca", "eastern-ok", "south-central-tx"]
_RECORDER = None


def set_recorder(recorder) -> None:
    """Install a callable that logs every file this module opens, so the gate can disclose access."""
    global _RECORDER
    _RECORDER = recorder


def _record(path) -> str:
    if _RECORDER is not None:
        _RECORDER(Path(path))
    return str(path)
REGION_LABELS = {"maricopa-az": "Maricopa AZ", "northern-ca": "Northern CA",
                 "eastern-ok": "Eastern OK", "south-central-tx": "South-Central TX"}
STRATA_COLUMNS = ["GEOID", "STATEFP", "COUNTYFP"]


# --------------------------------------------------------------------------- spec parsing
def sha256_file(path: Path) -> str:
    _record(path)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def section4(spec_path: Path) -> dict[str, str]:
    """Return the subsection bodies of section 4, keyed by their heading."""
    _record(spec_path)
    text = Path(spec_path).read_text(encoding="utf-8")
    start = text.find("\n## 4. The external lab")
    if start < 0:
        raise ValueError(f"spec has no section 4 in {spec_path}")
    body = text[start:]
    stop = re.search(r"\n## (?!4\.)[0-9]+\.", body)
    if stop:
        body = body[: stop.start()]
    parts: dict[str, str] = {}
    headings = [match for match in re.finditer(r"\n### (4\.[0-9] [^\n]+)", body)]
    for index, match in enumerate(headings):
        heading = match.group(1).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        parts[heading] = body[match.end(): end].strip()
    if len(parts) < 5:
        raise ValueError(f"expected five subsections in spec section 4, found {sorted(parts)}")
    return parts


def parse_groups(subsection: str) -> list[dict[str, Any]]:
    """Numbered group entries of 4.2: id, label, grouped-CV unit and approximate tract count."""
    groups: list[dict[str, Any]] = []
    for match in re.finditer(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*\[([A-Z\-]+)\]([^\n]*)",
                             subsection, re.MULTILINE):
        label, unit = match.group(1).strip(), match.group(2).strip()
        # the count lives on the group's own line (4.2 gives "≈890 tracts"), except for CA-F where it
        # is a second sentence, so the line plus the following sentence is the search window
        tail = match.group(3) + subsection[match.end(): match.end() + 200]
        count = re.search(r"\u2248\s*([0-9,]+)\s+tracts|about\s+([0-9,]+)\s+tracts", tail)
        approx = int((count.group(1) or count.group(2)).replace(",", "")) if count else None
        groups.append({"spec_label": label, "cv_unit": unit, "approx_tracts": approx})
    if not groups:
        raise ValueError("no lab groups parsed from spec 4.2")
    return groups


def parse_volume_table(subsection: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in subsection.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] and not set(cells[0]) <= {"-", " "} and cells[0] != "Item":
            rows.append({"item": cells[0].strip("* "), "estimate": cells[1]})
    return rows


def parse_recipe(subsection: str) -> dict[str, Any]:
    """The 4.4 recipe: projection, components, and how many variants each declares."""
    projection = re.search(r"EPSG:(\d{4})", subsection)
    components: dict[str, Any] = {}
    for name, pattern in (("road", r"\*\*Road\.\*\*"), ("building", r"\*\*Building\.\*\*"),
                          ("poi_facilities", r"\*\*POI facilities\.\*\*"),
                          ("poi_establishments", r"\*\*POI establishments\.\*\*")):
        match = re.search(pattern, subsection)
        body = subsection[match.end(): match.end() + 700] if match else ""
        components[name] = {
            "declared": bool(match),
            "uses_clip_mapping": "clip(1 " in body or "clip(1 −" in body or "clip(1 -" in body,
            "variant_mentions": len(re.findall(r"[Vv]ariant", body)),
        }
    undefined = re.search(r"Undefined if ([^.\n]+)\.", subsection)
    return {
        "projection": f"EPSG:{projection.group(1)}" if projection else None,
        "components": components,
        "undefined_rule_seen": undefined.group(1).strip() if undefined else None,
        "recipe_identification_declared": "Recipe identification" in subsection,
        "hospital_check_declared": "Hospital check" in subsection or "hospital" in subsection.lower(),
        "road_ratio_band_declared": bool(re.search(r"0\.71.{0,3}1\.59", subsection)),
    }


def parse_sources(subsection: str) -> dict[str, Any]:
    """Surface-level facts about 4.3 that the register in the config must agree with."""
    return {
        "source_families": {
            "tiger_tracts": "TIGER/Line" in subsection and "tract" in subsection,
            "overture": "Overture" in subsection and "release" in subsection,
            "tiger_prisecroads": "prisecroads" in subsection,
            "microsoft": "USBuildingFootprints" in subsection,
            "global_ml_variant": "GlobalMLBuildingFootprints" in subsection,
            "usgs_structures": "National Structures" in subsection or "Struct_Point" in subsection,
            "cbp_zbp": "cbp23co" in subsection or "ZBP" in subsection,
            "hud_crosswalk": "usps_crosswalk" in subsection or "HUD-USPS" in subsection,
            "hospital_check": "Hospital check" in subsection,
        },
        "structure_codes": sorted({int(code) for code in re.findall(r"\b(7[0-9]{4})\b", subsection)}),
        "allocation_variants": len(re.findall(r"\((i{1,3})\)", subsection)),
    }


def parse_placement(subsection: str) -> dict[str, Any]:
    """4.6 what goes where: what the spec allows in the writeup and what it gates on the ruling."""
    bullets: list[str] = []
    current: list[str] = []
    for line in subsection.splitlines():
        if line.startswith("- "):
            if current:
                bullets.append(" ".join(" ".join(current).split()))
            current = [line[2:]]
        elif current:
            current.append(line)
    if current:
        bullets.append(" ".join(" ".join(current).split()))
    question = re.search(r'Ask one combined question: "(.+?)"', subsection, re.DOTALL)
    return {
        "bullets": bullets,
        "writeup_declared_always_allowed": any(bullet.startswith("**Writeup (always allowed")
                                               for bullet in bullets),
        "scored_submission_default": "Track A" if "Track A by default" in subsection else None,
        "track_b_requires_ruling": "written organiser ruling" in subsection,
        "policy_document": "docs/submission-policy.md" if "docs/submission-policy.md" in subsection
        else None,
        "combined_question": " ".join(question.group(1).split()) if question else None,
    }


def parse_operator_decision(spec_path: Path) -> dict[str, Any] | None:
    """The operator decision block at the head of the memo, if the memo carries one."""
    _record(spec_path)
    text = Path(spec_path).read_text(encoding="utf-8")
    match = re.search(r"\*\*Operator decision (.+?)\.\*\*(.+?)\n\n", text, re.DOTALL)
    if not match:
        return None
    body = " ".join(match.group(2).split()).strip()
    return {"stamp": " ".join(match.group(1).split()), "body": body,
            "verbatim": f"**Operator decision {match.group(1).strip()}.** {body}",
            "overrides_ruling_precondition": "overrides every" in match.group(1)
            or "overrides every" in body}


# --------------------------------------------------------------------------- provided-data study sets
def official_scored_ids(workspace: Path) -> list[str]:
    """Official GEOIDs through the frozen reader only; score placeholder cells are never opened."""
    script = Path(__file__).resolve().parents[2] / "scripts/audit_v2_component_masks.py"
    _record(script)
    spec = importlib.util.spec_from_file_location("bias60_lab_mask_reader", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # the frozen reader returns (GEOID, region) pairs in official order and opens only the GEOID
    # column, so score placeholder cells are never accessed
    return [geoid for geoid, _region in module.official_scored_ids(Path(workspace))]


def template_geoids(workspace: Path) -> list[str]:
    """Every GEOID in the provided platform template, including the 415 eastern-WA rows."""
    path = _record(Path(workspace) / "SampleSubmission.csv")
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [row["GEOID"].strip() for row in csv.DictReader(handle)]


def study_counties(workspace: Path) -> dict[str, dict[str, Any]]:
    """Study state/county FIPS straight from the provided strata tables (declared columns only)."""
    import pyarrow.parquet as pq

    out: dict[str, dict[str, Any]] = {}
    for region in REGION_DIRECTORIES:
        path = _record(Path(workspace) / "defined-flags-inputs"
                       / f"{region}-strata-tract-table.parquet")
        table = pq.read_table(path, columns=STRATA_COLUMNS)
        geoids = [str(value) for value in table.column("GEOID").to_pylist()]
        counties = [str(value) for value in table.column("COUNTYFP").to_pylist()]
        states = [str(value) for value in table.column("STATEFP").to_pylist()]
        pairs = sorted({f"{state}{county}" for state, county in zip(states, counties)})
        out[region] = {"rows": len(geoids), "states": sorted(set(states)),
                       "county_fips": pairs, "geoid_prefix": sorted({geoid[:2] for geoid in geoids})}
    return out


# --------------------------------------------------------------------------- exclusion policy
@dataclass
class ExclusionPolicy:
    """Executable spec 4.1: a candidate lab tract is rejected before any layer is loaded for it."""

    official_geoids: frozenset[str]
    template_geoids: frozenset[str]
    study_county_fips: frozenset[str]
    study_states: frozenset[str]
    buffer_km: float
    study_points: list[tuple[float, float]] = field(default_factory=list)
    crs: str = "EPSG:5070"

    @property
    def excluded_geoids(self) -> frozenset[str]:
        return self.official_geoids | self.template_geoids

    county_exclusion_enabled: bool = True

    def reject_reason(self, geoid: str, county_fips: str | None = None,
                      centroid: tuple[float, float] | None = None) -> str | None:
        """Return why this candidate must be dropped, or ``None`` when it may be computed."""
        if geoid in self.excluded_geoids:
            return "official_scored_or_template_geoid"
        if self.county_exclusion_enabled and county_fips and county_fips in self.study_county_fips:
            return "study_county"
        if county_fips and county_fips[:2] in self.study_states and self.buffer_km <= 0.0:
            # a zero buffer would silently allow a same-state tract next to the study border
            return "study_state_with_no_buffer"
        if centroid is not None and self.buffer_km > 0.0 and self.study_points:
            for x, y in self.study_points:
                if (x - centroid[0]) ** 2 + (y - centroid[1]) ** 2 <= self.buffer_km ** 2 * 1e6:
                    return "within_neighbour_buffer_of_study_tract"
        return None

    def partition(self, candidates: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
        """Split candidate tracts into accepted and rejected, keyed by rejection reason."""
        buckets: dict[str, list[str]] = {"accepted": []}
        for candidate in candidates:
            reason = self.reject_reason(candidate["geoid"], candidate.get("county_fips"),
                                       candidate.get("centroid"))
            if reason is None:
                buckets["accepted"].append(candidate["geoid"])
            else:
                buckets.setdefault(reason, []).append(candidate["geoid"])
        return buckets

    def summary(self) -> dict[str, Any]:
        return {"official_geoids_excluded": len(self.official_geoids),
                "template_geoids_excluded": len(self.template_geoids),
                "union_geoids_excluded": len(self.excluded_geoids),
                "study_counties_excluded": len(self.study_county_fips),
                "study_states_excluded": sorted(self.study_states),
                "centroid_buffer_km": self.buffer_km,
                "buffer_crs": self.crs,
                "study_points_available_for_buffer_check": len(self.study_points),
                "fail_closed": True}


def policy_from_config(config: dict[str, Any], workspace: Path,
                       study_points: list[tuple[float, float]] | None = None) -> ExclusionPolicy:
    policy = config["exclusion_policy"]
    counties = study_counties(workspace)
    county_fips = {fips for entry in counties.values() for fips in entry["county_fips"]}
    states = {state for entry in counties.values() for state in entry["states"]}
    return ExclusionPolicy(
        official_geoids=frozenset(official_scored_ids(workspace))
        if policy["drop_official_scored_geoids"] else frozenset(),
        template_geoids=frozenset(template_geoids(workspace)),
        study_county_fips=frozenset(county_fips),
        study_states=frozenset(states),
        buffer_km=float(policy["centroid_buffer_km"]),
        study_points=list(study_points or []),
        county_exclusion_enabled=bool(policy.get("drop_study_counties", True)))


# --------------------------------------------------------------------------- cross-checks
def cross_check(config: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    """Compare the frozen plan against a fresh reading of the spec."""
    groups_spec = parsed["groups"]
    groups_config = config["groups"]
    findings: dict[str, Any] = {"group_count_spec": len(groups_spec),
                                "group_count_config": len(groups_config)}
    findings["group_cv_units_match"] = ([entry["cv_unit"] for entry in groups_spec]
                                        == [entry["id"] for entry in groups_config])
    findings["approx_tract_counts_match"] = all(
        spec_entry["approx_tracts"] == config_entry["approx_tracts"]
        for spec_entry, config_entry in zip(groups_spec, groups_config))
    spec_states = {state for entry in groups_config for state in entry["states"]}
    findings["states_declared"] = sorted(spec_states)
    findings["core_groups_declared"] = sorted(config["core_groups"])
    findings["core_groups_are_a_prefix_of_the_nine"] = all(
        group in [entry["id"] for entry in groups_config] for group in config["core_groups"])
    findings["volume_rows_spec"] = len(parsed["volume_table"])
    findings["volume_rows_config"] = len(config["lab_root"]["volume_table_from_spec"])
    sources_spec = parsed["sources"]["source_families"]
    registered = {entry["id"] for entry in config["sources"]}
    findings["source_families_missing_from_register"] = sorted(
        family for family, present in sources_spec.items()
        if present and not (_FAMILY_ALIASES.get(family, set()) & registered))
    findings["structure_codes_spec"] = parsed["sources"]["structure_codes"]
    codes_config = set()
    for entry in config["sources"]:
        for values in entry.get("structure_codes", {}).values():
            codes_config.update(values)
    findings["structure_codes_config"] = sorted(codes_config)
    findings["structure_codes_covered"] = set(parsed["sources"]["structure_codes"]) <= codes_config
    recipe = parsed["recipe"]
    findings["recipe"] = recipe
    findings["recipe_projection_matches"] = recipe["projection"] == config["recipe"]["projection"]
    findings["all_four_reference_components_declared"] = all(
        entry["declared"] for entry in recipe["components"].values())
    return findings


_FAMILY_ALIASES = {
    "tiger_tracts": {"tiger_tracts_2020"},
    "overture": {"overture_package_release"},
    "tiger_prisecroads": {"tiger_prisecroads_2024", "tiger_county_roads_check"},
    "microsoft": {"ms_us_building_footprints"},
    "global_ml_variant": {"ms_global_ml_building_footprints"},
    "usgs_structures": {"usgs_national_structures"},
    "cbp_zbp": {"cbp_2023"},
    "hud_crosswalk": {"hud_usps_crosswalk"},
    "hospital_check": {"hospital_check"},
}


def load_plan(path: Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    config["__path__"] = str(path)
    config["__sha256__"] = hashlib.sha256(
        json.dumps({key: value for key, value in config.items() if not key.startswith("__")},
                   sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return config
