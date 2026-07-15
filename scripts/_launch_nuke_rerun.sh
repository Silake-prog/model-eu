#!/usr/bin/env bash
cd /diskdata/cired/brigode/clever-work
exec bash scripts/run_scenarios_parallel.sh \
    policy_nuke R0_v1_nuke \
    policy_nuke_corr2x policy_nuke_corr3x \
    R0_v1_nuke_corr2x R0_v1_nuke_corr3x \
    > logs/corridors_all.log 2>&1
