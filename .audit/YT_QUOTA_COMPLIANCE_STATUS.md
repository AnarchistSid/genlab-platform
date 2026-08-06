# YouTube Data API v3 — Quota-Increase Compliance: Ground-Truth Status

Read-only discovery pass. Every claim below is backed by a command + its output; anything the
repo does not support is flagged rather than filled in. Verdict at the bottom.

**Scope note:** this doc describes the state of the repo as of branch
`audit-a/fix-queue-execution` (HEAD `5591d546`). The RLS migration
`a0b0c0d0e0f0_rls_deny_by_default` is on the same branch, apply-blocked, and unrelated to this
task; it is not disturbed by anything here.

---

## Part 1 — What the quota-increase actually requires

### Repository artifacts

- `compliance/youtube-quota/README.md` — 153 lines. Describes what the scaffold produces
  (two silent WebM clips), setup, run, ffmpeg post-processing, and a **suggested** reply
  narrative to send back to the reviewer.
- `dashboard/server/api/yt_quota_demo.py` — the compliance-only Flask blueprint.
- No other doc, no `.eml`, no draft-response file, no ticket, no reviewer-email transcript
  anywhere in the repo. Verified via:
  ```
  rg -il 'youtube.*api.*services|api services team|screencast|third and final|final notice' \
     --type-add 'doc:*.{md,txt,html,eml}' -tdoc
  ```
  → only `compliance/youtube-quota/README.md` and unrelated planning docs.
- Zero mentions in `.audit/OPERATOR_ACTIONS.md`, `.audit/OPERATOR_TASKS.md`, or
  `.audit/BACKLOG.md`. Verified via grep on all three files.

### What the reviewer actually asked for

**Not documented in the repo.** The only in-repo hints are:

1. `ccd03d54` commit message (the compliance-scaffold commit) references
   "the YouTube API Services Team … their ongoing quota review thread (third-and-final notice
   email)." That's the entire in-repo trace of the reviewer's requirements.
2. `compliance/youtube-quota/README.md` §"Reviewer-facing narrative" contains a **suggested**
   reply body — but that's what to send, not what the reviewer asked for.

The reviewer's specific asks (repeated requests for "the complete use case", potential
requirements around narration/terminal context, whatever bounces have already happened) live
in the operator's email inbox, not in the repo.

### What a passing submission needs to show

Inferred from the scaffold's design + suggested reply, **not from any authoritative source
in the repo**:

- The application (GenLab dashboard) triggering an upload via a real user workflow
- The `videos.insert` API call to `googleapis.com/upload/youtube/v3/videos` actually happening
- The resulting video visible on YouTube (proof of end-to-end)
- Client location (Hetzner NBG1 per the scaffold's default header text)

**Finding 1.A (load-bearing gap):** the submission requirements are not captured in the
repo. Any Claude session or human working on this from a cold start has to reconstruct them
from the operator's memory or the email thread. If the reviewer has bounced this three times
already (per the `ccd03d54` "third-and-final notice" language), the specific reasons for each
bounce are also missing — so the current scaffold's design decisions (silent video, browser-
only visibility) are betting on unverified assumptions about what will pass. See Part 3
fragility flag 2.

---

## Part 2 — The real YouTube publish path

### Client location

- Module: `genlab-core/src/genlab_core/platforms/youtube.py`
- Public method the pipeline calls: `YouTubeClient.publish(payload: PublishPayload) -> PublishResult`
  (line 302). Not `upload_video` — that name does not exist as a method today.
- Private helper for the resumable upload: `_upload_video` (line 554).

### Current return shape (post-`5591d546`)

- `_upload_video` returns `tuple[str, dict[str, Any]]` — `(video_id, response)` where
  `response` is the googleapis body from the final chunk of the resumable upload
  (verified at line 639: `return video_id, response`).
- `publish()` line 471 unpacks: `video_id, upload_response = self._upload_video(...)`
- `publish()` line 504-521 constructs the `PublishResult` with:
  ```python
  raw_response = {
      "video_id": video_id,
      "title": title,
      "youtube_response": upload_response,  # NEW — full googleapis body
  }
  ```
