# Q — journald has no SystemMaxUse floor on the VPS

**Filed** 2026-09-21, ANIME-12 §5.

Several diagnoses this month depended on reading journald further back than
it retained. Set a floor rather than discovering the limit during an
incident:

    /etc/systemd/journald.conf
    SystemMaxUse=500M

500 MB against a 38 GB disk shared with Postgres — the same disk that hit
100% on 2026-07-01, so the floor should be stated as a CAP too, not left to
the 10%-of-disk default (3.8 GB).

Verify with `journalctl --disk-usage` before and after
`systemctl restart systemd-journald`.
