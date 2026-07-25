"""Prepare a small, blind human audit of the finalized TutorBench Q-matrix.

The default design fits a 15--20 minute review window for three reviewers:

* 10 criteria are reviewed by A, B, and C;
* 5 criteria are reviewed by A and B;
* 5 criteria are reviewed by A and C;
* 5 criteria are reviewed by B and C.

This produces 25 unique criteria, 20 reviews per person, at least two ratings per
criterion, and a 10-criterion common block for three-rater agreement.

Sampling is deterministic and deliberately emphasizes labels that changed
between the generator output and the finalized, LLM-verified Q-matrix. It also
includes stable positive labels for every skill and stable all-zero rows to
audit false negatives.

The reviewer HTML files are blind: they do not contain the generated mapping,
the final mapping, the LLM rationale, or the sampling stratum. A separate
coordinator-only manifest retains those fields for analysis after review.

Usage:
    python scripts/prepare_qmatrix_human_review.py
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("content", "diagnosis", "scaffolding")
REVIEWERS = ("A", "B", "C")
SAMPLING_VERSION = "qmatrix-human-review-v1"

SKILL_DEFINITIONS = {
    "content": (
        "Accurate subject-matter knowledge, calculations, facts, concepts, or "
        "domain reasoning needed to satisfy the criterion."
    ),
    "diagnosis": (
        "Identifying or reasoning about the student's specific understanding, "
        "error, misconception, or likely source of difficulty."
    ),
    "scaffolding": (
        "Structuring guidance that helps the student make progress: hints, "
        "questions, intermediate steps, or appropriately withheld support."
    ),
}


@dataclass(frozen=True)
class Target:
    kind: str
    skill: str | None = None

    @property
    def label(self) -> str:
        return self.kind if self.skill is None else f"{self.kind}:{self.skill}"


# Ten shared rows: both verifier-change directions for each skill, one stable
# positive per skill, and one stable all-zero row.
CORE_TARGETS = [
    Target("changed_0_to_1", "content"),
    Target("changed_0_to_1", "diagnosis"),
    Target("changed_0_to_1", "scaffolding"),
    Target("changed_1_to_0", "content"),
    Target("changed_1_to_0", "diagnosis"),
    Target("changed_1_to_0", "scaffolding"),
    Target("stable_positive", "content"),
    Target("stable_positive", "diagnosis"),
    Target("stable_positive", "scaffolding"),
    Target("stable_all_zero"),
]

# Five rows in each pairwise block. Across the three blocks, change directions,
# skills, stable positives, and all-zero rows remain balanced.
PAIR_TARGETS = {
    "AB": [
        Target("changed_0_to_1", "content"),
        Target("changed_1_to_0", "diagnosis"),
        Target("stable_positive", "scaffolding"),
        Target("stable_positive", "content"),
        Target("stable_all_zero"),
    ],
    "AC": [
        Target("changed_0_to_1", "diagnosis"),
        Target("changed_1_to_0", "scaffolding"),
        Target("stable_positive", "content"),
        Target("stable_positive", "diagnosis"),
        Target("stable_all_zero"),
    ],
    "BC": [
        Target("changed_0_to_1", "scaffolding"),
        Target("changed_1_to_0", "content"),
        Target("stable_positive", "diagnosis"),
        Target("stable_positive", "scaffolding"),
        Target("stable_all_zero"),
    ],
}


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def normalized_q(record: dict) -> dict[str, int]:
    q = record.get("q_mapping") or {}
    return {skill: int(q.get(skill, 0)) for skill in SKILLS}


def enrich_candidates(generated: list[dict], finalized: list[dict]) -> list[dict]:
    generated_by_id = {row["criterion_id"]: row for row in generated}
    candidates: list[dict] = []
    for final in finalized:
        cid = final["criterion_id"]
        original = generated_by_id.get(cid)
        if original is None:
            continue
        q_generated = normalized_q(original)
        q_final = normalized_q(final)
        candidates.append(
            {
                "criterion_id": cid,
                "generated": original,
                "final": final,
                "q_generated": q_generated,
                "q_final": q_final,
                "changed": q_generated != q_final,
            }
        )
    return sorted(candidates, key=lambda row: row["criterion_id"])


def matches(candidate: dict, target: Target) -> bool:
    before = candidate["q_generated"]
    after = candidate["q_final"]
    skill = target.skill
    if target.kind == "changed_0_to_1":
        return bool(candidate["changed"] and before[skill] == 0 and after[skill] == 1)
    if target.kind == "changed_1_to_0":
        return bool(candidate["changed"] and before[skill] == 1 and after[skill] == 0)
    if target.kind == "stable_positive":
        return bool(not candidate["changed"] and after[skill] == 1)
    if target.kind == "stable_all_zero":
        return bool(not candidate["changed"] and sum(after.values()) == 0)
    raise ValueError(f"unknown target kind: {target.kind}")


def fallback_predicates(target: Target) -> list[Callable[[dict], bool]]:
    """Broaden a sparse stratum while retaining its main audit purpose."""
    skill = target.skill
    if target.kind.startswith("changed_"):
        return [
            lambda row: row["changed"] and row["q_final"][skill] == int(target.kind.endswith("1")),
            lambda row: row["changed"],
        ]
    if target.kind == "stable_positive":
        return [
            lambda row: row["q_final"][skill] == 1,
            lambda row: sum(row["q_final"].values()) > 0,
        ]
    return [
        lambda row: sum(row["q_final"].values()) == 0,
        lambda row: sum(row["q_final"].values()) < len(SKILLS),
    ]


def choose_one(
    candidates: list[dict], target: Target, used: set[str], rng: random.Random
) -> dict:
    predicates = [lambda row: matches(row, target), *fallback_predicates(target)]
    for predicate in predicates:
        pool = [
            row for row in candidates
            if row["criterion_id"] not in used and predicate(row)
        ]
        if pool:
            chosen = pool[rng.randrange(len(pool))]
            used.add(chosen["criterion_id"])
            return chosen
    raise ValueError(f"no unused Q-matrix criterion available for target {target.label}")


def select_sample(
    generated: list[dict], finalized: list[dict], seed: int
) -> dict[str, list[dict]]:
    candidates = enrich_candidates(generated, finalized)
    if len(candidates) < 25:
        raise ValueError(f"need at least 25 matched rubric rows; found {len(candidates)}")
    rng = random.Random(seed)
    used: set[str] = set()

    groups: dict[str, list[dict]] = {"CORE": [], "AB": [], "AC": [], "BC": []}
    for target in CORE_TARGETS:
        row = choose_one(candidates, target, used, rng)
        groups["CORE"].append({**row, "sampling_target": target.label})
    for group in ("AB", "AC", "BC"):
        for target in PAIR_TARGETS[group]:
            row = choose_one(candidates, target, used, rng)
            groups[group].append({**row, "sampling_target": target.label})
    return groups


def reviewer_assignments(groups: dict[str, list[dict]]) -> dict[str, list[dict]]:
    assignments = {
        "A": groups["CORE"] + groups["AB"] + groups["AC"],
        "B": groups["CORE"] + groups["AB"] + groups["BC"],
        "C": groups["CORE"] + groups["AC"] + groups["BC"],
    }
    for reviewer, rows in assignments.items():
        if len(rows) != 20 or len({r["criterion_id"] for r in rows}) != 20:
            raise AssertionError(f"reviewer {reviewer} does not have 20 unique criteria")
    return assignments


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def _html_text(value: object) -> str:
    return html.escape(_text(value)).replace("\n", "<br>")


def _context_html(context: list[dict] | None) -> str:
    if not context:
        return "<em>No prior conversation context.</em>"
    parts = []
    for turn in context:
        role = html.escape(_text(turn.get("role", "turn")))
        content = _html_text(turn.get("content", ""))
        parts.append(f"<p><strong>{role}:</strong> {content}</p>")
    return "".join(parts)


def render_item(index: int, row: dict, scenario: dict) -> str:
    rubric = row["final"]
    cid = html.escape(row["criterion_id"])
    skill_inputs = []
    for skill in SKILLS:
        title = skill.capitalize()
        definition = html.escape(SKILL_DEFINITIONS[skill])
        skill_inputs.append(
            f"""
            <fieldset class="skill">
              <legend>{title}</legend>
              <div class="definition">{definition}</div>
              <label><input type="radio" name="{cid}__{skill}" value="1"> 1 — required</label>
              <label><input type="radio" name="{cid}__{skill}" value="0"> 0 — not required</label>
            </fieldset>
            """
        )
    expected = rubric.get("expected_evidence") or []
    expected_html = (
        "<ul>" + "".join(f"<li>{_html_text(item)}</li>" for item in expected) + "</ul>"
        if expected else "<em>No separate expected-evidence field.</em>"
    )
    return f"""
    <section class="item" data-criterion-id="{cid}">
      <h2>{index}. Criterion <code>{cid}</code></h2>
      <details>
        <summary>Scenario context</summary>
        <h3>Prompt</h3><p>{_html_text(scenario.get('prompt', ''))}</p>
        <h3>Conversation context</h3>{_context_html(scenario.get('conversation_context'))}
        <h3>Reference solution</h3><p>{_html_text(scenario.get('reference_solution', ''))}</p>
      </details>
      <div class="criterion"><strong>Criterion:</strong> {_html_text(rubric.get('criterion', ''))}</div>
      <div class="evidence"><strong>Expected evidence:</strong> {expected_html}</div>
      <p class="instruction">Mark 1 only when this criterion cannot reasonably be satisfied without the skill.</p>
      <div class="skills">{''.join(skill_inputs)}</div>
      <label>Primary skill
        <select name="{cid}__primary">
          <option value="">Select…</option>
          <option value="content">Content</option>
          <option value="diagnosis">Diagnosis</option>
          <option value="scaffolding">Scaffolding</option>
          <option value="none">None</option>
        </select>
      </label>
      <label>Confidence
        <select name="{cid}__confidence">
          <option value="">Select…</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </label>
      <label>Optional note
        <textarea name="{cid}__notes" rows="2" placeholder="Only needed for ambiguity or a difficult decision"></textarea>
      </label>
    </section>
    """


def render_html(reviewer: str, rows: list[dict], scenarios: dict[str, dict]) -> str:
    cards = []
    for index, row in enumerate(rows, 1):
        scenario_id = row["final"]["scenario_id"]
        cards.append(render_item(index, row, scenarios[scenario_id]))
    storage_key = f"{SAMPLING_VERSION}-{reviewer}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Q-matrix human review — Reviewer {reviewer}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1050px; margin: 0 auto; padding: 24px; line-height: 1.45; color: #17202a; }}
.sticky {{ position: sticky; top: 0; background: white; border-bottom: 1px solid #bbb; padding: 10px 0; z-index: 3; }}
.item {{ border: 1px solid #bbb; border-radius: 10px; margin: 22px 0; padding: 18px; background: #fafafa; }}
.criterion {{ font-size: 1.08rem; background: #eef5ff; padding: 12px; border-radius: 6px; margin: 12px 0; }}
.evidence {{ margin: 10px 0; }}
.skills {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
.skill {{ border: 1px solid #aaa; border-radius: 6px; }}
.skill label {{ display: block; margin: 8px 0; }}
.definition {{ min-height: 7em; font-size: .9rem; color: #39434d; }}
select, textarea {{ display: block; width: 100%; max-width: 700px; margin: 4px 0 12px; padding: 6px; }}
button {{ padding: 9px 15px; font-weight: 600; cursor: pointer; }}
.instruction {{ font-weight: 600; }}
@media (max-width: 760px) {{ .skills {{ grid-template-columns: 1fr; }} .definition {{ min-height: auto; }} }}
</style>
</head>
<body>
<h1>Q-matrix human review — Reviewer {reviewer}</h1>
<p>You have 20 criteria. Budget about 45–60 seconds each. Review the criterion itself—not whether a particular tutor answered it correctly.</p>
<p><strong>Decision rule:</strong> assign 1 only if a tutor cannot reasonably satisfy the criterion without demonstrating that skill. Otherwise assign 0. Do not infer that every desirable tutoring behavior is required.</p>
<div class="sticky"><strong>Progress:</strong> <span id="progress">0 / 20 complete</span> &nbsp; <button type="button" id="export">Validate and download CSV</button></div>
<form id="review-form">{''.join(cards)}</form>
<script>
const reviewer = {json.dumps(reviewer)};
const storageKey = {json.dumps(storage_key)};
const skills = ["content", "diagnosis", "scaffolding"];
const form = document.getElementById("review-form");
function state() {{
  const out = {{}};
  new FormData(form).forEach((value, key) => out[key] = value);
  return out;
}}
function save() {{ localStorage.setItem(storageKey, JSON.stringify(state())); updateProgress(); }}
function restore() {{
  const saved = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
  Object.entries(saved).forEach(([name, value]) => {{
    const fields = form.elements[name];
    if (!fields) return;
    if (fields.length && fields[0] && fields[0].type === "radio") {{
      [...fields].forEach(field => field.checked = field.value === value);
    }} else {{ fields.value = value; }}
  }});
}}
function complete(card) {{
  const cid = card.dataset.criterionId;
  return skills.every(skill => form.querySelector(`input[name="${{cid}}__${{skill}}"]:checked`))
    && form.elements[`${{cid}}__primary`].value
    && form.elements[`${{cid}}__confidence`].value;
}}
function updateProgress() {{
  const cards = [...document.querySelectorAll(".item")];
  document.getElementById("progress").textContent = `${{cards.filter(complete).length}} / ${{cards.length}} complete`;
}}
function csvCell(value) {{ return '"' + String(value ?? "").replaceAll('"', '""') + '"'; }}
document.getElementById("export").addEventListener("click", () => {{
  const cards = [...document.querySelectorAll(".item")];
  const missing = cards.filter(card => !complete(card));
  if (missing.length) {{ alert(`Please finish all required fields. ${{missing.length}} criteria remain incomplete.`); return; }}
  const header = ["reviewer", "criterion_id", "content", "diagnosis", "scaffolding", "primary_skill", "confidence", "notes"];
  const rows = [header];
  cards.forEach(card => {{
    const cid = card.dataset.criterionId;
    rows.push([reviewer, cid,
      form.querySelector(`input[name="${{cid}}__content"]:checked`).value,
      form.querySelector(`input[name="${{cid}}__diagnosis"]:checked`).value,
      form.querySelector(`input[name="${{cid}}__scaffolding"]:checked`).value,
      form.elements[`${{cid}}__primary`].value,
      form.elements[`${{cid}}__confidence`].value,
      form.elements[`${{cid}}__notes`].value]);
  }});
  const csv = rows.map(row => row.map(csvCell).join(",")).join("\n") + "\n";
  const url = URL.createObjectURL(new Blob([csv], {{type: "text/csv;charset=utf-8"}}));
  const link = document.createElement("a"); link.href = url; link.download = `qmatrix_review_${{reviewer}}.csv`; link.click();
  URL.revokeObjectURL(url);
}});
form.addEventListener("change", save); form.addEventListener("input", save); restore(); updateProgress();
</script>
</body>
</html>
"""


