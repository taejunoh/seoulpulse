from __future__ import annotations

from seoulpulse.fetchers.youtube_rss import parse_youtube_feed
from seoulpulse.matching import classify_video

from .conftest import read_fixture, utc

OBSERVED = utc(2026, 10, 14, 9, 0, 3)


def test_parses_four_entries_with_entry_level_channel_id():
    videos = parse_youtube_feed(read_fixture("youtube_hybe_labels.xml"), OBSERVED)
    assert [v.video_id for v in videos] == ["FIXTURE0001", "FIXTURE0002", "FIXTURE0003", "FIXTURE0004"]
    # The feed-level yt:channelId lacks "UC" and must be ignored in favour of the entry-level one.
    assert {v.channel_id for v in videos} == {"UC3IZKseVpdzPSBaWxBxundA"}
    assert all(v.observed_at == OBSERVED for v in videos)


def test_fields_and_statistics():
    v1, v2, v3, v4 = parse_youtube_feed(read_fixture("youtube_hybe_labels.xml"), OBSERVED)
    assert v1.title == "LE SSERAFIM (르세라핌) 'Test Pattern' Official MV"
    assert v1.url == "https://www.youtube.com/watch?v=FIXTURE0001"
    assert v1.published_at == utc(2026, 10, 13, 9, 0, 2)
    assert v1.views == 1234567
    assert v1.thumbnail_url == "https://i3.ytimg.com/vi/FIXTURE0001/hqdefault.jpg"
    assert v4.views == 203362


def test_shorts_link_detected():
    videos = parse_youtube_feed(read_fixture("youtube_hybe_labels.xml"), OBSERVED)
    short = videos[1]
    assert "/shorts/" in short.url
    assert classify_video(short.title, short.url) == "short"


def test_missing_statistics_keeps_video_without_views():
    videos = parse_youtube_feed(read_fixture("youtube_hybe_labels.xml"), OBSERVED)
    teaser = videos[2]
    assert teaser.video_id == "FIXTURE0003"
    assert teaser.views is None
    assert classify_video(teaser.title, teaser.url) == "teaser"


def test_media_description_is_not_captured():
    videos = parse_youtube_feed(read_fixture("youtube_hybe_labels.xml"), OBSERVED)
    for v in videos:
        assert "FIXTURE-SECRET-DESCRIPTION" not in repr(v)


def test_tolerates_leading_whitespace_and_bom():
    raw = b"\xef\xbb\xbf\n  " + read_fixture("youtube_hybe_labels.xml")
    assert len(parse_youtube_feed(raw, OBSERVED)) == 4
