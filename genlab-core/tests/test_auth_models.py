from __future__ import annotations

from datetime import datetime


def test_permission_ordering():
    from genlab_core.auth.models import Permission
    assert Permission.VIEWER < Permission.EDITOR
    assert Permission.EDITOR < Permission.PUBLISHER
    assert Permission.PUBLISHER < Permission.ADMIN


def test_team_creation():
    from genlab_core.auth.models import Team
    t = Team(team_id="t1", team_name="GenLab Ops", admin_user_id="u1", created_at=datetime.now())
    assert t.team_name == "GenLab Ops"


def test_niche_access_defaults():
    from genlab_core.auth.models import NicheAccess
    na = NicheAccess(team_id="t1", niche_id="gaming")
    assert na.can_publish is False
    assert na.can_approve is False
