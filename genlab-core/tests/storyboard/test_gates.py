"""Plan-time gates. Each was learned by rendering something and measuring it wrong."""


from genlab_core.storyboard.gates import check, failures
from genlab_core.storyboard.models import (
    BeatGrid,
    ClassifierVerdict,
    EffectEvent,
    Scope,
    Shot,
    Storyboard,
    SubjectPlan,
    Treatment,
    WindowPlan,
)

FPS, N = 30, 96


def _sb(**kw):
    shots = kw.pop("shots", None)
    if shots is None:
        # The approved hit3s: 3.2s, ONE continuous shot. Zero cuts was a
        # selection criterion, so a segment has nothing to cut between.
        shots = [Shot(index=0, start_frame=0, end_frame=N - 1, magnification=2.05,
                      centre_x=0.5, centre_y=0.5)]
    events = kw.pop("events", None)
    if events is None:
        events = ([EffectEvent(kind="aura", frame=f) for f in range(0, 84, 12)]
                  + [EffectEvent(kind="white_flash", frame=13)])
    base = dict(
        niche_id="sports", scope=Scope.SEGMENT, fps=FPS, total_frames=N,
        classifier=ClassifierVerdict(treatment=Treatment.ACTION, speech_ratio=0.1,
                                     motion_energy=12.0, face_persistence=0.4,
                                     confidence=0.9, reason="test"),
        window=WindowPlan(start_s=674.9, duration_s=3.2, motion=11.4, cuts=0,
                          subject_hold=1.0, finish_frame=13),
        subject=SubjectPlan(hue_deg=235.0, votes=10, vote_frames=10, unanimous=True),
        beats=BeatGrid(bpm=150.0, first_beat_s=0.0, period_frames=12.0, fps=FPS),
        full_bleed_floor=2.0361, shots=shots, events=events)
    base.update(kw)
    return Storyboard(**base)


def _named(sb, name):
    return next(r for r in check(sb) if r.name == name)


def test_the_approved_plan_passes_every_gate():
    assert failures(_sb()) == [], [str(f) for f in failures(_sb())]


def test_one_clip_held_for_a_whole_REEL_fails_shot_density():
    """What this platform published every day since September 9.

    Gated at REEL scope only: a SEGMENT is one shot by construction, and gating
    segments on cuts/s would reject every correctly-chosen zero-cut window.
    """
    one = [Shot(index=0, start_frame=0, end_frame=N - 1, magnification=2.05,
                centre_x=0.5, centre_y=0.5)]
    r = _named(_sb(scope=Scope.REEL, shots=one), "shot_density")
    assert not r.passed and "cuts/s" in r.detail


def test_the_same_single_shot_passes_at_SEGMENT_scope():
    one = [Shot(index=0, start_frame=0, end_frame=N - 1, magnification=2.05,
                centre_x=0.5, centre_y=0.5)]
    assert _named(_sb(scope=Scope.SEGMENT, shots=one), "shot_density").passed


def test_a_flash_on_the_last_frames_fails_flash_at_finish():
    """v3's real defect: anchored to peak motion, fired on frames 93-95."""
    ev = [EffectEvent(kind="aura", frame=f) for f in range(0, 84, 12)]
    ev += [EffectEvent(kind="drawing_flash", frame=f) for f in (93, 94, 95)]
    r = _named(_sb(events=ev), "flash_at_finish")
    assert not r.passed and "finish f13" in r.detail


def test_overlays_in_the_tail_fail_ends_live():
    ev = [EffectEvent(kind="aura", frame=f) for f in range(0, 84, 12)]
    ev += [EffectEvent(kind="mega_bolt", frame=90)]
    r = _named(_sb(events=ev), "ends_live")
    assert not r.passed and "90" in r.detail


def test_a_shot_below_the_full_bleed_floor_is_rejected_before_rendering():
    shots = [Shot(index=0, start_frame=0, end_frame=47, magnification=1.30,
                  centre_x=0.5, centre_y=0.5),
             Shot(index=1, start_frame=48, end_frame=95, magnification=2.05,
                  centre_x=0.5, centre_y=0.5)]
    r = _named(_sb(shots=shots), "magnification_floor")
    assert not r.passed and "[0]" in r.detail


def test_a_split_subject_vote_is_rejected():
    r = _named(_sb(subject=SubjectPlan(hue_deg=15.0, votes=5, vote_frames=10)),
               "subject_vote")
    assert not r.passed and "50%" in r.detail


def test_two_drawing_flashes_read_as_a_transition():
    ev = [EffectEvent(kind="aura", frame=f) for f in range(0, 84, 12)]
    ev += [EffectEvent(kind="drawing_flash", frame=f) for f in (13, 14, 40, 41)]
    r = _named(_sb(events=ev), "plate_once")
    assert not r.passed and "2 drawing-flash run" in r.detail


def test_a_late_hook_fails_at_REEL_scope():
    ev = [EffectEvent(kind="aura", frame=f) for f in range(40, 84, 10)]
    r = _named(_sb(scope=Scope.REEL, events=ev), "hook_onset")
    assert not r.passed


def test_a_long_quiet_run_fails():
    ev = [EffectEvent(kind="aura", frame=0), EffectEvent(kind="aura", frame=80)]
    r = _named(_sb(events=ev), "quiet_run")
    assert not r.passed and "longest gap" in r.detail


def test_STILL_is_not_gated_on_cuts_or_cadence():
    """The footage-free path holds one image on purpose."""
    cls = ClassifierVerdict(treatment=Treatment.STILL, speech_ratio=0.0,
                            motion_energy=0.2, face_persistence=0.0,
                            confidence=0.8, reason="no footage")
    one = [Shot(index=0, start_frame=0, end_frame=N - 1, magnification=2.05,
                centre_x=0.5, centre_y=0.5)]
    sb = _sb(classifier=cls, shots=one,
             events=[EffectEvent(kind="caption", frame=3)])
    assert _named(sb, "shot_density").passed
    assert _named(sb, "quiet_run").passed


def test_check_returns_the_whole_table_not_the_first_failure():
    ev = [EffectEvent(kind="drawing_flash", frame=f) for f in (93, 94, 95)]
    one = [Shot(index=0, start_frame=0, end_frame=N - 1, magnification=1.0,
                centre_x=0.5, centre_y=0.5)]
    rs = check(_sb(scope=Scope.REEL, shots=one, events=ev))
    assert len(rs) == 9
    assert sum(1 for r in rs if not r.passed) >= 4


def test_gate_result_prints_as_a_table_row():
    r = check(_sb())[0]
    assert str(r).startswith("[PASS]") and r.name in str(r)
