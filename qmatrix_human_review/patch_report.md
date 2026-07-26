# Q-matrix human-review + rule-fix patch report

- Applied: YES (written)
- Rubric file: `C:\Users\Samee\Documents\GitHub\eduLLM-Evals\data\rubrics_qmatrix_final.jsonl`
- Records total: 6462
- human_review_v1 criteria changed: 7
- qmatrix_rulefix_v1 criteria changed: 40

## human_review_v1 (human-adjudicated)

| Criterion | Change | From -> To |
| --- | --- | --- |
| `tb_0620_c05` | q_mapping.diagnosis | 0 -> 1 |
| `tb_0125_c01` | q_mapping.diagnosis | 1 -> 0 |
| `tb_0125_c01` | primary_skill | diagnosis -> None |
| `tb_0016_c04` | q_mapping.scaffolding | 1 -> 0 |
| `tb_0215_c05` | q_mapping.scaffolding | 0 -> 1 |
| `tb_0215_c05` | primary_skill | content -> scaffolding |
| `tb_0576_c05` | q_mapping.content | 1 -> 0 |
| `tb_0056_c06` | q_mapping.scaffolding | 1 -> 0 |
| `tb_0113_c01` | q_mapping.diagnosis | 1 -> 0 |
| `tb_0113_c01` | primary_skill | diagnosis -> None |

## qmatrix_rulefix_v1 (rule-based: self-contradictory rows -> all-zero)

40 criteria had a q_rationale stating no skill is required while still loading >=1 skill; each was set to the all-zero mapping.

| Criterion | Prior mapping | Prior primary |
| --- | --- | --- |
| `tb_0006_c09` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0008_c05` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0017_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0019_c08` | content=1, diagnosis=0, scaffolding=0 | content |
| `tb_0039_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0047_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0050_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0057_c09` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0057_c13` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0064_c07` | content=1, diagnosis=0, scaffolding=0 | content |
| `tb_0084_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0088_c07` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0096_c03` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0104_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0111_c05` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0125_c07` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0134_c02` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0166_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0168_c09` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0213_c10` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0215_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0217_c04` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0253_c08` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0301_c02` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0333_c02` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0343_c06` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0355_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0390_c07` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0464_c15` | content=1, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0468_c07` | content=1, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0473_c10` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0475_c14` | content=1, diagnosis=0, scaffolding=0 | content |
| `tb_0489_c06` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0512_c01` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
| `tb_0573_c10` | content=1, diagnosis=0, scaffolding=0 | content |
| `tb_0583_c08` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0589_c01` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0596_c08` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0604_c02` | content=0, diagnosis=1, scaffolding=0 | diagnosis |
| `tb_0607_c06` | content=0, diagnosis=0, scaffolding=1 | scaffolding |
