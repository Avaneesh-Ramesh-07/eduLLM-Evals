# Data drop point

Place the preprocessed TutorBench files here:

- `scenarios.jsonl` — one Scenario Schema object per line
- `rubrics.jsonl` — one Rubric Schema object per line (must include the
  calibrated `discrimination` map, `difficulty`, and `q_mapping`)

Then check them with:

```
tutor-cat validate --config config.yaml
```
