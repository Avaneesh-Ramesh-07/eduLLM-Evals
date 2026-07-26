"""Scrape APUSH essays - Document-Based Questions (DBQ) and Long Essay Questions
(LEQ) - into scenario + rubric JSONL.

Both essay types share ONE rubric grammar in the Scoring Guidelines PDF: a table
of scoring sub-rows, each anchored by

    0 points
    Does not meet the criteria for one point.
    1 point
    <criterion for the first point>
    [2 points
    <criterion for the second point>]        <- only for polytomous sub-rows

`parse_essay_rows` walks that grammar once for either type. The only per-type
differences are (a) the skill taxonomy and (b) DBQ carries seven source
documents in conversation_context while LEQ carries none.

Polytomous -> binary: any sub-row with a "2 points" level is split into two
*linked* binary criteria (level 2 builds on level 1 - e.g. "supports an argument
using >=6 documents" entails "uses >=3 documents"). The pair reference each other
via `linked_criteria`, exactly the split the DBQ Evidence row requires.

Run with the venv python that has pypdf:
    llm-from-scratch/Scripts/python.exe scripts/scrape_apush_essays.py
"""
from __future__ import annotations

import json
import re

import apush_common as ac
from apush_common import BULLET, clean_ws

OUT_DIR = ac.CACHE.parents[1] / "data" / "AP_IB" / "APUSH"

# skill taxonomies (also the q_mapping key sets)
DBQ_SKILLS = [
    "Thesis/Claim",
    "Contextualization",
    "Evidence from Documents",
    "Evidence beyond Documents",
    "Analysis and Reasoning Sourcing",
    "Analysis and Reasoning Complex Understanding",
]
LEQ_SKILLS = [
    "Thesis/Claim",
    "Contextualization",
    "Evidence",
    "Historical Reasoning",
    "Complex Understanding",
]

def _ws_tolerant(phrase: str) -> re.Pattern:
    """Compile `phrase` so ANY whitespace (incl. none) may sit between its chars.

    PDF extraction of justified table cells breaks words mid-token in
    unpredictable places - "Does not  meet" (2023 Row A), "f\\nor" (2025 lone
    letter), "cri\\nteria" (2021 Evidence). A per-word `\\s+` pattern still misses
    the last case because the break lands INSIDE a word. Since this anchor is a
    fixed, 40-char, effectively-unique sentence, joining every character with
    `\\s*` matches it regardless of where the breaks fall, with no realistic
    false-positive risk - far safer than teaching ac.dejoin to rejoin multi-char
    fragments (which is ambiguous with dropped-space word boundaries).
    """
    chars = [re.escape(c) for c in phrase if not c.isspace()]
    return re.compile(r"\s*".join(chars), re.I | re.S)


# each scoring sub-row opens with this fixed sentence.
ANCHOR = _ws_tolerant("0 points Does not meet the criteria for one point")
# lines that are page/table furniture, not a scoring category name
_SKIP_LINE = re.compile(
    r"^\s*(?:[\(\[]\s*0\s*-\s*\d+\s*points?\s*[\)\]]|[\(\[]\s*continued\s*[\)\]]"
    r"|Row [A-D]\b|Reporting|Category|(?:Category\s+)?Scoring Criteria"
    r"|Decision Rules and Scoring Notes|.*College Board.*|.*Scoring Guidelines.*"
    r"|United States History)\s*$",
    re.I,
)
# boundaries that separate example/explanation blocks within a sub-row
BOUNDARY = re.compile(
    r"(Examples?\b[^:]{0,220}:"
    r"|Demonstrating complex understanding might include[^:]{0,140}:"
    r"|Using a historical reasoning process[^:]{0,140}:"
    r"|Responses that [^:]{0,90}:"
    r"|Additional Notes:"
    r"|Decision Rules and Scoring Notes"
    r"|Row [A-D]\b)",
    re.S,
)