- Historical `video_id` and `title` fields preserved verbatim.

### Commit `5591d546` — verification

- **Exists**: `git log --oneline | grep 5591d546` → present, `feat(youtube): capture full
  googleapis videos.insert response in PublishResult`.
- **Diff scope**: `git show 5591d546 --stat` → touches only
  `genlab-core/src/genlab_core/platforms/youtube.py` (+22/-6 lines).
- **Branch**: `git branch --contains 5591d546` → only `audit-a/fix-queue-execution`.
  **Not on `main`.** Deployment still requires the branch to land on main and prod to be
  redeployed.

### `_upload_video` caller safety check (§2.3 — the load-bearing gate)

Command: `rg -n '_upload_video\b' --type py | grep -v node_modules`

Output (verbatim):
```
genlab-core/src/genlab_core/platforms/youtube.py:471:            video_id, upload_response = self._upload_video(
genlab-core/src/genlab_core/platforms/youtube.py:554:    def _upload_video(
```

- **Total call sites in the repo: 1** (line 471) plus the definition itself (line 554).
- Line 471 unpacks the tuple. ✓ HANDLES NEW SHAPE.

### Test-caller check

Command: `rg -n '_upload_video' --type py | grep -Ei 'test|spec'`

Output:
```
dashboard/tests/test_p3_polish.py:161:    def test_upload_video_retries_on_chunk_error(self, mock_sleep, tmp_path):
```

That match is a test **function name**, not a call to `_upload_video` (the method).
The test in question:
- Line 91 of the same file has `@pytest.mark.skip(reason="Tests BB-specific
  upload_short/upload_video — genlab-core uses publish()")` at class level → the whole
  test class is skipped.
- Line 181 inside that class calls `client.upload_video(...)` — a method that **does not
  exist** in current source. Confirmed via `grep -n 'def upload_video\|def _upload_video'
  genlab-core/src/genlab_core/platforms/youtube.py` → only `_upload_video` at 554.
- Because the class is `@pytest.mark.skip`-ed, the missing-method call is never exercised.

**Part 2.3 verdict: SAFE.** All `_upload_video` callers handle the new tuple shape. There is
exactly one prod caller, it unpacks correctly, and the one test that mentions
`upload_video` is skipped and calls a non-existent method — no runtime path is broken.

---

## Part 3 — The compliance scaffold's real state

### Files present + parse-clean

```
compliance/youtube-quota/
├── README.md                                    (153 lines, verified)
├── package.json                                 (pins @playwright/test ^1.55, TS ^5.6)
├── package-lock.json                            (present — committed via ccd03d54)
├── tsconfig.json
├── .gitignore                                   (node_modules/, recordings/, test-results/)
├── playwright.config.ts                         (headed 1440×900 recordVideo, single worker)
├── node_modules/                                (installed locally, gitignored)
└── tests/
    ├── _overlay.ts                              (pinHeader + step helpers, textContent-only)
    ├── compliance-01-dashboard-approve.spec.ts
    └── compliance-02-api-direct.spec.ts

dashboard/server/api/yt_quota_demo.py            (Flask blueprint, PARSE OK)
```

- Both Python files (`yt_quota_demo.py`, `review_server.py`) parse via
  `python3 -c "import ast; ast.parse(...)"`.
- Blueprint IS registered in `dashboard/server/review_server.py`:
  - Line 827: `from server.api.yt_quota_demo import bp as yt_quota_demo_bp`
  - Line 851: `app.register_blueprint(yt_quota_demo_bp)`
- URL prefix: `/api/v1/yt-quota-demo` (from `yt_quota_demo.py` — distinct from the existing
  `compliance_api` blueprint at `/api/v1/compliance`; no collision).
- Response passthrough verified: `yt_quota_demo.py:285` returns
  `"raw_response": result.raw_response` verbatim, so the `youtube_response` field added by
  commit `5591d546` will reach the browser demo page.

### What the two specs do (verified by direct read)

