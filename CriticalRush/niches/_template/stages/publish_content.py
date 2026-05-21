"""Niche-specific publishing stage.

Publishing clients are shared infrastructure -- import from genlab-core.
This stage handles WHAT gets posted (captions, hooks, hashtags,
video selection). The clients handle HOW it gets posted (HTTP calls,
token management, retry logic).

You should NOT:
  - Create new platform API apps for this niche
  - Store platform tokens in this niche's .env
  - Copy tiktok_client.py or threads_client.py into this niche

You SHOULD:
  - Import clients from genlab_core.publishing
  - Override the caption/hook/hashtag formatting for your niche's voice
  - Use the same CLOUDFLARE_TUNNEL_URL and platform tokens as all other agents
"""
