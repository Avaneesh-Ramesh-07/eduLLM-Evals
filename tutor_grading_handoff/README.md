# Tutor Grading Handoff — Runbook

Grade the 100-model TutorBench tutor responses with the **frozen LLM judge** and
produce the MIRT response matrix. Follow this top-to-bottom; every step is
copy-pasteable. Troubleshooting (resume/checkpoint/S3/vLLM) lives in
[`OPERATIONS.md`](./OPERATIONS.md).

---

## What this is / prerequisites

This bundle turns finished tutor responses into a `models × criteria` pass/fail
matrix in three moves: **bridge → judge → ingest**.

- We do **not** reimplement the judge. Grading is done by the teammate's canonical
  runner at `aws_judge_handoff/scripts/run_judge_validation.py` (subcommand `run`).
- Our scripts only (1) build the judge's input cases from staged tutor responses
  and (2) ingest the judge's verdicts into the matrix.

Prerequisites:

- The repo checked out on **`main`** (this folder + `scripts/` + `tutor_cat/` +
  `aws_judge_handoff/` + `data/` all present).
- A **Linux NVIDIA GPU** box for the judge: Ampere-or-newer, **40–48 GB** is the
  simplest BF16 target (Qwen3.5-9B fits comfortably). vLLM is pinned to `0.26.0`.
- **Frozen judge = `qwen`** → `Qwen/Qwen3.5-9B`, revision pinned in
  [`../judge_frozen.yaml`](../judge_frozen.yaml)
  (`c202236235762e1c871ad0ccb60c8ee5ba337b9a`). The judge is **selectable via CLI**
  (`--judge qwen|selene|flow|prometheus|gemma`); use `qwen` unless told otherwise.
- **Precondition:** run this only **after** the FIXED tutor re-run has landed in
  `tutorbench-responses/*.jsonl` (i.e. after Bug #1 vLLM-load and Bug #2
  prompt-truncation fixes). Grading pre-fix responses wastes the run.

> `staging/` does not need to exist — the scripts create it. If you prefer, run
> `mkdir -p staging` first; it's harmless.

---

## Step 0 — Environment setup

Two separate dependency sets. Our bridge/ingestion deps are tiny; the judge
inference deps (vLLM + boto3) are heavier and only needed on the GPU box.

