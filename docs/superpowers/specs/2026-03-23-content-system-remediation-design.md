# Content Generation System Remediation — Design Spec

**Date:** 2026-03-23
**Scope:** Fix 165 issues found in comprehensive system analysis
**Approach:** 10 sequential sub-projects (S1–S10)

## Sub-project Sequence

### S1: Critical One-Liner Fixes (~20 issues)
Quick, mechanical fixes with no dependencies. Wrong model IDs, missing methods,
hardcoded paths, wrong imports, type mismatches.

### S2: Pipeline Architecture Repair (~15 issues)
Wire stages to read `stories` not `blueprints`. Implement stage retries.
CLI `--stages` filter. Express lane → gatekeeper integration.

### S3: Credential Isolation (~12 issues)
Fix `launch_wrapper.sh` BB env sourcing. Per-niche poller credentials.
Remove direct `META_ACCESS_TOKEN` reads.

### S4: Platform Client Fixes (~15 issues)
Instagram `_graph_get` method. YouTube category routing. Facebook CDN upload.
Threads self-reply filter. Link redirect domain allowlist.

### S5: Dead Code Activation or Removal (~20 issues)
Wire dedup_engine, hook_validator, cost_accumulator, validate_videos into
pipeline — or delete if not needed. Clean up stubs.

### S6: Engagement System Repair (~12 issues)
Wire Threads/Facebook pollers into config + runner. Fix async facade.
Engagement rate limiting across workers.

### S7: LLM Client Unification (~8 issues)
Single LLM client pattern across affiliate_matcher, persona_engine, and
writing. Unified model routing and cost tracking.

### S8: Code Quality & Cleanup (~30 issues)
Bare exception handlers, duplicate code, dead methods, import cleanup,
shell script hardcoded paths, inline HTML.

### S9: Documentation & Config Sync (~20 issues)
CLAUDE.md updates for 15 undocumented plists, stale architecture docs,
storage routing config, migration consolidation.

### S10: Test Coverage Gaps (~13 issues)
Tests for 7 untested modules. Fix duplicate test files. Address segfault.

## Principles
- Each sub-project is independently committable
- No sub-project should break existing tests
- Prefer minimal changes that fix the issue over refactors
- If a "fix" requires >50 lines of new code, it belongs in a later sub-project
