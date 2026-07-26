# TutorBench Multimodal → Text Transcription Plan

**Status:** proposal, not yet implemented
**Date:** 2026-07-25
**Source:** [`ScaleAI/TutorBench`](https://huggingface.co/datasets/ScaleAI/TutorBench) — public, ungated, 1,473 rows / 15,043 rubric criteria
**Goal:** recover the multimodal half of TutorBench for a text-only pipeline, without destroying item validity
**Related:** [TutorEval Integration Spec](TutorEval%20Integration%20Spec.md), [IRT Calibration PRD](IRT%20Calibration%20PRD.md)

Every number here is measured against the actual parquet, not inferred from the
dataset card.

---

## 1. Why this is the highest-value ingest available

Our bank is **662 scenarios / 6,462 criteria**, all text. The full TutorBench
release is 1,473 items / 15,043 criteria. The `BATCH` field explains the gap:

```
USE_CASE_1_TEXT        327     USE_CASE_1_MULTIMODAL   146
USE_CASE_2_TEXT        165     USE_CASE_2_MULTIMODAL   342
USE_CASE_3_TEXT        164     USE_CASE_3_MULTIMODAL   329
                    -------                         -------
                       656  ← our 662                 817  ← unused
```

Our `use_case` counts (adaptive_explanation 329 / feedback 167 / hint_generation 166)
line up with the TEXT batches. So we already ingested the text half and left
**817 multimodal items carrying 8,600 criteria** on the table — blocked only by the
text-only constraint in [`dataio.py:82-83`](../tutor_cat/dataio.py#L82-L83).

Same rubric conventions, same subjects, same authorship. No new-dataset risk.

**The raw `RUBRICS` field also carries metadata we'd otherwise have to generate:**

```json
{"attributes": {"explicitness": "explicit", "objectivity": "objective", "severity": "critical",
                "tutoring_skill": "Identifying Core difficulty/ misconception attribution",
                "eval_dimension": "instruction_following, student_level_calibration"},
 "criteria": "The response must explain that at the first equivalence point, not both protons are neutralized..."}
```

`explicitness` / `objectivity` / `severity` are exactly the three fields
[`assign_irt_params.py`](../scripts/assign_irt_params.py) consumes and that
TutorEval lacks. `tutoring_skill` and `eval_dimension` are a native pedagogical
taxonomy we could cross-validate the 3-skill Q-matrix against, and there's a
`bloom_taxonomy` column too. **Worth checking whether our original ingest
discarded these.**

---

## 2. What the images actually are

Three sampled images span the full range:

| Sample | Content | Transcribable? |
| --- | --- | --- |
| Chemistry UC1 | **Pure typeset text** — a malonic acid titration problem statement, Ka values, question prompt. No graphics at all | **Losslessly.** The `prompt` is literally `Solve the problem shown in the image.` — the image *is* the problem text |
| Calculus | **One line of typeset LaTeX** — "Find the volume of the solid obtained by rotating the region bounded by y = 6−x² and y = \|x\| about the line y = 2…" | **Losslessly** |
| Biology | **Two electron micrographs** — coronavirus (spherical, 50 nm bar) and Ebola (filamentous, 1 μm bar) | **No.** Its rubric: *"must correctly state that image a belongs to the family Coronaviridae and cites at least one visual detail (spherical shape or 50 nm bar)."* Any description faithful enough to grade against hands over the answer |

So a large share of "multimodal" items are **text rendered as an image**.
Transcribing those isn't a compromise — it's a fix.

### Vision-dependency screen

Screening all 8,600 criteria for vision-dependent language (`visual perception`,
`visual detail`, `from the image`, `image a`/`image b`, `the graph`/`diagram`/`axis`,
`handwritten`):

```
CLEAN     486 items  →  5,148 criteria   (no vision-dependent criterion)
AFFECTED  331 items  →  3,452 criteria, of which only 493 are the vision-dependent ones
```

**493 criteria — 6% — are the genuine blockers.** Distribution:

| Subject | Items | With ≥1 vision-dep criterion |
| --- | --- | --- |
| Physics | 135 | 69 (51%) |
| Biology | 130 | 57 (44%) |
| Calculus | 140 | 59 (42%) |
| Computer Science | 134 | 53 (40%) |
| Statistics | 136 | 48 (35%) |
| Chemistry | 142 | 45 (32%) |

| Use case | Items | Criteria | With ≥1 vision-dep criterion |
| --- | --- | --- | --- |
| UC1 | 146 | 1,497 | 76 (52%) |
| UC2 | 342 | 3,678 | 146 (43%) |
| UC3 | 329 | 3,425 | 109 (33%) |

Also: only **324 of 817** multimodal prompts reference the image in their text
(vs 66 of 656 text prompts), so for ~493 items the image is assumed context the
prompt never mentions.

> ⚠️ This screen is regex keyword matching over criterion text, not a look at
> the images. It will have false positives ("the graph" in a criterion whose image
> is text) and false negatives (a criterion needing the image without saying so).
> **Phase 1 replaces it with a real classification.** Don't treat 6% as final.

---

## 3. Image storage: embedded bytes, not URLs

The parquet schema carries the images inline:

```
IMAGE_URL: string
Image: struct<bytes: binary, path: string>
```

Measured coverage:

```
multimodal rows            817
  with embedded bytes      811
  with IMAGE_URL           811
  bytes but no URL           0
  URL but no bytes           0
  NEITHER                    6   ← dead rows, exclude at ingest
text-only rows             656   (neither — as expected)
```

Bytes and URL cover **exactly the same 811 rows**, so there is no fallback case.
**The workable count is 811, not 817.**

Properties of the 811:

- **All PNG** (`89504e47` magic bytes, uniformly)
- **All unique** — zero image reuse across UC1/UC2/UC3, so there is nothing to dedupe
- **1,116 MB total**; min 2,456 B, median 207,292 B, **max 19.2 MB**
- `path` is just `<TASK_ID>.png`

### Use the bytes, not the URL

The Claude API accepts both `{"type": "base64", ...}` and `{"type": "url", ...}`.
Bytes win here:

- **No dependency on someone else's S3.** `scale-static-assets.s3.us-west-2.amazonaws.com`
  is Scale's bucket. A URL source is fetched at request time, so a policy change
  or expired object breaks a batch mid-run — and with the Batch API you'd find out
  an hour later in per-item `errored` results.
- **Reproducibility.** The parquet is pinnable to a revision; the bucket isn't.
- **The `sha256` provenance field in Phase 5 needs the bytes anyway.**

---

## 4. The structural principle

**Transcribe blind, gate separately.** Two rules that shape everything else:

- **The transcriber never sees the rubric.** If it does, it writes toward the
  criteria and produces leakage no downstream check will catch — the transcription
  looks perfect and grades perfectly for the wrong reason.
- **The gate never sees the image.** It judges "is this criterion answerable from
  this text alone?" With the image available it fills gaps from the picture and
  passes transcriptions that are actually insufficient.

---

## 5. Phase 0 — Extract and inventory (offline, no API)

Emit `image_inventory.jsonl`:

- `sha256` of bytes, dimensions, byte size, format
- **Drop the 6 rows with neither bytes nor URL**
- **Cheap pixel statistics as a prior for Phase 1** — mean luminance, luminance
  variance, saturation, near-white fraction, edge density. A rendered text page is
  high-luminance / low-saturation / low-variance; a micrograph is the opposite.
  This is not the classifier — it's a check *against* the classifier, so a
  disagreement flags an item for review.
- **Downsample anything over 2576 px on the long edge.** That's the high-resolution
  vision ceiling; beyond it the image is resized server-side anyway, so you'd be
  paying upload bandwidth for discarded pixels. The 19.2 MB outlier is almost
  certainly a high-DPI scan pinned at the ~4,784-token ceiling.
- `token_estimate` from `client.messages.count_tokens()` on a stratified sample of
  ~30 images. **Do not estimate from pixel dimensions** — client-side estimators
  are wrong for Claude.

### Batch sizing

The Batch API caps at **256 MB per batch** (and 100,000 requests). Base64 inflates
by ~33%, so 1,116 MB of PNGs becomes ~1.5 GB encoded → **split into ~8–10 batches**.
Chunk by *cumulative encoded size*, not count — the size distribution is skewed
(median 207 KB, max 19.2 MB). Per-request limit is 32 MB, so even the outlier fits
individually.

---

## 6. Phase 1 — Classify each image

**Model: `claude-sonnet-5`.** Classification is a coarse judgment, and Sonnet 5 is
in the same high-resolution vision tier as Opus 5 (2576 px long edge, up to ~4,784
image tokens). It's at introductory pricing — **$2/$10 per MTok through
2026-08-31**, vs $3/$15 after — making it ~2.5× cheaper than Opus 5 for identical
vision capability on this task.

Structured output via `client.messages.parse()`:

```python
class ImageClass(BaseModel):
    kind: Literal["typeset_text", "typeset_math", "handwritten_work",
                  "diagram_schematic", "data_plot", "photo_micrograph", "mixed"]
    fully_recoverable_in_text: bool
    what_would_be_lost: str
    contains_student_work: bool
    contains_visible_error: bool
    confidence: Literal["high", "medium", "low"]
```

Routing:

| `kind` | Action |
| --- | --- |
| `typeset_text`, `typeset_math` | Transcribe — lossless |
| `handwritten_work` | Transcribe with the error-preservation rule (§7) |
| `diagram_schematic`, `data_plot` | **Route to review** — judgment call, don't trust the flag |
| `photo_micrograph` | **Never transcribe** — exclude |
| `mixed` | Review |

`contains_student_work` and `contains_visible_error` must be collected **before**
transcription, so the Phase 8c error-preservation check isn't circular.

---

## 7. Phase 2 — Transcribe

**Model: `claude-opus-5`, `effort: "low"`.**

Opus 5 specifically because the failure mode is *notation*. Getting `HC₃H₂O₄⁻`
versus `HC₃HO₄⁻` right — a missing hydrogen that is itself the subject of a rubric
criterion — is exactly where the capability gap shows, and a silent subscript error
propagates into every criterion for that item. This is the one phase worth Opus rates.

`effort: "low"` because transcription is not a reasoning task, and low/medium effort
are unusually strong on Opus 5.

### API gotchas on Opus 5

- **Thinking is ON by default** (unlike Opus 4.8, where omitting the field meant no
  thinking), and **`max_tokens` caps thinking + output together**. Set
  `max_tokens=8192` or transcriptions truncate mid-formula.
- **Don't disable thinking.** It's permitted only at `effort: "high"` or below, and
  it introduces a `<thinking>`-tag leakage failure mode you don't want contaminating
  transcribed text.
- **`temperature` / `top_p` / `top_k` return a 400.** Determinism comes from a tight
  prompt, not a sampling parameter.
- **Prompt caching:** put the shared instruction block behind a `cache_control`
  breakpoint. Opus 5's minimum cacheable prefix is **512 tokens** (down from 1024 on
  4.8), so a detailed instruction block will cache.

### Prompt rules that matter

1. **Transcribe, do not solve.** Verbatim only. No worked answer, no explanation,
   no correction.
2. **Preserve errors exactly as written.** *The single most important instruction.*
   For UC2/UC3 items the image contains a student's mistake and that mistake **is**
   the item. A VLM will helpfully fix it unless told not to, silently destroying
   every diagnosis criterion.
3. **Notation fidelity:** `$...$` inline, `$$...$$` display; preserve subscripts,
   superscripts, charges, and units exactly.
4. **Mark gaps** with `[UNTRANSCRIBABLE: <what>]` rather than paraphrasing.

Submit via `client.messages.batches.create()` — 50% off, most batches finish within
an hour (max 24 h), results retained 29 days. **Key results by `custom_id`, never
by position** — they return in arbitrary order.

---

## 8. Phase 3 — Criterion-level gating

**Model: `claude-opus-5`. Text-only — no image.**

Batch all criteria for one item into one call (**811 calls, not 8,600**):

```python
class CriterionGate(BaseModel):
    criterion_id: str
    gradable_from_text: bool      # can a grader reach pass/fail from prompt + transcription alone?
    leaks_answer: bool            # does the transcription itself satisfy this criterion?
    reason: str
```

**`leaks_answer` is the check that protects validity** — the micrograph case
generalized. Drop every criterion where it's true. Our `critical_failures` report
already has an `answer_leakage` type; shipping leaked criteria would mean
manufacturing the exact defect the pipeline exists to detect.

This replaces the §2 regex screen with something real.

---

## 9. Phase 4 — Verifying item validity (five checks)

### 9a. Cross-model transcription agreement

`temperature` is unavailable, so re-running Opus 5 gives near-identical output and
tells you nothing. Transcribe a second time with **`claude-sonnet-5`** — different
model, genuinely independent errors. Compare in increasing strictness:

1. Normalized string similarity
2. Extracted numeric-token multiset
3. **Extracted LaTeX / chemical-formula token multiset** ← the signal that matters

Disagreement above threshold → human review.

### 9b. Answer-leakage probe using the existing calibration fleet

Run 3–4 of the smallest models from the IRT Calibration PRD list
(`gemma-3-270m-it`, `SmolLM2-360M-Instruct`, `TinyLlama-1.1B-Chat`) against the
transcribed items. **A 270M model has no business passing an undergraduate
chemistry criterion. If it does, the transcription leaked.**

Strongest automated leakage test available, and it costs only cluster time. Note
this uses the floor-effect property that is a *problem* for TutorEval as a
*detector* here.

### 9c. Error-preservation check (deterministic — trust this one most)

For every item where Phase 1 set `contains_visible_error: true`, extract the quoted
and parenthesized tokens from its rubrics — they name the error directly:

> *"The response must identify that the student's error stating the species HC3HO4¯ (missing a hydrogen)."*

Assert those tokens appear in the transcription. If the VLM silently corrected the
student's work, the erroneous token is gone and this catches it — **no LLM
required**. This is the failure mode most likely to occur and the only one with a
mechanical test.

### 9d. Human spot-check

Stratified: 10 items per `kind` × 7 kinds = **70 items**, reviewed against the
original image. Record per-item agreement. This is ground truth for 9a–9c —
without it you don't know whether the automated thresholds are calibrated. Fits the
existing `grader_packets/` workflow.

### 9e. Behavioral equivalence (strongest evidence, ~50 items)

Run one multimodal-capable tutor already in [`config.yaml`](../config.yaml) —
`gemini-group/gemini-3.6-flash` or `claude-group/claude-opus-4-8` via the
TrueFoundry gateway — **twice**: once on the original image item, once on the
transcribed item. Judge both with the existing judge and compare per-criterion
verdicts.

High agreement is *direct evidence* the transcription preserved the construct,
rather than an argument that it should have. Cheap at 50 items, and the only check
that tests the thing we actually care about.

---

## 10. Phase 5 — Emit with provenance

Every transcribed scenario carries:

```json
{
  "transcription": "...",
  "transcription_model": "claude-opus-5",
  "transcription_prompt_version": "transcribe-v1",
  "image_sha256": "...",
  "image_kind": "typeset_math",
  "derived_from": "image",
  "validity_checks": {
    "agreement_score": 0.98,
    "leakage_probe_pass": true,
    "error_tokens_preserved": true,
    "human_reviewed": false
  },
  "status": "approved"
}
```

- **Store the transcription in the JSONL** rather than regenerating it — it's part
  of the item now and must be auditable.
- `modality` stays `"text"` so [`dataio.py`](../tutor_cat/dataio.py#L82-L83) accepts
  it, but `derived_from: "image"` keeps it distinguishable from native-text items.
- Add a `transcribed: true` flag so that after calibration we can test for
  **differential item functioning** against native-text items. If transcribed items
  show a systematically different `b` distribution, the transcription introduced
  bias and we'll be able to see it.
- Criteria that fail the gate get `status: "needs_review"`, not `"approved"`, so
  [`dataio.py:103-104`](../tutor_cat/dataio.py#L103-L104) warns rather than silently
  including them.

---

## 11. Phase 6 — Acceptance gate

Do not ingest unless:

| Check | Threshold |
| --- | --- |
| Human spot-check agreement (9d) | ≥ 0.90 |
| Leakage-probe false-pass rate (9b) | ≈ 0 |
| Error-token preservation on `contains_visible_error` subset (9c) | ≥ 0.95 |
| Behavioral-equivalence per-criterion agreement (9e) | ≥ 0.85 |

---

## 12. Cost

Assuming ~2,000 image tokens each (**re-baseline with `count_tokens` before
trusting this**), all batched at 50%:

| Phase | Model | Batched |
| --- | --- | --- |
| 1 — classify | `claude-sonnet-5` | ~$2.60 |
| 2 — transcribe | `claude-opus-5` | ~$11.40 |
| 9a — second transcription | `claude-sonnet-5` | ~$4.60 |
| 3 — criterion gating (text-only) | `claude-opus-5` | ~$12.30 |
| 9b / 9e — probes | local fleet + gateway | negligible |
| | **Total** | **~$31** |

Call it **$30–60** with image-token uncertainty.

**Cost is not the constraint here — validity is.** Which is the argument for
spending on Opus 5 in phases 2 and 3 and on the human spot-check, rather than
optimizing the model tier down.

Model reference (Anthropic first-party rates):

| Model | ID | Input / Output per MTok |
| --- | --- | --- |
| Claude Opus 5 | `claude-opus-5` | $5 / $25 |
| Claude Sonnet 5 | `claude-sonnet-5` | $3 / $15 ($2 / $10 intro through 2026-08-31) |
| Claude Haiku 4.5 | `claude-haiku-4-5` | $1 / $5 |

---

## 13. Do this first

Run phases 1 and 2 on **30 images stratified across the pixel-statistic classes**
and read the transcriptions yourself. That tests whether the error-preservation
instruction actually holds — the assumption the entire plan rests on — and costs
about a dollar.

**If Opus 5 corrects student work despite being told not to, the plan needs
rethinking rather than scaling.**

---

## 14. Expected yield

If the acceptance gate passes:

| | Items | Criteria |
| --- | --- | --- |
| Current bank (TutorBench text) | 662 | 6,462 |
| + clean multimodal | +486 | +5,148 |
| + affected multimodal, vision-dep criteria dropped | +325 | +2,959 |
| **Total** | **~1,473** | **~14,569** |

That roughly **doubles the bank** without touching a new dataset. Compare
TutorEval's contribution: 370 items / 814 criteria closed-book, or 834 / 1,845 both
conditions (see the [TutorEval Integration Spec](TutorEval%20Integration%20Spec.md)).

Skill balance is the open question. Current bank loads
`content 4,531 / diagnosis 2,395 / scaffolding 1,151`, with 1,065 skill-inert. The
multimodal items are same-provenance so should distribute similarly — but that's an
assumption to verify with a Q-matrix sample, not a claim.

---

## Appendix: reproducing the numbers

```bash
# Both parquet shards (~1.1 GB of embedded PNGs)
for i in 0 1; do
  curl -sL "https://huggingface.co/api/datasets/ScaleAI/TutorBench/parquet/default/train/$i.parquet" \
    -o "/tmp/tb$i.parquet"
done
```

```python
import pyarrow.parquet as pq
pq.ParquetFile("/tmp/tb0.parquet").schema_arrow   # confirms Image: struct<bytes, path>
```

Caveats on figures in this document:

- The **6% vision-dependent** figure (§2) is a **regex keyword screen over criterion
  text**, not an inspection of images. Phase 1 supersedes it.
- **Token estimates are `chars/4`-class approximations**, not a real tokenizer. Use
  `client.messages.count_tokens()` on a real sample before committing to a budget.
- The vision-dependency screen ran over all **817** multimodal rows; the workable
  set after dropping dead rows is **811**. Per-subject and per-use-case counts in
  §2 are therefore off by at most 6 items.
