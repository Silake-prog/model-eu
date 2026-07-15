#!/bin/bash
# Watchdog: launches DECARB_HIGH_vreEXT when a parallel-2 slot frees up
# (i.e. when DECARB_LOW or DECARB_CENTRAL finishes).
cd /diskdata/cired/brigode/clever-work || exit 1

SCEN=R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT
LOG=logs/decarb_high_v2_watchdog.log
RUN_LOG=logs/decarb_high_v2.log
mkdir -p logs

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "$(ts)  watchdog PID=$$ starting; target = ${SCEN}" >> "$LOG"
echo "$(ts)  waiting for a parallel-2 slot..." >> "$LOG"

# Wait until at most 1 run_adequacy.py is running
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

: > "$RUN_LOG"
echo "$(ts)  >>> launching ${SCEN}" >> "$LOG"
nice -n 10 mamba run -n EOLES_POMMES python -u scripts/run_adequacy.py > "$RUN_LOG" 2>&1
rc=$?
echo "$(ts)  <<< ${SCEN} finished (rc=$rc)" >> "$LOG"

mkdir -p results/diagnostics
{
    echo "decarb_high_v2_completed_at: $(ts)"
    echo "exit_code: $rc"
    echo "scenario: $SCEN"
} > results/diagnostics/DECARB_HIGH_V2_DONE
