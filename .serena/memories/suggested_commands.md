# Suggested Commands

## Testing
```bash
# Run tests for each package (use absolute uv path)
/Users/anarchistsid/.local/bin/uv run --package content-scraper pytest "Content Scraper/tests/" -x -q
/Users/anarchistsid/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -x -q
/Users/anarchistsid/.local/bin/uv run --package criticalrush pytest CriticalRush/tests/ -x -q
/Users/anarchistsid/.local/bin/uv run --package content-scraper pytest dashboard/tests/ -x -q
/Users/anarchistsid/.local/bin/uv run --package content-scraper pytest ClutchWire/tests/ -x -q
/Users/anarchistsid/.local/bin/uv run --package content-scraper pytest SpliceReel/tests/ -x -q
/Users/anarchistsid/.local/bin/uv run --package content-scraper pytest FrameDrift/tests/ -x -q
```

## Frontend
```bash
cd dashboard/frontend && npm run build   # Build dashboard
cd dashboard/frontend && npm run dev     # Dev server
```

## Pipeline
```bash
# Run daily pipeline (BB)
./Content\ Scraper/runbooks/daily_intel.sh

# Run pipeline for any niche
python Content\ Scraper/run_pipeline.py
python CriticalRush/run_pipeline.py
python ClutchWire/run_pipeline.py
python SpliceReel/run_pipeline.py
python FrameDrift/run_pipeline.py
```

## Utilities
```bash
# System commands (macOS/Darwin)
git, ls, grep, find, curl — standard
launchctl load/unload ~/Library/LaunchAgents/<plist>  # daemon management
```

## Dependency Management
```bash
/Users/anarchistsid/.local/bin/uv add <package>          # Add dependency
/Users/anarchistsid/.local/bin/uv sync                   # Sync lockfile
```
