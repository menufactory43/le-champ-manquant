#!/bin/bash
# Keep Jev playing and the harness healing: restart on crash, resume from live.state.
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
export PATH="$HOME/.local/bin:$PATH"
AWAKE=""; command -v caffeinate > /dev/null && AWAKE="caffeinate -i"      # macOS only
( while true; do
    .venv/bin/python repair.py >> repair.log 2>&1
    echo "$(date) — réparateur arrêté (code $?), relance dans 30 s" >> repair.log; sleep 30
  done ) &
trap 'kill 0' EXIT INT TERM
while true; do
  $AWAKE .venv/bin/python serve.py "$@" >> serve.log 2>&1
  echo "$(date) — serveur arrêté (code $?), relance dans 5 s" >> serve.log
  sleep 5
done
