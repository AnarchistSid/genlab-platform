"""Provenance routes; the classifier is advisory and scored against it."""

from __future__ import annotations

from genlab_core.action import router as R


def c(**kw) -> dict:
    base = {
        "video_id": "vid",
        "title": "",
        "download_url": "https://x/y.mp4",
        "is_highlight": False,
    }
    return {**base, **kw}


# ───────────────────────── the order of the rules ───────────────────────────


def test_no_footage_beats_everything():
    """Nothing about a title or a score turns a clipless blueprint into ACTION."""
    d = R.route(c(is_highlight=True, download_url="", title="Knockout of the year"))
    assert d.treatment == "STILL" and "no footage" in d.reason


def test_talk_provenance_beats_the_highlight_flag():
    """A post-match interview on an official channel is still a microphone."""
    d = R.route(c(is_highlight=True, title="EXCLUSIVE: Penta reacts to his match"))
    assert d.treatment == "TALK"


def test_reddit_provenance_routes_to_talk():
    d = R.route(c(is_highlight=True, download_url="https://v.redd.it/abc123"))
    assert d.treatment == "TALK" and "redd" in d.reason


def test_a_highlight_with_a_usable_source_is_action():
    d = R.route(c(is_highlight=True, title="Raw highlights"), source_score=0.72)
    assert d.treatment == "ACTION" and "0.72" in d.reason


def test_a_highlight_the_template_cannot_frame_is_not_action():
    """is_highlight alone let in wide hard-cam footage. The source score is the
    measurement that catches it, and WWE hard-cam is the clip it was built on."""
    d = R.route(c(is_highlight=True, title="Raw highlights"), source_score=0.21)
    assert d.treatment == "STILL" and "cannot frame" in d.reason


def test_unflagged_footage_lands_on_still_not_action():
    """STILL is the safe landing: its treatment works on any footage, where
    ACTION on a lectern produces a fight that is not there."""
    assert R.route(c(title="Some clip")).treatment == "STILL"


def test_an_unscored_highlight_still_routes_action_but_says_so():
    d = R.route(c(is_highlight=True, title="highlights"))
    assert d.treatment == "ACTION" and "unscored" in d.reason


# ───────────────────────── the classifier rides along ───────────────────────


def test_the_classifier_never_changes_the_route():
    """67% on real material. It watches; it does not drive."""
    agree = R.route(
        c(is_highlight=True, title="highlights"), source_score=0.8, classifier_verdict="ACTION"
    )
    disagree = R.route(
        c(is_highlight=True, title="highlights"), source_score=0.8, classifier_verdict="TALK"
    )
    assert agree.treatment == disagree.treatment == "ACTION"
    assert agree.classifier_agrees is True
    assert disagree.classifier_agrees is False


def test_no_classifier_verdict_is_not_a_disagreement():
    d = R.route(c(is_highlight=True), source_score=0.8)
    assert d.classifier_agrees is None


def test_the_decision_is_persistable_beside_the_plan():
    d = R.route(
        c(is_highlight=True, title="highlights"), source_score=0.8, classifier_verdict="TALK"
    )
    note = d.as_storyboard_note()
    assert note["routed_by"] == "provenance"
    assert note["routed_treatment"] == "ACTION"
    assert note["classifier_verdict"] == "TALK"
    assert note["classifier_agrees"] is False


# ───────────────────────── the agreement report ─────────────────────────────


def test_agreement_is_reported_per_fire():
    ds = [
        R.route(c(is_highlight=True), source_score=0.9, classifier_verdict="ACTION"),
        R.route(c(is_highlight=True), source_score=0.9, classifier_verdict="TALK"),
        R.route(c(title="interview"), classifier_verdict="TALK"),
    ]
    rep = R.agreement_report(ds)
    assert rep["n"] == 3
    assert rep["agreement"] == round(2 / 3, 4)
    assert rep["by_treatment"]["ACTION"] == {"n": 2, "agree": 1}


def test_a_fire_with_no_classifier_verdicts_reports_none_not_zero():
    """0.0 agreement and 'no data' are different states; conflating them would
    read as the classifier failing when it simply did not run."""
    rep = R.agreement_report([R.route(c(is_highlight=True), source_score=0.9)])
    assert rep["n"] == 0 and rep["agreement"] is None


def test_disagreements_are_listed_so_the_corpus_grows():
    ds = [
        R.route(
            c(video_id="v1", is_highlight=True),
            source_score=0.9,
            classifier_verdict="STILL",
            classifier_signals={"video_id": "v1"},
        )
    ]
    rep = R.agreement_report(ds)
    assert rep["disagreements"][0]["routed"] == "ACTION"
    assert rep["disagreements"][0]["classifier"] == "STILL"


# ───────────────────────── kits ─────────────────────────────────────────────


def test_an_unknown_sport_gets_no_kit_rather_than_a_guess():
    """A wrong kit is worse than no kit -- cricket must not fall through to the
    ball family, which is why family_for_niche returns None."""
    assert R.route(c(is_highlight=True), source_score=0.9, sport="kabaddi").kit_family is None
