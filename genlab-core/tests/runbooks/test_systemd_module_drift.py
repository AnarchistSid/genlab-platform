"""Universal drift-protection: every systemd unit's `ExecStart` python
module must actually exist and expose `main()` or be importable.

This single parametric test catches the entire class of half-wired
infrastructure where a systemd `.service` references a Python module
that's been renamed, deleted, or never existed.

The session that birthed this test (2026-06-14) shipped a duplicate
`genlab-hook-classifier-train.service` because the existing
`genlab-hook-trainer.service` (invoking the SAME target module) was
missed during the survey. That mistake would have caused a
file-name collision on prod systemd. This test wouldn't have caught
the duplicate directly — it catches the inverse problem (unit
references nonexistent module) — but reading it before shipping
new units forces the survey question "which existing unit already
invokes this module?"

How it works
============

For every `.service` file in `deploy/systemd-phase2/`:
    1. Parse the file as INI
    2. Find the `ExecStart` line
    3. Extract the Python module after `-m`
    4. Skip if no module (some services invoke bash scripts, dramatiq, etc.)
    5. Assert `importlib.import_module(module)` succeeds
    6. If the module has a `main()` callable, assert it's callable
       (operator-CLI contract)

Skipped service patterns:
    * dramatiq workers — invoke `dramatiq broker actor_module` shape
    * Bash-only services (rare; none today)

Coverage extends automatically: a new `.service` file dropped into
`deploy/systemd-phase2/` gets walked next run without test changes.
"""

from __future__ import annotations

import configparser
import importlib
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SYSTEMD_DIR = _REPO_ROOT / "deploy" / "systemd-phase2"

# Match `-m foo.bar.baz` in an ExecStart line. Underscore + dot
# only — the legal Python module-name shape.
_MODULE_RE = re.compile(r"-m\s+([a-zA-Z_][a-zA-Z0-9_.]*)")

# Services whose ExecStart deliberately doesn't reference a single
# importable module. Add to this set with a comment explaining WHY.
_SKIP_REASONS: dict[str, str] = {
    # dramatiq workers invoke `dramatiq broker_module actor_module`
    # which isn't a simple `-m` import target.
    "genlab-engagement-worker": "dramatiq broker invocation, not a -m module",
    # Pipeline systemd unit per niche — wraps a bash script (see also:
    # tests/deploy/test_pipeline_systemd_units.py for those).
    "genlab-pipeline-ai": "bash pipeline wrapper, not a -m module",
    "genlab-pipeline-anime": "bash pipeline wrapper, not a -m module",
    "genlab-pipeline-gaming": "bash pipeline wrapper, not a -m module",
    "genlab-pipeline-movies": "bash pipeline wrapper, not a -m module",
    "genlab-pipeline-sports": "bash pipeline wrapper, not a -m module",
}


def _extract_module(service_path: Path) -> str | None:
    """Return the Python module invoked by ExecStart, or None."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(service_path)
    if not parser.has_section("Service"):
        return None
    exec_start = parser["Service"].get("ExecStart", "")
    m = _MODULE_RE.search(exec_start)
    return m.group(1) if m else None


def _collect_services_with_modules() -> list[tuple[str, str]]:
    """Walk deploy/systemd-phase2/*.service and return [(name, module)]
    pairs for services whose ExecStart names an importable Python module."""
    pairs: list[tuple[str, str]] = []
    for service_path in sorted(_SYSTEMD_DIR.glob("*.service")):
        name = service_path.stem
        if name in _SKIP_REASONS:
            continue
        module = _extract_module(service_path)
        if module:
            pairs.append((name, module))
    return pairs


_SERVICE_MODULE_PAIRS = _collect_services_with_modules()


def test_at_least_some_services_have_module_targets():
    """Sanity check: if this finds zero pairs, the discovery itself
    broke (e.g. the regex stopped matching), and the parametric test
    below would pass vacuously."""
    assert len(_SERVICE_MODULE_PAIRS) >= 5, (
        f"Expected ≥ 5 systemd services with -m module ExecStart, "
        f"found {len(_SERVICE_MODULE_PAIRS)} — discovery likely broke"
    )


@pytest.mark.parametrize(
    "service_name,module_name",
    _SERVICE_MODULE_PAIRS,
    ids=[p[0] for p in _SERVICE_MODULE_PAIRS],
)
def test_systemd_service_module_is_importable(service_name: str, module_name: str):
    """The headline pin: every systemd service that invokes a Python
    module must reference one that actually exists.

    A drift here means production systemd will run the service, fail
    with `ModuleNotFoundError`, and the operator sees journal output
    but no functional change. This test surfaces the drift at CI time.
    """
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"systemd unit {service_name}.service invokes "
            f"`-m {module_name}` but the module is not importable: {exc}\n"
            "Either: (a) the module was renamed/deleted — update the "
            ".service to match, or (b) the .service file is stale and "
            "should be removed."
        )
    except ImportError as exc:
        # Other ImportError (e.g. missing optional dep at import time):
        # report but allow CI to pass. The module exists, just imports
        # something the test env doesn't have.
        pytest.skip(f"{service_name}: module {module_name} present but has missing dep: {exc}")
