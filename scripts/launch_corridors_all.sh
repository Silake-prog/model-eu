#!/usr/bin/env bash
# scripts/launch_corridors_all.sh — Plan C launcher.
#
# Syncs everything to inari and starts a detached parallel-2 scheduler
# running the 10 corridor variants (5 scenarios × {2x, 3x}).
#
# Safe to launch even while policy_re_noMin is still running — the
# scheduler auto-waits for in-flight run_adequacy.py before starting
# the queue.
#
# Wall-clock estimate: 12 solves @ ~3 h each, 2 in parallel → ceil(12/2) × 3h = 18h.
# Includes 2 re-solves (policy_nuke + R0_v1_nuke) with the Nuclear-injection
# fix, plus the 10 corridor variants.
# If launched ~13:00 UTC today, done ~07:00 UTC tomorrow.
#
# Usage (from your Mac):
#   bash ~/Desktop/clever-work/scripts/launch_corridors_all.sh
#
# Monitor:
#   ssh brigode@inari.centre-cired.fr 'tail -f /diskdata/cired/brigode/clever-work/logs/corridors_all.log'
# Detect completion:
#   ssh brigode@inari.centre-cired.fr 'cat /diskdata/cired/brigode/clever-work/results/diagnostics/PARALLEL_DONE 2>/dev/null'

set -euo pipefail

HOST=brigode@inari.centre-cired.fr
LOCAL=~/Desktop/clever-work
REMOTE=/diskdata/cired/brigode/clever-work

# Order: (1) the 2 _nuke re-solves go first because the previous policy_nuke
# and R0_v1_nuke results were Nuclear-less (CLEVER 2050 declares 0 nuclear, so
# the EXPANDABLE flip silently did nothing). The Nuclear-injection fix in
# add_dispatchable_from_non_enr now force-creates Nuclear with the
# EXPANSION_HEADROOM_BY_COUNTRY caps. Then (2) corridor variants alphabetical,
# low-demand bundles first so the first 2 parallel slots are smaller LPs.
SCENARIOS=(
  # Re-solves (Nuclear now properly injected)
  policy_nuke          R0_v1_nuke
  # Corridor variants
  R0_v1_corr2x         R0_v1_corr3x
  R0_v1_nuke_corr2x    R0_v1_nuke_corr3x
  policy_re_corr2x     policy_re_corr3x
  policy_nuke_corr2x   policy_nuke_corr3x
  policy_re_noMin_corr2x policy_re_noMin_corr3x
)

echo "==> sync clever/ (constants + model corridor hook)"
rsync -avz "$LOCAL/clever/" "$HOST:$REMOTE/clever/"

echo
echo "==> sync notebook (10 corridor SCENARIO values)"
rsync -avz "$LOCAL/notebooks/adequacy_clean.ipynb" "$HOST:$REMOTE/notebooks/"

echo
echo "==> sync scripts/ (parallel scheduler)"
rsync -avz "$LOCAL/scripts/" "$HOST:$REMOTE/scripts/"

echo
echo "==> sync tables/ (per-scenario CSV dirs incl. corridor variants)"
rsync -avz "$LOCAL/tables/" "$HOST:$REMOTE/tables/"

echo
echo "==> kill any stale parallel scheduler (safe no-op if none)"
# Wrap with `|| true` so a non-zero exit (e.g. pkill returning 1 when no
# match, or any ssh transient hiccup) doesn't abort the launcher under set -e.
ssh "$HOST" "BASHRCSOURCED= bash --noprofile --norc -c 'pkill -f run_scenarios_parallel.sh 2>/dev/null; sleep 1; true'" || true

echo
echo "==> launch detached parallel-2 scheduler"
ssh "$HOST" "BASHRCSOURCED= bash --noprofile --norc -c '
    export BASHRCSOURCED=
    cd $REMOTE
    chmod +x scripts/run_scenarios_parallel.sh
    mkdir -p logs
    nohup bash scripts/run_scenarios_parallel.sh ${SCENARIOS[*]} > logs/corridors_all.log 2>&1 &
    echo \"  spawned PID \$!\"
    sleep 2
    pgrep -af run_scenarios_parallel.sh | head -3 | sed \"s/^/  PID alive: /\"
'"

echo
echo "==> launched. ${#SCENARIOS[@]} scenarios queued, max 2 parallel."
echo "    Scenarios in order:"
printf '      %s\n' "${SCENARIOS[@]}"
echo
echo "    Live log:              ssh $HOST 'tail -f $REMOTE/logs/corridors_all.log'"
echo "    Per-scenario logs:     $REMOTE/logs/<scenario>.log"
echo "    Poll for completion:   ssh $HOST 'cat $REMOTE/results/diagnostics/PARALLEL_DONE 2>/dev/null'"
echo "    Expected wall:         ~15 h (5 rounds × ~3 h)"
