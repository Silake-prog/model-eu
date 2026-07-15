#!/usr/bin/env bash
# scripts/launch_corridors.sh — Mac-side wrapper that:
#   1. Syncs the updated clever/ + notebook + scripts/ to inari
#   2. Starts a detached nohup-launched sequential runner for the 4
#      corridor SCENARIO variants on the server
#
# Run AFTER policy_re_noMin finishes (the runner solves sequentially and
# would compete with noMin for RAM otherwise — 2 × ~50 GB on a 124 GB box
# is marginal).
#
# Usage (from your Mac):
#   bash ~/Desktop/clever-work/scripts/launch_corridors.sh
#
# Monitor:
#   ssh brigode@inari.centre-cired.fr 'tail -f /diskdata/cired/brigode/clever-work/logs/corridors.log'
# Detect completion (sentinel):
#   ssh brigode@inari.centre-cired.fr 'cat /diskdata/cired/brigode/clever-work/results/diagnostics/SEQUENTIAL_DONE 2>/dev/null'

set -euo pipefail

HOST=brigode@inari.centre-cired.fr
LOCAL=~/Desktop/clever-work
REMOTE=/diskdata/cired/brigode/clever-work

echo "==> sync clever/ (constants + model with corridor hook)"
rsync -avz "$LOCAL/clever/" "$HOST:$REMOTE/clever/"

echo
echo "==> sync notebook (adds 4 corridor SCENARIO values)"
rsync -avz "$LOCAL/notebooks/adequacy_clean.ipynb" "$HOST:$REMOTE/notebooks/"

echo
echo "==> sync scripts/ (sequential runner)"
rsync -avz "$LOCAL/scripts/" "$HOST:$REMOTE/scripts/"

echo
echo "==> kill any stale runner from prior corridor attempts"
ssh "$HOST" "BASHRCSOURCED= bash --noprofile --norc -c 'pkill -f run_scenarios_sequential.sh 2>/dev/null || true ; sleep 1'"

echo
echo "==> launch detached sequential runner for 4 corridor variants"
# Pass the 4 scenarios as args to the sequential runner. Writes log to
# logs/corridors.log; per-scenario logs at logs/<scenario>.log.
ssh "$HOST" "BASHRCSOURCED= bash --noprofile --norc -c '
    export BASHRCSOURCED=
    cd $REMOTE
    chmod +x scripts/run_scenarios_sequential.sh
    mkdir -p logs
    nohup bash scripts/run_scenarios_sequential.sh policy_re_corr2x policy_re_corr3x policy_nuke_corr2x policy_nuke_corr3x > logs/corridors.log 2>&1 &
    echo \"  spawned PID \$!\"
    sleep 2
    pgrep -af run_scenarios_sequential.sh | head -3 | sed \"s/^/  PID alive: /\"
'"

echo
echo "==> launched. Sequential runner is processing 4 scenarios on $HOST."
echo "    Live log:              ssh $HOST 'tail -f $REMOTE/logs/corridors.log'"
echo "    Per-scenario logs:     logs/{policy_re,policy_nuke}_corr{2x,3x}.log"
echo "    Poll for completion:   ssh $HOST 'cat $REMOTE/results/diagnostics/SEQUENTIAL_DONE 2>/dev/null'"
echo "    Expected total wall:   ~12 h (4 × 3 h sequential)"
