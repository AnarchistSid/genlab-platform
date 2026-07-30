# FIX_A-0011 — Nested `genlab-core/.git` on Mac

## Closes
Nested `.git` in `genlab-core/` on Mac. Orphan HEAD `ec9925c` not reachable from parent repo. `git -C genlab-core` silently uses the nested repo, not the monorepo. A-0061 confirmed no fresh secret in nested history (one legit `PGPASSWORD` cleanup commit).

## Exact change

**Do NOT just `rm -rf genlab-core/.git`.** Even without secret exposure risk, the nested repo may contain commits the parent doesn't. Sequenced procedure:

### Step 1: subject+date cross-reference (NOT SHA — meaningless across repos)
```bash
cd /Users/anarchistsid/GenLab
GIT_DIR=genlab-core/.git git log --all --format='%h %ci %s' > /tmp/nested_commits.txt
# For each commit in nested, grep parent for a matching subject on same-or-later date
while IFS= read -r line; do
  subj=$(echo "$line" | cut -d' ' -f3-)
  date=$(echo "$line" | cut -d' ' -f2)
  hits=$(git log --all --oneline --grep="$subj" --since="$date -1 day" | wc -l)
  [ "$hits" -eq 0 ] && echo "MISSING FROM PARENT: $line"
done < /tmp/nested_commits.txt
```

### Step 2: format-patch any uniques
For each `MISSING FROM PARENT` line from Step 1:
```bash
GIT_DIR=genlab-core/.git git format-patch -1 <SHA> -o /tmp/nested_patches/
```
Review each `.patch` by eye. If it's real work not in parent → `git am /tmp/nested_patches/*.patch` from the parent repo root. If duplicate/superseded → discard.

### Step 3: check for uncommitted work in nested
```bash
GIT_DIR=genlab-core/.git git status --porcelain
GIT_DIR=genlab-core/.git git stash list
GIT_DIR=genlab-core/.git git remote -v
```
Any dirty state or stashes: extract before proceeding.

### Step 4: archive before delete
```bash
tar czf ~/genlab-core-nested-git-archive-$(date +%Y%m%d).tar.gz -C /Users/anarchistsid/GenLab genlab-core/.git
mv ~/genlab-core-nested-git-archive-*.tar.gz ~/backups/  # or somewhere outside the repo tree
```

### Step 5: delete
```bash
rm -rf /Users/anarchistsid/GenLab/genlab-core/.git
```

### Step 6: recurrence guard
- New file: `scripts/verify_no_nested_git.sh`
  ```bash
  #!/usr/bin/env bash
  set -eu
  # A nested .git under any workspace member is banned.
  MEMBERS="BlackboxBrief CriticalRush ClutchWire SpliceReel FrameDrift genlab-core dashboard"
  FOUND=0
  for m in $MEMBERS; do
      if [ -d "$m/.git" ]; then
          echo "ERROR: nested .git found under $m/" >&2
          FOUND=1
      fi
  done
  [ "$FOUND" -eq 0 ] && echo "no nested .git in any workspace member"
  exit "$FOUND"
  ```
- Add to `.pre-commit-config.yaml` local hooks:
  ```yaml
  - repo: local
    hooks:
      - id: no-nested-git
        name: no nested .git under workspace members
        entry: bash scripts/verify_no_nested_git.sh
        language: system
        pass_filenames: false
  ```
- Add to CI (`.github/workflows/ci.yml`) as a step in the pre-commit-check job (same one from FIX_A-0083).

## Verification gate

```bash
ls -d /Users/anarchistsid/GenLab/genlab-core/.git 2>&1
```
Expected: `ls: genlab-core/.git: No such file or directory`

```bash
bash scripts/verify_no_nested_git.sh
```
Expected: `no nested .git in any workspace member`, exit 0.

```bash
# Create a fake nested .git and verify the guard fires
mkdir /Users/anarchistsid/GenLab/dashboard/.git
bash scripts/verify_no_nested_git.sh; echo "exit=$?"
rmdir /Users/anarchistsid/GenLab/dashboard/.git
```
Expected: `ERROR: nested .git found under dashboard/`, `exit=1`. Then cleanup.

## Sequencing
- Must land after: none (nested repo has no secret exposure per A-0061)
- Blocks: none (isolated cleanup)

## Blast radius if wrong
Losing work in a nested-only commit. Steps 1-3 prevent this (subject+date cross-ref catches uniques, stash/status catches WIP). Step 4 archives everything to `~/backups/` before deletion so recovery is possible for weeks. Step 5's `rm -rf` is the only irreversible action, and it's after 4 checks.

## Effort
M (half-day: mostly Step 1 + review time). Step 2 patch application is likely small (nested repo diverged only briefly per prior analysis).

## Owner
dev (Mac-side; Mac is the only host with the nested repo)
