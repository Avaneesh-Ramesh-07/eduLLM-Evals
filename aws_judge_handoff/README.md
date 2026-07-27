# AWS judge handoff: v3 evidence-gated development experiment

This folder is the blinded GPU-job bundle for `judge-validation-v3`. It contains the 261 tutor-response/criterion cases and the runner for all five judges. It does **not** contain the human P/F labels, grader packets, human notes, or tutor-model identities.

This is a **development/recalibration experiment**, because results from these same 261 cases motivated the stricter prompt. Use it to measure whether the evidence gate improves agreement over v2. Do not treat meeting a threshold on these reused cases as final acceptance; freeze the winning configuration and evaluate it once on a separately labeled, unseen scenario-level holdout.

## Contents

- `inputs/judge_cases.blinded.jsonl`: the tutor responses and criteria to grade;
- `scripts/run_judge_validation.py`: blinded judge runner and S3 checkpointing;
- `run_judge_suite.sh`: six-wave launcher for one judge/GPU job;
- `STUDY_DESIGN.json`: frozen wave definitions and the retained diagnostic target thresholds;
- `requirements-aws.txt`: GPU and S3 Python dependencies;
- `SHA256SUMS`: transfer-integrity hashes for the bundle files.

## AWS setup

Use a Linux NVIDIA GPU instance with Python 3.10 or newer. An Ampere-or-newer
40–48 GB GPU is the simplest BF16 target for all five judges, including Gemma
3 12B. Use an NVIDIA driver/CUDA image compatible with the pinned vLLM 0.26.0
release. Smaller cards may require tensor parallelism or quantization; if used,
keep that setting identical across all six waves for a judge. From this folder:

```bash
python3 -m venv .venv-judge
source .venv-judge/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-aws.txt
python scripts/run_judge_validation.py models
```

This blinded copy is intended only for the `models` and `run` commands. Preparation and comparison remain with the evaluation team.

Attach an IAM role that can list the chosen bucket prefix and get/put objects. Do not place AWS keys in this folder. Gemma is gated, so accept its Hugging Face license and provide `HF_TOKEN` to that job.

## Study design

All five judges receive the same substantive `criterion-evidence-gate-v1` policy while retaining their model-native output format. A judge must find evidence in the candidate response itself, check every part of a criterion, and fail vague, partial, implied, missing, incorrect, or contradicted requirements. Reference/background text cannot fill a gap in the response. Positive requirements use observable response evidence; negative/prohibition requirements use an explicit whole-response absence check. Equivalent wording, notation, and mathematically equivalent work remain acceptable unless exact form is required.

The runner records best-effort structured evidence plus the full rationale and raw output. Evidence extraction is for auditing and does not silently change the judge's native verdict.

`judge-normalization-v3` accepts one explicit Selene `Result: Yes/No` marker
whether or not the label is bold, and one explicit Qwen/Gemma JSON-style
`verdict: pass/fail` marker even when surrounding JSON is malformed by model
formatting or LaTeX escapes. Missing, duplicate, or conflicting verdict markers
remain `no_decision`; the parser never infers a verdict from rationale prose.

Each judge runs six waves over all 261 cases:

1. Three identical canonical runs: `canonical_r1`, `canonical_r2`, and `canonical_r3`.
2. Three controlled variants, each run once: `whitespace_r1`, `header_synonyms_r1`, and `instruction_politeness_r1`.

That produces 1,566 judgments per judge and 7,830 across all five judges. Temperature, seed, checkpoint, threshold, and task content stay frozen. The variants alter only fixed prompt scaffolding; they never alter the tutor response, criterion, reference material, or other case data. Each output records a frozen-configuration hash so the evaluation team can reject cross-wave setting drift.

## Submit the five jobs

Pass the shared S3 **project root**. The launcher automatically appends `/judge-validation-v3-evidence-gated/blinded/JUDGE/WAVE`, preventing collision with v2. Run one command per GPU job:

```bash
bash run_judge_suite.sh selene     s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh flow       s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh prometheus s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh qwen       s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh gemma      s3://YOUR-BUCKET/edu-judge-validation
```

Run these as five separate GPU jobs, ideally in parallel. Each launcher processes its six waves sequentially. Model weights are cached after the first download, although the current runner reloads the cached model for each wave.

The launcher gives every judge and wave a distinct prefix: `S3_PROJECT_ROOT/judge-validation-v3-evidence-gated/blinded/JUDGE/WAVE`. Local files go under `outputs/judge-validation-v3-evidence-gated/JUDGE/WAVE.jsonl`. Each completed batch is flushed locally, uploaded with a SHA-256 checksum, and followed by a manifest checkpoint. `--require-s3-upload` makes missing or failed S3 publishing fail the job.

Before a full launch, smoke-test each judge; for example, test Selene with three
cases per wave:

```bash
bash run_judge_suite.sh selene \
  s3://YOUR-BUCKET/edu-judge-validation \
  --limit 3
```

Then rerun the same command without `--limit`; resume mode retains those three cases and evaluates the remaining 258.

To resume on a replacement instance, restore this folder and run the same command. The runner downloads and verifies the saved checkpoint. If it reports that the remote run may still be active, first confirm the old job has stopped, then append `--allow-s3-takeover` to the command.

Return `s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded` to the evaluation team. They will download the 30 wave JSONLs and compare them locally against the separately retained `human_labels.csv`.

After transferring the folder, its contents can be checked with `sha256sum -c SHA256SUMS` on Linux.
