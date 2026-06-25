# Auto-deploy activation runbook

The `.github/workflows/auto-deploy.yml` workflow ships **disabled**.
This doc walks the operator through enabling it.

## Why this is opt-in (not opt-out)

The operator's current deploy workflow is **deliberate manual deploy** —
SSH to prod, run `./scripts/deploy.sh --apply`, verify. The script is
safe-by-default (refuses on dirty tree, refuses on non-main, requires
explicit `--apply`).

Auto-deploy changes that contract: every merge-to-main fires the deploy
without operator review. That's the RIGHT default for most projects but
the operator has chosen the deliberate model for valid reasons (4 GB
Hetzner VPS — concurrent restart could OOM; sensitive multi-tenant data;
no on-call escalation chain yet).

So the workflow ships with `if: false` as a safety guard. The operator
flips it on consciously.

## Why this exists at all

The 2026-06-25 prod audit found:
- Dashboard shows version 0.0.0
- 9 PRs from one session not visible on prod (despite merge to main)
- The dashboard restart didn't pick up the latest bundle

Cause: no automated deploy on merge. Operator deploys manually but does
not deploy after every merge. PRs accumulate on main without reaching
prod. The dashboard surface becomes a stale view of the codebase.

Auto-deploy fixes this if-and-only-if the operator is comfortable with
"every merge ships." For non-merge-worthy commits (work-in-progress on
main), the operator can either:
- Land them as PRs that merge once stable
- Pre-flight branches without merging
- Use `workflow_dispatch` (manual trigger) instead of `push: main`

## Activation sequence

### 1. Prepare the SSH key pair

On a workstation (NOT prod):

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/genlab_deploy
```

This produces:
- `~/.ssh/genlab_deploy` (private key — for GitHub secret)
- `~/.ssh/genlab_deploy.pub` (public key — for prod authorized_keys)

### 2. Add the public key to prod

SSH to prod as the `genlab` user:

```bash
ssh genlab@<HETZNER_HOST>
mkdir -p ~/.ssh && chmod 700 ~/.ssh
cat >> ~/.ssh/authorized_keys <<< "<contents of genlab_deploy.pub>"
chmod 600 ~/.ssh/authorized_keys
```

Test from a different machine:

```bash
ssh -i ~/.ssh/genlab_deploy genlab@<HETZNER_HOST> 'echo ok'
# Should print: ok
```

### 3. Configure GitHub secrets

In the GitHub repo: Settings → Secrets and variables → Actions → New
repository secret.

- `HETZNER_SSH_KEY` — paste the entire `~/.ssh/genlab_deploy` (private)
  including the `-----BEGIN OPENSSH PRIVATE KEY-----` / `-----END...-----`
  headers
- `HETZNER_HOST` — the hostname (e.g., `box.aspirehub.ai`) or IP

### 4. Test with workflow_dispatch first

In GitHub: Actions tab → "Auto-deploy on main" → "Run workflow" →
select main branch → Run.

The workflow will:
1. Set up SSH agent with HETZNER_SSH_KEY
2. Verify host fingerprint via ssh-keyscan
3. SSH to prod, run scripts/deploy.sh --apply
4. Wait 30s, hit /api/health, verify version bumped from 0.0.0

If all steps green: ready to flip the safety guard.

### 5. Flip the safety guard

Edit `.github/workflows/auto-deploy.yml`:

```yaml
jobs:
  deploy:
    if: false   # ← change to: true
```

Commit + push. Next merge to main will auto-deploy.

### 6. (Recommended) Wire dependencies on CI passing

Once auto-deploy is stable, add `needs:` to make it wait for CI:

```yaml
jobs:
  deploy:
    if: true
    needs: [lint, test, integration]   # adjust to actual job names in ci.yml
    runs-on: ubuntu-latest
```

This ensures a failing test prevents deploy.

### 7. (Recommended) Add failure notification

The placeholder `Notify on failure` step just prints. Replace with a
real notification — Slack webhook is easiest:

```yaml
- name: Notify on failure
  if: failure()
  env:
    SLACK_URL: ${{ secrets.SLACK_DEPLOY_FAILURE_WEBHOOK }}
  run: |
    curl -X POST -H 'Content-type: application/json' \
      --data '{"text":"🚨 Auto-deploy FAILED on main. See: '"$GITHUB_RUN_URL"'"}' \
      "$SLACK_URL"
```

## Rollback plan

If auto-deploy causes problems:

1. **Immediate**: change `if: true` back to `if: false`, commit, push.
   Next merge no longer deploys. Operator resumes manual deploys.

2. **Per-merge skip**: prefix a commit message with `[skip ci]` or
   `[no-deploy]`. The workflow respects `[skip ci]` by default; if you
   want a custom skip, add `if: !contains(github.event.head_commit.message, '[no-deploy]')`
   to the deploy job.

3. **Permanent removal**: delete `.github/workflows/auto-deploy.yml`.

## What this workflow does NOT do

- **No coordinated migration**: scripts/deploy.sh handles migrations
  but doesn't roll back on failure. If a migration fails, the operator
  is paged via Actions email + must SSH to inspect.
- **No multi-stage canary**: deploys to prod directly, not via
  staging. For a 5-niche dashboard with 198 followers, that's
  appropriate. For larger scale, add a staging step.
- **No blue-green or zero-downtime**: services restart in place. There
  will be a few seconds of dashboard unavailability per deploy. The
  operator's review traffic is low enough this doesn't matter.
- **No deploy frequency limit**: every push to main triggers. If the
  operator pushes 10 commits in a minute (e.g., during a refactor),
  there will be 10 deploys queued. Combine with `[skip ci]` markers
  on intermediate commits.

## Security posture

- SSH private key stored as GitHub secret (encrypted at rest)
- Host fingerprint verified via ssh-keyscan (TOFU pattern)
- Workflow uses env: for secrets (no inline interpolation in run:)
- The genlab user on prod has access to: git pull, alembic upgrade,
  systemctl restart — no sudo escalation
- Workflow runs in GitHub-hosted ubuntu-latest (clean env per run)
- Logs do not echo secrets

## Related

- `scripts/deploy.sh` — the manual deploy script the workflow invokes
- `dashboard/runbooks/review_server_wrapper.sh` — local dev wrapper
- 2026-06-25 prod audit (memory: `MASTER-prod-findings-2026-06-25.md`)
- Memory: `ACTION-PLAN-next-session.md` P0 item #1
