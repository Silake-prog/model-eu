#!/usr/bin/env bash
# Scheduled launcher (run via `at`) for the corrected biomethane-fix 12-cell matrix sweep.
# - Guards against starting a 2nd concurrent run_adequacy solver (one-at-a-time rule).
# - Then runs the resume-safe sweep wrapper, which uses the FIXED clever/ code
#   (_BIOMASS_TO_BIOMETHANE_EFF applied) and runs all 12 cells serially.
# Created 2026-06-01 for a 2026-06-02 07:00 CEST launch.
set -u
cd /diskdata/cired/brigode/clever-work || exit 1
LOG="logs/sweep_at_launch_$(date +%F_%H%M).log"

# A real solver is a *python* process running run_adequacy.py (bash watcher/monitor
# scripts that merely mention it are excluded by the comm=python test).
if ps -eo comm,args | awk 'tolower($1) ~ /^python/ && /run_adequacy\.py/ {f=1} END{exit !f}'; then
    echo "[$(date -Is)] ABORT: a run_adequacy solver is already running — matrix NOT launched (one-at-a-time)." >> "$LOG"
    exit 0
fi

echo "[$(date -Is)] Launching corrected matrix sweep (biomethane efficiency fix)." >> "$LOG"
bash scripts/run_sensitivity_sweep.sh >> "$LOG" 2>&1
echo "[$(date -Is)] Wrapper exited rc=$?." >> "$LOG"
