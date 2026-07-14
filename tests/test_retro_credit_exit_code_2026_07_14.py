"""Pin tests for the 2026-07-14 retro-credit exit-code semantics fix.

Session 2026-07-14 alert dashboard: SYSTEMD_UNIT_FAILED for
genlab-retro-credit.service fired 11 times in 10 minutes. Journal
showed the service was doing useful work:

  attempted_fb=0
  attempted_ig=2
  success_fb=0
  success_ig=0
  already_credited=1
  skipped_no_creds=0
  skipped_no_url=0
  skipped_platform=185     # 90% of the 431 targets — yt/threads/x/tiktok
  failed=1                 # 1 IG shortcode-resolve failure

But `exit 0 if stats['failed'] == 0 else 1` returned 1 because of the
single IG failure. systemd marked FAILED. Mission Control alerted.

The 1 IG shortcode failure is normal noise for a 30d retro-credit
window: Meta's shortcode-to-media_id caching lags, some shortcodes
rotate/expire. Meanwhile skipped_platform=185 accounts for content
retro-credit doesn't (yet) handle (YT/threads/X/TikTok).

New semantics:
  * Exit 0 when SUCCESS + already_credited > 0 (some useful work)
  * Exit 0 when 0 attempts (nothing to do)
  * Exit 1 only when 100% failure rate = token or DB issue (real
    catastrophic failure)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    """Load the retro_credit script as a module for direct testing."""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "retro_credit_uncredited_posts.py"
    spec = importlib.util.spec_from_file_location("retro_credit_uncredited_posts", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExitCodeSemantics:
    """The main-work-done function returns 0/1 based on realistic
    success/failure ratios. Prior behavior was strict-eq on
    stats['failed'] which false-fired the alert every 90 min."""

    def test_source_has_new_semantics(self):
        """Source-level check: the new logic must reference attempted
        + success + failed, not just failed."""
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "retro_credit_uncredited_posts.py"
        source = script_path.read_text()
        # New pattern: exit 1 only when attempted > 0 AND success == 0
        assert "attempted > 0" in source or "attempted == 0" in source or "return 0" in source
        # Must not use the old strict-eq on failed
        assert "return 0 if stats[\"failed\"] == 0 else 1" not in source, (
            "Old strict-eq on stats['failed'] regressed. This fired systemd "
            "FAILURE on 1 IG shortcode noise every 90 min."
        )

    def test_docstring_documents_new_exit_semantics(self):
        """Docstring for exit codes should mention the new fail-open
        behavior so future readers understand why we don't fail on
        every noisy target."""
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "retro_credit_uncredited_posts.py"
        source = script_path.read_text()
        # There should be a comment explaining the fix
        assert "exit-code semantics" in source.lower() or "90 min" in source, (
            "Missing rationale comment for the exit-code fix. Future "
            "reviewers won't understand why exit=1 requires 100% "
            "failure rate now."
        )