def _category(text_before: str) -> str:
    """Walk backward from a sub-row anchor to the scoring-category name."""
    lines = text_before.splitlines()
    i = len(lines) - 1
    while i >= 0 and (not lines[i].strip() or _SKIP_LINE.match(lines[i])):
        i -= 1
    run = []
    while i >= 0 and lines[i].strip() and not _SKIP_LINE.match(lines[i]):
        run.append(lines[i])
        i -= 1
    return clean_ws(" ".join(reversed(run))).rstrip(":")


def _is_positive(header: str) -> bool:
    low = header.lower()
    if "do not earn" in low:  # guard MUST precede "that earn": negatives contain it
        return False
    return (
        "example" in low
        or "demonstrating complex understanding might include" in low
        or "using a historical reasoning process" in low
        # "Responses that earn this point:" / "... earn N point(s):" - the canonical
        # scoring criteria. In 2024-25 SGs this is the ONLY source of the level-2
        # complex-understanding examples (the 2023 "Demonstrating..." block was cut).
        or "that earn" in low
    )


def _section_level(header: str) -> int:
    """Which point-level a positive example block supports (for polytomous rows)."""
    low = header.lower()
    if (
        re.search(r"\b2\s*points?\b", low)
        or "support" in low
        or "demonstrating complex understanding" in low
    ):
        return 2
    return 1


def _sections(span: str) -> list[tuple[str, int, list[str]]]:
    """Return [(header, level, bullets)] for the positive example blocks in a span."""
    bnds = list(BOUNDARY.finditer(span))
    out = []
    for k, b in enumerate(bnds):
        header = clean_ws(b.group(0))
        if not _is_positive(header):
            continue
        end = bnds[k + 1].start() if k + 1 < len(bnds) else len(span)
        zone = span[b.end() : end]
        bullets = [clean_ws(x) for x in zone.split(BULLET)[1:]]
        bullets = [x for x in bullets if x]
        out.append((header, _section_level(header), bullets))
    return out


def parse_essay_rows(region: str, skill_of) -> list[dict]:
    """Parse one essay region into criterion dicts (before id/link assignment).

    Each returned dict: {criterion, expected_evidence, primary_skill, group}.
    `group` marks the two halves of a split polytomous row so callers can link
    them; None for standalone binary criteria.
    """
    anchors = list(ANCHOR.finditer(region))
    if not anchors:
        raise ValueError("no scoring sub-rows found")
    bounds = [a.start() for a in anchors] + [len(region)]

    criteria: list[dict] = []
    group_seq = 0
    for i, a in enumerate(anchors):
        category = _category(region[: a.start()])
        span = region[a.start() : bounds[i + 1]]
        head = span.split("Decision Rules and Scoring Notes", 1)[0]
        m1 = re.search(r"\b1\s*point\b\s*(.+?)(?:\b2\s*points\b|\Z)", head, re.S)
        m2 = re.search(r"\b2\s*points\b\s*(.+)\Z", head, re.S)
        desc1 = clean_ws(m1.group(1)) if m1 else ""
        desc2 = clean_ws(m2.group(1)) if m2 else None
        secs = _sections(span)

        if desc2:  # polytomous -> two linked binary criteria
            group_seq += 1
            ev1 = [b for _, lv, bs in secs if lv == 1 for b in bs]
            ev2 = [b for _, lv, bs in secs if lv == 2 for b in bs]
            criteria.append(
                {
                    "criterion": desc1,
                    "expected_evidence": ev1,
                    "primary_skill": skill_of(category, 1),
                    "group": group_seq,
                }
            )
            criteria.append(
                {
                    "criterion": desc2,
                    "expected_evidence": ev2,
                    "primary_skill": skill_of(category, 2),
                    "group": group_seq,
                }
            )
        else:  # standalone binary criterion
            ev = [b for _, _, bs in secs for b in bs]
            criteria.append(
                {
                    "criterion": desc1,
                    "expected_evidence": ev,
                    "primary_skill": skill_of(category, 1),
                    "group": None,
                }
            )
    return criteria


