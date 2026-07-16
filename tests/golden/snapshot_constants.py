"""Tier-A golden snapshot — fingerprint the ``clever.constants`` module globals.

The scenario is an **import-time process global**: ``clever.constants`` reads
``CLEVER_SCENARIO`` at module top and eagerly runs its ``_parse_*`` functions into
module-level globals. You therefore cannot re-evaluate a different scenario in the
same process — so this harness spawns a **fresh subprocess per scenario**, imports
``clever.constants`` there, and emits a canonical SHA-256 per public data global.

Dependency-light by design: it needs only the standard library plus ``platformdirs``
(pulled in by ``clever/__init__.py``). It does NOT need ``pommes_craft``, ``pommes``,
``pandas`` or any solver/data cache, so it runs in the code-only snapshot environment
and is the safety net re-run after every cleanup phase.

Usage
-----
    python tests/golden/snapshot_constants.py --dump      # one scenario (reads env)
    python tests/golden/snapshot_constants.py --write      # (re)write baseline_hashes.json
    python tests/golden/snapshot_constants.py --check      # diff current vs baseline (exit 1 on drift)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
BASELINE_PATH = HERE / "baseline_hashes.json"

# Reference scenarios chosen to exercise distinct parser branches:
#   - baseline_R0      : minimal R0 sufficiency baseline (low_h2 bundle, VRE 0.15)
#   - policy_re        : the policy_* prefix gate (high demand, VRE 0.30)
#   - rich_high        : deep flag stack (nuke/bioMed/atr/el700/noGas/corr2x/elecX180/h2HIGH/vreEXT)
#   - mena_variantB    : MENA optimised-imports path + caps/floors (Variant-B parsers)
# All four are structurally valid under the flag registry (scenario/registry.py).
REFERENCE_SCENARIOS: dict[str, str] = {
    "baseline_R0": "R0_v1",
    "policy_re": "policy_re",
    "rich_high": "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT",
    "mena_variantB": (
        "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT"
        "_nofloor_noElecFloor_co2150_batt20_menaOptim_menaCap200"
    ),
}

# Only these types are treated as "data" worth fingerprinting. This deliberately
# excludes modules, functions, classes, compiled regexes and other non-data globals
# so the snapshot tracks values, not code objects.
_DATA_TYPES = (dict, set, frozenset, list, tuple, str, int, float, bool, type(None))


def _canonical(obj: object) -> str:
    """Deterministic, order-stable string encoding of a data value.

    Dict keys and set elements are sorted by their own canonical encoding so the
    hash is invariant to insertion order. ``float`` uses ``repr`` (round-trippable).
    """
    if isinstance(obj, bool):  # before int — bool is a subclass of int
        return "bool:" + repr(obj)
    if obj is None or isinstance(obj, (int, str)):
        return repr(obj)
    if isinstance(obj, float):
        return "float:" + repr(obj)
    if isinstance(obj, dict):
        items = sorted(
            ((_canonical(k), _canonical(v)) for k, v in obj.items()),
            key=lambda kv: kv[0],
        )
        return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"
    if isinstance(obj, (set, frozenset)):
        return "set(" + ",".join(sorted(_canonical(x) for x in obj)) + ")"
    if isinstance(obj, (list, tuple)):
        kind = "list" if isinstance(obj, list) else "tuple"
        return kind + "[" + ",".join(_canonical(x) for x in obj) + "]"
    # Should be unreachable given _DATA_TYPES gating; fall back to a stable repr.
    return "repr:" + repr(obj)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dump_current_process() -> dict[str, str]:
    """Import ``clever.constants`` in THIS process and hash each public data global.

    Returns a mapping ``{global_name: sha256}``. Assumes ``CLEVER_SCENARIO`` and
    ``sys.path`` are already set up by the caller (see :func:`collect`).
    """
    import clever.constants as c  # noqa: WPS433 (import inside function is intentional)

    snapshot: dict[str, str] = {}
    for name, value in vars(c).items():
        if name.startswith("__"):
            continue
        if not isinstance(value, _DATA_TYPES):
            continue
        snapshot[name] = _sha(_canonical(value))
    return snapshot


def collect(scenario_string: str) -> dict[str, str]:
    """Fingerprint ``clever.constants`` for one scenario in a fresh subprocess."""
    with tempfile.TemporaryDirectory(prefix="clever_golden_") as data_dir:
        env = dict(os.environ)
        env["CLEVER_SCENARIO"] = scenario_string
        # Keep the import hermetic: send clever's writable data dir to scratch so
        # importing does not create results/ inside the repo.
        env["CLEVER_DATA"] = data_dir
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        out = subprocess.check_output(
            [sys.executable, str(HERE / "snapshot_constants.py"), "--dump"],
            env=env,
            cwd=str(REPO_ROOT),
        )
    return json.loads(out)


def collect_all() -> dict[str, dict[str, str]]:
    """Fingerprint every reference scenario. Returns ``{label: {name: sha}}``."""
    return {label: collect(scn) for label, scn in REFERENCE_SCENARIOS.items()}


def load_baseline() -> dict[str, dict[str, str]]:
    return json.loads(BASELINE_PATH.read_text())


def write_baseline() -> dict[str, dict[str, str]]:
    snapshot = collect_all()
    payload = {
        "_meta": {
            "note": "Regenerating this file changes the reproduction contract; "
            "only do so in an explicit, reviewed commit.",
            "scenarios": REFERENCE_SCENARIOS,
        },
        "snapshots": snapshot,
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return snapshot


def diff_against_baseline() -> list[str]:
    """Return a list of human-readable drift messages (empty == byte-identical)."""
    baseline = load_baseline()["snapshots"]
    current = collect_all()
    problems: list[str] = []
    for label in REFERENCE_SCENARIOS:
        base = baseline.get(label, {})
        cur = current.get(label, {})
        for name in sorted(set(base) - set(cur)):
            problems.append(f"[{label}] dropped global: {name}")
        for name in sorted(set(cur) - set(base)):
            problems.append(f"[{label}] new global: {name}")
        for name in sorted(set(base) & set(cur)):
            if base[name] != cur[name]:
                problems.append(f"[{label}] value changed: {name}")
    return problems


def _main(argv: list[str]) -> int:
    if "--dump" in argv:
        json.dump(dump_current_process(), sys.stdout)
        return 0
    if "--write" in argv:
        snap = write_baseline()
        n = sum(len(v) for v in snap.values())
        print(f"Wrote baseline: {len(snap)} scenarios, {n} hashed globals total.")
        return 0
    if "--check" in argv:
        problems = diff_against_baseline()
        if problems:
            print("GOLDEN DRIFT DETECTED:")
            for p in problems:
                print("  " + p)
            return 1
        print("Golden snapshot OK — all reference scenarios byte-identical.")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
