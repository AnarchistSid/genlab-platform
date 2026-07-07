# Operator runbook — Monetization catalog cleanup

**Sprint**: Monetization Layer 3 (2026-07-07)
**PR**: L3 PR 14
**Audience**: Operator with prod SSH access
**Time**: ~5 minutes
**Requires**: Prod SSH access to Hetzner VPS

## Context

`genlab-core/config/affiliate_catalog.yaml` is gitignored — it lives on
prod at `/opt/genlab/genlab-core/config/affiliate_catalog.yaml` and is
never committed. That means catalog cleanups can't be shipped as PRs;
they need to be applied on prod manually.

This runbook documents 5 catalog entries that should be removed. All
5 are `enabled: false` — the matcher already skips them at runtime, but
they take up bandit_arms space after L3 PR 3's arm registration.

## The 5 entries to remove

| Niche | Product | Why it's dead |
|---|---|---|
| sports | FanCode Subscription | `networks: {}` — no working affiliate URL |
| movies | Netflix Premium | Netflix has NO public affiliate program |
| anime | Crunchyroll Premium | `networks: {}` — no working affiliate URL |
| ai_creators | ChatGPT Plus | OpenAI has no public affiliate program |
| ai_creators | Midjourney Subscription | Duplicates the enabled `Midjourney` source-tool entry |

## Steps

1. **SSH to prod**:

   ```bash
   ssh -i ~/.ssh/id_ed25519 root@46.224.237.56
   ```

2. **Back up the current catalog**:

   ```bash
   cp /opt/genlab/genlab-core/config/affiliate_catalog.yaml \
      /opt/genlab/genlab-core/config/affiliate_catalog.yaml.bak-$(date +%Y%m%d)
   ```

3. **Delete the 5 blocks**. Each is a `- name: <X>` YAML product entry
   with `enabled: false`. Confirm current state:

   ```bash
   awk '/^    - name:/{name=$0} /enabled: false/{print name}' \
       /opt/genlab/genlab-core/config/affiliate_catalog.yaml
   ```

   Expected output:

   ```
       - name: FanCode Subscription
       - name: Netflix Premium
       - name: Crunchyroll Premium
       - name: ChatGPT Plus
       - name: Midjourney Subscription
   ```

   Edit the file with your preferred editor and delete each product
   block (from `    - name: <X>` down to the last field, INCLUDING
   the trailing indented lines). Boundary: the next `    - name:` OR
   the niche's closing (next top-level `  <niche>:` line).

4. **Verify**:

   ```bash
   awk '/^    - name:/{name=$0} /enabled: false/{print name}' \
       /opt/genlab/genlab-core/config/affiliate_catalog.yaml
   ```

   Expected output: (empty)

5. **Regenerate arm registrations** to remove the now-dead entries:

   ```bash
   cd /opt/genlab
   uv run python scripts/register_product_arms.py --dry-run
   ```

   Expected: total drops from 113 → 108 arms.

   Then apply:

   ```bash
   uv run python scripts/register_product_arms.py --niche all
   ```

6. **Clean the dead arms from bandit_arms** (they'd otherwise linger
   with cold-start posteriors forever):

   ```bash
   sudo -u postgres psql genlab -c "
       DELETE FROM bandit_arms
       WHERE arm_type = 'product'
         AND arm_id IN (
             'product__fancode-subscription',
             'product__netflix-premium',
             'product__crunchyroll-premium',
             'product__chatgpt-plus',
             'product__midjourney-subscription'
         );
   "
   ```

   Expected: `DELETE 5` (one per niche where the product was
   registered).

## Rollback

If something goes wrong:

```bash
cp /opt/genlab/genlab-core/config/affiliate_catalog.yaml.bak-$(date +%Y%m%d) \
   /opt/genlab/genlab-core/config/affiliate_catalog.yaml
```

Then re-run `scripts/register_product_arms.py --niche all` to
recreate any deleted arms with Beta(1,1) priors.

## The `TestNoDisabledProducts` pin

`genlab-core/tests/monetization/test_catalog_structural_invariants.py::
TestNoDisabledProducts` enforces the invariant that no catalog product
has `enabled: false`. After this cleanup runs on prod, that test will
pass. It also protects against future regressions — if an operator
later adds `enabled: false` as a "fix later" bookmark, CI fails and
they'll see this doc referenced.

## Sprint context

* `docs/MONETIZATION-LAYER-3-DESIGN.md` § Phase E step 14
* Companion L3 PR 13: `test_catalog_structural_invariants.py` pin tests
* The bandit reward loop (L3 PR 8) already handles missing/deleted
  arms gracefully — they just don't get updated. Deleting stale rows
  is hygiene, not correctness.
