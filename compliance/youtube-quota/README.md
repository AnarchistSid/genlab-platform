# YouTube Data API v3 — Compliance Recording

Playwright scripts + Flask endpoint that produce the screencast the YouTube
API Services Team asked for in their review thread.

## What this produces

Two silent WebM recordings (converted to MP4 for the submission):

1. **`compliance-01-dashboard-approve`** — walks through the operator's real
   workflow: open the GenLab review dashboard, pick a `VISUAL_READY`
   blueprint, click Approve, wait for the pipeline to run `videos.insert`,
   then navigate to the resulting video on YouTube to prove it's live.
2. **`compliance-02-api-direct`** — surgical clip that hits a
   compliance-only Flask endpoint. That endpoint runs
   `YouTubeClient.publish(...)` server-side and returns the request +
   response payload as JSON, which the demo page renders on-screen so the
   reviewer can read exactly what `googleapis.com/youtube/v3/videos`
   received and returned.

Both recordings include a persistent header banner (client location,
timestamp, use-case one-liner) and step-by-step banners so the silent
video is still readable end-to-end.

## Prerequisites

- Node 20+ (`node --version`)
- The dashboard reachable from your machine (default: reads `DASHBOARD_URL`
  env var; falls back to `https://dashboard.your-domain.example`)
- Admin password for the dashboard basic-auth (`DASHBOARD_PASSWORD`)
- A test blueprint in `VISUAL_READY` for Script 1 (`TEST_BLUEPRINT_ID`).
  If your queue is often empty, seed one first via the pipeline.
- A small compliance-safe MP4 for Script 2 (`YT_COMPLIANCE_ASSET`) — the
  Flask endpoint uploads whatever path you point at, so pick a 10-30s
  test clip that's OK to publish (privacy will be `unlisted`).
- `ffmpeg` on PATH for the WebM → MP4 post-processing.

## Setup

```bash
cd compliance/youtube-quota
npm install
npx playwright install chromium
```

## Add the Flask endpoint (one-time)

The Python file is at `dashboard/server/api/yt_quota_demo.py` (already
created by this scaffold). It's off by default — enable per-run via
`GENLAB_YT_COMPLIANCE_DEMO=1`. Register it in
`dashboard/server/review_server.py` next to the other
`app.register_blueprint(...)` calls:

```python
from server.api.yt_quota_demo import bp as yt_quota_demo_bp
# ... alongside the other imports

app.register_blueprint(yt_quota_demo_bp)
# ... alongside the other register_blueprint calls
```

**Do not** leave `GENLAB_YT_COMPLIANCE_DEMO=1` set in production `.env` —
the endpoint's 404 gate depends on that flag being unset.

## Run

```bash
export DASHBOARD_URL='https://dashboard.your-domain.example'
export DASHBOARD_PASSWORD='<admin password>'
export TEST_BLUEPRINT_ID='<a VISUAL_READY blueprint UUID>'
export CLIENT_LOCATION='hetzner-nbg1-dc1'      # or wherever the VPS is
export GENLAB_YT_COMPLIANCE_DEMO=1              # on the DASHBOARD host
export YT_COMPLIANCE_ASSET='/absolute/path/to/small-test.mp4'
export YT_COMPLIANCE_NICHE='ai_creators'        # niche to publish under

npx playwright test
```

Recordings land in `recordings/<test-name>-chromium/video.webm`.

## Convert to MP4 + concatenate for submission

```bash
# Individual MP4s
for f in recordings/*/video.webm; do
  out="${f%.webm}.mp4"
  ffmpeg -y -i "$f" -c:v libx264 -crf 22 -preset fast -c:a aac "$out"
done

# Concatenated submission clip
{
  printf "file '%s'\n" recordings/*compliance-01*/video.mp4
  printf "file '%s'\n" recordings/*compliance-02*/video.mp4
} > /tmp/concat.txt
ffmpeg -y -f concat -safe 0 -i /tmp/concat.txt -c copy compliance-submission.mp4
```

Upload `compliance-submission.mp4` as an unlisted YouTube video (or your
Drive) and reply to the YouTube API Services email thread with the link.

## Reviewer-facing narrative to include in the email reply

Recommended reply body (adapt to your voice):

> Thanks for the follow-up. The attached screencast (link) covers the full
> use case in two segments:
>
> 1. **[0:00]** Operator opens the GenLab dashboard, reviews a
>    ready-to-publish short-form video blueprint that our pipeline
>    generated, and clicks Approve. The dashboard then invokes
>    `YouTubeClient.publish()` server-side, which calls
>    `POST https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status`.
>    The resulting video appears on our own channel (shown at the end of
>    the segment).
> 2. **[N:MM]** A compliance-only endpoint exposes the API request/response
>    verbatim so the exchange itself is visible. It runs the same code
>    path, uploads a short unlisted test clip, and displays the
>    `videos.insert` request + response JSON. The resulting video is then
>    opened on YouTube for verification.
>
> Client location: our production VPS (Hetzner, region NBG1). All uploads
> in this demo are to channels our team owns. Happy to answer follow-ups.

## What the scripts DON'T do

- No audio — pure Playwright `recordVideo` is silent. Every step is
  narrated via injected on-screen banners so the video is readable
  without sound.
- No terminal capture — everything visible is inside the browser
  viewport. The Flask endpoint compensates for that by putting the
  request/response payload IN the browser.
- No auto-seeding of a VISUAL_READY blueprint. Script 1 requires one
  in the queue at recording time. See the "seed a blueprint" note in
  README §Prerequisites.

## File map

```
compliance/youtube-quota/
├── README.md                                    (this file)
├── package.json
├── tsconfig.json
├── playwright.config.ts
├── .gitignore                                   (ignores node_modules, recordings)
└── tests/
    ├── _overlay.ts                              (banner helpers)
    ├── compliance-01-dashboard-approve.spec.ts
    └── compliance-02-api-direct.spec.ts

dashboard/server/api/
└── yt_quota_demo.py                             (compliance-only Flask blueprint)
```
