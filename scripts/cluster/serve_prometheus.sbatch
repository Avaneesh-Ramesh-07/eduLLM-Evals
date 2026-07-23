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
# orcd-login.mit.edu ssh). If partition names changed, check `sinfo`.
# The 7B model needs a >=24GB GPU — Engaging's L40S (48GB) is plenty.

#SBATCH --job-name=prometheus-judge
#SBATCH --partition=mit_normal_gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=06:00:00     # mit_normal_gpu caps at 6h; 8h is rejected at submit
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
echo "  ssh -N -o ServerAliveInterval=60 -L ${PORT}:${NODE}:${PORT} $USER@orcd-login.mit.edu"
echo "then verify:  curl http://localhost:${PORT}/v1/models"
echo ""

# Weights were pre-downloaded by setup_prometheus.sh; don't touch the network.
export HF_HUB_OFFLINE=1

# --host 0.0.0.0 so the login node (your tunnel's entry point) can reach it.
exec "$VENV/bin/vllm" serve prometheus-eval/prometheus-7b-v2.0 \
  --host 0.0.0.0 --port "$PORT" \
  --max-model-len 16384 --gpu-memory-utilization 0.92
