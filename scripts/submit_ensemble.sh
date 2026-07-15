#!/bin/bash
# ERAA-style multi-year_op weather ensemble (Joule Issue 3).
# For each flagship config: 3 build-only jobs (one per weather year, CLEVER_BUILD_ONLY=1)
# then a dependent merge+solve of the shared-fleet ensemble (year_op-weighted, discount 1/N).
# Run ON Aloret (jupyter2): bash scripts/submit_ensemble.sh [tag1 tag2 ...]
set -u
cd "$(dirname "$0")/.."
YEARS="2017 2018 2024"
EY="2017,2018,2024"

RefeH="R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_pipekm1000"
GFeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
NoNucH="R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000"
FlexeH="R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_pipekm1000"
declare -A CFG=( [RefeH]="$RefeH" [GFeH]="$GFeH" [NoNucH]="$NoNucH" [FlexeH]="$FlexeH" )

TAGS="${*:-RefeH GFeH NoNucH FlexeH}"
for tag in $TAGS; do
  base="${CFG[$tag]}"
  if [ -z "$base" ]; then echo "unknown tag $tag"; continue; fi
  dep=""
  for Y in $YEARS; do
    jid=$(sbatch --parsable --job-name="b_${tag}_${Y}" --mem=90G --cpus-per-task=8 \
          --export=ALL,SCEN="${base}_wy${Y}",CLEVER_BUILD_ONLY=1 scripts/sbatch_clever.sh)
    echo "  build  $tag $Y -> job $jid"
    dep="${dep}:${jid}"
  done
  mjid=$(sbatch --parsable --dependency=afterok${dep} --job-name="ens_${tag}" \
         --export=ALL,ENS_BASE="$base",ENS_YEARS="$EY",ENS_TAG="${tag}_ENS" \
         scripts/sbatch_ensemble_merge.sh)
  echo "MERGE  $tag -> job $mjid (after${dep})"
done
