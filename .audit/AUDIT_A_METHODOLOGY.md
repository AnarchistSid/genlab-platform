# AUDIT A — Methodology Ledger

## Class-of-bug taxonomy (from A-0088; inherit into next audit)

Five recurring bug shapes appeared multiple times across this run. Each has a general-purpose detection heuristic — the heuristic is the value carried forward.

### (i) durable-error-file-stale-state
**Shape:** reading a `.runtime/*_last_error.txt` (or any durable-error-only file) and inferring "still failing" from its presence. The file writes on failure and is never overwritten on success — so "file exists with old traceback" = "system has ever failed", NOT "system is still failing."

**Detection heuristic:** for any durable-state file, also write a `_last_success.txt` marker. Alert only when error-mtime > success-mtime. Alternative: check `systemctl status` for exit-code=0 since the error-file mtime.

**Instances this run:** A-0024 (Session 3, Phase 2) → A-0043 (Session 5 retraction after actually checking service run history); A-0009 (Phase 0 no-consumer claim).

### (ii) grep-pattern-doesn't-match-codebase
**Shape:** grepping for an expected shape (`INSERT INTO x`, `class X`, `except ImportError`) and concluding absence when the codebase writes via a backend abstraction (`pg.create("x", ...)`, dependency-injection, decorators). "I searched for the finding I expected" — Session 8 §12 exact quote after this class of error was flagged in A-0055 correction, then repeated one finding later in A-0068 → A-0073.

**Detection heuristic:** try 3 shapes (raw SQL, ORM, backend abstraction) before concluding absence. Corollary: any file-count grep for a workspace member must inherit the manifest's exclusion set — see A-0030 → A-0078.

**Instances this run:** A-0055 → A-0072 (Session 8, gitleaks IS in pre-commit); A-0068 → A-0073 (Session 7-9, affiliate writer IS in link_tracker.py); A-0030 → A-0078 (Session 8, BB "758 JSONs" was 741 in `.tmp/`).

### (iii) git-blind in-place writers
**Shape:** components that write git-tracked YAML/config files in place, then git records only the endpoints of a series of overwrites — ladder history is destroyed.

**Detection heuristic:** any file with "AUTO-GENERATED" comment should be gitignored; commit a schema instead. Any writer that opens a file for `w` mode against a git-tracked path is a candidate.

**Instances this run:** `auto_ramp_auto2.py` (A-0023, weekly ramp writing publishing.yaml); `genlab-cuelinks-campaign-refresh.timer` (A-0020, regenerates cuelinks_campaigns.yaml). Merged as A-0082 class-fix.

