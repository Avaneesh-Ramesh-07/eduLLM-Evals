"""Scrape AP English Language & Composition Question 1 (Synthesis Essay) into
scenario + rubric JSONL.

Each Q1 -> 1 scenario (prompt + the six source documents) and 10 binary rubric
criteria derived from the three scoring rows:

  Row A  Thesis        (0-1) -> 1 binary criterion
  Row B  Evidence AND Commentary (0-4)  <- the polytomous row
  Row C  Sophistication (0-1) -> 1 binary criterion

Row B is split TWICE. First the two graded dimensions are separated
(EVIDENCE vs COMMENTARY), then each dimension's 4-level ladder becomes 4 binary
criteria that BUILD on one another:

  EVIDENCE:   E1 <- E2 <- E3 <- E4     (mutually `linked_criteria`)
  COMMENTARY: C1 <- C2 <- C3 <- C4     (mutually `linked_criteria`)

A response scoring 3/4 automatically earns E1-E3 and C1-C3. "Negative
specificity" - a level clause describing a *deficiency* that caps the score
(e.g. level-2 commentary "...but no line of reasoning is established, or the
line of reasoning is faulty") - is stripped from the criterion text so a
higher-scoring response is not wrongly denied the lower rung; the stripped
clause is preserved verbatim in `q_rationale` for provenance.

Two source PDFs per set (both first-party on apcentral, 2023-2025):
  * FRQ - carries the Q1 prompt and the six synthesis sources.
  * SG  - carries the Row A/B/C scoring criteria.

Run with the venv python that has pypdf:
    llm-from-scratch/Scripts/python.exe scripts/scrape_aplang_q1.py
"""
from __future__ import annotations

import json
import re

import apush_common as ac
from apush_common import BULLET, clean_ws

OUT_DIR = ac.CACHE.parents[1] / "data" / "AP_IB" / "AP_Lang"

# --- source manifest: 2023-2025, both sets, first-party -----------------------
_MEDIA = "https://apcentral.collegeboard.org/media/pdf/"
SETS = [(2023, 1), (2023, 2), (2024, 1), (2024, 2), (2025, 1), (2025, 2)]


def sg_url(year: int, s: int) -> str:
    return f"{_MEDIA}ap{str(year)[2:]}-sg-english-language-set-{s}.pdf"


def frq_url(year: int, s: int) -> str:
    return f"{_MEDIA}ap{str(year)[2:]}-frq-english-language-set-{s}.pdf"


# q_mapping skill keys (the four AP Lang scored dimensions)
QSKILLS = ["Thesis", "Evidence", "Commentary", "Sophistication"]


# --- FRQ: prompt + six sources ------------------------------------------------
def _denoise_frq(full: str) -> str:
    keep = []
    for line in full.splitlines():
        s = line.strip()
        if s.startswith("Visit College Board") or "collegeboard.org" in s:
            continue
        if re.match(r"AP ENGLISH LANGUAGE AND COMPOSITION.*FREE-RESPONSE", s, re.I):
            continue
        if re.fullmatch(r"[©\s]*\d{4}\s+College Board\.?", s):
            continue
        if s.startswith("GO ON TO THE NEXT PAGE"):
            continue
        if re.fullmatch(r"\d{1,3}", s):
            continue
        keep.append(line)
    return "\n".join(keep)


def q1_prompt_and_sources(frq_path) -> tuple[str, list[dict]]:
    """Return (prompt, [source dicts]) for the Synthesis question of one FRQ."""
    full = _denoise_frq(ac.pdf_text(frq_path))
    doc_a = re.search(r"\nSource A\s*\n", full)
    if not doc_a:
        raise ValueError(f"{frq_path.name}: no 'Source A' document start found")

    # prompt: the numbered Q1 stem up to the first source document. Search through
    # doc_a.end() so the lookahead can see the whole "Source A \n" document marker.
    pm = re.search(r"(?m)^\s*1\.\s+(.+?)(?=\nSource A\s*\n)", full[: doc_a.end()], re.S)
    if not pm:
        raise ValueError(f"{frq_path.name}: could not isolate Q1 prompt")
    prompt = clean_ws(pm.group(1))

    # sources: from the Source A document start to the Question 2 stem
    q2 = re.search(r"(?m)^\s*2\.\s", full[doc_a.start():])
    region = full[doc_a.start():][: q2.start()] if q2 else full[doc_a.start():]
    starts = list(re.finditer(r"\nSource ([A-F])\s*\n", "\n" + region))
    bounds = [m.start() for m in starts] + [len(region) + 1]
    padded = "\n" + region
    sources = []
    for i, m in enumerate(starts):
        chunk = clean_ws(padded[m.start() : bounds[i + 1]])
        if len(chunk) < 40:  # a purely-visual source whose text did not extract
            chunk = f"Source {m.group(1)}: [Visual source - graphic with no machine-extractable text.]"
        sources.append({"role": "source", "content": chunk})
    if len(sources) != 6:
        raise ValueError(f"{frq_path.name}: found {len(sources)} sources, expected 6")
    return prompt, sources


