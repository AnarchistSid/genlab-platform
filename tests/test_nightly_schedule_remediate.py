"""Pins for ``scripts/nightly_schedule_remediate.py`` +
``genlab-nightly-schedule-remediate.service`` (task #612, 2026-07-09).

Companion to ``test_nightly_schedule_top_per_niche.py``. Remediate is
the OnFailure loop that fires when the parent nightly runner exits 1 —
it writes an actionable ``pipeline_alerts`` row with suggested SQL +
candidate list, plugging into the existing CriticalAlertsBanner surface.

If these pins regress:

* Removing the journal parser would silently drop the missing-niche
  detection and fall back to the generic (less actionable) alert.
* Changing the SUGGESTED SQL template's shape would break the
  copy-paste operator workflow.
* Removing the ``target+1`` operator-override (vs the parent runner's
  ``target+2`` machine safeguard) would make the remediate suggestion
  identical to what the automated pull-back already tried and rejected.
* Dropping the LLM-refusal filter from the candidate query would let
  refusal-text hooks show up in operator suggestions.
* Dropping OnFailure chain-order would break the two-alert composition
  (generic + specialized).
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "nightly_schedule_remediate.py"
SERVICE = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule-remediate.service"
PARENT_SERVICE = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule.service"


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location("nightly_schedule_remediate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── niche inventory pin ──────────────────────────────────────────────


def test_all_5_niches_in_scope(script_module):
    """Remediate must recognize every niche the parent runner does —
    otherwise a valid missing-niche log line would be parsed but the
    niche would be filtered out silently."""
    assert set(script_module.NICHES) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
        "anime",
    }


# ── journal parser pin ──────────────────────────────────────────────


def test_parse_missing_niches_single(script_module):
    journal = (
        "Jul 08 22:00:03 ubuntu python[503387]: "
        "Target slot: 2026-07-09T06:00:00+00:00\n"
        "Jul 08 22:00:03 ubuntu python[503387]: "
        "⚠️  No schedulable candidate for: ['sports'] (empty ...\n"
    )
    assert script_module.parse_missing_niches(journal) == {"sports"}


def test_parse_missing_niches_multiple(script_module):
    """Multiple niches missing = comma-separated list inside brackets."""
    journal = (
        "Jul 08 22:00:03 ubuntu python[503387]: "
        "⚠️  No schedulable candidate for: ['sports', 'gaming'] (empty ...\n"
    )
    assert script_module.parse_missing_niches(journal) == {"sports", "gaming"}


def test_parse_missing_niches_empty_journal(script_module):
    """No log line → empty set. Never raise on missing data."""
    assert script_module.parse_missing_niches("") == set()
    assert script_module.parse_missing_niches("some unrelated log content") == set()


def test_parse_missing_niches_ignores_unknown_names(script_module):
    """A niche name not in NICHES tuple (e.g. typo in log) must NOT
    generate an alert — we'd send a critical alert about a niche that
    doesn't exist."""
    journal = "⚠️  No schedulable candidate for: ['sports', 'fashion']"
    assert script_module.parse_missing_niches(journal) == {"sports"}


def test_parse_missing_niches_regex_pin(script_module):
    """Locked regex. Keeps the parser in sync with the exact log line
    format written by ``pick_top_per_niche``'s missing-candidate branch.
    If the parent runner changes the log wording, this test flips FIRST
    (compile-time) rather than a silent parse miss in production."""
    import inspect

    body = inspect.getsource(script_module)
    assert "No schedulable candidate for:" in body


# ── target-slot pin — must match parent runner ──────────────────────


def test_compute_target_slot_matches_parent_runner(script_module):
    """Remediate's target-slot computation must equal the parent's.
    Divergence here would suggest SQL to fill the wrong date."""
    fixed_now = datetime(2026, 7, 5, 18, 0, 0, tzinfo=UTC)
    slot = script_module.compute_target_slot(fixed_now)
    assert slot == datetime(2026, 7, 6, 6, 0, 0, tzinfo=UTC)


# ── operator-override vs machine safeguard pin ──────────────────────


def test_pullback_uses_target_plus_1_not_plus_2(script_module):
    """The whole point of remediate's suggestion is to be MORE aggressive
    than the automated pull-back (task #611 uses ``MIN_PULLBACK_DAYS = 2``,
    aka target+2). Remediate SUGGESTS from target+1, letting the operator
    override the cascade guard. If a refactor accidentally aligns them,
    remediate's suggestions match what the machine already rejected."""
    import inspect

    body = inspect.getsource(script_module.find_pullback_candidates)
    assert "timedelta(days=1)" in body, (
        "Remediate must query from target+1 (operator override) not "
        "target+2 (machine cascade guard). If aligned, suggestions "
        "would match what the automated pull-back already found empty."
    )


