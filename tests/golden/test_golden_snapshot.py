"""Golden-snapshot regression tests — the reproduction safety net.

Tier A (always runs in the code-only env): the ``clever.constants`` assembly for the
reference scenarios must stay byte-identical to ``baseline_hashes.json``. Any drift is
a numerics regression and fails the build. Regenerate the baseline only via an explicit
``python tests/golden/snapshot_constants.py --write`` in a reviewed commit.

Tier B (skips unless ``pommes_craft``/``pandas`` are importable): the model builder
stays importable with a stable signature. See ``synthetic_inputs.py`` for the deferred
``parameter_tables`` fingerprint that needs a deps-equipped environment.

Runnable two ways:
    pytest tests/golden/            # under pytest, with proper skips
    python tests/golden/test_golden_snapshot.py   # plain runner (no pytest needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.golden import snapshot_constants as snap  # noqa: E402
from tests.golden import synthetic_inputs  # noqa: E402


def test_tier_a_constants_match_baseline() -> None:
    """Every reference scenario reproduces the committed constants fingerprint."""
    problems = snap.diff_against_baseline()
    assert problems == [], "Golden drift:\n  " + "\n  ".join(problems)


def test_tier_b_builder_contract() -> None:
    """Model builder importable with the expected signature (skips without deps)."""
    result = synthetic_inputs.check_builder_contract()
    if result.skipped:
        _skip(result.reason)
        return
    assert result.ok, result.reason


# ── minimal pytest-compatible skip that also works under the plain runner ──
class _Skipped(Exception):
    pass


def _skip(reason: str) -> None:
    try:
        import pytest

        pytest.skip(reason)
    except ImportError:
        raise _Skipped(reason)


def _run_standalone() -> int:
    tests = [
        ("test_tier_a_constants_match_baseline", test_tier_a_constants_match_baseline),
        ("test_tier_b_builder_contract", test_tier_b_builder_contract),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except _Skipped as exc:
            print(f"SKIP  {name} — {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name} — {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
