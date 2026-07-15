#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Scenario-matrix completion batch (2026-06-17) — fills the cells of
# analysis/scenario_matrix.tex that aren't already landed/running.
# Baseline (pinned): bioLow, H2 High, std VRE (vreEXT), nuclear std, el700,
# corr2x, co2150, batt20. Clean one-lever-at-a-time on the STANDARD gas-free
# build (R2). Already covered, NOT re-run here:
#   R1 (gas+nuclear) x1.0/x1.8  -> 16-cell bioLow gas cells (landed)
#   R3 (gas-free, no nuclear) x1.0/x1.8 -> headroom B1 (landed)
#   R4 (gas-free, max firm) x1.0 landed / x1.8 running (B3)
# This batch runs the 5 missing cells, EU30 cells first then MENA (heavier):
#   R2 x1.8  gas-free base (foundational — never run before)
#   R5 x1.8  + flexibility (elec VoLL 900 + H2 effacement 400)
#   R6 x1.8  + MENA H2 imports (Variant B, +4 areas)
#   R7 x1.8  + flexibility + imports
#   R2 x1.0  gas-free base, low demand (completeness; low priority)
#
# QUEUE: blocks until no run_adequacy solver is alive, so it self-queues behind
# B3.eHI (inari one-run-at-a-time). CLEVER_AREAS unset -> EU30 (+MENA for R6/R7).
# Resume-safe (skips cells whose export CSV exists); SIGTERM/INT trap.
#
# LAUNCH:  ssh inari 'cd /diskdata/cired/brigode/clever-work && \
#   nohup ./scripts/run_matrix_complete.sh > /dev/null 2>&1 & disown; echo "PID=$!"'
# MONITOR: tail -n 50 logs/sweep_matrix_*.log
# KILL:    pkill -TERM -P $WRAPPER_PID && kill $WRAPPER_PID
# ----------------------------------------------------------------------------

set -u
source /data/software/mambaforge/mambaforge/etc/profile.d/conda.sh
conda activate EOLES_POMMES
export MPLBACKEND=Agg
# CLEVER_AREAS intentionally UNSET.

MASTER_LOG="logs/sweep_matrix_$(date +%F_%H%M).log"
mkdir -p logs results/export

# NOTE: wait-guard removed 2026-06-18 — the `pgrep -f 'python -u scripts/run_adequacy'`
# guard self-stalled for 20h by matching a stray launcher shell. Launch only after
# manually confirming the box is free of OUR jobs (free -g + ps by user).
echo "[$(date -Is)] MATRIX BATCH START pid=$$ (box manually pre-confirmed free; no wait-guard)" >> "$MASTER_LOG"

SUFFIXES=(
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20"                              # R2 x1.8
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400"            # R5 +flex
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim"                    # R6 +imports
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim"  # R7 +flex+imports
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20"                                       # R2 x1.0
)

CHILD_PID=""
cleanup() {
    echo "[$(date -Is)] SIGNAL received, killing child PID=${CHILD_PID:-none}" >> "$MASTER_LOG"
    if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null || true; sleep 5
        kill -0 "$CHILD_PID" 2>/dev/null && kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    echo "[$(date -Is)] BATCH ABORTED pid=$$ (signal trap)" >> "$MASTER_LOG"; exit 130
}
trap cleanup TERM INT

echo "[$(date -Is)] BATCH START pid=$$ scenarios=${#SUFFIXES[@]} master_log=$MASTER_LOG" >> "$MASTER_LOG"
for SUFFIX in "${SUFFIXES[@]}"; do
    OUT="results/export/${SUFFIX}/adequacy_dashboard_noramp_flex0pct.csv"
    if [[ -f "$OUT" ]]; then
        echo "[$(date -Is)] SKIP  $SUFFIX (output exists)" >> "$MASTER_LOG"; continue
    fi
    echo "[$(date -Is)] START $SUFFIX" >> "$MASTER_LOG"
    CLEVER_SCENARIO="$SUFFIX" nice -n 10 python -u scripts/run_adequacy.py > "logs/${SUFFIX}.log" 2>&1 &
    CHILD_PID=$!; wait "$CHILD_PID"; RC=$?; CHILD_PID=""
    OBJ=$(grep -m1 "Optimal objective" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 200)
    VOLL=$(grep -m1 "VoLL override" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 120)
    if [[ $RC -eq 0 ]]; then
        echo "[$(date -Is)] DONE  $SUFFIX rc=0 | ${OBJ:-<no obj>} | ${VOLL:-}" >> "$MASTER_LOG"
    else
        echo "[$(date -Is)] FAIL  $SUFFIX rc=$RC (see logs/${SUFFIX}.log) | ${VOLL:-}" >> "$MASTER_LOG"
    fi
done
echo "[$(date -Is)] BATCH END pid=$$" >> "$MASTER_LOG"