# --- SG: Row A / Row B / Row C scoring criteria -------------------------------
def _one_point_text(row: str) -> str:
    """The '1 point' criterion sentence in a 0-1 row (Row A or Row C)."""
    m = re.search(r"\b1 point\b\s*(.+?)\bDecision Rules and Scoring Notes", row, re.S)
    if not m:
        raise ValueError("no '1 point' criterion found in 0-1 row")
    return clean_ws(m.group(1))


def _bullets(region: str) -> list[str]:
    return [b for b in (clean_ws(x) for x in region.split(BULLET)[1:]) if b]


def _rowA_examples(rowA: str) -> list[str]:
    """Example theses under Row A 'Examples that earn this point:'."""
    m = re.search(r"Examples that earn this point:(.+?)(?:Additional Notes:|\Z)", rowA, re.S)
    return _bullets(m.group(1)) if m else []


def _rowC_examples(rowC: str) -> list[str]:
    """Numbered ways to demonstrate sophistication under Row C."""
    m = re.search(r"by doing any of the following:(.+?)(?:Additional Notes:|\Z)", rowC, re.S)
    if not m:
        return []
    items = re.split(r"(?m)^\s*\d\.\s+", m.group(1))
    return [clean_ws(x) for x in items[1:] if clean_ws(x)]


def _neg_split(text: str) -> tuple[str, str | None]:
    """Split off trailing 'negative specificity' (a ', but <deficiency>' clause)."""
    m = re.search(r",?\s+but\s+", text)
    if not m:
        return text, None
    return text[: m.start()].rstrip(" ,.;"), text[m.end():].strip().rstrip(".")


def _rowB_levels(rowB: str) -> dict[int, dict]:
    """Parse Row B into {level: {evidence, commentary, com_neg, bullets}} for 1-4."""
    # criteria half sits between the '(0-4 points)' header and the Decision Rules
    after_hdr = rowB.split("(0-4 points)", 1)[-1]
    crit_half = re.split(r"Decision Rules and Scoring Notes", after_hdr, 1)[0]
    dr_half = re.split(r"Decision Rules and Scoring Notes", after_hdr, 1)
    dr_half = dr_half[1] if len(dr_half) > 1 else ""
    dr_half = re.split(r"Additional Notes:", dr_half, 1)[0]

    # level columns 1..4 (line-anchored so 'three of the provided sources' can't match)
    marks = list(re.finditer(r"(?m)^\s*([1-4]) points?\b", crit_half))
    cols = {}
    for i, mk in enumerate(marks):
        lvl = int(mk.group(1))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(crit_half)
        chunk = crit_half[mk.end() : end]
        ec = re.search(r"EVIDENCE:\s*(.+?)\s*AND\s*COMMENTARY:\s*(.+)", chunk, re.S)
        if not ec:
            raise ValueError(f"Row B level {lvl}: EVIDENCE/COMMENTARY not found")
        com_pos, com_neg = _neg_split(clean_ws(ec.group(2)))
        cols[lvl] = {
            "evidence": clean_ws(ec.group(1)),
            "commentary": com_pos,
            "com_neg": com_neg,
        }

    # decision-rule 'Typical responses that earn N points' bullets, per level
    dm = list(re.finditer(r"Typical responses that earn\s+([0-4]) points?:", dr_half))
    for i, mk in enumerate(dm):
        lvl = int(mk.group(1))
        end = dm[i + 1].start() if i + 1 < len(dm) else len(dr_half)
        if lvl in cols:
            cols[lvl]["bullets"] = _bullets(dr_half[mk.end() : end])
    for lvl in cols:
        cols[lvl].setdefault("bullets", [])
    if set(cols) != {1, 2, 3, 4}:
        raise ValueError(f"Row B levels found: {sorted(cols)} (expected 1-4)")
    return cols


def parse_q1_rubric(sg_path) -> dict:
    """Return {thesis, rowB(levels), sophistication} pieces for the SG's Q1."""
    full = ac.pdf_text(sg_path)
    a = full.find("Synthesis Essay")
    b = full.find("Rhetorical Analysis")
    region = full[a : b if b != -1 else len(full)]

    ra = region.find("Row A")
    rb = region.find("Row B")
    rc = region.find("Row C")
    if not (0 <= ra < rb < rc):
        raise ValueError(f"{sg_path.name}: Row A/B/C not in expected order")
    rowA, rowB, rowC = region[ra:rb], region[rb:rc], region[rc:]

    return {
        "thesis": {"criterion": _one_point_text(rowA), "examples": _rowA_examples(rowA)},
        "levels": _rowB_levels(rowB),
        "sophistication": {
            "criterion": _one_point_text(rowC),
            "examples": _rowC_examples(rowC),
        },
    }


# --- record assembly ----------------------------------------------------------
def _rubric(cid, sid, criterion, evidence, skill, qkey, linked, rationale,
            crit, obj, expl, src) -> dict:
    return {
        "criterion_id": cid,
        "scenario_id": sid,
        "criterion": criterion,
        "expected_evidence": evidence,
        "scoring_type": "binary",
        "score_anchors": None,
        "linked_criteria": linked,
        "primary_skill": skill,
        "q_mapping": {s: (1 if s == qkey else 0) for s in QSKILLS},
        "q_rationale": rationale,
        "difficulty_uncalibrated": None,
        "discrimination_uncalibrated": None,
        "irt_params_uncalibrated": None,
        "criticality": crit,
        "objectivity": obj,
        "explicitness": expl,
        "source": src,
        "status": "approved",
        "version": "1.0",
    }


