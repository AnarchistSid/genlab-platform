#!/usr/bin/env python3
"""Validate a franchise pack against the schema. Missing optional keys fall
back to defaults and never crash; missing REQUIRED keys are errors."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "niches/anime/edit_lane"
TIERS = {"unknown", "permissive", "cautious", "hostile"}
WORK_TIERS = {"unknown", "usable", "usable_with_note", "cautious", "block"}
COMPONENT_STATUS = {"clean", "claimed_block", "claimed_monetise", "claimed_track",
                    "untested", "not_applicable", "per_bed"}

# ASSET KIND is the rights variable, not the show and not the arc. Measured on
# Demon Slayer 2026-09-26, same show, same channel, same arc:
#   trailer         XoJCOAU_72U  picture CLEAN      audio BLOCKED 249   (CLM-010)
#   episode_clip    AR6-4GaCPn0  picture BLOCKED 249, audio BLOCKED 249 (CLM-011)
#   bonus_segment   SDBOxgbvZyY  picture CLEAN      audio CLEAN
# A probe result therefore NEVER generalises across kinds, and every source
# must declare which kind it is so a reading can be attached to the right one.
SOURCE_KINDS = {"trailer", "episode_clip", "bonus_segment", "key_visual"}


def validate(p: Path) -> tuple[list[str], list[str]]:
    schema = yaml.safe_load((BASE / "franchises/_schema.yaml").read_text())
    pack = yaml.safe_load(p.read_text())
    grammars = {g["id"] for g in
                yaml.safe_load((BASE / "grammars.yaml").read_text())["grammars"]}
    profiles = {g["id"] for g in
                yaml.safe_load((BASE / "profiles.yaml").read_text())["profiles"]}
    err, warn = [], []
    for k in schema["required"]:
        if k not in pack:
            err.append(f"missing required key: {k}")
    r = pack.get("rights", {})
    works = r.get("works") or {}
    if not works and r.get("tier") not in TIERS:
        err.append(f"rights.tier {r.get('tier')!r} not in {sorted(TIERS)}")
    if works:
        if r.get("tier"):
            err.append("rights.tier is set alongside rights.works — tier is "
                       "PER WORK; a franchise-level tier retires usable works")
        for wid, w in works.items():
            if (w or {}).get("tier") not in WORK_TIERS:
                err.append(f"rights.works.{wid}.tier {(w or {}).get('tier')!r} "
                           f"not in {sorted(WORK_TIERS)}")
            if (w or {}).get("usable") is None:
                err.append(f"rights.works.{wid} has no `usable` verdict")
            for cname, c in ((w or {}).get("components") or {}).items():
                st = (c or {}).get("status")
                if st not in COMPONENT_STATUS:
                    err.append(f"rights.works.{wid}.{cname}.status {st!r} invalid")
                elif st not in ("untested", "not_applicable") \
                        and not (c or {}).get("evidence"):
                    err.append(f"rights.works.{wid}.{cname} is {st} with no evidence")
    comps = r.get("components") or {}
    if not comps and not works:
        warn.append("neither rights.works nor rights.components — every "
                    "component is effectively untested")
    for name, c in comps.items():
        st = (c or {}).get("status")
        if st not in COMPONENT_STATUS:
            err.append(f"rights.components.{name}.status {st!r} invalid")
        elif st not in ("untested", "not_applicable") and not (c or {}).get("evidence"):
            err.append(f"rights.components.{name} is {st} with no evidence")
    for g in pack.get("moment_grammars", []):
        if g not in grammars:
            err.append(f"unknown grammar: {g}")
    for sp in pack.get("source_profiles", []):
        if sp not in profiles:
            err.append(f"unknown source profile: {sp}")
    m = comps.get("music") or {}
    if m.get("status") == "per_bed" and not m.get("beds"):
        err.append("music.status is per_bed but no beds are listed — that is a "
                   "dodge, not a record")
    for bname, b in (m.get("beds") or {}).items():
        if (b or {}).get("status") not in COMPONENT_STATUS:
            err.append(f"music.beds.{bname}.status {(b or {}).get('status')!r} invalid")
        if (b or {}).get("status", "untested") not in ("untested", "not_applicable") \
                and not (b or {}).get("claim"):
            err.append(f"music.beds.{bname} has a status but no claim id")

    # Source-audio policy. The preflight decides PER REEL, so a claimed_block at
    # franchise level is no longer fatal -- but `allow_monetised` must never be a
    # licence to ship a BLOCKING claim, and that is the check worth having.
    pol = pack.get("audio", {}).get("source_audio_policy", "auto")
    if pol not in {"auto", "none", "allow_monetised"}:
        err.append(f"audio.source_audio_policy {pol!r} invalid")
    if pol == "allow_monetised" and \
            comps.get("dialogue_audio", {}).get("status") == "claimed_block":
        err.append("source_audio_policy is allow_monetised but dialogue_audio is "
                   "claimed_block — allow_monetised covers monetise/track claims "
                   "only, never a block")
    for c in pack.get("callbacks", []) + pack.get("techniques", []):
        if not c.get("cite"):
            err.append(f"unsourced fact on screen: {c.get('name') or c.get('id')}")
    if not pack.get("sources"):
        err.append("no sources: nothing licensed to draw from")
    for src in pack.get("sources", []):
        k = (src or {}).get("kind")
        if k not in SOURCE_KINDS:
            err.append(f"source {src.get('name') or src.get('url')}: kind {k!r} "
                       f"not in {sorted(SOURCE_KINDS)}")
    # A rights component reading must say which asset kind it came from.
    for wid, w in ((pack.get("rights") or {}).get("works") or {}).items():
        for cid, comp in (w.get("components") or {}).items():
            ev_kind = (comp or {}).get("kind")
            if ev_kind is not None and ev_kind not in SOURCE_KINDS:
                err.append(f"rights.works.{wid}.{cid}.kind {ev_kind!r} "
                           f"not in {sorted(SOURCE_KINDS)}")
            if (comp or {}).get("status") not in ("untested", "not_applicable", None) \
                    and ev_kind is None:
                warn.append(f"rights.works.{wid}.{cid}: a tested reading with no `kind` — "
                            f"it cannot be told which asset class it applies to")
    return err, warn


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    paths = [Path(a) for a in args] or sorted(
        (BASE / "franchises").glob("*.yaml"))
    bad = 0
    for p in paths:
        if p.name.startswith("_"):
            continue
        err, warn = validate(p)
        status = "FAIL" if err else ("OK (warnings)" if warn else "OK")
        print(f"  {p.name:<28}{status}")
        for e in err:
            print(f"      ERROR  {e}")
        for w in warn:
            print(f"      warn   {w}")
        bad += bool(err)
    print(f"\n  {len(paths)-bad}/{len(paths)} packs valid")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
