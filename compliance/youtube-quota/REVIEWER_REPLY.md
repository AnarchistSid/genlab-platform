# Reviewer reply — YouTube API Services quota compliance

Send on the existing email thread. Do NOT open a new ticket / new thread — reply so
the reviewer's context carries.

Every `[OPERATOR: ...]` placeholder below MUST be filled before sending. Every one is
information that lives outside this repo (email inbox, Google Cloud Console, operator
memory).

**The single most important placeholder is the "Quota context" paragraph — it makes or
breaks the request. Read it, replace every bracket with the real growth plan and real
numbers, and re-read the paragraph cold before sending. A confident wrong number is
worse than an honest range.**

---

**Subject (keep the existing thread subject; do not change it. If starting fresh:)**

> Re: YouTube Data API v3 quota-review — GenLab (project `[OPERATOR: fill in GCP project number, e.g. 123456789012]`)

**Body:**

Hi `[OPERATOR: reviewer's first name from the email thread]`,

Thanks for the detailed follow-up. Attached (and also mirrored as an unlisted YouTube
link at `[OPERATOR: paste the unlisted upload URL, or "attached to this email" if
sending as a direct attachment]`) is a 2-minute screencast showing our exact use case
end-to-end.

**What the recording shows, mapped to your four asks:**

- **How we are using the YouTube Data API to upload videos** — ~0:55 → 1:55. An SSH
  session on our production host runs the same `YouTubeClient.publish()` method our
  daily pipeline uses. The terminal prints the request first (`POST
  https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=resumable`,
  snippet+status body, resumable upload), then the full `videos.insert` response body
  returned by googleapis.com (id, kind, etag, snippet, status, contentDetails). No
  mocks — this is the production client, the production OAuth bundle, and the same
  quota-gated code path as the daily pipeline.

- **Client location** — ~0:15 → 0:35. The terminal is an SSH session into our dashboard
  host on Hetzner Cloud in Nuremberg, Germany (region `nbg1`). `curl ipinfo.io` shows
  the client IP resolving to `AS24940 Hetzner Online GmbH`. Every one of our uploads
  originates from this host.

- **End result** — ~2:00 → 2:20. After the API returns the video ID, we open
  `https://youtube.com/shorts/<id>` in a browser tab (that's the URL our client
  produces from the API response); the video is live and playing. The URL bar shows
  the same video ID the API returned; the page shows the "Unlisted" privacy badge that
  matches our upload request.

- **Complete use case** — 0:00 → 2:30 (framing). GenLab is a video-first automated
  content platform for our own owned channels. We publish **one short-form video reel
  per channel per day** across five channels — Blackbox Brief (AI news), CriticalRush
  (gaming), ClutchWire (sports, channel `UC9QhmCY9PnWW5i4H8LgtJUg`), SpliceReel
  (movies, channel `UCdqiuSQOiSp-t3IFzhbcvzQ`), and FrameDrift (anime, channel
  `UCt7vXV-dzsgofLyZRAFbvIA`) — always at a fixed UTC time (06:30 UTC). Every upload
  uses `videos.insert` with `uploadType=resumable`, `privacyStatus=public` for daily
  content or `unlisted` for compliance samples like this one, and the OAuth 2.0
  refresh-token flow tied to each channel's own Google account. We do not read
  viewer data or automate any behaviour we couldn't do by hand as the channel owner.

**Quota context.** Under the current 10,000 units/day budget, our production
pipeline consumes:

- **Uploads:** 5/day × 1,600 units = **8,000 units** (one `videos.insert` per
  channel per day).
- **Reads:** approximately **200–500 units/day** on typical days. Breakdown:
  `search.list` at 100u/call for anime and AI-creator trend discovery
  (the two niches without a native `videoCategoryId`; the other three use
  `chart=mostPopular` at 1u), plus `videos.list` at 1u for per-post metric
  collection at 6h/24h/48h/168h windows, plus a top-creator upload watcher
  at 40u/day (4 fires × 10 creators × 1u), plus `channels.list` for token-health
  checks.
- **Daily total: ~8,200–8,500 units, ~85% of the 10,000-unit budget** on a
  normal day. Read-side spikes (a keyword-search fallback when an RSS feed is
  down, an extra metric-collection window on a viral post) periodically push
  the account into upload-blocked territory; we have an internal counter of
  those events and it fires several times per month.

