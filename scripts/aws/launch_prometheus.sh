#!/usr/bin/env bash
# Launch (or find) the EC2 GPU instance that serves Prometheus 2 7B via vLLM.
#
# One command, fully self-provisioning:
#   - creates key pair  tutor-cat-key      (saved to scripts/aws/tutor-cat-key.pem)
#   - creates sec group tutor-cat-sg       (SSH only, from YOUR current IP; port
#                                           8000 is never exposed — use tunnel.sh)
#   - finds the latest AWS Deep Learning AMI (NVIDIA drivers preinstalled)
#   - launches a g5.xlarge (A10G 24GB, ~$1/hr) whose user-data installs vLLM and
#     runs Prometheus as a systemd service (auto-restarts, survives reboots)
#
# Idempotent: if a tutor-cat-prometheus instance already exists, prints it and exits.
# First boot takes ~10-15 min (vLLM install + 15GB model download). Check with:
#   scripts/aws/prometheus_ctl.sh status
#
# Requirements: aws cli v2 configured (aws configure), ssh. Works in Git Bash.

set -euo pipefail

TAG="tutor-cat-prometheus"
KEY_NAME="tutor-cat-key"
SG_NAME="tutor-cat-sg"
INSTANCE_TYPE="${INSTANCE_TYPE:-g5.xlarge}"
DISK_GB=100
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PEM="$SCRIPT_DIR/$KEY_NAME.pem"

# ---- already running? ----
EXISTING=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$TAG" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[][InstanceId,State.Name,PublicIpAddress]' --output text)
if [[ -n "$EXISTING" ]]; then
  echo "Instance already exists:"
  echo "  $EXISTING"
  echo "Use scripts/aws/prometheus_ctl.sh start|status, then scripts/aws/tunnel.sh"
  exit 0
fi

# ---- key pair ----
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  echo "Creating key pair $KEY_NAME -> $PEM"
  aws ec2 create-key-pair --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "$PEM"
  chmod 400 "$PEM"
elif [[ ! -f "$PEM" ]]; then
  echo "ERROR: key pair $KEY_NAME exists in AWS but $PEM is missing locally." >&2
  echo "Delete it (aws ec2 delete-key-pair --key-name $KEY_NAME) and rerun." >&2
  exit 1
fi

# ---- security group: SSH from the caller's IP only ----
MY_IP=$(curl -s https://checkip.amazonaws.com)
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [[ -z "$SG_ID" || "$SG_ID" == "None" ]]; then
  SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "tutor-cat prometheus judge - SSH only" --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)
fi
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
  --protocol tcp --port 22 --cidr "$MY_IP/32" >/dev/null 2>&1 || true  # ok if rule exists

# ---- latest Deep Learning AMI (Ubuntu 22.04, NVIDIA drivers baked in) ----
AMI_ID=$(aws ec2 describe-images --owners amazon \
  --filters "Name=name,Values=Deep Learning OSS Nvidia Driver AMI GPU PyTorch*Ubuntu 22.04*" \
            "Name=state,Values=available" \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)
echo "AMI: $AMI_ID"

# ---- user-data: install vLLM + systemd service on first boot ----
USER_DATA=$(cat <<'EOF'
#!/bin/bash
set -e
python3 -m venv /opt/vllm-env
/opt/vllm-env/bin/pip install --upgrade pip
/opt/vllm-env/bin/pip install vllm
cat > /etc/systemd/system/prometheus-judge.service <<'UNIT'
[Unit]
Description=vLLM serving Prometheus 2 7B (tutor-cat LLM judge)
After=network-online.target

[Service]
User=ubuntu
Environment=HF_HOME=/home/ubuntu/.cache/huggingface
ExecStart=/opt/vllm-env/bin/vllm serve prometheus-eval/prometheus-7b-v2.0 \
  --port 8000 --max-model-len 16384 --gpu-memory-utilization 0.92
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now prometheus-judge
EOF
)

# ---- launch ----
echo "Launching $INSTANCE_TYPE ..."
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$DISK_GB,\"VolumeType\":\"gp3\"}}]" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG}]" \
  --user-data "$USER_DATA" \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

echo ""
echo "Launched: $INSTANCE_ID  ($IP)"
echo "First boot needs ~10-15 min (vLLM install + 15GB model download)."
echo ""
echo "Next steps:"
echo "  scripts/aws/prometheus_ctl.sh status   # wait until 'JUDGE READY'"
echo "  scripts/aws/tunnel.sh                  # keep open while running the pipeline"
echo "  tutor-cat run --tutor all --mode both"
echo ""
echo "When done (stops billing): scripts/aws/prometheus_ctl.sh stop"