def dbq_skill(category: str, level: int) -> str:
    c = category.lower()
    if "thesis" in c:
        return "Thesis/Claim"
    if "context" in c:
        return "Contextualization"
    if "evidence beyond" in c:  # check "beyond" before "from"
        return "Evidence beyond Documents"
    if "evidence from" in c or "evidence" in c and "document" in c:
        return "Evidence from Documents"
    if "sourcing" in c:
        return "Analysis and Reasoning Sourcing"
    if "complex" in c:
        return "Analysis and Reasoning Complex Understanding"
    raise ValueError(f"unmapped DBQ category: {category!r}")


def leq_skill(category: str, level: int) -> str:
    c = category.lower()
    if "thesis" in c:
        return "Thesis/Claim"
    if "context" in c:
        return "Contextualization"
    if "evidence" in c:
        return "Evidence"
    if "analysis" in c or "reasoning" in c:
        return "Historical Reasoning" if level == 1 else "Complex Understanding"
    raise ValueError(f"unmapped LEQ category: {category!r}")


def _prompt_of(region: str) -> str:
    """The essay prompt sits between the General Scoring Notes and Row A."""
    m = re.search(r"described below\.\s*(.+?)(?:\bRow [A-D]\b|\b0\s*points\b)", region, re.S)
    return clean_ws(m.group(1)) if m else ""


def dbq_documents(frq_path) -> list[dict]:
    """The seven DBQ source documents from the FRQ PDF, in order."""
    full = ac.denoise_frq(ac.pdf_text(frq_path))
    start = full.find("Document 1")
    tail = full[start:]
    end = re.search(r"END OF DOCUMENTS|Question 2, 3, or 4", tail)
    region = tail[: end.start()] if end else tail
    parts = re.split(r"\bDocument (\d+)\b", region)
    docs = []
    for i in range(1, len(parts) - 1, 2):
        num, chunk = parts[i], clean_ws(parts[i + 1])
        # A DBQ always has 7 documents; some are purely visual (map/cartoon/photo/
        # ad) whose text - sometimes even the Source line - lives inside the image
        # and does not extract. Keep the slot with an explicit placeholder rather
        # than silently yielding a short DBQ (e.g. 2021 Document 6).
        if not chunk:
            chunk = "[Visual source - image/graphic with no machine-extractable text.]"
        docs.append({"role": "source", "content": f"Document {num}: {chunk}"})
    return docs


def _essay_records(region, sid, qtype, src, skill_of, skills, prompt, context):
    """Build (scenario, [rubrics]) for one essay from its parsed region."""
    parsed = parse_essay_rows(region, skill_of)
    crit_ids = [f"{sid}_c{j:02d}" for j in range(1, len(parsed) + 1)]

    # map group -> the criterion_ids that make it up (for linked_criteria)
    groups: dict[int, list[str]] = {}
    for cid, c in zip(crit_ids, parsed):
        if c["group"] is not None:
            groups.setdefault(c["group"], []).append(cid)

    rubrics = []
    for cid, c in zip(crit_ids, parsed):
        linked = [x for x in groups.get(c["group"], []) if x != cid] if c["group"] else []
        skill = c["primary_skill"]
        rubrics.append(
            {
                "criterion_id": cid,
                "scenario_id": sid,
                "criterion": c["criterion"],
                "expected_evidence": c["expected_evidence"],
                "scoring_type": "binary",
                "score_anchors": None,
                "linked_criteria": linked,
                "primary_skill": skill,
                "q_mapping": {s: (1 if s == skill else 0) for s in skills},
                "q_rationale": (
                    f'The criterion assesses the "{skill}" skill for the {qtype}.'
                ),
                "difficulty_uncalibrated": None,
                "discrimination_uncalibrated": None,
                "irt_params_uncalibrated": None,
                "criticality": "critical",
                "objectivity": "objective",
                "explicitness": "explicit",
                "source": src,
                "status": "approved",
                "version": "1.0",
            }
        )

    scenario = {
        "scenario_id": sid,
        "use_case": qtype,
        "subject": "US History",
        "grade_band": "9-12",
        "modality": "text",
        "prompt": prompt,
        "conversation_context": context,
        # no full-credit model essay is published in the source materials
        "reference_solution": "N/A",
        "criterion_ids": crit_ids,
        "source": src,
        "split": "calibration",
        "version": "1.0",
    }
    return scenario, rubrics


