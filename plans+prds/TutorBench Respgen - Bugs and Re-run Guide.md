# TutorBench Response Generation — Bug Report & Re-run Guide

**Audience:** the engineer/agent re-running the TutorBench tutor-response generation job.
**Purpose:** this document is self-contained. It explains every known defect in the current
`tutor_cat/respgen` pipeline that corrupted the last run, gives concrete fixes (as diffs), and
provides a ready re-run plan so the next run produces clean, calibration-valid data.

> **Status of the last run:** 97 model shards were produced at `tutorbench-responses/*.jsonl`
> (one JSONL file per model, 662 records each = scenarios `tb_0001`…`tb_0662`). The schema is
> correct and row-complete, **but two independent bugs make a large fraction of the data
> invalid.** Nothing below was executed — the investigation was read-only (the shell was
> unavailable), and any code edits already present in `tutor_cat/respgen` are **UNTESTED**.

---

## 1. Executive summary

Two separate defects, plus a few smaller issues:

- **Bug #1 — vLLM engine-init load failures (~50 of 97 models are 662/662 `error` rows).** These
  models produced *no* output at all. Diagnosed into 5 signatures; **49/50 are fixable** via
  config/code + a re-run. Only `state-spaces/mamba-2.8b-hf` is genuinely hard in the current
  setup.
- **Bug #2 — prompt-truncation budget collapse (silently corrupts part of the "usable" set).**
  Every model with a context window ≤ 4096 had its prompt truncated to **1 token**, so it
  generated from an empty prompt and emitted off-task garbage that looks superficially valid
  (`Issue: 0`, non-empty `Output`). This affects models such as `phi-2`, `Phi-3-mini-4k`,
  `pythia-*`, `TinyLlama`, `OLMo-2-1124-7B`.

**Critical ordering constraint:** many of the Bug #1 models *also* have context ≤ 4096 (opt,
bloom, gpt2, RedPajama, phi-1_5, Cerebras, vicuna, deepseek, granite-3.0, MiniCPM, Yi, …).
**If you re-run them without fixing Bug #2 first, they will regenerate garbage.** Apply the
Bug #2 fix (Diff A below) *before* any re-run.

**Goal of the next run:** one fixed re-run that repairs **both** classes, so the calibration
fleet is as large and clean as possible.

---

## 2. Bug #1 — vLLM engine-init load failures (~50 dead models)

### Symptom
Each dead shard has all 662 rows with `"Finish Reason": "error"`, `"Issue": 1`,
`"Output": ""`, `"Rendered Prompt": ""`, `"Generation Params": {}`, and an `Issue Description`
that is usually the generic `load failed: RuntimeError('Engine core initialization failed. See
root cause above. Failed core proc(s): {}')`.

### Why the root cause was hidden
vLLM's V1 engine core crashes in a **subprocess**, and `runner.py` only stores `repr(e)` of the
wrapper exception — so the true stack trace was never captured. The generic message is a wrapper,
not the cause.

### Key evidence that these are NOT true architecture-incompatibilities
Many dead models have an **alive same-architecture sibling** from the same run:
- `Qwen/Qwen2.5-Coder-7B` (dead) vs `Qwen/Qwen2.5-7B-Instruct` (alive)
- `HuggingFaceTB/SmolLM2-360M-Instruct` (dead) vs `SmolLM2-360M` (alive)
- `EleutherAI/gpt-neo-2.7B` (dead) vs `gpt-neo-1.3B` (alive)

Also confirmed: `google/gemma-2-2b` and `mistralai/Mistral-7B-v0.1` are **alive with real output
and pinned revisions**, and both are gated — so the box's `HF_TOKEN` is valid and has gemma +
mistral access.

**Leading hypothesis for the largest group:** GPU memory is **not released between sequential
model loads in a reused worker process**, so later models in each worker's queue OOM at engine
init. (~47 alive / ~50 dead ≈ half-and-half is consistent with cumulative leakage across
~12 models per worker.)

