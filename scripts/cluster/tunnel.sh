#!/usr/bin/env bash
# Run LOCALLY: tunnel localhost:8000 -> the cluster compute node running the judge.
# Keep this terminal open while the pipeline runs. Ctrl-C to close.
#
# Usage:
#   scripts/cluster/tunnel.sh <compute-node> <user>@<login-host> [port]
# Example (MIT ORCD/Engaging):
#   scripts/cluster/tunnel.sh node1234 <kerberos>@orcd-login.mit.edu
#
# (<compute-node> comes from `squeue --me` or the NODE: line in the job log.
#  Note: the tunnel goes to a LOGIN node, never to orcd-ood.mit.edu — that's
#  the web portal, not an ssh host.)
# Works in Git Bash on Windows; plain PowerShell equivalent:
#   ssh -N -o ServerAliveInterval=60 -L 8000:<node>:8000 <user>@<login-host>

set -euo pipefail

NODE="${1:?usage: tunnel.sh <compute-node> <user>@<login-host> [port]}"
LOGIN="${2:?usage: tunnel.sh <compute-node> <user>@<login-host> [port]}"
PORT="${3:-8000}"

echo "Tunneling localhost:$PORT -> $NODE:$PORT via $LOGIN  (Ctrl-C to close)"
echo "Test in another terminal:  curl http://localhost:$PORT/v1/models"

# Keepalives so the login node doesn't drop an idle session; fail loudly if the
# local port can't bind (stale tunnel) instead of silently connecting with no
# forward. On macOS, run under `caffeinate` so the tunnel survives display/idle
# sleep during a long run (the pipeline is not resumable, so a drop is costly).
SSH_OPTS=(-N -o ServerAliveInterval=60 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -L "$PORT:$NODE:$PORT")
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -is ssh "${SSH_OPTS[@]}" "$LOGIN"
else
  exec ssh "${SSH_OPTS[@]}" "$LOGIN"
fi
