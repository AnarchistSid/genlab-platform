# INV-01b — Methodology errors

## Overturned during this audit

| # | claim | correction | how caught |
|---|---|---|---|
| E-01 | "The publisher does not require `reviewed_at`" — asserted 2026-09-09 after reading `blueprint_selector`'s status filter and sort | **Wrong.** There is an `approval_gate` I did not grep for. `1b3e0a5c` was blocked by it and did not publish: `blocked by approval_gate: Not approved`. Setting `scheduled_for` directly bypassed the path that stamps approval. The correlation CADENCE-01 found (27/27 scheduled rows were reviewed) *did* imply a requirement; I dismissed it | publisher journal, this audit |
| E-02 | Step 1 retention copy reported "3 files per niche, 1.5 MB" | The `find` in my copy script matched no MP4s. The renders existed all along (21 files in the run dir). Corrected copy: **86 files, 281 MB** | inspected a run dir directly |
| E-03 | Section 3 reported **0 cuts in every niche** | The `movie=`-lavfi scene-detection returned 0 on a file where `showinfo` finds **9 cuts at scene>0.3**. Method was broken, not the renders | ran two methods on the same file before reporting |
| E-04 | `find -maxdepth 1` reported `.tmp` as empty (0 entries) during OPS-02 | `.tmp` is a **symlink** to `/mnt/genlab-media/.tmp` (68 entries); `find` does not follow symlinks by default | `readlink` |

## Could not meet the evidence standard

| # | item | reason | recorded as |
|---|---|---|---|
| U-01 | Caption burn-in presence, ai_creators | My pixel heuristic (YMAX≥235 in the middle third) returned 0/5, but it is **unvalidated** — I have no positive control proving it detects captions that are present. Reporting "captions absent" from it would repeat E-03 | `HUMAN-PENDING`, not PASS/FAIL |
| U-02 | VO presence in render; ducking measurement | Not probed. Whisper was not run against the retention copies this pass | `MISSING`, declared NOT AUDITED |
| U-03 | Render path, transitions, text-overlay effects, attribution slate | Not read from render metadata this pass | `MISSING`, declared NOT AUDITED |
| U-04 | Source-clip audio disposition (gaming/sports licensed-audio exposure) | gaming produced no render; sports not probed | `MISSING` |
| U-05 | anime 14-day stoppage cause | Publisher journal for prior days has rotated. Scheduler and pipeline **ruled out** by today's successful fire; approver-rejection leading but publish-failure not excluded | `UNMEASURABLE` for the window |
| U-06 | "Top three compliance warn classes" | Only **two** classes exist in the store. The requested shape cannot be produced | reported as two |

## Compliance notes

- Retention copy completed **before** any probe, per Step 1; every measurement
  below it reads from the copy, not the live path.
- Every LUFS/TP figure states its method (`ffmpeg loudnorm print_format=json`).
- Every cut count states its threshold (`showinfo`, `scene>0.3`).
- Nothing requiring ears or eyes is marked PASS.
- `find: Failed to restore initial working directory: /root` appeared in copy
  output — cosmetic, from running as `genlab` with a root cwd; the copy is
  verified by the 86-entry manifest.

| E-09 | **A withdrawal is a claim, and needs the original's standard.** I reported the "9 cuts / 0.294 cuts-per-second" ai_creators figure as matching no render measured, and it was accepted into an instruction to withdraw it. I had run ONE detector. Running both on the same file shows `showinfo` reads 2.4–3× higher than `shot-density-check` — today's ai_creators is 6 cuts / 0.191 on the original method, the same order as the figure I was retracting. The retraction would have encoded a second error on top of the first. | Two detectors on the same file BEFORE retracting a measurement, exactly as E-03 required before reporting one. Retracting feels like caution and is not: it is an assertion that the earlier number was wrong, and it needs evidence of the same weight. |
