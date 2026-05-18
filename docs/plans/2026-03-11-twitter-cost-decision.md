# Twitter/X API Cost Decision — Sprint 30

**Decision needed:** Keep X/Twitter at $200/mo Basic plan, or disable entirely?

## Current State

- **Auth**: OAuth 1.0a for publishing, App-Only bearer for analytics
- **Tier**: Free tier (1 post/day limit) — hits 429 on post #2
- **Workaround**: `max_blueprints_per_run: 1` limits to 1 publish per daemon run
- **First live publish** (2026-03-08): X published 5/5 stories successfully

## Cost-Benefit

| | Free ($0) | Basic ($200/mo) |
|---|---|---|
| Posts/day | 1 | 4 |
| Posts/month | ~30 | ~120 |
| Cost/post | $0 | $1.67 |
| Meets 2/day schedule | No | Yes |
| Mentions polling | Included | Included |

## What Uses Twitter

| Feature | Impact if Removed |
|---------|-------------------|
| `publish_twitter.py` | No tweets posted. Other platforms unaffected (best_effort strategy) |
| `metric_collector._fetch_x()` | Returns `{}`. Dashboard shows "no API data" badge |
| `social_analytics.get_x_analytics()` | Returns error dict. Analytics tab degrades |
| `engagement_poller.poll_twitter_mentions()` | Returns empty list. No engagement replies |

**No hard dependencies.** All code paths gracefully degrade.

## Recommendation

**Disable Twitter.** Rationale:
1. Free tier is insufficient (1 post/day vs 2/day schedule)
2. $200/mo is $2,400/year for ~2% of total reach (IG Reels >> Twitter for tech/gaming)
3. Engagement replies not enabled — mentions polling is wasted
4. Simplifies credential management (5 env vars removed)
5. No cascading failures — removal is a 30-minute config change

## If Keeping

Upgrade to Basic ($200/mo) and:
- Fix bearer token auto-refresh (not in token_health.py currently)
- Enable thread publishing for carousel-length content
- Consider disabling mentions polling unless reply automation is planned

## Files to Modify (if disabling)

1. `BlackboxBrief/config/publishing.yaml` — remove "twitter" from enabled_platforms
2. `CriticalRush/niches/gaming/config/publishing.yaml` — same
3. `genlab-core/tools/credential_check.py` — move X to OPTIONAL_PLATFORMS
4. `genlab-core/engagement/poller.py` — remove `poll_twitter_mentions()`
5. Tests: `BlackboxBrief/tests/test_publish_twitter.py` — mark @skip

**Status: DISABLED (Sprint 30) — decision committed**
