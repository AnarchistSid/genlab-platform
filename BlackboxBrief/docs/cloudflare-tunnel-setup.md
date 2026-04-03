# Cloudflare Tunnel Setup for Review Server

## Current Setup

| Component | Value |
|-----------|-------|
| Tunnel name | `review-server` |
| Tunnel ID | `772bfb73-bfff-44d8-b1fa-03e32450c190` |
| Hostname | `review.aspirehub.ai` |
| Origin | `http://localhost:5151` |
| Config | `~/.cloudflared/config-review.yml` |
| Credentials | `~/.cloudflared/772bfb73-bfff-44d8-b1fa-03e32450c190.json` |
| Auth | HTTP Basic Auth (`REVIEW_AUTH_USER` / `REVIEW_AUTH_PASS` in `.env`) |

## DNS Setup (Required — One-Time)

Add a CNAME record in Cloudflare DNS for `aspirehub.ai`:

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `review` | `772bfb73-bfff-44d8-b1fa-03e32450c190.cfargotunnel.com` | Proxied (orange) |

**How:** Cloudflare Dashboard > aspirehub.ai > DNS > Add Record

## Running the Tunnel

### Manual start
```bash
cloudflared tunnel --config ~/.cloudflared/config-review.yml run review-server
```

### Auto-start (launchd)
```bash
# Install both daemons
cp runbooks/com.genlab.review-server.plist ~/Library/LaunchAgents/
cp runbooks/com.genlab.review-tunnel.plist ~/Library/LaunchAgents/

# Load them
launchctl load ~/Library/LaunchAgents/com.genlab.review-server.plist
launchctl load ~/Library/LaunchAgents/com.genlab.review-tunnel.plist
```

### Stop
```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.review-tunnel.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.review-server.plist
```

## Checking Status
```bash
# Tunnel connections
cloudflared tunnel info review-server

# Server logs
tail -f .tmp/logs/review_server_stderr.log

# Tunnel logs
tail -f .tmp/logs/review_tunnel_stderr.log
```

## Notes
- Quick tunnels (`cloudflared tunnel --url`) do NOT work reliably on this machine
- Do NOT modify the existing "trading-bot" tunnel (used by dash.astuteos.com)
- The origin cert (`~/.cloudflared/cert.pem`) is authorized for astuteos.com, not aspirehub.ai — that's why `cloudflared tunnel route dns` fails. The CNAME must be added manually in the Cloudflare dashboard.
- Optional: add Cloudflare Access (Zero Trust > Access > Applications) for SSO on top of Basic Auth
