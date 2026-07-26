# TutorBench Respgen — Bugs & Re-run Guide

**Purpose:** everything a coding agent needs to fix the TutorBench response-generation
harness (`tutor_cat/respgen/`) and do **one clean AWS re-run** that repairs two
independent classes of defect at once. Self-contained — no prior context required.

**Do not treat the current `tutorbench-responses/*.jsonl` as final.** Two bugs mean
the real number of trustworthy model shards is far below the ~47 that "look" usable.

---

## 1. Executive summary

The AWS job ran **97 open-weight models** over **662 TutorBench scenarios**
(`tb_0001..tb_0662`), one JSONL shard per model in `tutorbench-responses/`, using the
Model Output schema produced by `tutor_cat/respgen/records.py` (Title-Case keys).

There are **two independent bugs**, plus a few smaller data-quality issues:

| Class | What it looks like | Count (of 97 shards) |
|---|---|---|
| **Bug #1 — load failures** | 662/662 rows `"Finish Reason":"error"`, `Issue=1`, empty `Output` | **50 shards dead** |
| **Bug #2 — prompt truncation** | Loaded fine (`Issue=0`, non-empty `Output`) but prompt was truncated to ~1 token, so output is scenario-independent garbage that repeats across all 662 rows | **~16 of the 47 "loaded" shards** (9 confirmed + 7 inferred) |
| **Genuinely clean** | Correct prompt, real per-scenario output (incl. *legitimately weak* base models) | **~31 shards** |

**Headline:** the fleet you think is "47 good, 50 bad" is really **~31 good, ~66 to
repair, 1 write-off.** A single fixed re-run (a few hours) is worth it: it recovers
**≈49 of the 50 load-failures** *and* fixes the **~16 silently-corrupted "passing"
shards**, taking you from ~31 trustworthy models to **≈96**.

**The goal of the re-run:** apply the Bug #1 + Bug #2 fixes, regenerate the affected
shards (or all 97 for a self-consistent dataset) with `--no-resume`, and validate.

---

## 2. Bug #1 — vLLM engine-init load failures (50 shards)

### 2.1 Symptom
Every one of a shard's 662 rows is identical:
```json
{"Finish Reason":"error","Issue":1,"Output":"","Issue Description":"load failed: ..."}
```
This happens because the load (`runner._load_backend`) is attempted **once** per model;
on failure `runner.run_model` writes one `error_record` per outstanding scenario, so a
single load error is replicated 662×.

### 2.2 The five error signatures (read directly from the shards)

| # | Signature (verbatim `Issue Description`) | Shards | Meaning |
|---|---|---|---|
| A | `RuntimeError('Engine core initialization failed. See root cause above. Failed core proc(s): {}')` | **41** | vLLM V1 engine core crashed in a **subprocess**; the real traceback was on stderr and **was never captured** (the harness stores only `repr(e)`). |
| B | `OSError("... is not a valid model identifier ... If this is a private repository, make sure to pass a token ...")` | **5** | Hub repo-resolution failed for **public** repos → transient Hub/network/rate-limit blip during those loads. |
| C | `OSError('You are trying to access a gated repo ... meta-llama/Llama-2-7b-hf ... 403 ...')` | **2** | OpenELM ships **no tokenizer** and points at gated Llama-2, which this HF account has not been granted. |
| D | `AcceleratorError("CUDA error: device-side assert triggered ...")` (during **load**/smoke) | **1** | Mamba SSM kernel/config assert on the transformers path. |
| E | `AcceleratorError("CUDA error: device-side assert triggered ...")` (during **generation**) | **1** | `gpt-neo-2.7B` loaded + smoke-passed, then asserted on the batched run. |

