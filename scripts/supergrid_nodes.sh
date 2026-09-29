#!/usr/bin/env bash
# Connect our six named, geolocated hospital SuperNodes to SuperGrid (your personal federation).
# Prereq: `flwr login supergrid` and keys registered with:
#   flwr supernode register runs/sg/keys/<hood>.pub supergrid --name sf-<hood> --location <lat,lon>
# Usage: scripts/supergrid_nodes.sh start|stop
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-start}" == "stop" ]]; then pkill -f "flower-supernode.*fleet-supergrid" || true; exit 0; fi
if [[ -f .env ]]; then set -a; source .env; set +a; fi
mkdir -p runs/sg
port=9300
for hood in mission soma chinatown marina alamo sunset; do
  k=runs/sg/keys/$hood
  # Kimi on the first three hospitals, MiniMax on the others (Nebius Token Factory keys from .env)
  if [[ $port -lt 9303 ]]; then nb_key="${NEBIUS_KIMI_API_KEY:-}"; nb_model="dedicated/flowerai/Kimi-K2.7-Code-1OUHWL"
  else nb_key="${NEBIUS_MINIMAX_API_KEY:-}"; nb_model="dedicated/flowerai/MiniMax-M3-OOLI9o"; fi
  node_env=()
  if [[ -n "$nb_key" ]]; then
    node_env=(FLWR_MODEL_API_ENDPOINT="https://api.tokenfactory.tf-ca1.nebius.com/v1/responses"
              FLWR_MODEL_API_KEY="$nb_key" SWARM_PARTICIPANT_MODEL="$nb_model")
  fi
  env ${node_env[@]+"${node_env[@]}"} uv run flower-supernode --superlink fleet-supergrid.flower.ai:443 \
    --auth-supernode-private-key "$k" --auth-supernode-public-key "$k.pub" \
    --port "$port" --isolation subprocess --allow-runtime-dependency-installation \
    > "runs/sg/supernode-$hood.log" 2>&1 &
  port=$((port + 1))
done
sleep 12
uv run flwr supernode ls supergrid
