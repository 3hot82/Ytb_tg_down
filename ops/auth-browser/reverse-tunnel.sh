#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -f .auth-proxy.env ]; then
  set -a
  . ./.auth-proxy.env
  set +a
fi

: "${AUTH_PROXY_SSH_HOST:?Set AUTH_PROXY_SSH_HOST, for example user@your-vps.example}"

REMOTE_HOST=${AUTH_PROXY_SSH_HOST}
REMOTE_PORT=${AUTH_PROXY_PORT:-33000}
LOCAL_HOST=${AUTH_BROWSER_LOCAL_HOST:-127.0.0.1}
LOCAL_PORT=${AUTH_BROWSER_LOCAL_PORT:-33000}

exec ssh -N \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -R "127.0.0.1:${REMOTE_PORT}:${LOCAL_HOST}:${LOCAL_PORT}" \
  "${REMOTE_HOST}"
