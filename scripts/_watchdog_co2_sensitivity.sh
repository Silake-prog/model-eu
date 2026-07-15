#!/bin/bash
# scripts/_watchdog_co2_sensitivity.sh
#
# Launches 3 CO2-sensitivity scenarios as slots open in the global
# parallel-2 pool. Counts ALL run_adequacy.py procs so total never > 2.

cd /diskdata/cired/brigode/clever-work || exit 1

NEW_SCENARIOS=(
    R0_v1_nuke_bioLow_co2250_nofloor
    policy_nuke_bioMed_atr_co2250_nofloor
    policy_nuke_bioMed_h2HIGH_atr_co2250_nofloor
)
TARGET_PARALLEL=2

WATCHDOG_LOG=logs/co2_watchdog.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "$(ts)  watchdog: PID=$$ starting" >> "$WATCHDOG_LOG"
echo "$(ts)  watchdog: PATH=$PATH" >> "$WATCHDOG_LOG"
echo "$(ts)  watchdog: which python = $(which python 2>&1)" >> "$WATCHDOG_LOG"
echo "$(ts)  watchdog: which jupyter = $(which jupyter 2>&1)" >> "$WATCHDOG_LOG"
echo "$(ts)  watchdog: queue = ${NEW_SCENARIOS[*]}" >> "$WATCHDOG_LOG"

export CLEVER_WORK_ROOT=/diskdata/cired/brigode/clever-work
export MPLBACKEND=Agg

# Re-run nbconvert + display-→-print to ensure latest notebook is the script
echo "$(ts)  watchdog: re-running nbconvert" >> "$WATCHDOG_LOG"
jupyter nbconvert --to script notebooks/adequacy_clean.ipynb \
    --output ../scripts/run_adequacy >> "$WATCHDOG_LOG" 2>&1
sed -i 's/^display(/print(/g; s/ display(/ print(/g' scripts/run_adequacy.py
echo "$(ts)  watchdog: scripts/run_adequacy.py size = $(stat -c %s scripts/run_adequacy.py 2>/dev/null) bytes" >> "$WATCHDOG_LOG"

declare -A LAUNCHED_PIDS=()

for scen in "${NEW_SCENARIOS[@]}"; do
    echo "$(ts)  watchdog: queuing $scen" >> "$WATCHDOG_LOG"

    # Wait until a slot is free
    while true; do
        n=$(pgrep -f "python -u scripts/run_adequacy.py" 2>/dev/null | wc -l)
        if [ "$n" -lt "$TARGET_PARALLEL" ]; then
            echo "$(ts)  watchdog: slot free (n=$n < $TARGET_PARALLEL), launching $scen" >> "$WATCHDOG_LOG"
            break
        fi
        sleep 60
    done

    LOG="logs/${scen}.log"
    : > "$LOG"
    export CLEVER_SCENARIO="$scen"
    nice -n 10 python -u scripts/run_adequacy.py > "$LOG" 2>&1 &
    pid=$!
    LAUNCHED_PIDS[$pid]=$scen
    echo "$(ts)  watchdog: >>> $scen launched (PID $pid)" >> "$WATCHDOG_LOG"
    sleep 30
done

echo "$(ts)  watchdog: all ${#NEW_SCENARIOS[@]} scenarios spawned, waiting..." >> "$WATCHDOG_LOG"
for pid in "${!LAUNCHED_PIDS[@]}"; do
    wait $pid 2>/dev/null
    rc=$?
    echo "$(ts)  watchdog: <<< ${LAUNCHED_PIDS[$pid]} finished (PID $pid, rc=$rc)" >> "$WATCHDOG_LOG"
done

mkdir -p results/diagnostics
{
    echo "co2_sensitivity_completed_at: $(ts)"
    echo "scenarios:"
    for s in "${NEW_SCENARIOS[@]}"; do echo "  - $s"; done
} > results/diagnostics/CO2_SENSITIVITY_DONE
echo "$(ts)  watchdog: ALL DONE — sentinel written" >> "$WATCHDOG_LOG"
