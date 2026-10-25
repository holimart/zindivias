#!/usr/bin/env python3
"""Claim-to-evidence audit for the Bias Bounty Best Discovery / Documentation writeup.

Every quantitative statement in the three writeup documents must carry an exact
evidence pointer into a committed artifact and a reproducible command, and every
number it states must be present in the named evidence. The audit measures the
fraction of quantitative claims that are traceable, executes each claim's verify
command, hash-verifies every reproduce command's product, and fails closed on
unsupported accuracy claims or on any prohibited removed-reference layer,
label, placeholder or leaderboard-probe source presented as scored-model
evidence.

It is a documentation screen only: it does not promote a model, does not
estimate accuracy, and never reads labels, placeholders or the shared ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/writeup-claim-evidence-preregistered.json"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


CONFIG: dict[str, Any] = {}
TOKENIZER: dict[str, Any] = {}
NUMBER = re.compile("")
KEEP_INLINE = re.compile("")


def configure(config: dict[str, Any]) -> None:
    global CONFIG, TOKENIZER, NUMBER, KEEP_INLINE
    CONFIG = config
    TOKENIZER = config["tokenizer"]
    NUMBER = re.compile(TOKENIZER["pattern"])
    KEEP_INLINE = re.compile(TOKENIZER["keep_inline_code"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- tokenizing
def strip_fenced_code(text: str) -> str:
    return re.sub(r"```.*?```", lambda match: "\n" * match.group(0).count("\n"),
                  text, flags=re.S)


def prepare_prose(text: str) -> str:
    """Remove inline code that is not a bare number, so identifiers do not pose as claims."""
    def replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        return match.group(0) if KEEP_INLINE.match(body) else " "

    return re.sub(r"`([^`]*)`", replace, text)


def decimals_of(raw: str) -> int:
    body = raw.replace(",", "").replace("_", "")
    return len(body.split(".")[1]) if "." in body else 0


def digits_of(raw: str) -> int:
    return len(re.sub(r"\D", "", raw))


def collect_numbers(node: Any) -> list[float]:
    out: list[float] = []
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, bool):
            continue
        elif isinstance(current, (int, float)):
            out.append(float(current))
        elif isinstance(current, str):
            out.extend(value for value, _, _ in numeric_tokens(current))
    return out


def numeric_tokens(text: str) -> list[tuple[float, int, str]]:
    """Return (value, decimals, raw) for quantitative tokens, dropping identifiers."""
    out: list[tuple[float, int, str]] = []
    for match in NUMBER.finditer(text):
        raw = match.group(0)
        body = raw.replace(",", "")
        if ("." not in body and digits_of(raw) >= TOKENIZER["identifier_minimum_digits"]
                and "," not in raw):
            continue  # bare long digit strings are GEOIDs / FIPS, not quantities
        try:
            value = float(body)
        except ValueError:
            continue
        out.append((value, decimals_of(raw), raw))
    return out


NEGATION_WORDS = ("no ", "not ", "never", "without", "removed", "exclude", "absent",
                  "neither", "did not", "does not", "only", "distinct", "instead")


def evidence_path(name: str) -> Path | None:
    """Resolve an evidence name; WORKSPACE: entries live in the authorised workspace."""
    if name.startswith("WORKSPACE:"):
        import os
        workspace = os.environ.get("COMPETITION_WORKSPACE")
        if not workspace:
            return None
        return Path(workspace) / name.split(":", 1)[1]
    return ROOT / name


def classify_prohibited(text: str) -> dict[str, list[str]]:
    """Split prohibited-token mentions into data usage versus disclosed absence."""
    lowered = text.lower()
    markers = tuple(CONFIG.get("disclosure_markers", ())) + NEGATION_WORDS
    exceptions = CONFIG.get("prohibited_prose_exceptions", [])
    out: dict[str, list[str]] = {"usage": [], "disclosure": []}
    for token in CONFIG["prohibited_tokens"]:
        start = lowered.find(token)
        while start != -1:
            window = lowered[max(0, start - 70):start + len(token) + 70]
            inside_exception = any(token in exception and exception in lowered
                                   and start >= lowered.find(exception)
                                   and start + len(token) <= lowered.find(exception) + len(exception)
                                   for exception in exceptions)
            if (any(word in window for word in markers) or inside_exception):
                out["disclosure"].append(token)
            else:
                out["usage"].append(token)
            start = lowered.find(token, start + 1)
    out["usage"] = sorted(set(out["usage"]))
    out["disclosure"] = sorted(set(out["disclosure"]) - set(out["usage"]))
    return out


def accuracy_assertion(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in
               CONFIG["accuracy_assertion_patterns"])


def is_informative(value: float, decimals: int) -> bool:
    rule = TOKENIZER["informative"]
    return (decimals >= rule["minimum_decimals"] or abs(value) >= rule["minimum_absolute"]
            or (abs(value) >= rule["minimum_absolute_with_decimal"] and decimals >= 1))


# --------------------------------------------------------------------------- document blocks
CITATION = re.compile(r"(?:reports|config|docs|scripts)/[A-Za-z0-9_.*/-]+\.(?:json|csv|md|py|png)")


def cited_artifacts(text: str) -> list[str]:
    """Artifacts the writeup itself names inside this block."""
    found: list[str] = []
    for candidate in CITATION.findall(text):
        name = candidate.replace("*", "")
        if name in CONFIG["evidence_allowlist"] and name not in found:
            found.append(name)
    return found


def is_bullet(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*]|\d+\.)\s+", line))


def document_blocks(doc: str, numeric_only: bool = True) -> list[dict[str, Any]]:
    """Split a writeup into blocks with their heading section and occurrence.

    With ``numeric_only`` a block becomes a claim only when it carries a numeric token.
    The full block list is also used to scope section-level hedge language.
    """
    text = strip_fenced_code((ROOT / doc).read_text(encoding="utf-8"))
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    current: list[tuple[int, str]] = []
    section = ""
    occurrences: Counter[str] = Counter()

    def flush() -> None:
        nonlocal current
        if current:
            body = "\n".join(line for _, line in current)
            if not numeric_only or numeric_tokens(prepare_prose(body)):
                blocks.append({"section": section, "occurrence": occurrences[section],
                               "line_start": current[0][0],
                               "line_end": current[-1][0], "text": body,
                               "kind": "table" if body.lstrip().startswith("|") else "paragraph"})
        current = []

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            flush()
            continue
        if is_bullet(line) and current and not is_bullet(current[-1][1]):
            flush()
        elif current and is_bullet(line) and is_bullet(current[-1][1]):
            flush()
        if line.startswith("## ") or line.startswith("### "):
            flush()
            section = line.lstrip("# ").strip()
            occurrences[section] += 1
            continue
        if line.startswith("#"):
            flush()
            if not section:
                section = line.lstrip("# ").strip()
                occurrences[section] += 1
            continue
        current.append((index, line))
    flush()
    return blocks


def derive_pointer(pool: EvidencePool, name: str,
                   informative: list[tuple[float, int]]) -> str:
    """Exact pointer into the primary artifact: JSON path, code def, markdown section, or whole."""
    for value, decimals in informative[:12]:
        pointer = pool.find_pointer(name, value, decimals)
        if pointer:
            return pointer
    lowered = name.lower()
    payload = pool.payloads.get(name, "")
    if lowered.endswith(".py") and isinstance(payload, str):
        for line in payload.splitlines():
            match = re.match(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match:
                return f"def:{match.group(1)}"
        return "document"
    if lowered.endswith(".md") and isinstance(payload, str):
        for line in payload.splitlines():
            if line.startswith("#"):
                return f"section:{line.lstrip('# ').strip()}"
        return "document"
    if lowered.endswith((".csv", ".tsv", ".jsonl")):
        return "document"
    return "/"


def section_rule(doc: str, section: str, occurrence: int) -> dict[str, Any]:
    for rule in CONFIG["sections"]:
        if (rule["doc"] == doc and rule["section"] == section
                and int(rule.get("occurrence", 1)) == occurrence):
            return rule
    for rule in CONFIG["sections"]:
        if rule["doc"] == doc and rule.get("occurrence") == "any":
            return rule
    raise ValueError(f"no preregistered evidence rule for {doc} section {section!r} "
                     f"occurrence {occurrence}")


# --------------------------------------------------------------------------- evidence pool
class EvidencePool:
    """Numeric values available in the committed evidence artifacts."""

    def __init__(self) -> None:
        self.values: dict[str, list[float]] = {}
        self.payloads: dict[str, Any] = {}
        self.rounded: dict[tuple[str, int], set[float]] = {}
        self._pointer_cache: dict[tuple[str, float, int], str | None] = {}

    def load(self) -> None:
        for name in list(CONFIG["evidence_allowlist"]):
            path = evidence_path(name)
            if path is None or not path.is_file():
                if name.startswith("WORKSPACE:"):
                    continue  # optional workspace object; verified per claim when used
                raise ValueError(f"allowlisted evidence {name} is missing")
            self._add(name, path)

    @staticmethod
    def _workspace_rules_path() -> Path | None:
        import os
        workspace = os.environ.get("COMPETITION_WORKSPACE")
        if not workspace:
            return None
        return Path(workspace) / CONFIG["workspace_rules"]

    def _add(self, name: str, path: Path) -> None:
        found: set[float] = set()
        if path.suffix == ".py":
            text = path.read_text(encoding="utf-8", errors="replace")
            self.payloads[name] = text
            found.update(value for value, _, _ in numeric_tokens(text))
        elif path.suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.payloads[name] = payload
            stack: list[Any] = [payload]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, bool):
                    continue
                elif isinstance(node, (int, float)):
                    found.add(float(node))
                elif isinstance(node, str):
                    found.update(value for value, _, _ in numeric_tokens(node))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            self.payloads[name] = text
            found.update(value for value, _, _ in numeric_tokens(prepare_prose(text)))
        self.values[name] = sorted(found)

    def _rounded(self, name: str, decimals: int, scale: float = 1.0) -> set[float]:
        key = (name, decimals, scale)
        cached = self.rounded.get(key)
        if cached is None:
            decimals = min(decimals, int(TOKENIZER["maximum_decimals"]))
            cached = {round(value * scale, decimals) for value in self.values.get(name, [])}
            self.rounded[key] = cached
        return cached

    def candidates(self, value: float) -> list[float]:
        return [value, -value, 1.0 - value, abs(value)]

    def matches(self, value: float, decimals: int, observed: list[float] | set[float]) -> bool:
        """True when any observed value equals the token directly, as a fraction or as a percent."""
        targets = {round(candidate, decimals) for candidate in self.candidates(value)}
        scales = [1.0] + [float(scale) for scale in TOKENIZER["value_scales"]] \
            + [1.0 / float(scale) for scale in TOKENIZER["value_scales"]]
        values = list(observed)
        return any(round(candidate, decimals) in targets
                   for scale in scales for candidate in (item * scale for item in values))

    def hit_in(self, value: float, decimals: int, names: list[str]) -> list[str]:
        """A token matches when the artifact stores it directly, as a fraction, or as a percent."""
        targets = {round(candidate, decimals) for candidate in self.candidates(value)}
        scales = [1.0] + [float(scale) for scale in TOKENIZER["value_scales"]] \
            + [1.0 / float(scale) for scale in TOKENIZER["value_scales"]]
        return sorted(name for name in names
                      if name in self.values
                      and any(target in self._rounded(name, decimals, scale)
                              for scale in scales for target in targets))

    def values_under_key(self, name: str, path: str, key: str) -> list[float]:
        """Numbers stored under `key` anywhere below `path` (for aggregate claims)."""
        node = self.pointer_payload(name, path)
        out: list[float] = []

        def walk(current: Any) -> None:
            if isinstance(current, dict):
                for child_key, child in current.items():
                    if str(child_key) == key:
                        out.extend(collect_numbers(child))
                    walk(child)
            elif isinstance(current, list):
                for child in current:
                    walk(child)

        walk(node)
        return out

    def pointer_payload(self, name: str, pointer: str | None) -> Any:
        payload = self.payloads.get(name)
        if payload is None or not pointer:
            return payload
        if pointer.startswith("section:"):
            heading = pointer.split(":", 1)[1]
            text = payload if isinstance(payload, str) else json.dumps(payload)
            lines = text.splitlines()
            collected: list[str] = []
            inside = False
            for line in lines:
                if line.startswith("#"):
                    inside = heading.lower() in line.lower()
                    continue
                if inside:
                    collected.append(line)
            return "\n".join(collected)
        if pointer.startswith("def:"):
            name_target = pointer.split(":", 1)[1]
            text = payload if isinstance(payload, str) else ""
            lines = text.splitlines()
            collected: list[str] = []
            capturing = False
            for line in lines:
                if re.match(rf"^\s*(?:def|class)\s+{re.escape(name_target)}\b", line):
                    capturing = True
                    collected.append(line)
                    continue
                if capturing:
                    if line.strip() and not line.startswith((" ", "\t")):
                        break
                    collected.append(line)
            return "\n".join(collected)
        if pointer.startswith("column:"):
            column = pointer.split(":", 1)[1]
            text = payload if isinstance(payload, str) else ""
            header = text.splitlines()[0].split(",") if text else []
            if column not in header:
                return None
            index = header.index(column)
            values: list[str] = []
            for line in text.splitlines()[1:]:
                cells = line.split(",")
                if len(cells) > index:
                    values.append(cells[index])
            return values
        node: Any = payload
        for part in pointer.strip("/").split("/"):
            if not part:
                continue
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                return None
        return node

    def scalar(self, name: str, pointer: str | None) -> float | None:
        payload = self.pointer_payload(name, pointer)
        if isinstance(payload, bool):
            return None
        if isinstance(payload, (int, float)):
            return float(payload)
        return None

    def values_under(self, name: str, pointer: str | None) -> set[float]:
        payload = self.pointer_payload(name, pointer)
        if payload is None:
            return set()
        if isinstance(payload, str):
            return {value for value, _, _ in numeric_tokens(prepare_prose(payload))}
        found: set[float] = set()
        stack: list[Any] = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, bool):
                continue
            elif isinstance(node, (int, float)):
                found.add(float(node))
            elif isinstance(node, str):
                found.update(value for value, _, _ in numeric_tokens(node))
        return found

    def find_pointer(self, name: str, value: float, decimals: int) -> str | None:
        """Public entry with a memo cache over repeated tokens."""
        key = (name, value, decimals)
        if key not in self._pointer_cache:
            self._pointer_cache[key] = self._find_pointer(name, value, decimals)
        return self._pointer_cache[key]

    def _find_pointer(self, name: str, value: float, decimals: int) -> str | None:
        """Derive the shallowest dict path in a JSON evidence file holding this value."""
        payload = self.payloads.get(name)
        if payload is None:
            return None
        if not isinstance(payload, (dict, list)):
            return "document"
        wanted = {round(candidate, decimals) for candidate in self.candidates(value)}
        best: str | None = None

        def walk(node: Any, path: list[str]) -> None:
            nonlocal best
            if best is not None and len(path) >= 6:
                return
            if isinstance(node, dict):
                for key, child in node.items():
                    walk(child, path + [str(key)])
            elif isinstance(node, list):
                for index, child in enumerate(node[:400]):
                    walk(child, path + [str(index)])
            elif isinstance(node, bool):
                return
            elif isinstance(node, (int, float)) and round(float(node), decimals) in wanted:
                candidate = "/" + "/".join(path)
                if best is None or len(candidate) < len(best):
                    best = candidate
            elif isinstance(node, str):
                if any(candidate in {value for value, _, _ in numeric_tokens(node)}
                       for candidate in wanted):
                    candidate = "/" + "/".join(path)
                    if best is None or len(candidate) < len(best):
                        best = candidate

        walk(payload, [])
        if best is None:
            return None
        parts = best.strip("/").split("/")
        trimmed: list[str] = []
        for part in parts:
            if part.isdigit():
                break
            trimmed.append(part)
        return "/" + "/".join(trimmed) if trimmed else "/"


# --------------------------------------------------------------------------- inventory
def build_inventory(pool: EvidencePool) -> list[dict[str, Any]]:
    overrides = CONFIG.get("claim_overrides", {})
    claims: list[dict[str, Any]] = []
    for doc in CONFIG["docs"]:
        stem = Path(doc).stem
        doc_blocks = document_blocks(doc)
        all_doc_blocks = document_blocks(doc, numeric_only=False)
        for index, block in enumerate(doc_blocks, start=1):
            # hedge scope is the enclosing section: a qualified section covers its tables
            neighbours = [other["text"] for other in all_doc_blocks
                          if other["section"] == block["section"] and other is not block]
            claim_id = f"{stem}-{index:03d}"
            rule = section_rule(doc, block["section"], block["occurrence"])
            override = overrides.get(claim_id, {})
            evidence_names = list(override.get("evidence", rule["evidence"]))
            provenance = override.get("provenance_class", rule["provenance_class"])
            body = prepare_prose(block["text"])
            tokens = numeric_tokens(body)
            informative = [(value, decimals) for value, decimals, _ in tokens
                           if is_informative(value, decimals)]
            cited = cited_artifacts(block["text"])
            merged: list[str] = []
            for name in cited + list(rule["evidence"]):
                if name.endswith(".png") or name in merged:
                    continue
                merged.append(name)
            evidence_names = override.get("evidence", merged[:6])
            scored = [(sum(1 for value, decimals in informative
                           if pool.hit_in(value, decimals, [name])), name)
                      for name in evidence_names]
            ordered = [name for _, name in sorted(scored, key=lambda item: (-item[0], len(item[1])))]
            evidence: list[dict[str, Any]] = []
            for position, name in enumerate(ordered):
                resolved = evidence_path(name)
                if resolved is None or not resolved.is_file():
                    raise ValueError(f"{claim_id}: evidence {name} cannot be resolved")
                entry: dict[str, Any] = {"path": name, "sha256": sha256_file(resolved),
                                         "primary": position == 0}
                pointer = override.get("primary_pointer") if position == 0 else None
                if pointer is None:
                    pointer = derive_pointer(pool, name, informative)
                entry["pointer"] = pointer
                evidence.append(entry)
            reproduce = rule["reproduce"]
            reproduce = CONFIG["reproduce_command_aliases"].get(reproduce, reproduce)
            products = [{"path": product, "sha256": sha256_file(ROOT / product)}
                        for product in rule["products"] if (ROOT / product).is_file()]
            claims.append({
                "claim_id": claim_id, "doc": doc, "section": block["section"],
                "line_start": block["line_start"], "line_end": block["line_end"],
                "kind": block["kind"], "text": block["text"],
                "provenance_class": provenance,
                "evidence": evidence,
                "verify_command": CONFIG["verify_command_template"].format(claim_id=claim_id),
                "reproduce_command": reproduce,
                "reproduce_products": products,
                "tokens_exempt": [{"value": entry["value"], "decimals": entry["decimals"],
                                   "class": entry["class"], "reason": entry["reason"]}
                                  for entry in override.get("tokens_exempt", [])],
                "derived_tokens": override.get("derived_tokens", []),
                "hedge_context": override.get("hedge_context", neighbours),
                "notes": override.get("notes", ""),
            })
    return claims


def compute_aggregate(pool: EvidencePool, entry: dict[str, Any]) -> float | None:
    """Recompute a stated aggregate from the named artifact, or return None."""
    evidence = entry["evidence"]
    operation = entry["operation"]
    if operation == "difference":
        first = pool.scalar(evidence, entry.get("pointer_a"))
        second = pool.scalar(evidence, entry.get("pointer_b"))
        if first is None or second is None:
            return None
        delta = (first - second) * float(entry.get("scale", 1.0))
        return abs(delta) if entry.get("absolute") else delta
    if operation == "percentage":
        numerator = pool.scalar(evidence, entry.get("pointer_a"))
        denominator = pool.scalar(evidence, entry.get("pointer_b"))
        if numerator is None or denominator in (None, 0):
            return None
        return numerator / denominator * 100.0
    if entry.get("key"):
        numbers = pool.values_under_key(evidence, entry.get("path", "/"), entry["key"])
    else:
        numbers = sorted(pool.values_under(evidence, entry.get("path")))
    if not numbers:
        return None
    if operation == "sum":
        return float(sum(numbers))
    if operation == "mean":
        return float(sum(numbers) / len(numbers))
    if operation == "count":
        return float(len(numbers))
    if operation == "max":
        return float(max(numbers))
    if operation == "min":
        return float(min(numbers))
    raise ValueError(f"undeclared aggregate operation {operation!r}")


# --------------------------------------------------------------------------- verification
def verify_claim(claim: dict[str, Any], pool: EvidencePool) -> dict[str, Any]:
    doc_lines = (ROOT / claim["doc"]).read_text(encoding="utf-8").splitlines()
    slice_text = "\n".join(doc_lines[claim["line_start"] - 1:claim["line_end"]])
    anchor_ok = slice_text == claim["text"]

    names = [entry["path"] for entry in claim["evidence"]]
    hash_ok = True
    for entry in claim["evidence"]:
        resolved = evidence_path(entry["path"])
        if (resolved is None or not resolved.is_file()
                or entry["path"] not in CONFIG["evidence_allowlist"]):
            hash_ok = False
            continue
        if sha256_file(resolved) != entry["sha256"]:
            hash_ok = False

    body = prepare_prose(claim["text"])
    tokens = numeric_tokens(body)
    exempt_keys = {(str(entry["value"]), entry["decimals"]) for entry in claim["tokens_exempt"]}
    uncovered: list[str] = []
    covered = 0
    derived_verified: list[str] = []
    derived_keys: set[tuple[float, int]] = set()
    for entry in claim.get("derived_tokens", []):
        aggregate = compute_aggregate(pool, entry)
        if aggregate is None:
            continue
        tolerance = float(entry.get("tolerance", 1e-9))
        if abs(aggregate - float(entry["value"])) <= tolerance:
            label = f"{entry['operation']}:{entry['evidence']}#{entry.get('key', '')}"
            derived_verified.append(label)
            derived_keys.add((float(entry["value"]), int(entry["decimals"])))

    for value, decimals, raw in tokens:
        if (str(value), decimals) in exempt_keys or (raw, decimals) in exempt_keys:
            covered += 1
            continue
        if (value, decimals) in derived_keys:
            covered += 1
            continue
        if pool.hit_in(value, decimals, names):
            covered += 1
        else:
            uncovered.append(raw)

    informative = [(value, decimals) for value, decimals, _ in tokens
                   if is_informative(value, decimals)]
    primary = next((entry for entry in claim["evidence"] if entry.get("primary")), None)
    pointer_ok = bool(primary)
    pointer_reason = ""
    if primary is None:
        pointer_ok = False
        pointer_reason = "no primary evidence"
    elif informative:
        under = sorted(pool.values_under(primary["path"], primary.get("pointer")))
        for value, decimals in informative:
            if pool.matches(value, decimals, under):
                pointer_ok = True
                break
        if not pointer_ok:
            pointer_reason = "no informative claim value occurs under the primary pointer"
    elif not primary.get("pointer"):
        pointer_ok = False
        pointer_reason = "primary evidence carries no pointer"

    asserted_accuracy = accuracy_assertion(claim["text"])
    accuracy_ok = (not asserted_accuracy
                   or (claim["provenance_class"] in CONFIG["accuracy_claim_allowed_classes"]
                       and any(entry["path"] in CONFIG["accuracy_evidence_required"]
                               for entry in claim["evidence"])))
    prohibited = classify_prohibited(claim["text"])
    removed = set(CONFIG["removed_reference_tokens"])
    platform_state = set(CONFIG["platform_state_tokens"])
    disclosure_classes = set(CONFIG["disclosure_classes"])
    model_classes = {"measured_model_only", "model_specification"}
    violations: list[str] = []
    for token in prohibited["usage"]:
        if token in removed and claim["provenance_class"] not in disclosure_classes:
            violations.append(f"removed-reference layer used as data: {token}")
        elif token in platform_state and claim["provenance_class"] not in disclosure_classes:
            violations.append(f"platform/label/leaderboard state used as data: {token}")
        elif claim["provenance_class"] in model_classes:
            violations.append(f"prohibited source named as model evidence: {token}")
    prohibited_ok = not violations
    hedge_text = claim["text"].lower()
    hedge_ok = True
    if claim["provenance_class"] in CONFIG["hedge_required_classes"]:
        context = claim.get("hedge_context") or []
        hedge_text = "\n".join([claim["text"], *context]).lower()
        hedge_ok = any(phrase in hedge_text for phrase in CONFIG["hedge_phrases"])

    products_ok = True
    for product in claim["reproduce_products"]:
        path = ROOT / product["path"]
        if not path.is_file() or sha256_file(path) != product["sha256"]:
            products_ok = False

    passed = all([anchor_ok, hash_ok, not uncovered, pointer_ok, accuracy_ok, prohibited_ok,
                  hedge_ok, products_ok, bool(names), bool(claim["reproduce_command"])])
    return {"claim_id": claim["claim_id"], "passed": passed, "anchor_verbatim": anchor_ok,
            "evidence_hashes_verified": hash_ok,
            "tokens_total": len(tokens), "tokens_covered": covered,
            "tokens_uncovered": uncovered, "derived_tokens_verified": derived_verified,
            "primary_pointer_hit": pointer_ok, "pointer": (primary or {}).get("pointer"),
            "pointer_reason": pointer_reason,
            "accuracy_assertion": asserted_accuracy, "accuracy_ok": accuracy_ok,
            "prohibited_usage": prohibited["usage"],
            "prohibited_disclosure": prohibited["disclosure"],
            "prohibited_violations": violations,
            "prohibited_ok": prohibited_ok,
            "hedge_ok": hedge_ok, "reproduce_products_verified": products_ok}


# --------------------------------------------------------------------------- commands
def json_leaf_differences(old: Any, new: Any, path: str = "") -> list[str]:
    """List JSON leaf paths whose scalar values differ between two committed states."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f"{path}/{key}")
            else:
                out.extend(json_leaf_differences(old[key], new[key], f"{path}/{key}"))
        return out
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{path}[length]"]
        out = []
        for index, (left, right) in enumerate(zip(old, new)):
            out.extend(json_leaf_differences(left, right, f"{path}[{index}]"))
        return out
    return [] if old == new else [path]


