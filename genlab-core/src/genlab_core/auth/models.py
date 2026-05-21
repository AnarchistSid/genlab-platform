"""Team, permission, and niche access models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class Permission(IntEnum):
    VIEWER = 0
    EDITOR = 1
    PUBLISHER = 2
    ADMIN = 3


@dataclass
class Team:
    team_id: str
    team_name: str
    admin_user_id: str
    created_at: datetime


@dataclass
class TeamMember:
    user_id: str
    team_id: str
    permission: Permission
    active: bool = True


@dataclass
class NicheAccess:
    team_id: str
    niche_id: str
    can_publish: bool = False
    can_approve: bool = False
