#!/usr/bin/env bash
# scripts/_rerun_one_scenario.sh — re-run a single scenario on inari without
# touching the other three. Invoked by Mac wrapper after rsync.
#
# Usage (server-side):
#   bash scripts/_rerun_one_scenario.sh policy_re_noMin

set -e

SCEN="${1:?scenario name required (e.g. policy_re_noMin)}"
WORK=/diskdata/cired/brigode/clever-work
cd "$WORK"

export BASHRCSOURCED=""
[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc" || true
mamba activate EOLES_POMMES

export CLEVER_SCENARIO="$SCEN"
export CLEVER_WORK_ROOT="$WORK"
export MPLBACKEND=Agg

mkdir -p "logs" "results/diagnostics/$SCEN" "results/figures/$SCEN" "results/export/$SCEN"

echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  re-running scenario $SCEN"
echo "  python: $(which python)"

# Fresh nbconvert — wipe stale first
rm -f scripts/run_adequacy.py
jupyter nbconvert --to script notebooks/adequacy_clean.ipynb \
                  --output ../scripts/run_adequacy 2>&1 | tail -2 | sed 's/^/  /'
if [ ! -f scripts/run_adequacy.py ]; then
    echo "FATAL: nbconvert did not produce scripts/run_adequacy.py"
    exit 4
fi
sed -i 's/^display(/print(/g; s/ display(/ print(/g' scripts/run_adequacy.py
python -c "import ast; ast.parse(open('scripts/run_adequacy.py').read()); print('  syntax OK')"

# Kill any stale runner of the same scenario
pkill -f "run_adequacy.py" 2>/dev/null || true
sleep 1

nohup nice -n 10 python -u scripts/run_adequacy.py > "logs/${SCEN}.log" 2>&1 &
PID=$!
echo "  spawned PID $PID — log at logs/${SCEN}.log"
sleep 2
pgrep -f run_adequacy.py | head -3 | sed 's/^/  alive PID: /'
