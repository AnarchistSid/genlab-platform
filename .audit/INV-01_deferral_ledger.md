# INV-01 — Deferral ledger
Out-of-scope items noticed during the inventory. **Tasks, not commits.** None acted on.

## Human blockers (owner: operator) — none resolved

| # | blocker | blocks | evidence still open | class |
|---|---|---|---|---|
| H-01 | Anthropic credit | writer, auto_approval_gate, persona_engine | Resolved 2026-09-09 (probe OK); `anthropic_credit_exhausted` fired 36× since 08-22, latest 09-09 08:30 | M |
| H-02 | ElevenLabs credential quality | VO tier — key present, but tier unrecordable (T-01) so use is unverifiable | key present in .env; no successful-call timestamp exists to read | M |
| H-03 | YouTube quota headroom | sourcing across 5 niches | not exposed by the API in a form we read; `UNMEASURABLE` | M |
| H-04 | X/Twitter credentials | X publishing | out of scope per rule #23 (4-platform focus) | D |
| H-05 | SpliceReel Facebook page provenance | movies reach interpretation | 8,653 fans, origin unverified | M |
| H-06 | Listen verdict on first narrated reel | #218 | **PASS given 2026-09-10**; publish pending 09-11T07:00Z | M |
| H-07 | FB page provenance (ai_creators) | ai_creators reach interpretation | 10,022 fans vs 0 IG movement | M |
| H-08 | OpenAI billing alerts | fallback tier during Anthropic outages | not configured | D |

## Tasks surfaced

| # | task | why it matters | class |
|---|---|---|---|
| T-01 | **Persist `audio_provider` to `blueprints.extra`** | VO tier is unrecoverable for every reel ever published; blocks the playbook's DEGRADED determination permanently, not just retrospectively | M |
| T-02 | **Fix journal retention** — `MaxRetentionSec=30d` configured, hours actual, 47.8M of 2G used; two config files disagree (2G vs 500M) | every future incident is un-forensicable; already cost this audit Phase 1 | M |
| T-03 | Repair `genlab-post-deploy-verify` | the verify half of "deploy and verify" is down while deploys are bare pulls | M |
| T-04 | Repair `genlab-strategist` | weekly meta-analysis dark since at least 08-23 | M |
| T-05 | Decompose engagement zero — polled-and-empty vs not-polled | 0 rows in 14d; cause unmeasurable post-rotation, so this needs an instrument not a query | M |
| T-06 | Reconcile 6 prod-only units into the repo | deploys cannot reproduce prod | M |
| T-07 | Extend `flag_audit` to the 46 untracked flags | canary verification covers 59 of 105 | M |
| T-08 | Remove orphan `run-u555874.service` (`/tmp/narr06_repro.py`) | leftover from a prior session; register #2 shape | M |
| T-09 | Anime sourcing/writer — title-as-hook on 60% of DRAFTED | see #241 lineage; gates any anime consolidation decision | M |
| T-10 | Gate-enforcement inconsistency across niches | BB/gaming pass title-as-hook to VISUAL_READY; other three reject | M |
| T-11 | `publish_silence` wording → "cadence miss (n/expected)" | alert is correct but reads as an outage | M |
| T-12 | Prune 65 untracked tuner `.bak`/`.lock` files | #227 blast radius | M |
