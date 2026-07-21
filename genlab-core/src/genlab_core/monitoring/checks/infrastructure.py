"""System / infrastructure health checks.

Extracted from ``health_monitor.py`` (2026-07-08 god-module split, DEV-1).
Every check here targets the *host* rather than any single niche's
content pipeline — disk usage, swap pressure, systemd services, git
drift on prod, and the Cloudflare WARP proxy that fronts yt-dlp.

**Patch surface note**: ``check_swap`` reads ``subprocess`` through the
``genlab_core.monitoring.health_monitor`` facade so
``patch("genlab_core.monitoring.health_monitor.subprocess.run")`` and
``patch.object(health_monitor.subprocess, "run", …)`` still take effect
after the module split. The other checks bind ``subprocess.run``
directly and pair with global ``patch("subprocess.run")`` patterns.
"""

from __future__ import annotations

import logging
import os
import subprocess

from genlab_core.monitoring.alerts import Alert

logger = logging.getLogger(__name__)


def check_disk() -> list[Alert]:
    """Check disk usage on root and media volumes.

    Thresholds are read from ``alerting.yaml`` (audit M-1). The warning
    threshold maps to ``thresholds.disk_usage_pct``; critical fires +10
    points above it. Operators can tune without a deploy.

    2026-07-01 fix: previously ran ``df / /mnt/genlab-media`` in ONE
    subprocess call. On hosts without ``/mnt/genlab-media`` (all current
    prod — Hetzner VPS with no separate media mount), df errored on the
    whole command, the exception got logged at DEBUG, and check_disk
    silently returned zero alerts. Prod PG then crashed at 100% disk on
    2026-07-01 with no advance warning. Now each mount is queried
    independently so a missing mount doesn't hide pressure on ``/``.
    Also uses ``shutil.disk_usage`` which is more portable than parsing
    df output and doesn't need a subprocess round-trip.
    """
    import shutil

    from genlab_core.monitoring.alerting_config import get_alerting_config

    cfg = get_alerting_config().thresholds
    warn_pct = cfg.disk_usage_pct  # was hardcoded 85
    crit_pct = min(100, warn_pct + 10)  # was hardcoded 90

    alerts: list[Alert] = []
    # Query each mount INDEPENDENTLY — one missing mount must never hide
    # pressure on the others. This was the 2026-07-01 crash root cause.
    for mount in ("/", "/mnt/genlab-media"):
        try:
            total, used, _free = shutil.disk_usage(mount)
        except (FileNotFoundError, PermissionError):
            # Mount doesn't exist or unreadable — skip silently. On this
            # host it might be an optional media volume; on others it
            # might not be provisioned yet.
            continue
        except Exception as exc:
            # Log-but-continue: one weird mount shouldn't block others.
            logger.warning("check_disk: disk_usage(%r) failed: %s", mount, exc)
            continue
        pct = int(used * 100 / total) if total > 0 else 0
        if pct > warn_pct:
            alerts.append(
                Alert(
                    check="disk_pressure",
                    severity="critical" if pct > crit_pct else "warning",
                    message=f"{mount} at {pct}% usage",
                    details={"mount": mount, "usage_pct": pct},
                    auto_fix=(
                        "Run /opt/genlab/scripts/disk_cleanup.sh (frees ~10 GB "
                        "from gh-runner cache + docker prune)"
                        if mount == "/"
                        else ""
                    ),
                )
            )
    return alerts


