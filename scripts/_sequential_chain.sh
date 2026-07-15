#!/bin/bash
# Wait for HIGH no-imports to finish, then launch CENTRAL no-imports solo.
# Both use the new loose Gurobi tolerances (BarConvTol=1e-3, etc.) from clever/runner.py
cd /diskdata/cired/brigode/clever-work || exit 1

LOG=logs/sequential_chain.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

count_runs() {
    pgrep -af '^python -u scripts/run_adequacy.py$' 2>/dev/null | wc -l
}

echo "$(ts)  chain watcher PID=$$ starting" >> "$LOG"
echo "$(ts)  waiting for HIGH no-imports (currently running) to finish..." >> "$LOG"

# Wait until no adequacy run is active (HIGH has finished + saved)
while [ "$(count_runs)" -gt 0 ]; do
    sleep 60
done

echo "$(ts)  HIGH finished. Confirming via log + diagnostics..." >> "$LOG"

# Quick sanity: did HIGH actually save?
HIGH_DIAG=/diskdata/cired/brigode/clever-work/results/diagnostics/R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT
HIGH_SOL_MTIME=$(stat -c %Y "$HIGH_DIAG/solution_2050.nc" 2>/dev/null || echo 0)
NOW_MTIME=$(date -u +%s)
AGE=$(( NOW_MTIME - HIGH_SOL_MTIME ))
echo "$(ts)  HIGH solution_2050.nc age = ${AGE}s (fresh = <600s old)" >> "$LOG"

# 60s grace period for the kernel/filesystem to settle + RSS to release
sleep 60

# Confirm memory is back to a healthy state
FREE_GB=$(free -g | awk '/^Mem:/ {print $4}')
echo "$(ts)  free memory before CENTRAL launch: ${FREE_GB} GB" >> "$LOG"

if [ "$FREE_GB" -lt 50 ]; then
    echo "$(ts)  *** WARNING: only ${FREE_GB} GB free, sleeping 5 more min for cleanup" >> "$LOG"
    sleep 300
fi

# Launch CENTRAL no-imports solo
SCEN=R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
RUN_LOG=logs/${SCEN}_locked_v2.log
: > "$RUN_LOG"
echo "$(ts)  launching CENTRAL: $SCEN" >> "$LOG"

setsid bash -lc "
  export CLEVER_SCENARIO=$SCEN
  export MPLBACKEND=Agg
  cd /diskdata/cired/brigode/clever-work
  nice -n 10 mamba run -n EOLES_POMMES python -u scripts/run_adequacy.py > $RUN_LOG 2>&1
" < /dev/null > /dev/null 2>&1 &
CENTRAL_PID=$!
echo "$(ts)  CENTRAL launched (watcher PID $CENTRAL_PID)" >> "$LOG"

# Wait for CENTRAL to also finish
sleep 30
while [ "$(count_runs)" -gt 0 ]; do
    sleep 60
done

echo "$(ts)  CENTRAL also finished" >> "$LOG"

# Write sentinel
mkdir -p results/diagnostics
{
    echo "sequential_chain_completed_at: $(ts)"
    echo "scenarios:"
    echo "  - R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT"
    echo "  - R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central"
} > results/diagnostics/SEQUENTIAL_CHAIN_DONE

echo "$(ts)  ALL DONE — sentinel written" >> "$LOG"