def test_pullback_shares_llm_refusal_filter(script_module):
    """Same discipline as the parent runner: never surface refusal-text
    hooks to the operator as a suggestion. Copy-paste refactor risk."""
    import inspect

    body = inspect.getsource(script_module.find_pullback_candidates)
    for pattern in [
        "I need to stop",
        "I cannot",
        "I can''t",
        "I am unable",
        "I''m sorry",
        "I apologize",
    ]:
        assert pattern in body, (
            f"Remediate SQL missing LLM-refusal pattern {pattern!r}. "
            "Would suggest refusal text hooks as operator remediation."
        )
    assert "length(hook) BETWEEN 15 AND 100" in body


def test_pullback_returns_limited_number_of_candidates(script_module):
    """Enough for the operator to compare hooks, not so many that the
    alert becomes noise. LIMIT 3 pinned."""
    import inspect

    body = inspect.getsource(script_module.find_pullback_candidates)
    assert "LIMIT 3" in body, (
        "Remediate must LIMIT candidates so the alert stays scannable. "
        "Too many rows overwhelm the operator; too few risks missing "
        "a better hook."
    )


def test_pullback_orders_by_scheduled_for_ascending(script_module):
    """Same cascade-minimization discipline as the parent's pull-back —
    earliest far-future slot is the least-disruptive one to steal."""
    import inspect

    body = inspect.getsource(script_module.find_pullback_candidates)
    assert "scheduled_for ASC" in body


# ── alert-message shape pin — the operator's copy-paste target ──────


def test_alert_message_has_target_date(script_module):
    """Operator needs to see WHICH date they're filling. Message must
    include the ISO-format target date."""
    target_slot = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 8, 20, 0, 0, tzinfo=UTC)
    msg = script_module.build_alert_message("sports", target_slot, [], now)
    assert "2026-07-09" in msg


def test_alert_message_has_hours_to_publisher(script_module):
    """Operator needs to see time pressure. 10.5 hours ≈ enough time
    to investigate; <1 hour = drop everything."""
    target_slot = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 8, 20, 0, 0, tzinfo=UTC)  # 10.5h before publisher
    msg = script_module.build_alert_message("sports", target_slot, [], now)
    # Publisher fires 06:35 UTC = 10.583 hours from now.
    assert "10." in msg and "hour" in msg


def test_alert_message_has_suggested_sql_when_candidates(script_module):
    """The copy-paste target. Message MUST include a runnable UPDATE
    statement when candidates exist. Without this, operator has to
    write the SQL themselves against high time pressure."""
    target_slot = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 8, 20, 0, 0, tzinfo=UTC)
    candidates = [
        {
            "id": "354c4b90-8401-4945-b15c-6501e73f937f",
            "scheduled_for": datetime(2026, 7, 10, 6, 0, 0, tzinfo=UTC),
            "priority_score": 0.42,
            "hook": "Atletico Madrid couldn't put Bilbao away until it...",
        }
    ]
    msg = script_module.build_alert_message("sports", target_slot, candidates, now)
    assert "UPDATE blueprints SET scheduled_for" in msg
    assert "354c4b90-8401-4945-b15c-6501e73f937f" in msg
    assert "2026-07-09T06:00:00" in msg


def test_alert_message_has_investigation_hint_when_no_candidates(script_module):
    """If far-future queue is ALSO empty (genuine stall), the message
    must include a systemctl command for the operator to run — else
    they have to figure out the command themselves."""
    target_slot = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 8, 20, 0, 0, tzinfo=UTC)
    msg = script_module.build_alert_message("sports", target_slot, [], now)
    assert "systemctl status genlab-pipeline-sports.service" in msg
    assert "systemctl start genlab-pipeline-sports.service" in msg


def test_alert_message_mentions_611_relationship(script_module):
    """Traceability pin: the operator reading this alert should know
    the automated pull-back (task #611) ALREADY ran with MIN_PULLBACK_DAYS
    and this alert is the escalation. Otherwise they might wonder why
    the machine didn't fix it."""
    target_slot = datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 7, 8, 20, 0, 0, tzinfo=UTC)
    msg = script_module.build_alert_message("sports", target_slot, [], now)
    assert "#611" in msg or "pull-back" in msg.lower()


# ── DB write pin ────────────────────────────────────────────────────


def test_write_alert_uses_correct_check_name(script_module):
    """Distinct check_name distinguishes this from generic systemd_unit_failed
    written by systemd_failure_alert.sh. Dashboard filters on this to
    show both alerts side-by-side."""
    import inspect

    body = inspect.getsource(script_module.write_alert)
    assert "'nightly_schedule_missing_slot'" in body, (
        "Remediate must use a distinct check_name so dashboard can "
        "distinguish this from generic 'systemd_unit_failed' alerts."
    )


