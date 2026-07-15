#!/bin/bash
#SBATCH --mem=110G
#SBATCH --cpus-per-task=8
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --job-name=ens_builds
#SBATCH --mail-type=END
#SBATCH --mail-user=brigodesimon@gmail.com
#SBATCH --output=slurm_%j.out
# One SLURM job that builds all 12 ensemble inputs SERIALLY (build-only).
# Robust detachment (unlike nohup-over-nested-ssh). ~45 min.
srun bash /home/aloret/DATA/brigode/clever-work/scripts/launch_builds_serial.sh
