# GenLab

Video-first viral content automation platform for short-form video reels.

## What it does

GenLab finds trending video content on YouTube, writes platform-native captions and hooks using LLMs, renders branded reels with FFmpeg, and publishes to Instagram, YouTube Shorts, Facebook Reels, Threads, and X/Twitter.

## Architecture

GenLab is organized as a monorepo with a shared core library and per-channel configurations.

```
genlab-core/         Shared infrastructure (pipeline, platform clients, learning loop)
channels/            Per-channel configurations (niche-specific)
dashboard/           Operations dashboard (React + Flask)
scripts/             Shared automation scripts
```

Each channel directory contains YAML configs for sources, scoring, visuals, and publishing, along with thin strategy subclasses that customize the shared pipeline for that niche.

## Quick start

```bash
# Prerequisites: Python 3.12+, PostgreSQL, FFmpeg, uv
# 1. Clone and install
uv sync

# 2. Set up environment
cp .env.example .env
# Fill in API keys: ANTHROPIC_API_KEY, YOUTUBE_API_KEY, META_ACCESS_TOKEN, etc.

# 3. Run a pipeline
uv run --package genlab-core python -m genlab_core.pipeline --niche <niche_id>

# 4. Run tests
uv run --package genlab-core pytest genlab-core/tests/ -x
```

## Configuration

Each channel has its own `config/` directory with YAML files for sources, scoring, visuals, scheduling, and publishing. Shared configs live in `genlab-core/config/`.

All credentials go in `.env` files (never committed). See `.env.example` for the full list.

## License

[MIT](LICENSE)
