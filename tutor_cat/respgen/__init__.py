"""Response-generation subpackage: run open-weight models over TutorBench
scenarios on GPU, then emit the PRD Model Output schema to sharded JSONL and
(optionally) upload to S3.

Layering — heavy deps (vllm / torch / transformers / boto3) are imported LAZILY
inside backends.py, runner.py, orchestrator.py and s3.py. The pure modules
(manifest, registry, prompts, records, shard) import on any machine and stay
unit-testable without a GPU. Install the runtime deps on the GPU host with:

    pip install -e ".[gen]"

This subpackage builds ONLY the response matrix (rows = models, cols = 662
scenarios). The Q-matrix, LLM judge and MIRT calibration are out of scope and
live in tutor_cat.mirt / tutor_cat.judge / scripts/*qmatrix*.
"""