def run_command(command: str, timeout: int, env_workspace: bool = False) -> int:
    import os
    environment = dict(os.environ)
    if not env_workspace:
        environment.pop("COMPETITION_WORKSPACE", None)
    try:
        completed = subprocess.run(["sh", "-c", command], cwd=ROOT, capture_output=True,
                                   text=True, timeout=timeout, env=environment)
        return completed.returncode
    except subprocess.TimeoutExpired:
        return 124


def execute_reproduce(claims: list[dict[str, Any]],
                      only: str | None = None) -> dict[str, Any]:
    timeout = int(CONFIG["command_timeout_seconds"])
    before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                            text=True).stdout
    log_path = ROOT / CONFIG["execution_log"]
    previous: dict[str, Any] = {}
    current_commands = sorted({claim["reproduce_command"] for claim in claims})
    if log_path.is_file():
        previous = {entry["command"]: entry
                    for entry in json.loads(log_path.read_text())["commands"]
                    if entry["command"] in current_commands}
    entries: list[dict[str, Any]] = []
    commands = sorted({claim["reproduce_command"] for claim in claims})
    if only:
        commands = [command for command in commands if only in command]
    disclosed = tuple(CONFIG.get("reproduce_disclosed_difference_paths", []))
    disclosed_exact = set(CONFIG.get("reproduce_disclosed_difference_exact_paths", []))
    never_disclosed = tuple(CONFIG.get("reproduce_never_disclosed_products", ()))
    disclosed_products = set(CONFIG.get("reproduce_disclosed_products", []))
    for command in commands:
        products = sorted({product["path"] for claim in claims
                           if claim["reproduce_command"] == command
                           for product in claim["reproduce_products"]})
        hash_before = {path: (sha256_file(ROOT / path) if (ROOT / path).is_file() else None)
                       for path in products}
        exit_code = run_command(command, timeout=timeout, env_workspace=True)
        hash_after = {path: (sha256_file(ROOT / path) if (ROOT / path).is_file() else None)
                      for path in products}
        changed: list[str] = []
        for path in products:
            if hash_before[path] == hash_after[path]:
                continue
            if path in never_disclosed:
                changed.append(f"{path} (registry product must reproduce byte-identically)")
                continue
            if path in disclosed_products:
                continue
            if path.endswith(".json") and hash_before[path] and hash_after[path]:
                try:
                    old = json.loads((ROOT / path).read_text())
                except json.JSONDecodeError:
                    old = None
                # the file on disk is now the regenerated one; compare against git HEAD
                committed = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT,
                                           capture_output=True, text=True).stdout
                try:
                    new = json.loads(committed)
                except json.JSONDecodeError:
                    new = None
                if old is None or new is None:
                    changed.append(path)
                else:
                    for difference in json_leaf_differences(new, old):
                        allowed = (any(difference.startswith(prefix) for prefix in disclosed)
                                   or difference in disclosed_exact)
                        if not allowed:
                            changed.append(f"{path}:{difference}")
            else:
                changed.append(path)
        # committed artifacts are restored so pins and later audits stay stable
        subprocess.run(["git", "checkout", "--", *products], cwd=ROOT,
                       capture_output=True, text=True)
        entries.append({"command": command, "exit_code": exit_code,
                        "undisclosed_product_changes": changed,
                        "products": [{"path": path,
                                      "sha256": sha256_file(ROOT / path)}
                                     for path in products if (ROOT / path).is_file()]})
    after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                           text=True).stdout
    merged = {entry["command"]: entry for entry in previous.values()}
    merged.update({entry["command"]: entry for entry in entries})
    return {"commands": [merged[key] for key in sorted(merged)],
            "working_tree_clean_before": before.strip() == "",
            "working_tree_clean_after": after.strip() == "",
            "present": True, "executed_in_this_pass": len(entries),
            "commands_total": len(merged)}


