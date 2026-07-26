# TutorEval Integration Spec

**Status:** proposal, not yet implemented — decisions still open
**Date:** 2026-07-25
**Dataset:** [`princeton-nlp/TutorEval`](https://huggingface.co/datasets/princeton-nlp/TutorEval) @ revision `aafd8c96c506f11755c005842c893677b76f20ca`
**Upstream code:** [`princeton-nlp/LM-Science-Tutor`](https://github.com/princeton-nlp/LM-Science-Tutor) (paper: *Language Models as Science Tutors*)

Everything below is measured against the actual parquet (834 rows, 1.3 MB), not
inferred from the dataset card. Numbers should reproduce.

---

## 1. What the dataset contains

Single `train` split, one parquet file, nine flat fields:

| Field | Type | Measured |
| --- | --- | --- |
| `chapter` | str | Textbook section. min 3,181 / median 9,153 / max 38,452 chars. **199 unique** chapters across 834 rows (~4.2 items each) |
| `question` | str | One student turn. median 93, max 661 chars. **834 unique — no duplicates** |
| `key_points` | str | Markdown bullets. Already criterion-shaped prose |
| `closed_book` | bool | 370 True / 464 False |
| `answer_in_chapter` | bool | 307 True / 527 False |
| `misleading_question` | bool | 114 True (question contains a false premise) |
| `difficulty` | str | `easy` 593 / `hard` 241 — **only two values, no "medium"** |
| `domain` | str | math 362, computer_science 205, physics 167, environmental_science 53, life_sciences 47 |
| `path_to_chapter` | str | OpenStax / LibreTexts paths. Prefixes: eng 312, math 273, phys 120, stats 89, med 37, bio 3 |

`key_points` bullet counts per item: 1→185, 2→381, 3→192, 4→63, 5→10, 6→1, 7→2.
**Total 1,845 bullets** = the criterion budget, at 2.2/item (vs TutorBench's 9.8).

Cross-tabs:

```
                   answer_in_chapter          misleading_question
                   False   True               False   True
closed_book False    262    202                 398     66
closed_book True     265    105                 322     48
```

### Chapter text is sufficient — no textbook download needed

The textbooks are **not published**. `LM-Science-Tutor` has 26 files and no
`textbooks/` directory; `princeton-nlp/textbook_chapters` does not exist.
`path_to_chapter` is provenance only. The embedded `chapter` field *is* the release.

It's also adequate. Worst case found — item 40, whose rubric is the maximally
opaque `- Student is confused / - No, net force is zero / - Answer is wrong`, and
whose question asks about *"the third question of the reinforcement exercises"* —
resolves cleanly, because the chapter contains that exact exercise:

```
Reinforcement Exercises
You slide a box across the floor by applying a 220 N force to the right...
3. The box is not accelerating in the vertical direction, so what is the net vertical force?
```

Chapters are not truncated (602/834 end on sentence-final punctuation; ragged
endings are LaTeX/code artifacts). Coverage of internally-referenced sections is
good: 297 chapters contain "Exercise", 347 "Example", 338 "Figure".

Two small fidelity gaps, both worth excluding rather than fixing:

- **Figures are referenced but absent.** 338 chapters mention "Figure"; **zero**
  contain image embeds. Only 4 questions ask about a figure directly.
- **4 chapters strip equations to bare `(2)`, `(3)` placeholders.** 567/834
  retain real LaTeX, 263 are prose-only. So this is 4 items, not systemic.

---

## 2. How `closed_book` actually works (this is the confusing part)

**It is not a paired variant, and not a 50/50 partition.** 834 unique questions,
zero appearing under both flag values.

From the authors' [`generate.py:72-74`](https://github.com/princeton-nlp/LM-Science-Tutor/blob/main/tutoreval/generate.py#L72-L74):

```python
if args.closedbook:
    data = data.filter(lambda x: x["closed_book"])
```

The filter applies **only** in closed-book mode. So the two official evals are:

- **Open-book:** no filter → **all 834** questions, chapter included in the prompt.
- **Closed-book:** the **370** flagged questions, chapter withheld.

`closed_book: True` therefore means *"this question is also answerable without the
chapter."* Those 370 appear in **both** official evals. The 464 are not an
"open-book set" — they're simply the questions that require the chapter. The CSV
shipped in the repo confirms the semantics: its column is named `requires_chapter`.

Prompt templates, verbatim from the repo:

```
# generation_template.txt (open-book)
Here is a passage from a textbook I am trying to understand:

"""
{{CHAPTER}}
"""

{{QUESTION}}

# closedbook_generation_template.txt
{{QUESTION}}
```

### Grading methodology differs from ours

Official TutorEval grades **holistically**: PRESENTATION 0–3 and CORRECTNESS 0–3,
half points allowed, with all `key_points` handed to the grader at once as a group
checklist. Our pipeline atomizes them into independent binary criteria.

That's a defensible departure — MIRT needs independent items — but it means
**our numbers will not be comparable to published TutorEval scores.** Record the
departure in the run manifest so nobody later mistakes them for the same metric.
(Their axes map suggestively onto ours: CORRECTNESS ≈ `content`,
PRESENTATION ≈ `scaffolding`.)

Note also: `closedbook_grading_template.txt` contains **no `{chapter}`
placeholder**. The authors' own closed-book grader never sees the chapter.

---

## 3. Is it worth including? (open question — evidence both ways)

### Against

**It's a content probe, and content is what we already have most of.**
Existing bank, by skill loading:

```
content      4531 criteria
diagnosis    2395
scaffolding  1151      <- the scarce axis
```

Keyword-classifying the 814 closed-book bullets: **78% purely factual** (no
pedagogical or diagnostic language). Only 16% pedagogical, 6% diagnostic. So
TutorEval closed-book adds ~630 content criteria to a pile of 4,531, and maybe
~130 touching scaffolding, where we're thin.

*(Caveat: this is a keyword heuristic, not ground truth. See §7.)*

**Floor effects against the calibration fleet.** The IRT Calibration PRD's fleet
is 24 models from 0.27B to 2.85B. TutorEval is undergraduate free-response STEM —
4-momentum in special relativity, eigenvector multiplicity and diagonalizability,
increasing sequences of events in measure theory, whether trained sigmoid
activations can be swapped for step functions. Sub-3B models will fail nearly
every criterion. Items everyone fails carry no Fisher information: they sit at
`b` far above the fleet's θ, contribute nothing to calibration, and the selector
will correctly never pick them.

**Partly redundant.** For content we already have 4,531 criteria, and the PRD
already lists ARC / OpenBookQA / SciQ / MMLU, which measure content far more
cheaply with no LLM judge at all.

### For

- **Free-response content graded by rubric is not the same construct as MCQ.**
  A model can select a correct answer and still explain it wrongly. If the claim
  is about a *tutor*, generative content correctness matters.
- **The 114 `misleading_question` items are genuinely valuable.** False-premise
  resistance — a student asserts something wrong and the model must not agree —
  is a real tutoring failure mode (sycophancy), maps to `diagnosis`, and feeds the
  `critical_failures` report. Not redundant with anything we have.
- **It extends the difficulty range upward.** For IRT you need items above strong
  models' θ to get precision on them. Speculative while `b` is synthetic, but real
  once calibration lands.
- **`difficulty` (easy/hard) is our first non-synthetic difficulty signal.**
  Coarse, but currently `b` is entirely metadata-heuristic.

### Where the pedagogically richer items live

Comparing the closed-book 370 against their complement:

| | closed-book (370) | complement (464) |
| --- | --- | --- |
| pedagogical language | 16% | **24%** |
| diagnostic language | 6% | **10%** |
| purely factual | **78%** | 67% |

Closed-book questions must stand alone, so they become definition requests
("What's a solenoid?"). The chapter-dependent ones are things like *"I think the
net vertical force is the weight of the box — is this correct?"*, where the rubric
is a student's specific wrong belief. That's diagnosis-and-scaffolding-shaped. The
property that made those items awkward to ingest is the same one that makes them
pedagogically real.

**If `scaffolding` is the actual gap, TutorEval is the wrong tool either way** —
it's content-skewed in both conditions. MathDial / Bridge (already in the PRD)
carry expert annotations about *how to respond and why*, which is
scaffolding-native.

---

## 4. Design decision: partition, don't duplicate

Assign each of the 834 questions to **exactly one** condition:

- `closed_book == True` → **370 items**, asked without the chapter
- `closed_book == False` → **464 items**, asked with the chapter

Not the authors' setup (they'd run the 370 both ways), but the right call here:
every question gets asked in the condition it was designed for, all 834 items and
all 1,845 criteria are used, and no `key_points` set is graded twice.

**Do not run the 370 in both conditions and pool them.** Same criteria graded
twice = perfectly correlated items, which breaks local independence in
[`mirt.py`](../tutor_cat/mirt.py) and collapses SE spuriously. A within-item
"does the chapter help?" contrast is a fine standalone diagnostic — just keep it
out of the item bank.

---

## 5. Extraction spec

### 5a. Scenarios → 834 rows

| Target field | Source |
| --- | --- |
| `scenario_id` | `te_0001`…`te_0834`, ordered by `(path_to_chapter, question)` so ids stay stable if the parquet reorders |
| `prompt` | **closed-book:** `question` verbatim. **open-book:** authors' template (§2) with `chapter` interpolated |
| `criterion_ids` | `te_0001_c01`… one per `key_points` bullet |
| `use_case` | `adaptive_explanation` — matches existing vocabulary (`adaptive_explanation` / `feedback` / `hint_generation`); TutorEval questions are all explanation requests |
| `subject` | `domain` |
| `grade_band` | `null` — consistent with all 662 TutorBench scenarios |
| `modality` | `"text"` |
| `conversation_context` | `[]` — single-turn |
| `reference_solution` | `""` — none exists, and [`judge.py:8`](../tutor_cat/judge.py#L8) ignores this field anyway |
| `source` | `"TutorEval"` |
| `split` | `"calibration"` |
| `version` | `"1.0"` |

Chapter goes in `prompt`, **not** `conversation_context` —
[`tutors.py:32-37`](../tutor_cat/tutors.py#L32-L37) replays context as separate
messages, which would misrepresent a single-turn interaction.

`scenarios.jsonl` grows to ~6 MB (464 embedded chapters). Expected, fine.

**Schema addition:** add `book_condition: str = ""` to the `Scenario` dataclass
and `from_json` in [`schemas.py`](../tutor_cat/schemas.py). Two lines. Without it
the field is silently dropped (`from_json` picks fields explicitly) and we lose
the ability to stratify results — which is the main reason to include both
conditions. Overloading `use_case` instead would pollute a field we want
comparable across datasets.

### 5b. Rubrics → 1,845 rows

| Target field | Source |
| --- | --- |
| `criterion_id` / `scenario_id` | as above |
| `criterion` | one `key_points` bullet, `- ` stripped, whitespace collapsed |
| `expected_evidence` | `[]` — empty for all 6,462 TutorBench criteria too |
| `scoring_type` | `"binary"` |
| `score_anchors` | `null` |
| `source` / `status` / `version` | `"TutorEval"` / `"approved"` / `"1.0"` |
| `q_mapping`, `q_rationale`, `primary_skill` | filled by [`generate_qmatrix.py`](../scripts/generate_qmatrix.py) |
| `difficulty`, `discrimination` | filled by [`assign_irt_params.py`](../scripts/assign_irt_params.py) |
| `explicitness`, `objectivity`, `criticality` | ⚠️ **nothing in the pipeline produces these** — see §6 |

---

## 6. The metadata gap

`explicitness`, `objectivity`, and `criticality` came bundled with TutorBench.
`generate_qmatrix.py` emits only `q_mapping` + `q_rationale` + `primary_skill`,
but `assign_irt_params.py` consumes all three directly:

```python
B_EXPLICITNESS = {"explicit": -0.4, "implicit": 0.4}
A_OBJECTIVITY  = {"objective": 1.2, "subjective": 0.8}
A_CRITICALITY  = {"critical": 1.15, "not_critical": 0.90, "critical_negative": 1.25}
```

So TutorEval needs a labeling pass TutorBench never did.

**Option 1 (recommended): extend the Q-matrix prompt to emit all six labels in
one call.** We already make one LLM call per criterion with the scenario in
context; three more fields cost nothing extra. Touches the JSON schema,
`validate_label`, and `label_to_fields` in `generate_qmatrix.py`, and warrants a
`PROMPT_VERSION` bump.

**Option 2: heuristics from the flags.** `criticality = critical` for the 114
misleading-question items; `objectivity = objective` for factual bullets;
`explicitness = implicit` for most.

Prefer Option 1 because of **variance**. TutorBench has real spread:

```
explicitness   explicit 4761  /  implicit 1701
objectivity    objective 5801 /  subjective 661
criticality    critical 4589  /  not_critical 1710  /  critical_negative 163
```

Heuristics would likely make TutorEval ~95% implicit + objective + not_critical,
so all 1,845 criteria land on near-identical `b` and `a`, separated only by jitter
(`B_JITTER_SD=0.5`, `A_JITTER_LOG_SD=0.15`). Flat Fisher information means the
selector picks among them arbitrarily — breadth without adaptivity.

---

## 7. Order of operations

1. **`scripts/ingest_tutoreval.py`** — parquet → `scenarios_tutoreval.jsonl` +
   `rubrics_tutoreval.jsonl`, plus `review_queue.jsonl`. Fully offline, no API.
2. **Review the queue** (see §8) — hand-fix or drop.
3. **Extend `generate_qmatrix.py`** for the three extra fields; run on TutorEval
   criteria.
4. **`assign_irt_params.py`** on the result. Consider wiring TutorEval's
   `difficulty` (easy/hard) into `b` alongside the synthetic terms, tagging
   `irt_params.source` so it's distinguishable.
5. **Concatenate** with TutorBench files, `tutor-cat validate`, and add a
   `data.sources` filter to [`config.yaml`](../config.yaml) so either bank can be
   run alone or pooled.

### Decisive check before committing (cheap)

Run step 3 on **~100 sampled bullets** and read the actual `q_mapping`
distribution. If it comes back mostly `('content',)`, that confirms §3's
"it's a content probe" verdict. If it surfaces more scaffolding than the keyword
heuristic found, the heuristic was wrong and TutorEval is worth more than §3
credits. This is the single number that should decide inclusion.

### After step 4, verify

- `q_mapping` distribution vs TutorBench's:
  `content 4531 / diagnosis 2395 / scaffolding 1151`, with 1,065 skill-inert.
- Spread of `difficulty` / `discrimination` vs TutorBench's — flat spread means
  the criteria inflate the bank without adding adaptivity.
- Criterion uniqueness: TutorBench is 97.5% (6,302 unique of 6,462). TutorEval
  should be near 100%, since bullets are hand-written per item.

---

## 8. Known bad items to flag at ingest

Scanning the 814 closed-book bullets (the 464 open-book set is worse — 30% of its
questions reference something chapter-internal, vs 5% for closed-book):

```
  12  defer to the chapter        'The chapter shows that the answer is 446/3'
  12  bare verdict only           'Student is confused'
  34  very short (<20 chars)      'Answer is radiation'
   2  refer to a code example
```

**43 of 370 closed-book items (12%)** have ≥1 questionable bullet. Note most are
merely *terse*, not unresolvable — `Answer is radiation` is gradable given the
question. Only the ~12 that genuinely defer to the chapter need resolution.

Also exclude: the 4 questions referencing a figure, and the 4 items whose chapters
have stripped equations.

**A full LLM resolution pass is not needed.** Emit the 43 to `review_queue.jsonl`
and hand-fix. If automation is preferred later, the pass costs 0.45M tokens for
closed-book (155 unique chapters, grouped) — not the 2.5M an ungrouped per-item
pass would cost.

---

## 9. Token budget

Estimated chapter tokens (chars/4): p50 2,288 · p90 5,934 · p99 8,519 · max 9,613.

| Path | Tokens |
| --- | --- |
| Tutor input, 464 open-book items | **1.40M** per model run |
| Judge input if chapter sent per-criterion (1,845 calls) | 5.5M |
| Judge input if chapter sent per-item | 2.5M |
| Offline criterion resolution, closed-book, grouped by chapter | 0.45M once |

**Judge context constraint:** Prometheus 2 7B is Mistral-7B-based. At an 8k
`max_model_len`, **14 items overflow on the chapter alone**, before the response,
criterion, and rubric scaffolding; at 4k, 205 items overflow. This only bites if
we decide to give the judge chapter context — see §10.

199 unique chapters over 834 items (~4.2 each, ~9.3 criteria each) means batching
any chapter-grouped pass gives strong prompt-cache reuse. Figures above are upper
bounds.

---

## 10. Open decisions

1. **Include TutorEval at all?** §3 argues it's content-heavy and will floor out
   against the sub-3B calibration fleet. The 100-criterion Q-matrix sample (§7)
   is the cheap tiebreaker. A middle path: take only the 114
   `misleading_question` items as a targeted diagnosis / critical-failure set.

2. **Both conditions, or closed-book only?** Both = 834 items / 1,845 criteria,
   with the §4 partition. Closed-book only = 370 items / 814 criteria, and it's
   notably cleaner: no chapter in any tutor prompt, no chapter in any judge
   prompt, no `max_model_len` bump, no resolution pass, `chapter` column droppable
   at ingest. But it keeps the *less* pedagogically rich half.

3. **Judge-side chapter context?** Extending the judge to receive reference
   context would help all datasets, but changes `judge_prompt_version` and
   invalidates the human labels in `grader_packets/`. §8's finding is that we can
   avoid this entirely by resolving criteria at ingest instead. Recommend: don't
   touch the judge.

4. **Per-benchmark Q-matrix skills, or one shared 3-skill θ?** IRT Calibration
   PRD line 50 says per-benchmark; the code assumes shared (`SKILLS` is
   hard-coded in [`__init__.py:12`](../tutor_cat/__init__.py#L12);
   `theta_init` / `max_se` / `u_init_diag` are 3-vectors). Shared keeps θ
   comparable across benchmarks, which is what makes pooled CAT meaningful.
   Deferrable until after ingest.

---

## 11. Side find: `human_gpt_grades.csv` is partly corrupt

The repo ships `tutoreval/human_gpt_grades.csv` — 3,348 rows, 834 questions × 4
models (Llemma-7B-32K-MathMix, Llemma-7B-32K-UltraChat-Baseline, vicuna-13b-v1.5-16k,
gpt-4-1106-preview), with human *and* GPT-4 grades on both axes. Would have been a
free external judge-validation set alongside `grader_packets/`.

It isn't usable as-is:

- `human_correctness` is **byte-identical** to `gpt-4-1106_correctness` (impossible)
- 12 rows have shifted columns (`model` values of `yes`/`no`, `requires_chapter`
  values of `easy`/`hard`) — likely unescaped delimiters in `key_points` / `model_output`
- one `human_correctness` score is 5.0 on a 0–3 scale

`human_presentation` does differ from GPT-4's, so that single column may be
salvageable. Low priority; don't build on it.

---

## Appendix: reproducing the numbers

```bash
# TutorEval parquet (1.3 MB)
curl -sL "https://huggingface.co/api/datasets/princeton-nlp/TutorEval/parquet/default/train/0.parquet" \
  -o /tmp/tutoreval.parquet

# upstream templates + eval code
curl -s https://raw.githubusercontent.com/princeton-nlp/LM-Science-Tutor/main/tutoreval/generate.py
curl -s https://raw.githubusercontent.com/princeton-nlp/LM-Science-Tutor/main/tutoreval/templates/grading_template.txt
curl -s https://raw.githubusercontent.com/princeton-nlp/LM-Science-Tutor/main/tutoreval/templates/closedbook_grading_template.txt
```

Token estimates are `chars / 4`, not a real tokenizer — treat as ±15%.
The pedagogical/diagnostic/factual classification in §3 is regex keyword
matching over bullet text, not labels. Both should be re-derived properly if
they end up load-bearing for a decision.
