#!/usr/bin/env bash
# Hourly discovery cycle on the box (DECISIONS D11.7): candidates -> judge (new templates only) -> flags.
# The collector refreshes the catalog hourly on its own; `python -m arb` picks up new flags within 60 s.
#   tmux new -s refresh -d 'cd ~/ieor4011-agentic-ai-project && source .venv/bin/activate && bash scripts/refresh_loop.sh'
set -u
cd "$(dirname "$0")/.."
while true; do
  echo "=== refresh $(date -u +%FT%TZ)"
  for s in football/nfl basketball/nba; do
    python -m candidates sports "$s" 2>&1 | grep -E 'Total market pairs|Status|Template pairs' | sed "s#^#[$s] #"
    python -m judge sports "$s" 2>&1 | grep -E '^\s+[0-9a-f]{16}|identical|failed' | sed "s#^#[judge $s] #"
  done
  python -m candidates.flag 2>&1 | tr -d '\n' | sed 's/  */ /g'; echo
  sleep 3600
done
