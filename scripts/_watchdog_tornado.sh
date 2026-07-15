#!/bin/bash
# Watchdog: launches the 10 tornado-sensitivity scenarios in parallel-2,
# after all run_adequacy.py processes (the 3 baselines) have finished.
cd /diskdata/cired/brigode/clever-work || exit 1

SENSITIVITIES=(
    R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioOff_el700_noGas_corr2x_elecX125_h2central
    R0_v1_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_corr2x_elecX125_h2central_co2300
    R0_v1_nuke_bioMed_atr_el700_noGas_corr3x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_vreEXT
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2010
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2018
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX150_h2central
)

TARGET_PARALLEL=2
LOG=logs/tornado_watchdog.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Robust count of actual python run_adequacy procs (not bash/mamba wrappers).
# Use pgrep -ax to match argv[0]="python" exactly, then grep the script name.
count_runs() {
    pgrep -af '^python -u scripts/run_adequacy.py$' 2>/dev/null | wc -l
}

echo "$(ts)  watchdog PID=$$ starting (target=parallel-${TARGET_PARALLEL})" >> "$LOG"
echo "$(ts)  ${#SENSITIVITIES[@]} sensitivities queued" >> "$LOG"

# ── Phase 1: wait until all baselines have exited ─────────────────────────
echo "$(ts)  PHASE 1: waiting for baselines to clear (count_runs → 0)..." >> "$LOG"
while true; do
    n=$(count_runs)
    if [ "$n" -eq 0 ]; then
        echo "$(ts)  PHASE 1 done — no run_adequacy procs alive" >> "$LOG"
        break
    fi
    sleep 60
done

# ── Phase 2: launch sensitivities, parallel-2 ─────────────────────────────
echo "$(ts)  PHASE 2: launching sensitivities in parallel-${TARGET_PARALLEL}" >> "$LOG"

export CLEVER_WORK_ROOT=/diskdata/cired/brigode/clever-work
export MPLBACKEND=Agg

for scen in "${SENSITIVITIES[@]}"; do
    echo "$(ts)  queueing ${scen}" >> "$LOG"

    # Wait for a free slot (n < TARGET_PARALLEL)
    while true; do
        n=$(count_runs)
        if [ "$n" -lt "$TARGET_PARALLEL" ]; then
            echo "$(ts)    slot free (n=$n), launching ${scen}" >> "$LOG"
            break
        fi
        sleep 60
    done

    LOG_RUN="logs/${scen}.log"
    : > "$LOG_RUN"
    export CLEVER_SCENARIO="$scen"

    nice -n 10 mamba run -n EOLES_POMMES python -u scripts/run_adequacy.py > "$LOG_RUN" 2>&1 &
    pid=$!
    echo "$(ts)    >>> ${scen} launched (PID ${pid})" >> "$LOG"
    sleep 30   # let the process settle so count_runs sees it
done

echo "$(ts)  all sensitivities spawned; waiting for the last ones to finish..." >> "$LOG"
while true; do
    n=$(count_runs)
    if [ "$n" -eq 0 ]; then
        break
    fi
    sleep 60
done

mkdir -p results/diagnostics
{
    echo "tornado_completed_at: $(ts)"
    echo "scenarios:"
    for s in "${SENSITIVITIES[@]}"; do echo "  - $s"; done
} > results/diagnostics/TORNADO_DONE
echo "$(ts)  ALL DONE — sentinel written" >> "$LOG"