CSV_FIELDS = [
    "reviewer", "criterion_id", "content", "diagnosis", "scaffolding",
    "primary_skill", "confidence", "notes",
]


def write_blank_csv(path: Path, reviewer: str, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({"reviewer": reviewer, "criterion_id": row["criterion_id"]})


def group_reviewers(group: str) -> list[str]:
    return list(REVIEWERS) if group == "CORE" else list(group)


def write_outputs(
    out_dir: Path,
    groups: dict[str, list[dict]],
    scenarios: dict[str, dict],
    seed: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    assignments = reviewer_assignments(groups)
    for reviewer, rows in assignments.items():
        (out_dir / f"reviewer_{reviewer}.html").write_text(
            render_html(reviewer, rows, scenarios), encoding="utf-8"
        )
        write_blank_csv(out_dir / f"reviewer_{reviewer}_blank.csv", reviewer, rows)

    criteria = []
    for group, rows in groups.items():
        for row in rows:
            final = row["final"]
            criteria.append(
                {
                    "criterion_id": row["criterion_id"],
                    "scenario_id": final["scenario_id"],
                    "review_group": group,
                    "reviewers": group_reviewers(group),
                    "sampling_target": row["sampling_target"],
                    "generated_q_mapping": row["q_generated"],
                    "final_q_mapping": row["q_final"],
                    "final_primary_skill": final.get("primary_skill"),
                }
            )
    manifest = {
        "sampling_version": SAMPLING_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "blind_review": True,
        "n_unique_criteria": len(criteria),
        "reviews_per_reviewer": {r: len(rows) for r, rows in assignments.items()},
        "design": {"CORE": "ABC", "AB": "AB", "AC": "AC", "BC": "BC"},
        "criteria": criteria,
    }
    (out_dir / "coordinator_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "README.md").write_text(
        """# Q-matrix human review

Send each reviewer only their matching `reviewer_*.html` file. Do not send
`coordinator_manifest.json`; it contains the AI mappings and would unblind the review.

Each reviewer opens the HTML locally, completes 20 criteria, and clicks **Validate and
download CSV**. Progress is saved in that browser. The blank CSV is only a spreadsheet
fallback.

Return the three exported files as `qmatrix_review_A.csv`, `qmatrix_review_B.csv`, and
`qmatrix_review_C.csv`. Every sampled criterion has at least two ratings; the ten CORE
criteria have three ratings. Resolve disagreements only after all independent reviews are
complete.
""",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "data/scenarios.jsonl")
    parser.add_argument("--generated", type=Path, default=ROOT / "data/rubrics_qmatrix.jsonl")
    parser.add_argument("--final", type=Path, default=ROOT / "data/rubrics_qmatrix_final.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "qmatrix_human_review")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    scenarios = {row["scenario_id"]: row for row in read_jsonl(args.scenarios)}
    groups = select_sample(read_jsonl(args.generated), read_jsonl(args.final), args.seed)
    missing_scenarios = sorted(
        row["final"]["scenario_id"]
        for rows in groups.values() for row in rows
        if row["final"]["scenario_id"] not in scenarios
    )
    if missing_scenarios:
        raise ValueError(f"sample references missing scenarios: {missing_scenarios}")
    write_outputs(args.out, groups, scenarios, args.seed)

    assignments = reviewer_assignments(groups)
    print(f"wrote 25-criterion blind review to {args.out}")
    for reviewer in REVIEWERS:
        print(f"reviewer {reviewer}: {len(assignments[reviewer])} criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
