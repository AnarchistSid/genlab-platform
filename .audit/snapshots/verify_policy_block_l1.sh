#!/usr/bin/env bash
# Post-deploy verification for policy-block learning loop L1
# (commits 65f07548 / bc6ba499 / edf73774, deployed 2026-07-21 late).
# Safe to run any time after Wed 2026-07-22 12:05 IST publisher fire.
set -eu
echo "=== 1. Publisher status ==="
systemctl status genlab-publisher.service --no-pager -l | head -20
echo
echo "=== 2. Journal — any references to new policy_block wire ==="
journalctl -u genlab-publisher.service --since "24 hours ago" \
  | grep -iE "policy_block|POLICY_BLOCK|record_policy_block_event" \
  | head -30 || echo "(no policy_block mentions — expected if no code=368 this run)"
echo
echo "=== 3. Fresh platform_policy_block rows in last 24h ==="
PGPASSWORD=[REDACTED_BY_AUDIT_A_2026-07-29] psql -h 127.0.0.1 -U g*** -d g*** -c \
  "SELECT niche_id, platform, event_type, created_at, metadata->>'error_snippet' AS err
   FROM compliance_events
   WHERE event_type='platform_policy_block'
     AND created_at > NOW() - INTERVAL '24 hours'
   ORDER BY created_at DESC LIMIT 10;"
echo
echo "=== 4. Total platform_policy_block baseline ==="
PGPASSWORD=[REDACTED_BY_AUDIT_A_2026-07-29] psql -h 127.0.0.1 -U g*** -d g*** -c \
  "SELECT COUNT(*) AS total, MAX(created_at) AS most_recent
   FROM compliance_events WHERE event_type='platform_policy_block';"
echo
echo "=== 5. FAILED publish rows classified POLICY_BLOCK in last 24h ==="
PGPASSWORD=[REDACTED_BY_AUDIT_A_2026-07-29] psql -h 127.0.0.1 -U g*** -d g*** -c \
  "SELECT niche_id, platform, error_message, created_at
   FROM publishing_analytics
   WHERE status='FAILED'
     AND created_at > NOW() - INTERVAL '24 hours'
     AND (error_message ILIKE '%code=368%' OR error_message ILIKE '%temporarily blocked%')
   ORDER BY created_at DESC LIMIT 10;"
