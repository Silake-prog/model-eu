#!/bin/bash
# Phase-2 extraction chain (el no-floor pivot) — run detached on Aloret.
cd /home/aloret/DATA/brigode/clever-work || exit 1
export CLEVER_WORK_ROOT=$PWD CLEVER_SCEN_SUFFIX=_pipekm1000
export CLEVER_DIAG=$PWD/results/diagnostics CLEVER_MASTER_OUT=$PWD/viz_data/investigation_master.csv
PY=$HOME/.conda/envs/EOLES_POMMES/bin/python
{
  echo "=== EXTRACTION START $(date) ==="
  echo "--- 76509 vF_RefLL optimality ---"
  grep -E "Optimal objective|Barrier solved|Suboptimal|infeasible" \
    logs/R0_v1_nuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_vreFree_pipekm1000.log | tail -2
  echo "--- investigate_master (28 rows; noPipe_L may be MISSING until 76512) ---"
  $PY analysis/investigate_master.py
  echo "--- price_gas_audit ---"
  $PY analysis/_price_gas_audit.py $PWD
  echo "--- make_paper_figs ---"
  $PY analysis/make_paper_figs.py
  echo "--- paper_numbers harvest ---"
  $PY analysis/_paper_numbers.py
  echo "=== EXTRACTION DONE $(date) ==="
} > extraction_run.log 2>&1
