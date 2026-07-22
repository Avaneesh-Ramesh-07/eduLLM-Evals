#!/usr/bin/env bash
# Open the SSH tunnel to the Prometheus judge instance: localhost:8000 -> EC2:8000.
# Keep this terminal open while running the pipeline. Ctrl-C to close.

set -euo pipefail

TAG="tutor-cat-prometheus"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEM="$SCRIPT_DIR/tutor-cat-key.pem"

IP=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

if [[ -z "$IP" || "$IP" == "None" ]]; then
  echo "No running $TAG instance. Start one:" >&2
  echo "  scripts/aws/launch_prometheus.sh        (first time)" >&2
  echo "  scripts/aws/prometheus_ctl.sh start     (if stopped)" >&2
  exit 1
fi

echo "Tunneling localhost:8000 -> $IP:8000  (Ctrl-C to close)"
echo "Test in another terminal:  curl http://localhost:8000/v1/models"
exec ssh -i "$PEM" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=60 \
  -N -L 8000:localhost:8000 "ubuntu@$IP"
