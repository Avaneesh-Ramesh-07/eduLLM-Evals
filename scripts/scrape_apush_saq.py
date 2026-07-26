"""Scrape APUSH Short-Answer Questions (SAQs) -> scenario + rubric JSONL.

Each SAQ -> 1 scenario. Each subpart (a/b/c) -> 1 binary rubric criterion whose
`expected_evidence` is the College Board "Examples that earn this point" list(s).

Two source PDFs per exam set:
  * Scoring Guidelines (SG) - restates each subpart prompt AND lists every
    acceptable answer, so prompt + rubric both come from it.
  * Free-Response Questions (FRQ) - carries the *stimulus* (the excerpt(s) /
    image a question is built on), which the SG omits. Q1 is a secondary-source
    question (two excerpts), Q2 a primary-source question (one excerpt/image),
    Q3/Q4 have no stimulus.

Self-contained: PDFs are downloaded on demand (see apush_common). Run with the
venv python that has pypdf:
    llm-from-scratch/Scripts/python.exe scripts/scrape_apush_saq.py
"""
from __future__ import annotations

import json
import re

import apush_common as ac
from apush_common import BULLET, clean_ws

OUT_DIR = ac.CACHE.parents[1] / "data" / "AP_IB" / "APUSH"

# subpart start: an (A)/[a]/bare-A label immediately followed by the "Briefly ..." stem.
# Every APUSH SAQ subpart opens with "Briefly describe/explain", the stable anchor
# across the 2023 "(A)", 2024 "[a]", and 2025 bare-"A" marker styles.
SUBPART = re.compile(r"(?:\(|\[)?\s*([ABCabc])\s*(?:\)|\])?\s+(Briefly\b)")
# both header phrasings: 2023/24 "Examples that earn this point ...", 2025 "Examples of
# acceptable responses ..."; also the "might include, if appropriate elaboration" list.
EX_HEADER = r"Examples (?:that earn this point|of acceptable responses)[^:]*:"

# a question stem in the FRQ. Phrasing drifts by year, so match both variants and
# tolerate the irregular whitespace PDF extraction injects:
#   2023-2025: "N. [Using the excerpt(s), ]respond to  parts a, b, and c."
#   2021:      "N. [Using the excerpts above, ]answer (a), (b), and (c)."
# The optional "Using ..." preface (present only for Q1/Q2, the stimulus-bearing
# questions) may run any length before the task verb.
SAQ_MARKER = re.compile(
    r"\n\s*([1-4])\.\s+"
    r"(?:Using\b[^\n]*?)?"
    r"(?:respond\s+to\s+parts?\s+a\b|answer\s+\(?a\)?)",
    re.I,
)


def scrub_bullet(chunk: str) -> str:
    """Remove list-header / point-tally tokens that stick to bullet fragments."""
    chunk = re.sub(EX_HEADER, " ", chunk)
    chunk = re.sub(r"Total for question\s*\d+.*$", " ", chunk, flags=re.S)
    chunk = re.sub(r"\b\d+\s*points?\b", " ", chunk)
    return clean_ws(chunk)


def skill_of(prompt: str) -> str:
    """APUSH SAQ verbs map to the describe/explain skill taxonomy."""
    m = re.search(r"[Bb]riefly\s+(describe|explain)", prompt)
    if m:
        return m.group(1)
    return "explain" if "explain" in prompt.lower() else "describe"


def parse_saq_block(body: str) -> list[dict]:
    """Return [{prompt, bullets:[...]}] for subparts a,b,c of one SAQ block."""
    want = ["a", "b", "c"]
    picked, wi = [], 0
    for m in SUBPART.finditer(body):
        if wi < 3 and m.group(1).lower() == want[wi]:
            picked.append(m)
            wi += 1
    if len(picked) != 3:
        raise ValueError(f"expected 3 subparts, found {len(picked)}")
    bounds = [p.start() for p in picked] + [len(body)]

    parts = []
    for i in range(3):
        seg = body[bounds[i] : bounds[i + 1]]
        mp = re.search(rf"(Briefly\b.*?)(?:{EX_HEADER}|\b1 point\b)", seg, re.S)
        prompt = clean_ws(mp.group(1)) if mp else clean_ws(seg.split(BULLET)[0])
        bullets = [b for b in (scrub_bullet(c) for c in seg.split(BULLET)[1:]) if b]
        if not bullets:
            raise ValueError(f"no acceptable-answer bullets for: {prompt[:60]!r}")
        parts.append({"prompt": prompt, "bullets": bullets})
    return parts


def process_sg(path) -> list[list[dict]]:
    """Return list of SAQs (each a list of 3 subpart dicts) for one SG PDF."""
    full = ac.denoise_sg(ac.pdf_text(path))
    heads = list(re.finditer(r"Question\s*([1-4]):\s*Short Answer", full))
    if len(heads) != 4:
        raise ValueError(f"{path.name}: found {len(heads)} SAQ headers, expected 4")
    essay = re.search(r"Question\s*\d+:\s*(?:Document-Based|Long Essay)", full)
    end = essay.start() if essay else len(full)
    stops = [h.start() for h in heads[1:]] + [end]
    return [parse_saq_block(full[h.end() : stops[i]]) for i, h in enumerate(heads)]