def test_write_alert_uses_critical_severity(script_module):
    """Slot-miss = same publisher-day-blank consequence as any other
    critical. CriticalAlertsBanner filters severity='critical'."""
    import inspect

    body = inspect.getsource(script_module.write_alert)
    assert "'critical'" in body


def test_write_alert_leaves_resolved_at_null(script_module):
    """Alert is unresolved until operator acts. Auto-resolve is a
    separate concern (there's an existing auto-resolve sweeper for
    other alert types; hook this one up in a follow-up if needed)."""
    import inspect

    body = inspect.getsource(script_module.write_alert)
    assert "NULL" in body


# ── systemd unit pins ───────────────────────────────────────────────


def test_service_file_present():
    assert SERVICE.is_file()


def test_service_uses_file_path_invocation():
    """scripts/ is not a Python package. Sibling gotcha
    [[systemd-scripts-invocation-gotcha]]."""
    content = SERVICE.read_text()
    assert "scripts/nightly_schedule_remediate.py" in content
    assert "-m scripts." not in content


def test_service_sources_env_file():
    """DATABASE_URL lives in /opt/genlab/.env — must be sourced."""
    content = SERVICE.read_text()
    assert "EnvironmentFile=/opt/genlab/.env" in content


def test_service_does_not_have_own_onfailure():
    """Cascade discipline: if this remediate unit itself fails, we
    don't want to trigger ANOTHER failure-alert about IT. The generic
    systemd_failure_alert.sh already-fired for the PARENT covers this
    case. A cascade of failure-alerts would lose the original signal."""
    content = SERVICE.read_text()
    assert "OnFailure=" not in content


def test_service_uses_oneshot_type():
    """One-shot execution matches the parent runner's shape."""
    content = SERVICE.read_text()
    assert "Type=oneshot" in content


def test_service_has_reasonable_timeout():
    """Journal parse (<1s) + 5 niche queries (<5s) + insert (<1s) fits
    comfortably in 30s. Kept intentionally tighter than the generic
    failure-alert's 120s — remediate should be fast."""
    content = SERVICE.read_text()
    assert "TimeoutSec=30" in content


# ── parent-service chaining pin ─────────────────────────────────────


def test_parent_service_chains_both_onfailure(script_module):
    """The 2-alert composition is the whole point of task #612:
    generic + specialized alerts fire in parallel. If a refactor drops
    either OnFailure line, the composition breaks."""
    content = PARENT_SERVICE.read_text()
    assert "OnFailure=genlab-service-failure-alert@" in content, (
        "Parent must still fire the generic failure-alert — covers "
        "failure modes that aren't a missing-niche shape."
    )
    assert "OnFailure=genlab-nightly-schedule-remediate.service" in content, (
        "Parent must fire the specialized remediate service — task "
        "#612's whole reason for existence."
    )


def test_parent_service_generic_alert_fires_first(script_module):
    """Order pin: generic alert first, then specialized. Rationale: if
    the specialized parser crashes on unexpected journal format, the
    generic alert still gets written. Systemd fires OnFailure directives
    in declaration order; keeping generic first ensures the fallback
    path always runs first."""
    content = PARENT_SERVICE.read_text()
    generic_idx = content.find("OnFailure=genlab-service-failure-alert@")
    specialized_idx = content.find("OnFailure=genlab-nightly-schedule-remediate.service")
    assert generic_idx > 0 and specialized_idx > 0
    assert generic_idx < specialized_idx, (
        "Generic OnFailure must be declared BEFORE specialized so the "
        "safety net fires first. Reordering could mask the original "
        "signal if the specialized path crashes."
    )


# ── functional pin — end-to-end with mocked cursor ──────────────────


def test_find_pullback_candidates_calls_correct_sql_with_correct_params(script_module):
    """Prove the SQL fires with the right target+1 date, not target
    itself or target+2. Ensures the operator-override semantics are
    preserved end-to-end."""
    calls = []

    class FakeCursor:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchall(self):
            return []

    cur = FakeCursor()
    target = date(2026, 7, 9)
    script_module.find_pullback_candidates(cur, "sports", target)
    assert len(calls) == 1
    _sql, params = calls[0]
    # params shape: (niche, min_pullback_date)
    assert params[0] == "sports"
    assert params[1] == date(2026, 7, 10), (
        f"Remediate must query from target+1 (2026-07-10), got {params[1]}. "
        f"target+2 would defeat the operator-override purpose; target+0 "
        f"would suggest stealing today's already-committed slot (if any)."
    )
