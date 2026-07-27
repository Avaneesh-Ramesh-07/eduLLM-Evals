# Operations & Troubleshooting — GPU Judge Grading

Runbook for running the frozen LLM judge (7B–12B, e.g. Qwen / Gemma-3-12B) via
vLLM on a Linux NVIDIA GPU (Ampere+ 40–48 GB target, vLLM pinned to `0.26.0`).

- Runner: `aws_judge_handoff/scripts/run_judge_validation.py` (`prepare` / `run` /
  `compare` / `models`).
- Launcher: `aws_judge_handoff/run_judge_suite.sh` (six waves for one judge).
- Deps: `aws_judge_handoff/requirements-aws.txt` (`vllm==0.26.0`, `boto3>=1.34`).

---

## Part 1 — Resume / Checkpoint / S3 / Logging (grounded in the runner code)

> Everything below is read directly from `run_judge_validation.py` / `run_judge_suite.sh`.
> Where a behavior is **not** in the code, that is called out explicitly.

### Output layout

For `--output outputs/<study>/<judge>/<wave>.jsonl`, the runner writes a **sibling
set** of files (naming comes from `Path.with_suffix` / helper functions):

| File | Role | Produced by |
| --- | --- | --- |
| `<wave>.jsonl` | Append-only data log; one JSON row per case | `run_cases` |
| `<wave>.manifest.json` | **The checkpoint** — status + provenance + S3 integrity block | `target.with_suffix(".manifest.json")` |
| `<wave>.retry_history.jsonl` | Archived rows replaced by `--retry-errors` | `_retry_history_path` |
| `<wave>.corrupt_tail.txt` | A truncated final line recovered on resume | `_corrupt_tail_path` |
| `<wave>.jsonl.lock` | Advisory `flock` so two processes can't write the same wave | `output_lock` |

There is **no separate integer "checkpoint counter" file** — the durable data is the
fsync'd JSONL append log, and `manifest.json` is the metadata checkpoint.

### How checkpoint / resume works

- **`--resume`** (`store_true`) is what allows writing into an existing output.
  Without it, if `<wave>.jsonl` or `<wave>.manifest.json` already exist locally the
  runner raises `FileExistsError` (`"...pass --resume or choose a new path"`).
- On resume, `_load_existing_results` reads every existing row and **re-validates**
  it before continuing. A row is only trusted (and therefore skipped) if its
  `configuration_hash`, `input_hash` (case content), `prompt_hash` (rendered prompt),
  `judge_name`, and `adapter` all still match. Any mismatch aborts with
  `"stale configuration"` / `"case input changed"` / `"rendered prompt changed"` — so
  resume can never silently blend drifted settings.
- **What gets skipped:** `pending = [case for case in selected if case not in existing]`.
  Already-completed `case_id`s are skipped; only the remainder are generated.
- **Batching + durability:** cases run in batches of `--batch-size` (default **32**).
  After each batch the runner does `handle.flush()` **and `os.fsync()`**, then fires the
  checkpoint callback. So a crash loses at most the in-flight batch.
- **Truncated-tail recovery:** on resume, a half-written final line is removed from the
  JSONL, appended to `<wave>.corrupt_tail.txt`, and the run continues (`WARNING:` on
  stderr). Any *non-tail* invalid JSON is a hard error.
- **`--retry-errors`:** on resume, rows whose `status != "ok"` or `verdict ∉ {pass,fail}`
  are moved into `<wave>.retry_history.jsonl` and re-run (attempt counter incremented).

#### Resuming on a replacement instance

1. Restore this bundle folder on the new box (`sha256sum -c SHA256SUMS`).
2. Re-run the **same** command, including `--resume` (the launcher already adds it) and
   the same `--s3-output-prefix`.
3. With an S3 prefix set, `_hydrate_run_from_s3` runs first: it `HEAD`s the remote
   manifest, verifies its `configuration-hash` and `cases-sha256` match this job, then
   **downloads + checksum-verifies** the manifest and JSONL (and retry/corrupt-tail
   files) *only if they're missing locally*.
4. If the remote manifest is still in `status: "starting"`, the runner refuses with
   `"remote S3 run may still be active ... pass --allow-s3-takeover"`. **First confirm
   the old job is dead**, then re-run with **`--allow-s3-takeover`**.