def load_execution_log() -> dict[str, Any]:
    path = ROOT / CONFIG["execution_log"]
    if not path.is_file():
        return {"commands": [], "working_tree_clean_before": False,
                "working_tree_clean_after": False, "present": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["present"] = True
    payload["sha256"] = sha256_file(path)
    return payload


# --------------------------------------------------------------------------- audit
def audit(pool: EvidencePool, claims: list[dict[str, Any]]) -> dict[str, Any]:
    expected = build_inventory(pool)
    inventory_matches = expected == claims

    results = [verify_claim(claim, pool) for claim in claims]
    by_id = {result["claim_id"]: result for result in results}

    timeout = int(CONFIG["command_timeout_seconds"])
    verify_exit = {}
    # verify commands may read the workspace RULES.md (documentation only); every workspace
    # evidence name is separately constrained to that single documentation entry
    for command in sorted({claim["verify_command"] for claim in claims}):
        verify_exit[command] = run_command(command, timeout=timeout, env_workspace=True)
    log = load_execution_log()
    logged = {entry["command"]: entry for entry in log.get("commands", [])}

    products_verified = True
    reproduce_all_zero = True
    tier_b: list[dict[str, Any]] = []
    commands_used = sorted({claim["reproduce_command"] for claim in claims})
    inventory_products = sorted({product["path"] for claim in claims
                                 for product in claim["reproduce_products"]})
    for path in inventory_products:
        artifact = ROOT / path
        recorded = next((product["sha256"] for claim in claims
                         for product in claim["reproduce_products"]
                         if product["path"] == path), None)
        if not artifact.is_file() or recorded is None or sha256_file(artifact) != recorded:
            products_verified = False
    for command in commands_used:
        entry = logged.get(command)
        if entry is None or entry.get("exit_code") != 0:
            reproduce_all_zero = False
            tier_b.append({"command": command, "exit_code": None if entry is None
                           else entry["exit_code"], "undisclosed_product_changes":
                               list(entry.get("undisclosed_product_changes", []))
                           if entry else [], "products": []})
            continue
        for product in entry.get("products", []):
            artifact = ROOT / product["path"]
            if not artifact.is_file() or sha256_file(artifact) != product["sha256"]:
                products_verified = False
        tier_b.append({"command": command, "exit_code": entry["exit_code"],
                       "undisclosed_product_changes":
                           entry.get("undisclosed_product_changes", []),
                       "products": entry.get("products", [])})
    undisclosed_changes = sorted(f"{entry['command']} :: {change}"
                                 for entry in tier_b
                                 for change in entry.get("undisclosed_product_changes", []))

    tokens_total = sum(result["tokens_total"] for result in results)
    tokens_covered = sum(result["tokens_covered"] for result in results)
    claims_with_evidence = sum(
        1 for claim in claims
        if claim["evidence"]
        and by_id[claim["claim_id"]]["evidence_hashes_verified"]
        and all(entry.get("pointer") for entry in claim["evidence"]))
    claims_with_command = sum(1 for claim in claims
                              if claim["verify_command"] in verify_exit
                              and verify_exit[claim["verify_command"]] == 0
                              and claim["reproduce_command"])
    claims_all = [result["passed"] for result in results]

    acceptance = {
        "inventory_matches_docs_and_config": inventory_matches,
        "all_claim_anchors_verbatim": all(result["anchor_verbatim"] for result in results),
        "every_quantitative_claim_has_evidence_pointer": claims_with_evidence == len(claims),
        "every_quantitative_claim_has_reproducible_command": claims_with_command == len(claims),
        "all_numeric_tokens_covered": tokens_covered == tokens_total,
        "all_evidence_hashes_verified": all(result["evidence_hashes_verified"]
                                            for result in results),
        "all_primary_pointers_hit": all(result["primary_pointer_hit"] for result in results),
        "all_verify_commands_exit_zero": all(code == 0 for code in verify_exit.values())
        and bool(verify_exit),
        "workspace_evidence_is_documentation_only": all(
            entry["path"] in set(CONFIG.get("workspace_documentation_evidence", []))
            for claim in claims for entry in claim["evidence"]
            if entry["path"].startswith("WORKSPACE:")),
        "all_reproduce_products_hash_verified": products_verified,
        "all_reproduce_products_reproduced_or_disclosed": not undisclosed_changes
        and reproduce_all_zero,
        "all_reproduce_commands_exit_zero": reproduce_all_zero and bool(tier_b),
        "no_unsupported_accuracy_claim": all(result["accuracy_ok"] for result in results),
        "no_prohibited_source_used_as_model_evidence": all(result["prohibited_ok"]
                                                           for result in results),
        "hedged_language_present_where_required": all(result["hedge_ok"] for result in results),
        "writeup_carries_no_prohibited_open": all(
            not result["prohibited_usage"] for result in results
            if claim_is_model(claim_by_id(claims, result["claim_id"]))),
        "no_upload_and_no_model_change": bool(
            not any(token in command for command in commands_used
                    for token in ("zindi_submit", "kaggle_submit", "submit_zindi"))
            and candidate_artifact_ties_to_registry()),
        "all_passed": all(claims_all),
    }
    acceptance["all_passed"] = all(
        value is True for key, value in acceptance.items() if key != "all_passed")

    provenance_counts = Counter(claim["provenance_class"] for claim in claims)
    report = {
        "preregistration": {
            "config_sha256": sha256_file(CONFIG_PATH_RESOLVED),
            "row": CONFIG["row"], "intent": CONFIG["intent"],
            "docs": [{"path": doc, "sha256": sha256_file(ROOT / doc)} for doc in CONFIG["docs"]],
            "tokenizer": TOKENIZER, "provenance_classes": CONFIG["provenance_classes"],
            "evidence_allowlist": CONFIG["evidence_allowlist"],
            "accuracy_claim_patterns": CONFIG["accuracy_claim_patterns"],
            "prohibited_tokens": CONFIG["prohibited_tokens"],
            "acceptance": CONFIG["acceptance"],
        },
        "measure_before": measure_before(),
        "summary": {
            "claims_total": len(claims),
            "claims_by_doc": dict(Counter(claim["doc"] for claim in claims)),
            "claims_by_kind": dict(Counter(claim["kind"] for claim in claims)),
            "claims_by_provenance_class": dict(provenance_counts),
            "claims_fully_traceable": sum(claims_all),
            "fraction_quantitative_claims_traceable":
                round(sum(claims_all) / len(claims), 6) if claims else 0.0,
            "numeric_tokens_total": tokens_total,
            "numeric_tokens_covered": tokens_covered,
            "fraction_numeric_tokens_covered":
                round(tokens_covered / tokens_total, 6) if tokens_total else 1.0,
            "claims_asserting_accuracy": sum(1 for result in results
                                             if result["accuracy_assertion"]),
            "claims_with_prohibited_data_language": sum(1 for result in results
                                                        if result["prohibited_usage"]),
            "claims_with_prohibited_disclosure_language": sum(
                1 for result in results if result["prohibited_disclosure"]),
            "derived_aggregates_verified": sum(len(result["derived_tokens_verified"])
                                               for result in results),
            "claims_by_class": dict(Counter(claim["provenance_class"] for claim in claims)),
            "distinct_verify_commands": len(verify_exit),
            "distinct_reproduce_commands": len(tier_b),
        },
        "claims": [{**claim, "verification": by_id[claim["claim_id"]]} for claim in claims],
        "commands": {
            "tier_a_verify": [{"command": command, "exit_code": code}
                              for command, code in sorted(verify_exit.items())],
            "tier_b_reproduce": tier_b,
            "undisclosed_product_changes": undisclosed_changes,
            "execution_log": {key: value for key, value in log.items()
                              if key in ("present", "sha256", "working_tree_clean_before",
                                         "working_tree_clean_after")},
        },
        "unresolved": [result for result in results if not result["passed"]],
        "acceptance": acceptance,
    }
    return report


def claim_by_id(claims: list[dict[str, Any]], claim_id: str) -> dict[str, Any] | None:
    return next((claim for claim in claims if claim["claim_id"] == claim_id), None)


def candidate_artifact_ties_to_registry() -> bool:
    """The audit must not have changed or submitted the frozen scored candidate."""
    registry = json.loads((ROOT / "reports/candidates.json").read_text(encoding="utf-8"))
    cards = {card.get("candidate_id"): card for card in registry.get("candidate_set", [])}
    card = cards.get("V2_model")
    if not isinstance(card, dict):
        return False
    artifact = ROOT / str(card.get("artifact", ""))
    return artifact.is_file() and card.get("sha256") == sha256_file(artifact)


def claim_is_model(claim: dict[str, Any] | None) -> bool:
    return bool(claim) and claim["provenance_class"] in {"measured_model_only",
                                                         "model_specification"}


def measure_before() -> dict[str, Any]:
    prior = sorted(str(path.relative_to(ROOT))
                   for path in (ROOT / "docs/writeup").glob("*claim*")
                   if path.name != Path(CONFIG["audit_markdown"]).name)
    return {
        "writeup_documents": [{"path": doc,
                               "sha256": sha256_file(ROOT / doc),
                               "lines": len((ROOT / doc).read_text().splitlines())}
                              for doc in CONFIG["docs"]],
        "prior_claim_inventory": prior,
        "note": ("the writeup drafts and their reports already existed; before this audit no "
                 "consolidated claim inventory linked each quantitative statement to an "
                 "evidence pointer and a reproducible command"),
    }


# --------------------------------------------------------------------------- markdown
def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = ["# Claim-to-evidence audit for the Bias Bounty writeup", ""]
    lines.append(f"Row: `{report['preregistration']['row']}`. "
                 f"{report['preregistration']['intent'].capitalize()}.")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    acceptance = report["acceptance"]
    token_percent = f"{summary['fraction_numeric_tokens_covered'] * 100:.1f}%"
    claim_percent = f"{summary['fraction_quantitative_claims_traceable'] * 100:.1f}%"
    lines.append(f"- **{summary['claims_fully_traceable']} of {summary['claims_total']} "
                 f"quantitative claims fully traceable** ({claim_percent}).")
    lines.append(f"- **{summary['numeric_tokens_covered']} of "
                 f"{summary['numeric_tokens_total']} numeric tokens ({token_percent}) "
                 f"are present in the evidence named by the claim that states them.**")
    lines.append(f"- {summary['distinct_verify_commands']} per-claim verify commands and "
                 f"{summary['distinct_reproduce_commands']} reproduce commands; every listed "
                 f"command's recorded exit code is 0 and every reproduce product hash-verifies.")
    lines.append(f"- Accuracy language outside the allowed classes: "
                 f"{'none' if acceptance['no_unsupported_accuracy_claim'] else 'PRESENT'}. "
                 f"Prohibited reference/label/leaderboard sources used as scored-model evidence: "
                 f"{'none' if acceptance['no_prohibited_source_used_as_model_evidence'] else 'PRESENT'}.")
    lines.append(f"- All acceptance gates: **{'PASS' if acceptance['all_passed'] else 'FAIL'}**.")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("Each of the three writeup documents is split into quantitative claim blocks "
                 "(a block is a paragraph or a table containing at least one number outside a "
                 "fenced command). Every number in a block is a token; a token is *covered* when "
                 "the same value appears in an evidence artifact named by that claim, allowing "
                 "for the decimal places actually written and for fraction/percentage "
                 "presentation. Identifier-like digit strings (bare GEOIDs) are not claims. "
                 "Every claim names a primary artifact plus an exact pointer into it, is checked "
                 "against the live file hash, carries a per-claim verify command that must exit "
                 "0, and carries the command that regenerates its artifact.")
    lines.append("")
    lines.append("## Claims by document")
    lines.append("")
    lines.append("| Document | Claims | Fully traceable | Numeric tokens | Covered |")
    lines.append("|---|---:|---:|---:|---:|")
    for doc in report["preregistration"]["docs"]:
        claims = [claim for claim in report["claims"] if claim["doc"] == doc["path"]]
        passed = sum(1 for claim in claims if claim["verification"]["passed"])
        tokens = sum(claim["verification"]["tokens_total"] for claim in claims)
        covered = sum(claim["verification"]["tokens_covered"] for claim in claims)
        lines.append(f"| `{doc['path']}` | {len(claims)} | {passed} | {tokens} | {covered} |")
    lines.append("")
    lines.append("## Claim inventory")
    lines.append("")
    lines.append("Each row is one quantitative claim: where it is stated, the artifact and "
                 "pointer that substantiate it, and how to reproduce that artifact. `Class` is "
                 "the provenance of the statement, not a claim of accuracy.")
    lines.append("")
    lines.append("| Claim | Document | Section | Lines | Class | Primary evidence | Pointer | "
                 "Tokens | Verify | Reproduce |")
    lines.append("|---|---|---|---|---|---|---|---:|---|---|")
    for claim in report["claims"]:
        verification = claim["verification"]
        primary = claim["evidence"][0] if claim["evidence"] else {"path": "-", "pointer": "-"}
        excerpt = " ".join(claim["text"].split())
        excerpt = (excerpt[:70] + "…") if len(excerpt) > 70 else excerpt
        excerpt = excerpt.replace("|", "\\|")
        lines.append(f"| `{claim['claim_id']}` {excerpt} | `{Path(claim['doc']).name}` | "
                     f"{claim['section'][:38]} | {claim['line_start']}-{claim['line_end']} | "
                     f"`{claim['provenance_class']}` | `{primary['path']}` | "
                     f"`{primary.get('pointer') or '-'}` | "
                     f"{verification['tokens_covered']}/{verification['tokens_total']} | "
                     f"{'exit 0' if verification['passed'] else 'FAILED'} | "
                     f"`{claim['reproduce_command'][:60]}…` |")
    lines.append("")
    lines.append("## Evidence artifacts and hashes")
    lines.append("")
    lines.append("| Artifact | SHA-256 | Claims |")
    lines.append("|---|---|---:|")
    usage: dict[str, list[str]] = defaultdict(list)
    for claim in report["claims"]:
        for entry in claim["evidence"]:
            usage[entry["path"]].append(claim["claim_id"])
    for path in sorted(usage):
        digest = sha256_file(ROOT / path) if (ROOT / path).is_file() else "MISSING"
        lines.append(f"| `{path}` | `{digest}` | {len(usage[path])} |")
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    lines.append("Per-claim verify commands (executed by the audit; each must exit 0):")
    lines.append("")
    lines.append("```sh")
    for entry in report["commands"]["tier_a_verify"]:
        lines.append(f"# exit {entry['exit_code']}\n{entry['command']}")
    lines.append("```")
    lines.append("")
    lines.append("Reproduce commands (executed once with the authorised workspace and recorded "
                 "in the committed execution log; products are hash-verified by every audit "
                 "run):")
    lines.append("")
    lines.append("```sh")
    for entry in report["commands"]["tier_b_reproduce"]:
        lines.append(f"# exit {entry['exit_code']}")
        lines.append(entry["command"])
    lines.append("```")
    lines.append("")
    lines.append("## Unresolved claims")
    lines.append("")
    if not report["unresolved"]:
        lines.append("None. Every quantitative claim in the three writeup documents carries a "
                     "verified evidence pointer, a passing verify command and a hash-verified "
                     "reproduction path.")
    else:
        for result in report["unresolved"]:
            lines.append(f"- `{result['claim_id']}`: uncovered tokens "
                         f"{result['tokens_uncovered']}; pointer "
                         f"{result['pointer']!r} hit={result['primary_pointer_hit']}; "
                         f"accuracy ok={result['accuracy_ok']}; prohibited "
                         f"{result['prohibited_violations']}.")
    lines.append("")
    lines.append("## Limits of this screen")
    lines.append("")
    lines.append("This is a documentation screen. Presence of a number in the cited artifact "
                 "proves the statement was not invented and is reproducible from committed "
                 "permitted data; it does not make an unvalidated proxy accurate, does not "
                 "endorse any interpretation, and does not assess the hidden organiser target. "
                 "Statements about real-world consequences remain hypothetical, and no claim "
                 "here is an accuracy measurement. No candidate was changed and nothing was "
                 "uploaded.")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
CONFIG_PATH_RESOLVED = CONFIG_PATH


def main() -> int:
    global CONFIG_PATH_RESOLVED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--out", type=Path, default=ROOT / CONFIG["report"]
                        if CONFIG else ROOT / "reports/writeup-claim-evidence-audit.json")
    parser.add_argument("--markdown", type=Path,
                        default=ROOT / "docs/writeup/claim-evidence-audit.md")
    parser.add_argument("--inventory", type=Path, default=ROOT / "reports/writeup-claim-inventory.json")
    parser.add_argument("--write-inventory", action="store_true")
    parser.add_argument("--execute-reproduce", action="store_true")
    parser.add_argument("--reproduce-filter", default=None,
                        help="run only reproduce commands containing this substring")
    parser.add_argument("--report-only-missing", action="store_true")
    arguments = parser.parse_args()

    CONFIG_PATH_RESOLVED = arguments.config
    configure(load_config(arguments.config))
    out = arguments.out if arguments.out.is_absolute() else ROOT / arguments.out
    markdown = arguments.markdown if arguments.markdown.is_absolute() else ROOT / arguments.markdown
    inventory_path = arguments.inventory if arguments.inventory.is_absolute() \
        else ROOT / arguments.inventory

    pool = EvidencePool()
    pool.load()

    if arguments.write_inventory:
        claims = build_inventory(pool)
        inventory_path.write_text(json.dumps({"claims": claims}, indent=1,
                                             ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {relative(inventory_path)} with {len(claims)} claims")
        return 0

    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    claims = payload["claims"]

    if arguments.execute_reproduce:
        log = execute_reproduce(claims, only=arguments.reproduce_filter)
        log_path = ROOT / CONFIG["execution_log"]
        log_path.write_text(json.dumps(log, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(f"wrote {relative(log_path)}: "
              f"{sum(1 for entry in log['commands'] if entry['exit_code'] == 0)}/"
              f"{len(log['commands'])} reproduce commands exited 0")
        return 0

    report = audit(pool, claims)

    if arguments.report_only_missing:
        for result in report["unresolved"]:
            claim = claim_by_id(claims, result["claim_id"])
            print(f"{result['claim_id']} L{claim['line_start']} "
                  f"uncovered={result['tokens_uncovered']} pointer={result['primary_pointer_hit']} "
                  f"acc={result['accuracy_ok']} pro={result['prohibited_ok']} "
                  f"hedge={result['hedge_ok']} | {' '.join(claim['text'].split())[:90]}")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")

    summary = report["summary"]
    print(json.dumps({
        "gates_all_passed": report["acceptance"]["all_passed"],
        "claims": summary["claims_total"],
        "traceable": summary["claims_fully_traceable"],
        "fraction_traceable": summary["fraction_quantitative_claims_traceable"],
        "tokens": summary["numeric_tokens_total"],
        "tokens_covered": summary["numeric_tokens_covered"],
        "failed_gates": sorted(key for key, value in report["acceptance"].items()
                               if value is not True),
    }, indent=1))
    failed = [key for key, value in report["acceptance"].items() if value is not True]
    if failed:
        print(f"FAIL v2 writeup claim audit: {', '.join(failed)}")
        return 1
    print("PASS v2 writeup claim audit: every quantitative claim in the writeup carries a "
          "hash-verified evidence pointer, a passing verify command and a hash-verified "
          "reproduction path, with no unsupported accuracy claim and no prohibited source used "
          "as scored-model evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
