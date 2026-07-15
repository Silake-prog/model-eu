#!/usr/bin/env bash
# scripts/run_all_overnight.sh — sequential overnight runner for Tier A scenarios.
#
# Runs the four scenarios one after the other in a single tmux session so RAM
# never exceeds one solve's footprint (~50 GB). Wall-clock budget: 4 × ~3 h
# ≈ 12 h. Launch in the evening, walk away, results land overnight.
#
# Usage:
#   ssh brigode@inari.centre-cired.fr
#   tmux new -s overnight
#   bash /diskdata/cired/brigode/clever-work/scripts/run_all_overnight.sh
#   # Ctrl-b d to detach; close laptop; come back tomorrow.
#
# Reattach:   tmux a -t overnight
# Final sentinel: results/diagnostics/OVERNIGHT_DONE (timestamped on completion)
# Per-scenario logs: logs/<scenario>.log
# Per-scenario CAPEX audit: results/diagnostics/<scenario>/audit.log
#
# Sequence:
#   1. policy_re        — canary for central/+20% elec/VRE 0.30 plumbing
#   2. R0_v1_nuke       — independent demand path (low_h2); not blocked if 1 fails
#   3. policy_nuke      — depends on 1's plumbing + Nuclear plumbing
#   4. policy_re_noMin  — depends on 1's plumbing, no-min variant
#
# Fail-fast: if policy_re fails, scenarios 3 and 4 are skipped (same demand path).
# R0_v1_nuke still runs even if policy_re fails (different bundle).

# NB: deliberately NOT using `set -u`. inari's /etc/bashrc references
# $BASHRCSOURCED without setting it on the same line, which trips strict
# mode when ~/.bashrc transitively sources /etc/bashrc. Not using `set -e`
# either — we want to continue past per-scenario failures.

# ────────────────────────────────────────────────────────────────────────
# Paths and env
# ────────────────────────────────────────────────────────────────────────
WORK=/diskdata/cired/brigode/clever-work
cd "$WORK"

