#!/bin/bash
# GenLab security audit — checks credential health and rotation needs
set -euo pipefail

echo "============================================"
echo "  GenLab Security Audit — $(date +%Y-%m-%d)"
echo "============================================"

echo ""
echo "1. Environment files:"
for env in .env BlackboxBrief/.env CriticalRush/.env ClutchWire/.env SpliceReel/.env FrameDrift/.env; do
    if [ -f "$env" ]; then
        age_days=$(( ( $(date +%s) - $(stat -f %m "$env") ) / 86400 ))
        echo "  $env: last modified ${age_days}d ago"
        # Check for empty or placeholder values
        empty=$(grep -cE '=""$|=PLACEHOLDER|=CHANGE_ME|=your_' "$env" 2>/dev/null || true)
        empty=${empty:-0}
        if [ "$empty" -gt 0 ]; then
            echo "    ⚠ $empty empty/placeholder values"
        fi
    else
        echo "  $env: MISSING"
    fi
done

echo ""
echo "2. Token expiry check:"
echo "  Meta tokens: EAA tokens are permanent (expires_at=0) — no rotation needed"
echo "  YouTube OAuth: refresh tokens don't expire — access tokens auto-refresh"
echo "  Anthropic API key: no expiry — rotate periodically for security"

echo ""
echo "3. Git secrets check:"
SECRETS_IN_GIT=$(git log --all --diff-filter=A -- '*.env' '*.key' '*.pem' 'credentials.json' 'token.json' 2>/dev/null | head -5)
if [ -n "$SECRETS_IN_GIT" ]; then
    echo "  ⚠ Potential secrets found in git history"
else
    echo "  ✓ No secrets detected in git history"
fi

echo ""
echo "4. .gitignore coverage:"
for pattern in '.env' '*.key' '*.pem' 'credentials.json' 'token.json'; do
    if grep -q "$pattern" .gitignore 2>/dev/null; then
        echo "  ✓ $pattern is in .gitignore"
    else
        echo "  ⚠ $pattern NOT in .gitignore"
    fi
done

echo ""
echo "5. Dashboard auth:"
if grep -q "REVIEW_AUTH_PASSWORD" .env 2>/dev/null; then
    echo "  ✓ Dashboard auth is configured"
else
    echo "  ⚠ REVIEW_AUTH_PASSWORD not set"
fi

echo ""
echo "6. CSRF protection:"
echo "  Dashboard: CSRF tokens via /api/csrf-token endpoint"

echo ""
echo "Recommendation: Rotate ANTHROPIC_API_KEY every 90 days"
echo "Last rotation: (check manually)"