### The 5 signatures

**Group A — `Engine core initialization failed` (vLLM V1 core crash) — 41 models.**
All architectures current vLLM supports (proven by alive siblings). Root cause not captured
(only `repr(e)`). Leading cause: cross-load GPU memory leakage → OOM.

| Family | Ids | Verdict |
|---|---|---|
| OPT | `facebook/opt-1.3b`, `opt-2.7b`, `opt-6.7b` | Fixable — systemic memory/transient |
| BLOOM/BLOOMZ | `bigscience/bloom-560m,-1b1,-1b7,-3b,-7b1`, `bloomz-1b7,-3b,-7b1` | Fixable |
| GPT-2 | `openai-community/gpt2,-medium,-large,-xl` | Fixable |
| Yi | `01-ai/Yi-6B`, `Yi-6B-Chat` | Fixable — Llama arch |
| InternLM2 | `internlm/internlm2-1_8b,-7b`, `internlm2_5-7b` | Fixable — needs `trust_remote_code` (already set) |
| DeepSeek | `deepseek-ai/deepseek-llm-7b-base`, `deepseek-coder-6.7b-base`, `deepseek-math-7b-base` | Fixable — Llama arch |
| Granite 3.x | `ibm-granite/granite-3.0-2b-base`, `granite-3.1-2b-base`, `granite-3.1-2b-instruct` | Fixable |
| Falcon-7B (old) | `tiiuae/falcon-7b`, `falcon-7b-instruct` | Fixable — `RWForCausalLM` via trust_remote_code |
| Vicuna | `lmsys/vicuna-7b-v1.5` | Fixable — Llama-2 arch |
| Zephyr-β | `HuggingFaceH4/zephyr-7b-beta` | Fixable — Mistral arch |
| RedPajama | `togethercomputer/RedPajama-INCITE-7B-Base`, `-Base-3B-v1` | Fixable — GPT-NeoX arch |
| MiniCPM | `openbmb/MiniCPM-2B-sft-bf16` | Fixable — trust_remote_code |
| Nemotron-Mini | `nvidia/Nemotron-Mini-4B-Instruct` | Fixable |
| SmolLM2 | `HuggingFaceTB/SmolLM2-360M-Instruct` | Fixable — base 360M is alive |
| StableLM | `stabilityai/stablelm-3b-4e1t` | Fixable |
| Phi-1.5 | `microsoft/phi-1_5` | Fixable |
| h2o-danube | `h2oai/h2o-danube2-1.8b-base`, `h2o-danube3-4b-base` | Fixable — Llama arch |
| Qwen-Coder | `Qwen/Qwen2.5-Coder-7B` | Fixable — same arch as alive Qwen2.5-7B |
| Amber | `LLM360/Amber` | Fixable — Llama arch |

**Group B — `OSError('… is not a valid model identifier … pass a token')` — 5 models.**
`cerebras/Cerebras-GPT-1.3B, -2.7B, -6.7B`, `mosaicml/mpt-7b`, `mpt-7b-instruct`. These are
**public repos** and other repos downloaded fine on the same box → **transient Hub
repo-resolution failure** (8 workers hammering the Hub → rate-limit/blip surfaced as 401/404).
Fixable via re-run. (MPT also needs `trust_remote_code`, already set.)

**Group C — gated tokenizer 403 — 2 models.**
`apple/OpenELM-3B`, `apple/OpenELM-1_1B`: `403` on `meta-llama/Llama-2-7b-hf`. OpenELM ships **no
tokenizer** and points at gated Llama-2, which this account isn't granted. Fixable via config
(Fix 1).

**Group D — mamba load-time CUDA assert — 1 model (the only hard one).**
`state-spaces/mamba-2.8b-hf`: `CUDA error: device-side assert triggered` during the HF smoke
test. Needs `mamba-ssm` + `causal-conv1d` kernels built for the box's CUDA/torch. **Flag for
manual bring-up; not a config toggle.** Drop from the re-run unless kernels are installed.

