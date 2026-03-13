#!/usr/bin/env bash
# ============================================================================
# CLEANUP_PLAN.sh — GenLab Disk Reclamation Script
# Generated: 2026-03-08
#
# ⚠️  DO NOT RUN THIS BLINDLY — review each section first.
#     Uncomment sections you want to execute.
#     Estimated savings: ~66 GB (conservative) to ~70 GB (aggressive)
# ============================================================================

set -euo pipefail

GENLAB="/Users/anarchistsid/GenLab"
CS="$GENLAB/Content Scraper"
CR="$GENLAB/CriticalRush"
GC="$GENLAB/genlab-core"

dry_run() {
    echo "[DRY-RUN] Would delete: $1 ($(du -sh "$1" 2>/dev/null | cut -f1))"
}

echo "============================================"
echo "  GenLab Disk Cleanup — $(date '+%Y-%m-%d %H:%M')"
echo "============================================"
echo ""

# --------------------------------------------------------------------------
# TIER 1 — SAFE: Ephemeral run artifacts & media (est. ~61 GB)
# These are re-downloadable or re-renderable. No data loss.
# --------------------------------------------------------------------------

echo "=== TIER 1: Ephemeral artifacts (SAFE) ==="

# 1a. Content Scraper .tmp/runs/ — keep latest 3 runs, nuke the rest
#     Biggest offender: 20260308_084935_54382 alone is 38 GB
echo "[1a] Content Scraper: prune old runs (keep latest 3 by date)..."
# cd "$CS/.tmp/runs"
# ls -d 20*/ 2>/dev/null | sort -r | tail -n +4 | xargs -I{} rm -rf "{}"
# rm -f full_rerender_*.log landscape_*.log  # orphan log files in runs/

# 1b. Content Scraper .tmp/media/videos/ — 21 GB of downloaded clips
#     All re-downloadable via yt-dlp
echo "[1b] Content Scraper: purge media/videos/ (21 GB, re-downloadable)..."
# rm -rf "$CS/.tmp/media/videos/"

# 1c. Content Scraper .tmp/audio/ — 102 MB of generated audio
echo "[1c] Content Scraper: purge audio/ (102 MB, re-generatable)..."
# rm -rf "$CS/.tmp/audio/"

# 1d. CriticalRush .tmp/ artifacts — clips, rendered, captions
#     All re-renderable from source material
echo "[1d] CriticalRush: purge clips/ (3.7 GB), rendered/ (751 MB), captions/ (250 MB)..."
# rm -rf "$CR/.tmp/clips/"
# rm -rf "$CR/.tmp/rendered/"
# rm -rf "$CR/.tmp/captions/"

# 1e. genlab-core test_video_dl — 354 MB of test downloads
echo "[1e] genlab-core: purge test_video_dl/ (354 MB)..."
# rm -rf "$GC/.tmp/test_video_dl/"

echo ""

# --------------------------------------------------------------------------
# TIER 2 — SAFE: Logs & caches (est. ~80 MB)
# --------------------------------------------------------------------------

echo "=== TIER 2: Logs & caches (SAFE) ==="

# 2a. Content Scraper logs older than 7 days
echo "[2a] Content Scraper: prune logs older than 7 days..."
# find "$CS/.tmp/logs/" -name "*.log" -mtime +7 -delete

# 2b. CriticalRush phase logs (old test runs)
echo "[2b] CriticalRush: remove phase logs (8.8 MB)..."
# rm -f "$CR/.tmp/live_run_phase_"*.log
# rm -f "$CR/.tmp/phase5_e2e_run.log"

# 2c. Playwright MCP console logs
echo "[2c] Content Scraper: purge .playwright-mcp/ logs (1.2 MB)..."
# rm -rf "$CS/.playwright-mcp/"

# 2d. firebase-debug.log files
echo "[2d] Firebase debug logs (552 KB)..."
# rm -f "$GENLAB/firebase-debug.log"
# rm -f "$CS/firebase-debug.log"

# 2e. __pycache__ directories (2.8 MB, 41 dirs)
echo "[2e] Purge all __pycache__ (2.8 MB)..."
# find "$GENLAB" -name "__pycache__" -type d -not -path "*/node_modules/*" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null