**`compliance-01-dashboard-approve.spec.ts`** (~180 lines):
1. Goto `/`, pin persistent header.
2. Click a link matching regex `/focus review|review|queue/i` (TODO — may need selector fix).
3. If `TEST_BLUEPRINT_ID` env var set → goto `/focus-review/<uuid>`.
   Otherwise → click first row matching
   `[data-testid="blueprint-row"], .blueprint-card, article` (TODO — real markup may differ).
4. Wait for the rendered video element, then click a button matching text `/approve/i`
   (TODO — actual button may be "Approve" or "Approve & Schedule").
5. Wait for a POST matching `/api/v1/blueprints/[^/]+/(approve|approve-and-schedule|publish)`.
6. Poll every 15s (up to 3 min) for a "PUBLISHED" text badge to appear (TODO — status
   may live in a specific badge component).
7. Extract YouTube URL from `<a href="youtube.com/watch">`; open it in a new tab and
   pin a "verify uploaded video" header.

**`compliance-02-api-direct.spec.ts`** (~90 lines):
1. Goto `/api/v1/yt-quota-demo/page`, pin header.
2. Click `#trigger-upload` (deterministic selector — the compliance page controls its own
   markup, no dashboard drift).
3. Wait for `#response-pre` to contain `post_id` or `videoId`.
4. Extract video ID from `#video-id`; goto `youtube.com/watch?v=<id>` and pin header.

### Env-var inventory (operator inputs)

**Client-side (Mac, invoking Playwright)** — verified via
`grep -n 'process\.env\.' compliance/youtube-quota/tests/*.ts compliance/youtube-quota/playwright.config.ts`:

| Var | Used at | Default | Required |
|---|---|---|---|
| `DASHBOARD_URL` | playwright.config.ts:22 | `https://dashboard.your-domain.example` (unreachable placeholder) | **Yes** — placeholder won't resolve |
| `DASHBOARD_PASSWORD` | playwright.config.ts:36-37 | undefined → basic-auth disabled | Only if dashboard uses basic-auth |
| `TEST_BLUEPRINT_ID` | spec 1 line 59 | undefined → falls back to first queue row | **Recommended** (see fragility flag 1) |
| `CLIENT_LOCATION` | both specs, header text | `'nbg1-dc1'` | Optional (header cosmetics) |
| `FALLBACK_YT_URL` | spec 1 line 155 | `'https://www.youtube.com/@YourChannel'` | Optional (only used if PUBLISHED with no visible URL) |

**Server-side (dashboard host, invoked by Flask)** — verified via
`grep -n 'GENLAB_YT_COMPLIANCE_DEMO\|YT_COMPLIANCE_ASSET\|YT_COMPLIANCE_NICHE'
dashboard/server/api/yt_quota_demo.py`:

| Var | Used at | Default | Required |
|---|---|---|---|
| `GENLAB_YT_COMPLIANCE_DEMO` | yt_quota_demo.py, `_enabled()` | unset → both routes return 404 | **Yes, `=1`** — endpoint dead otherwise |
| `YT_COMPLIANCE_ASSET` | yt_quota_demo.py, `trigger_upload()` | unset → 400 with error message | **Yes** — must be absolute path to a readable MP4 on the DASHBOARD host |
| `YT_COMPLIANCE_NICHE` | yt_quota_demo.py, `trigger_upload()` | `'ai_creators'` | Optional (default fine) |

### Fragility flag 1 — empty-queue dependency

- Verified: `grep -n 'seed\|create.*blueprint\|POST.*blueprint'
  compliance/youtube-quota/tests/compliance-01-dashboard-approve.spec.ts` → **no matches**.
  No seed-a-blueprint pre-step exists.
- If `TEST_BLUEPRINT_ID` env var is set → script goes directly to
  `/focus-review/<uuid>`. Safe.
- If not set → falls back to clicking the first `[data-testid="blueprint-row"], .blueprint-card,
  article` in the Focus Review list. Requires a **live `VISUAL_READY` blueprint** in the queue
  at recording time. If the queue is empty (which per audit history happens periodically after
  publish cycles), the test fails.
