#!/bin/bash
# Phase 1.7 batch — 6 scenarios in 3 paired waves (parallel-2 per wave):
#   Wave 1: LOW       (no imports)  +  LOW       (with menaOptim imports)
#   Wave 2: CENTRAL   (no imports)  +  CENTRAL   (with menaOptim imports)
#   Wave 3: HIGH      (no imports)  +  HIGH      (with menaOptim imports)
#
# Changes from prior run:
#   - Phase 1.7: AD plant unbundled from BioCCGT/ATR (shared CAPEX)
#   - VRE downside band -25% → -30% on DECARB CENTRAL + HIGH
#   - LOW unchanged at -50% downside
#
# Logs go to logs/<scenario>_phase17.log
# Sentinel written when all 6 complete: results/diagnostics/PHASE17_6RUNS_DONE
cd /diskdata/cired/brigode/clever-work || exit 1

QUEUE=(
    # ── Wave 1: LOW pair ───────────────────────────────────────────────
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x
    R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_menaOptim
    # ── Wave 2: CENTRAL pair ───────────────────────────────────────────
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
    R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_menaOptim
    # ── Wave 3: HIGH pair ──────────────────────────────────────────────
    R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT
    R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaOptim
)

TARGET_PARALLEL=2
LOG=logs/phase17_6runs_watchdog.log
mkdir -p logs results/diagnostics

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Robust count: only the actual python process (not bash/mamba wrappers)
count_runs() {
    pgrep -af '^python -u scripts/run_adequacy.py$' 2>/dev/null | wc -l
}

echo "$(ts)  Phase 1.7 watchdog PID=$$ starting (target=parallel-${TARGET_PARALLEL})" >> "$LOG"
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
    echo "phase17_6runs_completed_at: $(ts)"
    echo "scenarios:"
    for s in "${QUEUE[@]}"; do echo "  - $s"; done
} > results/diagnostics/PHASE17_6RUNS_DONE
echo "$(ts)  ALL DONE — sentinel written" >> "$LOG"