Edge cases enforced in code: a remote JSONL with **no** manifest → error
(`"remote judgment exists without its manifest"`); resume against a remote whose config
differs → error (won't clobber a different run).

### How S3 saving works, and how to make loss impossible

- **`--s3-output-prefix s3://bucket/...`** (or env `JUDGE_S3_OUTPUT_PREFIX`) enables the
  `S3Publisher`. **If no prefix is set, `publisher is None` and nothing is uploaded** —
  this is the silent-loss trap.
- **`--require-s3-upload`** guards against that: `_s3_publisher_from_args` raises if the
  flag is set but no prefix is configured. **Set both `--s3-output-prefix` and
  `--require-s3-upload`** (the launcher does) so a job can't run without publishing.
- **Every upload is verified, not fire-and-forget.** `S3Publisher.upload` sends
  `ChecksumSHA256` + a `sha256` metadata field, then re-`HEAD`s the object and raises
  `RuntimeError` on any size / SHA-256 / checksum mismatch (`"S3 checksum verification
  failed"`, etc.). Downloads verify the same way.
- **When uploads happen:** an initial sync at start, then a checkpoint sync after **every
  batch** (uploads the changed JSONL + rewrites/uploads the manifest with an integrity
  block), then a final sync at completion. During the run these calls are **not**
  best-effort — an upload failure propagates and **fails the job**.
- **On failure/interrupt** the manifest is stamped `failed`/`interrupted` and a
  `_best_effort_failure_sync` tries one last upload (errors here are swallowed into
  `s3_sync_error` so the original exception surfaces).

Net: with `--s3-output-prefix` + `--require-s3-upload`, a missing or corrupt upload
raises rather than being lost.

### What gets logged & how to check progress mid-run

- **stdout:** `vllm ... generate(use_tqdm=True)` prints a tqdm progress bar per batch;
  `S3Publisher` prints `Uploaded and verified: s3://...` / `Downloaded and verified: ...`.
- **`<wave>.manifest.json`** is the source of truth. It transitions
  `starting → complete | complete_with_errors | failed_no_usable_decisions | failed |
  interrupted`, and at completion records `new_rows`, `resumed_rows`,
  `usable_decisions`, `no_decision_rows`, `status_counts`, `elapsed_seconds`, plus a
  `runtime` block and an `s3.checkpointed_at` timestamp.
- **Mid-run checks (read-only, no shell needed on the operator's box):**
  - Count lines in `<wave>.jsonl` vs `case_count` in the manifest.
  - Read `manifest.s3.checkpointed_at` to see the last successful S3 checkpoint.
  - Or list/read the objects under the S3 prefix — they update every batch.

### Smoke-test-first pattern (`--limit`)

`--limit N` runs only the first `N` cases. Recommended flow (matches `README.md`):

```bash
# 1) Smoke test 3 cases across all six waves
bash run_judge_suite.sh selene s3://YOUR-BUCKET/edu-judge-validation --limit 3

# 2) Finish: rerun WITHOUT --limit; --resume (already in the launcher) keeps the
#    first 3 verified rows and processes the remaining 258.
bash run_judge_suite.sh selene s3://YOUR-BUCKET/edu-judge-validation
```

Resume keeps the smoke rows because their config/input/prompt hashes still validate.

> **Launcher guardrail:** `run_judge_suite.sh` **rejects** attempts to override frozen
> knobs (`--seed`, `--temperature`, `--top-p`, `--max-tokens`, `--prompt-variant`,
> `--s3-output-prefix`, etc.). Ops/tuning flags that *are* safe to pass through `"$@"`
> include `--limit`, `--gpu-memory-utilization`, `--max-model-len`,
> `--tensor-parallel-size`, `--dtype`, `--quantization`, and `--batch-size`.

### Runner-exposed vLLM knobs (defaults from argparse)

| Flag | Default | Notes |
| --- | --- | --- |
| `--gpu-memory-utilization` | `0.90` | fraction of VRAM for weights+KV |
| `--max-model-len` | `8192` | context ceiling; sizes the KV pool |
| `--tensor-parallel-size` | `1` | multi-GPU shard count |
| `--dtype` | `bfloat16` | set `float16` on non-Ampere |
| `--quantization` | *(none)* | e.g. `awq`, `gptq`, `fp8` |
| `--batch-size` | `32` | cases per generate() + checkpoint interval |
| `--max-tokens` | `1024` | frozen (launcher blocks override) |
| `--seed` / `--temperature` / `--top-p` | `42` / `0.0` / `1.0` | frozen |

> **Not exposed by this runner:** `--enforce-eager`, `--max-num-seqs`, and
> `--attention-backend` are **not** CLI flags here. The generator uses vLLM's in-process
> `LLM(...)` engine with `enable_prefix_caching=True` hardcoded. To use those knobs you'd
> either set the corresponding env var (see Part 2) or edit `VLLMGenerator.__init__`.

---

## Part 2 — vLLM issues & popular fixes

> General vLLM operational guidance (current best practice, 2026), not specific to this
> repo. Reflects vLLM `0.26.0`-era behavior. Where a fix maps to a runner flag above, use
> that; otherwise it applies when driving `vllm` directly or after editing the generator.

### Symptom → Cause → Fix

| Symptom | Likely cause | Actionable fix |
| --- | --- | --- |
| `ValueError: max seq len (N) larger than tokens storable in KV cache` at startup | KV pool too small after weights | Lower **`--max-model-len`** to your real 99th-pctile prompt+output first; then nudge **`--gpu-memory-utilization`** up in small steps (`0.90→0.92→0.94`, rarely `0.95`). |
| CUDA OOM on the fly (not startup) | Concurrency / long-context spikes | Reduce concurrency (`--max-num-seqs`, vLLM default 256 — not a runner flag), lower `--batch-size`, or drop `--max-model-len`. Don't set utilization to `1.0`. |
| OOM even after tuning; model too big for card | Weights dominate VRAM | **Quantize** (`--quantization awq|gptq|fp8`) and/or **`--tensor-parallel-size N`** across GPUs. Note: Ampere (sm80) does **not** support FP8 KV cache. |
| Startup CUDA-graph capture eats memory | CUDA graph capture overhead | **`--enforce-eager`** (env/engine arg) frees graph memory for KV at some throughput cost; also a stability fallback. |
| `RuntimeError: ... driver / CUDA version` or `undefined symbol` on import | Driver/CUDA vs vLLM `0.26.0` wheel mismatch | Check `nvidia-smi` (driver + CUDA). Install the torch/CUDA wheel matching the pinned vLLM (`pip install vllm==0.26.0` pulls its own torch); use a matching CUDA base image. Don't mix a hand-installed torch. |
| `GatedRepoError` / 401 loading Gemma | HF license not accepted / no token | Accept the model license on its HF page, export **`HF_TOKEN`**, or run `huggingface-cli login`. Pass the token into the GPU job's env. |
| `flash-attn` / `flashinfer` build or wheel failure | Trying to compile a mismatched attn wheel | vLLM **bundles** its own FlashAttention — don't `pip install flash-attn` unless told to. Prefer prebuilt wheels; if a backend misbehaves, **`--enforce-eager`** falls back off CUDA graphs. |
| Garbage / random output on newer arch with FlashInfer + FP8 KV + graphs | Backend/arch incompatibility | Switch backend (see note) or use `--enforce-eager` + BF16 KV cache as a known-good fallback. |
| `bfloat16 is not supported` / weird output on old GPU | Non-Ampere (pre-sm80) can't do BF16 | **`--dtype float16`**. |
| `ValueError: prompt (N tokens) longer than max_model_len` | Prompt exceeds context ceiling | Raise **`--max-model-len`** (trades against OOM) or enable **chunked prefill** (`--enable-chunked-prefill`); as a last resort shorten inputs. |
| Server/health won't come up, port in use | Port conflict / slow load | Change `--port`; wait past model load; hit `/health` / `/v1/models`. (This runner is in-process, not a server — N/A unless using `vllm serve`.) |
| Model download stalls / HF hub timeouts | Slow or flaky HF transfer | Set **`HF_HUB_ENABLE_HF_TRANSFER=1`** (`pip install hf_transfer`); pre-warm the HF cache; pin a `--revision` commit. |
| Wrong/empty verdicts, bad formatting | Tokenizer / chat-template mismatch | Ensure the model ships a chat template; verify `apply_chat_template` output. This runner freezes `enable_thinking` per judge and errors rather than silently dropping it. |
| Runs not reproducible across boxes | Nondeterminism | Keep **`--seed`** fixed (default `42`) and `--temperature 0.0`; keep dtype/quantization/TP **identical across all six waves** (the study rejects cross-wave drift via the frozen-config hash). |

### Short notes

- **Attention backend override:** `VLLM_ATTENTION_BACKEND` is **no-op'd in recent vLLM
  (≥0.19)** — the working override is the `--attention-backend` CLI flag on `vllm serve`
  (e.g. `FLASHINFER`, `FLASH_ATTN`, `TRITON_ATTN`). On Ampere/Hopper the default is
  `FLASH_ATTN`. Since this runner uses the in-process `LLM` engine, you'd pass this via
  the engine args (edit the generator) rather than a runner flag.
- **Quantization tradeoffs:** `awq`/`gptq` (4-bit) cut weight VRAM ~3–4× (big KV
  headroom) at a small quality/latency cost; `fp8` needs sm89+ (Ada/Hopper), **not
  Ampere**. Quantization does **not** help when OOM is driven by long context or high
  concurrency — reduce `--max-model-len` / `--max-num-seqs` instead.
- **Multi-wave reload cost:** per `README.md`, the launcher **reloads the cached model
  for each of the six waves**. Weights are cached after the first download, but expect
  ~model-load overhead × 6 per judge. Keep the HF cache warm; consider running judges as
  separate parallel GPU jobs (one judge per job, six waves sequential).
- **Driver sanity:** always confirm `nvidia-smi` shows a driver/CUDA compatible with
  vLLM `0.26.0` before debugging anything else.
