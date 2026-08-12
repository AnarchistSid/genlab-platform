"""2026-08-12: pin the MIGRATION_DATABASE_URL > DATABASE_URL precedence
in `migrations/env.py`.

Motivating incident: post-Audit-A role hardening splits Postgres roles
into `genlab_app` (runtime, no DDL) and `genlab` (schema owner).
`DATABASE_URL` points at genlab_app. Tonight's F-QB-0702 alembic
migration (commit a8242c5c) needed a manual URL override to run as
the owner role — otherwise `ALTER TABLE ... ADD COLUMN` failed with
`must be owner of table`.

`MIGRATION_DATABASE_URL` makes the owner-role override first-class.
env.py checks it before DATABASE_URL. This pin locks in the
precedence so a future refactor of env.py can't silently regress
into "runs alembic under runtime role" (which would resurface the
'must be owner of table' friction on every migration).
"""

from __future__ import annotations

from pathlib import Path

ENV_PY = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "env.py"
)


class TestMigrationDatabaseUrlPrecedence:
    def test_env_py_checks_migration_database_url_first(self):
        """env.py MUST check MIGRATION_DATABASE_URL before DATABASE_URL.
        Otherwise the runtime role runs migrations and every DDL fails."""
        content = ENV_PY.read_text()

        # Find the URL-resolution line. The pattern is
        #   os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
        # Order matters — MIGRATION_DATABASE_URL must appear first.
        mig_pos = content.find('"MIGRATION_DATABASE_URL"')
        db_pos = content.find('"DATABASE_URL"')

        assert mig_pos != -1, (
            "env.py does not reference MIGRATION_DATABASE_URL. Post-role-split "
            "prod migrations will fail with 'must be owner of table' unless "
            "operators manually override DATABASE_URL in-shell every time."
        )
        assert db_pos != -1, (
            "env.py DATABASE_URL reference removed? That's the CI fallback path — "
            "removing it breaks CI + dev migrations."
        )
        assert mig_pos < db_pos, (
            "MIGRATION_DATABASE_URL must be checked BEFORE DATABASE_URL. "
            "Wrong order = runtime role picked even when owner override is set."
        )

    def test_env_py_still_falls_back_to_database_url(self):
        """CI and dev environments only set DATABASE_URL. env.py MUST
        still honor it when MIGRATION_DATABASE_URL is unset."""
        content = ENV_PY.read_text()
        # The `or os.environ.get("DATABASE_URL")` fallback pattern
        # (with optional whitespace) is the shape we want.
        import re

        pattern = re.compile(
            r'os\.environ\.get\(\s*"MIGRATION_DATABASE_URL"\s*\)'
            r'\s*or\s*'
            r'os\.environ\.get\(\s*"DATABASE_URL"\s*\)'
        )
        assert pattern.search(content), (
            "env.py must use `os.environ.get(MIGRATION_...) or "
            "os.environ.get(DATABASE_URL)` shape. Missing the `or` "
            "fallback breaks CI (which only sets DATABASE_URL)."
        )


class TestDeployShLogsUrlChoice:
    def test_deploy_sh_mentions_migration_database_url(self):
        """deploy.sh MUST log which URL is being used so a 'must be owner'
        failure is diagnosable in one grep. Regression against silently
        picking DATABASE_URL when owner override was configured."""
        deploy_sh = (
            Path(__file__).resolve().parents[2] / "scripts" / "deploy.sh"
        )
        content = deploy_sh.read_text()
        assert "MIGRATION_DATABASE_URL" in content, (
            "deploy.sh should reference MIGRATION_DATABASE_URL — either to "
            "check for it or to reference it in the fail-message. Missing "
            "reference means the operator has no signal about which role "
            "alembic used when a permission error surfaces."
        )
