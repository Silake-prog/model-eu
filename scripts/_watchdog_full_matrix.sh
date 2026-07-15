#!/bin/bash
# Master watchdog: launches all 21 scenarios in parallel-2, priority order.
# - Phase 1: 3 baselines (most important, run first)
# - Phase 2: 12 priority sensitivities (electrolyser CAPEX × weather year × 3 baselines)
# - Phase 3: 6 CENTRAL-only tornado completion
cd /diskdata/cired/brigode/clever-work || exit 1

QUEUE=(
    # ── Phase 1: 3 baselines ──────────────────────────────────────────────
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x
    R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT
    # ── Phase 2: electrolyser CAPEX + weather year on each baseline ───────
    R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2010
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2018
    R0_v1_nuke_bioLow_atr_el500_noGas_corr2x
    R0_v1_nuke_bioLow_atr_el900_noGas_corr2x
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2010
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2018
    R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX180_h2HIGH_vreEXT
    R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX180_h2HIGH_vreEXT
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2010
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2018
    # ── Phase 3: CENTRAL tornado completion ───────────────────────────────
    R0_v1_nuke_bioOff_el700_noGas_corr2x_elecX125_h2central
    R0_v1_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_corr2x_elecX125_h2central_co2300
    R0_v1_nuke_bioMed_atr_el700_noGas_corr3x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_vreEXT
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX150_h2central
)

TARGET_PARALLEL=2
LOG=logs/full_matrix_watchdog.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Robust count: only the actual python process, not bash/mamba wrappers
count_runs() {
    pgrep -af '^python -u scripts/run_adequacy.py$' 2>/dev/null | wc -l
}

echo "$(ts)  master watchdog PID=$$ starting (target=parallel-${TARGET_PARALLEL})" >> "$LOG"
echo "$(ts)  ${#QUEUE[@]} scenarios queued" >> "$LOG"

export CLEVER_WORK_ROOT=/diskdata/cired/brigode/clever-work
export MPLBACKEND=Agg

idx=0
for scen in "${QUEUE[@]}"; do
    idx=$((idx + 1))
    echo "$(ts)  [${idx}/${#QUEUE[@]}] queueing ${scen}" >> "$LOG"

    # Wait for a free slot
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
    echo "$(ts)    >>> [${idx}] ${scen} launched (PID ${pid})" >> "$LOG"
    sleep 30
done

echo "$(ts)  all ${#QUEUE[@]} scenarios spawned; waiting for last ones to finish..." >> "$LOG"
while [ $(count_runs) -gt 0 ]; do
    sleep 60
done

mkdir -p results/diagnostics
{
    echo "full_matrix_completed_at: $(ts)"
    echo "scenarios:"
    for s in "${QUEUE[@]}"; do echo "  - $s"; done
} > results/diagnostics/FULL_MATRIX_DONE
echo "$(ts)  ALL DONE — sentinel written" >> "$LOG"
