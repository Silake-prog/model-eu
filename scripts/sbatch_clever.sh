#!/bin/bash
#SBATCH --mem=120G                 # our solves peak 70-100 GB (2G would OOM instantly)
#SBATCH --cpus-per-task=16         # must match CLEVER_GRB_THREADS below
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --job-name=clever_${SCEN:-run}
#SBATCH --mail-type=END
#SBATCH --mail-user=brigodesimon@gmail.com
#SBATCH --output=slurm_%j.out
# Usage:  sbatch --export=ALL,SCEN=<full_scenario_name> scripts/sbatch_clever.sh
ROOT=/home/aloret/DATA/brigode/clever-work
PY=/home/aloret/.conda/envs/EOLES_POMMES/bin/python   # direct path (no mamba activate needed)
export CLEVER_WORK_ROOT=$ROOT MPLBACKEND=Agg GRB_LICENSE_FILE=/home/aloret/gurobi.lic
export PYTHONPATH="$ROOT:$ROOT/demandforge"
export CLEVER_SCENARIO=$SCEN CLEVER_GRB_THREADS=${SLURM_CPUS_PER_TASK:-16}
cd $ROOT && mkdir -p logs results/diagnostics/$SCEN results/export/$SCEN results/figures/$SCEN
srun $PY -u scripts/run_adequacy.py > logs/$SCEN.log 2>&1