- README §Prerequisites explicitly notes: "A test blueprint in `VISUAL_READY` for Script 1
  (`TEST_BLUEPRINT_ID`). If your queue is often empty, seed one first via the pipeline."
- **Verdict: PARTIALLY HANDLED** — operator has to either supply a UUID or ensure the queue
  is non-empty; there's no auto-seed. The `ccd03d54` commit message explicitly lists
  auto-seeding under "Not in scope for this commit."

### Fragility flag 2 — silent-video acceptability

- Scaffold produces silent WebM. Verified in `_overlay.ts:2-7`:
  > `On-screen overlay helpers for silent Playwright recordings.`
  > `Rationale: recordVideo produces a silent WebM.`
  > `... step is narrated on the video without needing audio.`
- README §"What the scripts DON'T do" (line 126-128):
  > `No audio — pure Playwright recordVideo is silent. Every step is narrated via injected
  > on-screen banners so the video is readable without sound.`
- The repo contains **no documentation** on whether the reviewer accepts silent recordings,
  nor whether previous bounces cited narration/terminal-context requirements. The
  reviewer's actual emails are not in the repo (see Part 1 finding).
- **Design risk**: the current approach is betting that on-screen text overlays substitute
  adequately for voice narration + terminal capture. If the reviewer bounces the submission
  citing lack of narration, the code path is easily addressable (run the same specs while
  recording the desktop with QuickTime/OBS+narration instead of Playwright's `recordVideo`),
  but it means at least one wasted round-trip.
- **Verdict: NOT HANDLED — deferred to trial-and-see.**

### Selectors are educated guesses

Verified via `grep -n 'TODO' compliance/youtube-quota/tests/compliance-01-dashboard-approve.spec.ts`
— 5 explicit `// TODO:` markers on selectors that need adjustment against the real dashboard's
React+Vite DOM. Spec 2's selectors are all deterministic (against `#trigger-upload`,
`#response-pre`, `#video-id` in the Flask-served HTML page) — no fragility there.

---

## Part 4 — What only the operator can do

Concrete list of steps this session cannot perform. Every one requires access or credentials
not available in the sandbox.

### On the dashboard host (Hetzner VPS)

1. **Deploy the branch.** Commits `5591d546`, `ccd03d54`, and the RLS-fix work are all on
   `audit-a/fix-queue-execution`. They need to reach main and be deployed before the
   compliance endpoint or the enriched `raw_response` will fire. Command shape:
   ```
   git checkout main && git merge audit-a/fix-queue-execution
   git push
   ssh vps 'cd /opt/genlab && git pull && sudo systemctl restart genlab-dashboard'
   ```
   Only merge the compliance commits (`ccd03d54`, `5591d546`) if you want to keep the RLS-fix
   work separate — cherry-pick those two onto a fresh branch off main and PR from there.
2. **Enable the endpoint.** Add to the dashboard's env:
   ```
   ssh vps 'echo "GENLAB_YT_COMPLIANCE_DEMO=1
   YT_COMPLIANCE_ASSET=/opt/genlab/compliance-test.mp4
   YT_COMPLIANCE_NICHE=ai_creators" | sudo tee -a /opt/genlab/.env.compliance'
   ssh vps 'sudo systemctl restart genlab-dashboard'
   ```
3. **Stage the test asset.** Copy a 10-30s unlisted-safe MP4 to
   `/opt/genlab/compliance-test.mp4` (or wherever `YT_COMPLIANCE_ASSET` points):
   ```
   scp small-test.mp4 vps:/opt/genlab/compliance-test.mp4
   ```
4. **Confirm the endpoint is live** before recording:
   ```
   curl -u admin:'<pw>' https://<dashboard>/api/v1/yt-quota-demo/page | head -20
   ```
   Expect the HTML `<h1>` with "GenLab — YouTube Data API v3 videos.insert compliance demo".
   If you get 404 → `GENLAB_YT_COMPLIANCE_DEMO=1` is not set or the dashboard wasn't restarted.
