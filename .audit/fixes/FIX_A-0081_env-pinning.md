# FIX_A-0081 — Env-pinning class fix (A-0005 + A-0006 + A-0007)

## Closes
Three Phase-0 findings collapse to one root cause: no dev/prod env pin.
- **A-0005** — Mac FFmpeg 8.1 vs VPS FFmpeg 6.1
- **A-0006** — Mac Python 3.14 vs VPS Python 3.12
- **A-0007** — Mac Postgres 14 vs VPS Postgres 18

Test outcomes don't transfer (A-0074's 77 collection errors likely include Python 3.14/3.12 drift). Media renders may differ. SQL that lints on Mac 14 may misbehave on 18.

## Exact change

### Change 1: Python pin (removes 3.14/3.12 drift)
- New file: `.python-version` at repo root
  ```
  3.12
  ```
- `uv run` respects `.python-version` on Mac; enforces dev-side alignment.
- If Mac doesn't have 3.12: `brew install python@3.12` OR let `uv` auto-install.

### Change 2: Postgres pin (removes Homebrew-14/prod-18 drift)
- New file: `docker/postgres/docker-compose.yml`
  ```yaml
  version: '3.8'
  services:
    postgres:
      image: postgres:18
      environment:
        POSTGRES_USER: genlab
        POSTGRES_PASSWORD: dev_only_local
        POSTGRES_DB: genlab
      ports: ["127.0.0.1:5432:5432"]
      volumes:
        - ./data:/var/lib/postgresql/data
  ```
- Update `docs/DEV_SETUP.md` (or CLAUDE.md dev section) to instruct: `docker compose -f docker/postgres/docker-compose.yml up -d` for local Postgres 18. Retire the Homebrew 14 (or just ignore it if it's convenient).

### Change 3: FFmpeg pin (removes 8.1/6.1 drift)
Two options — pick one:
- **Option A (fastest)**: `brew install ffmpeg@6` on Mac; update `PATH` to prefer it. Document in `docs/DEV_SETUP.md`.
- **Option B (better parity)**: containerize the render step. New `docker/render/Dockerfile` based on `ubuntu:24.04` (matches VPS), installs `ffmpeg` from apt (yields 6.1.1). Update `scripts/render_local.sh` to `docker run` the container.

Recommendation: Option B if effort budget allows, else Option A now + Option B as follow-up.

### Change 4: verification script
- New file: `scripts/verify_env_pin.sh`
  ```bash
  #!/usr/bin/env bash
  set -eu
  PY=$(python3 --version | awk '{print $2}')
  case "$PY" in
    3.12.*) ;;
    *) echo "Python: expected 3.12.x, got $PY" >&2; exit 1;;
  esac
  FF=$(ffmpeg -version 2>&1 | head -1 | awk '{print $3}' | cut -d. -f1-2)
  case "$FF" in
    6.1) ;;
    *) echo "FFmpeg: expected 6.1.x, got $FF" >&2; exit 1;;
  esac
  # Postgres check optional — depends on docker-compose being up
  echo "env pin OK"
  ```
- Wire it into pre-commit as an early-warn hook (non-blocking initially, block after 1 week).

## Verification gate (execution evidence)

1. `python3 --version` on Mac after fix: `Python 3.12.x`
2. `ffmpeg -version | head -1 | awk '{print $3}'`: starts with `6.1`
3. `docker compose -f docker/postgres/docker-compose.yml ps postgres`: state = `running`, `psql -h 127.0.0.1 -U genlab -tAc 'SHOW server_version'`: `18.x`
4. `bash scripts/verify_env_pin.sh`: exits 0

All 4 must pass.

## Sequencing
- Must land after: none
- Blocks: A-0074 partial (some subset of collection errors likely resolves with Python pin — measure delta after)

## Blast radius if wrong
Pins are strictly narrower than current unbounded env. Blast if wrong: dev inconvenience during the pin adoption (must install matching versions). No prod change.

## Effort
M (Option A: half-day). L if Option B containerization.

## Owner
dev
