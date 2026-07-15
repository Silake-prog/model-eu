#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Sensitivity sweep wrapper — 12-cell elec x H2 x biomethane matrix (2026-06-01).
#
# DESIGN INVARIANTS (do not violate when editing — see HANDOFF_MENA_BLUE_H2.md §8.2):
#   1. The LAUNCH backgrounds this wrapper, not the scenarios inside it.
#      The wrapper itself runs scenarios sequentially in its own foreground.
#   2. Scenarios run one at a time. Inari OOMs at 3-way parallel
#      (each Pyomo+Gurobi run holds 40-50 GB resident).
#   3. Resume-safe: each scenario skips if its export CSV already exists.
#      No state file; no flags. Re-running picks up only the missing scenarios.
#   4. SIGTERM/SIGINT trap propagates to the live solver child (TERM then KILL).
#   5. `python -u` so tail -f shows real progress; operator never feels the
#      urge to poll harder.
#   6. No `set -e` — one failed scenario must NOT abort the whole sweep.
#      It's logged as FAIL and the loop continues.
#
# LAUNCH (from any shell, including a Claude Code turn — returns immediately):
#   ssh inari 'cd /diskdata/cired/brigode/clever-work && \
#     nohup ./scripts/run_sensitivity_sweep.sh > /dev/null 2>&1 & disown; \
#     echo "wrapper PID=$!"'
#
# MONITOR (from a SEPARATE SSH session — NEVER from the launching turn):
#   ps -fp $WRAPPER_PID
#   tail -n 50 logs/sweep_master_*.log
#   grep -c "DONE" logs/sweep_master_*.log
#
# KILL (separate SSH session):
#   clean:     pkill -TERM -P $WRAPPER_PID && kill $WRAPPER_PID
#   emergency: pkill -9 -f run_adequacy.py && kill -9 $WRAPPER_PID
#
# To resume after any kill: re-run the launch one-liner. SKIP guard handles it.
# ----------------------------------------------------------------------------

set -u  # catch unset vars; do NOT set -e (one bad scenario must not abort the sweep)

# --- env -------------------------------------------------------------------
source /data/software/mambaforge/mambaforge/etc/profile.d/conda.sh
conda activate EOLES_POMMES
export MPLBACKEND=Agg

# --- config ----------------------------------------------------------------
# Sensitivity MATRIX (2026-06-01): 12-cell factorial over
#   elec        {LOW = no elecX (x1.0), HIGH = elecX180}
#   x H2        {LOW = no token (low_h2 bundle), HIGH = h2HIGH}
#   x biomethane{bioLow, bioMed, bioHigh}.
# Background = "freest": nuke + gas(carbon-taxed flex-CCGT) + atr + corr2x +
# vreEXT, floors lifted (nofloor, noElecFloor), el700, CO2 = 150 (co2150),
# batteries pinned 2.0x uniformly (batt20) so the H2 axis does not drag battery
# headroom, imports OFF (no menaOptim -> EU30-only).
# CORRECTED matrix (2026-06-11): same 12-cell factorial, now under
#   (1) electrolysis 1.43 MWh_e/MWh_H2 (was 1.85 — 2050 recalibration),
#   (2) CH4-leak pricing default-on (ng 2.5% / bio 1.0%, GWP100 × CO2 price),
#   (3) _ccsCap5 = 5 GW_H2/country cap on ATR+SMR-CCS (CO2-injection realism).
SUFFIXES=(
    "R0_v1_nuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"                   # elecLOW  h2LOW  bioLow
    "R0_v1_nuke_bioMed_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"                   # elecLOW  h2LOW  bioMed
    "R0_v1_nuke_bioHigh_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"                  # elecLOW  h2LOW  bioHigh
    "R0_v1_nuke_bioLow_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"            # elecLOW  h2HIGH bioLow
    "R0_v1_nuke_bioMed_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"            # elecLOW  h2HIGH bioMed
    "R0_v1_nuke_bioHigh_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"           # elecLOW  h2HIGH bioHigh
    "R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"          # elecHIGH h2LOW  bioLow
    "R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"          # elecHIGH h2LOW  bioMed
    "R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"         # elecHIGH h2LOW  bioHigh
    "R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"   # elecHIGH h2HIGH bioLow
    "R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"   # elecHIGH h2HIGH bioMed
    "R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20"  # elecHIGH h2HIGH bioHigh
    # ── noGas variant (4 cells, bioMed): no fossil methane at all — Gas/OCGT
    # banned, NG import + ATR/SMR-CCS deactivated; H2 = electrolysis +
    # ATR_biomethane only. `_noGas` placed AFTER `_corr2x` on purpose: the
    # ADJACENT `_noGas_corr2x` substring would trigger DECARB mode (different
    # VRE/battery tiers) and break ceteris-paribus vs the gas cells. No
    # `_ccsCap5` (CCS techs don't exist here). Smaller LP (no net_import
    # module) -> faster solves, lower OOM risk.
    "R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_vreEXT_nofloor_noElecFloor_co2150_batt20"                     # noGas elecLOW  h2LOW
    "R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20"              # noGas elecLOW  h2HIGH
    "R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_elecX180_vreEXT_nofloor_noElecFloor_co2150_batt20"            # noGas elecHIGH h2LOW
    "R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20"     # noGas elecHIGH h2HIGH
)

