# Finalization notes — pre-send fixes applied

Session goal: apply the four fixes flagged in review before the reviewer reply goes out.
No prod changes were made; only the compliance artifacts were edited in place.

---

## Diff summary

| File | Change | Why |
|---|---|---|
| `run_compliance_upload.py` | Removed the stray `Watch URL: https://www.youtube.com/watch?v={id}` line; changed docstring "watch/shorts URL" → "Shorts URL". Now prints only `Video is live at: {post_url}` (the `/shorts/<id>` form) plus a `Video ID: {id}` line. | The script was printing TWO URLs — `post_url` (`/shorts/<id>`) and a hand-built `/watch?v=<id>`. That's the source of the artifact-vs-doc split. One URL, consistent everywhere. |
| `SUBMISSION_PLAN.md` shot 2 | `curl -s ipinfo.io` → `curl -s ipinfo.io \| jq '{ip, city, region, country, org}'`; added note that the `org` line is the exact string the email quotes; fallback for no-`jq` machines. | The email asserts "resolving to `AS24940 Hetzner Online GmbH`". Piping through `jq` makes that specific `org` line legible on camera; the raw ipinfo JSON works too but is noisier. |
| `SUBMISSION_PLAN.md` shot 6 | `youtube.com/watch?v=<id>` → `youtube.com/shorts/<id>`; added note explaining the choice ties the browser URL bar to `PublishResult.post_url` printed in shot 5. | Reconciled to the truthful form (see §URL reconciliation below). Makes the ID-match unambiguous on camera. |
| `SUBMISSION_PLAN.md` new section | Added a "Cross-reference: every email claim → the shot that proves it" table between the shot-list and Part 3. | Fix 3 — every factual claim in the email must be visible in the recording. The table is the checklist that stops the operator narrating off-camera. |
| `REVIEWER_REPLY.md` header | Added a bold callout above the body: "The single most important placeholder is the Quota context paragraph …" | Puts the highest-risk edit at eye level so the operator can't miss it. |
| `REVIEWER_REPLY.md` timecode | "0:35 → 1:35" (upload shot) → "0:50 → 1:35" | Aligns with the actual shot-list (shot 4 = 0:35–0:50 script cat, shot 5 = 0:50–1:35 upload). |
| `REVIEWER_REPLY.md` end-result URL | `youtube.com/watch?v=<id>` → `youtube.com/shorts/<id>` with note "that's the URL our client produces from the API response". | Same reconciliation. |
| `REVIEWER_REPLY.md` phrase | "production quota tracker" → "same quota-gated code path as the daily pipeline" | Fix 3 — the quota tracker doesn't print anything to stdout on a successful publish, so the previous claim wasn't visible on camera. Soften to something demonstrably true from the script code visible in shot 4. |
| `REVIEWER_REPLY.md` Quota context | Complete rewrite. Replaced the "6-7 uploads/day, requesting increase" paragraph with a structured three-part rebuild: current usage arithmetic (uploads + reads + total), honest baseline framing, and a growth-plan block with four adaptable templates. | Fix 2. The prior paragraph argued *against* the request (8,000 of 10,000 = plenty of headroom). Reviewers approve when the *need* is legible; this makes it so. |
| `REVIEWER_REPLY.md` placeholder inventory | Rewrote with dangerous-vs-safe distinction. New rows for the read-units figure, daily-total figure, and growth-plan block. Removed the bare `[OPERATOR: 6-7]` — replaced with computed ranges or clearly-flagged operator inputs. | Fix 4. |
| `FINALIZATION_NOTES.md` (this file) | New. | Diff record + pre-send checklist. |

**Not touched:** `OPERATOR_RUNBOOK.md` (nothing in it contradicted the fixes),
`README.md` (legacy README from ccd03d54; not part of the outbound package).

---

## URL reconciliation — evidence

Truth: `genlab-core/src/genlab_core/platforms/youtube.py:508`:

```python
post_url=f"https://youtube.com/shorts/{video_id}",
```

That's what `PublishResult.post_url` contains for every successful upload the daily
pipeline produces. The CLI script's `Video is live at: {result.post_url}` line
therefore prints `https://youtube.com/shorts/<id>`.

Post-fix grep across all three artifacts:

```
$ grep -n 'watch?v=\|shorts/' compliance/youtube-quota/run_compliance_upload.py \
    compliance/youtube-quota/SUBMISSION_PLAN.md \
    compliance/youtube-quota/REVIEWER_REPLY.md
```

