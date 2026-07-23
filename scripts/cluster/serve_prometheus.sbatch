#!/bin/bash
# SLURM job serving Prometheus 2 7B via vLLM — submit ON THE CLUSTER LOGIN NODE:
#   sbatch serve_prometheus.sbatch
#
# Find where it landed + readiness:
#   squeue --me                            # node name, state R = running
#   grep -m1 NODE: prometheus-judge-*.log  # also printed in the job log
#   tail -f prometheus-judge-<jobid>.log   # "Uvicorn running" = ready
#
# Stop it (stops consuming your GPU allocation):
#   scancel <jobid>
#
# Preconfigured for MIT ORCD / Engaging (orcd-ood.mit.edu portal,
# orcd-login00X.mit.edu ssh). If partition names changed, check `sinfo`.
# The 7B model needs a >=24GB GPU — Engaging's L40S (48GB) is plenty.

#SBATCH --job-name=prometheus-judge
#SBATCH --partition=mit_normal_gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=08:00:00
#SBATCH --output=prometheus-judge-%j.log

set -euo pipefail

VENV="${VENV:-$HOME/vllm-env}"
PORT="${PORT:-8000}"
# Must match setup_prometheus.sh so the pre-downloaded weights are found.
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
NODE=$(hostname)

echo "NODE: $NODE"
echo "PORT: $PORT"
echo ""
echo "From your laptop, open the tunnel (keep it open while running the pipeline):"
echo "  ssh -N -o ServerAliveInterval=60 -L ${PORT}:${NODE}:${PORT} $USER@orcd-login001.mit.edu"
echo "then verify:  curl http://localhost:${PORT}/v1/models"
echo ""

# --- GPU diagnostics (printed before vLLM so init failures are explainable) ---
echo "== nvidia-smi =="
nvidia-smi || echo "WARNING: nvidia-smi failed - job has no visible GPU (check --gres/partition)"
echo "== torch CUDA check =="
"$VENV/bin/python" - <<'PY'
import torch
print("torch:", torch.__version__, " cuda build:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device:", torch.cuda.get_device_name(0))
PY
echo "================================================================"

# Weights were pre-downloaded by setup_prometheus.sh; don't touch the network.
export HF_HUB_OFFLINE=1

# Compute nodes have the NVIDIA driver but no CUDA toolkit (no nvcc), and
# FlashInfer's sampler JIT-compiles at startup -> "Could not find nvcc" crash.
# Fall back to vLLM's native torch sampler instead (no compilation needed).
export VLLM_USE_FLASHINFER_SAMPLER=0

# --host 0.0.0.0 so the login node (your tunnel's entry point) can reach it.
exec "$VENV/bin/vllm" serve prometheus-eval/prometheus-7b-v2.0 \
  --host 0.0.0.0 --port "$PORT" \
  --max-model-len 16384 --gpu-memory-utilization 0.92
