#!/bin/bash
# Re-launch the 3 failed _menaOptim scenarios after fixing the
# h2_storage-collision bug in run_adequacy.py (MENA areas were being
# included in the EU add_hydrogen loop).
#
# The 3 no-imports runs already completed successfully in the prior batch
# and are NOT re-launched here.
cd /diskdata/cired/brigode/clever-work || exit 1

QUEUE=(
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_menaOptim
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_menaOptim
    R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaOptim
)

TARGET_PARALLEL=2
LOG=logs/phase17_menaOptim_only_watchdog.log
mkdir -p logs results/diagnostics

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

count_runs() {
    pgrep -af '^python -u scripts/run_adequacy.py$' 2>/dev/null | wc -l
}

echo "$(ts)  menaOptim-only re-launch watchdog PID=$$ starting (parallel-${TARGET_PARALLEL})" >> "$LOG"
echo "$(ts)  ${#QUEUE[@]} scenarios queued (3 menaOptim variants)" >> "$LOG"

export CLEVER_WORK_ROOT=/diskdata/cired/brigode/clever-work
export MPLBACKEND=Agg

idx=0
for scen in "${QUEUE[@]}"; do
    idx=$((idx + 1))
    echo "$(ts)  [${idx}/${#QUEUE[@]}] queueing ${scen}" >> "$LOG"
    while true; do
        n=$(count_runs)
        if [ "$n" -lt "$TARGET_PARALLEL" ]; then
            echo "$(ts)    slot free (n=$n), launching ${scen}" >> "$LOG"
            break
        fi
        sleep 60
    done

    LOG_RUN="logs/${scen}_phase17.log"
    : > "$LOG_RUN"
    export CLEVER_SCENARIO="$scen"

    nice -n 10 mamba run -n EOLES_POMMES python -u scripts/run_adequacy.py > "$LOG_RUN" 2>&1 &
    pid=$!
    echo "$(ts)    >>> [${idx}] ${scen} launched (PID ${pid})" >> "$LOG"
    sleep 30
done

echo "$(ts)  all ${#QUEUE[@]} scenarios spawned; waiting for last ones to finish..." >> "$LOG"
while [ $(count_runs) -gt 0 ]; do
    sleep 60
done

{
    echo "phase17_menaOptim_only_completed_at: $(ts)"
    echo "scenarios:"
    for s in "${QUEUE[@]}"; do echo "  - $s"; done
} > results/diagnostics/PHASE17_MENA_ONLY_DONE
echo "$(ts)  ALL DONE — sentinel written" >> "$LOG"
