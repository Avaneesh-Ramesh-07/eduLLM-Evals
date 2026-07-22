#!/usr/bin/env bash
# Control the Prometheus judge instance:  status | start | stop | terminate | ip
#   status    - instance state + whether the vLLM judge answers on :8000
#   start     - start a stopped instance (model is cached; ready in ~2 min)
#   stop      - stop billing, keep the disk (model stays downloaded)
#   terminate - delete the instance entirely
#   ip        - print the public IP

set -euo pipefail

TAG="tutor-cat-prometheus"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEM="$SCRIPT_DIR/tutor-cat-key.pem"
CMD="${1:-status}"

read -r ID STATE IP <<<"$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$TAG" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].[InstanceId,State.Name,PublicIpAddress]' \
  --output text)"

if [[ -z "${ID:-}" || "$ID" == "None" ]]; then
  echo "No $TAG instance found. Run scripts/aws/launch_prometheus.sh"
  exit 1
fi

case "$CMD" in
  status)
    echo "instance: $ID  state: $STATE  ip: $IP"
    if [[ "$STATE" == "running" ]]; then
      if ssh -i "$PEM" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "ubuntu@$IP" \
           "curl -s --max-time 5 http://localhost:8000/v1/models" 2>/dev/null | grep -q prometheus; then
        echo "JUDGE READY - open the tunnel (scripts/aws/tunnel.sh) and run the pipeline."
      else
        echo "judge not up yet (first boot: ~10-15 min for vLLM install + model download)"
        echo "watch progress:  ssh -i $PEM ubuntu@$IP 'journalctl -u prometheus-judge -f'"
      fi
    fi
    ;;
  start)     aws ec2 start-instances --instance-ids "$ID" >/dev/null; echo "starting $ID (ready in ~2 min; check with: $0 status)" ;;
  stop)      aws ec2 stop-instances --instance-ids "$ID" >/dev/null; echo "stopping $ID (billing stops; disk and model kept)" ;;
  terminate) aws ec2 terminate-instances --instance-ids "$ID" >/dev/null; echo "terminating $ID" ;;
  ip)        echo "$IP" ;;
  *)         echo "usage: $0 {status|start|stop|terminate|ip}" >&2; exit 1 ;;
esac
