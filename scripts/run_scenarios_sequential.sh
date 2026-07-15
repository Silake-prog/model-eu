#!/usr/bin/env bash
# scripts/run_scenarios_sequential.sh — run any list of SCENARIO values
# sequentially, one per solve, with shared pre-flight + nbconvert. Same
# pattern as run_all_overnight.sh but accepts the scenario list as args
# so we can re-run a subset (e.g. only the corridor variants) without
# touching scenarios already solved.
#
# Usage (server-side, invoked by Mac wrapper):
#   bash scripts/run_scenarios_sequential.sh policy_re_corr2x policy_re_corr3x policy_nuke_corr2x policy_nuke_corr3x
#
# Writes:
#   logs/<SCEN>.log            — per-scenario solve log
#   results/diagnostics/<SCEN>/{solution,dual,input_dataset}_2050.nc
#   results/diagnostics/<SCEN>/audit.log
#   results/diagnostics/SEQUENTIAL_DONE — sentinel with summary at end

if [ "$#" -lt 1 ]; then
    echo "usage: $0 <scenario1> [scenario2 ...]"
    exit 2
fi
SCENARIOS=("$@")

WORK=/diskdata/cired/brigode/clever-work
cd "$WORK"

# bashrc trap defeat — same as run_all_overnight.sh
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
echo "$(ts)  === sequential runner — ${#SCENARIOS[@]} scenarios ==="
echo "$(ts)  scenarios: ${SCENARIOS[*]}"
echo "$(ts)  python: $(which python)"
echo "$(ts)  =========================================================="

# Pre-create per-scenario dirs
for s in "${SCENARIOS[@]}"; do
    mkdir -p "results/diagnostics/$s" "results/figures/$s" "results/export/$s"
done

# nbconvert once, shared across all scenarios
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

# Loop
SUMMARY=()
for SCEN in "${SCENARIOS[@]}"; do
    echo
    echo "$(ts)  =========================================================="
    echo "$(ts)  >>> $SCEN starting"
    echo "$(ts)  =========================================================="

    export CLEVER_SCENARIO="$SCEN"
    LOG="logs/${SCEN}.log"

    START=$(date +%s)
    if nice -n 10 python -u scripts/run_adequacy.py > "$LOG" 2>&1; then
        DURATION=$(( ($(date +%s) - START) / 60 ))
        echo "$(ts)  <<< $SCEN solve OK (${DURATION} min)"
        AUDIT_LOG="results/diagnostics/${SCEN}/audit.log"
        if python scripts/audit_capex.py \
             "results/diagnostics/${SCEN}/input_dataset_2050.nc" "$SCEN" \
             > "$AUDIT_LOG" 2>&1; then
            AUDIT="audit OK"
        else
            AUDIT="AUDIT FAIL — see $AUDIT_LOG"
            tail -8 "$AUDIT_LOG" | sed 's/^/    /'
        fi
        OBJ=$(grep -oE 'Objective value:[[:space:]]*[0-9,]+[[:space:]]*EUR' "$LOG" | tail -1)
        SUMMARY+=("$SCEN  OK  ${DURATION}min  $AUDIT  $OBJ")
    else
        DURATION=$(( ($(date +%s) - START) / 60 ))
        echo "$(ts)  <<< $SCEN FAILED after ${DURATION} min — last lines:"
        tail -30 "$LOG" | sed 's/^/    /'
        SUMMARY+=("$SCEN  FAIL  ${DURATION}min  see $LOG")
    fi
done

echo
echo "$(ts)  =========================================================="
echo "$(ts)  === sequential runner FINISHED ==="
echo "$(ts)  =========================================================="
echo
echo "Summary:"
printf '  %s\n' "${SUMMARY[@]}"

{
    echo "completed_at=$(ts)"
    echo "scenarios: ${SCENARIOS[*]}"
    echo
    echo "summary:"
    printf '  %s\n' "${SUMMARY[@]}"
} > results/diagnostics/SEQUENTIAL_DONE
