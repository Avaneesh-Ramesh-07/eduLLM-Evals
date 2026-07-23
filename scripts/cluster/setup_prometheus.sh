#!/usr/bin/env bash
# ONE-TIME setup — run this on an MIT ORCD/Engaging LOGIN node.
# Creates a venv with vLLM and pre-downloads the Prometheus weights, so the
# compute node never needs internet access.
#
# Easiest path — everything in the browser at https://orcd-ood.mit.edu:
#   1. Files -> Home Directory -> Upload: this script + serve_prometheus.sbatch
#   2. Clusters -> Shell Access
#   3. bash setup_prometheus.sh       # ~15 min (pip install + 15GB download)
# (Or classic: scp both scripts to <user>@orcd-login001.mit.edu and ssh in.)
#
# If python3 is too old on the login node, load a module first (check
# `module avail python miniforge`), e.g.:  module load miniforge

set -euo pipefail

VENV="${VENV:-$HOME/vllm-env}"
MODEL="prometheus-eval/prometheus-7b-v2.0"
# Weights cache — keep identical in serve_prometheus.sbatch. If your home
# quota is tight (~15GB needed), set both to scratch, e.g.:
#   export HF_HOME=/orcd/scratch/<pool>/<user>/huggingface
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"

echo "== creating venv at $VENV =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install vllm "huggingface_hub[cli]"

echo "== pre-downloading $MODEL to the HF cache (~15GB) =="
"$VENV/bin/huggingface-cli" download "$MODEL"

echo ""
echo "Setup done. Start the judge with:"
echo "  sbatch serve_prometheus.sbatch"
