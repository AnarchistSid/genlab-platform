#!/usr/bin/env bash
# Weekly: is the home directory still the shape we left it, and did the test
# harness clean up after itself?
#
# CLEAN-02 §4 / CLEAN-03 §7. Two findings this exists for:
#   - eight .bc-* directories in $HOME from harness runs whose cleanup() did not
#     clean. A harness that creates worktrees owns their removal.
#   - loose files and unexplained directories at the home root, which is how
#     94 screenshots reached a repo root and 36 secret copies reached a prod one.
#
# Exit 0 ALWAYS (rule #26): findings are data the operator reads, not an
# incident that should page. The report says what it found; a non-zero exit
# would turn a tidy-up note into a CRITICAL.
set -uo pipefail

GENLAB="${GENLAB_ROOT:-$HOME/GenLab}"
FINDINGS=()

# 1. worktrees — exactly one, the checkout itself
wt="$(git -C "$GENLAB" worktree list 2>/dev/null | wc -l | tr -d ' ')"
[ "${wt:-1}" = "1" ] || FINDINGS+=("$wt git worktrees for GenLab (expected 1): $(git -C "$GENLAB" worktree list | tr '\n' ';')")

# `ls` exits non-zero with no matches; pipefail would end the script silently.
stray_bc="$( { ls -Ad "$HOME"/.bc-* 2>/dev/null || true; } | wc -l | tr -d ' ')"
[ "$stray_bc" = "0" ] || FINDINGS+=("$stray_bc .bc-* director(y|ies) in \$HOME — the harness builds under the repo, never here")

# 2. loose files at the home root
# `ls -p` does not mark a SYMLINK to a directory with a slash, so a cloud-sync
# alias reads as a loose file. Test the link target, not the listing.
loose_list=""
for f in "$HOME"/*; do
    [ -f "$f" ] || continue
    b="$(basename "$f")"
    allowed "$b" && continue
    loose_list="$loose_list$b "
done
loose="$(echo "$loose_list" | wc -w | tr -d ' ')"
[ "$loose" = "0" ] || FINDINGS+=("$loose loose file(s) at the home root: $loose_list")

# 3. directories at the home root that are neither a tool dot-dir, a macOS
#    standard folder, nor a project. Anything else is a finding.
# macOS standard folders, plus CLOUD SYNC ROOTS, which are tool-owned state:
# the provider creates and names them and they must not be moved or tidied.
STANDARD="Applications Desktop Documents Downloads Library Movies Music Pictures Public Projects Sites"
# Newline-delimited, matched exactly: these names contain spaces, so a
# space-delimited " $b " test splits them, and `|` inside an expanded variable
# is not alternation in a `case` pattern. Both were tried; both matched nothing.
ALLOW_EXACT=$'Google Drive\nOneDrive - VeritasOne LLP\nDropbox\niCloud Drive\nCalibre Library\nE Book'
allowed() { printf '%s\n' "$ALLOW_EXACT" | grep -Fxq -- "$1"; }
KNOWN_PROJECTS="GenLab genlab-worker inference-apps scratch-archive aspirehub ai_trading_agent ai_trading_bot x_mw_ocr aa_ocr replay_data"
unexpected=()
for d in "$HOME"/*/; do
    b="$(basename "$d")"
    case " $STANDARD $KNOWN_PROJECTS " in *" $b "*) continue ;; esac
    allowed "$b" && continue
    unexpected+=("$b")
done
[ "${#unexpected[@]}" -eq 0 ] || FINDINGS+=("unexpected director(y|ies) at the home root: ${unexpected[*]}")

echo "[home-hygiene] $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "${#FINDINGS[@]}" -eq 0 ]; then
    echo "  clean: 1 worktree, 0 loose files, no unexpected directories"
    exit 0
fi
for f in "${FINDINGS[@]}"; do echo "  [ x ] $f"; done

# Same channel the deploy alerts use. Absent webhook logs and returns -- the
# report on stdout is the primary surface, Slack is the nudge.
if [ -n "${GENLAB_COMPLIANCE_SLACK_WEBHOOK:-}" ]; then
    payload="$(printf '%s\\n' "${FINDINGS[@]}")"
    curl -s --max-time 10 -X POST -H 'Content-type: application/json' \
        --data "{\"text\":\"*home hygiene* (${#FINDINGS[@]} finding(s)) on $(hostname -s)\\n$payload\"}" \
        "$GENLAB_COMPLIANCE_SLACK_WEBHOOK" >/dev/null || echo "  (slack post failed; the report above is the record)"
fi
exit 0