That said, the load-bearing reason we're asking for an increase is planned scale,
not current pressure — we want to be honest about that so you're not comparing our
request to today's numbers:

**Planned cadence increase.** Short-form best practice for the platforms we
target is 2–3 Shorts per channel per day, not one. We are ramping toward that
cadence over the coming quarter. At 5 channels × 3 uploads × 1,600 units =
**24,000 units/day for uploads alone**, plus the read overhead above (which
scales roughly linearly with content velocity, so ~600–1,500u/day at the higher
cadence), plus retry headroom for resumable-upload chunk failures (~10% =
~2,400u/day). Total planned steady-state consumption:
**~27,000–28,000 units/day**.

We're requesting an increase to **`[OPERATOR: confirm the exact amount you
requested on the original form — verify by checking GCP Console → APIs →
YouTube Data API → Quotas; a pending request is visible there. Our engineering
estimate suggests 100,000 units/day to cover the planned cadence with headroom
for the SaaS multi-tenant expansion on our roadmap, but the number on the form
is what governs — please confirm it before sending]`** units/day. Happy to
adjust the number if a different ceiling better fits your review criteria.

Happy to answer anything else — reply on this thread and I'll turn it around same-day.

Best,
`[OPERATOR: your name]`
`[OPERATOR: your title / GenLab]`
Google Cloud project: `[OPERATOR: project number]`
API application name: `[OPERATOR: name shown on the OAuth consent screen]`

---

## Placeholder inventory (fill before sending)

Most numerical placeholders were pre-filled from repo evidence — see
`FINALIZATION_NOTES.md` §"Researched values" for provenance. The remaining
placeholders below all require information that lives outside the repo (your
inbox, your GCP console account, your identity).

| Placeholder | Source | Notes |
|---|---|---|
| GCP project number | Google Cloud Console → project picker | e.g. `123456789012`. Appears twice (subject line + signature). |
| Reviewer's first name | The email thread | Personalises the reply. |
| Unlisted YouTube link | You after uploading the screencast | See runbook for the "unlisted upload" step. Skip this line if you attach the MP4 directly. |
| **Requested quota amount** | Your original quota-increase form | GCP Console → APIs → YouTube Data API → Quotas shows any pending request. Our engineering estimate is 100K/day (matches the recommendation in `genlab_core/monitoring/youtube_quota.py:87`), but the number on the form is what governs — verify it before sending. Mismatch with the form would be a bigger credibility hit than a slightly-off engineering estimate. |
| Your name / title | You | Match the name on the API application. |
| API application name | GCP Console → OAuth consent screen | The public name your OAuth flow shows. |

### Auto-filled fields (verify, don't blindly trust)

The following fields were populated from repo evidence and are internally
consistent, but check them against reality once before sending:

- **Channel identifiers** (lines 55–61): display names for all 5 channels + UC-IDs
  for 3 (ClutchWire, SpliceReel, FrameDrift, from their `publishing.yaml`).
  Blackbox Brief and CriticalRush channel_IDs live only in prod `.env` and were
  omitted rather than guessed. If you'd rather send channel handles (`@...`) or
  add the missing two UC-IDs, edit the paragraph.
- **Daily quota consumption breakdown** (Quota context bullets): computed from
  `youtube_quota.py`'s `OPERATION_COSTS` table + the systemd timer cadences
  (`genlab-watch-top-creators.timer` explicitly states 40u/day; other reads
  estimated from `trending_video_fetcher.py:445` search-fallback logic). Matches
  the empirical 8,300/9,800 figure recorded in `youtube_quota.py:74-78`.
- **Planned cadence arithmetic** (Planned scale paragraph): 3 Shorts/channel/day
  is the researched target you named earlier in this task; if the actual
  roadmap number is different (2/day, 4/day, more channels, etc.), rewrite
  the arithmetic to match — the paragraph must reflect your real plan on the
  original form.

## Deliberate omissions (do NOT add unless the reviewer asks)

- **Do not attach code samples or architecture diagrams.** The recording is the
  evidence; a wall of unrequested docs invites more back-and-forth.
- **Do not describe the learning loop, bandits, or engagement automation.** The
  reviewer's ask is about *upload* usage, not the broader platform. Scope discipline.
- **Do not offer to "expand on any aspect."** Invitations to further explanation
  create round-trips. Close warmly; if they need more they'll ask.
- **Do not ask about the review timeline or when the increase might land.** The
  reviewer is the one on the clock, not us. Asking creates the impression of
  impatience.
