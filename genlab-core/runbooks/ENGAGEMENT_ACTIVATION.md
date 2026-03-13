# Engagement Poller Activation Runbook

How to set up and verify the engagement poller LaunchAgent.

## 1. Required Environment Variables

Set these in your shell profile (`~/.zshrc` or `~/.bashrc`) or in a `.env` file loaded by the poller wrapper.

### Required (poller will not function without these)

| Platform | Env Var | Notes |
|----------|---------|-------|
| YouTube | `YOUTUBE_CLIENT_ID` | OAuth 2.0 client ID |
| YouTube | `YOUTUBE_CLIENT_SECRET` | OAuth 2.0 client secret |
| YouTube | `YOUTUBE_REFRESH_TOKEN` | Long-lived refresh token (auto-refreshes access tokens) |
| Instagram/Meta | `META_ACCESS_TOKEN` | Never-expiring EAA Page Token. Must use `graph.facebook.com`, NOT `graph.instagram.com` |
| Facebook | `FB_PAGE_ACCESS_TOKEN` | Page access token for post insights |
| X/Twitter | `X_BEARER_TOKEN` | App-only bearer token (read access) |
| X/Twitter | `X_API_KEY` | OAuth 1.0a consumer key (publishing) |
| X/Twitter | `X_API_SECRET` | OAuth 1.0a consumer secret |
| X/Twitter | `X_ACCESS_TOKEN` | OAuth 1.0a user access token |
| X/Twitter | `X_ACCESS_SECRET` | OAuth 1.0a user access secret |

### Optional (warnings only)

| Platform | Env Var | Notes |
|----------|---------|-------|
| TikTok | `TIKTOK_ACCESS_TOKEN` | Disabled until `TIKTOK_AUDIT_APPROVED=true` |
| Threads | `THREADS_ACCESS_TOKEN` | 60-day token, auto-refreshed by `token_health.py` |

## 2. Run Credential Check First

Before installing the LaunchAgent, verify all credentials are present:

```bash
cd /Users/anarchistsid/GenLab
~/.local/bin/uv run --package genlab-core python -m genlab_core.tools.credential_check
```

Exit code 0 means all required platforms are ready. Exit code 1 means at least one required platform is missing credentials. Fix any MISSING entries before proceeding.

## 3. Install the LaunchAgent

Copy the plist and load it:

```bash
cp /Users/anarchistsid/GenLab/genlab-core/runbooks/com.genlab.engagement-poller.plist \
   ~/Library/LaunchAgents/com.genlab.engagement-poller.plist

launchctl load ~/Library/LaunchAgents/com.genlab.engagement-poller.plist
```

The plist is configured with `KeepAlive: true`, so macOS will restart the poller if it crashes. `RunAtLoad` is false, so it starts on the next login or when manually kicked:

```bash
launchctl start com.genlab.engagement-poller
```

## 4. Verify the Poller Is Running

```bash
launchctl list | grep genlab
```

You should see a line like:

```
PID   Status   com.genlab.engagement-poller
```

A numeric PID in the first column means the process is running. A `-` means it is loaded but not currently running. Check logs for errors:

```bash
tail -50 /Users/anarchistsid/GenLab/.tmp/logs/engagement_poller.log
tail -50 /Users/anarchistsid/GenLab/.tmp/logs/engagement_poller_err.log
```

## 5. Troubleshooting

### Poller exits immediately (Status = non-zero, PID = -)

1. Check stderr log: `tail -100 .tmp/logs/engagement_poller_err.log`
2. Common cause: missing env vars. Run credential check (step 2).
3. Common cause: uv not found. Ensure `PATH` in the plist includes `~/.local/bin`.

### Poller runs but no metrics collected

1. Verify there are pending feedback tasks in SharePoint (`GenLab_PendingFeedback` list).
2. Check that published posts are old enough for the next collection window (6h minimum).
3. Check stdout log for `[metric_collector] No pending tasks`.

### Token expired / API 401 errors

1. Run token health check: `uv run --package genlab-core python -m execution.check_token_health`
2. YouTube: refresh token may be revoked. Re-run OAuth Playground flow.
3. Meta: EAA page tokens are permanent and should not expire.
4. Threads: 60-day tokens auto-refresh when >50 days old via `token_health.py`.
5. X/Twitter: 403 on `/users/me` is expected (paid plan required). Other 401s mean tokens need regeneration.

### Unloading the LaunchAgent

```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.engagement-poller.plist
```