def build_dbq():
    scenarios, rubrics = [], []
    for n, form in enumerate(ac.current_forms(), start=1):
        src = form.sg_src
        sg = ac.dejoin(ac.denoise_sg(ac.pdf_text(form.sg_path)))
        m = re.search(r"Question\s*1:\s*Document-Based Question", sg)
        end = re.search(r"Document Summaries|Question\s*[234]:\s*Long Essay", sg[m.end():])
        region = sg[m.start() : m.end() + end.start()] if end else sg[m.start():]
        prompt = _prompt_of(region)
        docs = dbq_documents(form.frq_path)
        sid = f"ap_ush_dbq_{n:03d}"
        sc, ru = _essay_records(
            region, sid, "DBQ", src, dbq_skill, DBQ_SKILLS, prompt, docs
        )
        scenarios.append(sc)
        rubrics.extend(ru)
    return scenarios, rubrics


def build_leq():
    scenarios, rubrics = [], []
    counter = 0
    for form in ac.current_forms():
        src = form.sg_src
        sg = ac.dejoin(ac.denoise_sg(ac.pdf_text(form.sg_path)))
        heads = list(re.finditer(r"Question\s*([234]):\s*Long Essay Question", sg))
        bounds = [h.start() for h in heads] + [len(sg)]
        for i, h in enumerate(heads):
            counter += 1
            region = sg[h.start() : bounds[i + 1]]
            prompt = _prompt_of(region)
            sid = f"ap_ush_leq_{counter:03d}"
            sc, ru = _essay_records(
                region, sid, "LEQ", src, leq_skill, LEQ_SKILLS, prompt, []
            )
            scenarios.append(sc)
            rubrics.extend(ru)
    return scenarios, rubrics


def _write(name, rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / name).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _summary(tag, scenarios, rubrics):
    print(f"\n==== {tag} ====")
    print(f"scenarios: {len(scenarios)}   rubrics: {len(rubrics)}")
    per = [len(s["criterion_ids"]) for s in scenarios]
    print(f"criteria per scenario: min={min(per)} max={max(per)}")
    linked = sum(1 for r in rubrics if r["linked_criteria"])
    print(f"criteria with linked_criteria: {linked}")
    skills = {}
    for r in rubrics:
        skills[r["primary_skill"]] = skills.get(r["primary_skill"], 0) + 1
    print("skill distribution:", skills)
    ev = [len(r["expected_evidence"]) for r in rubrics]
    print(f"expected_evidence per criterion: min={min(ev)} max={max(ev)}")
    if tag == "DBQ":
        print("documents in first scenario:", len(scenarios[0]["conversation_context"]))


def main():
    dbq_s, dbq_r = build_dbq()
    leq_s, leq_r = build_leq()
    _write("dbq_scenarios.jsonl", dbq_s)
    _write("dbq_rubrics.jsonl", dbq_r)
    _write("leq_scenarios.jsonl", leq_s)
    _write("leq_rubrics.jsonl", leq_r)
    _summary("DBQ", dbq_s, dbq_r)
    _summary("LEQ", leq_s, leq_r)
    print("\n--- sample DBQ rubric (Evidence-from-docs, linked) ---")
    linked_sample = next(r for r in dbq_r if r["linked_criteria"])
    print(json.dumps(linked_sample, indent=2, ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
