"""Pin the contract between ConfigUpdater (producer) and
run_config_update.py (consumer) on the change-dict key names.

The bug this pins against: producer emitted ``key``/``old_value``/
``new_value`` for months; consumer (the script's logger + persist
loop) read ``field``/``old``/``new``. Result: script crashed with
KeyError on the first real change ever produced — verified on prod
2026-06-14 immediately after PR #210 unblocked hydration.

Both halves of the contract are pinned here. If either side renames
a key in isolation, this test fails before the silent breakage
hits prod again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRODUCER = _REPO_ROOT / "genlab-core" / "src" / "genlab_core" / "learning" / "config_updater.py"
_CONSUMER = _REPO_ROOT / "genlab-core" / "src" / "genlab_core" / "scripts" / "run_config_update.py"

# Canonical keys the producer writes. Pinning the SET — order is
# unconstrained by Python dict semantics.
_CANONICAL_KEYS = {"file", "key", "old_value", "new_value", "n", "reason"}


class TestProducerEmitsCanonicalKeys:
    def test_change_dicts_contain_canonical_keys(self):
        """``ConfigUpdater._update_*`` methods build the change dict
        literal with the same shape. We can't easily call them
        without a niche fixture, so pin the literal text instead.
        Catches the most common drift: someone copy-pastes a change
        dict from elsewhere with stale key names."""
        src = _PRODUCER.read_text()
        for key in _CANONICAL_KEYS:
            assert f'"{key}":' in src, (
                f"ConfigUpdater dropped the ``{key}`` key from a change dict. "
                f"Producer must emit all of {sorted(_CANONICAL_KEYS)} so the "
                "script consumer + the config_updates table writer line up."
            )

    def test_change_dicts_do_not_emit_old_short_names(self):
        """Anti-pin: producer must NOT emit the old short-name keys
        that the consumer used to read. If they reappear, someone
        regressed to the pre-fix shape — and even if both sides
        compile, the AUDIT TRAIL split between two key names becomes
        a future-debugging nightmare. Pin ONE canonical set."""
        src = _PRODUCER.read_text()
        for old_name in ('"field":', '"old":', '"new":'):
            # ``"old":`` is plausibly inside a dict key for another
            # concept; bias the assertion to the change-dict context
            # by looking for the immediate vicinity of ``"file":``.
            # If the assertion gets noisy, narrow further.
            assert old_name not in src, (
                f"Producer re-introduced the legacy short key {old_name!r}. "
                "The consumer (run_config_update.py) was updated to read "
                "the canonical names; reintroducing the short forms here "
                "splits the change-dict shape across two contracts again."
            )


class TestConsumerReadsCanonicalKeys:
    def test_script_logger_uses_canonical_keys(self):
        """The KeyError-crashed code path. Must access by the
        canonical names."""
        src = _CONSUMER.read_text()
        # The logger.info loop accesses `change["key"]` etc.
        assert 'change["key"]' in src, (
            'script logger no longer reads ``change["key"]`` — '
            "this is the crashed path the PR fixed; the regression "
            "would re-introduce the KeyError that blocked the "
            "config_updater on prod for months."
        )
        assert 'change["new_value"]' in src
        assert 'change["old_value"]' in src

    def test_script_persist_uses_canonical_keys(self):
        """The _persist_changes branch writes to config_updates table.
        The DB columns are ``field``/``old_value``/``new_value`` but
        the dict-side lookups must use the canonical change-dict
        keys (``key``/``old_value``/``new_value``)."""
        src = _CONSUMER.read_text()
        assert 'change.get("key"' in src, (
            '_persist_changes no longer reads change.get("key") — '
            "would write NULL/empty into the config_updates.field "
            "column, making the dashboard's Config Updates tab "
            "show blank field names on every row."
        )
        assert 'change.get("old_value"' in src
        assert 'change.get("new_value"' in src

    def test_script_does_not_read_legacy_short_keys(self):
        """Anti-pin for the consumer: the short-name lookups that
        caused the prod KeyError must not return. ``change["field"]``
        / ``change["old"]`` / ``change["new"]`` were the broken shape."""
        src = _CONSUMER.read_text()
        for bad in (
            'change["field"]',
            'change["new"]',
            'change["old"]',
            'change.get("field"',
            'change.get("old"',
            'change.get("new"',
        ):
            # ``change.get("old"`` is also matched by ``change.get("old_value"``
            # — strip the longer form to detect ONLY the truly-bad short form.
            for line in src.splitlines():
                if bad in line and re.search(
                    r"\bchange\.get\(\"old_value\"|change\.get\(\"new_value\"", line
                ):
                    continue
                if bad in line:
                    pytest.fail(
                        f"consumer re-introduced the legacy lookup {bad!r} on a "
                        f"line not matched by the canonical longer form. "
                        f"Full line: {line.strip()}"
                    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
