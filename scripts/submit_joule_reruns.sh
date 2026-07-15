#!/bin/bash
# Joule-revision reruns: multi-year weather (18) + MENA WACC (6) + storage derate (2) = 26 solves.
# Run ON Aloret (jupyter2): bash scripts/submit_joule_reruns.sh
set -u
cd "$(dirname "$0")/.."
submit() { sbatch --job-name="$1" --export=ALL,SCEN="$2" scripts/sbatch_clever.sh; }

# ---- A. multi-year weather (Issue 3): 6 headline configs x {2021,2022,2023} ----
RefeH="R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_pipekm1000"
GFeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
NoNucH="R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
GFeL="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_vreFree_pipekm1000"
FlexeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_pipekm1000"
fiStP="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim_pipekm1000_storPx1000"
for pair in "RefeH:$RefeH" "GFeH:$GFeH" "NoNucH:$NoNucH" "GFeL:$GFeL" "FlexeH:$FlexeH" "fiStP:$fiStP"; do
  tag="${pair%%:*}"; base="${pair#*:}"
  for Y in 2021 2022 2023; do submit "wy${Y}_${tag}" "${base}_wy${Y}"; done
done

# ---- B. MENA WACC (M2): Imp_blue170 & FlexImp x {8,10,12}% via _menaRisk (pp x10) ----
IMPb="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim"
FIb="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim"
for PP in 40 60 80; do
  R="_menaRiskMA_${PP}_menaRiskDZ_${PP}_menaRiskTN_${PP}_menaRiskLY_${PP}"
  submit "w${PP}_Imp" "${IMPb}${R}_pipekm1000"
  submit "w${PP}_FImp" "${FIb}${R}_pipekm1000"
done

# ---- C. storage derate (one-tailed bracket): GF_eHh2H & fi x0.5 deliverability ----
submit "stx05_GF" "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000_storPx050"
submit "stx05_fi" "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim_pipekm1000_storPx050"

echo "submitted 26 jobs"
