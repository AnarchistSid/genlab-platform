# Sprint 42 Blueprint Discrepancy Investigation

**Date:** 2026-03-14
**Audit finding:** "Sprint 42 blueprint discrepancy"
**Scope:** Cross-repo (genlab-core, Content Scraper, scripts). CriticalRush impacted as data consumer.

---

## What Sprint 42 Changed

Sprint 42 was a **niche_id consistency fix** across SharePoint lists. The core problem: early publishes (before multi-niche support) wrote inconsistent or missing `niche_id` values on Blueprints, Publishing_Analytics, and Content_Memory records. This caused data discrepancies when the dashboard and intelligence scripts tried to filter by niche.

### Fixes Delivered (Sprint 42)

| Fix | File | Description |
|-----|------|-------------|
| 1A | `Content Scraper/execution/publish_to_instagram.py` | `_safe_update_status()` now writes `error_log` field (truncated to 2000 chars) when status is ERROR. Previously error details were lost. |
| 1B | `genlab-core/src/genlab_core/http/backlog_client.py` | `log_publish_result()` conditionally includes `niche_id` in the Publishing_Analytics fields dict. Omits the field entirely when empty (avoids writing blank strings). |

### Backfill Scripts (Sprint 42)

Three one-time backfill scripts were created in `/GenLab/scripts/`:

| Script | Target List | Action |
|--------|-------------|--------|
| `backfill_content_memory_niche_id.py` | Content_Memory | Updates `niche_id` from `ai_tech` to `ai_creators` (canonical BB identifier) |
| `backfill_analytics_niche_id.py` | Publishing_Analytics | Joins analytics records to their parent Blueprint via `candidate_id` to resolve niche_id. Falls back to `ai_creators` for unlinked records. |
| `backfill_publishing_analytics_niche_id.py` | Publishing_Analytics | Alternative approach: joins via `blueprint` lookup field (SharePoint record ID) instead of `candidate_id`. Same goal. |

### Tests Added (Sprint 42)

| Test File | Validates |
|-----------|-----------|
| `genlab-core/tests/http/test_log_publish_niche.py` | `log_publish_result()` includes niche_id when provided, omits it when empty |
| `Content Scraper/tests/test_safe_update_status.py` | `_safe_update_status()` writes error_log on ERROR, omits on non-ERROR, truncates at 2000 chars |

---

## Impact on CriticalRush

CriticalRush's `push_to_backlog.py` already stamps `niche_id: "gaming"` on all stories and blueprints. The Sprint 42 discrepancy did **not** originate from CriticalRush -- it affected legacy BB records that lacked niche_id.

However, CriticalRush is indirectly affected:
- **Dashboard filtering:** The Focus Review queue filters by niche_id. Records without niche_id defaulted to `ai_creators`, making gaming blueprints invisible when "All" was selected (documented in TRIAGE_REPORT.md, issue #5).
- **Analytics joins:** Publishing_Analytics records for gaming publishes now reliably carry `niche_id=gaming` via the Fix 1B change to `log_publish_result()`.

---

## Data Inconsistencies

| List | Inconsistency | Resolved By |
|------|--------------|-------------|
| Content_Memory | Records written as `ai_tech` instead of `ai_creators` | `backfill_content_memory_niche_id.py` |
| Publishing_Analytics | Early records had no `niche_id` at all | `backfill_analytics_niche_id.py` / `backfill_publishing_analytics_niche_id.py` |
| Blueprints | CriticalRush blueprints already had `niche_id=gaming`; BB blueprints had inconsistent values | Upstream fix in `log_publish_result()` prevents future occurrences |

---

## Schema Changes

No JSON schema files were modified in Sprint 42. The changes were to runtime Python code (field population logic) and one-time data backfill scripts. The SharePoint list schemas (column definitions) were not altered -- `niche_id` columns already existed on all lists.

---

## Conclusion

Sprint 42 was a data hygiene sprint. The "blueprint discrepancy" refers to Publishing_Analytics and Content_Memory records that were missing or had incorrect `niche_id` values, causing cross-niche data leakage in dashboard views and analytics queries. The fixes were:
1. Code-level: ensure `niche_id` is always written on new records
2. Data-level: backfill scripts to correct historical records
3. Error handling: `error_log` field now captured on failed publishes

No code changes are needed in CriticalRush as a result of this investigation.
