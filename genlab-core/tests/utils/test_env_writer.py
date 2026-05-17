"""Tests for env_writer — atomic .env file updates."""
from __future__ import annotations

import os
from pathlib import Path

from genlab_core.utils.env_writer import update_env_file


def test_replaces_existing_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=old\nBAR=keepme\n")
    assert update_env_file({"FOO": "new"}, env)
    content = env.read_text()
    assert "FOO=new" in content
    assert "BAR=keepme" in content


def test_appends_new_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n")
    assert update_env_file({"NEW_KEY": "value"}, env)
    content = env.read_text()
    assert "EXISTING=1" in content
    assert "NEW_KEY=value" in content


def test_creates_file_if_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert not env.exists()
    assert update_env_file({"KEY": "value"}, env)
    assert env.exists()
    assert "KEY=value" in env.read_text()


def test_quotes_values_with_spaces(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert update_env_file({"KEY": "value with space"}, env)
    assert 'KEY="value with space"' in env.read_text()


def test_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# This is a comment\n\nFOO=1\n# Another comment\nBAR=2\n")
    assert update_env_file({"FOO": "99"}, env)
    content = env.read_text()
    assert "# This is a comment" in content
    assert "# Another comment" in content
    assert "FOO=99" in content
    assert "BAR=2" in content


def test_rejects_invalid_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("")
    assert not update_env_file({"lowercase": "bad"}, env)
    assert not update_env_file({"WITH-DASH": "bad"}, env)
    assert not update_env_file({"WITH SPACE": "bad"}, env)


def test_multiple_updates_at_once(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\n")
    assert update_env_file({"A": "10", "C": "30"}, env)
    content = env.read_text()
    assert "A=10" in content
    assert "B=2" in content
    assert "C=30" in content


def test_preserves_file_mode(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("X=1\n")
    os.chmod(env, 0o600)
    assert update_env_file({"X": "2"}, env)
    mode = env.stat().st_mode & 0o777
    assert mode == 0o600


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    """Verify atomic write cleans up any intermediate temp files."""
    env = tmp_path / ".env"
    env.write_text("TOKEN=abc\n")
    big_value = "x" * 10000
    assert update_env_file({"TOKEN": big_value}, env)
    content = env.read_text()
    assert f"TOKEN={big_value}" in content
    leftover = list(tmp_path.glob(".env.*"))
    assert leftover == [], f"temp files not cleaned up: {leftover}"
