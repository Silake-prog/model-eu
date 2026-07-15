#!/usr/bin/env bash
# scripts/_remote_launch.sh — server-side helper invoked by the Mac wrapper.
# Pre-defines BASHRCSOURCED so any bashrc sourcing downstream won't trip
# strict mode. Kills any stale runner, then nohup's the overnight runner.

export BASHRCSOURCED=""

WORK=/diskdata/cired/brigode/clever-work
cd "$WORK"

mkdir -p logs

# Kill any stale runner from previous launches (safe no-op if none)
pkill -f run_all_overnight.sh 2>/dev/null || true
sleep 1

chmod +x scripts/run_all_overnight.sh

nohup bash scripts/run_all_overnight.sh > logs/overnight.log 2>&1 &
RUNNER_PID=$!
echo "  spawned PID $RUNNER_PID"

sleep 2
pgrep -f run_all_overnight.sh | head -3 | sed 's/^/  PID alive: /'
