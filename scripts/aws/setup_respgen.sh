#!/usr/bin/env bash
# ONE-TIME setup for the TutorBench response-generation job on the AWS P6 node.
# Run this ON the P6 box (the one with the 8x B200s) after `git pull`/clone.
# It only creates a venv and installs deps — it does NOT start the GPU job and
# touches no GPU, so it is safe to run while other jobs are on the node.
#
#   bash scripts/aws/setup_respgen.sh
#
# Then launch with scripts/aws/run_respgen_gpu2.sh (pinned to GPU index 2).

set -euo pipefail

cd "$(dirname "$0")/../.."          # -> repo root (tutor_cat/)
VENV="${VENV:-.venv}"

echo "== creating venv at $VENV =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip

echo "== installing tutor-cat with the [gen] extra (vllm/torch/transformers/boto3) =="
"$VENV/bin/pip" install -e ".[gen]"

echo ""
echo "Setup done. Before launching, in the SAME shell:"
echo "  source $VENV/bin/activate"
echo "  export HF_TOKEN=<token>        # gated repos (meta-llama, gemma, Llama-2 tokenizer). Never commit."
echo "  nvidia-smi -L                  # confirm this node numbers GPUs 0..7 and which are free"
echo ""
echo "Launch (GPU index 2 only, does not disturb other GPUs):"
echo "  bash scripts/aws/run_respgen_gpu2.sh          # add S3: S3_URI=s3://<bucket>/<prefix> bash ..."
