# YouTube Data API v3 Quota-Increase — Recording Submission Plan

**Context (established from the reviewer's actual emails, not from prior session
summaries).** The YouTube API Services team has sent the same request three times, now
labelled "third and final notice." Verbatim:

> a complete screencast or video recording of **how you are using the YouTube Data API
> to upload videos** using **your client location** along with **the end result** so
> that we can verify the **complete use case**.

Two prior compliance attempts submitted a **silent, browser-only Playwright recording
of the dashboard**. That satisfies at most two of the four required elements and is
almost certainly why the submission keeps bouncing. A reviewer watching a browser click
"Approve" cannot see the `videos.insert` API call — it's happening on the server, out
of view.

This plan corrects that. The recording is shot on the **operator's desktop with
QuickTime or OBS**, capturing (a) an SSH terminal into the Hetzner VPS running one real
`videos.insert` invocation with request + full googleapis response printed to the
terminal, and (b) a browser tab showing the resulting video live on YouTube. The
dashboard may appear briefly as context but must NOT be the entire recording.

---

## Part 1 — Requirement → on-screen action

| # | Reviewer's verbatim ask | What must be on screen | Evidence source |
|---|---|---|---|
| 1 | "how you are using the YouTube Data API to upload videos" | An SSH terminal on the VPS running `python compliance/youtube-quota/run_compliance_upload.py --niche ai_creators --asset /opt/genlab/compliance-test.mp4`. The script prints (a) the request summary (endpoint = `POST https://www.googleapis.com/upload/youtube/v3/videos`, params `part=snippet,status&uploadType=resumable`, snippet+status body), then (b) the **full googleapis response body** captured in `PublishResult.raw_response["youtube_response"]` (added by commit `5591d546`). | Script: `compliance/youtube-quota/run_compliance_upload.py`. Uses the production `genlab_core.platforms.youtube.YouTubeClient.publish()` — no mocks. |
| 2 | "using your client location" | Same SSH session. First shot: `hostname` (shows the VPS hostname), then `curl -s ipinfo.io` (shows `city: "Nuremberg"`, `region: "Bavaria"`, `country: "DE"`, `org: "AS24940 Hetzner Online GmbH"`). Optional: `cat /etc/systemd/system/genlab-publisher.timer` snippet to establish this is the same host that runs the daily publish. | The terminal is executing on the production dashboard host. |
| 3 | "the end result" | After the script prints `Video is live at: https://youtube.com/shorts/<id>`, cut to the desktop browser tab and open that URL. The unlisted video plays on YouTube. Show the browser URL bar + the "Unlisted" badge under the title, so the reviewer sees the video-ID matches what the API returned. | The real YouTube watch page, in a browser, on the operator's desktop. |
| 4 | "complete use case" | Bookend framing. **Before** the upload shot: one shot of a currently-DRAFTED-or-VISUAL_READY blueprint in the dashboard's Focus Review (context: "this is the content our platform produces every day; the video below was rendered from that pipeline"). **After** the end-result shot: one caption on screen restating the flow in one sentence ("GenLab autonomously publishes one short-form reel per channel per day to five owned channels via the YouTube Data API"). | Dashboard is *contextual*, not central. |

The `run_compliance_upload.py` invocation is what makes elements 1, 2, 3 legible in
under two minutes. Element 4 is narration around it.

---

## Part 2 — Shot-list (ordered, timed, narrated)

**Recording tool: QuickTime Player (Mac) or OBS Studio, capturing the operator's full
desktop.** Playwright's `recordVideo` is the wrong tool here — it only captures a
headless browser and misses the terminal + API entirely. That is the correction to the
prior approach.

**Narration vs captions.** Given three bounces, **narration or clear on-screen captions
is strongly recommended** — the reviewer needs zero ambiguity. Silent + no captions is
what failed. Recommendation: voice narration (30–60 sec of talking, natural pace). If
narration isn't an option, add clean on-screen captions (large, high-contrast, one
sentence per shot). Do NOT ship silent with no captions.

**Length target: 2:00–2:30.** Short and complete beats long. If you can't say it in
2:30, cut a shot rather than adding one.

**Real-upload caveat.** This records a genuine upload to the real channel. Unlisted
is the standard and expected privacy for compliance demos — the video appears at the
watch URL for anyone with the link but doesn't hit the channel's public feed. Use a
real but low-stakes video (10–30 sec is plenty). Do NOT switch to a fake client or
sandbox project — the reviewer must see the same production client the daily pipeline
uses.

### Shot list

The operator's live typing is now reduced to **one command** — `./record_demo.sh` —
which paces the terminal shots (client location, asset, API exchange, end URL) with
section headers and `sleep`s tuned for a legible recording. See
`compliance/youtube-quota/record_demo.sh` for the source. This removes the six
separate typed commands that used to appear on-camera and eliminates the "fumble a
command mid-take" failure mode.

