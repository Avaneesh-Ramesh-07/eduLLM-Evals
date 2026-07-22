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

## Judge: Prometheus 2 7B on AWS (3 commands)

The judge runs on an EC2 GPU instance (g5.xlarge, ~$1/hr) and is reached
through an SSH tunnel at `localhost:8000` — config.yaml already points there.
Needs: AWS CLI v2 with credentials (`aws configure`), ssh. On Windows run these
in Git Bash.

```bash
scripts/aws/launch_prometheus.sh     # once: provisions key, firewall, GPU box;
                                     # vLLM + model install themselves (~10-15 min)
scripts/aws/prometheus_ctl.sh status # repeat until it prints JUDGE READY
scripts/aws/tunnel.sh                # keep this terminal open while running
```

Then in another terminal:

```bash
tutor-cat run --tutor all --mode both
```

Daily driver commands:

```bash
scripts/aws/prometheus_ctl.sh stop   # done for the day -> stops billing, keeps model
scripts/aws/prometheus_ctl.sh start  # next session -> ready in ~2 min
scripts/aws/prometheus_ctl.sh terminate  # project over -> delete everything
```

Security notes: port 8000 is never exposed to the internet (SSH tunnel only,
SSH restricted to the launching IP); the key pair pem is gitignored.

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
