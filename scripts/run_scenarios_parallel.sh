#!/usr/bin/env bash
# scripts/run_scenarios_parallel.sh — parallel-N scheduler.
#
# Runs the given list of SCENARIOs with at most N (default 2) concurrent
# Python solves. When one finishes, the next from the queue starts.
# Auto-waits for any in-flight scripts/run_adequacy.py at startup so it's
# safe to launch while another single-scenario solve (e.g. policy_re_noMin)
# is still going.
#
# Usage (server-side):
#   bash scripts/run_scenarios_parallel.sh policy_re_corr2x R0_v1_corr2x ...
#
# Env overrides:
#   MAX_PARALLEL=2   default concurrency cap (2 × ~50 GB ≈ 100 GB RAM)
#
# Sentinel: results/diagnostics/PARALLEL_DONE — written on completion with
# per-scenario summary lines.

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <scenario1> [scenario2 ...]"
    exit 2
fi
SCENARIOS=("$@")
MAX_PARALLEL="${MAX_PARALLEL:-2}"

WORK=/diskdata/cired/brigode/clever-work
cd "$WORK"

# bashrc trap defeat
export BASHRCSOURCED=""
[ -f "$HOME/.bashrc" ]       && source "$HOME/.bashrc"       || true
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" || true

if ! declare -F mamba >/dev/null 2>&1; then
    for hook in "$HOME/mambaforge/etc/profile.d/mamba.sh" \
                "$HOME/miniconda3/etc/profile.d/mamba.sh" \
                "$HOME/miniconda3/etc/profile.d/conda.sh"; do
        [ -f "$hook" ] && source "$hook"
    done
fi
mamba activate EOLES_POMMES || conda activate EOLES_POMMES

if [[ "$(which python)" == "/usr/bin/python" || -z "${CONDA_DEFAULT_ENV:-}" ]]; then
    echo "FATAL: env activation didn't take. which python = $(which python)"
    exit 3
fi

export CLEVER_WORK_ROOT="$WORK"
export MPLBACKEND=Agg
mkdir -p logs

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

echo "$(ts)  =========================================================="
echo "$(ts)  === parallel-${MAX_PARALLEL} scheduler — ${#SCENARIOS[@]} scenarios ==="
echo "$(ts)  scenarios: ${SCENARIOS[*]}"
echo "$(ts)  python: $(which python)"
echo "$(ts)  free -h:"
free -h | sed 's/^/    /'
echo "$(ts)  =========================================================="

# Wait for any in-flight scripts/run_adequacy.py (e.g. policy_re_noMin still
# running). Otherwise we'd kick off 2 corridor solves on top of it = 3-way
# RAM contention.
while pgrep -af "run_adequacy.py" | grep -v -E "(grep|run_scenarios_parallel)" >/dev/null 2>&1; do
    OTHER=$(pgrep -af "run_adequacy.py" | grep -v -E "(grep|run_scenarios_parallel)" | head -1 | awk '{print $1}')
    echo "$(ts)  waiting for existing run_adequacy.py (PID $OTHER) to finish..."
    sleep 120
done
echo "$(ts)  no in-flight solves — proceeding"

# Pre-create per-scenario dirs
for s in "${SCENARIOS[@]}"; do
    mkdir -p "results/diagnostics/$s" "results/figures/$s" "results/export/$s"
done

# nbconvert once — all scenarios share the script
echo
echo "$(ts)  --- nbconvert ---"
rm -f scripts/run_adequacy.py
jupyter nbconvert --to script notebooks/adequacy_clean.ipynb \
                  --output ../scripts/run_adequacy 2>&1 | tail -2 | sed 's/^/  /'
if [ ! -f scripts/run_adequacy.py ]; then
    echo "$(ts)  FATAL: nbconvert did not produce scripts/run_adequacy.py"
    exit 4
fi
sed -i 's/^display(/print(/g; s/ display(/ print(/g' scripts/run_adequacy.py
python -c "import ast; ast.parse(open('scripts/run_adequacy.py').read()); print('  syntax OK')"

declare -A PIDS=()
declare -A STARTS=()
SUMMARY=()

run_scenario() {
    local SCEN=$1
    local LOG="logs/${SCEN}.log"
    (
        export CLEVER_SCENARIO="$SCEN"
        nice -n 10 python -u scripts/run_adequacy.py > "$LOG" 2>&1
    ) &
    local pid=$!
    PIDS[$pid]=$SCEN
    STARTS[$pid]=$(date +%s)
    echo "$(ts)  >>> $SCEN started (PID $pid) — ${#PIDS[@]}/$MAX_PARALLEL running"
}

reap_one() {
    wait -n  # wait for ANY background job to finish
    # Identify which one of our tracked PIDs is no longer alive
    local finished_pid=""
    for pid in "${!PIDS[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            finished_pid=$pid
            break
        fi
    done
    if [ -z "$finished_pid" ]; then
        # Race condition fallback — shouldn't happen but just in case
        return
    fi
    local SCEN=${PIDS[$finished_pid]}
    local duration=$(( ($(date +%s) - ${STARTS[$finished_pid]}) / 60 ))

    local AUDIT_LOG="results/diagnostics/${SCEN}/audit.log"
    local AUDIT="solve FAILED (no input_dataset)"
    if [ -f "results/diagnostics/${SCEN}/input_dataset_2050.nc" ]; then
        if python scripts/audit_capex.py \
             "results/diagnostics/${SCEN}/input_dataset_2050.nc" "$SCEN" \
             > "$AUDIT_LOG" 2>&1; then
            AUDIT="audit OK"
        else
            AUDIT="AUDIT FAIL — see $AUDIT_LOG"
        fi
    fi

    local LOG="logs/${SCEN}.log"
    local OBJ=$(grep -oE 'Objective value:[[:space:]]*[0-9,]+[[:space:]]*EUR' "$LOG" 2>/dev/null | tail -1)
    SUMMARY+=("$SCEN  ${duration}min  $AUDIT  $OBJ")
    echo "$(ts)  <<< $SCEN finished ($duration min) — $AUDIT"
    unset 'PIDS[$finished_pid]'
    unset 'STARTS[$finished_pid]'
}

# Main scheduling loop
for SCEN in "${SCENARIOS[@]}"; do
    while [ "${#PIDS[@]}" -ge "$MAX_PARALLEL" ]; do
        reap_one
    done
    run_scenario "$SCEN"
done

# Drain remaining
while [ "${#PIDS[@]}" -gt 0 ]; do
    reap_one
done

echo
echo "$(ts)  =========================================================="
echo "$(ts)  === parallel scheduler FINISHED ==="
echo "$(ts)  =========================================================="
echo
echo "Summary:"
printf '  %s\n' "${SUMMARY[@]}"

{
    echo "completed_at=$(ts)"
    echo "scenarios: ${SCENARIOS[*]}"
    echo "max_parallel: $MAX_PARALLEL"
    echo
    echo "summary:"
    printf '  %s\n' "${SUMMARY[@]}"
} > results/diagnostics/PARALLEL_DONE