# 2f. .pytest_cache directories
echo "[2f] Purge .pytest_cache (164 KB)..."
# find "$GENLAB" -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null

# 2g. Content Scraper .tmp/cache/ (4.2 MB, will be rebuilt on next run)
echo "[2g] Content Scraper: purge HTTP cache (4.2 MB)..."
# rm -rf "$CS/.tmp/cache/"

# 2h. Content Scraper .tmp/svm_videos/ (empty) & .tmp/backups/ (15 MB)
echo "[2h] Content Scraper: purge svm_videos/ (0 B) + backups/ (15 MB)..."
# rm -rf "$CS/.tmp/svm_videos/"
# rm -rf "$CS/.tmp/backups/"

echo ""

# --------------------------------------------------------------------------
# TIER 3 — CAUTION: Docker images (est. ~4.2 GB)
# Verify nothing depends on these before removing.
# --------------------------------------------------------------------------

echo "=== TIER 3: Docker cleanup (CAUTION) ==="

# 3a. short-video-maker image — 4.13 GB, not attached to active container
echo "[3a] Docker: remove gyoridavid/short-video-maker (4.13 GB, inactive)..."
# docker rmi gyoridavid/short-video-maker:latest-tiny

# 3b. Unused Docker volumes — 48 MB
echo "[3b] Docker: prune unused volumes (48 MB)..."
# docker volume prune -f

# 3c. Docker build cache — 0 B currently, but prune anyway
echo "[3c] Docker: prune build cache..."
# docker builder prune -f

# NOTE: redis:alpine (133 MB) is ACTIVE — do NOT remove

echo ""

# --------------------------------------------------------------------------
# TIER 4 — CAUTION: Old venv & package caches (est. ~4.8 GB)
# uv manages deps now. The root .venv may be vestigial.
# --------------------------------------------------------------------------

echo "=== TIER 4: Old venv & caches (CAUTION) ==="

# 4a. Root .venv — 1.6 GB (contains torch 382 MB but not importable on Py 3.14)
#     uv workspace uses its own resolution. Verify nothing references this venv.
echo "[4a] Root .venv (1.6 GB) — verify nothing uses it before removing..."
# Verify: which python should NOT point to $GENLAB/.venv/bin/python
# rm -rf "$GENLAB/.venv"

# 4b. pip cache — 504 MB (uv has its own cache)
echo "[4b] pip cache (504 MB)..."
# rm -rf ~/Library/Caches/pip/

# 4c. uv cache prune — 2.2 GB (only removes unreferenced entries)
echo "[4c] uv cache prune (safe subset of 2.2 GB)..."
# ~/.local/bin/uv cache prune

# 4d. Homebrew cache — 1.1 GB
echo "[4d] Homebrew cache (1.1 GB)..."
# brew cleanup --prune=all

echo ""

# --------------------------------------------------------------------------
# DO NOT DELETE — these are required
# --------------------------------------------------------------------------

echo "=== DO NOT DELETE ==="
echo "  .git directories (48.7 MB total) — source control"
echo "  node_modules (254 MB) — dashboard dev dependencies"
echo "  dashboard/dist (1.6 MB) — build output"
echo "  HuggingFace cache (141 MB) — faster-whisper model"
echo "  Content Scraper .tmp/health/ — runtime health state"
echo "  Content Scraper .tmp/learning_logs/ — self-learning data"
echo "  Content Scraper .tmp/notification_prefs.json — user prefs"
echo "  Content Scraper .tmp/publisher.lock — daemon lock file"
echo "  redis:alpine Docker image (133 MB) — active container"
echo ""

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo "============================================"
echo "  Estimated savings by tier:"
echo "    TIER 1 (SAFE):      ~61 GB"
echo "    TIER 2 (SAFE):      ~100 MB"
echo "    TIER 3 (CAUTION):   ~4.2 GB"
echo "    TIER 4 (CAUTION):   ~4.8 GB"
echo "    TOTAL:              ~70 GB"
echo "============================================"
echo ""
echo "To execute: uncomment the sections you want,"
echo "then run:   bash CLEANUP_PLAN.sh"