# --- FRQ stimulus extraction --------------------------------------------------
# Q1 (secondary source) carries two distinct excerpts, each a "..." quote block
# followed by a `Source: <attribution>`; split on those. A single excerpt may hold
# several quoted paragraphs, so the "Source:" attribution - not the quote - is the
# reliable inter-source delimiter.
_SEC_SOURCE = re.compile(r'".*?"\s*Source:.*?(?=(?:"|\Z))', re.S)


def _sources_secondary(region: str) -> list[dict]:
    """Q1: split a two-excerpt secondary-source region on Source: attributions."""
    blocks = [clean_ws(b) for b in _SEC_SOURCE.findall(region)]
    blocks = [b for b in blocks if b]
    if blocks:
        return [{"role": "source", "content": b} for b in blocks]
    return _source_single(region)  # degrade to one entry if the pattern misses


def _source_single(region: str) -> list[dict]:
    """Q2: one primary-source excerpt (or image caption); keep it whole."""
    i = region.find('"')
    if i == -1:
        j = region.find("Source:")
        return [{"role": "source", "content": clean_ws(region[j:])}] if j != -1 else []
    return [{"role": "source", "content": clean_ws(region[i:])}]


def saq_stimuli(frq_path) -> dict[int, list[dict]]:
    """Map SAQ question number -> conversation_context list from the FRQ PDF."""
    full = ac.denoise_frq(ac.pdf_text(frq_path))
    sec1 = full[: full.find("Document 1")] if "Document 1" in full else full
    pos = {int(m.group(1)): (m.start(), m.end()) for m in SAQ_MARKER.finditer(sec1)}
    out: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: []}
    if 1 in pos:
        out[1] = _sources_secondary(sec1[: pos[1][0]])
    if 2 in pos:
        start = pos[1][1] if 1 in pos else 0
        out[2] = _source_single(sec1[start : pos[2][0]])
    return out


def build_records():
    scenarios, rubrics = [], []
    counter = 0
    for form in ac.current_forms():
        src = form.sg_src
        saqs = process_sg(form.sg_path)
        stim = saq_stimuli(form.frq_path)
        for qi, saq in enumerate(saqs, start=1):
            counter += 1
            sid = f"ap_ush_saq_{counter:03d}"
            crit_ids = [f"{sid}_c{j:02d}" for j in range(1, 4)]

            prompt = "\n".join(f"({'abc'[j]}) {saq[j]['prompt']}" for j in range(3))
            # full-scoring (100%) solution: one acceptable answer for each subpart
            reference = "\n".join(
                f"({'abc'[j]}) {saq[j]['bullets'][0]}" for j in range(3)
            )

            scenarios.append(
                {
                    "scenario_id": sid,
                    "use_case": "SAQ",
                    "subject": "US History",
                    "grade_band": "9-12",
                    "modality": "text",
                    "prompt": prompt,
                    "conversation_context": stim.get(qi, []),
                    "reference_solution": reference,
                    "criterion_ids": crit_ids,
                    "source": src,
                    "split": "calibration",
                    "version": "1.0",
                }
            )

            for j in range(3):
                part = saq[j]
                skill = skill_of(part["prompt"])
                rubrics.append(
                    {
                        "criterion_id": crit_ids[j],
                        "scenario_id": sid,
                        "criterion": (
                            "The response answers the subpart: "
                            f"'{part['prompt'].rstrip(' .')}'."
                        ),
                        "expected_evidence": part["bullets"],
                        "scoring_type": "binary",
                        "score_anchors": None,
                        "linked_criteria": [],
                        "primary_skill": skill,
                        "q_mapping": {
                            "describe": 1 if skill == "describe" else 0,
                            "explain": 1 if skill == "explain" else 0,
                        },
                        "q_rationale": (
                            f'The criterion requires answering a "{skill}"-based question.'
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
    return scenarios, rubrics


def main():
    scenarios, rubrics = build_records()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "saq_scenarios.jsonl").open("w", encoding="utf-8") as f:
        for r in scenarios:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (OUT_DIR / "saq_rubrics.jsonl").open("w", encoding="utf-8") as f:
        for r in rubrics:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"SAQ scenarios: {len(scenarios)}")
    print(f"SAQ rubrics:   {len(rubrics)}")
    with_stim = sum(1 for s in scenarios if s["conversation_context"])
    print(f"scenarios with stimulus: {with_stim} (expect Q1+Q2 per set = 2/4)")
    ctx = {}
    for s in scenarios:
        ctx.setdefault(len(s["conversation_context"]), 0)
        ctx[len(s["conversation_context"])] += 1
    print("stimulus-count distribution:", dict(sorted(ctx.items())))
    print("\n--- sample scenario (Q1, has stimulus) ---")
    print(json.dumps(scenarios[0], indent=2, ensure_ascii=False)[:1400])
    print("\n--- sample rubric ---")
    print(json.dumps(rubrics[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