def build_records():
    scenarios, rubrics = [], []
    counter = 0
    for year, s in SETS:
        f_src, s_src = frq_url(year, s), sg_url(year, s)
        prompt, sources = q1_prompt_and_sources(ac.download(f_src))
        rub = parse_q1_rubric(ac.download(s_src))

        counter += 1
        sid = f"ap_lang_q1_{counter:03d}"
        # id layout: c01 thesis | c02-c05 evidence | c06-c09 commentary | c10 soph
        cids = [f"{sid}_c{j:02d}" for j in range(1, 11)]
        ev_ids, com_ids = cids[1:5], cids[5:9]

        scenarios.append({
            "scenario_id": sid,
            "use_case": "q1",
            "subject": "English Language and Composition",
            "grade_band": "9-12",
            "modality": "text",
            "prompt": prompt,
            "conversation_context": sources,
            "reference_solution": "N/A",
            "criterion_ids": cids,
            "source": f_src,
            "split": "calibration",
            "version": "1.0",
        })

        # Row A - Thesis
        rubrics.append(_rubric(
            cids[0], sid, rub["thesis"]["criterion"], rub["thesis"]["examples"],
            "Thesis/Claim", "Thesis", [],
            "The criterion requires the student to respond to the prompt with a "
            "defensible thesis/claim that presents a clear position.",
            "critical", "objective", "explicit", s_src,
        ))

        # Row B - Evidence ladder (E1<-E2<-E3<-E4)
        for k, lvl in enumerate((1, 2, 3, 4)):
            linked = [x for x in ev_ids if x != ev_ids[k]]
            rubrics.append(_rubric(
                ev_ids[k], sid, f"EVIDENCE: {rub['levels'][lvl]['evidence']}",
                rub["levels"][lvl]["bullets"], "Evidence", "Evidence", linked,
                f"The criterion assesses the Evidence dimension of Row B at the "
                f"{lvl}-point level; it builds cumulatively on the lower Evidence rungs.",
                "critical", "objective", "explicit", s_src,
            ))

        # Row B - Commentary ladder (C1<-C2<-C3<-C4), negative specificity stripped
        for k, lvl in enumerate((1, 2, 3, 4)):
            linked = [x for x in com_ids if x != com_ids[k]]
            neg = rub["levels"][lvl]["com_neg"]
            rationale = (
                f"The criterion assesses the Commentary dimension of Row B at the "
                f"{lvl}-point level; it builds cumulatively on the lower Commentary rungs."
            )
            if neg:
                rationale += (
                    f" Negative specificity from the source rubric ('but {neg}') is "
                    "disregarded so a higher-scoring response still earns this rung."
                )
            rubrics.append(_rubric(
                com_ids[k], sid, f"COMMENTARY: {rub['levels'][lvl]['commentary']}",
                rub["levels"][lvl]["bullets"], "Commentary", "Commentary", linked,
                rationale, "critical", "subjective", "explicit", s_src,
            ))

        # Row C - Sophistication
        rubrics.append(_rubric(
            cids[9], sid, rub["sophistication"]["criterion"],
            rub["sophistication"]["examples"], "Sophistication", "Sophistication", [],
            "The criterion assesses sophistication of thought and/or a complex "
            "understanding of the rhetorical situation (Row C).",
            "minor", "subjective", "implicit", s_src,
        ))
    return scenarios, rubrics


def main():
    scenarios, rubrics = build_records()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "q1_scenarios.jsonl").open("w", encoding="utf-8") as f:
        for r in scenarios:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (OUT_DIR / "q1_rubrics.jsonl").open("w", encoding="utf-8") as f:
        for r in rubrics:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"scenarios: {len(scenarios)}   rubrics: {len(rubrics)}")
    per = [len(s["criterion_ids"]) for s in scenarios]
    print(f"criteria per scenario: min={min(per)} max={max(per)} (expect 10)")
    src_ctx = [len(s["conversation_context"]) for s in scenarios]
    print(f"sources per scenario: min={min(src_ctx)} max={max(src_ctx)} (expect 6)")
    linked = sum(1 for r in rubrics if r["linked_criteria"])
    print(f"criteria with linked_criteria: {linked} (expect 8/scenario = {8*len(scenarios)})")
    skills = {}
    for r in rubrics:
        skills[r["primary_skill"]] = skills.get(r["primary_skill"], 0) + 1
    print("skill distribution:", skills)
    print("\n--- sample scenario ---")
    print(json.dumps(scenarios[0], indent=2, ensure_ascii=False)[:1600])
    print("\n--- Row B commentary L2 (negative specificity stripped) ---")
    c = next(r for r in rubrics if r["criterion"].startswith("COMMENTARY") and "but" in r["q_rationale"])
    print(json.dumps(c, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
