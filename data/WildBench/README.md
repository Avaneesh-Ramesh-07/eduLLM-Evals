Scraped WildBench.

Scenario Schema: 
{
  "scenario_id": WB_<added_zero_based_index>,
  "use_case": <primary_tag>,
  "subject": <primary_tag>,
  "grade_band": "N/A",
  "modality": "text", - For now all of these will be text until image/multimodal is added
  "prompt": <”content” field in <conversation_input> column>
  "conversation_context": [<source document 1>, <source document 2>, …], 
  "reference_solution": <references> column
  "criterion_ids": [<scenario_id>_c01", …], - link to specific criteria entries used for evaluation,
  "source": "<link to WildBench on HuggingFace>",
  "split": "calibration", - which training portion this is part of
  "version": "1.0"
}
Rubric Schema:
{
  "criterion_id": "WB_00_c01",
  "scenario_id": "WB_00",
  "criterion": "Are the sources cited for the data on Hungary's and Indonesia's digital economy growth credible and current?" - each element of the checklist for this particular scenario
  "expected_evidence": [<references column>]
  "scoring_type": "binary", - no polytumous for now
  "score_anchors": null, - if polytumous, specific definitions for each level in the score range,
  "primary_skill": "<primary_tag>", 
  "q_mapping": {<take the corresponding primary_tag field and that entry in the q-map should be 1; rest should be 0
  }, - MIRT skill mapping - taken from each row’s criteria type
  "q_rationale": "The primary tag marked in the dataset was <q_mapping_priority>", 
  "criticality": "critical", - if it fails here, it’s a major error 
  "objectivity": "objective", - if it’s externally verifiable
  "explicitness": "explicit", - if the criteria is explicitly asked for in the scenario prompt.
  "source": "<link to dataset>",
  "status": "approved", - if the criteria is finalized/approved
  "version": "1.0",
}