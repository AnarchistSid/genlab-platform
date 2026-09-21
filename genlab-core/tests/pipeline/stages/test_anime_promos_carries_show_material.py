"""Pins: a fetched anime story CARRIES the show's own material.

Two reels shipped on 2026-09-21 made entirely of generated anime pictures of
nothing -- no cover, no PV, no characters -- because the fetcher dropped every
one of those fields at the story boundary. AniList returns them in the same
response the stage already makes; they were extracted and then not carried.

That is the four-gate propagator shape (fetcher -> story -> blueprint ->
column) failing at gate one, and it is invisible downstream: ``carried_art``
correctly reported "the record holds nothing", so the STILL path correctly
generated instead. Every layer behaved; the material never arrived.
"""

from __future__ import annotations

from genlab_core.pipeline.stages import fetch_anime_promos as F
from genlab_core.still.sourcing import show_art


def _media(**over):
    m = {
        "id": 189123,
        "siteUrl": "https://anilist.co/anime/189123",
        "title": {"english": "Blue Box Season 2", "romaji": "Ao no Hako 2"},
        "trailer": {
            "id": "hJ6Y8PAOUk8",
            "site": "youtube",
            "thumbnail": "https://i.ytimg.com/vi/hJ6Y8PAOUk8/hqdefault.jpg",
        },
        "coverImage": {
            "extraLarge": "https://s4.anilist.co/cover/x.jpg",
            "large": "https://s4.anilist.co/cover/l.jpg",
            "color": "#e4ae50",
        },
        "bannerImage": "https://s4.anilist.co/banner/189123.jpg",
        "episodes": 25,
        "season": "FALL",
        "seasonYear": 2026,
        "startDate": {"year": 2026, "month": 10, "day": 3},
        "popularity": 33843,
        "trending": 90,
        "description": "A long enough synopsis to clear the writer's context floor twice over.",
        "genres": ["Romance", "Sports"],
        "studios": {"nodes": [{"name": "Telecom Animation Film"}]},
        "characters": {
            "nodes": [
                {
                    "name": {"full": "Taiki Inomata"},
                    "image": {"large": "https://s4.anilist.co/c/1.jpg"},
                },
                {
                    "name": {"full": "Chinatsu Kano"},
                    "image": {"large": "https://s4.anilist.co/c/2.jpg"},
                },
            ]
        },
    }
    m.update(over)
    return m


def _run_anilist(monkeypatch, media_list):
    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"Page": {"media": media_list}}}

    monkeypatch.setattr(F.requests, "post", lambda *a, **k: R())
    return F._fetch_anilist_trailers(20)


class TestFetcherCarries:
    def test_a_fetched_story_carries_cover_and_trailer_id(self, monkeypatch):
        [p] = _run_anilist(monkeypatch, [_media()])
        assert p["cover_image_url"] == "https://s4.anilist.co/cover/x.jpg"
        assert p["trailer_id"] == "hJ6Y8PAOUk8"
        assert p["trailer_site"] == "youtube"

    def test_every_declared_carried_field_is_actually_emitted(self, monkeypatch):
        """The tuple is the contract. A name in it with no producer is worse
        than no name, because consumers read the tuple."""
        [p] = _run_anilist(monkeypatch, [_media()])
        missing = [f for f in F.ANILIST_CARRIED_FIELDS if f not in p]
        assert missing == [], missing

    def test_characters_carry_name_and_portrait(self, monkeypatch):
        [p] = _run_anilist(monkeypatch, [_media()])
        assert [c["name"] for c in p["characters"]] == ["Taiki Inomata", "Chinatsu Kano"]
        assert all(c["image_url"].startswith("http") for c in p["characters"])

    def test_a_character_with_no_portrait_is_dropped_not_carried_blank(self, monkeypatch):
        m = _media(
            characters={
                "nodes": [
                    {"name": {"full": "Nameless"}, "image": {"large": None}},
                    {
                        "name": {"full": "Taiki Inomata"},
                        "image": {"large": "https://s4.anilist.co/c/1.jpg"},
                    },
                ]
            }
        )
        [p] = _run_anilist(monkeypatch, [m])
        assert [c["name"] for c in p["characters"]] == ["Taiki Inomata"]

    def test_missing_banner_degrades_rather_than_carrying_none(self, monkeypatch):
        """bannerImage was null for 2 of the top 3 FALL 2026 shows. A consumer
        that assumes it exists breaks on the majority case, not the edge."""
        [p] = _run_anilist(monkeypatch, [_media(bannerImage=None)])
        assert p["banner_image_url"] == ""
        assert p["cover_image_url"], "cover must survive a missing banner"

    def test_art_is_carried_with_provenance_never_bare(self, monkeypatch):
        [p] = _run_anilist(monkeypatch, [_media()])
        assert p["art_provenance"] == "https://anilist.co/anime/189123"
        assert "Telecom Animation Film" in p["art_attribution"]


class TestStoryBoundary:
    """Gate one: the promo dict -> the story record."""

    def test_the_fields_survive_onto_the_story(self, monkeypatch):
        ctx = {
            "niche_id": "anime",
            "stories": [],
            "sources_config": {"jikan": {"enabled": False}, "anilist": {"enabled": True}},
        }

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"Page": {"media": [_media()]}}}

        monkeypatch.setattr(F.requests, "post", lambda *a, **k: R())
        out = F.FetchAnimePromos().execute(ctx)
        [story] = out["stories"]
        assert story["cover_image_url"].startswith("http")
        assert story["trailer_id"] == "hJ6Y8PAOUk8"
        assert len(story["characters"]) == 2

    def test_show_art_reads_that_story_and_finds_the_material(self, monkeypatch):
        """The end-to-end claim: what the fetcher carries is what the STILL
        path can see. Before this change show_art found nothing and the kit
        generated a picture of nothing."""
        ctx = {
            "niche_id": "anime",
            "stories": [],
            "sources_config": {"jikan": {"enabled": False}, "anilist": {"enabled": True}},
        }

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"Page": {"media": [_media()]}}}

        monkeypatch.setattr(F.requests, "post", lambda *a, **k: R())
        [story] = F.FetchAnimePromos().execute(ctx)["stories"]

        art = show_art(story)
        assert art.has_any
        assert art.cover.startswith("http")
        assert art.banner.startswith("http")
        assert len(art.characters) == 2
        assert art.provenance.startswith("https://anilist.co/")
        assert art.dominant_colour == "#e4ae50"

    def test_character_lookup_prefers_the_longer_name(self):
        art = show_art(
            {
                "characters": [
                    {"name": "Yami", "image_url": "https://x/1.jpg"},
                    {"name": "Yami Sukehiro", "image_url": "https://x/2.jpg"},
                ]
            }
        )
        hit = art.character_for("Yami Sukehiro finally draws his sword")
        assert hit["image_url"].endswith("2.jpg")

    def test_no_material_still_reports_honestly(self):
        assert not show_art({"title": "X", "source": "anilist"}).has_any
