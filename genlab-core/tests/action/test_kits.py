"""Pins for the sport-family kits.

Two of these are safety properties, not preferences:
  * cricket must never be routed into the footage path
  * racing must never place a negative click on the other competitor
Both are the kind of thing a later edit silently flips.
"""

from genlab_core.action.kits import KIT_BY_FAMILY, family_for_niche, load_kit


def test_every_kit_loads():
    assert set(KIT_BY_FAMILY) >= {"impact", "speed", "ball", "grace", "cricket"}
    for fam in KIT_BY_FAMILY:
        load_kit(fam)


def test_cricket_is_footage_free():
    """BCCI/ICC are among the most hostile holders measured. Cricket gets its own
    family precisely so it cannot fall through to `ball` and pick up a ball trail."""
    assert load_kit("cricket").footage_allowed is False
    assert family_for_niche("cricket") == "cricket"
    assert family_for_niche("cricket") != "ball"


def test_only_cricket_is_footage_free():
    for fam in KIT_BY_FAMILY:
        if fam == "cricket":
            continue
        assert load_kit(fam).footage_allowed is True, fam


def test_racing_never_treats_the_other_competitor_as_noise():
    """In combat the second person is noise to exclude from the matte. In racing
    the other car IS the moment -- a negative click there tells the tracker to
    exclude the thing the shot is about."""
    assert load_kit("speed").second_person_is_noise is False
    assert load_kit("impact").second_person_is_noise is True


def test_helmet_sports_do_not_claim_face_protection():
    assert load_kit("speed").face_protected is False
    for fam in ("impact", "ball", "grace"):
        assert load_kit(fam).face_protected is True, fam


def test_unknown_sport_returns_none_not_a_guess():
    """A wrong kit is worse than no kit."""
    assert family_for_niche("curling") is None
    assert family_for_niche("") is None


def test_grace_runs_at_a_lower_tempo_with_bolts_off():
    g = load_kit("grace")
    assert g.beat["bolts_on"] is None
    assert g.beat["bpm_range"][1] <= load_kit("impact").beat["bpm_range"][0]


def test_ball_declares_the_unbuilt_dependency():
    """The kit must say out loud that it needs work that does not exist yet."""
    assert load_kit("ball").subject.get("requires_small_object_tracking") is True
    assert load_kit("ball").status == "last"


# ── a kit names methods and targets, never clip assets (ACTION-UFC-03) ──


def test_no_kit_carries_a_file_path():
    """A stored asset is ANOTHER clip's asset.

    The drawing must be regenerated from the clip's own contact frame; pointing
    a kit at v9/draw024_mask.png would ship a WWE wrestler's likeness into every
    reel built with the impact kit.
    """
    import pathlib
    import re

    here = pathlib.Path(
        load_kit("impact").__class__.__module__
        and __import__("genlab_core.action.kits.registry", fromlist=["x"]).__file__
    ).parent
    bad = []
    for y in sorted(here.glob("*.yaml")):
        for i, line in enumerate(y.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"\.(png|jpg|jpeg|mp4|mov|wav|npy)\b", line):
                bad.append(f"{y.name}:{i}: {line.strip()}")
    assert not bad, "kit config references a stored asset:\n" + "\n".join(bad)


def test_impact_drawing_flash_is_derived_per_clip():
    import pathlib

    import yaml

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    d = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())
    df = d.get("drawing_flash")
    assert df and df.get("source") == "derived_per_clip"
    assert "path" not in df and "file" not in df


def test_impact_carries_grade_TARGETS_not_solved_multipliers():
    """Multipliers are per source; the reference's output numbers are constant."""
    import pathlib

    import yaml

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    d = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())
    assert "world_luma" not in d["palette"], "a solved multiplier leaked into the kit"
    t = d["grade_targets"]
    assert t["subject_world_ratio"] == 2.07
    assert set(t) >= {"corner_luma", "world_luma", "subject_luma"}


def test_impact_names_a_subject_METHOD_not_a_colour():
    import pathlib

    import yaml

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    d = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())
    assert "subject_colour" not in d["subject"], "a clip's colour leaked into the kit"
    sel = d["subject"]["selection"]
    assert sel["method"] == "upright_on_last_frame"
    assert sel["hold_across_annotations"] is True


def test_measured_constants_live_in_the_kit_not_in_code():
    """RENDER-01 §1: no literals in code. Every measured number is config."""
    import pathlib

    import yaml

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    d = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())
    m = d["measured"]
    for key in (
        "full_bleed_floor_1080p",
        "torso_box_target",
        "matte_area_band",
        "subject_vote_frames",
        "drawing_pose_iou_gate",
        "tail_live_frames",
        "cuts_per_s_band",
    ):
        assert key in m, f"{key} missing from the kit's measured block"
    assert abs(m["full_bleed_floor_1080p"] - 1920 / 1080) < 1e-3
    assert m["matte_area_band"] == [0.12, 0.55]
    assert m["drawing_pose_iou_gate"] > 0.291, (
        "the pose gate must sit above the measured wrong-fighter floor"
    )


def test_kit_constants_agree_with_the_code_that_still_holds_them():
    """Where a constant exists in both places, they must not drift apart."""
    import pathlib

    import yaml
    from genlab_core.action.silhouette_subject import MIN_BODY_FRAC, VOTE_FRAMES
    from genlab_core.action.source_score import FULL_BLEED_FLOOR_1080P, TORSO_FLOOR

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    m = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())["measured"]
    assert abs(m["full_bleed_floor_1080p"] - FULL_BLEED_FLOOR_1080P) < 1e-3
    assert m["torso_floor"] == TORSO_FLOOR
    assert m["subject_vote_frames"] == VOTE_FRAMES
    assert m["subject_body_area_floor"] == MIN_BODY_FRAC


def test_matte_module_constants_match_the_kit():
    """Port the function, keep the constant in YAML (RENDER-01 rule)."""
    import pathlib

    import yaml
    from genlab_core.action.matte import MATTE_AREA_BAND

    reg = __import__("genlab_core.action.kits.registry", fromlist=["x"])
    m = yaml.safe_load((pathlib.Path(reg.__file__).parent / "impact.yaml").read_text())["measured"]
    assert list(MATTE_AREA_BAND) == m["matte_area_band"], (
        "matte.py and impact.yaml disagree on the area band"
    )