**Group E — gpt-neo generation-time CUDA assert — 1 model (watch-item).**
`EleutherAI/gpt-neo-2.7B`: loaded fine, then `generation failed: AcceleratorError(device-side
assert)` on the batched run. 1.3B sibling is alive → likely transient. Fixable via re-run; if it
recurs, route to `hf_fallback` or `enforce_eager=True`.

**Bottom line: 49 fixable-via-config/code + re-run, 1 genuinely hard (`mamba-2.8b-hf`), with
`gpt-neo-2.7B` as a watch-item.**

---

## 3. Bug #2 — prompt-truncation budget collapse (affects the "usable" set too)

### Symptom
Models that **loaded fine** (`Issue: 0`, non-empty `Output`) but whose input was truncated to a
single token, so they generate scenario-independent garbage that is ~identical on every one of
the 662 rows. These pass a naive "not an error row" filter but are **invalid data**.

### Root cause
`runner._prompt_tokens_and_truncate` computes:

```python
budget = max(1, max_model_len - max_new_tokens)
```

With `max_new_tokens = 4096`, **any model whose clamped `Max Model Len ≤ 4096` gets a prompt
budget of 1 token.** `HFBackend.generate` has the identical bug:

```python
input_cap = max(1, self.max_model_len - params.max_new_tokens)
```

The truncation also drops the **head** (the actual question) and keeps the tail, so the surviving
token is the trailing `":"` of `"…Tutor:"` (or `"<|assistant|>"` for chat models).

### Confirmed live examples (all `Prompt Tokens: 1`, `Truncated: 1`, off-task output)
- `microsoft/phi-2` (2048), `microsoft/Phi-3-mini-4k-instruct` (4096)
- `EleutherAI/pythia-1b` (2048), `EleutherAI/gpt-neo-1.3B` (2048)
- `TinyLlama/TinyLlama-1.1B-Chat-v1.0` (2048), `allenai/OLMo-2-1124-7B` (4096)

**Detection rule:** `Max Model Len ≤ 4096` (given `max_new_tokens=4096`) ⇒ broken. This hits
small-context **instruct** models too (truncation happens regardless of chat template).

> **Do not enumerate the affected set by scanning `Output` text** — these degenerate outputs make
> the JSONL lines enormous, and line-oriented search tools silently skip them. Enumerate from
> model context-window sizes / the roster instead, and verify with a validator pass after the
> re-run.

### Contrast (healthy)
`mistralai/Mistral-7B-v0.1` and `tiiuae/Falcon3-7B-Base` are base models (`Chat Template
Applied: 0`) with `Max Model Len: 32768` → full `"System:/Student:/Tutor:"` prompt (~800 tokens,
`Truncated: 0`) and coherent answers. Also note `meta-llama/Llama-3.2-1B` had a *correct* prompt
(703 tokens) but produced degenerate repetition — that is **model weakness, not this bug**, and is
legitimate low-ability calibration data to keep.

### Fix — Diff A (apply BEFORE any re-run of ≤4096-context models)
Reserve at least half the window for the prompt, in **both** the runner and HFBackend:

```python
# tutor_cat/respgen/runner.py  (_prompt_tokens_and_truncate)
- budget = max(1, max_model_len - max_new_tokens)
+ # Never let the generation budget consume the whole window: reserve at least
+ # half the context for the prompt so short-context models keep a usable input.
+ eff_new = min(max_new_tokens, max(1, max_model_len // 2))
+ budget = max(1, max_model_len - eff_new)
```

```python
# tutor_cat/respgen/backends.py  (HFBackend.generate)
- input_cap = max(1, self.max_model_len - params.max_new_tokens)
+ eff_new = min(params.max_new_tokens, max(1, self.max_model_len // 2))
+ input_cap = max(1, self.max_model_len - eff_new)
```

