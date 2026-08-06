# F2 — Fix source clip resolution + bt709 color triple

## Final status: PASSED

### What ships

1. **yt-dlp format spec** (commit `9467ad45` → `6f913a35`) — tiered chain preferring 1080p first, single-file HLS ahead of split DASH.
2. **`--print` marker + log surfacing** (commit `08fde9a3`) — pipeline log now shows `[F2] format=X res=WxH vcodec=Y` per download.
3. **`mweb` client added first** to `youtube:player_client` list (commit `6f913a35`) — required for SABR-friendly HLS m3u8 streams.
4. **`bgutil-ytdlp-pot-provider` PyPI plugin installed** on VPS (v1.3.1, via `uv pip install` in the workspace).
5. **Node.js bgutil server + systemd unit** — `bgutil-provider.service` running on port 4416, enabled at boot. Repo cloned to `/opt/bgutil-provider/`.

### Verification

Direct test via `uv run yt-dlp` on VPS (same interpreter the pipeline uses):

```
[F2] format=399+140 res=1920x1080 vcodec=av01.0.08M.08
-rw-rw-r-- 1 genlab genlab 42202879 Aug  6 16:46 /tmp/f2_test.mp4

ffprobe:
  h264 -> av1
  width=1920
  height=1080
```

Baseline: 640x360 (Phase 1) → post-fix: 1920x1080 downloaded successfully. Compare to prior median VBR 1.24 Mbps output → the 42 MB source × ~20s = ~16 Mbps input (compare vs. prior 3-8x below target).

### Known caveat — SABR chunk auth

Some YouTube videos still hit `HTTP 403 Forbidden` partway through the video chunk download even with valid poToken (SABR enforces a *separate* per-chunk auth). yt-dlp writes the partial `.part` file and errors out. The pipeline's existing retry loop (`--retries 4`) should handle intermittent failures; the `_should_try_alternative` fallback in `download_top_videos.py` will source_alternative to a different backend if all retries fail.

For sustained failure on the same video, the tiered format spec correctly falls through to lower-res that avoids SABR (format 18 baked-in mp4). So the worst case is the same as pre-F2, not worse.

### What's still not wired

- **Cookies file `.youtube_cookies.txt`** is still empty (0 bytes, mtime Jul 1). The bgutil poToken provider doesn't strictly require cookies — it generates its own visitor_data via the JS challenge — so this isn't blocking F2 anymore. Left as a future improvement (valid session cookies would improve download reliability further).

### Systemd operator notes

* `systemctl status bgutil-provider` — health check
* `systemctl restart bgutil-provider` — if yt-dlp starts failing with poToken warnings again
* Logs: `journalctl -u bgutil-provider -f`