Every remaining hit is `/shorts/<id>`. YouTube auto-redirects `/watch?v=<id>` →
`/shorts/<id>` for uploads that are Shorts, so opening either works — but keeping
one form everywhere means the video-ID on the reviewer's screen matches character-for-
character between the terminal output (shot 5) and the browser URL bar (shot 6). That
character-for-character match is the whole point of the end-result shot.

---

## Rewritten Quota context — evidence

Sources in the repo that back the numbers:

- `genlab-core/src/genlab_core/monitoring/youtube_quota.py:68` — `UPLOAD_COST: int = 1_600`
- `genlab-core/src/genlab_core/monitoring/youtube_quota.py:92-105` — full cost table:
  `upload: 1600, thumbnail_set: 50, comment_list: 1, analytics_query: 1,
  channel_list: 1, playlist_insert: 50, search: 100, video_list: 1`
- `genlab-core/src/genlab_core/monitoring/youtube_quota.py:69-88` — history block on
  `HARD_STOP_PCT` documents that the pipeline hit 9 upload-blocked events/month at 84.7%
  budget consumption when the hard-stop was 9,800u and headroom was 200u. Moved to
  100% hard-stop 2026-07-16 to trade rare overshoot (handled by the retry-next-day
  path in `error_classifier.py`) for fewer lost publish windows. The same comment
  explicitly names the real fix: "apply for YouTube quota increase (100K/day)."
- Call sites for read ops (grep confirmed):
  - `media/trending_video_fetcher.py:992` — `_quota.record("search", niche_id=...)` for
    keyword-driven trend discovery
  - `engagement/outbound_youtube_fetcher.py:72` — `tracker.record("video_list" if
    units == 1 else "search", niche_id="all")`

The rewritten paragraph:

1. States current consumption as arithmetic (5 × 1,600 = 8,000 uploads, plus
   `[OPERATOR: 300–500]` reads, total `[OPERATOR: 8,300–8,500]` — ~85% of budget).
2. Names the current constraint honestly (read spikes push into upload-blocked
   territory periodically — mirrors the code comment).
3. Says explicitly that **planned scale** is the load-bearing reason, not current
   pressure. Templates (a)–(d) let the operator fill in cadence increase, new
   channels, deeper analytics, or an honest "requesting ahead of scale" — whichever
   is real.
4. Ties the requested-amount ask to the arithmetic in template (a)–(d), so the number
   isn't arbitrary.

**Do not fill any of these figures without checking prod state.** The code gives us a
ceiling estimate; the operator's `~/.genlab/youtube_quota.json` on the VPS shows what
today actually consumed.

---

## Email-claim → shot cross-reference (also in `SUBMISSION_PLAN.md`)

Copied here so it's the single source of truth. When narrating, only speak to what's
in the frame.

| Claim in `REVIEWER_REPLY.md` | Shot | On-screen evidence |
|---|---|---|
| "SSH session on our production host" | 1–5 | Terminal prompt visible |
| "`YouTubeClient.publish()` method our daily pipeline uses" | 4 | `cat` output showing the import + `client.publish(payload)` |
| "The terminal prints the request first (`POST https://...videos?part=snippet,status&uploadType=resumable`)" | 5 | `REQUEST — ...` block at top of script output |
| "the full videos.insert response body returned by googleapis.com" | 5 | `RESPONSE — ...` block, `raw_response.youtube_response` sub-object; hold ≥3 sec |
| "production OAuth bundle" | 4 + 5 | Script shows `resolve_youtube_credentials`; publish logs "channel verified — <id>" |
| "Hetzner Cloud in Nuremberg, Germany" | 2 | `ipinfo.io \| jq` shows `city: Nuremberg`, `country: DE` |
| "IP resolving to `AS24940 Hetzner Online GmbH`" | 2 | The `org` line in the same `jq` output |
| "URL bar shows the same video ID the API returned" | 5 → 6 | Same `<id>` in shot 5's `post_url` line and shot 6's browser URL bar |
| "'Unlisted' privacy badge that matches our upload request" | 6 | Badge under the video title on the YouTube page |
| "one short-form video reel per channel per day across five channels" | 7 (opt.) or narration | Dashboard queue view, or spoken |

---

## Placeholder inventory — with fill-sources and danger flags

