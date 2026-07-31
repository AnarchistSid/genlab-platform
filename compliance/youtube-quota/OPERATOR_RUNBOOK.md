# Operator runbook — YouTube quota compliance recording

Every command below runs on your Mac unless the prompt is prefixed with `vps$`
(indicating an SSH session on the Hetzner VPS). Nothing in this runbook has been
executed for you — Claude produced the artifacts, you run the ops.

**Do NOT merge `audit-a/fix-queue-execution` to `main`.** That branch carries the
APPLY-BLOCKED RLS migration and the rollout-default change, neither of which are
cleared for prod. Cherry-pick ONLY the two compliance commits onto a fresh branch off
`main`.

---

## Part 4 — Clean deploy (cherry-pick, PR, deploy)

**Verification already done (this session):**

- `git log main..5591d546^ -- genlab-core/src/genlab_core/platforms/youtube.py
  dashboard/server/review_server.py dashboard/server/api/yt_quota_demo.py compliance/`
  returned zero intermediate commits. The two compliance commits are self-contained
  and can be cherry-picked without dragging RLS work along.
- `ccd03d54` touches: `compliance/youtube-quota/*` (all new) +
  `dashboard/server/api/yt_quota_demo.py` (new) +
  `dashboard/server/review_server.py` (2 lines: import + `register_blueprint`).
- `5591d546` touches: `genlab-core/src/genlab_core/platforms/youtube.py` (+22/−6).
- `_upload_video` caller safety check (`.audit/YT_QUOTA_COMPLIANCE_STATUS.md` §2.3):
  SAFE. Single prod caller unpacks the new tuple shape; the one test caller is a
  `@pytest.mark.skip`-ed method that never runs.

### Cherry-pick commands

```bash
# On your Mac, at repo root:
git fetch origin
git checkout main
git pull --ff-only

git checkout -b compliance/yt-quota-recording

# Cherry-pick in commit order (ccd03d54 scaffolds the compliance module,
# 5591d546 enriches PublishResult.raw_response used by it):
git cherry-pick ccd03d54
git cherry-pick 5591d546

# (Optional but recommended) add the CLI script left staged by the
# planning session:
git add compliance/youtube-quota/run_compliance_upload.py \
        compliance/youtube-quota/SUBMISSION_PLAN.md \
        compliance/youtube-quota/REVIEWER_REPLY.md \
        compliance/youtube-quota/OPERATOR_RUNBOOK.md
git commit -m "docs(compliance): submission plan, reviewer reply, run-book, CLI"

git push -u origin compliance/yt-quota-recording
gh pr create --base main --title "compliance(youtube): quota-review recording bundle" \
             --body "Cherry-picks compliance scaffold (ccd03d54) + raw_response enrichment (5591d546) from audit-a/fix-queue-execution, plus the planning docs and CLI script for the recording session. No RLS commits included."
```

**Merge the PR** (squash or merge commit — either works; two commits are already
small).

### Deploy on the VPS

```bash
# SSH to the VPS
ssh <your-vps-alias>

vps$ cd /opt/genlab
vps$ sudo -u genlab git fetch origin
vps$ sudo -u genlab git checkout main
vps$ sudo -u genlab git pull --ff-only

# Restart the dashboard so review_server.py picks up the new blueprint.
# (Only needed if you plan to also record the browser demo page as context;
# the CLI script does NOT need the dashboard restarted.)
vps$ sudo systemctl restart genlab-dashboard

# Sanity: is the endpoint reachable? (Only if GENLAB_YT_COMPLIANCE_DEMO=1 is set;
# see next step. If you don't want the browser demo, skip this whole check.)
vps$ curl -sI -u admin:'<dashboard-pw>' \
       https://<dashboard-domain>/api/v1/yt-quota-demo/page | head -3
# Expect HTTP/2 200 if the flag is set; HTTP/2 404 if not.
```

### Optional: enable the browser demo page (only if you want shot #7 to use it)

```bash
vps$ sudo systemctl edit genlab-dashboard
# Add under [Service]:
#   Environment="GENLAB_YT_COMPLIANCE_DEMO=1"
#   Environment="YT_COMPLIANCE_ASSET=/opt/genlab/compliance-test.mp4"
#   Environment="YT_COMPLIANCE_NICHE=ai_creators"
vps$ sudo systemctl restart genlab-dashboard
```

**MUST be turned off after recording:** revert the drop-in edit, then
`sudo systemctl restart genlab-dashboard`. The demo endpoint's gate is the flag.

---

## Part 5 — Recording checklist (one ordered pass)

Do these in order. Total operator time: ~45 minutes including one retake budget.

### Pre-flight (do BEFORE hitting record)

1. **Cherry-pick + deploy** (Part 4 above). Confirm the VPS is on `main` HEAD equal to
   the merged PR's merge commit: `vps$ cd /opt/genlab && git log --oneline -1`.

2. **Stage the compliance test video on the VPS.** Pick a real 10–30 sec MP4
   (1080×1920 vertical is ideal — matches production reels; landscape works too).

   ```bash
   # From your Mac:
   scp path/to/test-clip.mp4 <vps-alias>:/tmp/compliance-test.mp4
   ssh <vps-alias> "sudo mv /tmp/compliance-test.mp4 /opt/genlab/compliance-test.mp4 \
                    && sudo chown genlab:genlab /opt/genlab/compliance-test.mp4 \
                    && sudo chmod 644 /opt/genlab/compliance-test.mp4 \
                    && ls -la /opt/genlab/compliance-test.mp4"
   ```

3. **Pick the niche.** Recommended: `ai_creators` (Blackbox Brief) — the CLAUDE.md
   auto-approver is already enrolled there, so the channel is warm for compliance
   activity. Any niche is fine as long as its per-niche OAuth bundle is loaded on the
   VPS.

