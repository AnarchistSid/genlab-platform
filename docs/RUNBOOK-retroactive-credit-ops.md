# Runbook — Retroactive Creator Credit Operations

**Audience**: operator on-call for a Markanimation-style incident (creator
DMs Gen Lab asking why their content was reposted without credit).
**When to use**: after any period where the writer wire was broken and
posts shipped without a credit marker. Also for one-off requests from
creators.

**Do not** run this against posts already credited — it double-appends
the marker unless the state file catches them.

## What "credited" means

A live caption on Facebook / Instagram / YouTube / Threads / X that
contains one of:

- `"🎬 Original: @{creator} — {url}"` (writer marker)
- `"Footage: {url}"` (YT description marker)

Both are recognised by the Layer 5 monitor + the retro script's
idempotency check. See `docs/attribution-defense-stack.md` for the
canonical definition.

## The tools

- **`scripts/retro_credit_uncredited_posts.py`** — script that walks
  the last N days of PUBLISHED blueprints, filters state-known-credited
  ones out, and edits captions via Meta Graph API. State file at
  `/opt/genlab/.runtime/retro_credit_state.json`.
- **`genlab-retro-credit.timer`** — systemd timer that runs the script
  every 90 minutes automatically. Installed on prod; will keep firing
  until the state file catches all targets.
- **`genlab-retro-credit.service`** — systemd unit invoked by the
  timer. See `deploy/systemd/`.

## Command reference

Run on the prod VPS (`ssh root@46.224.237.56`):

```bash
cd /opt/genlab
set -a; source .env; set +a

# See what would happen without touching the API
.venv/bin/python scripts/retro_credit_uncredited_posts.py --dry-run --days 7

# Apply with default 3s pacing (safe for Meta rate limit)
.venv/bin/python scripts/retro_credit_uncredited_posts.py --apply --days 7

# Wider window, slower pacing (avoids app-scoped 200/hr limit)
.venv/bin/python scripts/retro_credit_uncredited_posts.py --apply --days 30 --sleep 5.0
```

**Idempotency**: safe to re-run. State file skips already-credited
posts before making any API calls.

## Understanding the output

```
Found 419 target (blueprint, platform) rows across last 30 days (pacing 3.0s).
State file has 60 already-credited keys.
Skipped 60 state-known-credited targets

  attempted_fb = 129        Facebook posts we tried to edit
  attempted_ig = 111        Instagram posts we tried to edit
  success_fb = 94           FB successes (Meta returned success:true)
  success_ig = 85           IG successes
  already_credited = 5      Live caption already had marker (check hit)
  skipped_no_creds = 0      Niche env var mapping missing (rare)
  skipped_no_url = 0        Blueprint had no video_url (rare)
  skipped_platform = 179    YT/Threads/X posts (script only edits FB+IG)
  failed = 1                Non-recoverable errors
```

## Common error patterns

### `(#4) Application request limit reached`

Meta's app-scoped rate limit fires after ~200 requests in a 1-hour
window. The script detects this and exits early. State file preserves
progress. Wait ≥1 hour (the systemd timer fires every 90 min anyway)
and re-run.

**Do not** try to work around by using different tokens — the limit is
app-scoped, not page-scoped. All 5 niches share the same bucket.

### `Object with ID '{X}' does not exist`

Post was deleted. This is a real permanent failure — mark the state
key credited anyway (or accept the -1 on the count). Most common when
we retro-fix a post that was already taken down by Meta / us.

### `shortcode not found in recent media`

The script walks the IG user's media feed to convert a shortcode to a
numeric media_id. If the post is old enough to be off the first 6 pages
(~300 posts), the lookup fails. Options:

1. Increase the walk depth in `_ig_shortcode_to_media_id` (currently 6
   pages of 50 = 300 posts)
2. Skip and accept the gap
3. Manually resolve via the IG post URL + edit via Meta Business Suite

## What the script does NOT do

| Platform | Status | Path forward |
|---|---|---|
| Facebook | ✅ Handled | Edit via `POST /{post_id}?message=...` |
| Instagram | ✅ Handled | Shortcode → media_id lookup + `POST /{media_id}?caption=...` |
| YouTube | ❌ Skipped | Would need OAuth refresh token per niche (not configured) |
| Threads | ❌ Skipped | Meta's Threads API doesn't document caption edits |
| X/Twitter | ❌ Skipped | Twitter API doesn't support post edits (ever) |
| TikTok | ❌ Skipped | Same as Threads — no documented edit endpoint |

**For YT**: manual edit via YouTube Studio. Time-consuming but the
credit-line goes in the video description which most audiences don't
see anyway.

**For Threads / X / TikTok**: consider a "quoted reply" that says
"🎬 Original: @creator" as an addition rather than an edit. Less
prominent but works around the API limitation.

## When to escalate

- Meta returns HTTP 400 code=100 subcode=33 across many posts →
  likely a bulk deletion event (Meta community standards violation
  hit us). Stop the script, investigate via `platform_posts` table +
  Meta Business Suite.
- Meta returns HTTP 190 (session expired) → the page token is dead.
  Re-provision the EAA token via the Meta App Dashboard. See
  `.claude/rules/security.md` for the Meta API rules.
- Script exits with `errors=N` where N is large → check the failures
  list. If failure reasons are all different, systemic issue; if all
  the same, targeted fix.

## Kill switches

**Disable timer** (stops all future automatic runs):

```bash
systemctl stop genlab-retro-credit.timer
systemctl disable genlab-retro-credit.timer
```

**One-off kill of a running script** (rare, script runs ≤20 min):

```bash
systemctl stop genlab-retro-credit.service
```

**Wipe state file** (restart from scratch — WARNING: will re-attempt
all posts, most will be "already_credited" via live check, but this
wastes API budget):

```bash
# DO NOT do this unless you've confirmed the state file is corrupted
mv /opt/genlab/.runtime/retro_credit_state.json /opt/genlab/.runtime/retro_credit_state.json.bak
```

## Post-incident checklist

After a retro-credit run:

- [ ] Verify state file grew (`jq '.credited | length' /opt/genlab/.runtime/retro_credit_state.json`)
- [ ] Check Layer 5 metric for the affected window
  (`/api/v1/attribution-health/stats?window_hours=168`)
- [ ] Sample 3-5 live posts on the actual platforms to confirm the
  credit line shows (Meta's read API has cache lag; use the audience-
  facing URL directly)
- [ ] If Meta returned errors, log to `compliance_events` for future
  audit
- [ ] Update the state file's `_source` field with a note explaining
  the incident (helps future ops understand what got credited when)

## Cross-refs

- `docs/attribution-defense-stack.md` — the 5-layer defense that
  prevents future incidents from producing uncredited posts
- `scripts/retro_credit_uncredited_posts.py` — the script itself
- `deploy/systemd/genlab-retro-credit.{service,timer}` — automation
- `memory/session-2026-07-13-audit-followup-writer-wire.md` — the
  Markanimation incident + writer wire fix that motivated this runbook
- `memory/class-of-bug-metric-proxies-mask-audience-facing-failures.md`
   — the class-of-bug the Layer 5 tightening exposed

## When NOT to use this runbook

- Post-2026-07-13 all new publishes carry the credit natively via the
  writer wire fix (PR #779). No retro is needed for fresh posts.
- If a creator asks for credit removal (not addition), this runbook
  doesn't apply — that's a delete-and-repost workflow, not a caption
  edit.
- For platforms this script doesn't handle (YT, Threads, X, TikTok),
  don't invoke the script — use the platform-specific manual path.

Last updated: 2026-07-13 (initial version, post-Markanimation arc).
