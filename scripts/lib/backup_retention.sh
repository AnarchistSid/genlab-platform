#!/usr/bin/env bash
# Shared backup retention: keep the last 7 DAILY plus 4 WEEKLY, delete the rest.
#
# WHY NOT A ROLLING WINDOW
# ------------------------
# Both backup scripts used "older than 14 days", independently. That is a policy
# about AGE, not about how many copies you hold, so its disk cost is whatever
# the daily volume happens to be: at ~270 MB/day of visuals it held 15 dirs and
# 4.0 GB, and nothing objected until the disk reached 97%.
#
# Keep-N-daily-plus-M-weekly bounds the COUNT, so the cost is bounded too, and
# it reaches FURTHER BACK for the same space -- four weekly points cover a month
# where 14 rolling days cover a fortnight.
#
# HARDLINKS: the visuals backups are hardlinked between dates (link counts of
# 10-13, measured on prod). Deleting a date frees only the blocks whose LAST
# link goes away, so `du` on the doomed set overstates the gain -- measured
# 1.5 GB by du against 0.4 GB by df. Report df when claiming freed space.
#
# Usage:  retention_drop_list <dir> <glob>   -> paths that should be deleted
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"

retention_drop_list() {
    local root="$1" pattern="${2:-*}"
    python3 - "$root" "$pattern" "$KEEP_DAILY" "$KEEP_WEEKLY" <<'PY'
import datetime, glob, os, re, sys
root, pattern, keep_daily, keep_weekly = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])

def date_of(name):
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", os.path.basename(name))
    if not m:
        return None
    try:
        return datetime.date(*map(int, m.groups()))
    except ValueError:
        return None

entries = [(date_of(p), p) for p in glob.glob(os.path.join(root, pattern))]
entries = sorted([(d, p) for d, p in entries if d], key=lambda e: e[0], reverse=True)

keep = {p for _, p in entries[:keep_daily]}
weeks = {}
for d, p in entries:                            # newest-first, so the first hit
    weeks.setdefault(d.isocalendar()[:2], p)    # in each ISO week is the newest
for p in list(weeks.values())[:keep_weekly]:
    keep.add(p)

for _, p in entries:
    if p not in keep:
        print(p)
PY
}
