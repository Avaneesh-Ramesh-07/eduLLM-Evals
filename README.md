# tutor-cat

CAT-driven MIRT evaluation pipeline for LLM tutors, implementing the team PRD:
an LLM judge grades tutor responses criterion-by-criterion, criterion verdicts
update a 3-skill MIRT ability vector (content, diagnosis, scaffolding), and a
Fisher-information CAT selector picks each next scenario until the stopping
rule is met.

## What's implemented (PRD mapping)

| PRD section | Code |
| --- | --- |
| Equations 1–3 (p, U update, θ update) | `tutor_cat/mirt.py` |
| Choosing Next Scenario (V_kc, ScenarioValue, top-5 uniform seeded, fallback) | `tutor_cat/selector.py` |
| Judge Evaluation (per-criterion direct pass/fail, unscorable→fail) | `tutor_cat/judge.py` |
| Critical Failures (separate report, still update θ) | `tutor_cat/engine.py` |
| Stopping Rule (max SE + ≥15 scorable evals + max-scenario cap) | `tutor_cat/engine.py` |
| Baseline (non-adaptive, seeded-random order) | `tutor_cat/engine.py` (`mode="baseline"`) |
| Schemas + validation | `tutor_cat/schemas.py`, `tutor_cat/dataio.py` |
| Tutor adapters (GPT-5.5 / Opus 4.8 / Gemini 3.5 Flash) + response cache | `tutor_cat/tutors.py` |
| SE-over-time plots (CAT vs baseline) | `tutor_cat/plotting.py` |
| Open-model response generation (vLLM/HF on GPU, sharded + resumable) | `tutor_cat/respgen/`, `tutor-cat generate` |

## Quickstart on a new machine (fresh clone)

```bash
git clone <repo-url> && cd tutor_cat
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # then put TFY_API_KEY=<key> in .env
pytest tests                       # 19 offline tests, no APIs needed
tutor-cat validate                 # dataset ships in data/, should print OK
```

Tutors route through the TrueFoundry gateway (config.yaml is preconfigured);
the only secret needed is `TFY_API_KEY`.

## Judge: Prometheus 2 7B on the MIT cluster

Only the **model** lives on the cluster (vLLM server on a GPU node); the
pipeline stays on your laptop and reaches it through an SSH tunnel at
`localhost:8000` — config.yaml already points there.

Cluster: **MIT ORCD / Engaging** — web portal at <https://orcd-ood.mit.edu>,
ssh login node `orcd-login.mit.edu`. The sbatch
script is preconfigured for the `mit_normal_gpu` partition (L40S 48GB).

**One-time cluster setup** — entirely in the browser if you like:

1. <https://orcd-ood.mit.edu> → **Files → Home Directory → Upload**:
   `scripts/cluster/setup_prometheus.sh` and `scripts/cluster/serve_prometheus.sbatch`
2. **Clusters → Shell Access**, then:

```bash
bash setup_prometheus.sh        # venv + vLLM + pre-downloads the 15GB model (~15 min)
```

(Heads-up: the model cache needs ~15GB — if your home quota is tight, set
`HF_HOME` to scratch; see the comment in `setup_prometheus.sh`.)

**Each work session:**

```bash
# on the cluster (OOD web shell or ssh to orcd-login.mit.edu):
sbatch serve_prometheus.sbatch
squeue --me                             # wait for state R, note the node name
tail -f prometheus-judge-<jobid>.log    # "Uvicorn running" = ready

# on your laptop (keep open; Git Bash on Windows):
scripts/cluster/tunnel.sh <node> <user>@orcd-login.mit.edu
curl http://localhost:8000/v1/models    # sanity check from another terminal

# then run the pipeline as usual:
tutor-cat run --tutor all --mode both

# done for the day (frees the GPU):
scancel <jobid>
```

The tunnel always targets a **login node** (`orcd-login.mit.edu`), never
`orcd-ood.mit.edu` — that's the web portal, not an ssh host.

The sbatch job auto-expires after 6h (`--time`; `mit_normal_gpu` caps at 6h, so
8h is rejected at submit); the model stays cached on the cluster, so the next
`sbatch` is ready in ~2 minutes.

## Data