### (iv) line-exists-doesn't-fire
**Shape:** a guard/prune/hook mechanism is CONFIGURED in code (line exists) but does NOT execute in prod (line doesn't fire). Reading the code and seeing "yes, the retention line is there" produces false confidence.

**Detection heuristic:** for every guard mechanism, pair with an INDEPENDENT "did-it-fire" alert. E.g. a prune script's retention-14-days guarantee is verified by a separate sentinel `find ... -mtime +14 | wc -l` alert; a pre-commit gitleaks hook is verified by a CI-side `pre-commit run --all-files`. The verifier must be decoupled from the guarded mechanism so a shared bug can't hide both.

**Instances this run:** A-0026 (pg_backup.sh -mtime +14 -delete not firing, 20 files retained), A-0062 (backup_visual_assets.sh same predicate, 421 files retained, 33-day-old), A-0072 (gitleaks pre-commit hook configured but bypassed). Merged as A-0083 meta-finding class-fix.

### (v) hook-configured-but-not-enforced (sub-case of iv)
**Shape:** a pre-commit hook exists in config, but `pre-commit install` was never run on the operator's machine, OR the operator used `--no-verify`. Config is symbolic; enforcement is real.

**Detection heuristic:** ANY pre-commit hook must have a mirror in CI — a workflow that runs `pre-commit run --all-files` and fails PRs. Pre-commit hooks are opt-in per developer; CI is mandatory per commit.

**Instance this run:** A-0072 (gitleaks configured v8.24.3, but 2026-07-22 commit added 3× `PGPASSWORD=genlab_***` anyway).

---

## Errors by origin

### PROMPT-ORIGIN (3 confirmed; the spec author's errors)

1. **v1.0 §0.4 root-anchored globs** (`./.venv/*`) — leaked all nested `.venv/`, `node_modules/`, etc. into manifests. 66% of Phase 0's drift diff was junk. Corrected in v1.1 with `*/` prefix.
2. **v1.0 §0.5 manifests directed into public `.audit/`** — would have published a full prod filesystem inventory + `.env` hashes had they been committed. Caught pre-first-push. Corrected in v1.1 with `.audit/.gitignore` for `manifest_*` etc.
3. **v1.1 §0.4 `.mypy_cache` omission + `.hypothesis` anomaly** — v1.1's extended exclusion list still missed `.mypy_cache/`. `.hypothesis` anomaly (A-0019) was execution-error not prompt-error (my own fabricated count), but the v1.1 exclusion set was legitimately incomplete. Corrected in v1.2.

### EXECUTION (my errors, per session §12 methodology sections)

1. **Phase-0 `time`-command stderr leaked into manifest** — my bash `time (find … | xargs …)` sent `time`'s output to stderr, which I redirected to the manifest file. `0m55.154s/` etc. appeared as fake "directories". Caught in Phase 1 injected item.
2. **Phase-0 stale absolute-path `tr` on VPS** — I referenced a Mac-local path (`/Users/anarchistsid/…/paths_vps.txt`) inside a VPS SSH command. Errored silently before the actual find; error message leaked into VPS manifest as a "path". One line of noise.
3. **A-0002 blast-radius overreach** — narrated `rollout_pct: 1.0` as a live 100% rollout without noting the `enabled: false` two lines above. Corrected in Session 3 via A-0012 → and again in Session 4 via A-0027.
4. **A-0009 severity-cap breach + false no-consumer claim** — filed S2 with a STATIC_ONLY tag (should have capped at S3); AND claimed `.backups/` had no consumer without checking. Session 2 corrected via A-0014 (which itself was wrong) → A-0026.
5. **A-0014 wrong script name** — assumed `backup_db.sh` was what ran from the systemd timer; the actual script is `pg_backup.sh`. Different directory, different retention rule. Session 3 corrected via A-0026.
6. **A-0019 two false claims** — fabricated "6 hypothesis files leaked" (actual: 0) AND wrong spec-history claim ("v1.0 excluded .mypy_cache") (actual: v1.0 mac paths file contains 0 `.mypy_cache` lines). Pre-Session-3 corrected in-place with real grep counts.
7. **Dangling A-0021 reference** — parenthetical `(S3 finding A-0021 below)` in Phase 1 §2 pointing at a finding never created. Pre-Session-3 removed.
8. **A-0018 §13 mislabel** — filed as `STATIC_ONLY (S3 cap)` when action was `consolidate` (staleness verified by dated filenames, no removable-claim, uncapped). Pre-Session-3 relabeled.
9. **Non-verbatim evidence blocks** (A-0011, A-0017) — parenthetical commentary inside `output:` blocks. §0.2 v1.2 verbatim-only rule addressed the pattern.
10. **Session-3 secret leak into `.audit/`** — snapshotted `verify_policy_block_l1.sh` containing `PGPASSWORD=genlab_***` × 3 into `.audit/snapshots/` without pre-redacting. Redacted mid-session; gitignored `snapshots/` after. Then wrote the literal into 3 committable artifacts (PHASE_1_waste.md, PHASE_2_config.md, PHASE_2_findings.yaml). §0.5 v1.3 added the mandatory per-session self-scan as countermeasure.
11. **Phase-3 summary over live shells** — wrote §11 self-scan "PASSED" claim while 2 SSH sessions still running. Claim was against a moving filesystem. §0.10 v1.4 added explicit shell-quiescence hard gate.
12. **Phase-3 second-scrub for meta-references** — the self-scan caught 4 meta-references (the literal in `grep 'genlab_***'` commands documented in the artifact). §0.5 v1.3 clarified that meta-references count as writes.
13. **Gate-as-operator-handoff fiction** — Pre-Session-3 and Pre-Session-4 were both written as "operator does this out-of-band"; both times the operator did not. v1.4 corrected: gates are session-first tasks; operator changes go to OPERATOR_ACTIONS.
14. **A-0024 durable-error-file misread** → A-0043 retraction (Session 5). Class (i).
15. **A-0055 "no hook exists" inferred from absence** → A-0072 correction (Session 8). Class (ii).
16. **A-0030 BB-JSON manifest-noise** → A-0078 correction (Session 8). Class (ii sub-form).
17. **A-0068/A-0073 grep-pattern-doesn't-match-codebase** → Pre-Session-9 addendum caught it despite Class (ii) already being named. **Recognizing a class is not the same as not repeating it.** This is the run's clearest single lesson: taxonomy tells you what to check; it doesn't do the check for you.
18. **A-0062 + A-0069 split filing** — S1 mechanism + S3 timeline as two entries made the register more alarming than the evidence. Pre-Session-9 merged.

Plus this session (Phase 9A):
19. **Session-10 gate re-verification revealed 4 findings materially changed since Phase 3** (A-0033, A-0038, A-0044, A-0034). Register carries partial retractions with reference to `PHASE_9A_gate.md`. Lesson: state has entropy; findings from 5 sessions ago need re-verification at triage time. This is exactly what v1.5's "re-verify every S0/S1 from scratch" rule caught.

**Zero further errors would not be credible.** If a session ends with none logged, that itself is suspicious. This session logged one meta-error (#19).

---

## Burned-ID map (do not reassign)

- **A-0021** — Referenced in Phase 1 §2 as a forward reference (`S3 finding A-0021 below`). Never actually created. Pre-Session-3 removed the reference; ID stays burned to prevent reuse confusion.
- **A-0022** — Used informally in Phase 1 §11 as a positive note for cross-imports = 0/5. Not a real finding schema entry. ID stays burned.

Next free ID for any future amendment: **A-0089**.

---

## Register + fix-queue inheritance

- Register: `AUDIT_A_REGISTER.yaml` (47 entries after merges + retractions).
- Fix files: `.audit/fixes/FIX_A-XXXX.md` per FIX-dispositioned entry (subset written this session; batched for high-value + top-15 items).
- Deferrals disposition: `AUDIT_A_DEFERRALS_DISPOSITION.md` — produced by Session 11 Phase 9B.
- Retracted: `AUDIT_A_RETRACTED.md` — Session-10 partial retractions listed there.

---

## Meta-observation on the audit's own methodology

The single most important discipline this run demonstrated: **the audit's mistakes were caught by the audit's own rules, applied recursively.** §0.5 self-scans caught secret leaks I introduced. §0.8 "prior artifacts untrusted" caught A-0002/A-0009/A-0014/A-0024/A-0030/A-0055/A-0062/A-0068 corrections. §0.10 shell quiescence caught Phase 3's over-live-shells summary. v1.5's forced S1 re-verification caught Session-10's 4 materially-changed findings.

**The methodology's job is not to prevent errors — it's to make errors visible and correctable within the same run.** By that metric the discipline worked. The register that ships from Phase 9A is more accurate than any single phase's findings YAML, because it consumed the corrections.
