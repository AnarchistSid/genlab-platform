#!/usr/bin/env python3
"""Run our gates and the independent checkers on one render, side by side.

The two are NOT averaged and neither overrides the other. Where they disagree
the disagreement is the finding: it has already been informative twice -- a
`shot-density-check` FAIL on a 5.8s "unbroken shot" turned out to be three
authored cuts its detector merged, which is a real perceptual note even though
the literal claim is false; and `kinetic-edit-check` reports three cut counts
for one file and says so itself.

Exit 0 unless OUR gates fail (rule #26). An external checker failing is data for
the operator, not a reason to page.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CHECKERS = [
    "anarchistsid/reel-spec-check",
    "anarchistsid/shot-density-check",
    "anarchistsid/kinetic-edit-check",
    "anarchistsid/broadcast-chrome-check",
    "fuplus/seamless-loop-inspector",
]
# the loop inspector takes ~10 min; skipped unless --full
SLOW = {"fuplus/seamless-loop-inspector"}


def run_checker(app: str, video: Path) -> dict:
    r = subprocess.run(
        ["belt", "app", "run", app, "--json", "--input",
         json.dumps({"video": str(video)})],
        capture_output=True, text=True, timeout=1800)
    for ln in reversed(r.stdout.splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
            except json.JSONDecodeError:
                continue
            out = d.get("output") or {}
            return {"app": app, "ran": d.get("status_text") == "completed",
                    "passed": out.get("passed"), "report": out.get("report"),
                    "output": out}
    return {"app": app, "ran": False, "passed": None,
            "report": f"no parsable result: {r.stdout[-200:]}"}


def our_gates(log: dict) -> dict:
    pac = log.get("pacing", {})
    return {
        "pacing": pac.get("PASS"),
        "noise_floor": (log.get("noise_floor") or {}).get("pass"),
        "smoothness": (log.get("smoothness") or {}).get("ok_ignoring_authored"),
        "bed_alignment": (log.get("alignment") or {}).get("ok"),
        "geometry_plate_constant": len({s["aspect"] for s in log["segments"]}) == 1,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: anime_qa_harness.py <render.mp4> <render_log.json> [--full]")
        return 0                                   # not an incident
    video, logp = Path(sys.argv[1]), Path(sys.argv[2])
    full = "--full" in sys.argv
    log = json.loads(logp.read_text())

    ours = our_gates(log)
    rows = []
    for app in CHECKERS:
        if app in SLOW and not full:
            rows.append({"app": app, "ran": False, "passed": None,
                         "report": "skipped (slow); pass --full to include"})
            continue
        rows.append(run_checker(app, video))

    print(f"\n  OUR GATES — {video.name}")
    for k, v in ours.items():
        print(f"    {k:<26} {'PASS' if v else 'FAIL' if v is False else 'n/a'}")
    print("\n  INDEPENDENT CHECKERS")
    for r in rows:
        v = ("PASS" if r["passed"] else "FAIL" if r["passed"] is False
             else "ran, no verdict" if r["ran"] else "not run")
        print(f"    {r['app']:<38} {v}")
        if r.get("report"):
            for ln in str(r["report"]).splitlines()[:4]:
                print(f"        {ln}")

    ext_fail = [r["app"] for r in rows if r["passed"] is False]
    ours_fail = [k for k, v in ours.items() if v is False]
    if ext_fail and not ours_fail:
        print(f"\n  DISAGREEMENT: our gates all pass; {', '.join(ext_fail)} does not.")
        print("  Logged, not averaged. Read the checker's finding before changing a gate.")
    out = {"video": str(video), "our_gates": ours, "checkers": rows,
           "disagreement": bool(ext_fail) != bool(ours_fail)}
    Path(logp.parent / "qa_harness.json").write_text(json.dumps(out, indent=2))
    return 1 if ours_fail else 0


if __name__ == "__main__":
    sys.exit(main())
