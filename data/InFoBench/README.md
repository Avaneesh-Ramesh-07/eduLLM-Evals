# InFoBench

InFoBench (Instruction Following Benchmark) reformatted into the Scenario + Rubric
schema. Source: https://huggingface.co/datasets/kqsong/InFoBench (one `train` split).

Built by [`scripts/ingest_infobench.py`](../../scripts/ingest_infobench.py); the
synthetic IRT parameters are appended by
[`scripts/assign_irt_params.py`](../../scripts/assign_irt_params.py).

## What's in this folder

| File | Rows | What it is |
|------|------|------------|
| `scenarios.jsonl` / `.json` | 500 | one instruction each (`.json` is a pretty-printed twin for reading) |
| `rubrics.jsonl` / `.json`   | 2250 | one binary criterion per decomposed question |
| `irt_logs/manifest.json`    | –    | synthetic-param run manifest (constants, seed, summary stats) |

- **500 scenarios, 2250 criteria.** Each InFoBench instruction becomes one scenario;
  each of its `decomposed_questions` becomes one binary rubric criterion.
- `.jsonl` is the canonical artifact; the `.json` twin holds the identical records
  pretty-printed for comfortable reading.

## How the source maps into the schema

| InFoBench field | Schema field |
|-----------------|--------------|
| `id` | `scenario_id` provenance → `source_id` |
| `instruction` (+ `input`) | `prompt` (the `input` context block is appended only when non-empty; 225/500 rows) |
| `category` | `subject` (raw domain/source string, e.g. `Quora`) |
| `subset` | `subset` (native `Easy_set` / `Hard_set` band, preserved) |
| `decomposed_questions[i]` | `criterion` (one rubric row each) |
| `question_label[i]` | `question_label` + `q_mapping` + `primary_skill` |

## Skill axis (q-matrix)

The q-matrix is built from InFoBench's own `question_label` annotation over the five
constraint types, slugged lowercase:

`content, format, number, style, linguistic`

InFoBench is **multi-label**: a criterion can carry more than one constraint type, so
`q_mapping` may sum to >1 (unlike WildBench, which always marks exactly one skill). Of
the 2250 criteria, 1604 have 1 label, 576 have 2, and 70 have 3. The raw label list is
kept verbatim in `question_label` for provenance; `primary_skill` is the first label's
slug.

Criteria marking each skill (`q_mapping` load) and how often each is the `primary_skill`:

| skill | load | primary |
|-------|-----:|--------:|
| content | 1395 | 1153 |
| format | 794 | 576 |
| number | 419 | 269 |
| style | 190 | 128 |
| linguistic | 168 | 124 |

## Placeholders — what is real vs. synthetic

- **Real (from the dataset):** `prompt`, `criterion`, `question_label`, `q_mapping`,
  `primary_skill`, `subject`, `subset`.
- **Placeholder (uniform, matching WildBench):** `criticality="critical"`,
  `objectivity="objective"`, `explicitness="explicit"`. InFoBench has no native source
  for these; they feed only the synthetic IRT heuristic, and because they are uniform
  they add no per-item signal there.
- **Synthetic / dummy (`irt_params.source == "synthetic"`, `metadata_heuristic_v1`):**
  `difficulty`, `discrimination`. Generated from the metadata above with per-criterion
  RNG jitter — **not calibrated** from real judge responses. Swappable later for
  calibrated values with the same schema.

## Notes

- `question_label` is an **extra field** beyond the WildBench rubric schema, kept as
  InFoBench-specific provenance (it is the annotation the q-matrix is derived from).
- The `content` slug is textually identical to the TutorBench pedagogical `content`
  skill. This is safe as long as benchmarks are loaded under separate per-benchmark
  configs and never co-loaded on one axis.

## Rebuilding

```bash
python scripts/ingest_infobench.py
python scripts/assign_irt_params.py \
    --input data/InFoBench/rubrics.jsonl \
    --skills content,format,number,style,linguistic \
    --log-dir data/InFoBench/irt_logs --no-backup
```

Re-running the ingester strips the IRT params (it writes only the base schema fields),
so always re-run the assign step after a rebuild.
