# Dashboard Issues — 2026-03-17 (Updated from browser audit)

## VERIFIED ISSUES (with screenshots):

### 1. Pipeline Monitor — No run history, empty stages
- **Page**: /pipeline
- **Issue**: All 5 niches show "Idle" with empty stage circles. No last_run data.
- **Root cause**: Pipeline status API returns `last_run: null` — not reading from .tmp/runs/
- **Impact**: Users can't see when pipelines last ran or what they produced
- **Fix needed**: Backend `/api/v1/pipeline/status` needs to scan .tmp/runs/ for recent reports

### 2. Publishing Queue — No thumbnails (404 on video API)
- **Page**: /queue
- **Issue**: All items show gray placeholder instead of video thumbnails
- **Console errors**: `Failed to load resource: 404 /api/video/1791`, `/api/video/1785`
- **Root cause**: Video thumbnail endpoint can't find rendered MP4 files
- **Impact**: Queue is functional (approve/reject works) but hard to review without previews
- **Fix needed**: `/api/video/<id>` endpoint needs to resolve rendered_path from blueprint

### 3. Schedule — Some video thumbnails 404
- **Page**: /schedule
- **Issue**: Most slots show content correctly with thumbnails. Some show 404 errors.
- **Console errors**: Failed to load MP4 resources (specific hash-named files)
- **Root cause**: Rendered videos may have been cleaned up or paths changed
- **Note**: Schedule layout itself looks correct — all 5 niches × 7 days showing

### 4. Focus Review — Shows only 1 item (not duplicates)
- **Page**: /focus-review
- **Issue**: Shows "1 / 1" — only 1 pending review item (the PENDING one from queue)
- **Status**: This is CORRECT behavior — only PENDING items need review
- **Note**: User may have seen duplicates in a previous session; current state is fine

### 5. Analytics Chart — Lines for CW/SR/FD
- **Page**: /analytics (All Niches view)
- **Issue**: Chart shows all 5 niche lines but CW/SR/FD are very small compared to BB/gaming
- **Root cause**: BB has 561K reach viral post, CW/SR/FD have 100-400 reach
- **Code fix**: ts_buckets now includes sports_reach/movies_reach/anime_reach keys ✓
- **Note**: Scale difference is real data, not a bug. Per-niche views show correct data.

### 6. Monetisation — Empty ("No monetisation data yet")
- **Page**: /monetisation
- **Issue**: Shows "No monetisation data yet. The tracker runs daily at 13:30 IST."
- **Root cause**: The monetisation tracker plist needs to run and populate data
- **Fix needed**: Check com.genlab.monetisation-tracker plist status and data source

### 7. FrameDrift accent color
- **Page**: All pages
- **Issue**: FrameDrift shows purple (#7B3FE4) in sidebar but user says chart line is wrong
- **Status**: Code has correct color. May be a CSS variable resolution issue for chart.

## WORKING CORRECTLY:
- Mission Control: All 5 niches showing, pipeline status, publishing timeline ✓
- Publishing Queue: 1 pending + 8 approved items showing, approve/reject buttons ✓
- Focus Review: 1 pending item with video preview, approve/reject/revise/skip ✓
- Schedule: 7-day calendar view with all 5 niches, drag-and-drop slots ✓
- Sidebar: All 10 nav items, niche switcher, keyboard shortcuts ✓
- Niche colors: BB cyan, CR orange, CW red, SR gold, FD purple — all correct ✓

## PRIORITY ORDER:
1. Pipeline Monitor (P1 — users need to see run status)
2. Queue thumbnails (P2 — functional but hard to review)
3. Monetisation page (P2 — empty, needs data source)
4. Schedule thumbnail 404s (P3 — minor, most work)