# Activate the EOLES_POMMES conda/mamba env.
#
# Subtlety: this script is invoked via nohup (non-interactive, non-login),
# so ~/.bashrc is NOT auto-sourced and `mamba activate` would silently
# no-op even if mamba is on PATH (because `activate` is a SHELL FUNCTION
# defined by mamba's init hook, not an executable). We MUST source the
# user's bashrc / profile first to pick up that function.
#
# inari's /etc/bashrc line 12 reads `$BASHRCSOURCED` under strict mode
# (which the user's ~/.bashrc enables before sourcing /etc/bashrc). The
# variable is supposed to be set elsewhere; if it isn't, set -u trips and
# the entire bashrc chain aborts. Pre-defining the variable to an empty
# string defeats the trip and lets the bashrc continue normally.
export BASHRCSOURCED=""
[ -f "$HOME/.bashrc" ]       && source "$HOME/.bashrc"       || true
[ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" || true

# Fallback: source the standard mamba/conda init hooks directly if they
# weren't loaded by bashrc.
if ! type mamba >/dev/null 2>&1 || ! declare -F mamba >/dev/null 2>&1; then
    for hook in "$HOME/mambaforge/etc/profile.d/mamba.sh" \
                "$HOME/mambaforge/etc/profile.d/conda.sh" \
                "$HOME/miniconda3/etc/profile.d/mamba.sh" \
                "$HOME/miniconda3/etc/profile.d/conda.sh" \
                "$HOME/conda/etc/profile.d/mamba.sh" \
                "$HOME/conda/etc/profile.d/conda.sh" \
                "/opt/mambaforge/etc/profile.d/mamba.sh" \
                "/opt/conda/etc/profile.d/mamba.sh" \
                "/opt/conda/etc/profile.d/conda.sh"; do
        if [ -f "$hook" ]; then
            # shellcheck disable=SC1090
            source "$hook"
        fi
    done
fi

# Now activate — try mamba, fall back to conda
if declare -F mamba >/dev/null 2>&1; then
    mamba activate EOLES_POMMES
elif declare -F conda >/dev/null 2>&1; then
    conda activate EOLES_POMMES
else
    echo "FATAL: neither mamba nor conda activate function is available. "
    echo "       sourced ~/.bashrc and standard hooks but nothing took."
    echo "       Run interactively on inari and copy your env init lines here."
    exit 3
fi

# Hard guard: refuse to proceed if we're still on system python
if [[ "$(which python)" == "/usr/bin/python" || -z "${CONDA_DEFAULT_ENV:-}" ]]; then
    echo "FATAL: env activation didn't take. which python = $(which python)"
    echo "       CONDA_DEFAULT_ENV = ${CONDA_DEFAULT_ENV:-<empty>}"
    exit 3
fi

export CLEVER_WORK_ROOT="$WORK"

# Headless matplotlib — the notebook has figure cells that would call
# plt.show() and hang or crash under non-interactive Python; Agg backend
# turns them into silent no-ops.
export MPLBACKEND=Agg

mkdir -p logs

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

echo "$(ts)  =========================================================="
echo "$(ts)  === overnight runner starting ==="
echo "$(ts)  =========================================================="
echo "$(ts)  WORK=$WORK"
echo "$(ts)  python: $(which python)"
echo "$(ts)  free -h:"
free -h | sed 's/^/    /'

# ────────────────────────────────────────────────────────────────────────
# Pre-flight checks (fail loudly if anything is missing)
# ────────────────────────────────────────────────────────────────────────
echo
echo "$(ts)  --- pre-flight ---"

# 1. EOLES costs — sourced from in-package dicts (clever/constants.py), not
#    from CSVs. Confirm the dicts are populated with the techs we need.
echo "  EOLES cost dicts + nuclear smoke test:"
python <<'PY' 2>&1 | sed 's/^/    /'
from clever.constants import (
    _EOLES_CAPEX_2026, _EOLES_FOM_2026, _EOLES_VOM_2026,
    _EOLES_DISCOUNT_RATE_UNIFORM, _EOLES_STORAGE_CAPEX_2026,
    FUEL_ADDER_2050, EOLES_LIFETIME,
)
required = {"capex": _EOLES_CAPEX_2026, "fom": _EOLES_FOM_2026, "vom": _EOLES_VOM_2026,
            "discount": _EOLES_DISCOUNT_RATE_UNIFORM, "storage_capex": _EOLES_STORAGE_CAPEX_2026}
for name, d in required.items():
    print(f"  {name:15s}: {len(d)} entries")
# Nuclear smoke-test (needed for policy_nuke / R0_v1_nuke)
capex_eur_per_mw = (_EOLES_CAPEX_2026.get("nuclear", 0.0)) * 1000
vom_plus_fuel    = (_EOLES_VOM_2026.get("nuclear", 0.0) or 0.0) + (FUEL_ADDER_2050.get("nuclear", 0.0) or 0.0)
fom_eur_per_mwyr = (_EOLES_FOM_2026.get("nuclear", 0.0)) * 1000
life             = EOLES_LIFETIME.get("nuclear", 60)
discount         = _EOLES_DISCOUNT_RATE_UNIFORM.get("nuclear", 0.04)
print(f"  nuclear:  invest={capex_eur_per_mw/1e6:.2f} M€/MW  "
      f"variable={vom_plus_fuel:.2f} €/MWh  fixed={fom_eur_per_mwyr/1e3:.1f} k€/MW/yr  "
      f"life={life}yr  discount={discount}")
if not (4 <= capex_eur_per_mw/1e6 <= 9):
    print(f"  WARN: nuclear invest {capex_eur_per_mw/1e6:.2f} M€/MW outside [4, 9] band — "
          "may be FOAK EPR2 estimate (high end), confirm intended.")
if not (5 <= vom_plus_fuel <= 15):
    print(f"  WARN: nuclear variable {vom_plus_fuel:.2f} €/MWh outside [5, 15] band.")
PY
if [ "${PIPESTATUS[0]:-0}" -ne 0 ]; then
    echo "$(ts)  FATAL: EOLES dict check failed."
    exit 2
fi

# 2. Pre-create per-scenario diagnostics/figures/export dirs
for s in R0_v1 policy_re policy_nuke R0_v1_nuke policy_re_noMin; do
    mkdir -p "results/diagnostics/$s" "results/figures/$s" "results/export/$s"
done
echo "  Per-scenario dirs created"

# 3. nbconvert the notebook once — all scenarios share the script.
#    NB: jupyter `--output` is relative to the input file's directory,
#    so `--output ../scripts/run_adequacy` writes to ./scripts/ from cwd.
echo
echo "$(ts)  --- nbconvert ---"
# Wipe any stale script so a silent nbconvert failure can't leave us
# running outdated code from a previous session.
rm -f scripts/run_adequacy.py
jupyter nbconvert --to script notebooks/adequacy_clean.ipynb \
                  --output ../scripts/run_adequacy 2>&1 | tail -2 | sed 's/^/  /'
if [ ! -f scripts/run_adequacy.py ]; then
    echo "$(ts)  FATAL: nbconvert did not produce scripts/run_adequacy.py"
    exit 4
fi
sed -i 's/^display(/print(/g; s/ display(/ print(/g' scripts/run_adequacy.py
python -c "import ast; ast.parse(open('scripts/run_adequacy.py').read()); print('  syntax OK')"

# ────────────────────────────────────────────────────────────────────────
# Sequence
# ────────────────────────────────────────────────────────────────────────
SCENARIOS=(policy_re R0_v1_nuke policy_nuke policy_re_noMin)
SUMMARY=()
POLICY_RE_OK=1

for SCEN in "${SCENARIOS[@]}"; do
    echo
    echo "$(ts)  =========================================================="
    echo "$(ts)  >>> $SCEN starting"
    echo "$(ts)  =========================================================="

    # Fail-fast: skip same-demand-path dependents if policy_re failed
    if [ "$POLICY_RE_OK" -eq 0 ] && [ "$SCEN" != "R0_v1_nuke" ]; then
        echo "  SKIPPED: same demand path as policy_re, which failed."
        SUMMARY+=("$SCEN  SKIPPED (policy_re failed)")
        continue
    fi

    export CLEVER_SCENARIO="$SCEN"
    LOG="logs/${SCEN}.log"

    START_EPOCH=$(date +%s)
    # nice -n 10 → deprioritize so we don't starve other inari users
    if nice -n 10 python -u scripts/run_adequacy.py > "$LOG" 2>&1; then
        SOLVE="OK"
        DURATION=$(( ($(date +%s) - START_EPOCH) / 60 ))
        echo "$(ts)  <<< $SCEN solve OK (${DURATION} min)"

        # CAPEX audit
        AUDIT_LOG="results/diagnostics/${SCEN}/audit.log"
        if python scripts/audit_capex.py \
             "results/diagnostics/${SCEN}/input_dataset_2050.nc" "$SCEN" \
             > "$AUDIT_LOG" 2>&1; then
            AUDIT="audit OK"
        else
            AUDIT="AUDIT FAIL — see $AUDIT_LOG"
            tail -8 "$AUDIT_LOG" | sed 's/^/    /'
        fi

        # Quick stats grep from solve log
        OBJ=$(grep -oE 'Objective value:[[:space:]]*[0-9,]+[[:space:]]*EUR' "$LOG" | tail -1)
        SUMMARY+=("$SCEN  $SOLVE  ${DURATION}min  $AUDIT  $OBJ")
    else
        SOLVE="FAIL"
        DURATION=$(( ($(date +%s) - START_EPOCH) / 60 ))
        echo "$(ts)  <<< $SCEN FAILED after ${DURATION} min — last lines of $LOG:"
        tail -30 "$LOG" | sed 's/^/    /'
        SUMMARY+=("$SCEN  FAIL  ${DURATION}min  see $LOG")
        if [ "$SCEN" = "policy_re" ]; then
            POLICY_RE_OK=0
        fi
    fi
done

# ────────────────────────────────────────────────────────────────────────
# Final summary + sentinel
# ────────────────────────────────────────────────────────────────────────
echo
echo "$(ts)  =========================================================="
echo "$(ts)  === overnight runner FINISHED ==="
echo "$(ts)  =========================================================="
echo
echo "Summary:"
printf '  %s\n' "${SUMMARY[@]}"

# Sentinel for external watchers and quick polling from the laptop
{
    echo "completed_at=$(ts)"
    echo
    echo "summary:"
    printf '  %s\n' "${SUMMARY[@]}"
} > results/diagnostics/OVERNIGHT_DONE

echo
echo "Sentinel: results/diagnostics/OVERNIGHT_DONE"
echo "Per-scenario logs: logs/{policy_re,R0_v1_nuke,policy_nuke,policy_re_noMin}.log"
echo "Per-scenario audits: results/diagnostics/<scenario>/audit.log"
echo "Diagnostics: results/diagnostics/<scenario>/{solution,dual,input_dataset}_2050.nc"
