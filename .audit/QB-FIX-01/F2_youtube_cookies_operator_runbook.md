# F2 blocker — YouTube cookies file is empty (0 bytes since 2026-07-01)

**Diagnostic:**
```
ssh genlab-prod ls -la /opt/genlab/.youtube_cookies.txt
→ -rw------- 1 genlab genlab 0 Jul  1 13:31 /opt/genlab/.youtube_cookies.txt
```

The file exists (yt-dlp finds it and tries to load it) but is empty. yt-dlp reports "does not look like a Netscape format cookies file" because an empty file has no header. Without cookies, YouTube's SABR-only streaming experiment gates every high-res format — the only format yt-dlp can see for our sample URL (`youtube.com/watch?v=p6W3EWqpvZ4`) is:

```
ID  EXT  RESOLUTION FPS  FILESIZE  TBR  VCODEC       ACODEC     ASR MORE INFO
18  mp4  288x360    24   839.15KiB 416k avc1.42001E  mp4a.40.2  44k 240p
```

That is a **240p legacy progressive stream**. Every source clip downloaded via yt-dlp for the last 5 weeks has been this 240p stream, upscaled to 1080×1920 by the compositor. F-QB-0101 (0.9-2.4 Mbps output bitrate) is a direct downstream consequence.

## Operator action (est. 15 min)

1. Open Chrome or Firefox on a machine where you can sign in to YouTube.
2. Install a cookies-export extension:
   - Chrome: **"Get cookies.txt LOCALLY"** (name-drops "LOCALLY" — the one without a server call). ID: `cclelndahbckbenkjhflpdbgdldlbecc` in Chrome Web Store.
   - Firefox: **"cookies.txt"** by Lennon Hill. Namespace `cookies.txt`.
3. Visit `https://www.youtube.com/` and confirm you are signed in.
4. Click the extension icon, select **"Export"** or **"Save cookies.txt"** — export **cookies for `.youtube.com`** (or "current site").
5. The file starts with `# Netscape HTTP Cookie File` and has tab-separated rows.
6. Upload to VPS:
   ```
   scp <local-cookies.txt> genlab-prod:/tmp/yt-cookies.txt
   ssh genlab-prod 'sudo -u genlab cp /tmp/yt-cookies.txt /opt/genlab/.youtube_cookies.txt && chmod 600 /opt/genlab/.youtube_cookies.txt && rm /tmp/yt-cookies.txt'
   ```
7. Verify the file loaded correctly:
   ```
   ssh genlab-prod 'head -1 /opt/genlab/.youtube_cookies.txt'
   → expect: # Netscape HTTP Cookie File
   ```
8. Ping me — I'll re-run F2 gate.

## Cookie hygiene

- Cookies expire. Chrome extensions typically export `__Secure-3PSID`, `__Secure-3PAPISID`, `LOGIN_INFO`, `SID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `PREF`, `VISITOR_INFO1_LIVE`. All are needed for SABR bypass.
- Store the source browser sign-in on a **secondary Google account** dedicated to GenLab — do not use a personal account. Cookies grant full YouTube session control.
- If the account has 2FA enabled, cookies stay valid ~30-60 days before requiring re-export.

## Verification I'll run after upload

```bash
# 1. File is non-empty and Netscape-formatted
head -1 /opt/genlab/.youtube_cookies.txt | grep -c "^# Netscape HTTP Cookie File"  # expect 1

# 2. yt-dlp can see higher-res formats
yt-dlp --list-formats --no-warnings \
  --cookies /opt/genlab/.youtube_cookies.txt \
  --extractor-args "youtube:player_client=ios,tv,web_safari,android,web" \
  --proxy socks5://127.0.0.1:40000 \
  https://www.youtube.com/watch?v=p6W3EWqpvZ4 2>&1 | grep -E "^\s*[0-9]+.*mp4.*(1080|720|480)"
# expect: at least one 720p or 1080p mp4 format listed

# 3. Trigger a movies pipeline run and check clip resolution
sudo systemctl start genlab-pipeline-movies.service
# wait, then:
ffprobe -v error -select_streams v:0 -show_entries stream=width,height /opt/genlab/.tmp/runs/movies_$(date +%Y%m%d)*/clips/*.mp4
# expect: short side >= 1080
```

That's F2's primary gate.
