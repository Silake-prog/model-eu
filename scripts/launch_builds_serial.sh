#!/bin/bash
# SERIAL build-only launcher for the weather-ensemble inputs.
# The shared DEMAND_DIR/hourly_electricity_demand.csv is OVERWRITTEN by every build
# (clever/demand.py), so builds MUST run one at a time — concurrent writes corrupt
# it (interleaved rows) and cross-contaminate weather years. Run as a single SLURM
# job (scripts/sbatch_serial_builds.sh) for robust detachment.
set -u
AW=/home/aloret/DATA/brigode/clever-work
PY=/home/aloret/.conda/envs/EOLES_POMMES/bin/python
export CLEVER_WORK_ROOT=$AW MPLBACKEND=Agg GRB_LICENSE_FILE=/home/aloret/gurobi.lic
export PYTHONPATH="$AW/supplyforge:$AW:$AW/demandforge"
export CLEVER_GRB_THREADS=${SLURM_CPUS_PER_TASK:-8} CLEVER_BUILD_ONLY=1
cd $AW
# discard any corrupted shared demand CSV from a prior concurrent run; the first
# build rewrites it cleanly anyway.
rm -f $AW/results/hourly_demand/hourly_electricity_demand.csv 2>/dev/null

RefeH=R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_pipekm1000
GFeH=R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000
NoNucH=R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000
FlexeH=R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_pipekm1000
ImpeH=R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim_pipekm1000
# Config list can be overridden via CLEVER_ENS_CONFIGS (space-separated base names).
CONFIGS="${CLEVER_ENS_CONFIGS:-$GFeH $RefeH $NoNucH $FlexeH}"
for base in $CONFIGS; do
  for Y in 2017 2018 2024; do
    SCEN=${base}_wy${Y}
    mkdir -p results/diagnostics/$SCEN logs
    echo "[$(date +%H:%M:%S)] BUILD $SCEN"
    rm -f results/diagnostics/$SCEN/input_dataset_2050.nc
    CLEVER_SCENARIO=$SCEN $PY -u scripts/run_adequacy.py > logs/build_${SCEN}.log 2>&1
    if [ -f results/diagnostics/$SCEN/input_dataset_2050.nc ]; then
      echo "[$(date +%H:%M:%S)]   OK -> input_dataset dumped"
    else
      echo "[$(date +%H:%M:%S)]   FAIL — tail:"; tail -4 logs/build_${SCEN}.log
    fi
  done
done
echo "ALL SERIAL BUILDS DONE at $(date +%H:%M:%S)"
