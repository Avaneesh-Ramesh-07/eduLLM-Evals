#!/usr/bin/env bash
# Launch the TutorBench response-generation job on the AWS P6 node, PINNED TO
# GPU INDEX 2 ONLY. All 100 models run sequentially on that one B200; no other
# GPU is ever touched (each worker gets CUDA_VISIBLE_DEVICES=2), so jobs on the
# other GPUs are left completely alone.
#
# Prereqs (see scripts/aws/setup_respgen.sh):
#   source .venv/bin/activate
#   export HF_TOKEN=<token>
# Optional:
#   export S3_URI=s3://<bucket>/<prefix>     # periodic per-model upload (instance IAM)
#   export OUT_DIR=runs/responses            # local shard dir (default)
#   export GPU=2                             # override the pinned index if ever needed
#
# Run it (recommended inside tmux so a laptop disconnect doesn't kill it):
#   tmux new -s respgen
#   bash scripts/aws/run_respgen_gpu2.sh
#
# Resumable: re-running skips scenarios already in each shard. Nothing here
# provisions AWS resources or spends beyond the P6 box you already pay for.

set -euo pipefail

cd "$(dirname "$0")/../.."          # -> repo root (tutor_cat/)

GPU="${GPU:-2}"
OUT_DIR="${OUT_DIR:-runs/responses}"

: "${HF_TOKEN:?set HF_TOKEN in the environment (gated repos); never commit it}"

command -v nvidia-smi >/dev/null 2>&1 || { echo "nvidia-smi not found — are you on the GPU node?" >&2; exit 1; }

# --- GPU index must exist on this node -------------------------------------
NGPU="$(nvidia-smi -L | wc -l)"
if (( GPU >= NGPU )); then
  echo "This node has $NGPU GPU(s), valid indices 0..$((NGPU-1)); GPU $GPU does not exist." >&2
  echo "(CUDA indices are 0-based: the 8th GPU is index 7, not 8.)" >&2
  exit 1
fi

# --- refuse to start if GPU 2 is already in use (never disturb existing jobs) ---
BUSY="$(nvidia-smi --id="$GPU" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c . || true)"
if (( BUSY > 0 )); then
  echo "GPU $GPU already has $BUSY running compute process(es); refusing to start so I don't disturb them." >&2
  echo "Free GPU $GPU first, or set GPU=<a free index> — do NOT kill the existing job." >&2
  nvidia-smi --id="$GPU"
  exit 1
fi

echo "== launching response generation on GPU index $GPU (100 models, 662 scenarios) =="
echo "   out_dir=$OUT_DIR  s3=${S3_URI:-<none>}  resume=on"

ARGS=(generate --gpu-ids "$GPU" --out-dir "$OUT_DIR")
[[ -n "${S3_URI:-}" ]] && ARGS+=(--s3-uri "$S3_URI")

exec tutor-cat "${ARGS[@]}"
