#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Demand-side load-shedding (effacement / VoLL) sensitivity — gas-free eHI
# (2026-06-17, Lucie/Quentin). One-variable overlays on the gas-free elecHIGH
# baselines. The gas-free H2-CCGT peaker is marginal at ~1300 EUR/MWh, H2 ~500.
#   _voll900   electricity VoLL BELOW the peaker  -> effacement REMUNERATION
#              (LP prefers paying 900 to shed over firing the 1300 peaker)
#   _voll3000  electricity VoLL ABOVE the peaker  -> scarcity / blackout cap
#   _h2voll400 hydrogen VoLL BELOW the H2 marginal -> industrial-H2 effacement
#              (storable -> frees H2/electrolysis power for the elec balance,
#               dodges the 24% power-round-trip wall)
#   _h2voll2000 hydrogen VoLL backstop below the 10 000 default
# Each cell is identical to its headroom baseline except the VoLL token, so it
# is a clean A/B vs the already-decomposed B1/B2 elecHIGH cells.
#
# VERIFICATION: the override fires PRE-BUILD, so within minutes of a cell
# starting its log prints e.g. "VoLL override: electricity load_shedding_cost
# = 900 EUR/MWh (_vollNNN)" — that confirms the wiring before the long solve.
#
# QUEUE BEHAVIOUR: this wrapper BLOCKS until no `run_adequacy` python is alive,
# so it can be launched now and will slot in right after B3.eHI frees the box
# (inari is strictly one-run-at-a-time; parallel solves OOM the server).
#
# DESIGN INVARIANTS (mirror scripts/run_headroom_variants.sh):
#   - serial; resume-safe (skips a cell whose export CSV exists);
#   - SIGTERM/SIGINT trap propagates to the live solver child;
#   - CLEVER_AREAS UNSET -> full EU30 (no MENA; these are EU30-only).
#
# LAUNCH (returns immediately; sits waiting until the box frees):
#   ssh inari 'cd /diskdata/cired/brigode/clever-work && \
#     nohup ./scripts/run_voll_sensitivity.sh > /dev/null 2>&1 & disown; \
#     echo "wrapper PID=$!"'
# MONITOR (separate session): tail -n 50 logs/sweep_voll_*.log
# KILL: pkill -TERM -P $WRAPPER_PID && kill $WRAPPER_PID
# ----------------------------------------------------------------------------

set -u  # do NOT set -e (one bad scenario must not abort the batch)

source /data/software/mambaforge/mambaforge/etc/profile.d/conda.sh
conda activate EOLES_POMMES
export MPLBACKEND=Agg
# CLEVER_AREAS intentionally UNSET -> full EU30.

MASTER_LOG="logs/sweep_voll_$(date +%F_%H%M).log"
mkdir -p logs results/export

# --- queue: wait until the box is free (no other run_adequacy solver) --------
echo "[$(date -Is)] VOLL BATCH QUEUED pid=$$ — waiting for box to free" >> "$MASTER_LOG"
while pgrep -f 'python -u scripts/run_adequacy' > /dev/null 2>&1; do
    sleep 120
done
echo "[$(date -Is)] box free — starting VoLL batch" >> "$MASTER_LOG"

# Ordered most-informative-first.
SUFFIXES=(
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_voll3000"   # B2 scarcity cap (over peaker)
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_voll900"    # B2 elec effacement (under peaker)
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_h2voll400"  # B2 industrial-H2 effacement
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_h2voll2000" # B2 H2 backstop
    "R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900"         # B1 effacement vs nuclear
)

CHILD_PID=""
cleanup() {
    echo "[$(date -Is)] SIGNAL received, killing child PID=${CHILD_PID:-none}" >> "$MASTER_LOG"
    if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null || true
        sleep 5
        kill -0 "$CHILD_PID" 2>/dev/null && kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    echo "[$(date -Is)] BATCH ABORTED pid=$$ (signal trap)" >> "$MASTER_LOG"
    exit 130
}
trap cleanup TERM INT

echo "[$(date -Is)] BATCH START pid=$$ scenarios=${#SUFFIXES[@]} master_log=$MASTER_LOG" >> "$MASTER_LOG"

for SUFFIX in "${SUFFIXES[@]}"; do
    OUT="results/export/${SUFFIX}/adequacy_dashboard_noramp_flex0pct.csv"
    if [[ -f "$OUT" ]]; then
        echo "[$(date -Is)] SKIP  $SUFFIX (output exists: $OUT)" >> "$MASTER_LOG"
        continue
    fi
    echo "[$(date -Is)] START $SUFFIX" >> "$MASTER_LOG"
    CLEVER_SCENARIO="$SUFFIX" nice -n 10 python -u scripts/run_adequacy.py > "logs/${SUFFIX}.log" 2>&1 &
    CHILD_PID=$!
    wait "$CHILD_PID"
    RC=$?
    CHILD_PID=""
    OBJ=$(grep -m1 "Optimal objective" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 200)
    VOLL=$(grep -m1 "VoLL override" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 120)
    if [[ $RC -eq 0 ]]; then
        echo "[$(date -Is)] DONE  $SUFFIX rc=0 | ${OBJ:-<no obj>} | ${VOLL:-<no voll log>}" >> "$MASTER_LOG"
    else
        echo "[$(date -Is)] FAIL  $SUFFIX rc=$RC (see logs/${SUFFIX}.log) | ${VOLL:-}" >> "$MASTER_LOG"
    fi
done

echo "[$(date -Is)] BATCH END pid=$$" >> "$MASTER_LOG"
