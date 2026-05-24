"""R-42: X media upload must use the v2 endpoint (v1.1 media/upload retired
2025-06-09). Verify the v2 protocol: images = single POST; video = chunked
INIT -> APPEND -> FINALIZE -> (STATUS poll).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from genlab_core.platforms.x_twitter import XTwitterClient

_MOD = "genlab_core.platforms.x_twitter"


def _resp(json_data: dict) -> MagicMock:
    m = MagicMock()
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    return m


def _client() -> XTwitterClient:
    return XTwitterClient(api_key="k", api_secret="s", access_token="t", access_secret="ts")


def test_image_uses_single_v2_post(tmp_path: Path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"x" * 100)
    with patch("requests.post", return_value=_resp({"data": {"id": "111"}})) as mp:
        mid = _client()._upload_media_v2(img)
    assert mid == "111"
    assert mp.call_count == 1
    assert "/2/media/upload" in mp.call_args.args[0]
    assert mp.call_args.kwargs["data"]["media_category"] == "tweet_image"


def test_video_chunked_init_append_finalize(tmp_path: Path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 100)
    posts = [
        _resp({"data": {"id": "222"}}),  # INIT
        _resp({}),  # APPEND
        _resp({"data": {"id": "222", "processing_info": {"state": "succeeded"}}}),  # FINALIZE
    ]
    with patch("requests.post", side_effect=posts) as mp:
        mid = _client()._upload_media_v2(vid)
    assert mid == "222"
    cmds = [c.kwargs["data"]["command"] for c in mp.call_args_list]
    assert cmds == ["INIT", "APPEND", "FINALIZE"]
    init_data = mp.call_args_list[0].kwargs["data"]
    assert init_data["media_type"] == "video/mp4"
    assert init_data["media_category"] == "tweet_video"


def test_video_polls_status_until_succeeded(tmp_path: Path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 100)
    posts = [
        _resp({"data": {"id": "333"}}),
        _resp({}),
        _resp(
            {
                "data": {
                    "id": "333",
                    "processing_info": {"state": "in_progress", "check_after_secs": 0},
                }
            }
        ),
    ]
    statuses = [_resp({"data": {"id": "333", "processing_info": {"state": "succeeded"}}})]
    with (
        patch("requests.post", side_effect=posts),
        patch("requests.get", side_effect=statuses) as mg,
        patch(f"{_MOD}.time.sleep"),
    ):
        mid = _client()._upload_media_v2(vid)
    assert mid == "333"
    assert mg.call_args.kwargs["params"]["command"] == "STATUS"


def test_processing_failed_returns_none(tmp_path: Path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x" * 100)
    posts = [
        _resp({"data": {"id": "444"}}),
        _resp({}),
        _resp({"data": {"id": "444", "processing_info": {"state": "failed"}}}),
    ]
    with patch("requests.post", side_effect=posts):
        mid = _client()._upload_media_v2(vid)
    assert mid is None


def test_extract_media_id_tolerates_shapes():
    assert XTwitterClient._extract_media_id({"data": {"id": "1"}}) == "1"
    assert XTwitterClient._extract_media_id({"media_id_string": "2"}) == "2"
    assert XTwitterClient._extract_media_id({}) is None
