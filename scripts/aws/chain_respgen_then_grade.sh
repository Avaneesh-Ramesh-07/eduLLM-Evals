#!/usr/bin/env bash
# Autonomous node-side chain: WAIT for the filtered respgen run to finish, then
# run the full grading pipeline on GPU 4 — with no operator machine attached.
# Launched once (detached via setsid nohup); everything after is on the node.
#
# Completion of respgen is detected by the "RESPGEN COMPLETE" marker its wrapper
# writes to full_launch2.log (or the wrapper process disappearing). Then it waits
# for GPU 4 to be free and invokes run_grading_gpu4.sh (bridge -> judge -> ingest).
# All logs go to chain.log and are synced to S3 so results are retrievable even if
# the laptop is closed the whole time.
set -uo pipefail

GPU="${GPU:-4}"
RESP_LOG="${RESP_LOG:-/opt/dlami/nvme/tutor-cat-v2/full_launch2.log}"
RESP_WRAPPER_PAT="${RESP_WRAPPER_PAT:-run_on_node_gpu4.sh}"
GRADE_WRAP="${GRADE_WRAP:-/opt/dlami/nvme/tutor-grading/eduLLM-Evals/scripts/aws/run_grading_gpu4.sh}"
MATRIX_DIR="${MATRIX_DIR:-/opt/dlami/nvme/tutor-grading/eduLLM-Evals/staging}"
CHAIN_LOG="${CHAIN_LOG:-/opt/dlami/nvme/tutor-grading/chain.log}"
S3_ARTIFACTS="${S3_ARTIFACTS:-s3://edullm-adaptive-inference-056956104102/edu-tutor-grading/artifacts}"
WAIT_TIMEOUT_H="${WAIT_TIMEOUT_H:-12}"

mkdir -p "$(dirname "$CHAIN_LOG")"
log(){ echo "[$(date -u +%FT%TZ)] $*" | tee -a "$CHAIN_LOG"; }
sync_logs(){ aws s3 cp "$CHAIN_LOG" "$S3_ARTIFACTS/chain.log" --only-show-errors 2>/dev/null || true; }

log "=== chain start (gpu=$GPU) ==="
log "waiting for respgen completion: marker 'RESPGEN COMPLETE' in $RESP_LOG"

deadline=$(( $(date +%s) + WAIT_TIMEOUT_H*3600 ))
while :; do
  if grep -q "RESPGEN COMPLETE" "$RESP_LOG" 2>/dev/null; then
    log "respgen finished (marker found)"; break
  fi
  if ! pgrep -f "$RESP_WRAPPER_PAT" >/dev/null 2>&1; then
    sleep 20
    if grep -q "RESPGEN COMPLETE" "$RESP_LOG" 2>/dev/null; then log "respgen finished (marker after exit)"; break; fi
    log "respgen wrapper gone without marker; proceeding anyway"; break
  fi
  if [ "$(date +%s)" -gt "$deadline" ]; then
    log "TIMEOUT after ${WAIT_TIMEOUT_H}h waiting for respgen; aborting chain"; sync_logs; exit 1
  fi
  sleep 60
  sync_logs
done

# Let the respgen worker release GPU memory, then confirm GPU 4 is free.
log "waiting for GPU $GPU to be free before grading"
for _ in $(seq 1 90); do
  busy="$(nvidia-smi --id="$GPU" --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -c . || true)"
  if [ "${busy:-0}" = "0" ]; then log "GPU $GPU free"; break; fi
  sleep 20
done

log "=== launching grading pipeline: $GRADE_WRAP ==="
sync_logs
GPU="$GPU" bash "$GRADE_WRAP" >> "$CHAIN_LOG" 2>&1
rc=$?
log "grading pipeline exited rc=$rc"

# Publish artifacts so they're retrievable regardless of the laptop being closed.
for f in response_matrix.csv response_matrix.npy response_matrix_manifest.json \
         verdicts.jsonl judge_inputs_manifest.json; do
  [ -f "$MATRIX_DIR/$f" ] && aws s3 cp "$MATRIX_DIR/$f" "$S3_ARTIFACTS/$f" --only-show-errors 2>/dev/null || true
done
sync_logs
log "=== chain complete (rc=$rc). artifacts -> $S3_ARTIFACTS ==="
exit "$rc"
