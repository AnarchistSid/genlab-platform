# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in GenLab, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email **security@<your-domain>** with:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

We will acknowledge receipt within 48 hours and provide a timeline for a fix.

## Supported Versions

Only the latest version on `main` is supported with security updates.

## Security Practices

- All secrets are stored in `.env` files (never committed to git)
- Platform credentials use per-niche prefixed env vars
- SQL queries use parameterized statements
- All external content is treated as untrusted (sanitized before use)
- CSRF protection on all authenticated state-changing endpoints
- Webhook signature verification (HMAC-SHA256)
