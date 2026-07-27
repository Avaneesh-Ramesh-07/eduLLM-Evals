# Human–LLM judge validation

This workflow measures how closely each candidate judge agrees with the human pass/fail labels. It is separate from the CAT evaluation loop: it grades the fixed tutor responses once, preserves each judge's raw output, and then calls `scripts/compare_judges.py` to produce the comparison metrics.

The current runner uses the `judge-validation-v3` evidence-gated prompt. Because the earlier v2 results on these 261 cases motivated this prompt, the v3 rerun is a development/recalibration comparison, not an untouched acceptance test. Freeze the selected configuration and use separately labeled, unseen scenarios for the final acceptance decision.

The three stages are:

1. `prepare` combines canonical task text with each packet's tutor response and human label to create one case per response/criterion pair.
2. `run` evaluates all cases with one frozen judge. Run this command once per judge, normally as a separate GPU job.
3. `compare` joins the normalized judge verdicts to the human labels and creates the report.

## Prerequisites

- Python 3.10 or newer.
- A Linux NVIDIA GPU host for the default vLLM backend. The runner uses POSIX advisory file locks. A 24 GB GPU is a reasonable starting point for the three primary judges; 40–48 GB is the simplest choice for every registered judge in BF16, including Gemma 3 12B. Smaller GPUs may require tensor parallelism or quantization.
- Enough persistent disk for the repository, environment, and model cache. Budget at least 100 GB if all five checkpoints will be cached together.
- Network access for the initial Hugging Face downloads.
- Hugging Face access to `google/gemma-3-12b-it`. Accept the Gemma license before the job starts and make `HF_TOKEN` available to the process. The other registered checkpoints are not gated.

On the AWS GPU instance, install the project and its separate GPU extra:

```bash
python3 -m venv .venv-judge
source .venv-judge/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[judge-gpu]"
python scripts/run_judge_validation.py models
```

The GPU extra pins vLLM separately from the ordinary laptop/test dependencies. Use a CUDA driver and AMI or container compatible with that vLLM release. A persistent Hugging Face cache prevents every job from downloading the weights again.

The v3 comparison evaluates all five registered judges: three specialized judges (Selene Mini, Flow-Judge, and Prometheus 2) plus two general-model controls (Qwen3.5-9B and Gemma 3 12B). `models` prints every exact model ID and pinned revision. Direct vLLM runs load Qwen and Gemma in language-model-only mode so their unused vision components do not consume GPU memory.

### AWS/S3 permissions

Use an EC2 instance profile or job role rather than static AWS keys. The runner uses boto3's normal credential chain and never needs credentials in a command line. `judge-gpu` already installs boto3; a CPU-only comparison host can instead install `python -m pip install -e ".[judge-aws]"`.

