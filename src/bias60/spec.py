"""Parse the 60-idea research memo into machine-readable idea records.

The registry ``config/ideas.json`` is derived from this parse rather than hand-written, so the
set of ids, families, components, costs and inputs is traceable to the spec the operator froze
in ``$ORCHESTRATOR_ROOT/docs/research/bias-60-ideas-2026-10.md``.

Only section 2 ("The 60 ideas") is parsed. Every classification rule used here is deterministic,
documented in the registry under ``derivation``, and covered by tests.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

SECTION_START = "## 2. The 60 ideas"
SECTION_END = "### Orthogonality map"
COMPONENTS = ("R", "B", "F", "Est", "C")
COST_ORDER = ("cheap", "medium", "heavy")
IDEA_PATTERN = re.compile(r"\n\*\*([A-Za-z]{1,3}\d+)\.\s*([^*]+)\*\*\n(.*?)(?=\n\*\*[A-Za-z]{1,3}\d+\.|\n### |\Z)",
                          re.DOTALL)
FAMILY_PATTERN = re.compile(r"### Family ([A-Za-z]+) —([^\n]*)")
BULLET_PATTERN = re.compile(r"\n-\s*\*([^*]+):\*\s*(.*?)(?=\n-\s*\*|\Z)", re.DOTALL)

# A gap-scale idea states an explicit clipped-ratio quantity; everything else is a covariate that
# only reaches gap scale through the lab fit or a pre-registered monotone link (spec section 3.1).
GAP_MARKERS = ("ĝ", "gap proxy", "gap variant", "gap_k", "est gap", "expected gap",
               "gap score", "post-reference-epoch fire loss", "deficit =")
COVARIATE_MARKERS = ("covariate", "share", "index", "slope", "mean", "posterior", "saturation")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _field(bullets: dict[str, str], name: str) -> str:
    return " ".join(bullets.get(name, "").split())


def _components(text: str) -> tuple[str, list[str]]:
    """Return (primary, all) components named in the spec's *Component:* line, in spec order."""
    if text.strip().lower().startswith("all"):
        # The spec writes "*Component:* all." for estimators that wrap or apply to every component
        # (S1, S2, ...). They are recorded as "all" rather than being silently assigned one.
        return "all", list(COMPONENTS)
    tokens: list[str] = []
    for match in re.finditer(r"\b(R|B|F|Est|C)\b", text):
        token = match.group(1)
        if token not in tokens:
            tokens.append(token)
    if not tokens:
        raise ValueError(f"no component token found in spec component line: {text!r}")
    return tokens[0], tokens


def _cost(text: str) -> str:
    lowered = text.split("*Cite:")[0].lower()
    found = [cost for cost in COST_ORDER if cost in lowered]
    if not found:
        raise ValueError(f"no cost token found in spec cost line: {text!r}")
    # "cheap to medium" is budgeted as the conservative (heaviest) declared cost.
    return COST_ORDER[max(COST_ORDER.index(cost) for cost in found)]


def _inputs(text: str) -> list[str]:
    parts = [part.strip().strip(".") for part in text.split(",")]
    return [part for part in parts if part]


def _kind(recipe: str, component_line: str) -> tuple[str, str]:
    lowered = (recipe + " " + component_line).lower()
    for marker in GAP_MARKERS:
        if marker in lowered:
            return "gap", marker
    for marker in COVARIATE_MARKERS:
        if marker in lowered:
            return "covariate", marker
    return "covariate", "default: no gap marker in the per-tract recipe"


def parse_spec(spec_path: Path) -> dict[str, Any]:
    """Parse the memo into one record per idea plus the family table."""
    if not spec_path.is_file():
        raise FileNotFoundError(f"60-idea spec missing: {spec_path}")
    text = spec_path.read_text(encoding="utf-8")
    if SECTION_START not in text or SECTION_END not in text:
        raise ValueError(f"spec section boundaries not found in {spec_path}")
    body = text[text.index(SECTION_START):text.index(SECTION_END)]

    families: dict[str, str] = {}
    for match in FAMILY_PATTERN.finditer(body):
        families[match.group(1)] = " ".join(match.group(2).split()).lstrip("—").strip()

    ideas: list[dict[str, Any]] = []
    for match in IDEA_PATTERN.finditer(body):
        idea_id, title, block = match.group(1), " ".join(match.group(2).split()), match.group(3)
        # The first bullet has no leading newline inside the captured block, so restore one.
        bullets = {name.strip().lower(): value
                   for name, value in BULLET_PATTERN.findall("\n" + block)}
        component_line = _field(bullets, "component")
        primary, all_components = _components(component_line)
        recipe_scope = next((name for name in bullets if name.startswith("per ")), None)
        if recipe_scope is None:
            raise ValueError(f"idea {idea_id} declares no per-tract recipe bullet")
        recipe = _field(bullets, recipe_scope)
        kind, kind_evidence = _kind(recipe, component_line)
        inputs = _inputs(_field(bullets, "inputs"))
        if not inputs:
            raise ValueError(f"idea {idea_id} declares no inputs")
        ideas.append({
            "id": idea_id,
            "family": re.match(r"[A-Za-z]+", idea_id).group(0),
            "title": title.rstrip("."),
            "component": primary,
            "components": all_components,
            "kind": kind,
            "kind_evidence": kind_evidence,
            "inputs": inputs,
            "cost": _cost(_field(bullets, "cost")),
            "signal": _field(bullets, "signal"),
            "recipe": recipe,
            "recipe_scope": recipe_scope,
            "orthogonal_because": _field(bullets, "orthogonal because"),
            "failure_mode": _field(bullets, "failure mode"),
            "status": "declared",
            "implementation": None,
        })

    if len(ideas) != 60:
        raise ValueError(f"expected 60 ideas in the spec, parsed {len(ideas)}")
    ids = [idea["id"] for idea in ideas]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate idea ids in the spec parse")
    for idea in ideas:
        if idea["family"] not in families:
            raise ValueError(f"idea {idea['id']} has family {idea['family']} absent from the family table")
    return {
        "spec": {
            "path": str(spec_path),
            "sha256": sha256_file(spec_path),
            "idea_count": len(ideas),
            "family_count": len(families),
        },
        "families": [{"family": name, "label": label,
                      "ideas": sorted((i["id"] for i in ideas if i["family"] == name),
                                      key=lambda value: (len(value), value))}
                     for name, label in families.items()],
        "ideas": ideas,
    }
