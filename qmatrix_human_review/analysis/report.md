# Q-matrix human-review audit

All three review files passed assignment and completeness validation.

## Scope

- Criteria: 25
- Criterion reviews: 60
- Binary criterion-skill labels audited: 75
- Adjudication rows: 32

> This was a dispute-enriched audit, not a simple random sample. Overall AI agreement must not be reported as population accuracy for the full Q-matrix.

## Human agreement

| Skill | Unanimous | Rate | Unresolved 1–1 splits | Core Fleiss κ |
| --- | ---: | ---: | ---: | ---: |
| content | 21/25 | 0.840 | 3 | 0.850 |
| diagnosis | 16/25 | 0.640 | 5 | 0.365 |
| scaffolding | 20/25 | 0.800 | 2 | 0.593 |

### Pairwise Cohen κ

| Skill | A–B | A–C | B–C |
| --- | ---: | ---: | ---: |
| content | 0.857 | 0.857 | 0.615 |
| diagnosis | 0.444 | 0.167 | 0.444 |
| scaffolding | 0.587 | 0.455 | 0.865 |

## Final AI Q-matrix versus provisional human consensus

| Slice | Resolved | Accuracy | Precision | Recall | F1 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| content | 22/25 | 0.955 | 0.923 | 1.000 | 0.960 | 1 | 0 |
| diagnosis | 20/25 | 0.800 | 0.667 | 0.400 | 0.500 | 1 | 3 |
| scaffolding | 23/25 | 0.870 | 0.778 | 0.875 | 0.824 | 2 | 1 |
| overall | 65/75 | 0.877 | 0.840 | 0.840 | 0.840 | 4 | 4 |

### By sampling stratum

| Stratum | Resolved | Accuracy | FP | FN |
| --- | ---: | ---: | ---: | ---: |
| changed | 33/36 | 0.849 | 3 | 2 |
| stable | 32/39 | 0.906 | 1 | 2 |

## Next step

Open `adjudication_queue.csv`. Review every row independently of the provisional majority, enter `adjudicated_value` and `adjudication_rationale`, and only then decide whether sampled mappings should be patched. A 2–1 vote is intentionally queued rather than silently accepted.