The role needs `s3:PutObject` and `s3:GetObject` for the study objects. It also needs `s3:ListBucket` on the bucket so a `HEAD` request for a not-yet-created checkpoint returns “not found” rather than an ambiguous access-denied response. A minimal policy is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListJudgeValidationPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::YOUR-BUCKET"
    },
    {
      "Sid": "ReadWriteJudgeValidationArtifacts",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/*"
    }
  ]
}
```

No delete permission is required. If the bucket uses a customer-managed KMS key, also grant `kms:Decrypt` and `kms:GenerateDataKey` on that key. Bucket versioning is recommended as an additional recovery layer.

## 1. Prepare the cases

Preparation requires the complete 10-scenario × 3-tutor response matrix by default. Run:

```bash
python scripts/run_judge_validation.py prepare \
  --packets-dir grader_packets \
  --scenarios grader_packets/sample_scenarios.jsonl \
  --rubrics grader_packets/sample_rubrics.jsonl \
  --out-dir runs/judge_validation_v2
```

Judge prompts use the scenario prompt, structured conversation context, and reference solution from `sample_scenarios.jsonl`, plus the criterion text from `sample_rubrics.jsonl`. These canonical fields are shared across the three tutors so Markdown reformatting or packet-copy drift cannot give one tutor different task text. Hidden `expected_evidence` is not included.

Every adapter receives the same `criterion-evidence-gate-v1` policy while keeping its native verdict format. Evidence must come from the candidate response; the task, criterion, and reference/background cannot supply missing content. Positive requirements need observable response text or work, multi-part requirements need support for every part, and negative/prohibition requirements require an explicit whole-response absence check. Missing, partial, vague, implied, incorrect, or contradicted requirements fail. Equivalent wording, notation, and mathematics remain acceptable unless the criterion explicitly requires exact form. Evidence is extracted on a best-effort basis for auditability and never silently overrides the native verdict.

The candidate tutor response and human P/F label come from each `grader_*.md` packet. Packet copies of the prompt, context, reference, and criterion are parsed only to audit whether the humans saw text that differs from the canonical source. Thus, the judge's evaluated task text is canonical, while the response and gold judgment remain the actual packet artifacts.

Preparation writes:

- `judge_cases.blinded.jsonl`: scenario, response, and criterion data used to build judge prompts;
- `human_labels.csv`: the separately stored human gold labels and analysis metadata;
- `prepare_manifest.json`: case counts, packet/source hashes, and exact packet-versus-source mismatch counts.

`packet_source_exact_mismatch_counts` reports, for each audited field, how many case rows have a packet copy that is not textually identical to the canonical source. These are exact-string diagnostics, so harmless Markdown or whitespace changes can count as mismatches. Review unexpectedly high counts before launching the GPU jobs; judge prompts still use the canonical source.

Packet reference placeholders—blank text, `(not provided)`, `not provided`, `N/A`, or `none`—are normalized to an absent reference before mismatch accounting. When the canonical source has no reference, every judge adapter receives its standard no-reference instruction rather than packet placeholder text.

The judge case file is blinded at the file level: it contains assignment-based IDs but no human labels, notes, anonymous tutor names, candidate-model names, or model slugs. The real tutor mapping exists only in `human_labels.csv`. The runner also rejects forbidden gold or identity fields before inference.

For the current sample, preparation produces 261 cases from 30 tutor responses and 10 scenarios. All 261 packet labels are currently binary P/F. Generate the final snapshots with the strict label gate:

```bash
python scripts/run_judge_validation.py prepare \
  --out-dir runs/judge_validation_v2 \
  --overwrite \
  --require-complete-labels
```

Do not use `--allow-incomplete-matrix` for the final study; it is only for development fixtures or partial dry runs. Only `judge_cases.blinded.jsonl` needs to go to GPU workers. Keep `human_labels.csv` on the comparison side so inference remains operationally separate from the gold labels. The `run` command's S3 integration uploads judgment artifacts only; it never uploads the blinded input file or `human_labels.csv`.

### Human-label adjudication gate

Before interpreting human-agreement metrics, independently re-review ambiguous or disputed human labels against only the criterion and tutor response, without showing reviewers any judge verdict. A spot audit already flagged `grader_01_item_01__tb_0001_c02` and `grader_01_item_01__tb_0001_c03`: both are labeled Fail even though the response appears to contain the requested pH-to-ionic-concentration formula and Le Chatelier explanation. These are adjudication candidates, not automatic label changes. Record reviewer identities, the resolved label, and a short rationale; then freeze and version the adjudicated label file before comparing v3. Otherwise a more valid evidence-gated judge can score worse merely because it disagrees with an erroneous human label.

## 2. Run one judge per GPU job

The default backend loads the pinned Hugging Face checkpoint directly with vLLM. Use one job, local output, and S3 prefix per judge. The five registered jobs are:

```bash
# Selene Mini 8B
python scripts/run_judge_validation.py run \
  --cases runs/judge_validation_v2/judge_cases.blinded.jsonl \
  --judge selene \
  --output runs/judge_validation_v3_evidence_gated/judgments/selene.jsonl \
  --backend vllm \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded/selene \
  --require-s3-upload

# Flow-Judge 3.8B
python scripts/run_judge_validation.py run \
  --cases runs/judge_validation_v2/judge_cases.blinded.jsonl \
  --judge flow \
  --output runs/judge_validation_v3_evidence_gated/judgments/flow.jsonl \
  --backend vllm \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded/flow \
  --require-s3-upload

# Prometheus 2 7B
python scripts/run_judge_validation.py run \
  --cases runs/judge_validation_v2/judge_cases.blinded.jsonl \
  --judge prometheus \
  --output runs/judge_validation_v3_evidence_gated/judgments/prometheus.jsonl \
  --backend vllm \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded/prometheus \
  --require-s3-upload

# Qwen3.5 9B control
python scripts/run_judge_validation.py run \
  --cases runs/judge_validation_v2/judge_cases.blinded.jsonl \
  --judge qwen \
  --output runs/judge_validation_v3_evidence_gated/judgments/qwen.jsonl \
  --backend vllm \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded/qwen \
  --require-s3-upload

# Gemma 3 12B control
python scripts/run_judge_validation.py run \
  --cases runs/judge_validation_v2/judge_cases.blinded.jsonl \
  --judge gemma \
  --output runs/judge_validation_v3_evidence_gated/judgments/gemma.jsonl \
  --backend vllm \
  --resume \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded/gemma \
  --require-s3-upload
```

Those commands perform one canonical wave. For the complete v3 development/recalibration study, use the blinded `aws_judge_handoff` bundle instead. Its launcher runs six waves for one judge: three identical canonical repeats plus the `whitespace`, `header_synonyms`, and `instruction_politeness` variants. Submit one launcher per judge/GPU:

```bash
bash run_judge_suite.sh selene     s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh flow       s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh prometheus s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh qwen       s3://YOUR-BUCKET/edu-judge-validation
bash run_judge_suite.sh gemma      s3://YOUR-BUCKET/edu-judge-validation
```

This produces 1,566 judgments per judge and 7,830 total. The launcher automatically namespaces local and S3 artifacts under `judge-validation-v3-evidence-gated`, so they cannot overwrite v2. Every wave has a unique `JUDGE/WAVE` suffix. The three repeats keep the prompt, checkpoint, seed, temperature, and decoding settings identical; only `replicate_id` changes. Each controlled variant changes fixed prompt scaffolding without changing the task, tutor response, reference, or criterion. Every judgment also records a frozen-configuration hash that excludes only the planned prompt-variant and replicate identifiers; comparison rejects a judge whose remaining settings differ across waves.

Run all five judges for this v3 comparison. The jobs may run concurrently on separate GPUs or sequentially as separate jobs on one GPU. Never share a local output or S3 prefix between judges or waves. A local nonblocking `<output>.lock` prevents same-host corruption, while the remote manifest rejects a different configuration or case file at an occupied S3 prefix.

After every batch, the runner flushes and fsyncs the local JSONL, uploads it with an S3 SHA-256 checksum, verifies the stored checksum and size, and uploads the manifest last as the checkpoint marker. `--require-s3-upload` fails early if no S3 destination is configured; whenever an S3 destination is configured, a checkpoint/upload failure fails the job. Each prefix contains the judgment JSONL, its manifest, and any retry-history or recovered-tail audit file.

`--resume` can hydrate missing local artifacts from the same S3 prefix on a replacement instance, verify them, and continue only the missing cases. Keep the same output basename. A remote manifest with status `starting` may belong to a live job; the runner refuses to take it over. After confirming the old job has stopped, resume explicitly with the original command plus `--allow-s3-takeover`. This flag is a safety override, not a distributed lock.

The defaults freeze greedy decoding, seed 42, an 8,192-token context limit, and the registered prompt/parser for each judge. Qwen thinking is disabled, Qwen and Gemma use language-model-only loading, Prometheus scores 4–5 map to pass, and `trust_remote_code` is **false** by default. Enable `--trust-remote-code` only after reviewing and intentionally accepting checkpoint code.

Before spending a full job, a short smoke test is useful:

```bash
bash run_judge_suite.sh selene \
  s3://YOUR-BUCKET/edu-judge-validation \
  --limit 3
```

Run the same launcher without `--limit` afterward. Existing rows are retained only when their input, rendered prompt, and frozen configuration hashes still match. Lowering `--batch-size` is safe if a job runs out of memory. Changing the checkpoint, revision, quantization, prompt-relevant generation settings, or Prometheus threshold defines a different judge configuration and should use a new output path.

If a completed run contains parse or generation failures, inspect those rows and retry only the errors with the exact same run arguments:

```bash
bash run_judge_suite.sh selene \
  s3://YOUR-BUCKET/edu-judge-validation \
  --retry-errors
```

Valid decisions remain untouched. Replaced error rows are preserved in `judgments/selene.retry_history.jsonl`, and the new canonical row records an incremented attempt number. Resume mode can also recover an interrupted, truncated final JSONL line and archives that tail separately.

Each judgment JSONL row includes the normalized verdict, native score, rationale, evidence, raw model output, parse/generation status, errors, hashes, attempt number, checkpoint provenance, and latency. A sibling `*.manifest.json` records the checkpoint revision, settings, runtime versions, Git commit, usable-decision count, and completion status. Parsing and generation failures are recorded as `no_decision`; they are not silently converted to failures.

For an already-running OpenAI-compatible vLLM server, use `--backend openai` with `--base-url` and `--served-model`. The selected `--judge` still controls the prompt and parser, so it must match the checkpoint hosted by that endpoint. The runner cannot verify which checkpoint or revision the server actually loaded; these runs are explicitly recorded as `openai_compatible_endpoint_unverified` with `checkpoint_verified_by_runner=false`. Use direct vLLM loading for the primary frozen comparison, or independently verify and document the server deployment.

## 3. Compare with the human labels

After every judge file contains the complete case set, run:

```bash
python scripts/run_judge_validation.py compare \
  --human-labels runs/judge_validation_v2/human_labels.csv \
  --judgments \
    runs/judge_validation_v3_evidence_gated/judgments/selene.jsonl \
    runs/judge_validation_v3_evidence_gated/judgments/flow.jsonl \
    runs/judge_validation_v3_evidence_gated/judgments/prometheus.jsonl \
    runs/judge_validation_v3_evidence_gated/judgments/qwen.jsonl \
    runs/judge_validation_v3_evidence_gated/judgments/gemma.jsonl \
  --out-csv runs/judge_validation_v3_evidence_gated/comparison.csv \
  --json-out runs/judge_validation_v3_evidence_gated/comparison_summary.json \
  --disagreements-out runs/judge_validation_v3_evidence_gated/disagreements.csv \
  --require-complete-labels \
  --require-complete-judgments \
  --s3-output-prefix s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/unblinded/comparison \
  --require-s3-upload
```

This prints the report and writes:

- `comparison.csv`: human labels plus one normalized column per judge;
- `comparison_summary.json`: accuracy, balanced accuracy, pass/fail recall, false-pass rate, coverage, confusion counts, tutor-model slices, and scenario-clustered confidence intervals;
- `disagreements.csv`: cases where at least one judge differs from the human label.

`--require-complete-judgments` rejects the comparison if any judge is missing a case or has an unparseable/error row. `--require-complete-labels` rejects missing or nonbinary human labels. Omit either flag only for an explicitly exploratory report. Do not mix v2 and v3 judge outputs; the reliability comparison rejects differing prompt or normalization versions across judges.

Use a separately permissioned **unblinded** S3 prefix for comparison outputs. The comparison command never uploads the source `human_labels.csv` or source judgment JSONLs, but `comparison.csv` itself contains the joined human labels. It uploads the result files first and a `comparison.manifest.json` completion marker last; an occupied prefix with different inputs or settings is rejected.

Without the strict judgment gate, missing, malformed, or failed judgments become `no_decision`, reduce coverage, and count as incorrect in end-to-end accuracy. The merge also verifies that every judgment's input hash matches the prepared human-label snapshot before comparing it.

## 4. Compute six-wave development metrics against the retained targets

Download the blinded wave outputs while keeping the human labels local:

```bash
aws s3 cp \
  s3://YOUR-BUCKET/edu-judge-validation/judge-validation-v3-evidence-gated/blinded \
  runs/judge_reliability_v3/waves \
  --recursive
```

Then run the local wrapper. It expects the S3 layout created by `run_judge_suite.sh`:

```bash
scripts/compare_reliability_suite.sh \
  runs/judge_validation_v2/human_labels.csv \
  runs/judge_reliability_v3/waves \
  runs/judge_reliability_v3/reliability_report.json \
  runs/judge_reliability_v3/reliability_summary.csv
```

The report includes criterion-level binary macro-F1 and weighted F1, decided-case MCC with coverage, critical-failure sensitivity, F1 for each mapped primary skill, three-run exact agreement and Cohen's kappa, prompt-variant flip rates, provenance, status counts, and scenario-clustered confidence intervals. It retains the same numerical target thresholds for diagnostic comparison with v2:

- macro-F1 ≥ 0.80;
- critical-failure sensitivity ≥ 0.90, combining `critical` and `critical_negative` human failures;
- worst pairwise strict test–retest agreement ≥ 0.90;
- macro-F1 ≥ 0.70 for every mapped primary skill;
- worst prompt-variant flip rate ≤ 0.10.

`no_decision` penalizes F1, strict repeat agreement, and prompt consistency. The 39 cases without a primary-skill mapping are reported as `unmapped` but excluded from the per-skill acceptance gate. Add `--enforce-thresholds` when a nonzero exit status is desired for any failed gate; the JSON and CSV are written before exit code 3.

Formal IRT marginal reliability is recorded as `not_computed`: three tutor models and repeated ratings do not identify that statistic. The report provides MCC plus coverage as the prespecified non-IRT signal surrogate. The reduced six-wave design also does not estimate prompt-by-repeat interactions because each perturbed prompt is run only once.

These files are unblinded because they join human labels. If they must be retained in S3, place them under the separately permissioned unblinded results prefix rather than any GPU-worker prefix.

## Methodological caveats

- This study evaluates a complete judge configuration—checkpoint, prompt, parser, threshold, precision, and inference settings—not the checkpoint in isolation.
- This v3 rerun intentionally uses the diagnosed v2 cases as development data. Its thresholds are diagnostic, not a final pass/fail claim. Do not tune again on the unseen final holdout; freeze the complete winning configuration first.
- The cases share only a small number of scenarios and multiple criteria grade the same tutor response. Use the scenario-clustered intervals in the report and avoid treating criterion rows as fully independent.
- Quantized and BF16 runs are different judge configurations. Record and compare them separately rather than mixing their outputs.
- An OpenAI-compatible endpoint name is not proof of its underlying checkpoint. Treat endpoint results as unverified unless deployment provenance is established independently.
- Keep the raw JSONL and manifests. They are needed to audit parsing failures, reproduce the run, and explain disagreements with human graders.
