"""Pin: deploy.sh writes the version env to /opt/genlab/.version.env.

The original (PR #592) path was /etc/genlab/version.env. That dir is
root-owned on prod, but deploy.sh runs as `sudo -u genlab`, so the
write silently failed on every deploy until the post-deploy-verify
harness (PR #602) caught it on its first prod run 2026-06-26.

This pin locks in the correct target path so a future revert can't
quietly reintroduce the permission bug. Also pins the canonical
systemd drop-in shape so both the legacy and new EnvironmentFile
lines stay listed (drop-in must remain backwards-compatible during
the transition).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"
DROPIN = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-dashboard.service.d" / "version.conf"


class TestDeployVersionEnvPath:
    def test_deploy_sh_writes_to_opt_genlab_dot_version_env(self):
        """deploy.sh Phase 6.5 MUST target /opt/genlab/.version.env,
        not /etc/genlab/version.env. The genlab user lacks write
        permission on /etc/genlab."""
        content = DEPLOY_SH.read_text()
        assert 'VERSION_ENV_FILE="/opt/genlab/.version.env"' in content, (
            "deploy.sh must write to /opt/genlab/.version.env "
            "(genlab-writable). Found a different VERSION_ENV_FILE "
            "assignment — possibly the legacy /etc/genlab path which "
            "silently fails on every deploy."
        )

    def test_deploy_sh_does_not_assign_legacy_etc_genlab_path(self):
        """Catch the regression directly: any line that ASSIGNS
        VERSION_ENV_FILE to /etc/genlab is wrong."""
        content = DEPLOY_SH.read_text()
        # Allow the path to appear in comments (explaining the legacy
        # path) but not as the active assignment.
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert 'VERSION_ENV_FILE="/etc/genlab' not in stripped, (
                f"deploy.sh has a non-comment line assigning the legacy /etc/genlab path: {line!r}"
            )


class TestSystemdDropInBackwardsCompatible:
    def test_dropin_file_exists(self):
        assert DROPIN.is_file(), (
            f"canonical systemd drop-in must ship at {DROPIN.relative_to(REPO_ROOT)} "
            "so operators have a single source of truth to copy into "
            "/etc/systemd/system/genlab-dashboard.service.d/"
        )

    def test_dropin_loads_both_paths_with_new_one_last(self):
        """Drop-in lists both EnvironmentFile= entries so legacy
        installs (with a stale /etc/genlab/version.env) keep working
        AND fresh deploys win. systemd applies entries in order with
        later entries overriding earlier ones — the new path MUST
        come last."""
        content = DROPIN.read_text()
        legacy_line = "EnvironmentFile=-/etc/genlab/version.env"
        new_line = "EnvironmentFile=-/opt/genlab/.version.env"
        assert legacy_line in content, (
            "drop-in must keep legacy EnvironmentFile entry for "
            "backwards compatibility during transition"
        )
        assert new_line in content, "drop-in must load the new genlab-writable path"
        # New must come AFTER legacy (later wins in systemd).
        assert content.index(new_line) > content.index(legacy_line), (
            "new /opt/genlab/.version.env entry must come AFTER the "
            "legacy /etc/genlab path — systemd applies in order, last "
            "wins. Order reversed would let stale legacy files mask "
            "fresh deploys."
        )

    def test_dropin_uses_fail_open_dash_prefix(self):
        """Both EnvironmentFile lines must start with `-` so missing
        files are tolerated (drop-in installed once, paths populated
        per-deploy)."""
        content = DROPIN.read_text()
        for line in content.splitlines():
            if line.strip().startswith("EnvironmentFile="):
                assert "EnvironmentFile=-" in line, (
                    f"EnvironmentFile line must start with `-` (fail-open) "
                    f"so a missing file doesn't crash the unit: {line!r}"
                )
