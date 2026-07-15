#!/usr/bin/env bash
# scripts/launch_overnight.sh — single Mac-side command that does:
#   1. rsync clever/, notebook, scripts/, tables/ to inari
#   2. ssh in and start a detached tmux session running run_all_overnight.sh
#   3. return immediately so you can close the laptop
#
# Usage (from your Mac):
#   bash ~/Desktop/clever-work/scripts/launch_overnight.sh
#
# Reattach later (optional):
#   ssh brigode@inari.centre-cired.fr 'tmux a -t overnight'
#
# Detect completion from your Mac (poll until non-empty):
#   ssh brigode@inari.centre-cired.fr 'cat /diskdata/cired/brigode/clever-work/results/diagnostics/OVERNIGHT_DONE 2>/dev/null'

set -euo pipefail

HOST=brigode@inari.centre-cired.fr
LOCAL=~/Desktop/clever-work
REMOTE=/diskdata/cired/brigode/clever-work
SESSION=overnight

echo "==> sync clever/ → $HOST"
rsync -avz \
    "$LOCAL/clever/" \
    "$HOST:$REMOTE/clever/"

echo
echo "==> sync notebook"
rsync -avz "$LOCAL/notebooks/adequacy_clean.ipynb" \
    "$HOST:$REMOTE/notebooks/"

echo
echo "==> sync scripts/ (audit + overnight runner)"
ssh "$HOST" "mkdir -p $REMOTE/scripts"
rsync -avz "$LOCAL/scripts/" "$HOST:$REMOTE/scripts/"

echo
echo "==> sync tables/ (per-scenario CSV dirs)"
rsync -avz "$LOCAL/tables/" "$HOST:$REMOTE/tables/"

echo
echo "==> launch — try tmux first, fall back to nohup if unavailable"
# Detect tmux on the server (login shell to pick up user PATH)
HAS_TMUX=$(ssh "$HOST" 'bash -lc "command -v tmux 2>/dev/null"' || true)

# Invoke the remote launch helper. Pre-export BASHRCSOURCED= and use
# --noprofile --norc so inari's /etc/bashrc strict-mode trap can't fire
# during launch. The helper itself (scripts/_remote_launch.sh) handles
# pkill / nohup / pgrep with clean local-only quoting — no nested
# Mac→ssh→bash quoting hell.
REMOTE_BASH="BASHRCSOURCED= bash --noprofile --norc"

if [ -n "$HAS_TMUX" ]; then
    echo "    tmux found at: $HAS_TMUX — using tmux session '$SESSION'"
    ssh "$HOST" "$REMOTE_BASH -c 'tmux kill-session -t $SESSION 2>/dev/null || true'"
    ssh "$HOST" "$REMOTE_BASH -c 'cd $REMOTE && chmod +x scripts/run_all_overnight.sh && mkdir -p logs && tmux new -d -s $SESSION \"bash $REMOTE/scripts/run_all_overnight.sh\" && sleep 1 && tmux ls'"
    echo
    echo "==> launched in tmux session '$SESSION' on $HOST."
    echo "    Reattach (optional):    ssh $HOST 'tmux a -t $SESSION'"
else
    echo "    tmux NOT found on $HOST — falling back to nohup"
    ssh "$HOST" "$REMOTE_BASH $REMOTE/scripts/_remote_launch.sh"
    echo
    echo "==> launched via nohup on $HOST."
    echo "    Live log (top-level):   ssh $HOST 'tail -f $REMOTE/logs/overnight.log'"
fi

echo "    Per-scenario log live:  ssh $HOST 'tail -f $REMOTE/logs/policy_re.log'"
echo "    Poll for completion:    ssh $HOST 'cat $REMOTE/results/diagnostics/OVERNIGHT_DONE 2>/dev/null'"
