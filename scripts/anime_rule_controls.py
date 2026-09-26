#!/usr/bin/env python3
"""Run each rule's check against its positive control.

A rule is only real if its control FAILS it. This separates rules that are
genuinely enforced from ones that are currently prose, and says which is which
rather than letting a ledger imply they are all the same.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "genlab-core/src"))
from genlab_core.still import edit_rules as ER  # noqa: E402

SP = Path("/private/tmp/claude-501/-Users-anarchistsid-GenLab/"
          "1f202acb-13c4-4215-a4eb-ece569c89ee0/scratchpad/still")


def _log_for(video: Path) -> dict | None:
    tag = video.stem.replace("peak_tanjiro_", "").split("_")[0]
    p = SP / tag / "render_log.json"
    return json.loads(p.read_text()) if p.exists() else None


def context() -> dict:
    """What a check cannot read off the file. Anything missing makes a check
    INCONCLUSIVE, never a silent pass."""
    ctx: dict = {}
    al, asr = SP / "v23/aligned_lines.json", SP / "asr_source.json"
    if al.exists():
        ctx["lines"] = [{"start": x["src_a"], "end": x["src_b"], "text": x["text"]}
                        for x in json.loads(al.read_text())]
    elif asr.exists():
        ctx["lines"] = json.loads(asr.read_text())
    # plan_for_control is set PER CONTROL by main(); there is no global plan.
    ctx["declared_trims"] = [(61.83, 63.70), (64.75, 69.18), (76.00, 76.67)]
    if (SP / "plan_v22.json").exists():
        ctx["keep_boxes"] = {str(s["i"]): [s["x0"], s["x1"]]
                             for s in json.loads((SP / "plan_v22.json").read_text())["shots"]}
    if (SP / "cues_v20.json").exists():
        fb = {}
        for s in json.loads((SP / "cues_v20.json").read_text())["shots"]:
            if s.get("faces"):
                fb.setdefault(str(s["i"]), s["faces"])
        ctx["face_bands"] = fb
    return ctx


def main() -> int:
    led = yaml.safe_load((ROOT / "niches/anime/edit_lane/rules.yaml").read_text())
    ctx = context()
    print(f"  {'rule':<52}{'control':<24}result")
    print("  " + "-" * 104)
    proven = ran = inconc = 0
    for r in led["rules"]:
        rid = r["id"]
        art = (r.get("positive_control") or {}).get("artefact", "")
        fn = ER.REGISTRY.get(rid)
        if not fn:
            print(f"  {rid:<52}{art[:23]:<24}not executable ({r['status']})")
            continue
        v = SP / art
        log = _log_for(v) if v.exists() else None
        if not v.exists() or log is None:
            inconc += 1
            print(f"  {rid:<52}{art[:23]:<24}INCONCLUSIVE - control artefact or log missing")
            continue
        ran += 1
        plans = {"peak_tanjiro_v22.mp4": SP / "plan_v22.json",
                 "peak_tanjiro_v23_ccby.mp4": SP / "plan_v23.json"}
        pp = plans.get(art)
        ctx["plan_for_control"] = json.loads(pp.read_text()) if pp and pp.exists() else None
        try:
            violated, detail = fn(v, log, ctx)
        except Exception as exc:                       # noqa: BLE001
            print(f"  {rid:<52}{art[:23]:<24}ERRORED - {type(exc).__name__}: {exc}")
            continue
        if violated is None:
            inconc += 1
            print(f"  {rid:<52}{art[:23]:<24}INCONCLUSIVE - {detail}")
            continue
        proven += bool(violated)
        verdict = "control FAILS (proven)" if violated else "control PASSES - NOT PROVEN"
        print(f"  {rid:<52}{art[:23]:<24}{verdict} - {detail}")
    total = len(led["rules"])
    print(f"\n  {len(ER.REGISTRY)}/{total} rules have an executable check")
    print(f"  of the {ran} controls that ran: {proven} proven, {inconc} inconclusive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