> **Key diagnostic:** signature A is *not* "architecture unsupported." Many A-shards
> have an **alive same-architecture sibling**: `Qwen2.5-Coder-7B` (dead) vs
> `Qwen2.5-7B-Instruct` (alive); `SmolLM2-360M-Instruct` (dead) vs `SmolLM2-360M`
> (alive); `zephyr/vicuna/deepseek/Yi/Amber` (dead, Llama/Mistral arch) vs alive
> `Mistral-7B-v0.1` / `Llama-3.2`. So the architectures load fine under this vLLM
> build. The evidence points to a **systemic resource issue**, not model support.

### 2.3 Root cause per group + fix

- **Group A (41) — leading hypothesis: GPU memory not released between models.**
  `orchestrator._worker` keeps a worker process alive and pulls model after model off a
  shared queue, but nothing tears down the previous vLLM engine (KV-cache blocks / CUDA
  graphs / NCCL state that plain GC doesn't reclaim). Later models in each worker's
  queue then OOM at engine init. This matches the ~half-dead split and the alive/dead
  same-arch pairs. Note the `backends.py` docstring even says *"one model per process,"*
  which the orchestrator does **not** honor.
  **Fix:** free device memory between models (implemented, see §5.4) and/or spawn a fresh
  process per model. Belt-and-suspenders: fall back to the transformers backend if vLLM
  init fails (implemented).
  **Also capture the real cause next time:** first debugging pass with `--gpus 1`
  (serialized → subprocess traceback is readable), or set `VLLM_LOGGING_LEVEL=DEBUG`.

- **Group B (5, Cerebras + MPT) — transient Hub resolution.** Public repos; a retry
  should clear them. (MPT also needs `trust_remote_code`, which is already passed.)
  **Fix:** re-run. If it recurs, confirm Hub reachability / `HF_TOKEN` validity.

- **Group C (2, OpenELM) — gated Llama-2 tokenizer.** OpenELM documents "use the Llama-2
  tokenizer." **Fix:** load the tokenizer from an **ungated byte-identical mirror**
  (`NousResearch/Llama-2-7b-hf`) — implemented via a `tokenizer_id` override (see §5.5) —
  *or* accept the Llama-2 license on the token's HF account.

- **Group D (1, mamba-2.8b-hf) — genuine SSM bring-up.** Needs `mamba-ssm` +
  `causal-conv1d` kernels built for the box's CUDA/torch. **Not worth a config toggle;**
  drop from the re-run unless mamba coverage is required.

- **Group E (1, gpt-neo-2.7b) — generation-time assert.** 1.3B sibling works; likely
  transient/kernel. **Fix:** re-run; if it recurs, route to `hf_fallback` or set
  `enforce_eager=True`. (It is *also* Bug #2-affected: 2048 context.)

### 2.4 Recoverability headcount (the go/no-go input)

**≈49 of 50 recoverable · 1 genuine write-off.**

- ✅ **HIGH confidence — 7:** OpenELM ×2 (deterministic tokenizer fix), Cerebras ×3 +
  MPT ×2 (transient → re-run). *Only risk: repeated Hub flakiness, which a retry handles.*
- ✅ **MEDIUM-HIGH confidence — 42:** the 41 Group-A models + `gpt-neo-2.7b`. All are
  vLLM-supported architectures, several with alive same-arch siblings. *Caveat: the true
  root cause was masked (only `repr(e)` stored), so if the memory hypothesis is wrong for
  a subset, expect a **partial** recovery here. Cheap to de-risk: serialized `--gpus 1`
  first pass.*
- ❌ **NOT worth it — 1:** `state-spaces/mamba-2.8b-hf` (SSM kernel bring-up).

> "Recoverable" means "produces valid tutor output," which for the many ≤4096-context
> A/B/C models **requires the Bug #2 fix too** — otherwise they flip from `error` to
> *garbage-from-an-empty-prompt*.

### 2.5 Full dead list (50), by group

- **A (41):** `facebook/opt-1.3b`, `opt-2.7b`, `opt-6.7b`; `bigscience/bloom-560m`,
  `bloom-1b1`, `bloom-1b7`, `bloom-3b`, `bloom-7b1`, `bloomz-1b7`, `bloomz-3b`,
  `bloomz-7b1`; `openai-community/gpt2`, `gpt2-medium`, `gpt2-large`, `gpt2-xl`;
  `01-ai/Yi-6B`, `Yi-6B-Chat`; `internlm/internlm2-1_8b`, `internlm2-7b`,
  `internlm2_5-7b`; `deepseek-ai/deepseek-llm-7b-base`, `deepseek-coder-6.7b-base`,
  `deepseek-math-7b-base`; `ibm-granite/granite-3.0-2b-base`, `granite-3.1-2b-base`,
  `granite-3.1-2b-instruct`; `tiiuae/falcon-7b`, `falcon-7b-instruct`;
  `lmsys/vicuna-7b-v1.5`; `HuggingFaceH4/zephyr-7b-beta`;
  `togethercomputer/RedPajama-INCITE-7B-Base`, `RedPajama-INCITE-Base-3B-v1`;
  `openbmb/MiniCPM-2B-sft-bf16`; `nvidia/Nemotron-Mini-4B-Instruct`;
  `HuggingFaceTB/SmolLM2-360M-Instruct`; `stabilityai/stablelm-3b-4e1t`;
  `microsoft/phi-1_5`; `h2oai/h2o-danube2-1.8b-base`, `h2o-danube3-4b-base`;
  `Qwen/Qwen2.5-Coder-7B`; `LLM360/Amber`.
- **B (5):** `cerebras/Cerebras-GPT-1.3B`, `Cerebras-GPT-2.7B`, `Cerebras-GPT-6.7B`;
  `mosaicml/mpt-7b`, `mpt-7b-instruct`.
- **C (2):** `apple/OpenELM-3B`, `apple/OpenELM-1_1B`.
- **D (1):** `state-spaces/mamba-2.8b-hf`.
- **E (1):** `EleutherAI/gpt-neo-2.7B`.

---

## 3. Bug #2 — prompt truncation / generation budget (silent corruption)

### 3.1 Symptom
A model **loads fine** (`Issue=0`, non-empty `Output`, `Finish Reason` `stop`/`length`),
so its shard *looks* usable — but it **never saw the scenario**. Its `Rendered Prompt`
is essentially empty (just the trailing `":"` of `"...Tutor:"`, or the chat
generation-prompt token), so it emits scenario-independent filler that is ~identical on
every one of the 662 rows.

### 3.2 Mechanism (root cause)
In `tutor_cat/respgen/runner.py`, `_prompt_tokens_and_truncate` sets the prompt budget to
`max_model_len − max_new_tokens`:

```69:79:tutor_cat/respgen/runner.py
def _prompt_tokens_and_truncate(
    text: str, tokenizer, max_model_len: int, max_new_tokens: int
) -> tuple[str, int, bool]:
    """Count prompt tokens; left-truncate to reserve the generation budget so
    prompt_len + max_new_tokens <= max_model_len. Returns (text, n_tokens, truncated)."""
    ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    budget = max(1, max_model_len - max_new_tokens)
    if len(ids) <= budget:
        return text, len(ids), False
    ids = ids[-budget:]  # keep the most recent turn (the student's latest prompt)
    return tokenizer.decode(ids, skip_special_tokens=False), len(ids), True
```

`max_new_tokens` is a hardcoded **4096** (manifest default, passed as `spec.max_new_tokens`
in `run_model`, line ~222). Therefore for any model whose clamped `Max Model Len ≤ 4096`:

```
budget = max(1, max_model_len - 4096) = max(1, ≤0) = 1
```

→ the prompt is cut to **1 token**. And the cut keeps `ids[-budget:]` = the **tail**, so
it drops the **head** (the system prompt + the actual question) and keeps the trailing
`":"`. The model then free-associates.

**Two defects in one:**
1. **Budget can go to ≤0** because a fixed 4096 output budget is subtracted from small
   context windows.
2. **Truncation direction drops the head** (system + question), keeping the useless tail.
   (Note: `HFBackend.generate` truncates the *opposite* way — `tokenizer(..., truncation=True,
   max_length=input_cap)` defaults to right/tail-dropping, line ~163–168 of `backends.py` —
   so the two backends are also inconsistent. Fix both.)

### 3.3 Evidence (direct file reads)

| Shard | Max Model Len | Prompt Tokens | Truncated | Rendered Prompt | Output |
|---|---|---|---|---|---|
| `allenai_OLMo-2-1124-7B` (**BROKEN**) | 4096 | **1** | 1 | `":"` | unrelated "Student1/Student2 … 500 books, 60% fiction" ramble, ~identical across rows; `Finish Reason` stop, `Issue` 0 |
| `EleutherAI_pythia-1b` (**BROKEN**) | 2048 | **1** | 1 | `":"` | unrelated C++ / "Hello World" loop babble |
| `microsoft_phi-2` (**BROKEN**) | 2048 | **1** | 1 | `":"` | unrelated Python `sum(filter(...))` snippet |
| `microsoft_Phi-3-mini-4k-instruct` (**BROKEN**) | 4096 | **1** | 1 | `"<|assistant|>"` | unrelated C++ `PreferenceManager` code |
| `TinyLlama_TinyLlama-1.1B-Chat-v1.0` (**BROKEN**) | 2048 | **1** | 1 | `"\n"` | unrelated one-liner |
| `mistralai_Mistral-7B-v0.1` (**HEALTHY**) | 32768 | 836 | 0 | full `"System:/Student:/Tutor:"` | coherent, on-topic Ka/dilution answer |
| `tiiuae_Falcon3-7B-Base` (**HEALTHY**) | 32768 | 799 | 0 | full prompt | coherent |
| `meta-llama_Llama-3.2-1B` (**KEEP — model weakness, NOT the bug**) | 32768 | 703 | 0 | **correct** full prompt | degenerate repetition, `Finish Reason` length |

> **Critical distinction for calibration:** `Llama-3.2-1B` received the *correct* prompt
> and still produced degenerate output → that is **legitimate low-ability data, keep it.**
> Only discard outputs that are degenerate **because the prompt was destroyed** (Bug #2:
> `Prompt Tokens == 1` and/or `Rendered Prompt` is just `":"` / a lone generation token).

### 3.4 Detection rule
With `max_new_tokens = 4096`: **any model with `Max Model Len ≤ 4096` is broken.**
Operationally, a row is Bug #2-corrupted iff `Prompt Tokens <= 1` (or `Rendered Prompt`
∈ {`":"`, `"\n"`, a lone chat generation token}). After the fix, no non-error row should
have `Prompt Tokens <= 1`.

> **Do NOT enumerate affected shards by scanning `Output` text** — those degenerate
> outputs make individual JSONL lines enormous and the repo search tooling silently skips
> such files. Enumerate from **context-window size / roster / registry** instead, then
> confirm with a small validator pass over the `Prompt Tokens` / `Rendered Prompt` fields.

### 3.5 Affected models

**Loaded-but-broken (currently in the "usable 47", must be regenerated):**

- **Confirmed (read directly, `Prompt Tokens == 1`):** `allenai/OLMo-2-1124-7B` (4096),
  `EleutherAI/gpt-neo-1.3B` (2048), `EleutherAI/pythia-1b` (2048),
  `EleutherAI/pythia-70m` (2048), `EleutherAI/pythia-6.9b` (2048),
  `TinyLlama/TinyLlama-1.1B-Chat-v1.0` (2048), `microsoft/phi-2` (2048),
  `microsoft/Phi-3-mini-4k-instruct` (4096), `stabilityai/stablelm-2-1_6b` (4096).
- **Inferred (context ≤ 4096; verify with a validator run):**
  `EleutherAI/pythia-160m`, `pythia-410m`, `pythia-1.4b`, `pythia-2.8b` (all 2048),
  `allenai/OLMo-2-0425-1B` (4096), `stabilityai/stablelm-2-zephyr-1_6b` (4096),
  `Qwen/Qwen2.5-Math-7B` (4096).

  → ~9 confirmed + ~7 inferred = **~16 "passing" shards that are actually garbage.**

**Dead models that ALSO need the Bug #2 fix once they load (context ≤ 4096):** most of the
Bug #1 list is small-context and would regenerate garbage without this fix — e.g.
`opt-*` (2048), `gpt2*` (1024), `bloom*`/`bloomz*` (≤2048), `RedPajama-*` (2048),
`phi-1_5` (2048), `Cerebras-GPT-*` (2048), `falcon-7b*` (2048), `Amber` (2048),
`gpt-neo-2.7b` (2048), `vicuna-7b` (4096), `deepseek-*` (4096), `granite-3.0-2b-base`
(4096), `MiniCPM-2B` (4096), `Yi-6B*` (4096), `Nemotron-Mini-4B` (4096),
`stablelm-3b-4e1t` (4096). **This is why both fixes must ship in the same re-run.**

Because the fix lives in the harness (one change), it automatically covers **every**
model — the enumeration above only matters for deciding which currently-"clean" shards to
throw away.

### 3.6 Fix (code pointers)
Reserve prompt space **first**, cap output to what the window allows, and stop truncating
below the full rendered prompt when it fits. Keep the **head** (system + question) if a cut
is truly required.

Recommended policy (per model; preserves large-context "clean" models exactly):
```
PROMPT_RESERVE = 1024   # ceiling on observed TutorBench prompt tokens (measured ≤ ~900)
OUTPUT_FLOOR   = 256
eff_new = min(spec.max_new_tokens, max(OUTPUT_FLOOR, max_model_len - PROMPT_RESERVE))
prompt_budget = max_model_len - eff_new
# truncate the prompt to prompt_budget ONLY if it exceeds it, keeping the HEAD.
```
- For ctx ≥ 5120 (Qwen/Llama/Mistral/gemma/Falcon3/SmolLM2/Phi-3.5/h2o-danube): `eff_new`
  stays `4096` → **the ~31 clean shards are unchanged.**
- ctx 4096 → `eff_new = 3072`, full prompt fits, no truncation.
- ctx 2048 → `eff_new = 1024`, prompt budget 1024 (fits ~900-token prompts).
- ctx 1024 (gpt2) → `eff_new = 256`, prompt budget 768; long prompts trimmed from the head
  (legitimately `Truncated=1`).

Apply in **both** code paths:
1. `tutor_cat/respgen/runner.py` → `_prompt_tokens_and_truncate` (lines 69–79) and the
   `GenParams(...)` build in `run_model` (line ~219–225). Compute `eff_new` per model and
   pass it as the effective `max_new_tokens`; change `ids[-budget:]` → keep the head
   (`ids[:budget]`) or, better, head+tail keeping the system prompt and the latest turn.
2. `tutor_cat/respgen/backends.py` → `HFBackend.generate` `input_cap` (line ~163) and set
   `self.tokenizer.truncation_side = "left"`/`"right"` to match the runner's chosen
   direction; also cap `gen_kwargs["max_new_tokens"]`/`SamplingParams.max_tokens` to
   `eff_new`.

> More precise (optional): compute `eff_new` **per prompt** from that prompt's token count
> and pass a per-prompt `SamplingParams` list to `llm.generate` (vLLM supports this).

---

## 4. Bug #3+ — other issues to fix in the same re-run

Evidence-based only:

1. **`Latency (s)` is null on essentially every row.** `VLLMBackend.generate` derives
   latency from `out.metrics.finished_time - arrival_time`, but vLLM V1 does not reliably
   populate `RequestOutput.metrics`, so it stays `None` (confirmed null on Qwen/gemma/
   Mistral/pythia/OLMo/phi-2/Phi-3-mini rows; only isolated rows had a value). **Timing is
   effectively not captured.**
   ```80:85:tutor_cat/respgen/backends.py
           # Per-request latency from vLLM metrics when available; batching makes
           # per-scenario wall-clock otherwise ill-defined (see README note).
           latency = None
           metrics = getattr(out, "metrics", None)
           if metrics is not None and getattr(metrics, "finished_time", None) and getattr(metrics, "arrival_time", None):
   ```
   **Fix (if latency matters):** record a per-batch wall-clock and divide, or enable vLLM
   metrics explicitly. If latency is not needed for calibration, document it as
   intentionally absent so downstream code doesn't depend on it.

2. **Resume vs. re-run collision (must handle for a clean re-run).** `run_model` computes
   `done = completed_ids(path) if resume else set()` and skips scenarios already present.
   The 50 dead shards **already contain all 662 Scenario ids** (as error rows), so a
   default `resume=True` re-run would treat them as **complete and skip them entirely**.
   And the writer opens in append mode, so a naive re-run would **stack new rows on top of
   the old ones** (duplicate `(model, scenario)`). **Fix:** re-run with `--no-resume`
   *and* truncate the shard on no-resume (implemented, §5.3), or delete stale shards first.

3. **Truncation-direction inconsistency between backends** (see §3.2): runner keeps the
   tail, `HFBackend` (default) keeps the head. Unify as part of the Bug #2 fix.

4. **`max_position_embeddings` probe misses some configs.** `registry.resolve_max_model_len`
   only checks `max_position_embeddings / n_positions / max_seq_len / seq_length`. Models
   that name it differently (e.g. OpenELM's `max_context_length`, SSMs with no positional
   limit) fall back to the 32768 cap. Harmless for short TutorBench prompts, but note
   OpenELM ran at a 32768 budget rather than its true ~2048 window.

5. **HF token / gated repos.** Gated families need a valid `HF_TOKEN` (loaded from `.env`
   by `cli.cmd_generate`): `meta-llama/*`, `mistralai/*`, `google/gemma*` (these were
   confirmed working — gemma-2-2b and Mistral-7B-v0.1 produced real output). **Llama-2 is
   separately gated** and is what OpenELM's tokenizer needs → use the ungated mirror
   (§2.3 Group C) or grant Llama-2 access.

Chat-template detection (`registry.guess_apply_chat_template` + the runner's
"apply only if the tokenizer actually ships a template" guard) was spot-checked and looks
correct across the roster (instruct/chat/zephyr/vicuna/sft markers hit; base models fall
back to flat rendering). Verify via `--dry-run` but no change is required.

---

## 5. Recommended re-run configuration

### 5.0 Scope
- **Recommended:** re-run **all 97 models** with the fixed harness for a single,
  self-consistent dataset (the ~31 clean big models are fast under vLLM, so this adds
  little wall-clock and removes any doubt about the inferred Bug #2 list).
- **Faster alternative:** re-run only the **union** of {50 dead} ∪ {loaded shards with
  `Max Model Len ≤ 4096`} (~66 shards), keeping the ~31 confirmed-clean shards.
- **Drop** `state-spaces/mamba-2.8b-hf` unless SSM kernels are installed.
- Expect **a few hours**; any model that hits the vLLM→HF fallback runs per-prompt and is
  much slower — watch those.

### 5.1 Generation-budget policy (Bug #2) — **must apply**
Implement the §3.6 policy: `eff_new = min(4096, max(256, max_model_len - 1024))`, reserve
prompt space, keep the head on truncation, unify direction across both backends. This is
the one fix **not** yet in the repo (see §5.6) and is required for every ≤4096-context
model to be meaningful.

### 5.2 Command (from `tutor_cat/cli.py`, `generate` subcommand)
Real flags: `--models --scenarios --out-dir --s3-uri --gpus --gpu-ids --limit --model
--dry-run --no-resume`.

```bash
# 0. Ensure HF_TOKEN is in .env (gated: meta-llama/*, mistralai/*, google/gemma*;
#    plus Llama-2 access OR rely on the OpenELM ungated-mirror fix).

# 1. Offline sanity check (no weights, no network): confirm resolved backends,
#    max_model_len, and the OpenELM tokenizer override.
tutor-cat generate --models models_rerun.yaml --dry-run --limit 3

# 2. First pass SERIALIZED to surface any real vLLM tracebacks (Bug #1 Group A).
tutor-cat generate --models models_rerun.yaml \
  --scenarios data/scenarios.jsonl --out-dir tutorbench-responses \
  --gpus 1 --no-resume

# 3. Full run across the fleet.
tutor-cat generate --models models_rerun.yaml \
  --scenarios data/scenarios.jsonl --out-dir tutorbench-responses \
  --gpus 8 --no-resume --s3-uri s3://<bucket>/<prefix>
```
- **`--no-resume` is mandatory** (dead shards already contain all 662 Scenario ids, so a
  resume run skips them). Delete stale target shards first as belt-and-suspenders.
- `models_rerun.yaml` = a manifest of the re-run set. OpenELM auto-gets the ungated
  tokenizer via the registry heuristic (no per-model key needed). Add
  `EleutherAI/gpt-neo-2.7B` with `backend: hf_fallback` if the assert recurs; omit
  `state-spaces/mamba-2.8b-hf` unless kernels are ready.

### 5.3 Backend/registry knobs
- `trust_remote_code=True` — already set in both backends (needed for MPT, InternLM2,
  MiniCPM, Falcon, etc.).
- `dtype="auto"` — fine for the alive set; if a specific Group-A model still fails engine
  init, try `dtype="bfloat16"` and/or `enforce_eager=True` for that model.
- vLLM→HF fallback on load failure — implemented (§5.4); it makes a genuinely
  vLLM-unsupported arch still produce output instead of dead-lettering 662 rows.
- GPU-memory teardown between models — implemented (§5.4); the primary Group-A fix.

### 5.4 / 5.5 / 5.6 — Fixes already drafted in the repo (UNTESTED — review before relying)
The following were drafted directly in `tutor_cat/respgen/` (shell was unavailable, so
**none were executed or tested**). They do **not** change behavior for models that already
work. Review, keep, or redo as you prefer:

- **§5.4 (Bug #1) `runner.py`:** `_load_backend` now tries vLLM, and on any init/smoke
  failure calls `_free_backend(...)` then retries via `HFBackend`. `_free_backend`
  (guarded, never raises) runs vLLM `destroy_model_parallel` + drops refs + `gc.collect` +
  `torch.cuda.empty_cache`, and is also called at the end of `run_model` so each worker
  frees memory before the next model.
- **§5.5 (Bug #1 Group C) OpenELM tokenizer:** new optional `tokenizer_id` on `ModelSpec`
  → `registry.resolve` (heuristic maps `openelm` → `NousResearch/Llama-2-7b-hf`) →
  `HFBackend(tokenizer_id=...)`. Visible in `--dry-run` output.
- **§5.3 hygiene (Bug #3.2) `shard.py` + `runner.py`:** `ShardWriter(path, truncate=...)`;
  `run_model` passes `truncate=not resume` so `--no-resume` regenerates cleanly instead of
  appending onto old rows.
- **NOT YET APPLIED — Bug #2 (§3.6 / §5.1):** the generation-budget/truncation fix. It
  changes outputs of currently-"passing" small-context models, so it was intentionally
  left for this re-run. **This is the one you must implement.**

---

## 6. Post-run validation checklist

Run a small validator over the regenerated shards (read only the metadata fields —
`Prompt Tokens`, `Rendered Prompt`, `Finish Reason`, `Issue`, `Scenario` — never scan full
`Output` text, per §3.4):

1. **Coverage:** each expected shard has exactly **662** rows; scenario ids are the full
   `tb_0001..tb_0662` set with **no duplicates** (catches the resume/append bug).
2. **No load failures:** `Issue==1` / `Finish Reason=="error"` count ≈ 0 (only the
   accepted write-offs, e.g. mamba, may remain).
3. **Bug #2 cleared:** **no** non-error row has `Prompt Tokens <= 1`, and **no**
   `Rendered Prompt` equals `":"`, `"\n"`, or a lone chat generation token. Median
   `Prompt Tokens` per shard should be in the hundreds.
4. **Truncation sane:** `Truncated==1` only on genuinely long prompts (tiny-context models
   like gpt2), not on 2048/4096-context models for normal scenarios.
5. **Per-scenario variation:** outputs differ across scenarios — e.g. sample 5 random rows
   per shard and confirm they are **not** near-identical (the tell-tale of Bug #2). A
   cheap proxy: distinct `Output Tokens` values and distinct first-64-char hashes across
   sampled rows.
6. **Keep vs. discard weak models:** shards with correct prompts but degenerate output
   (e.g. `Llama-3.2-1B`, `Prompt Tokens` ~700, `Finish Reason` length) are **valid** — do
   not flag them.
7. **Spot-check the recovered families:** open one row each from OpenELM, a Cerebras/MPT,
   and 2–3 Group-A models to confirm real, on-topic tutor output.

Target end state: **~96 shards** with clean, per-scenario output (97 minus the mamba
write-off), replacing the current ~31 trustworthy shards.

---

## 7. Appendix — concrete evidence pointers

- **Bug #1 signatures** (first row of each): `tutorbench-responses/facebook_opt-1.3b.jsonl`
  & `bigscience_bloom-1b1.jsonl` (A), `cerebras_Cerebras-GPT-1.3B.jsonl` &
  `mosaicml_mpt-7b.jsonl` (B), `apple_OpenELM-3B.jsonl` & `apple_OpenELM-1_1B.jsonl` (C),
  `state-spaces_mamba-2.8b-hf.jsonl` (D), `EleutherAI_gpt-neo-2.7B.jsonl` (E, note
  `"generation failed: ..."`).
- **Bug #2 broken:** `allenai_OLMo-2-1124-7B.jsonl`, `EleutherAI_pythia-1b.jsonl`,
  `microsoft_phi-2.jsonl`, `microsoft_Phi-3-mini-4k-instruct.jsonl`,
  `TinyLlama_TinyLlama-1.1B-Chat-v1.0.jsonl` (all `Prompt Tokens:1`, `Rendered Prompt`
  ≈ `":"`).
- **Bug #2 healthy contrast:** `mistralai_Mistral-7B-v0.1.jsonl` (836 tok),
  `tiiuae_Falcon3-7B-Base.jsonl` (799 tok), `Qwen_Qwen2.5-7B-Instruct.jsonl` (chat
  template, 736 tok).
- **Bug #2 "keep, weak model":** `meta-llama_Llama-3.2-1B.jsonl` (703 tok, correct prompt,
  degenerate length output).
- **Code:** `tutor_cat/respgen/runner.py` (`_prompt_tokens_and_truncate` 69–79, `run_model`
  loads/renders/generates, `_load_backend`, `_free_backend`); `backends.py` (`VLLMBackend`
  args + latency 80–85, `HFBackend` truncation 163–168); `registry.py` (backend/gated/
  tokenizer/max_model_len derivation); `manifest.py` (`ModelSpec`); `shard.py`
  (`ShardWriter`, `completed_ids`); `orchestrator.py` (persistent worker + queue);
  `cli.py` (`generate` flags); `models.yaml` (checked-in roster; the AWS run used a larger
  roster not committed here).