| # | Placeholder (verbatim) | Fill source | Danger? |
|---|---|---|---|
| 1 | `[OPERATOR: fill in GCP project number, e.g. 123456789012]` (subject) | GCP Console → project picker | Low — wrong number bounces the thread but doesn't misrepresent |
| 2 | `[OPERATOR: reviewer's first name from the email thread]` | Email thread | Low |
| 3 | `[OPERATOR: paste the unlisted upload URL, or "attached to this email" if sending as a direct attachment]` | You after uploading the screencast | Low |
| 4 | `[OPERATOR: list your public YouTube channel handles here — e.g. @BlackboxBrief, @CriticalRush, @ClutchWire, @SpliceReel, @FrameDrift]` | YouTube Studio | Low — the placeholder inside gives real defaults |
| 5 | `[OPERATOR: 300–500 — verify from a day of prod logs …]` (read units/day) | `journalctl` grep or `~/.genlab/youtube_quota.json` on the VPS | **HIGH** — stated as fact in the arithmetic |
| 6 | `[OPERATOR: 8,300–8,500 based on the above]` (daily total) | Uploads (8,000) + reads figure above | **HIGH** — stated as fact; must match arithmetic |
| 7 | `[OPERATOR: this is the MUST-VERIFY paragraph. … templates (a)–(d) …]` (growth plan) | Product roadmap + original quota form | **HIGHEST** — the whole justification |
| 8 | `[OPERATOR: fill in the exact quota amount you requested on the original form — verify by checking GCP Console → APIs → YouTube Data API → Quotas …]` | GCP Console → Quotas | **HIGH** — must be internally consistent with template (a)–(d)'s arithmetic |
| 9 | `[OPERATOR: your name]` | You | Low |
| 10 | `[OPERATOR: your title / GenLab]` | You | Low |
| 11 | `[OPERATOR: project number]` (signature) | Same as #1 | Low (should equal #1) |
| 12 | `[OPERATOR: name shown on the OAuth consent screen]` | GCP Console → OAuth consent screen | Low |

Grep confirms the body and the inventory list the same set (12 placeholders including
the 2 duplicate project-number references). Post-edit verification (some placeholders
span multiple lines so a naïve `[OPERATOR:[^]]*]` regex would undercount; count the
openings instead and subtract the 1 meta reference in the header instruction):

```
$ grep -c '\[OPERATOR:' compliance/youtube-quota/REVIEWER_REPLY.md
13    # 12 real placeholders + 1 meta reference on line 6
```

---

## Researched values — provenance (second pass, fills-in-place)

The operator asked whether we could research the placeholder values instead of
leaving them all for manual fill. Second pass filled everything derivable from
the repo; only truly-external items remain as `[OPERATOR: ...]`. Provenance for
each auto-filled value below.

### Channel identifiers (Complete-use-case bullet, lines 55–61)

| Niche | Display name (from `niches_registry.yaml`) | Channel ID | Source |
|---|---|---|---|
| ai_creators | Blackbox Brief | not in repo config (lives in `.env`) | Omitted rather than guessed |
| gaming | CriticalRush | not in repo config (lives in `.env`) | Omitted rather than guessed |
| sports | ClutchWire | `UC9QhmCY9PnWW5i4H8LgtJUg` | `ClutchWire/config/publishing.yaml` |
| movies | SpliceReel | `UCdqiuSQOiSp-t3IFzhbcvzQ` | `SpliceReel/config/publishing.yaml` |
| anime | FrameDrift | `UCt7vXV-dzsgofLyZRAFbvIA` | `FrameDrift/config/publishing.yaml` |

The `@` handles were NOT filled because the repo only stores the Instagram
handle for BlackboxBrief (`@blackbox.brief` in `instagram_specs.yaml:346`), and
YouTube channel handles aren't guaranteed to match either the display name or
the Instagram handle. Display name + channel ID is unambiguous for the reviewer;
they can resolve either to a channel page.

### Read units/day (Quota-context bullet, "~200–500")

Derived from `OPERATION_COSTS` in `genlab_core/monitoring/youtube_quota.py:92-105`
crossed with systemd timer cadences:

| Source | Cost per fire | Fires/day | Daily units |
|---|---|---|---|
| `genlab-watch-top-creators.timer` (comment-documented: "4 fires × 10 creators × 1u") | 10 | 4 | 40 |
| `genlab-metric-collector.timer` (`OnUnitActiveSec=60min`, polls active posts at `videos.list` = 1u) | ~5–20 | 24 (but batched at 6h/24h/48h/168h windows per CLAUDE.md) | ~20–80 |
| `genlab-insights-collector.timer` (2 fires/day, analytics_query = 1u × 5 niches) | 5 | 2 | 10 |
| `genlab-pipeline-*.timer` × 5 niches — `trending_video_fetcher.py:445` uses `search.list` (100u) only when `allow_keyword_search=True` AND (candidates<3 OR no category). Anime + AI_creators typically hit this; the other 3 use `videoCategoryId` on `chart=mostPopular` (1u). | 100 or 1 | 5 (1 per niche) | ~100–200 (2 search-fallback niches × 100u, others 1u) |
| `genlab-anticipate-trends.service` (daily 03:30 UTC per CLAUDE.md; usage depends on keyword set) | varies | 1 | ~50–200 |
| Total | | | **~220–530** |

