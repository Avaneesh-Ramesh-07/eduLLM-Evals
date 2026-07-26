"""Apply adjudicated Q-matrix corrections to ``data/rubrics_qmatrix_final.jsonl``.

Two independent, separately versioned correction passes are applied:

1. ``human_review_v1`` -- the human-adjudicated decisions from the 25-criterion
   blind review, read from ``qmatrix_human_review/adjudications.csv``. Only rows
   flagged ``changed=1`` modify the dataset; confirmations are recorded for the
   audit trail but change nothing.

2. ``qmatrix_rulefix_v1`` -- a high-precision, rule-based sweep of criteria whose
   own ``q_rationale`` literally states "No skill strictly required" yet still
   load >=1 skill in ``q_mapping``. These self-contradictory rows are set to the
   all-zero mapping (primary_skill = null), matching the generator's stated
   reasoning. Human-reviewed criteria are excluded so the human decision always
   wins. This pass is validated by ``tb_0125_c01``, which is one such row and was
   independently confirmed all-zero by the human reviewers.

For every modified record the synthetic ``difficulty`` / ``discrimination`` /
``irt_params`` are re-derived (via ``assign_irt_params.assign_params``) so the
shipped artifact stays internally consistent; difficulty is unchanged (it does
not depend on ``q_mapping``) and only the discrimination vector moves. Each
modified record gains a ``q_mapping_provenance`` block recording the version,
the field-level changes, the prior values, and the rationale.

The run is a DRY RUN by default (nothing is written). Pass ``--apply`` to write
``rubrics_qmatrix_final.{jsonl,json}`` in place (with one-time ``*.bak`` copies)
and the patch report under ``qmatrix_human_review/``.

Usage:
    python scripts/apply_qmatrix_human_review.py            # dry run: preview only
    python scripts/apply_qmatrix_human_review.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import assign_irt_params as irt  # noqa: E402  (path adjusted above)

SKILLS = ("content", "diagnosis", "scaffolding")
HUMAN_REVIEW_VERSION = "human_review_v1"
RULEFIX_VERSION = "qmatrix_rulefix_v1"
RULEFIX_MARKER = "No skill strictly required"
PRIMARY_NULL = "none"


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json_array(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_json_obj(path: Path, obj: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def loaded_skills(q_mapping: dict) -> list[str]:
    return [s for s in SKILLS if int((q_mapping or {}).get(s, 0)) == 1]


def read_adjudications(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["criterion_id"] = row["criterion_id"].strip()
        row["skill"] = row["skill"].strip()
        row["changed"] = row.get("changed", "0").strip() == "1"
    return rows


def snapshot(record: dict) -> dict:
    return {
        "q_mapping": dict(record.get("q_mapping") or {}),
        "primary_skill": record.get("primary_skill"),
        "discrimination": dict(record.get("discrimination") or {}),
        "difficulty": record.get("difficulty"),
    }


def apply_human_review(
    records_by_id: dict[str, dict], adjudications: list[dict], seed: int
) -> list[dict]:
    """Apply changed=1 human decisions, grouped per criterion. Returns change log."""
    changes_by_id: dict[str, list[dict]] = {}
    rationale_by_id: dict[str, list[str]] = {}
    drift: list[str] = []

    for row in adjudications:
        cid = row["criterion_id"]
        record = records_by_id.get(cid)
        if record is None:
            raise ValueError(f"adjudicated criterion not in rubric file: {cid}")
        skill = row["skill"]
        expected = row["ai_value"].strip()

        # Drift guard: the file must still hold the value the reviewers saw.
        if skill == "primary_skill":
            current = record.get("primary_skill") or PRIMARY_NULL
        else:
            current = str(int((record.get("q_mapping") or {}).get(skill, 0)))
        if current != expected:
            drift.append(
                f"{cid}/{skill}: file={current!r} but adjudications.csv expected ai_value={expected!r}"
            )

        if not row["changed"]:
            continue
        changes_by_id.setdefault(cid, [])
        rationale_by_id.setdefault(cid, [])
        rationale_by_id[cid].append(f"[{skill}] {row['rationale']}")

        if skill == "primary_skill":
            new_primary = row["adjudicated_value"].strip()
            changes_by_id[cid].append(
                {"field": "primary_skill", "from": record.get("primary_skill"),
                 "to": None if new_primary == PRIMARY_NULL else new_primary}
            )
        else:
            new_val = int(row["adjudicated_value"].strip())
            changes_by_id[cid].append(
                {"field": f"q_mapping.{skill}",
                 "from": int((record.get("q_mapping") or {}).get(skill, 0)), "to": new_val}
            )

    if drift:
        raise ValueError(
            "rubric file has drifted from the reviewed labels; refusing to apply:\n  "
            + "\n  ".join(drift)
        )

    change_log: list[dict] = []
    for cid, changes in changes_by_id.items():
        record = records_by_id[cid]
        prior = snapshot(record)
        for change in changes:
            if change["field"] == "primary_skill":
                record["primary_skill"] = change["to"]
            else:
                skill = change["field"].split(".", 1)[1]
                record["q_mapping"][skill] = change["to"]
        _finalize_record(record, prior, HUMAN_REVIEW_VERSION,
                          " ".join(rationale_by_id[cid]), seed)
        change_log.append({"criterion_id": cid, "version": HUMAN_REVIEW_VERSION,
                           "changes": changes, "prior": prior, "new": snapshot(record)})
    return change_log


def apply_rulefix(
    records: list[dict], exclude_ids: set[str], seed: int
) -> list[dict]:
    """Zero out self-contradictory rows (rationale says no skill, mapping loads one)."""
    change_log: list[dict] = []
    for record in records:
        cid = record["criterion_id"]
        if cid in exclude_ids:
            continue
        rationale = record.get("q_rationale") or ""
        if RULEFIX_MARKER not in rationale:
            continue
        loaded = loaded_skills(record.get("q_mapping") or {})
        if not loaded:
            continue
        prior = snapshot(record)
        changes = [{"field": f"q_mapping.{s}", "from": 1, "to": 0} for s in loaded]
        if record.get("primary_skill") is not None:
            changes.append({"field": "primary_skill",
                            "from": record.get("primary_skill"), "to": None})
        for skill in SKILLS:
            record["q_mapping"][skill] = 0
        record["primary_skill"] = None
        _finalize_record(
            record, prior, RULEFIX_VERSION,
            "Record's own q_rationale states no skill is strictly required, yet the "
            "mapping loaded one or more skills; set to all-zero to match the stated "
            "reasoning. Validated by tb_0125_c01, an equivalent row confirmed all-zero "
            "in human_review_v1.",
            seed,
        )
        change_log.append({"criterion_id": cid, "version": RULEFIX_VERSION,
                           "changes": changes, "prior": prior, "new": snapshot(record)})
    return change_log


def _finalize_record(record: dict, prior: dict, version: str, rationale: str, seed: int) -> None:
    """Re-derive synthetic params for a mutated record and stamp provenance."""
    irt.assign_params(record, seed)  # refresh difficulty/discrimination/irt_params
    record["q_mapping_provenance"] = {
        "version": version,
        "changed_at": utcnow_iso(),
        "prior_q_mapping": prior["q_mapping"],
        "prior_primary_skill": prior["primary_skill"],
        "rationale": rationale,
    }


def render_report(human_log: list[dict], rule_log: list[dict], meta: dict) -> str:
    lines = [
        "# Q-matrix human-review + rule-fix patch report",
        "",
        f"- Applied: {'YES (written)' if meta['applied'] else 'DRY RUN (nothing written)'}",
        f"- Rubric file: `{meta['rubrics']}`",
        f"- Records total: {meta['n_records']}",
        f"- human_review_v1 criteria changed: {len(human_log)}",
        f"- qmatrix_rulefix_v1 criteria changed: {len(rule_log)}",
        "",
        "## human_review_v1 (human-adjudicated)",
        "",
        "| Criterion | Change | From -> To |",
        "| --- | --- | --- |",
    ]
    for entry in human_log:
        for change in entry["changes"]:
            lines.append(
                f"| `{entry['criterion_id']}` | {change['field']} | "
                f"{change['from']} -> {change['to']} |"
            )
    lines += [
        "",
        "## qmatrix_rulefix_v1 (rule-based: self-contradictory rows -> all-zero)",
        "",
        f"{len(rule_log)} criteria had a q_rationale stating no skill is required while still "
        "loading >=1 skill; each was set to the all-zero mapping.",
        "",
        "| Criterion | Prior mapping | Prior primary |",
        "| --- | --- | --- |",
    ]
    for entry in rule_log:
        pm = entry["prior"]["q_mapping"]
        pm_str = ", ".join(f"{s}={int(pm.get(s, 0))}" for s in SKILLS)
        lines.append(
            f"| `{entry['criterion_id']}` | {pm_str} | {entry['prior']['primary_skill']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rubrics", type=Path, default=ROOT / "data/rubrics_qmatrix_final.jsonl")
    parser.add_argument("--adjudications", type=Path,
                        default=ROOT / "qmatrix_human_review/adjudications.csv")
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "qmatrix_human_review/coordinator_manifest.json")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "qmatrix_human_review")
    parser.add_argument("--seed", type=int, default=irt.DEFAULT_SEED)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    records = read_jsonl(args.rubrics)
    records_by_id = {r["criterion_id"]: r for r in records}
    adjudications = read_adjudications(args.adjudications)

    reviewed_ids = {row["criterion_id"] for row in adjudications}
    if args.manifest.exists():
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        reviewed_ids |= {c["criterion_id"] for c in manifest.get("criteria", [])}

    human_log = apply_human_review(records_by_id, adjudications, args.seed)
    rule_log = apply_rulefix(records, reviewed_ids, args.seed)

    meta = {
        "applied": bool(args.apply),
        "rubrics": str(args.rubrics),
        "n_records": len(records),
    }
    report_md = render_report(human_log, rule_log, meta)
    report_json = {
        "generated_at": utcnow_iso(),
        "applied": bool(args.apply),
        "seed": args.seed,
        "n_records": len(records),
        "human_review_v1": {"n_changed": len(human_log), "changes": human_log},
        "qmatrix_rulefix_v1": {"n_changed": len(rule_log), "changes": rule_log},
    }

    print(report_md)

    if not args.apply:
        print("\n[dry run] no files written. Re-run with --apply to write changes.")
        return 0

    jsonl_path = args.rubrics
    json_path = jsonl_path.with_suffix(".json")
    if not args.no_backup:
        for path in (jsonl_path, json_path):
            bak = path.with_suffix(path.suffix + ".human_review.bak")
            if path.exists() and not bak.exists():
                shutil.copy2(path, bak)
                print(f"backup:  {path} -> {bak}")
    write_jsonl(jsonl_path, records)
    write_json_array(json_path, records)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "patch_report.md").write_text(report_md, encoding="utf-8")
    write_json_obj(args.report_dir / "patch_report.json", report_json)
    print(f"\nwrote:   {jsonl_path}")
    print(f"wrote:   {json_path}")
    print(f"wrote:   {args.report_dir / 'patch_report.md'}")
    print(f"wrote:   {args.report_dir / 'patch_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