Ships in `data/` (tracked in git): `scenarios.jsonl` (662 scenarios) and
`rubrics_calibrated.jsonl` (6,462 criteria with q_mapping + PLACEHOLDER
`discrimination`/`difficulty` from `scripts/estimate_placeholder_params.py`,
tagged `heuristic-v0-placeholder`). When real MIRT calibration lands, point
`data.rubrics` in config.yaml at the new file — every run manifest records
`calibration_version`, keeping placeholder runs distinguishable.

## Usage

```bash
tutor-cat validate                       # check data against the PRD schemas
tutor-cat run --tutor gpt-5.5 --mode cat # one CAT run
tutor-cat run --tutor all --mode both    # all tutors, CAT + baseline
tutor-cat plot runs/<cat_run> runs/<baseline_run> --out se.png
```

Cost note: a full run caps at 50 scenarios ≈ 1 tutor call + ~10 judge calls
each. `--tutor all --mode both` ≈ 3,300 calls total (tutor responses are
cached across CAT/baseline). Start with one tutor and sanity-check
`runs/<run_id>/final_result.json` before fanning out.

Each run writes `runs/<run_id>/` with: `manifest.json` (seeds, config echo),
`judge_results.jsonl` (PRD judge-result schema), `criterion_updates.jsonl`
(per-criterion θ/SE trace), `steps.jsonl` (per-scenario trace),
`critical_failures.json`, `final_result.json`.

## Generating open-model responses (AWS P6 / 8×B200)

`tutor-cat generate` runs the **100 open-weight "common person" models** in
`models.yaml` over all 662 TutorBench scenarios and writes one JSONL shard per
model (the PRD Model Output schema). Those 100 checkpoints are the rows of the
MIRT response matrix; a wide, diverse roster is what powers item calibration.
This is a separate stage from the CAT/judge pipeline above — it only produces
the response matrix.

The GPU deps have no Windows wheels, so this stage runs on the Linux GPU box; the
pure logic (prompt building, manifest/registry, output schema) is importable and
tested anywhere. Pull the repo on the box and:

```bash
pip install -e ".[gen]"
export HF_TOKEN=<token>            # for gated repos (meta-llama, gemma). Never committed.

# smoke: 2 scenarios on one GPU, one model
tutor-cat generate --model Qwen/Qwen2.5-0.5B-Instruct --limit 2 --gpus 1

# full run: all models across all GPUs, upload each shard to S3 (instance IAM)
tutor-cat generate --s3-uri s3://<bucket>/tutorbench-responses

# pin the whole run to one specific GPU (e.g. device 8), leaving the rest free
tutor-cat generate --gpu-ids 8 --s3-uri s3://<bucket>/tutorbench-responses
```

Design notes:

- **8-way data parallelism**: each ≤7B model fits one B200, so one worker per GPU
  runs a whole model (`tensor_parallel_size=1`); the fleet pulls the 100 models
  off a shared queue. Not tensor parallelism. `--gpu-ids <i,j,…>` restricts the
  fleet to specific physical devices (each worker gets `CUDA_VISIBLE_DEVICES=<id>`,
  so it cannot touch any other GPU); `--gpu-ids 8` runs the entire roster on GPU 8
  alone.
- **Resumable**: one shard per model, keyed by `Scenario` id. Re-running skips
  completed cells, so an interrupted run just continues (`--no-resume` to force).
