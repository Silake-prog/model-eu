#!/bin/bash
#SBATCH --mem=500G                 # 3x year_op ~ 3x a single solve's ~100GB peak
#SBATCH --cpus-per-task=32
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --job-name=ens_merge
#SBATCH --mail-type=END
#SBATCH --mail-user=brigodesimon@gmail.com
#SBATCH --output=slurm_%j.out
# Usage: sbatch --export=ALL,ENS_BASE=<base scenario w/o _wy>,ENS_TAG=<tag> \
#               scripts/sbatch_ensemble_merge.sh
# NB: do NOT pass ENS_YEARS via --export (SLURM --export is comma-delimited and
# truncates "2017,2018,2024" to "2017" — silent single-year bug, 2026-07-10).
# The default below is set INSIDE the script, comma-safe.
ROOT=/home/aloret/DATA/brigode/clever-work
PY=/home/aloret/.conda/envs/EOLES_POMMES/bin/python
export CLEVER_WORK_ROOT=$ROOT MPLBACKEND=Agg GRB_LICENSE_FILE=/home/aloret/gurobi.lic
export PYTHONPATH="$ROOT/supplyforge:$ROOT:$ROOT/demandforge"
export CLEVER_GRB_THREADS=${SLURM_CPUS_PER_TASK:-32}
export CLEVER_DIAG_ROOT=$ROOT/results/diagnostics
export CLEVER_ENS_BASE="$ENS_BASE"
export CLEVER_ENS_YEARS="${ENS_YEARS:-2017,2018,2024}"
export CLEVER_ENS_TAG="${ENS_TAG:-ens}"
export CLEVER_ENS_OUT=$ROOT/results_ensemble/${ENS_TAG}
cd $ROOT && mkdir -p logs results_ensemble/${ENS_TAG}
# run_ensemble.py REUSES clever.runner's proven pipeline (solver options, IIS
# diagnostics) and validates the merged multi-year_op dataset with pommes
# check_inputs + schema-conform. Replaces the old merge_year_ops.py.
srun $PY -u scripts/run_ensemble.py > logs/ens_${ENS_TAG}.log 2>&1