4. **Dry-run with `--check`** — this validates credentials + auth WITHOUT uploading
   (costs 1 quota unit for a `channels.list?mine=true` call). Turns "hope the take
   works" into "prove it works, then record":

   ```bash
   vps$ cd /opt/genlab
   vps$ source .venv/bin/activate  # or however your env activates
   vps$ export YT_COMPLIANCE_ASSET=/opt/genlab/compliance-test.mp4
   vps$ export YT_COMPLIANCE_NICHE=ai_creators   # or another niche
   vps$ ./compliance/youtube-quota/record_demo.sh --check
   ```

   Expect the script to print sections 1 + 2 (hostname/ipinfo/asset) followed by:

   ```
   [check] asset OK — ...
   [check] OAuth token exchange OK
   [check] channels.list OK — token resolves to channel UC...
   [check] PASS — safe to run without --check to perform the upload
   ```

   If it fails, **read the error and fix before recording.** Common causes:
   - Missing per-niche OAuth env (`BLACKBOXBRIEF_YOUTUBE_REFRESH_TOKEN` etc.) →
     `[check] FAIL — YouTubeClient instantiation raised …`
   - Expired/revoked refresh token → `[check] FAIL — OAuth token exchange raised …`
   - Token bound to wrong channel → `[check] FAIL — channels.list?mine=true returned
     no items` (or the printed channel ID differs from the niche's expected one)
   - `genlab_core` not on `PYTHONPATH` → readable message pointing at the venv
   - Quota hard-stop reached — `--check` uses 1 unit; if that fails to record, the
     account is truly at ceiling (wait for Pacific-midnight reset OR pick a different
     niche whose quota is fresh)

5. **Fill the placeholders in `REVIEWER_REPLY.md`** — do this while the pre-flight
   details are on screen. Most numeric placeholders were auto-filled from repo
   evidence (see `FINALIZATION_NOTES.md`); the remaining ~7 are external (GCP
   project number, reviewer's name, your identity, requested-quota amount, unlisted
   upload URL after recording).

### Recording (the take) — five live actions total

6. **Recording tool.** QuickTime Player → File → New Screen Recording (Mac) OR OBS
   Studio → Display Capture. Full desktop, so the SSH terminal window and the browser
   window are both visible. **Do NOT use Playwright `recordVideo`** — that only
   captures a headless browser and misses the terminal.

7. **Set up windows before hitting record:**
   - Left half: SSH terminal already logged into the VPS, at `/opt/genlab`, with the
     virtualenv activated AND the two env vars exported (`YT_COMPLIANCE_ASSET`,
     `YT_COMPLIANCE_NICHE`) — same ones you used for `--check`. Font ~16pt.
   - Right half: Browser with a fresh empty tab open (no URL yet).

8. **The take.** Your live actions are:
   1. **Start OBS/QuickTime recording.**
   2. **Type one command:** `./compliance/youtube-quota/record_demo.sh` and hit Enter.
      Narrate over each `════ N. TITLE ════` section header as it appears (see
      `SUBMISSION_PLAN.md` shot 2 for the narration cues). Total script runtime
      ~1:30–1:45.
   3. **When the §4 END RESULT banner appears**, the script prints a boxed
      `https://youtube.com/shorts/<id>` URL. **Switch to the browser tab, paste that
      URL, hit Enter.** Wait 3–5 seconds for the video to play so the reviewer sees
      it play; confirm the URL bar's `<id>` matches the banner and the "Unlisted"
      badge is visible.
   4. **Stop OBS/QuickTime.**
   5. Trim head/tail if there's dead space. Do NOT re-cut for length — the flow is
      more important than polish.

   That's the complete set of live actions during the take: (a) run one command,
   (b) paste one URL, (c) start/stop the recorder. Everything else is narration
   over paced script output.

### Post-record

9. **Upload the screencast as an unlisted YouTube video** on your personal account
   (or the operator account of your choice — NOT one of the GenLab channels, so the
   channel handles in the reviewer's video match production content, not compliance
   meta-content). Copy the unlisted link.

   Alternative: attach the MP4 directly to the email. Gmail's limit is 25 MB;
   QuickTime output for 2 minutes at 1080p is typically ~30–80 MB. If the file is
   over 25 MB, use the unlisted upload path.

10. **Send the reply** — paste `REVIEWER_REPLY.md`'s body into the existing thread,
    fill in the unlisted URL (or state the file is attached), verify all placeholders
    are resolved, hit send. Do this within the **7-business-day window** the reviewer
    named in their third-and-final notice.

11. **Delete the compliance-test video from the target YouTube channel** after the
    reviewer confirms. The unlisted screencast upload (step 9) can stay.

12. **If you enabled the browser demo endpoint** (Part 4 optional step): revert the
    `systemctl edit` drop-in and restart the dashboard. The gate is the env flag —
    leaving it on is a compliance risk.

---

## Things only the operator can do (not this session)

- SSH into the production VPS
- Merge the compliance PR to `main`
- Deploy to prod (`git pull` on VPS, `systemctl restart`)
- Trigger the real YouTube upload (needs prod OAuth credentials)
- Screen-record on your desktop (needs QuickTime/OBS + your speakers/mic)
- Upload the screencast to YouTube (needs your Google account)
- Send the reply email (needs access to the reviewer thread)
- Delete the compliance-test video from the channel after clearance

---

## Status when this session ends

- **Ready:** cherry-pick set verified clean; recording plan, shot-list, email draft,
  and CLI script are all on disk under `compliance/youtube-quota/`.
- **Next operator action:** run the cherry-pick + push + `gh pr create` block from
  Part 4 above.
- **Not done:** anything the "operator can do" list above.