- **Backends**: vLLM by default; a transformers fallback (`hf_fallback`) serves
  the architectures vLLM can't — SSM (mamba-2.8b), OpenELM, gemma-3, and GPT-Neo
  (`GPTNeoForCausalLM`). `registry.py` derives the backend, chat-template flag,
  gated flag, and `max_model_len` clamp from each model id; override any per model
  in `models.yaml`. Two OpenELM rows also set `tokenizer_id: meta-llama/Llama-2-7b-hf`
  because their repos ship no tokenizer. (Encoder-decoder seq2seq — flan-t5 via
  `AutoModelForSeq2SeqLM` — is still supported by the code but isn't in the 100.)
- **Failure isolation**: a load/generation error becomes an `Issue=1` cell rather
  than crashing the fleet, so the matrix always has an entry per (model, scenario).
- **Preview without a GPU**: `tutor-cat generate --dry-run --limit 5` prints the
  resolved config and rendered prompts with no model load and no network.
- **`Latency (s)`**: per-request from vLLM metrics when available; under batched
  decode per-scenario wall-clock is otherwise ill-defined (use `elapsed_s` /
  throughput in the run summary for per-model timing).

### Running it on the P6 node from your laptop

The job runs *on* the GPU node (vLLM loads the B200s); you just drive it over
SSH. This uses only the P6 box you already pay for — it provisions nothing and
incurs no extra AWS spend. Results come back over `scp`, so S3 is optional.

```bash
# 1. from your laptop: get the code onto the node (data/*.jsonl ride along in git)
ssh <user>@<p6-node>
git clone <repo-url> tutor_cat && cd tutor_cat      # or, if already cloned: git pull

# 2. one-time env (heavy [gen] deps: vllm/torch/transformers)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[gen]"
export HF_TOKEN=<token>          # gated repos; set on the node, never committed/pushed

# 3. confirm how THIS node numbers its GPUs — indices are 0-based and per-node
nvidia-smi -L                    # e.g. 8 GPUs => "GPU 0 ... GPU 7"

# 4. smoke test on the target GPU (1 small model, 2 scenarios)
tutor-cat generate --model Qwen/Qwen2.5-0.5B-Instruct --limit 2 --gpu-ids 8

# 5. full run, pinned to that GPU, surviving laptop disconnect
tmux new -s respgen
tutor-cat generate --gpu-ids 8 --out-dir runs/responses
#   detach: Ctrl-b then d    |    reattach later: tmux attach -t respgen

# 6. pull the shards back to your laptop (no S3, no extra spend)
#   run this ON YOUR LAPTOP:
scp -r <user>@<p6-node>:~/tutor_cat/runs/responses ./runs/
```

**GPU numbering (read before step 4):** CUDA indices are 0-based and per-node, so
on the standard 8×B200 node the eighth GPU is `--gpu-ids 7`, **not** `8`. If
`nvidia-smi -L` shows only GPU 0–7, use `--gpu-ids 7`; `--gpu-ids 8` now fails
fast with the valid range (`this node has 8 GPU(s), valid indices 0..7`) instead
of crashing each worker mid-load. `--gpu-ids 8` only works if the node actually
has ≥9 GPUs.

**Interrupted?** Re-run the exact same command — shards are resumable (completed
scenarios are skipped), so it continues where it stopped. `--no-resume` forces a
clean regen. If the node runs Slurm rather than bare SSH, wrap step 5 in an
sbatch script that requests one GPU (see `scripts/cluster/serve_prometheus.sbatch`
for the pattern) and run `tutor-cat generate` in the job body.

#### Helper scripts: run on one shared GPU without disturbing neighbors

When the P6 box is shared and only one GPU is yours, use the pinned launchers in
`scripts/aws/` (default: **GPU index 2**):

```bash
bash scripts/aws/setup_respgen.sh          # one-time: venv + pip install -e ".[gen]" (no GPU touched)
source .venv/bin/activate && export HF_TOKEN=<token>
bash scripts/aws/run_respgen_gpu2.sh       # all 100 models on GPU 2 only; S3_URI=s3://… to upload
```

`run_respgen_gpu2.sh` runs `tutor-cat generate --gpu-ids 2`, so every worker gets
`CUDA_VISIBLE_DEVICES=2` and cannot see any other GPU. It **pre-flight refuses to
start if GPU 2 already has a compute process**, so it never disturbs an existing
job — free the GPU or set `GPU=<free index>` instead of killing anything. Change
the target with `GPU=<n> bash scripts/aws/run_respgen_gpu2.sh`.

## Tests (offline, no API keys needed)

```powershell
pytest tests
```

Includes: the PRD worked example pinned as a regression test, selector
behavior (Fisher peak at p≈0.5, cost normalization, seeded top-5, fallback),
and a full simulated CAT run against a synthetic tutor with known true θ*
(checks SE shrinkage, θ recovery, reproducibility, critical-failure report,
max-scenario cap reporting).

## Determinism

One master seed (`run.seed`) derives a per-(tutor, mode) run seed; the judge
gets temperature 0 + fixed seed; tutor responses are cached per
(model, scenario) so CAT and baseline reuse identical responses. All seeds and
config are echoed into `manifest.json`.

## Known limitation (documented, per PRD discussion)

Criteria within one scenario grade the same tutor response, so they are not
conditionally independent; reported SEs are therefore somewhat optimistic.
A testlet-style correction is future work.
