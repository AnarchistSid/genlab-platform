# Affiliate Network Signup Playbook

5 affiliate networks block PA-API / Impact / ShareASale / CJ / EarnKaro
integration. Each requires operator identity verification + manual
account setup that automation cannot complete. This playbook captures
the signup steps + required artifacts so each one is a checklist, not
a research project.

After signing up, install the credentials via:

```bash
# Edit prod env file atomically
ssh root@46.224.237.56
nano /opt/genlab/.env
# Add the keys (see "Env vars" per network below)
# Save + restart consumers:
systemctl restart genlab-publisher.service genlab-engagement-worker.service
```

---

## 1. Amazon PA-API (Product Advertising API)

**Status**: Blocked on Amazon's "10 qualifying sales in 30 days" rule
**Estimate**: 1-3 months after consistent affiliate-tag traffic

### Path

1. Already have an Amazon Associates account (you do — `aspirehub06-20`
   for IN, `aspirehub-20` for US — verified live)
2. Amazon requires **10 verified affiliate sales within any 30-day
   window** before granting PA-API access
3. Once eligible: Amazon Associates Central → Tools → Product
   Advertising API → Request access
4. Approval is typically same-day after eligibility
5. Capture: Access Key, Secret Key, Associate Tag (per region)

### Env vars

```env
AMAZON_PA_API_KEY=AKIA...
AMAZON_PA_API_SECRET=...
# Per-region tags already set:
AMAZON_US_AFFILIATE_TAG=aspirehub-20
AMAZON_IN_AFFILIATE_TAG=aspirehub06-20
```

### Acceleration plan

- PR #277 (geo→US) is sending US viewers to amazon.com URLs (was
  amazon.in — 0% conversion possible for 91% of audience)
- Run that for 4-6 weeks; sales should accumulate
- Check eligibility weekly via Amazon Associates dashboard

---

## 2. Impact (impact.com)

**Status**: Blocked on operator manual signup + business verification
**Estimate**: 1-2 days for signup, 1-2 weeks for first merchant approvals

### Path