Range in reply: "200–500 units/day". Consistent with the empirical
`8300/9800 = 84.7%` datapoint in `youtube_quota.py:74-78` (uploads 8,000 +
observed reads ≈ 8,300 → reads ≈ 300).

### Daily total (~8,200–8,500)

Simple arithmetic: 8,000 uploads + 200–500 reads. Matches the historical
comment block in `youtube_quota.py:69-88` which documents 9 upload-blocked
events per month at 84.7% budget consumption — that's exactly this range.

### Planned cadence arithmetic (Planned-scale paragraph)

Grounded in the operator's own earlier prompt: *"the researched 2–3
Shorts/channel/day → up to 15/day"*. Used 3/channel/day = 15 total:

- Uploads: 15 × 1,600 = 24,000u/day
- Reads scale linearly with content: 3× current = 600–1,500u/day
- Retry headroom: ~10% × uploads = 2,400u/day
- **Total ~27,000–28,000 units/day**

Marked in the inventory as "verify against your actual roadmap" — if the real
plan is different, the arithmetic must be rewritten to match.

### Requested quota amount (100,000/day suggestion)

Sourced from `genlab_core/monitoring/youtube_quota.py:87` — verbatim engineering
comment: `Real fix (external): apply for YouTube quota increase (100K/day) to
remove this constraint entirely.` LEFT AS `[OPERATOR: ...]` because the number
on the original form (submitted before this session) is what the reviewer will
compare against — if the form asked for a different number, matching the form
matters more than matching engineering intent. The placeholder text names 100K
as the engineering estimate and asks the operator to verify.

### Left as `[OPERATOR: ...]` — genuinely external

- **GCP project number** — could be in `.env`, deliberately not read (secrets
  hygiene, and it's a fast console-lookup for the operator anyway).
- **Reviewer's first name** — lives in the email inbox.
- **Unlisted YouTube link** — only exists after the recording is uploaded.
- **Requested quota amount** — must match the original form submission (see above).
- **Your name / title** — operator identity.
- **API application name** — GCP Console → OAuth consent screen; not in repo.

---

## Operator pre-send checklist

Do these in order. Steps 1–4 are the finalization gate; 5 onward is the existing
recording flow from `OPERATOR_RUNBOOK.md`.

1. **Audit the auto-filled numeric claims.** Second-pass research filled the reads
   figure, the daily total, and the planned-cadence arithmetic from repo evidence.
   Provenance for each is documented above under §"Researched values". Read those
   bullets in `REVIEWER_REPLY.md`'s Quota-context paragraph and ask:
   - Does "~200–500 reads/day" match `cat ~/.genlab/youtube_quota.json` on the VPS?
   - Does the "3 Shorts/channel/day starting <coming quarter>" plan match what you
     submitted on the original quota-increase form? If not, rewrite the arithmetic
     (line 105 onwards in the reply body).
   - Is 100,000 units/day the number you asked for on the form? If not, edit the
     `[OPERATOR: ...]` block on the "requesting an increase to" line to match the
     form. Mismatch with the form is a bigger credibility hit than an off-by-2×
     engineering estimate.

2. **Fill the 6 remaining external placeholders:**
   - GCP project number (subject + signature — 2 uses of the same value)
   - Reviewer's first name (from the email thread)
   - Channel handles — optional refinement of the auto-filled display names + UC-IDs
     (if you want `@handles` instead, edit lines 55–61)
   - Your name + title
   - OAuth application name (GCP Console → OAuth consent screen)
   - Requested quota amount (verify against the form — see step 1)
   - Unlisted YouTube link (added after recording is uploaded)

3. **Read the whole email cold, as if you were the reviewer.** Ask: is the *need*
   for the increase legible in one read? Skim only the Quota-context paragraph —
   would you approve? If not, tighten the planned-cadence arithmetic.

4. **Confirm the recording will show every claimed element.** Walk the "Cross-
   reference" table in `SUBMISSION_PLAN.md`. If narrating, only speak to what's in
   frame — the table is your narration checklist.

5. **Cherry-pick + record + send flow.** Follow `OPERATOR_RUNBOOK.md` Part 4 (cherry-
   pick `ccd03d54 5591d546` onto a fresh `compliance/yt-quota-recording` branch off
   `main`, PR, deploy) then Part 5 (pre-flight → record → post-record → send within
   the 7-business-day window).

If any of steps 1–4 reveals something the recording doesn't support, fix the email
or add a shot BEFORE recording — never after. The reviewer will notice mismatches;
they've bounced this twice already.
