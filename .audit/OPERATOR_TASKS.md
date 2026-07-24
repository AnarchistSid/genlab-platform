# GenLab — Operator Task List

Consolidated 2026-07-24 (Phase 7.6). Every item below is blocked on operator authorization or console access. **None is an audit finding awaiting analysis.** Do not re-file as findings in future sessions.

| # | Task | Age | Effort | Owner | Verification |
|---|---|---|---|---|---|
| 1 | **Anthropic auto-reload.** `console.anthropic.com` → billing → auto-reload. Suggested $20 trigger, $50 top-up against $13.61/30d burn. | 5+ sessions | 1 console click | operator | Monitor line `live check returned 'exhausted'` changes to a non-zero balance. Re-check 7d for zero 402s. |
| 2 | **Port 5432 compose bind.** Edit `/opt/genlab/docker-compose.yml`: `ports: - "127.0.0.1:5432:5432"` (from `"5432:5432"`). Then `docker compose up -d postgres`. Take `.bak` first. | 60+ days | 1 line YAML + docker restart | operator (auto-mode classifier blocks) | `nc -zv 46.224.237.56 5432` refuses from off-box; `ss -tlnp \| grep 5432` shows `127.0.0.1` only; dashboard still up. |
| 3 | **Instrumentation deploy.** 37 lines / 2 files, uncommitted in Phase 7.5 working tree. All WARN-level, zero logic change. Files: `feedback_registration.py` (import/status/empty-post-id/exception/summary branches) + `parallel_publish.py:279` (falsy post_id). | 1 session | `git add + commit + push` locally; on prod `git pull` + `systemctl restart genlab-publisher` | operator | `journalctl -u genlab-publisher --since '1 hour ago' \| grep pf-instr` after next publisher run shows at least one WARN or clean pass with none. `git checkout -- <file>` reverts if needed. |
| 4 | **IG dual-ID forward fix (optional).** Add `platform_media_id` column to `publishing_analytics`; populate on IG publish. Both values available at publish time. Historical backfill NOT free — shortcode NOT derivable to media_id (Phase 7.6 verified). Skip backfill; forward writes only. | new (7.6) | 1 migration + 1-line PA writer + helper | operator (deploy path same as #3) | New IG PA rows have both `post_id` (shortcode) and `platform_media_id` (numeric) populated. |

**None of these is on the critical path for the learning loop** — Phase 7.6 confirmed the reward loop is closed at ~100% coverage on all four north-star platforms. Tasks 1 + 2 are the highest priority on their own merits (LLM outage + 60-day security exposure). Tasks 3 + 4 are diagnostic + audit-clarity, not defect fixes.