**Bash (Linux GPU box — this is where you'll do everything):**

```bash
cd /path/to/eduLLM-Evals            # repo root, on main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# our bridge/ingestion scripts
python -m pip install -r tutor_grading_handoff/requirements.txt
# judge inference (vllm==0.26.0, boto3) — needed for Step 2
python -m pip install -r aws_judge_handoff/requirements-aws.txt
```

**PowerShell (Windows, if you run the bridge/ingest steps locally):**

```powershell
cd C:\path\to\eduLLM-Evals
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r tutor_grading_handoff\requirements.txt
# (aws_judge_handoff\requirements-aws.txt is Linux/GPU only)
```

Gemma is gated; if you ever select `--judge gemma`, `export HF_TOKEN=...` first.
`qwen` is not gated.

---

## Step 1 — Write the finished tutor responses into the pipeline

Inputs consumed here:

- `tutorbench-responses/*.jsonl` — the completed tutor responses (one shard per
  model), produced by **your** tutor run.
- `data/scenarios.jsonl` — the scenario prompts + conversation context.
- `data/rubrics_qmatrix_final.jsonl` — the **frozen** rubric/Q-matrix item bank.

Run, in order:

```bash
# 1a. Validate the response shards. Writes a usable/dead manifest and flags
#     dead models (all-error) and prompt-truncated/empty outputs so they are
#     excluded or handled downstream.
python scripts/validate_responses.py

# 1b. Join usable responses × scenarios × frozen rubric into one row per
#     (model, scenario, criterion) gradeable cell.
python scripts/stage_judge_inputs.py

# 1c. Bridge those staged cells into the judge's EXACT blinded CASE schema.
python scripts/run_judge_grading.py build-cases
```

Artifacts produced:

| Artifact | What it is |
| --- | --- |
| `tutorbench-responses/_response_manifest.json` | usable vs dead models (+ model→file map) |
| `tutorbench-responses/_validation_report.json` | per-file finish-reason / empty-output / dup counts |
| `staging/judge_inputs.jsonl` | one row per `(model, scenario, criterion_id)` gradeable cell |
| `staging/judge_inputs_manifest.json` | **frozen** row order (`models`) + column order (`criterion_ids`) for the matrix |
| `staging/cases.jsonl` | **SHIP THIS** to the GPU judge — blinded cases in the runner's schema |
| `staging/cases_index.jsonl` | **PRIVATE — DO NOT SHIP.** Maps `case_id → (model, scenario, criterion)` for ingest. It is intentionally kept out of `cases.jsonl` because the judge is blinded to tutor identity (`candidate_model` is a forbidden case field). |

**Auto-fail cells:** cells whose tutor output errored/was empty/missing are
**excluded from `cases.jsonl`** (they can't be judged) and are **scored `y=0`
automatically at ingest** (Step 3), with the reason recorded. You don't do
anything special for them.

Sanity-check without writing anything:

```bash
python scripts/run_judge_grading.py build-cases --dry-run
```

---

## Step 2 — Run the frozen judge (GPU box)

Grade `staging/cases.jsonl` with the frozen `qwen` judge using the teammate
runner. Always **smoke-test first**, then resume the full run. See
[`OPERATIONS.md`](./OPERATIONS.md) for how resume/checkpoint/S3 verification and
vLLM issues work — everything below is grounded there.

```bash
# 2a. Smoke test: first 3 cases only, publishing to S3 with integrity checks.
python aws_judge_handoff/scripts/run_judge_validation.py run \
  --cases staging/cases.jsonl \
  --judge qwen \
  --output outputs/tutor-grading/qwen/canonical_r1.jsonl \
  --backend vllm \
  --prompt-variant canonical --replicate-id r1 \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-tutor-grading/qwen/canonical_r1 \
  --require-s3-upload \
  --limit 3

# 2b. Full run: same command WITHOUT --limit. --resume keeps the 3 verified
#     smoke rows and grades the rest. A crash loses at most one batch; rerun the
#     same command to resume (from S3 on a replacement box).
python aws_judge_handoff/scripts/run_judge_validation.py run \
  --cases staging/cases.jsonl \
  --judge qwen \
  --output outputs/tutor-grading/qwen/canonical_r1.jsonl \
  --backend vllm \
  --prompt-variant canonical --replicate-id r1 \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-tutor-grading/qwen/canonical_r1 \
  --require-s3-upload
```

- `--s3-output-prefix` + `--require-s3-upload` together make silent data loss
  impossible (uploads are SHA-256 verified after every batch). **Always set both.**
- This is a **single canonical wave** — do **not** use `aws_judge_handoff/run_judge_suite.sh`
  (that launcher is the 6-wave judge-*selection* study and hardcodes the blinded
  bundle's cases path). We reuse only the `run` machinery.
- The judge model + pinned revision come from `--judge qwen` (baked into the
  runner's `JUDGES` dict); no need to pass `--model-id`/`--revision`.
- Attach an IAM role that can list/get/put the bucket prefix. Do not put AWS keys
  in this folder.

The runner writes an append-only `...canonical_r1.jsonl` plus a sibling
`...manifest.json` checkpoint (see `OPERATIONS.md` → "Output layout").

---

## Step 3 — Ingest verdicts → MIRT matrix

Bring the judge's verdict JSONL back (or point at the local output / S3 download),
then ingest:

```bash
# preview coverage (how many gradeable cells have a verdict) without writing:
python scripts/run_judge_grading.py grade --mode ingest-verdicts \
  --ingest-file outputs/tutor-grading/qwen/canonical_r1.jsonl --dry-run

# real ingest -> response matrix:
python scripts/run_judge_grading.py grade --mode ingest-verdicts \
  --ingest-file outputs/tutor-grading/qwen/canonical_r1.jsonl
```

`--ingest-file` accepts one or more files **or directories** of `*.jsonl`.

What ingest does:

- `verdict: pass → y=1`, `verdict: fail → y=0`.
- `no_decision` / unscorable / generation-error → **`y=0` with the reason
  recorded** (honoring `judge-normalization-v3`: never infers a verdict from prose).
- Auto-fail cells from Step 1 → `y=0` (reason preserved), no judge call.
- Provenance (judge model + revision, prompt/normalization/evidence-policy
  versions, frozen-config hash) is copied from the verdict rows and
  **cross-checked against `judge_frozen.yaml`**; mismatches/cross-wave drift are
  warned.

Outputs (in `staging/`):

| File | What it is |
| --- | --- |
| `staging/verdicts.jsonl` | normalized per-cell verdicts (append-only; resume-safe) |
| `staging/response_matrix.csv` | rows = models, cols = criteria, `0/1` (blank = hole). Human-eyeballable + drop-in for the MIRT calibration. |
| `staging/response_matrix.npy` | same matrix as a numpy array (`NaN` = hole) |
| `staging/response_matrix_manifest.json` | frozen-judge provenance (expected + observed), counts, coverage/holes |

If `response_matrix_manifest.json` reports `holes > 0`, some gradeable cells had
no verdict yet — grade the missing cases and re-run `grade` (it resumes).

---

## Full end-to-end command block

**Bash (Linux GPU box):**

```bash
cd /path/to/eduLLM-Evals && source .venv/bin/activate

# Step 1 — responses into the pipeline
python scripts/validate_responses.py
python scripts/stage_judge_inputs.py
python scripts/run_judge_grading.py build-cases

# Step 2 — frozen judge (smoke, then full). Set YOUR bucket.
python aws_judge_handoff/scripts/run_judge_validation.py run \
  --cases staging/cases.jsonl --judge qwen \
  --output outputs/tutor-grading/qwen/canonical_r1.jsonl \
  --backend vllm --prompt-variant canonical --replicate-id r1 --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-tutor-grading/qwen/canonical_r1 \
  --require-s3-upload --limit 3
python aws_judge_handoff/scripts/run_judge_validation.py run \
  --cases staging/cases.jsonl --judge qwen \
  --output outputs/tutor-grading/qwen/canonical_r1.jsonl \
  --backend vllm --prompt-variant canonical --replicate-id r1 --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-tutor-grading/qwen/canonical_r1 \
  --require-s3-upload

# Step 3 — ingest -> matrix
python scripts/run_judge_grading.py grade --mode ingest-verdicts \
  --ingest-file outputs/tutor-grading/qwen/canonical_r1.jsonl
```

**PowerShell (Windows — Steps 1 & 3 only; Step 2 is Linux/GPU):**

```powershell
cd C:\path\to\eduLLM-Evals; .\.venv\Scripts\Activate.ps1

python scripts\validate_responses.py
python scripts\stage_judge_inputs.py
python scripts\run_judge_grading.py build-cases
# ... run Step 2 on the GPU box, bring back the verdict JSONL, then:
python scripts\run_judge_grading.py grade --mode ingest-verdicts `
  --ingest-file outputs\tutor-grading\qwen\canonical_r1.jsonl
```

---

## Outputs / hand-back

Return to the eval team:

1. **The verdict JSONL(s)** from Step 2:
   `outputs/tutor-grading/qwen/canonical_r1.jsonl` (+ its sibling
   `...manifest.json`), and/or the S3 prefix
   `s3://YOUR-BUCKET/edu-tutor-grading/...`.
2. **The response matrix** from Step 3: `staging/response_matrix.csv`,
   `staging/response_matrix.npy`, and `staging/response_matrix_manifest.json`.

Do **not** send `staging/cases_index.jsonl` outside the team if blinding matters —
it de-anonymizes `case_id → model`.

---

## Troubleshooting

See [`OPERATIONS.md`](./OPERATIONS.md): resume/checkpoint semantics, S3
integrity/verification, mid-run progress checks, the smoke-test `--limit` pattern,
runner-exposed vLLM knobs, and a full vLLM symptom → cause → fix table
(OOM, KV-cache sizing, dtype/quantization, gated models, tokenizer/chat-template).
