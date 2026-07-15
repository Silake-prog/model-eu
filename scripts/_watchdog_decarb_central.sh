#!/bin/bash
# Watchdog: launches DECARB_CENTRAL when a parallel-2 slot frees up
# (i.e. when DECARB_HIGH or DECARB_LOW finishes).
cd /diskdata/cired/brigode/clever-work || exit 1

SCEN=R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central
LOG=logs/decarb_central_watchdog.log
RUN_LOG=logs/decarb_central_canary.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "$(ts)  watchdog PID=$$ starting; target = ${SCEN}" >> "$LOG"
echo "$(ts)  waiting for a parallel-2 slot..." >> "$LOG"

# Wait until at most 1 run_adequacy.py is running (so launching makes it 2)
while true; do
    n=$(pgrep -f "python -u scripts/run_adequacy.py" 2>/dev/null | wc -l)
    if [ "$n" -lt 2 ]; then
        echo "$(ts)  slot free (n=$n), launching $SCEN" >> "$LOG"
        break
    fi
    sleep 60
done

export CLEVER_SCENARIO="$SCEN"
export CLEVER_WORK_ROOT=/diskdata/cired/brigode/clever-work
export MPLBACKEND=Agg

# Launch and stream into RUN_LOG (foreground here so we capture exit code)
: > "$RUN_LOG"
echo "$(ts)  >>> launching ${SCEN} (PID=$$)" >> "$LOG"
nice -n 10 mamba run -n EOLES_POMMES python -u scripts/run_adequacy.py > "$RUN_LOG" 2>&1
rc=$?
echo "$(ts)  <<< ${SCEN} finished (rc=$rc)" >> "$LOG"

# Sentinel for downstream automation
mkdir -p results/diagnostics
{
    echo "decarb_central_completed_at: $(ts)"
    echo "exit_code: $rc"
    echo "scenario: $SCEN"
} > results/diagnostics/DECARB_CENTRAL_DONE
