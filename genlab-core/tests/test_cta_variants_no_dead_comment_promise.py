"""Pin: Instagram CTA variants must NOT promise "1st comment".

The 2026-06-19 hardcoded pivot at `monetization/cta_engine.py:281` only
fixed the fallback branch. The bandit-pick branch (lines 282-292)
overrides the fallback with a YAML-driven template — and the YAML kept
saying "1st comment". Net effect in prod: published IG captions still
ended with "— 1st comment" because the bandit (almost always) succeeds
in picking a variant.

Bug context: IG's `platforms/instagram.py` has `post_reply` (for the
engagement engine) but the PUBLISH flow never calls `post_comment`. So
every "1st comment" CTA on IG is a dead promise — viewers tap through
to find no first comment, kills click-through, hurts trust signal.

This pin asserts that no IG bandit variant template contains "1st
comment" or "first comment", catching the regression class
"someone re-added the 2026-05-18 templates by hand or via copy-paste".

YouTube + Facebook variants intentionally untested — they use direct
URL inlining (`{url}` placeholder), no "first comment" promise to
break. Threads variants intentionally untested — `first reply` IS
delivered via `base_platform_adaptation.py:133`.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_variants():
    path = Path(__file__).parent.parent / "config" / "cta_variants.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


class TestIGVariantsNoCommentPromise:
    def test_no_ig_variant_promises_1st_comment(self):
        """Pin: every IG variant template must not contain '1st comment'."""
        cfg = _load_variants()
        ig_variants = cfg["platforms"]["instagram"]["variants"]
        offenders = []
        for v in ig_variants:
            template = (v.get("template") or "").lower()
            if "1st comment" in template or "first comment" in template:
                offenders.append(v.get("arm_id"))
        assert not offenders, (
            f"IG variants {offenders} promise '1st comment' — but the IG "
            f"publish flow does NOT post a first comment. Update templates "
            f"to 'link in bio' (the working monetization surface)."
        )

    def test_all_ig_variants_route_to_bio(self):
        """Pin: every IG variant must contain 'link in bio' so viewers
        know where the actual link lives. Future-proofs against 'forgot
        to mention WHERE to find the link' templates that read like
        truncated copy."""
        cfg = _load_variants()
        ig_variants = cfg["platforms"]["instagram"]["variants"]
        offenders = []
        for v in ig_variants:
            template = (v.get("template") or "").lower()
            if "link in bio" not in template:
                offenders.append(v.get("arm_id"))
        assert not offenders, (
            f"IG variants {offenders} don't tell viewers where the link "
            f"is. Every IG CTA must contain 'link in bio'."
        )

    def test_ig_arm_ids_unchanged_for_bandit_state_continuity(self):
        """Pin: arm_ids must NOT be renamed — bandit state (alpha/beta)
        is keyed on arm_id. Renaming kicks the affected arm back to
        cold-start uniform priors, destroying weeks of learned
        click-through data. The original 4 arm_ids from 2026-05-18 must
        survive the 2026-06-22 template swap intact."""
        cfg = _load_variants()
        ig_variants = cfg["platforms"]["instagram"]["variants"]
        arm_ids = {v.get("arm_id") for v in ig_variants}
        expected = {
            "ig_first_comment",
            "ig_get_yours_v2",
            "ig_best_deal_v2",
            "ig_we_use_v2",
        }
        missing = expected - arm_ids
        assert not missing, (
            f"IG bandit arm_ids missing: {missing}. Renaming arm_ids "
            f"destroys learned click-through data. Update templates "
            f"in place, never rename arm_ids."
        )

    def test_threads_variants_kept_intact(self):
        """Pin: threads 'first reply' templates are NOT affected by the
        IG pivot. `base_platform_adaptation.py:133` actually wires
        `result.first_comment` to `tw["first_reply"]` so threads's
        'first reply' CTA IS delivered. Don't accidentally regress
        threads templates while fixing IG."""
        cfg = _load_variants()
        th_variants = cfg["platforms"]["threads"]["variants"]
        has_first_reply = sum(
            1 for v in th_variants if "first reply" in (v.get("template") or "").lower()
        )
        assert has_first_reply == 3, (
            f"Threads must keep 3 'first reply' variants intact, got {has_first_reply}"
        )
