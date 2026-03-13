# Meta App Review Submission Guide

**App ID:** 2203397347132949 (Blackbox Brief)
**Dashboard URL:** https://developers.facebook.com/apps/2203397347132949/

---

## Current Status

| Requirement | Status | URL |
|-------------|--------|-----|
| Privacy Policy page | READY | `https://review.aspirehub.ai/legal/privacy` |
| Data Deletion callback | READY | `https://review.aspirehub.ai/api/v1/webhooks/meta/deletion` |
| Data Deletion info page | READY | `https://review.aspirehub.ai/legal/deletion` |
| Webhook verification (GET) | READY | `https://review.aspirehub.ai/api/v1/webhooks/meta` |
| Webhook receiver (POST) | READY | `https://review.aspirehub.ai/api/v1/webhooks/meta` |
| Comment processor | READY | `genlab_core.engagement.comment_processor` |
| Toxicity gate | READY | `genlab_core.engagement.toxicity_gate` |
| Persona engine | READY | `genlab_core.engagement.persona_engine` |
| Spam filter | READY | `genlab_core.engagement.spam_filter` |
| META_APP_SECRET configured | TODO | See Step 2 below |

---

## Step 1: Configure App Settings in Meta Developer Dashboard

Go to: https://developers.facebook.com/apps/2203397347132949/settings/basic/

1. **Privacy Policy URL** — set to:
   ```
   https://review.aspirehub.ai/legal/privacy
   ```

2. **Data Deletion Callback URL** — set to:
   ```
   https://review.aspirehub.ai/api/v1/webhooks/meta/deletion
   ```

3. **Data Deletion Instructions URL** — set to:
   ```
   https://review.aspirehub.ai/legal/deletion
   ```

4. **App Domains** — add:
   ```
   aspirehub.ai
   ```

5. **Category** — select: `Business and Pages`

6. Click **Save Changes**.

---

## Step 2: Set META_APP_SECRET

1. Copy "App Secret" from: Settings > Basic > App Secret (click Show)

2. Add to Content Scraper `.env`:
   ```
   META_APP_SECRET=<your-app-secret>
   ```

3. Add to the review server wrapper script env vars:
   ```bash
   # In dashboard/runbooks/review_server_wrapper.sh
   export META_APP_SECRET="<your-app-secret>"
   export META_WEBHOOK_VERIFY_TOKEN="***REMOVED***"
   ```

4. Restart the dashboard:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.genlab.review-server
   ```

---

## Step 3: Set Up Webhook Subscription

Go to: https://developers.facebook.com/apps/2203397347132949/webhooks/

1. Click **Add Subscription** > select **Instagram**

2. **Callback URL:**
   ```
   https://review.aspirehub.ai/api/v1/webhooks/meta
   ```

3. **Verify Token:**
   ```
   ***REMOVED***
   ```

4. Click **Verify and Save** — Meta will send a GET request to your endpoint.
   The dashboard webhook receiver will respond with the challenge.

5. After verification, subscribe to the **comments** field.

---

## Step 4: Switch App to Live Mode

Go to: https://developers.facebook.com/apps/2203397347132949/settings/basic/

1. Toggle **App Mode** from "Development" to "Live"
2. This requires the Privacy Policy URL to be set (Step 1)

---

## Step 5: Submit App Review for instagram_manage_comments

Go to: https://developers.facebook.com/apps/2203397347132949/review/

### Permission: `instagram_manage_comments`

**Use Case Description** (copy this into the submission form):

---

> **How does your app use instagram_manage_comments?**
>
> We operate a network of branded content channels (Blackbox Brief, CriticalRush, ClutchWire, SpliceReel, FrameDrift) that publish AI-curated video content (Reels and Shorts) to Instagram.
>
> We use instagram_manage_comments to:
>
> 1. **Read comments** on our own Instagram posts to identify audience questions, feedback, and engagement opportunities.
>
> 2. **Reply to comments** on our own posts using an AI-powered engagement system that generates contextual, on-brand responses. Each channel has a distinct persona (e.g., Blackbox Brief uses a tech-informed tone, CriticalRush uses an enthusiastic gaming voice).
>
> 3. **Filter harmful content** — all inbound comments pass through a toxicity screening layer (using the Detoxify ML model) before processing. All outbound replies also pass through toxicity screening to ensure our responses are appropriate.
>
> We ONLY interact with comments on our own page's posts. We do not access, read, or reply to comments on other users' content.
>
> **Data handling:** Comments are processed in real-time and not stored in any persistent database. Comment text is sent to Anthropic's Claude API for reply generation without any personal identifiers. Engagement metrics (aggregate counts only) are retained for analytics.
>
> **Rate limiting:** Our system enforces strict rate limits (Instagram: 20 replies/hour) to prevent spam and comply with platform guidelines.

---

### Screencast Requirements

Record a 2-3 minute screencast showing:

1. **A published Instagram Reel** on one of your pages
2. **The comment engagement flow:**
   - Show the webhook receiving a comment notification (check dashboard logs)
   - Show the comment passing through toxicity screening
   - Show the AI-generated reply being created
   - Show the reply appearing on the Instagram post
3. **The dashboard** showing engagement metrics

**Recording tips:**
- Use QuickTime Player > File > New Screen Recording
- Show the terminal/logs alongside the Instagram post
- Narrate or add captions explaining each step
- Keep it under 3 minutes

You can trigger a test flow by:
```bash
# Post a comment on one of your Instagram posts manually
# Then check the webhook logs:
tail -f /Users/anarchistsid/GenLab/.logs/engagement_webhook.log
```

---

## Step 6: Additional Permissions (if needed)

These may also require App Review depending on your app's current state:

| Permission | Purpose | Already Working? |
|-----------|---------|------------------|
| `instagram_basic` | Read IG Business Account info | Yes (auto-granted) |
| `pages_manage_posts` | Publish to FB Page | Yes (publishing works) |
| `publish_video` | Upload videos/Reels | Yes (publishing works) |
| `pages_read_engagement` | Read engagement metrics | Yes (analytics works) |
| `instagram_manage_comments` | Read/reply to comments | **NEEDS APP REVIEW** |

If `pages_manage_posts`, `publish_video`, or `pages_read_engagement` also need review, use this description:

> We publish AI-curated video content (Reels/Shorts) to our own Instagram Business Account and Facebook Page. We use pages_manage_posts and publish_video to automate our publishing workflow, and pages_read_engagement to collect performance metrics (views, likes, reach, comments) for content optimization. All operations target our own page only.

---

## Verification After Submission

Once approved, verify with:
```bash
# Run the readiness checker
uv run --package genlab-core python genlab-core/scripts/meta_app_review_checklist.py

# Test webhook end-to-end
curl -X POST https://review.aspirehub.ai/api/v1/webhooks/meta \
  -H "Content-Type: application/json" \
  -d '{"object":"instagram","entry":[{"id":"test","changes":[{"field":"comments","value":{"id":"test_comment","text":"test","media":{"id":"test_media"}}}]}]}'
```