| # | Time | Screen | Narration / caption | Notes |
|---|---|---|---|---|
| 1 | 0:00–0:15 | Desktop with SSH terminal open at `/opt/genlab`, prompt visible | "This is GenLab, an automated social-media platform. It publishes short-form video reels to our own YouTube channels using the YouTube Data API v3. Here's how one upload happens — from our production host, through the API call, to the video going live." | Orient the viewer. Terminal font ~16pt for readability. |
| 2 | 0:15–2:00 | Terminal: `./compliance/youtube-quota/record_demo.sh` | Narrate over the paced output as each section header appears: **§1 Client Location** — "The client runs on our production host in Hetzner Nuremberg — you can see the IP resolving to `AS24940 Hetzner Online GmbH`." **§2 The video** — "This is the real MP4 we're about to upload." **§3 The API call** — "Now the actual `videos.insert` call. The request goes out first — endpoint, params, resumable upload. Then the full response body from googleapis — video ID, snippet, status, upload details." Pause for the response to scroll. **§4 End result banner** — "And here's the URL our client produces from the API response." | **Load-bearing shot — elements 1, 2, 4.** `record_demo.sh` inserts `sleep`s (tunable via `PACE` env, default 2s) between sections; each `════ N. TITLE ════` header gives the narrator a natural cue. Let the RESPONSE JSON hold the screen ≥3s. Total script runtime: ~1:30–1:45 (varies with upload progress). |
| 3 | 2:00–2:20 | Browser tab: paste the `/shorts/<id>` URL the script printed in its §4 banner — video playing, URL bar visible, "Unlisted" badge visible | "And the end result — the video is live on YouTube. Same ID the API returned, unlisted as we uploaded it." | Element 3 (end result). Script explicitly prints "→ NEXT: switch to the browser tab and open the URL above" so the operator has a visible handoff cue. |
| 4 | 2:20–2:30 | (Optional) Dashboard Focus Review OR a caption slide | "GenLab publishes one reel per channel per day across five channels — this compliance test used the same code path." | Element 4 wrap. Skip if 2:20 already feels tight. |

Total: 2:20–2:30 in one take. The operator's live actions during recording collapse
to: (a) run `./record_demo.sh`, (b) switch tab and paste the printed URL. Everything
else is narration over paced script output.

**Dry-run before the real take.** `./record_demo.sh --check` runs shots 1 + 2 for
real and shot 3 in dry-run mode (validates credentials + a cheap `channels.list`
read, no upload — costs 1 quota unit). If `--check` prints `✓ --check PASSED`, the
next no-flag run will succeed. This turns "hope the live take works" into "prove it
works, then record." Always run `--check` at least once before hitting record.

### Cross-reference: every email claim → the shot that proves it

If the email asserts something that isn't visible in the recording, the whole
submission reads as unreliable. Before recording, walk this table and confirm each
row's shot actually captures the claimed evidence.

| Claim in `REVIEWER_REPLY.md` | Shot that shows it | What must be legible on screen |
|---|---|---|
| "SSH session on our production host" | Shots 1–2 | The terminal prompt (e.g. `genlab@vps:/opt/genlab$`) — visible for the whole terminal segment |
| "`YouTubeClient.publish()` method our daily pipeline uses" | Shot 2 §3 header | Script's `§3` header literally says "same YouTubeClient.publish() the daily pipeline uses" and "invokes videos.insert with uploadType=resumable against googleapis.com/upload/youtube/v3/videos — the same call the daily publisher uses" |
| "The terminal prints the request first (`POST https://...videos?part=snippet,status&uploadType=resumable`)" | Shot 2 §3 | The `REQUEST — ...` block printed at the top of the Python child's output — endpoint + params visible |
| "the full videos.insert response body returned by googleapis.com (id, kind, etag, snippet, status, contentDetails)" | Shot 2 §3 | The `RESPONSE — ...` block, specifically the `raw_response.youtube_response` sub-object — let it scroll on screen ≥3 sec (bump `PACE=3` to lengthen the trailing pause) |
| "production OAuth bundle" | Shot 2 §3 log line | Script's `INFO` log line "YouTube: channel verified — <id>" prints during publish, evidencing the real OAuth token exchange |
| "Hetzner Cloud in Nuremberg, Germany" | Shot 2 §1 | `curl ipinfo.io \| jq` output showing `"region": "Bavaria"` (or `"city": "Nuremberg"` when populated), `"country": "DE"` |
| "client IP resolving to `AS24940 Hetzner Online GmbH`" | Shot 2 §1 | The `org` field in the `jq` output — this exact string is what ipinfo returns for Hetzner IPs |
| "URL bar shows the same video ID the API returned" | Shot 2 §4 → Shot 3 | Shot 2's §4 banner prints the `/shorts/<id>` URL in a boxed block; Shot 3's browser URL bar shows the same `<id>` |
| "'Unlisted' privacy badge that matches our upload request" | Shot 3 | The "Unlisted" badge under the video title on the YouTube watch page |
| "one short-form video reel per channel per day across five channels" | Shot 4 (optional) or narration | If shot 4 uses the dashboard, the queue view showing multi-niche activity is the visual; otherwise narration carries this one |

**If narrating live:** speak only what the current frame shows. If the frame doesn't
show what you're claiming, cut back to the shot that does. If a claim in the email has
no supporting shot, either add the shot before recording OR soften the claim so the
email describes only what's filmed.

---

## Part 3 — Reply email

See `REVIEWER_REPLY.md` in this directory. Send it on the existing email thread with
the screencast attached (or as an unlisted YouTube link — see runbook for tradeoffs).

## Part 4 — Clean deploy (operator commands)

See `OPERATOR_RUNBOOK.md`.

## Part 5 — Operator run-book

See `OPERATOR_RUNBOOK.md`.