def check_services() -> list[Alert]:
    """Check for failed systemd services and attempt restart."""
    alerts = []
    try:
        result = subprocess.run(
            ["systemctl", "list-units", "genlab-*", "--state=failed", "--no-pager", "--plain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            if "failed" not in line.lower():
                continue
            unit = line.split()[0] if line.split() else ""
            if not unit:
                continue
            # Attempt restart
            fix_result = subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True,
                text=True,
                timeout=15,
            )
            fix_status = "restarted" if fix_result.returncode == 0 else "restart failed"
            alerts.append(
                Alert(
                    check="service_down",
                    severity="critical",
                    message=f"{unit} is in failed state",
                    auto_fix=fix_status,
                )
            )
    except Exception as e:
        # Round-3 audit P3 cleanup (2026-06-26): WARN, not DEBUG.
        # This check generates the "service_down" CRITICAL alert AND
        # attempts auto-restart. When systemctl raises (missing
        # binary, permission failure, timeout), the check is
        # SILENTLY DEAD — the SERVICE_DOWN 6h-ago finding from the
        # 2026-06-25 audit is exactly this pattern (services were
        # down, alerts didn't fire, auto-restart didn't trigger).
        logger.warning(
            "[health] check_services systemctl call failed — "
            "service_down alerts will NOT fire AND auto-restart will "
            "NOT run for this cycle: %s",
            e,
        )
    return alerts


def _check_warp_port_listening(host: str = "127.0.0.1", port: int = 40000) -> bool:
    """Probe whether the WARP SOCKS port is in LISTEN state.

    ``ss -ltn`` is preferred over a TCP connect — connect would
    succeed during the brief window after the daemon binds the
    socket but before it's actually proxying. ss reads the kernel's
    listen-queue directly.
    """
    try:
        result = subprocess.run(
            ["ss", "-ltn", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return f"{host}:{port}" in result.stdout or f"*:{port}" in result.stdout


def _attempt_warp_restart() -> str:
    """Escalating recovery for warp-svc — restart → re-verify → second
    attempt → reboot-recommendation.

    The original single-shot ``systemctl restart`` was insufficient for
    the daily WARP wedge documented in the 2026-06-12 autonomy gap
    analysis: ``systemctl restart warp-svc`` returns rc=0 + the unit
    flips Active, but port 40000 stays unbound — yt-dlp still fails
    with "connection refused". The wedge requires a full system reboot
    (verified). This function escalates:

      1. Try ``sudo -n systemctl restart warp-svc``.
      2. Wait 3s, check ``systemctl is-active`` AND port 40000 in LISTEN.
      3. If port still unbound: 2nd restart attempt (transient race).
      4. Wait 8s, re-check.
      5. If still unbound: return an explicit operator-actionable
         "REBOOT REQUIRED" string. Auto-reboot is INTENTIONALLY NOT
         attempted — losing the box mid-deploy or mid-publisher could
         strand state. Reboot stays operator-gated.

    Returns one of:

      * ``"restarted warp-svc, port 40000 LISTENING"`` — happy path
      * ``"restarted warp-svc, daemon active but port 40000 NOT listening"``
        — unit up but proxy not bound (the 2026-06-12 silent failure)
      * ``"REBOOT REQUIRED — 2 restart attempts left warp-svc wedged at
        unit-active-but-port-unbound. SSH and run: reboot"`` — the
        escalation terminal state
      * ``"sudoers not configured — add: genlab ALL=(root) NOPASSWD:
        /bin/systemctl restart warp-svc"`` — first-time setup gap
      * ``"restart failed (rc=N): <stderr-snippet>"`` — other systemctl
        failure
      * ``"restart raised: <exception>"`` — subprocess itself failed

    Always returns a non-empty string so Alert.auto_fix stays
    informative.
    """
    import time

    def _do_restart() -> tuple[int, str, str]:
        """Run sudo -n systemctl restart and return (rc, stdout, stderr)."""
        try:
            result = subprocess.run(
                ["sudo", "-n", "systemctl", "restart", "warp-svc"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as exc:
            return -1, "", f"{type(exc).__name__}: {exc}"

    rc, _, stderr = _do_restart()
    if rc != 0:
        stderr_lc = (stderr or "").lower()
        if "password is required" in stderr_lc or "a terminal is required" in stderr_lc:
            return (
                "sudoers not configured — add: "
                "genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc"
            )
        if rc == -1:
            return f"restart raised: {stderr}"
        stderr_snippet = (stderr or "").strip().splitlines()[:1]
        snippet = stderr_snippet[0][:120] if stderr_snippet else "no stderr"
        return f"restart failed (rc={rc}): {snippet}"

    # First restart succeeded — wait + verify daemon active AND port bound.
    time.sleep(3)
    try:
        verify = subprocess.run(
            ["systemctl", "is-active", "warp-svc"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return f"restart applied, verification raised: {type(exc).__name__}: {exc}"

    daemon_active = verify.returncode == 0 and verify.stdout.strip() == "active"
    port_listening = _check_warp_port_listening()

    if daemon_active and port_listening:
        return "restarted warp-svc, port 40000 LISTENING"

    # Daemon-active but port-unbound is the wedge shape that needs
    # reboot. Try a SECOND restart first — a fraction of these are
    # transient (systemd race during init); if it sticks after attempt
    # #2 we escalate to operator.
    if daemon_active and not port_listening:
        rc2, _, _ = _do_restart()
        if rc2 == 0:
            time.sleep(8)
            if _check_warp_port_listening():
                return "restarted warp-svc (2 attempts), port 40000 LISTENING"
        # 2 restarts didn't unbind the wedge — operator must reboot.
        return (
            "REBOOT REQUIRED — 2 restart attempts left warp-svc wedged at "
            "unit-active-but-port-unbound (2026-06-12 wedge shape). "
            "SSH and run: reboot"
        )

    # Daemon not active after restart — unusual; report honestly so
    # operator sees journalctl is the next step.
    return "restart applied but warp-svc still inactive after 3s"


def check_warp_health() -> list[Alert]:
    """Detect WARP SOCKS proxy outages within minutes, not days.

    yt-dlp routes all video downloads through Cloudflare WARP at
    127.0.0.1:40000 to bypass YouTube's bot-detection on Hetzner
    datacenter IPs. When WARP goes down (daemon stops, mode flips
    away from proxy, port closes), every pipeline that runs in the
    next 24h fails downloads silently, then 6h later the existing
    ``check_download_failures`` fires with a misleading "yt-dlp
    update: success" auto-fix message that doesn't address the
    real network-layer issue.

    History: 2026-05-11 17:33 IST WARP stopped, was disabled at the
    systemd level, never restarted. 5 days of pipeline runs failed
    downloads (4/5 niches at zero blueprints/day) before the audit
    caught it on 2026-05-17.

    This check fires CRITICAL immediately on either:
      * ``warp-svc`` systemd unit not active
      * 127.0.0.1:40000 not in LISTEN state

    Skipped silently when ``warp-svc`` isn't installed at all (dev
    environments don't need WARP — only the Hetzner production
    host routes through it).
    """
    alerts: list[Alert] = []
    try:
        # Is warp-svc installed?  list-unit-files exits 0 even when
        # the unit is missing; check via show + LoadState instead.
        show = subprocess.run(
            [
                "systemctl",
                "show",
                "warp-svc.service",
                "--property=LoadState,ActiveState,SubState",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        kv = dict(
            (line.split("=", 1)[0], line.split("=", 1)[1])
            for line in show.stdout.strip().split("\n")
            if "=" in line
        )
        if kv.get("LoadState") in ("not-found", "masked", ""):
            # WARP not installed — skip silently (dev environments).
            return alerts

        active = kv.get("ActiveState") == "active"
        if not active:
            # Attempt auto-restart via ``sudo -n systemctl restart``.
            # ``-n`` (non-interactive) ensures we fail fast if NOPASSWD
            # isn't configured — we don't want to hang waiting for a
            # password prompt that has nowhere to go.
            #
            # health_monitor.service runs as ``User=genlab``, so the
            # restart needs a sudoers entry. The auto_fix message
            # documents the exact entry to add so the first-time
            # operator action takes seconds:
            #
            #   genlab ALL=(root) NOPASSWD: /bin/systemctl restart warp-svc
            #
            # Until that lands, the message is still strictly better
            # than the prior "not attempted" string — we tried, this
            # is what blocked us, here's the exact fix.
            fix_msg = _attempt_warp_restart()

            alerts.append(
                Alert(
                    check="warp_down",
                    severity="critical",
                    message=(
                        f"warp-svc not active (ActiveState={kv.get('ActiveState')}, "
                        f"SubState={kv.get('SubState')}). All yt-dlp downloads "
                        "will fail with 'curl: (7) connection refused' until "
                        "the daemon is restored. Run: systemctl restart warp-svc"
                    ),
                    details={
                        "load_state": kv.get("LoadState"),
                        "active_state": kv.get("ActiveState"),
                        "sub_state": kv.get("SubState"),
                    },
                    auto_fix=fix_msg,
                )
            )
            # Don't bother checking port if daemon is down.
            return alerts

        # Daemon is up — verify the SOCKS port is actually listening.
        # WARP defaults to whole-OS tunnel mode; needs explicit
        # `warp-cli mode proxy` + `warp-cli proxy port 40000` to
        # surface the SOCKS endpoint.
        ss = subprocess.run(
            ["ss", "-tln"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        port_listening = any(
            "127.0.0.1:40000" in line and "LISTEN" in line for line in ss.stdout.split("\n")
        )
        if not port_listening:
            alerts.append(
                Alert(
                    check="warp_port_closed",
                    severity="critical",
                    message=(
                        "warp-svc is active but SOCKS port 40000 is not "
                        "listening. WARP likely flipped to whole-OS tunnel "
                        "mode. Run: warp-cli mode proxy && warp-cli proxy "
                        "port 40000 && warp-cli connect"
                    ),
                    details={"active_state": "active", "port_40000_listening": False},
                )
            )
    except Exception as e:
        logger.debug("WARP health check failed: %s", e)
    return alerts


def check_git_drift() -> list[Alert]:
    """Detect uncommitted working-tree changes on the production host.

    Without this check, ad-hoc edits accumulate in the working tree
    indefinitely.  History from 2026-05-17 audit: 25+ Python source
    files had real forward-fixes (LinUCB numerical guards,
    frame_compositor drawtext escaping, etc.) sitting uncommitted for
    months — invisible to ``systemctl`` and ``journalctl``.

    Categories matter more than raw counts:
      * YAML config drift is expected — production has prefix env values
        and per-host overrides that won't be in git (the ``assume-unchanged``
        pattern). Limit yaml-drift alerts to "very many" to avoid noise.
      * Python source drift is the real signal. Any uncommitted .py
        accumulation in genlab-core/, scripts/, or dashboard/ means an
        edit happened directly on prod and never made it back to the repo.

    Thresholds (tunable per-deployment):
      * ≥ 5 modified python source files → warning
      * ≥ 15 modified python source files → critical
      * ≥ 30 yaml configs modified → warning (well above normal override count)
    """
    alerts: list[Alert] = []
    project_root = os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")

    try:
        result = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            # Not a git repo, or git not available — silently skip.
            return alerts

        lines = [ln for ln in result.stdout.split("\n") if ln.strip()]
        if not lines:
            return alerts

        # Each line: 'XY filename' where XY is the two-char status.
        py_src_modified: list[str] = []
        yaml_modified: list[str] = []
        for ln in lines:
            if len(ln) < 4:
                continue
            status, _, path = ln[:2], ln[2], ln[3:]
            # Skip untracked-only entries (??) — those are noisier and
            # often legitimate (.tmp files, local notes, etc.).
            if status.strip() == "??":
                continue
            if path.endswith(".py") and (
                path.startswith("genlab-core/")
                or path.startswith("scripts/")
                or path.startswith("dashboard/")
                or path.startswith("BlackboxBrief/")
                or path.startswith("CriticalRush/")
            ):
                py_src_modified.append(path)
            elif path.endswith(".yaml") or path.endswith(".yml"):
                yaml_modified.append(path)

        py_count = len(py_src_modified)
        yaml_count = len(yaml_modified)

        if py_count >= 15:
            alerts.append(
                Alert(
                    check="git_drift",
                    severity="critical",
                    message=(
                        f"{py_count} uncommitted .py files on prod — edits "
                        f"are being made directly on the production host and "
                        f"never reaching the repo. Top 3: "
                        f"{', '.join(py_src_modified[:3])}"
                    ),
                    details={
                        "py_count": py_count,
                        "yaml_count": yaml_count,
                        "py_files": py_src_modified[:10],
                    },
                )
            )
        elif py_count >= 5:
            alerts.append(
                Alert(
                    check="git_drift",
                    severity="warning",
                    message=(
                        f"{py_count} uncommitted .py files on prod. Top 3: "
                        f"{', '.join(py_src_modified[:3])}"
                    ),
                    details={
                        "py_count": py_count,
                        "yaml_count": yaml_count,
                        "py_files": py_src_modified[:10],
                    },
                )
            )

        if yaml_count >= 30:
            alerts.append(
                Alert(
                    check="git_drift_yaml",
                    severity="warning",
                    message=(
                        f"{yaml_count} uncommitted yaml configs on prod — well "
                        "above expected per-host override count."
                    ),
                    details={"yaml_count": yaml_count, "yaml_files": yaml_modified[:10]},
                )
            )
    except Exception as e:
        logger.debug("Git drift check failed: %s", e)
    return alerts


def check_git_ownership_drift() -> list[Alert]:
    """Detect files in ``/opt/genlab/.git/`` not owned by ``genlab:genlab``.

    Class-of-bug shipped 2026-07-19 + 2026-07-21 (this session): git
    commands run via ``sudo git`` or as root leave objects with
    ``root:root`` ownership. Subsequent ``sudo -u genlab git fetch``
    fails with:

        error: insufficient permission for adding an object to
        repository database .git/objects
        fatal: failed to write object
        fatal: unpack-objects failed

    This blocked deploy for ~40 min today when 365 objects had drifted
    to root ownership since July 17. Manual `chown -R genlab:genlab
    /opt/genlab/.git` restored the state, but the drift is recurring
    (41 files 2026-07-18 → 2 files 2026-07-19 → 365 files today).
    Detection here means operator sees the drift BEFORE it blocks
    the next deploy.

    Thresholds:
      * ≥ 1 non-genlab-owned file → WARNING (early signal)
      * ≥ 100 non-genlab-owned files → CRITICAL (deploy-blocking imminent)

    Fail-open on any error (git dir missing / permission-denied on
    the check itself / etc.) — never crash the monitor loop.
    """
    alerts: list[Alert] = []
    project_root = os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")
    git_dir = f"{project_root}/.git"

    try:
        # `find ... -not -group genlab -o -not -user genlab | wc -l`
        # gives us a fast count without materialising the full list.
        # Use ! -group + ! -user with OR semantics (default) — matches
        # any file where EITHER user or group is wrong.
        result = subprocess.run(
            [
                "find",
                git_dir,
                "-not",
                "-group",
                "genlab",
                "-o",
                "-not",
                "-user",
                "genlab",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # find failed — could be missing .git, permission denied on
            # some paths (we run as any user, not necessarily root), etc.
            # Silent skip; not worth alarming on tooling failure.
            return alerts

        lines = [ln for ln in result.stdout.split("\n") if ln.strip()]
        count = len(lines)
        if count == 0:
            return alerts

        severity = "critical" if count >= 100 else "warning"
        # Sample 3 paths for the details payload so operator can spot-
        # check what got drifted.
        sample = lines[:3]
        alerts.append(
            Alert(
                check="git_ownership_drift",
                severity=severity,
                message=(
                    f"{count} file(s) in {git_dir} not owned by genlab:genlab "
                    f"— next `sudo -u genlab git fetch` will hit "
                    f"'insufficient permission' + block deploy"
                ),
                details={"count": count, "sample": sample, "git_dir": git_dir},
                auto_fix=f"sudo chown -R genlab:genlab {git_dir}",
            )
        )
    except Exception as exc:  # noqa: BLE001 — monitor must never crash
        logger.debug("[check_git_ownership_drift] failed: %s", exc)
    return alerts


def check_anthropic_credit() -> list[Alert]:
    """Probe Anthropic API with a minimal request to detect credit exhaustion.

    Class-of-bug shipped 2026-07-21 (this session): the 2026-07-18→21
    outage was caused by Anthropic credit exhausting silently. Symptoms
    were downstream — writers returned refusal preambles, auto_approval
    gate silently failed, dashboards showed VISUAL_READY blueprints
    stuck. Nothing surfaced "Anthropic is broken" until manual
    investigation.

    This check makes a 1-token request to Anthropic. If it returns
    with the exhaustion markers from ``llm/fallback.py``, we alert.

    Fires once per monitor cycle so cost is negligible:
      * Anthropic Haiku input pricing: $1.00 / 1M tokens
      * 1 token/probe × 24 fires/day = 24 tokens/day = < $0.0001/day

    Thresholds:
      * Credit exhaustion detected → CRITICAL
      * Auth failure / rate-limit / network → silent skip (fail-open)

    Kill switch: ``GENLAB_ANTHROPIC_HEALTHCHECK_DISABLED=1`` env var.
    Skip if ``ANTHROPIC_API_KEY`` env is unset (test/dev environments).

    Detection horizon: prevents 4th recurrence of the exhaustion class
    (2026-06-XX first hit, 2026-07-06 hit, 2026-07-18 hit).
    """
    alerts: list[Alert] = []

    if os.environ.get("GENLAB_ANTHROPIC_HEALTHCHECK_DISABLED") == "1":
        return alerts
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return alerts  # dev/test env — no key configured

    try:
        # Import inside the try so monitor still works if anthropic pkg
        # is somehow uninstalled (fail-open on tooling missing).
        import anthropic

        from genlab_core.llm.fallback import should_fallback

        client = anthropic.Anthropic(api_key=api_key, timeout=10.0)
        try:
            # 1-token probe against the cheapest model
            client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        except Exception as api_exc:  # noqa: BLE001
            # Only alert on the exhaustion-class exceptions; auth/network
            # errors are separately investigated + would just add noise.
            if should_fallback(api_exc):
                alerts.append(
                    Alert(
                        check="anthropic_credit_exhausted",
                        severity="critical",
                        message=(
                            "Anthropic API probe returned exhaustion signal: "
                            f"{type(api_exc).__name__}: {str(api_exc)[:200]}. "
                            "Writer + auto_approval_gate + persona_engine "
                            "will silently degrade to OpenAI fallback until "
                            "credit is topped up. Fallback is 7-8x cheaper "
                            "per token but may produce lower-creative-quality "
                            "hooks."
                        ),
                        details={
                            "exception_type": type(api_exc).__name__,
                            "error_snippet": str(api_exc)[:500],
                        },
                        auto_fix=(
                            "Top up Anthropic API credits at "
                            "https://console.anthropic.com/settings/billing"
                        ),
                    )
                )
    except Exception as exc:  # noqa: BLE001 — monitor must never crash
        logger.debug("[check_anthropic_credit] failed: %s", exc)
    return alerts


def check_swap() -> list[Alert]:
    """Check if swap usage is high AND RAM is also under pressure.

    Thresholds are read from ``alerting.yaml`` (audit M-1):
    ``thresholds.swap_critical_pct`` (default 0.9 — fraction of total
    swap for the imminent-OOM warning) and ``thresholds.swap_warning_mb``
    (default 500 — absolute MB for the soft warning).

    2026-06-19 update: warning alerts now also require RAM pressure.
    Linux opportunistically keeps idle pages in swap even when RAM is
    free, so swap > 500MB alone is a noisy false-positive (observed on
    prod: 791MB swap with 1.7GB RAM free). The warning now fires only
    when BOTH swap > warning_mb AND ``MemAvailable < 30%`` of total —
    i.e., the system is genuinely running out of headroom and may start
    thrashing.

    CRITICAL stays unconditional: 90% swap utilization on a 1GB swap
    partition is an imminent-OOM signal regardless of RAM state — the
    kernel can hit unswappable allocations even with abundant RAM if
    swap itself is full.
    """
    # Late-bound facade lookup so tests that patch
    # ``genlab_core.monitoring.health_monitor.subprocess.run`` (or
    # ``patch.object(health_monitor.subprocess, "run", …)``) continue
    # to affect this call site after the 2026-07-08 module split.
    from genlab_core.monitoring import health_monitor as _facade
    from genlab_core.monitoring.alerting_config import get_alerting_config

    cfg = get_alerting_config().thresholds
    critical_pct = cfg.swap_critical_pct  # was hardcoded 0.9
    warning_mb = cfg.swap_warning_mb  # was hardcoded 500
    # New: ratio of MemAvailable / MemTotal below which we consider RAM
    # to be under pressure. 0.3 = 30%. Below this AND swap > 500MB
    # means we're genuinely running out of memory (not just opportunistic
    # swap). Above this AND swap high means Linux is being lazy about
    # swapping idle pages back in — not a real problem.
    ram_pressure_pct = 0.3

    alerts = []
    try:
        result = _facade.subprocess.run(["free", "-b"], capture_output=True, text=True, timeout=5)
        # Parse both Mem: and Swap: lines so we know the RAM-pressure
        # context before deciding whether the swap warning is real.
        mem_total = 0
        mem_available = 0
        swap_total = 0
        swap_used = 0
        for line in result.stdout.split("\n"):
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "Mem:":
                # free -b columns: total used free shared buff/cache available
                mem_total = int(parts[1])
                # Use 'available' (last column) — what apps can actually use,
                # accounting for reclaimable cache/buffers.
                if len(parts) >= 7:
                    mem_available = int(parts[6])
            elif parts[0] == "Swap:":
                swap_total = int(parts[1])
                swap_used = int(parts[2])

        if swap_total > 0:
            ram_available_pct = (mem_available / mem_total) if mem_total > 0 else 1.0
            ram_under_pressure = ram_available_pct < ram_pressure_pct

            # R-67/R-03: a near-full swap is an imminent-OOM signal
            # regardless of RAM state — the kernel can hit unswappable
            # allocations even with abundant RAM. Keep this unconditional.
            if swap_used > critical_pct * swap_total:
                alerts.append(
                    Alert(
                        check="swap_pressure",
                        severity="critical",
                        message=f"Swap CRITICAL: {swap_used // (1024 * 1024)}MB / "
                        f"{swap_total // (1024 * 1024)}MB (>{int(critical_pct * 100)}%) — imminent OOM",
                    )
                )
            elif swap_used > warning_mb * 1024 * 1024 and ram_under_pressure:
                # Warning only when BOTH conditions hold — see docstring.
                alerts.append(
                    Alert(
                        check="swap_pressure",
                        severity="warning",
                        message=f"Swap at {swap_used // (1024 * 1024)}MB / "
                        f"{swap_total // (1024 * 1024)}MB + RAM available "
                        f"{int(ram_available_pct * 100)}% (<{int(ram_pressure_pct * 100)}%) — "
                        f"genuine memory pressure",
                    )
                )
            else:
                # 2026-07-14: auto-resolve stale swap_pressure alerts.
                # Swap on shared 4 GB VPS is CHURNY — peaks to 91% for
                # short bursts (typically warp-svc + aspirehub workers)
                # then recovers to 70% within an hour as page cache
                # gets reclaimed. Without auto-resolve, each transient
                # spike leaves a stale critical row in pipeline_alerts
                # that outlives the actual pressure by hours/days —
                # exactly the class-of-bug from
                # [[class-of-bug-alerts-must-reflect-current-state-not-historical-signal]].
                # When current swap is below both critical + warning
                # thresholds, mark any lingering swap_pressure alerts
                # as resolved.
                _resolve_stale_swap_alerts(
                    current_used_mb=swap_used // (1024 * 1024),
                    total_mb=swap_total // (1024 * 1024),
                )
    except Exception as e:
        logger.debug("Swap check failed: %s", e)
    return alerts


def _resolve_stale_swap_alerts(current_used_mb: int, total_mb: int) -> None:
    """Fire-and-forget cleanup of stale swap_pressure alerts.

    Fails silently on any DB error — the alert is a nice-to-have
    cleanup, not the primary invariant.
    """
    try:
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(niche_id="all") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE pipeline_alerts
                    SET resolved_at = NOW(),
                        auto_fix_applied = %s
                    WHERE check_name = 'swap_pressure'
                      AND resolved_at IS NULL
                    """,
                    (
                        f"auto-resolved: current swap {current_used_mb}MB / "
                        f"{total_mb}MB below alert thresholds",
                    ),
                )
                if cur.rowcount:
                    logger.info(
                        "[swap_pressure] auto-resolved %d stale alerts (current swap %d/%d MB)",
                        cur.rowcount,
                        current_used_mb,
                        total_mb,
                    )
    except Exception as exc:  # noqa: BLE001 — auto-resolve is best-effort
        logger.debug("[swap_pressure] auto-resolve skipped: %s", exc)


def check_foreign_host_writes() -> list[Alert]:
    """Detect rows arriving from any host other than `hetzner-vps`.

    The DB trigger `tag_host_id` populates `extra->>'host_id'` on every
    INSERT/UPDATE. Anything other than `hetzner-vps` here means a process
    on another machine (Mac, dev laptop, attacker) has written to the
    shared DB — the exact split-brain pattern that took out 12 blueprints
    on 2026-04-29 morning before the Mac plists were disabled.

    Returns one critical alert per foreign host_id seen in the last hour.
    """
    from genlab_core.storage.tenant_context import pg_connect

    alerts: list[Alert] = []
    try:
        conn = pg_connect(os.environ.get("DATABASE_URL", ""), niche_id="all")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT extra->>'host_id' AS host, count(*)
            FROM blueprints
            WHERE created_at > NOW() - INTERVAL '1 hour'
              AND extra ? 'host_id'
              AND extra->>'host_id' NOT IN ('hetzner-vps', '')
            GROUP BY 1 ORDER BY 2 DESC
            """
        )
        for host, count in cur.fetchall():
            alerts.append(
                Alert(
                    check="foreign_host_write",
                    severity="critical",
                    message=(
                        f"{count} blueprint(s) written from foreign host '{host}' "
                        f"in the last hour — split-brain in progress"
                    ),
                    details={"host": host, "count": count},
                )
            )
        conn.close()
    except Exception as e:
        logger.debug("Foreign host check failed: %s", e)
    return alerts
