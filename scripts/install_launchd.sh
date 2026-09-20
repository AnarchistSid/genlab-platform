#!/usr/bin/env bash
# Install the GenLab launchd agents, resolving $HOME rather than trusting the
# literal in the plist.
#
# CLEAN-03's rule: paths are variables, not literals. launchd will not expand
# $HOME in ProgramArguments, so the plists carry a concrete path -- which means
# moving the checkout silently breaks them unless something regenerates it.
# This is that something. `--check` verifies the installed copies match what
# this script would write, so drift is visible before the next reboot.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
AGENTS=(sh.genlab.matte-worker sh.genlab.home-hygiene)
CHECK=0
[[ "${1:-}" == "--check" ]] && CHECK=1
mkdir -p "$DEST"
rc=0
for a in "${AGENTS[@]}"; do
    src="$REPO/deploy/launchd/$a.plist"
    [ -f "$src" ] || { echo "  missing: $src"; rc=1; continue; }
    rendered="$(sed -e "s#/Users/anarchistsid/GenLab#$REPO#g" -e "s#/Users/anarchistsid#$HOME#g" "$src")"
    if [ "$CHECK" = 1 ]; then
        if [ -f "$DEST/$a.plist" ] && diff -q <(printf '%s\n' "$rendered") "$DEST/$a.plist" >/dev/null; then
            echo "  [ ok ] $a"
        else
            echo "  [ x  ] $a differs from the repo (or is not installed)"; rc=1
        fi
        continue
    fi
    printf '%s\n' "$rendered" > "$DEST/$a.plist"
    launchctl unload "$DEST/$a.plist" 2>/dev/null || true
    launchctl load "$DEST/$a.plist"
    echo "  installed + loaded: $a"
done
exit $rc