MASTER_LOG="logs/sweep_master_$(date +%F_%H%M).log"
mkdir -p logs results/export

# --- signal handling -------------------------------------------------------
CHILD_PID=""
cleanup() {
    echo "[$(date -Is)] SIGNAL received, killing child PID=${CHILD_PID:-none}" >> "$MASTER_LOG"
    if [[ -n "$CHILD_PID" ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
        kill -TERM "$CHILD_PID" 2>/dev/null || true
        sleep 5
        kill -0 "$CHILD_PID" 2>/dev/null && kill -KILL "$CHILD_PID" 2>/dev/null || true
    fi
    echo "[$(date -Is)] SWEEP ABORTED pid=$$ (signal trap)" >> "$MASTER_LOG"
    exit 130
}
trap cleanup TERM INT

# --- main loop -------------------------------------------------------------
echo "[$(date -Is)] SWEEP START pid=$$ scenarios=${#SUFFIXES[@]} master_log=$MASTER_LOG" >> "$MASTER_LOG"

for SUFFIX in "${SUFFIXES[@]}"; do
    OUT="results/export/${SUFFIX}/adequacy_dashboard_noramp_flex0pct.csv"
    if [[ -f "$OUT" ]]; then
        echo "[$(date -Is)] SKIP  $SUFFIX (output exists: $OUT)" >> "$MASTER_LOG"
        continue
    fi

    echo "[$(date -Is)] START $SUFFIX" >> "$MASTER_LOG"

    # Launch the python in the background so we can capture its PID for the trap,
    # then wait on it — this gives us a clean kill handle without leaving the
    # scenario itself running in the background of the wrapper.
    # CLEVER_SCENARIO env var is THE scenario channel (constants.py parses it at
    # import). run_adequacy.py:234 reads os.environ["CLEVER_SCENARIO"] and IGNORES
    # argv — passing "$SUFFIX" as a positional arg silently ran the default
    # policy_re every time (fixed 2026-06-03).
    CLEVER_SCENARIO="$SUFFIX" nice -n 10 python -u scripts/run_adequacy.py > "logs/${SUFFIX}.log" 2>&1 &
    CHILD_PID=$!
    wait "$CHILD_PID"
    RC=$?
    CHILD_PID=""

    OBJ=$(grep -m1 "Optimal objective" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 200)
    ENS=$(grep -m1 -iE "ENS|energy.not.served" "logs/${SUFFIX}.log" 2>/dev/null | tr -s ' ' | head -c 200)
    if [[ $RC -eq 0 ]]; then
        echo "[$(date -Is)] DONE  $SUFFIX rc=0 | ${OBJ:-<no obj>} | ${ENS:-<no ens>}" >> "$MASTER_LOG"
    else
        echo "[$(date -Is)] FAIL  $SUFFIX rc=$RC (see logs/${SUFFIX}.log) | ${OBJ:-} | ${ENS:-}" >> "$MASTER_LOG"
    fi
done

echo "[$(date -Is)] SWEEP END pid=$$" >> "$MASTER_LOG"
