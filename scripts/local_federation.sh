#!/usr/bin/env bash
# Real Flower deployment on this machine: one SuperLink + one SuperNode per SF neighborhood,
# registered with name and lat/long (same flow as adding own SuperNodes to SuperGrid).
# Usage: scripts/local_federation.sh start|stop
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=runs/fed
HOODS=("mission:37.7575,-122.4160" "soma:37.7770,-122.4000" "chinatown:37.7975,-122.4105"
       "marina:37.8005,-122.4380" "alamo:37.7775,-122.4385" "sunset:37.7560,-122.4880")

if [[ "${1:-start}" == "stop" ]]; then
  pkill -f "flower-supernode.*runs/fed" || true
  pkill -f "flower-superlink.*runs/fed" || true
  exit 0
fi

# Model access for AgentApps (FLWR_MODEL_API_KEY, optional FLWR_MODEL_API_ENDPOINT) from .env
if [[ -f .env ]]; then set -a; source .env; set +a; fi
mkdir -p "$DIR/keys" "$DIR/tls"
CERTS="$(pwd)/$DIR/tls"
if [[ ! -f "$CERTS/server.pem" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=enjambre-local-ca" \
    -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$CERTS/server.key" \
    -out "$CERTS/server.csr" 2>/dev/null
  printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\n' > "$CERTS/san.ext"
  openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -days 7 -extfile "$CERTS/san.ext" -out "$CERTS/server.pem" 2>/dev/null
fi
uv run flower-superlink --enable-supernode-auth --isolation subprocess \
  --ssl-ca-certfile "$CERTS/ca.crt" --ssl-certfile "$CERTS/server.pem" --ssl-keyfile "$CERTS/server.key" \
  --database "$DIR/state.db" --log-file "$DIR/superlink.log" > "$DIR/superlink.out" 2>&1 &
sleep 8

if ! grep -q "sf-local" ~/.flwr/config.toml; then
  printf '\n[superlink.sf-local]\naddress = "127.0.0.1:8000"\nroot-certificates = "%s/ca.crt"\n' "$CERTS" >> ~/.flwr/config.toml
fi

port=9200
for entry in "${HOODS[@]}"; do
  hood="${entry%%:*}"; loc="${entry#*:}"
  key="$DIR/keys/$hood"
  [[ -f "$key" ]] || ssh-keygen -q -t ecdsa -b 384 -N "" -f "$key" -C "sf-$hood"
  uv run flwr supernode register "$key.pub" sf-local --name "sf-$hood" --location "$loc" >/dev/null 2>&1 || true
  # Participant models: Kimi on the first three sites, MiniMax on the others (Nebius Token Factory).
  # Keys come from .env: NEBIUS_KIMI_API_KEY / NEBIUS_MINIMAX_API_KEY. Without them the node uses the
  # default Flower endpoint (FLWR_MODEL_API_KEY) or the labelled fallback.
  if [[ $port -lt 9203 ]]; then nb_key="${NEBIUS_KIMI_API_KEY:-}"; nb_model="dedicated/flowerai/Kimi-K2.7-Code-1OUHWL"
  else nb_key="${NEBIUS_MINIMAX_API_KEY:-}"; nb_model="dedicated/flowerai/MiniMax-M3-OOLI9o"; fi
  node_env=()
  if [[ -n "$nb_key" ]]; then
    node_env=(FLWR_MODEL_API_ENDPOINT="https://api.tokenfactory.tf-ca1.nebius.com/v1/responses"
              FLWR_MODEL_API_KEY="$nb_key" SWARM_PARTICIPANT_MODEL="$nb_model")
  fi
  env "${node_env[@]}" uv run flower-supernode --root-certificates "$CERTS/ca.crt" --superlink 127.0.0.1:9092 \
    --auth-supernode-private-key "$key" --auth-supernode-public-key "$key.pub" \
    --port "$port" --isolation subprocess --node-config "hood=\"$hood\"" \
    > "$DIR/supernode-$hood.log" 2>&1 &
  port=$((port + 1))
done
sleep 8
uv run flwr supernode ls sf-local
