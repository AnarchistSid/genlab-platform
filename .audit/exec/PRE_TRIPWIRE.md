# Pre-Tripwire — F-0080 + Anthropic Collision (2026-07-27 20:25 IST)

**Tripwire:** publisher fires 12:05 IST 2026-07-28 (**~15h40m** from writing). F-0080 hard-fail shipped commit `2ea7f12e`. Anthropic monitor at 20:15 IST reports still `exhausted, matches_found: 2` (session 9+ continues).

## 1. Anthropic decision — Path B3 (ESCALATED, freeze is default)

Operator deferred the freeze-vs-revert decision. **Default at 12:05 IST if nothing changes: freeze.** F-0080 hard-fails every story on empty balance; publish count ≈ 0.

Two paths if decided before 12:05 IST 2026-07-28:
- **Freeze (accept ≤24h)** — do nothing. Nothing terminated-format ships; a dark day beats a terminated-format day. Defensible.
- **Revert F-0080** — `git revert 2ea7f12e && git push` + prod pull. Publisher falls back to source-title hooks. **Re-enables the exact format YouTube terminated 16 channels for Jan 2026.** Only pick if a dark channel is genuinely worse than one more day of the terminated format.

**Freeze is the default.** No action = freeze. Recorded as conscious, not stumbled-into.

## 2. Verification query saved

`.audit/exec/verify_tripwire.sql` distinguishes the three outcomes at 12:05:
- `passthrough=0, real_hooks>0` → **WIN.** F-0080 works, writer works.
- `passthrough=0, published=0` → **FREEZE.** F-0080 works, empty balance starves it.
- `passthrough>0` → **the guard has a sibling site** (`base_hooks.py:208` isn't the only path); investigate `_generate_hook` callers + skip-fallbacks.

The changelog's original SQL counted passthrough only — could not tell win from freeze. Fixed.

## 3. F-0080 evidence amended; F-0083 dead-code filed

Fourteen sessions targeted `llm_hook_generator.py:1385`. Real site is `base_hooks.py:208-223`. `:1385` is `generate_platform_hooks` — **zero external callers, dead code.** F-0080 evidence rewritten in `findings.jsonl`; **F-0083 LOW** filed for deletion. `SCORECARD.md` "What audit got wrong" records the 14-session diagnostic error the discipline caught.

## 4. Scorecard staleness flagged

`SCORECARD.md` "⚠ SUPERSEDED-PENDING-REMEASURE" section:
- **COMPETENCY** leaned on "9,451 tests cannot complete" — they complete in 2:08. Stale.
- **CREATIVITY** was scored during the F-0080 outage. Re-measure on a clean week post-restore.

26/60 total not re-computed until both axes have clean data.

## 5. Test quarantined + RLS preconditions written

`test_picks_most_recent_backup_by_mtime` `@pytest.mark.skip` (mtime-resolution flake). 13 pass / 1 skip in that file, 0 fails elsewhere. RLS preconditions in `OPERATOR_TASKS.md`: (1) Anthropic non-zero, (2) 24-48h F-0069 WARN accumulated, (3) suite green. All three required.
