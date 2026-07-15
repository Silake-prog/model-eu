#!/bin/bash
# Submit the 4 flagship ensemble MERGE+solve jobs (inputs already built via
# CLEVER_BUILD_ONLY). No afterok dependency — the per-year input_datasets exist.
# Run ON Aloret (jupyter2): bash scripts/submit_ensemble_merges.sh [tag1 tag2 ...]
set -u
cd "$(dirname "$0")/.."
EY="2017,2018,2024"
RefeH="R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_pipekm1000"
GFeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
NoNucH="R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
FlexeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_pipekm1000"
ImpeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim_pipekm1000"
declare -A CFG=( [RefeH]="$RefeH" [GFeH]="$GFeH" [NoNucH]="$NoNucH" [FlexeH]="$FlexeH" [ImpeH]="$ImpeH" )
# Default: counterfactuals first (adequacy lives here); Ref is weather-invariant/adequate.
for tag in ${*:-NoNucH GFeH FlexeH ImpeH}; do
  base="${CFG[$tag]}"; [ -z "$base" ] && { echo "unknown $tag"; continue; }
  jid=$(sbatch --parsable --job-name="ens_${tag}" \
        --export=ALL,ENS_BASE="$base",ENS_YEARS="$EY",ENS_TAG="${tag}_ENS" \
        scripts/sbatch_ensemble_merge.sh)
  echo "MERGE $tag -> job $jid"
done