Optionally also clamp `SamplingParams.max_tokens` / `gen_kwargs["max_new_tokens"]` to `eff_new`
so short-context outputs aren't cut mid-stream by the context ceiling.

---

## 4. Other issues worth fixing on the re-run

- **`Latency (s)` is `null` on every record.** Generation wall-clock is not being captured. Minor,
  but if you want the "time per tutor call" metric, wire timing into the record writer.
- **`--no-resume` appended onto stale error shards.** Because the dead shards already contain all
  662 `Scenario` ids, `completed_ids` treats them as "done" and resume skips them, while a naive
  no-resume *appends* duplicates. Fixed by truncating the shard on no-resume (Fix 4 below).
  Regardless, **delete the 50 stale error shards before re-running** as belt-and-suspenders.
- **The generic vLLM error message hid every Group-A root cause.** For the first debugging pass,
  run the failed set serialized (`--gpus 1` or `--model <one-id>`) so the subprocess traceback is
  readable, or set `VLLM_LOGGING_LEVEL=DEBUG` and tee worker stderr. Optionally widen
  `error_record` to also store the last N stderr lines (Diff B).
- **Process reuse vs "one model per process."** `backends.py` documents *"one model per
  process,"* but `orchestrator._worker` reuses each process for many models off the queue — the
  exact condition that lets vLLM memory accumulate (Bug #1, Group A). The most robust fix is a
  fresh process per model (Diff C); the lighter in-process teardown is Fix 3.

---

## 5. Fixes already drafted in `tutor_cat/respgen` (UNTESTED)

These were written into the source as safe, additive changes that do **not** alter the ~47
working models. They still need to be validated in a working environment (the shell was down).
If you are re-running in a different environment, apply the equivalent changes there.

- **Fix 1 — OpenELM ungated tokenizer (Group C).** Optional `tokenizer_id` plumbed through
  `ModelSpec → registry.resolve → HFBackend`, with a heuristic mapping
  `openelm → NousResearch/Llama-2-7b-hf` (byte-identical, ungated). Only affects models carrying
  the override.
- **Fix 2 — vLLM → HF load fallback (Groups A/E safety net).** In `runner._load_backend`, a vLLM
  init/smoke failure retries on the transformers path instead of dead-lettering all 662
  scenarios. Runs only on the vLLM error path. *(Caveat: the HF path is much slower per prompt —
  watch any model that falls back.)*
- **Fix 3 — Guarded GPU-memory teardown between models (primary Group-A fix).** New
  `_free_backend()` (`destroy_model_parallel` + drop refs + `gc.collect` +
  `torch.cuda.empty_cache`, fully guarded) called at the end of `run_model` after outputs are
  flushed, so a reused worker starts each next model with clean device memory.
- **Fix 4 — Truncate on `--no-resume`.** `ShardWriter(path, truncate=not resume)`, so no-resume
  regenerates cleanly instead of appending onto stale error rows. Append remains the default.

**Still proposed as diffs (NOT auto-applied, because they change outputs of currently-passing
models):** Diff A (§3, the truncation fix — **required** before re-running ≤4096-context models),
Diff B (capture real vLLM root cause), Diff C (process-per-model).

---

## 6. Ready re-run plan

Real CLI flags (from `cli.py`): `tutor-cat generate --models --scenarios --out-dir --s3-uri
--gpus --gpu-ids --limit --model --dry-run --no-resume`. (`--model` runs a single id; for a
subset use a dedicated manifest.)

### Prerequisites
1. **Apply Diff A (truncation) first** — otherwise every ≤4096-context model regenerates garbage.
2. **`HF_TOKEN`** in `.env` (loaded by `cmd_generate._load_env()`). gemma/mistral access is
   already confirmed. For OpenELM either rely on Fix 1's ungated mirror **or** accept the Llama-2
   license on the token's account.
3. Install `mamba-ssm` + `causal-conv1d` **only** if you want to attempt `mamba-2.8b-hf`
   (Group D); otherwise drop it from the re-run.
4. **Delete the 50 stale error shards** before re-running (belt-and-suspenders even with Fix 4).

### Manifest
Create `models_rerun.yaml` containing only the 50 failed ids (defaults inherited; OpenELM
auto-gets the tokenizer override via the heuristic). Optionally add:
```yaml
- id: EleutherAI/gpt-neo-2.7B
  backend: hf_fallback   # only if the CUDA assert recurs
```
and drop `state-spaces/mamba-2.8b-hf` unless kernels are installed.

### Dry-run first (offline, no weights)
```
tutor-cat generate --models models_rerun.yaml --dry-run --limit 3
```
Confirm OpenELM shows `tokenizer=NousResearch/Llama-2-7b-hf` and backends resolve as expected.

### First debugging pass (serialized, to see real tracebacks for Group A)
```
tutor-cat generate --models models_rerun.yaml \
  --scenarios data/scenarios.jsonl \
  --out-dir tutorbench-responses \
  --gpus 1 \
  --no-resume
```
Confirm the memory-teardown fix holds and prompts are non-empty, then scale up.

### Full re-run (targets only the failed set, regenerates from scratch)
```
tutor-cat generate --models models_rerun.yaml \
  --scenarios data/scenarios.jsonl \
  --out-dir tutorbench-responses \
  --gpus 8 \
  --no-resume \
  --s3-uri s3://<bucket>/<prefix>
```
- `--no-resume` is required (shards already contain all 662 Scenario ids as errors). With Fix 4
  this truncates cleanly instead of duplicating.
- Expected wall time: a few hours for ~49 models across 8 GPUs. Any model that hits the vLLM→HF
  fallback will be **much slower** (per-prompt HF loop) — watch those.

---

## 7. Post-run validation checklist

After the re-run, confirm the data is clean:

- [ ] Per re-run shard, `"Finish Reason": "error"` count ≈ 0 (Bug #1 resolved).
- [ ] Every non-error row has `Prompt Tokens` well above 1 (proves Diff A landed; the old broken
      rows were exactly 1).
- [ ] No non-error row has `"Rendered Prompt": ":"` / `"<|assistant|>"`-only.
- [ ] `Truncated: 1` only appears on genuinely long prompts, not universally.
- [ ] Coverage: 97 shards × 662 scenarios, no missing `tb_XXXX`, no duplicate `Scenario` per
      shard.
- [ ] Spot-check that outputs **differ across scenarios** within a shard (catches any residual
      empty-prompt collapse).
- [ ] Re-validate the previously-"usable" ≤4096-context models (phi-2, Phi-3-mini-4k, pythia-*,
      TinyLlama, OLMo-2-1124-7B, …) — they must be re-run too, not just the 50 dead ones.

---

## 8. Appendix — evidence pointers

- Dead shards: any of `tutorbench-responses/{facebook_opt-1.3b, bigscience_bloom-1b1,
  openai-community_gpt2, state-spaces_mamba-2.8b-hf, apple_OpenELM-3B, 01-ai_Yi-6B}.jsonl` —
  every row `Finish Reason: error`, empty `Output`, `Generation Params: {}`.
- Truncation-broken (loaded but 1-token prompt): `tutorbench-responses/allenai_OLMo-2-1124-7B.jsonl`
  (`Max Model Len 4096`, `Prompt Tokens 1`, `Truncated 1`, `Rendered Prompt ":"`, off-task
  "Student1/Student2 … 500 books" output repeated across rows).
- Healthy base-model contrast: `tutorbench-responses/mistralai_Mistral-7B-v0.1.jsonl` and
  `tiiuae_Falcon3-7B-Base.jsonl` (`Max Model Len 32768`, full prompt, coherent output).
- Alive gated models proving `HF_TOKEN` works: `google_gemma-2-2b`, `mistralai_Mistral-7B-v0.1`.
