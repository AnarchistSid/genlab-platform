# Postiz fly.io Deployment — Sprint 31

## Prerequisites

1. flyctl installed: `brew install flyctl` (done)
2. Authenticate: `flyctl auth login`

## Deployment Steps

```bash
cd /Users/anarchistsid/GenLab/docker/postiz

# 1. Create the app
flyctl apps create genlab-postiz --region sin

# 2. Create Postgres (Postiz + Temporal share one)
flyctl postgres create --name genlab-postiz-db --region sin --vm-size shared-cpu-1x --initial-cluster-size 1 --volume-size 1
flyctl postgres attach genlab-postiz-db --app genlab-postiz

# 3. Create Redis
flyctl redis create --name genlab-postiz-redis --region sin --plan free
# Note the REDIS_URL from output

# 4. Set secrets (DO NOT commit these)
flyctl secrets set \
  JWT_SECRET="$(openssl rand -hex 32)" \
  MAIN_URL="https://genlab-postiz.fly.dev" \
  FRONTEND_URL="https://genlab-postiz.fly.dev" \
  NEXT_PUBLIC_BACKEND_URL="https://genlab-postiz.fly.dev/api" \
  X_API_KEY="FgXP2OEXCaQmi1EbI0MTsXqbe" \
  X_API_SECRET="<from docker-compose>" \
  FACEBOOK_APP_ID="2203397347132949" \
  FACEBOOK_APP_SECRET="<from docker-compose>" \
  INSTAGRAM_CLIENT_ID="1416127452837173" \
  INSTAGRAM_CLIENT_SECRET="<from docker-compose>" \
  YOUTUBE_CLIENT_ID="421065395448-qi8d5hr64tff57b9ssha8306vvlmgnro.apps.googleusercontent.com" \
  YOUTUBE_CLIENT_SECRET="<from docker-compose>" \
  --app genlab-postiz

# 5. Create volume for uploads
flyctl volumes create postiz_uploads --region sin --size 1 --app genlab-postiz

# 6. Deploy
flyctl deploy --app genlab-postiz

# 7. Verify
flyctl status --app genlab-postiz
curl https://genlab-postiz.fly.dev
```

## Post-Deploy

1. Add `genlab-postiz.fly.dev` to Meta Developer Console → App Settings → App Domains
2. Update OAuth redirect URIs for Instagram/YouTube/Facebook
3. Connect accounts in Postiz UI at https://genlab-postiz.fly.dev
4. Update publisher plist with fly.io URL (if migrating from local)

## Temporal Note

Postiz requires Temporal for background job scheduling. fly.io deployment may need
Temporal Cloud or a separate Temporal service. Evaluate if the free tier of
Temporal Cloud is sufficient, or if a simpler deployment without Temporal is possible
(Postiz may fall back to in-process scheduling).

## Migration Trigger

Per Sprint 31 spec: 14 days from connection + ≥95% success rate before promoting
fly.io Postiz to primary publisher.
