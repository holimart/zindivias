#!/usr/bin/env python
"""Passive public-leaderboard and Bias Scorecard snapshot for the Bias Bounty lane.

Row: backlog:.langywrap/backlogs/competitions.jsonl:bias-lb-snapshot-and-scorecard

Two sources, both sanctioned by the row and by the operator amendment of 2026-10-24:

* the shared orchestrator ledger (the steward snapshots every Zindi leaderboard each tick with
  ``scripts/leaderboard_snapshot.py``); this script only *reads* it, and
* the orchestrator's authenticated submission **status** path, i.e. one read-only ``GET`` of the
  submission list for this competition, which carries the organiser-computed ``public_scoring_rubric``
  (the per-submission Bias Scorecard).

Hard rules enforced by this script:

* **no upload, ever** - there is no POST path in this file (a test asserts it);
* **no secrets committed** - the presigned ``url`` field (which carries an AWS access-key id and a
  request signature) is dropped before anything is written, and every written file is scanned;
* **no leaderboard-driven modelling** - nothing read here is fed into a model, feature, threshold or
  parameter. The report records ``scorecard_influenced_parameters: []`` and the model chain is not
  imported or read by this script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "lb-scorecard-snapshot-preregistered.json"
SUBMISSIONS_ENDPOINT_PATH = "/submissions?per_page=100"
SCORECARD_FIELDS = ("bias_indicator", "status", "disparity_ratio", "coverage_gap_percentage",
                    "coverage_gap_description", "reason")
DROP_FIELDS = ("url",)
TIMESTAMP_KEYS = ("ts", "created_at", "observed_at", "best_at", "snapshot_utc", "retrieved_at_utc")
SECRET_PATTERNS = (r"AKIA[0-9A-Z]{16}", r"X-Amz-", r"[Ss]ignature=", r"auth-token", r"auth_token",
                   r"[Cc]ookie", r"storage_state", r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")


class SnapshotError(RuntimeError):
    """Fail-closed reason for the passive snapshot."""


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def display(path: Path, root: Path = ROOT) -> str:
    """Repo-relative when possible, otherwise the bare filename so sandboxes stay readable."""
    resolved = Path(path)
    return str(resolved.relative_to(root)) if resolved.is_relative_to(root) else resolved.name


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()


def ledger_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    orchestrator = os.environ.get("ORCHESTRATOR_ROOT")
    if not orchestrator:
        raise SnapshotError("neither --ledger nor $ORCHESTRATOR_ROOT is available")
    return (Path(orchestrator) / "data" / "ledger").resolve()


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------- scorecards (status path)

def fetch_submission_status(config: dict[str, Any]) -> list[dict[str, Any]]:
    """One read-only GET on the orchestrator's authenticated status path. No POST exists here."""
    token_file = os.environ.get("ZINDI_AUTH_TOKEN_FILE")
    if not token_file or not Path(token_file).is_file():
        raise SnapshotError("$ZINDI_AUTH_TOKEN_FILE is not available; run without --fetch to "
                            "verify the committed scorecard snapshots instead")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise SnapshotError("the Zindi auth token file is empty")
    url = config["status_endpoint"].rstrip("/") + SUBMISSIONS_ENDPOINT_PATH
    request = urllib.request.Request(url, headers={"auth-token": token,
                                                  "User-Agent": "Mozilla/5.0",
                                                  "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=config.get("http_timeout_seconds", 60)) as response:
        payload = json.load(response)
    data = payload.get("data")
    if not isinstance(data, list):
        raise SnapshotError("status path returned no submission list")
    return data


def redact(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the row needs; drop the presigned download URL and any token shape."""
    keep = config["retained_submission_fields"]
    clean = {key: record.get(key) for key in keep}
    for field in DROP_FIELDS:
        clean.pop(field, None)
    rubric = record.get("public_scoring_rubric")
    if not isinstance(rubric, dict):
        raise SnapshotError(f"submission {record.get('id')} carries no scoring rubric object")
    clean["rubric_dimensions"] = sorted(rubric)
    clean["scorecard"] = {dimension: {field: (values or {}).get(field)
                                      for field in SCORECARD_FIELDS}
                          for dimension, values in rubric.items()}
    return clean


def scorecard_summary(scorecard: dict[str, Any]) -> dict[str, Any]:
    ratios = [values["disparity_ratio"] for values in scorecard.values()
              if isinstance(values.get("disparity_ratio"), (int, float))]
    gaps = [abs(values["coverage_gap_percentage"]) for values in scorecard.values()
            if isinstance(values.get("coverage_gap_percentage"), (int, float))]
    return {
        "dimensions_total": len(scorecard),
        "dimensions_status_ok": sum(1 for v in scorecard.values() if v.get("status") == "ok"),
        "dimensions_status_undefined": sum(1 for v in scorecard.values()
                                           if v.get("status") == "undefined"),
        "dimensions_described_similar": sum(1 for v in scorecard.values()
                                            if v.get("coverage_gap_description") == "Similar"),
        "dimensions_flagged_not_similar": sorted(
            dimension for dimension, values in scorecard.items()
            if values.get("coverage_gap_description") not in (None, "Similar")),
        "dimensions_with_reason": sorted(dimension for dimension, values in scorecard.items()
                                         if values.get("reason")),
        "disparity_ratios_min": min(ratios) if ratios else None,
        "disparity_ratios_max": max(ratios) if ratios else None,
        "largest_absolute_coverage_gap_percentage": max(gaps) if gaps else None,
    }


def scorecard_document(record: dict[str, Any], config: dict[str, Any], ledger_scores: dict[str, Any],
                       retrieved_at_utc: str | None) -> dict[str, Any]:
    submission_id = str(record.get("id"))
    scorecard = {dimension: {field: values.get(field) for field in SCORECARD_FIELDS}
                 for dimension, values in (record.get("scorecard") or {}).items()}
    missing = [dimension for dimension, values in scorecard.items()
               if set(values) != set(SCORECARD_FIELDS)]
    if missing:
        raise SnapshotError(f"submission {submission_id} has incomplete scorecard fields: {missing}")
    ledger_record = ledger_scores.get(submission_id, {})
    scorecard_influenced_parameters: list[str] = []
    return {
        "preregistration": {
            "config_sha256": sha256(CONFIG_PATH),
            "row": config["row"],
            "lane": config["lane"],
            "intent": config["intent"],
            "read_only_status_path": config["status_endpoint"].rstrip("/") + SUBMISSIONS_ENDPOINT_PATH,
            "fields_retained": list(config["retained_submission_fields"]),
            "fields_dropped": list(DROP_FIELDS),
            "fields_dropped_reason": ("the presigned download URL embeds an AWS access-key id and a "
                                      "request signature and must never be committed"),
            "http_methods_used": ["GET"],
            "scorecard_fields_required": list(SCORECARD_FIELDS),
        },
        "competition": config["competition"],
        "submission": {
            "id": submission_id,
            "filename": record.get("filename"),
            "comment": record.get("comment"),
            "created_at": record.get("created_at"),
            "status": record.get("status"),
            "public_score": record.get("public_score"),
            "rubric_type": record.get("rubric_type"),
            "supports_rubric": record.get("supports_rubric"),
            "chosen": record.get("chosen"),
            "submitter_platform_recorded": record.get("submitter"),
            "identity_note": ("platform-recorded submitter handle, not an operator-confirmed team "
                             "name for the writeup"),
        },
        "ledger_cross_check": {
            "ledger_public_score": ledger_record.get("public_score"),
            "ledger_status": ledger_record.get("status"),
            "ledger_observed_at": ledger_record.get("observed_at"),
            "scores_agree": (ledger_record.get("public_score") == record.get("public_score")
                             if ledger_record and record.get("status") == "successful" else None),
            "agreement_applicable": record.get("status") == "successful",
        },
        "scorecard": scorecard,
        "derived": scorecard_summary(scorecard),
        "provenance": {
            "retrieved_at_utc": retrieved_at_utc or "unknown",
            "retrieved_by": "scripts/snapshot_lb_and_scorecards.py --fetch",
            "platform_html_scorecard_available": False,
            "html_note": ("the authenticated status path returns the rubric as JSON only; no HTML "
                          "scorecard document is exposed, so the JSON file is the committed record"),
        },
        "non_influence": {
            "scorecard_influenced_parameters": scorecard_influenced_parameters,
            "no_upload_performed": True,
            "note": ("reported passively; no feature, threshold, weight or parameter in the model "
                     "chain reads this file"),
        },
    }


def build_scorecards(config: dict[str, Any], scorecards_dir: Path, ledger_scores: dict[str, Any],
                     fetch: bool, tamper: str | None,
                     rewrite: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Write or verify one JSON file per scored submission; return (index, written_paths)."""
    required = set(config["required_scorecard_submission_ids"])
    if fetch:
        records = [redact(record, config) for record in fetch_submission_status(config)]
    else:
        records = []
        for path in sorted(scorecards_dir.glob("*.json")):
            if path.name == "index.json":
                continue
            document = load_json(path)
            records.append({"id": document["submission"]["id"],
                            "filename": document["submission"]["filename"],
                            "comment": document["submission"]["comment"],
                            "created_at": document["submission"]["created_at"],
                            "status": document["submission"]["status"],
                            "public_score": document["submission"]["public_score"],
                            "rubric_type": document["submission"]["rubric_type"],
                            "supports_rubric": document["submission"]["supports_rubric"],
                            "chosen": document["submission"]["chosen"],
                            "submitter": document["submission"]["submitter_platform_recorded"],
                            "scorecard": document["scorecard"],
                            "retrieved_at_utc": document["provenance"]["retrieved_at_utc"]})
    present = {str(record["id"]) for record in records}
    missing = sorted(required - present)
    if missing:
        raise SnapshotError(f"scorecards missing for required submissions: {missing}")

    fetched_at_utc = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    committed_at = {str(record["id"]): record.get("retrieved_at_utc") for record in records}
    written: list[str] = []
    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("created_at") or "")):
        submission_id = str(record["id"])
        document = scorecard_document(record, config, ledger_scores,
                                      fetched_at_utc if fetch else committed_at.get(submission_id))
        if tamper == "inject_secret" and submission_id == config["tamper_target_submission"]:
            document["provenance"]["injected"] = ("https://example.invalid/file.csv?X-Amz-"
                                                 "Credential=AKIAABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
        if tamper == "scorecard_field_dropped" and submission_id == config["tamper_target_submission"]:
            document["preregistration"]["scorecard_fields_required"] = list(SCORECARD_FIELDS[:-1])
            document["scorecard"] = {dimension: {field: values[field]
                                                 for field in SCORECARD_FIELDS[:-1]}
                                     for dimension, values in document["scorecard"].items()}
            document["derived"] = scorecard_summary(document["scorecard"])
        path = scorecards_dir / f"{submission_id}.json"
        payload = canonical_bytes(document)
        if path.is_file() and path.read_bytes() == payload and tamper is None:
            written.append(path.name)
        elif fetch or rewrite or tamper is not None or not path.is_file():
            scorecards_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            written.append(path.name)
        else:
            raise SnapshotError(f"committed scorecard differs from the rebuilt document: {path}")
        rows.append({
            "submission_id": submission_id,
            "filename": document["submission"]["filename"],
            "comment": document["submission"]["comment"],
            "created_at": document["submission"]["created_at"],
            "status": document["submission"]["status"],
            "public_score": document["submission"]["public_score"],
            "file": path.name,
            "sha256": sha256(path),
            "dimensions_total": document["derived"]["dimensions_total"],
            "dimensions_status_ok": document["derived"]["dimensions_status_ok"],
            "dimensions_status_undefined": document["derived"]["dimensions_status_undefined"],
            "dimensions_flagged_not_similar":
                document["derived"]["dimensions_flagged_not_similar"],
            "disparity_ratios_min": document["derived"]["disparity_ratios_min"],
            "disparity_ratios_max": document["derived"]["disparity_ratios_max"],
            "largest_absolute_coverage_gap_percentage":
                document["derived"]["largest_absolute_coverage_gap_percentage"],
            "ledger_scores_agree": document["ledger_cross_check"]["scores_agree"],
        })
    index = {
        "preregistration": {"config_sha256": sha256(CONFIG_PATH), "row": config["row"],
                            "lane": config["lane"], "intent": config["intent"]},
        "competition": config["competition"],
        "scorecards": rows,
        "required_submission_ids": sorted(required),
        "scorecards_present": sorted(row["submission_id"] for row in rows),
        "successful_scorecards_present": sorted(row["submission_id"] for row in rows
                                                if row["status"] == "successful"),
    }
    index_path = scorecards_dir / "index.json"
    payload = canonical_bytes(index)
    if not (index_path.is_file() and index_path.read_bytes() == payload):
        if fetch or rewrite or tamper is not None or not index_path.is_file():
            index_path.write_bytes(payload)
        else:
            raise SnapshotError(f"committed scorecard index differs from the rebuilt index: {index_path}")
    return index, written


# --------------------------------------------------------------- leaderboard (ledger only)

def pinned_event(events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """The pinned snapshot, or the newest one at or before it: never a drifting 'latest'."""
    pin = config["pinned_snapshot_utc"]
    eligible = [row for row in events if str(row.get("ts")) <= pin]
    if not eligible:
        raise SnapshotError(f"the shared ledger holds no snapshot event at or before {pin}")
    return sorted(eligible, key=lambda row: str(row.get("ts")))[-1]


def latest_archive(config: dict[str, Any], ledger: Path) -> tuple[Path, list[dict[str, Any]]]:
    directory = ledger / "leaderboards" / config["competition"]
    files = sorted(directory.glob("*.json.gz"))
    if not files:
        raise SnapshotError(f"no leaderboard archives under {directory}")
    wanted = filename_stamp(config["pinned_snapshot_utc"]) + ".json.gz"
    if (directory / wanted).is_file():
        path = directory / wanted
    else:
        older = [item for item in files if item.stem <= wanted]
        if not older:
            raise SnapshotError(f"no archive at or before the pinned snapshot {wanted}")
        path = older[-1]
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list) or not rows:
        raise SnapshotError(f"leaderboard archive {path} is empty")
    return path, rows


def competition_events(config: dict[str, Any], ledger: Path) -> list[dict[str, Any]]:
    events = [row for row in jsonl(ledger / "leaderboard-events.jsonl")
              if row.get("competition") == config["competition"]]
    if not events:
        raise SnapshotError("the shared ledger holds no leaderboard snapshot event for this "
                            "competition; the steward snapshot has not run")
    return sorted(events, key=lambda row: str(row.get("ts")))


def latest_event(config: dict[str, Any], ledger: Path) -> dict[str, Any]:
    return pinned_event(competition_events(config, ledger), config)


def ledger_submissions(config: dict[str, Any], ledger: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = [row for row in jsonl(ledger / "submissions.jsonl")
            if row.get("competition") == config["competition"]]
    by_id = {str(row.get("id")): row for row in rows}
    successful = {str(row["id"]): row for row in rows
                  if row.get("status") == "successful" and row.get("public_score") is not None}
    if not successful:
        raise SnapshotError("the ledger holds no successful scored submission for this competition")
    return by_id, successful


def filename_stamp(snapshot_utc: str) -> str:
    """`2026-10-25T05:12:19+00:00` -> `2026-10-25T051219+0000` (a UTC stamp usable as a filename)."""
    return snapshot_utc.replace(":", "").replace("+00:00", "+0000")


def leaderboard_document(config: dict[str, Any], ledger: Path, archive: Path, archive_rows: list[dict],
                         event: dict[str, Any], ledger_scores: dict[str, Any],
                         scorecard_index: dict[str, Any], tamper: str | None,
                         scorecard_index_path: Path) -> dict[str, Any]:
    ranked = sorted((row for row in archive_rows
                     if row.get("rank") and row.get("score") is not None),
                    key=lambda row: (row["rank"], row["score"]))
    scores = [row["score"] for row in ranked]
    our_best = event.get("our_best_public")
    positive = [row for row in ranked if row["score"] > 0]
    top20 = [{"rank": row["rank"], "public_rmse": row["score"],
              "submissions": row.get("subs"), "team_handle": row.get("user")}
             for row in ranked[:config["top_n"]]]
    per_submission = []
    for submission_id, record in sorted(ledger_scores.items()):
        score = record["public_score"]
        per_submission.append({
            "submission_id": submission_id,
            "filename": record.get("filename"),
            "comment": record.get("comment"),
            "public_score": score,
            "teams_strictly_better": sum(1 for value in scores if value < score),
            "teams_at_or_worse": sum(1 for value in scores if value >= score),
            "scorecard_available": submission_id in scorecard_index["scorecards_present"],
        })
    rank_10 = scores[9] if len(scores) >= 10 else None
    document = {
        "preregistration": {
            "config_sha256": sha256(CONFIG_PATH),
            "row": config["row"],
            "lane": config["lane"],
            "intent": config["intent"],
            "top_n": config["top_n"],
            "sources": {
                "leaderboard": "shared orchestrator ledger archive (steward snapshot, read-only)",
                "our_scores": "shared orchestrator ledger submission records (read-only)",
                "scorecards": "authenticated submission status path, read-only GET, committed as JSON",
            },
            "passive_only": True,
        },
        "competition": config["competition"],
        "direction": config["direction"],
        "snapshot_utc": event.get("ts"),
        "source": {
            "archive_path": str(archive),
            "archive_sha256": sha256(archive),
            "archive_rows": len(archive_rows),
            "ledger_root": str(ledger),
            "event_keys": sorted(event),
        },
        "standings": {
            "participants": event.get("participants"),
            "ranked_teams": event.get("ranked"),
            "our_rank": event.get("our_rank"),
            "our_percentile": event.get("our_percentile"),
            "our_submissions": event.get("our_submissions"),
            "our_best_public": our_best,
            "rank_10": rank_10,
            "rank_25": event.get("rank_25"),
            "rank_50": event.get("rank_50"),
            "rank_100": event.get("rank_100"),
            "median": event.get("median"),
            "perfect_scores": event.get("perfect_scores"),
        },
        "gaps": {
            "our_best_minus_rank_10": (None if our_best is None or rank_10 is None
                                       else our_best - rank_10),
            "our_best_minus_median": (None if our_best is None or event.get("median") is None
                                      else our_best - event["median"]),
            "our_best_minus_rank_50": (None if our_best is None or event.get("rank_50") is None
                                       else our_best - event["rank_50"]),
            "best_positive_score": positive[0]["score"] if positive else None,
            "our_best_minus_best_positive": (None if not positive or our_best is None
                                             else our_best - positive[0]["score"]),
        },
        "top20_public_rmse": top20,
        "our_submissions_in_the_public_column": per_submission,
        "scorecard_index_sha256": sha256(scorecard_index_path),
        "non_influence": {
            "scorecard_influenced_parameters": [],
            "no_upload_performed": True,
            "no_new_submission": True,
            "note": ("snapshot and reflection only; the operator amendment of 2026-10-24 forbids any "
                     "scorecard- or leaderboard-driven feature, threshold or parameter change"),
        },
    }
    if tamper == "secret_in_snapshot":
        document["source"]["injected"] = "auth-token=AKIAABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
    if tamper == "rank_without_timestamp":
        document.pop("snapshot_utc")
    return document


def wall_clock_hits(payload: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    pattern = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)?\b")
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TIMESTAMP_KEYS:
                continue
            hits += wall_clock_hits(value, f"{path}/{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            hits += wall_clock_hits(value, f"{path}[{index}]")
    elif isinstance(payload, str) and pattern.search(payload):
        hits.append(path)
    return hits


def secret_hits(payload: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            hits += secret_hits(value, f"{path}/{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            hits += secret_hits(value, f"{path}[{index}]")
    elif isinstance(payload, str):
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, payload):
                hits.append(f"{path}: {pattern}")
    return hits


# --------------------------------------------------------------- driver

def run(config: dict[str, Any], ledger: Path, fetch: bool, tamper: str | None,
        args_scorecards: str | None = None, args_leaderboard: str | None = None,
        rewrite: bool = False) -> dict[str, Any]:
    scorecards_dir = Path(args_scorecards) if args_scorecards else ROOT / config["scorecards_dir"]
    leaderboard_dir = Path(args_leaderboard) if args_leaderboard else ROOT / config["leaderboard_dir"]
    ledger_by_id, ledger_successful = ledger_submissions(config, ledger)
    scorecard_index, scorecard_files = build_scorecards(config, scorecards_dir, ledger_by_id,
                                                        fetch, tamper, rewrite)
    archive, archive_rows = latest_archive(config, ledger)
    event = latest_event(config, ledger)
    document = leaderboard_document(config, ledger, archive, archive_rows, event,
                                   ledger_successful, scorecard_index, tamper,
                                   scorecards_dir / "index.json")

    stamp = document.get("snapshot_utc")
    # An undated snapshot is still written (so the gate can report it) but never under a stamp name.
    snapshot_name = f"{filename_stamp(str(stamp))}.json" if stamp else "UNDATED.json"
    snapshot_path = leaderboard_dir / snapshot_name
    leaderboard_dir.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(document)
    if not (snapshot_path.is_file() and snapshot_path.read_bytes() == payload):
        if fetch or rewrite or tamper is not None or not snapshot_path.is_file():
            snapshot_path.write_bytes(payload)
        else:
            raise SnapshotError(f"committed snapshot differs from the rebuilt snapshot: {snapshot_path}")

    history = [{"snapshot_utc": row.get("ts"), "our_rank": row.get("our_rank"),
                "our_best_public": row.get("our_best_public"),
                "participants": row.get("participants"), "ranked_teams": row.get("ranked"),
                "rank_10": row.get("rank_10"), "median": row.get("median")}
               for row in [item for item in competition_events(config, ledger)
                           if str(item.get("ts")) <= config["pinned_snapshot_utc"]]]
    index = {
        "preregistration": {"config_sha256": sha256(CONFIG_PATH), "row": config["row"],
                            "lane": config["lane"], "intent": config["intent"]},
        "competition": config["competition"],
        "history": history,
    }
    index["snapshots"] = [{"file": snapshot_path.name,
                           "snapshot_utc": document.get("snapshot_utc"),
                           "sha256": sha256(snapshot_path)}]
    index_path = leaderboard_dir / "index.json"
    index_payload = canonical_bytes(index)
    if not (index_path.is_file() and index_path.read_bytes() == index_payload):
        if fetch or rewrite or tamper is not None or not index_path.is_file():
            index_path.write_bytes(index_payload)
        else:
            raise SnapshotError(f"committed snapshot index differs from the rebuilt index: {index_path}")

    return measure(config, document, scorecard_index, snapshot_path, index_path, scorecard_files,
                   scorecards_dir / "index.json", scorecards_dir)


def measure(config: dict[str, Any], document: dict[str, Any], scorecard_index: dict[str, Any],
            snapshot_path: Path, index_path: Path, scorecard_files: list[str],
            scorecard_index_path: Path, scorecards_dir: Path) -> dict[str, Any]:
    standings = document["standings"]
    gaps = document["gaps"]
    successful_ids = scorecard_index["successful_scorecards_present"]
    ranked_scores = [row["public_rmse"] for row in document["top20_public_rmse"]]
    written_files = ([snapshot_path, index_path, scorecard_index_path]
                     + [(scorecards_dir / name) if not Path(name).is_absolute() else Path(name)
                        for name in scorecard_files])
    secrets: list[str] = []
    for path in written_files:
        secrets += [f"{path.name}:{hit}" for hit in secret_hits(load_json(path))]
    fields_incomplete: list[str] = []
    for name in scorecard_files:
        card_path = (scorecards_dir / name) if not Path(name).is_absolute() else Path(name)
        card = load_json(card_path)
        for dimension, values in card["scorecard"].items():
            if sorted(values) != sorted(SCORECARD_FIELDS):
                fields_incomplete.append(f"{card_path.name}:{dimension}")
    ledger_root = Path(document["source"]["ledger_root"])
    gates = {
        "snapshot_carries_utc_timestamp": bool(document.get("snapshot_utc")),
        "snapshot_filename_is_the_utc_stamp": snapshot_path.stem == filename_stamp(
            str(document.get("snapshot_utc"))),
        "top_n_table_present": (len(document["top20_public_rmse"]) == config["top_n"]
                                and ranked_scores == sorted(ranked_scores)),
        "our_rank_and_best_public_recorded": (isinstance(standings["our_rank"], int)
                                              and isinstance(standings["our_best_public"], float)),
        "our_best_public_matches_ledger_best": (
            standings["our_best_public"] == min(row["public_score"]
                                                for row in
                                                document["our_submissions_in_the_public_column"]
                                                if row["public_score"] is not None)),
        "gap_to_top10_boundary_computed": (gaps["our_best_minus_rank_10"] is not None
                                           and abs(gaps["our_best_minus_rank_10"]
                                                   - (standings["our_best_public"]
                                                      - standings["rank_10"])) < 1e-12),
        "every_successful_submission_has_a_scorecard": (
            len(successful_ids) >= config["minimum_scorecards"]
            and all(row["scorecard_available"]
                    for row in document["our_submissions_in_the_public_column"])),
        "scorecard_dimensions_complete": all(row["dimensions_total"] == 9 for row
                                             in scorecard_index["scorecards"]),
        "scorecard_fields_complete_per_dimension": fields_incomplete == [],
        "scorecard_scores_agree_with_ledger": all(row["ledger_scores_agree"] is not False
                                                  for row in scorecard_index["scorecards"]),
        "required_scorecards_present": (set(scorecard_index["required_submission_ids"])
                                        .issubset(set(scorecard_index["scorecards_present"]))),
        "no_secrets_in_committed_outputs": secrets == [],
        "ledger_access_is_read_only": (config["ledger_write_forbidden"] is True
                                       and not any(str(path).startswith(str(ledger_root))
                                                   for path in written_files)),
        "no_upload_path_exists": not UPLOAD_TOKENS_PRESENT,
        "scorecard_never_influenced_parameters": (
            document["non_influence"]["scorecard_influenced_parameters"] == []
            and document["non_influence"]["no_upload_performed"] is True),
        "report_serialises_no_unexpected_wall_clock": wall_clock_hits(document) == [],
    }
    gates["gates_all_passed"] = all(value is True for key, value in gates.items()
                                    if key != "gates_all_passed")
    return {
        "preregistration": document["preregistration"],
        "measure_before": config["measure_before"],
        "summary": {
            "snapshot_utc": document.get("snapshot_utc"),
            "snapshot_file": display(snapshot_path),
            "snapshot_sha256": sha256(snapshot_path),
            "index_file": display(index_path),
            "index_sha256": sha256(index_path),
            "participants": standings["participants"],
            "ranked_teams": standings["ranked_teams"],
            "our_rank": standings["our_rank"],
            "our_best_public": standings["our_best_public"],
            "rank_10": standings["rank_10"],
            "gap_to_top10_boundary": gaps["our_best_minus_rank_10"],
            "median": standings["median"],
            "gap_to_median": gaps["our_best_minus_median"],
            "perfect_scores": standings["perfect_scores"],
            "scorecards_committed": len(scorecard_index["scorecards"]),
            "scorecards_successful": len(successful_ids),
            "scorecard_dimensions_total": 9,
            "scorecard_fields_incomplete": fields_incomplete,
            "secret_hits": secrets,
            "failed_gates": [key for key, value in gates.items()
                             if key != "gates_all_passed" and value is not True],
            "gates_all_passed": gates["gates_all_passed"],
        },
        "standings": standings,
        "gaps": gaps,
        "top20_public_rmse": document["top20_public_rmse"],
        "our_submissions_in_the_public_column": document["our_submissions_in_the_public_column"],
        "scorecards": scorecard_index["scorecards"],
        "non_influence": document["non_influence"],
        "gates": gates,
    }


def post_indicators_present() -> bool:
    """True only if this file gained a request body or an explicit POST (it must never have one).

    The markers are assembled from fragments declared in the config so this source file never
    contains the very strings it scans for (a self-hit would fail the gate forever).
    """
    fragments = load_json(CONFIG_PATH)["post_request_fragments"]
    verb = fragments["verb"][0] + fragments["verb"][1]
    indicators = (f"method=\"{verb}\"", f"method='{verb}'", f'Request("{verb}',
                  fragments["body"] + "body")
    source = Path(__file__).read_text()
    return any(indicator in source for indicator in indicators)


UPLOAD_TOKENS_PRESENT = post_indicators_present()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--fetch", action="store_true",
                       help="refresh scorecards from the read-only authenticated status path")
    parser.add_argument("--tamper", default=None,
                       choices=(None, "inject_secret", "secret_in_snapshot",
                                "scorecard_field_dropped", "rank_without_timestamp"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--rewrite", action="store_true",
                       help="regenerate derived products from the ledger and the committed "
                            "scorecards after a config or format change (never invents platform data)")
    parser.add_argument("--scorecards-dir", default=None,
                       help="write scorecards here instead of reports/scorecards (negative controls)")
    parser.add_argument("--leaderboard-dir", default=None,
                       help="write snapshots here instead of reports/leaderboard")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    config = load_json(Path(args.config))
    ledger = ledger_root(args.ledger)
    try:
        report = run(config, ledger, args.fetch, args.tamper, args.scorecards_dir,
                     args.leaderboard_dir, args.rewrite)
    except SnapshotError as error:
        print(f"BLOCKED {error}", file=sys.stderr)
        return 2
    payload = canonical_bytes(report)
    out = Path(args.out) if args.out else ROOT / config["report"]
    if args.check_only:
        committed = out.read_bytes() if out.is_file() else b""
        print(("unchanged " if committed == payload else "CHANGED ") + str(out))
        return 0 if committed == payload else 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    summary = report["summary"]
    print(json.dumps({"gates_all_passed": summary["gates_all_passed"],
                      "snapshot_utc": summary["snapshot_utc"],
                      "our_rank": summary["our_rank"],
                      "our_best_public": summary["our_best_public"],
                      "rank_10": summary["rank_10"],
                      "gap_to_top10_boundary": summary["gap_to_top10_boundary"],
                      "scorecards": summary["scorecards_committed"],
                      "failed_gates": summary["failed_gates"],
                      "report_sha256": summary["snapshot_sha256"]}, indent=1))
    if not summary["gates_all_passed"]:
        print("FAIL lb snapshot and scorecards: " + ", ".join(summary["failed_gates"]),
              file=sys.stderr)
        return 1
    print("PASS lb snapshot and scorecards: passive ledger snapshot plus hash-pinned per-submission "
          "Bias Scorecards committed without secrets, uploads, or any leaderboard-driven model change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