1. Navigate to https://app.impact.com/signup/none/create-new-mediapartner-account-flow.ihtml
2. Sign in with Google (auto-detects email)
3. Provide business details:
   - Business name (e.g. "Aspire Hub Media")
   - Website URL (the niches' channel pages)
   - Country/tax jurisdiction
   - Tax form (W-9 if US, W-8BEN if foreign)
   - Bank/PayPal payment details
4. Account approval: typically same-day for media partners
5. Apply to individual merchant programs (Amazon, Nike, etc.) —
   each merchant approves separately, 1-7 days
6. Once approved by ≥1 merchant: collect campaign IDs + API credentials
   from Impact dashboard → Settings → API & Webhooks

### Env vars

```env
IMPACT_ACCOUNT_SID=IR...
IMPACT_AUTH_TOKEN=...
IMPACT_DEFAULT_CAMPAIGN_ID=...  # optional
```

### Code wiring

The Impact adapter lives at
`genlab-core/src/genlab_core/monetization/networks/impact.py`. Reads
the 3 env vars above. Once credentials are set, the existing
`network_registry` picks it up automatically.

### Pre-check

```bash
# Verify credentials by hitting the actions endpoint (returns empty array
# on fresh account, 401 on bad creds, 200 on good)
curl -u "$IMPACT_ACCOUNT_SID:$IMPACT_AUTH_TOKEN" \
  https://api.impact.com/Mediapartners/$IMPACT_ACCOUNT_SID/Actions
```

---

## 3. ShareASale

**Status**: Blocked on operator account + merchant relationship building
**Estimate**: 1 week for account, 2-4 weeks for first merchants

### Path

1. Navigate to https://shareasale.com/info/affiliates/
2. Click "Sign up" → fill the 5-page form:
   - Step 1: Account info (username/password)
   - Step 2: Personal info (name, address, SSN/EIN for US)
   - Step 3: Website info (URL, traffic source description)
   - Step 4: Payment info (check/wire/PayPal)
   - Step 5: Agreement
3. ShareASale reviews the application — typically 1-3 business days
4. Once approved: Browse merchants, apply individually (each merchant
   approval is 1-7 days)
5. Collect API credentials: My Account → API Tools → API Credentials

### Env vars

```env
SHAREASALE_AFFILIATE_ID=...
SHAREASALE_TOKEN=...
SHAREASALE_SECRET_KEY=...
```

### Code wiring

`genlab-core/src/genlab_core/monetization/networks/shareasale.py` — reads
3 env vars. Auto-discovered by network_registry.

---

## 4. CJ Affiliate (commission junction)

**Status**: Blocked on operator account + Personalized Identifier (PID)
**Estimate**: 1 week for account, 1-3 weeks for first merchants

### Path

1. Navigate to https://signup.cj.com/member/signup/publisher/
2. Application has 4 sections:
   - Account info (email, password)
   - Publisher info (website, traffic source, content type)
   - Tax info (W-9 for US, W-8BEN for foreign)
   - Payment info
3. CJ reviews: 1-5 business days
4. Once approved: Apply to advertisers (Walmart, Best Buy, Lowe's, etc.)
5. Collect: PID (Personalized Identifier) + AID (Advertiser ID) per merchant
6. API key: Account → Network → Web Services → API Documentation

### Env vars

```env
CJ_PID=...                    # Your publisher ID
CJ_DEVELOPER_KEY=...          # API key for product/commission feeds
CJ_DEFAULT_AID=...            # Optional default advertiser
```

### Code wiring

`genlab-core/src/genlab_core/monetization/networks/cj.py`.

---

## 5. Twitter / X API

**Status**: Blocked on operator content-policy decision + Twitter Dev account
**Estimate**: Account setup is instant, BUT content review is operator-only

### Background

PR #326 ships a per-niche `platforms.<name>.enabled: false` filter so the
publisher gracefully skips Twitter for anime/movies/sports/AI niches that
have `x.enabled: false` in their `publishing.yaml`. The blocker isn't
technical — it's policy: does the operator want each channel publishing
to Twitter at all?

### Path (if enabling Twitter)

1. https://developer.twitter.com/en/portal/dashboard → Sign in with the
   relevant Twitter account
2. Apply for API access — Free tier allows 1,500 posts/month per app
3. Once approved (typically same-day for personal use case):
   - Create an App in the Twitter Dev Portal
   - Generate: API Key + API Secret + Access Token + Access Secret
4. Set per-niche env vars (one set per channel that has its own Twitter account)
5. Flip `x.enabled: true` in that niche's `publishing.yaml`
6. The genlab `platforms/twitter.py` client picks up the credentials via
   `resolve_twitter_credentials(niche_id)` (the per-niche shim)

### Env vars (per-niche, 5 sets if all 5 enable Twitter)

```env
# Shared app keys
X_API_KEY=...
X_API_SECRET=...

# Per-niche tokens (prefix maps via NICHE_CREDENTIAL_PREFIXES)
CRITICALRUSH_X_ACCESS_TOKEN=...
CRITICALRUSH_X_ACCESS_SECRET=...
CLUTCHWIRE_X_ACCESS_TOKEN=...
CLUTCHWIRE_X_ACCESS_SECRET=...
# (etc. for SPLICEREEL, FRAMEDRIFT, BLACKBOXBRIEF)
```

### Decision needed first

For each niche, decide:
- Do we want Twitter posts? (low effort, low reach for non-tech content)
- Do we want to use the Free tier (1500/mo) or pay $100/mo for Basic
  (10K/mo)?
- For anime/sports/gaming: do the channels even have Twitter accounts?

If skipping Twitter long-term: just leave `x.enabled: false` in each
`publishing.yaml`. PR #326 makes the publisher silently skip without
error.

---

## Time + difficulty matrix

| Network | Setup time | Credential time | Difficulty | Revenue priority |
|---|---|---|---|---|
| **PA-API** | Already done | Waiting on 10 sales | Easy after eligibility | High (highest commission rates) |
| **Impact** | 30 min | 1-2 days | Easy | High (premium brands) |
| **ShareASale** | 45 min | 1-3 days | Medium | Medium |
| **CJ** | 45 min | 1-5 days | Medium | Medium |
| **Twitter** | 15 min | Same-day | Easy (account) / Hard (decision) | Low (limited reach) |

### Recommended order

1. **Impact** first (premium brands, easy signup, good free tools)
2. **ShareASale** second (broad merchant catalog including smaller niche brands)
3. **CJ** third (overlap with above two but unique merchants too)
4. **PA-API** waits naturally on Amazon eligibility (no action needed today)
5. **Twitter** is a policy decision — answer the "do we want it?"
   question first

---

## After ANY install

```bash
# 1. Install the env vars (use scripts/install_elevenlabs_api_key.sh
#    as a template — modify the variable name and run via stdin pipe)

# 2. Restart consumers
ssh root@46.224.237.56 'systemctl restart genlab-publisher.service genlab-engagement-worker.service'

# 3. Smoke-test the credentials with a real API call (each network
#    documents this in their own docs)

# 4. Watch the dashboard's Revenue card over the next 7d for first
#    clicks → conversions
```