5. **After recording is done, disable the endpoint:**
   ```
   ssh vps 'sudo systemctl edit genlab-dashboard'   # remove the env line
   ssh vps 'sudo systemctl restart genlab-dashboard'
   ```
   The endpoint MUST NOT stay on in prod — its whole gate depends on the flag being unset.

### On the Mac (or wherever Playwright runs)

1. **Pick a `TEST_BLUEPRINT_ID`.** Query the live dashboard or DB for a blueprint currently
   at `VISUAL_READY` status, in a niche whose YouTube channel is safe to publish to. E.g.:
   ```
   curl -s -u admin:'<pw>' https://<dashboard>/api/v1/queue \
     | jq -r '.data[] | select(.status=="VISUAL_READY") | .id' | head -1
   ```
2. **Run recording**:
   ```
   cd compliance/youtube-quota
   export DASHBOARD_URL='https://<dashboard>'
   export DASHBOARD_PASSWORD='<pw>'
   export TEST_BLUEPRINT_ID='<uuid from step 1>'
   export CLIENT_LOCATION='hetzner-nbg1-dc1'
   npx playwright test
   ```
3. **First run WILL likely fail on selectors.** Spec 1 has 5 `// TODO:` markers. When
   Playwright fails, use its trace viewer (`npx playwright show-report`) or headed-mode
   inspection to identify the real selector, edit the spec, re-run.
4. **Convert + concat**:
   ```
   for f in recordings/*/video.webm; do
     ffmpeg -y -i "$f" -c:v libx264 -crf 22 -preset fast -c:a aac "${f%.webm}.mp4"
   done
   ```

### On Google's side (post-recording)

1. **Upload the concatenated MP4 as an unlisted YouTube video** on your own channel (or
   Drive), get the shareable link.
2. **Reply to the YouTube API Services email thread** with the link + the narrative from
   `README.md` §"Reviewer-facing narrative" (adapt to your voice).
3. **Delete the test upload from the target channel** after the reviewer confirms
   satisfaction — the unlisted MP4 in step 1 is separate and can stay for reference.

---

## Verdict — Recording readiness

**Code-side: GO.**

- `_upload_video` caller safety check (§2.3): **SAFE** (1 caller total, unpacks the tuple;
  test callers don't exist / are skipped). This gates the verdict per the prompt, and it
  passes.
- Scaffold files: present, parse-clean, blueprint registered in `review_server.py`,
  npm dependencies installed, Playwright discovers both tests, TypeScript typecheck clean.
- Response-passthrough wire: `yt_quota_demo.py:285` returns `raw_response` verbatim, so the
  post-`5591d546` `youtube_response` field will reach the browser demo page.

**Recording-side: GO WITH KNOWN RISKS.** Nothing below is a code-side blocker.

- **Risk 1 (Design):** silent video may not satisfy the reviewer if past bounces cited
  narration or terminal context. Repo has zero documentation on which bounces have
  happened, so this is a bet. Mitigation if it bounces: re-record the same Playwright
  flow while capturing the desktop with QuickTime/OBS + voice narration.
- **Risk 2 (Iteration):** spec 1's 5 selector TODOs mean the first Playwright run will
  almost certainly need adjustment against the actual React DOM. Second run will likely
  work. Budget one iteration cycle.
- **Risk 3 (Empty queue):** if `TEST_BLUEPRINT_ID` isn't set AND the Focus Review queue is
  empty, spec 1 fails. Mitigation: always set `TEST_BLUEPRINT_ID` explicitly.

**Deployment gate before recording**: the compliance code is on
`audit-a/fix-queue-execution`, not `main`. Recording will not work until commits `ccd03d54`
and `5591d546` are merged to `main` and the VPS is redeployed (see Part 4 step 1).

**Missing-from-repo asset that would reduce risk**: capture the reviewer's actual email
requirements + bounce history into a doc under `compliance/youtube-quota/` (e.g.
`REVIEWER_THREAD.md`) so future sessions have ground truth. Not a blocker — a hygiene item.
