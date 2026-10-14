from __future__ import annotations

import pytest

from seoulpulse.matching import classify_video, extract_song

W = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
S = "https://www.youtube.com/shorts/AAAAAAAAAAA"


@pytest.mark.parametrize(
    ("title", "url", "kind"),
    [
        ("KATSEYE (캣츠아이) “Unloveu” Visualizer", S, "short"),                       # Shorts URL wins over everything
        ("LE SSERAFIM (르세라핌) 'Test Pattern' Official MV", S, "short"),
        ("aespa 에스파 'Neon Tide' MV Teaser", W, "teaser"),                              # MV Teaser → teaser
        ("ILLIT (아일릿) ‘BREAK EVEN’ Concept Film QUIET🤫", W, "teaser"),
        ("정한X조슈아 (SEVENTEEN) 1st Mini Album ‘DREAMSCAPE' highlight medley", W, "teaser"),
        ("NMIXX 'Strange Muse' Official Trailer", W, "teaser"),
        ("[Behind] TWICE 'Sample' MV Shooting Sketch", W, "other"),
        ("BOYNEXTDOOR 'Sample' MV Making Film", W, "other"),
        ("KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV", W, "mv"),
        ("[MV] IU(아이유) _ Love wins all", W, "mv"),
        ("[M/V] 임영웅 - 가을 편지 (Autumn Letter)", W, "mv"),
        ("ITZY 'Sample' Official Music Video", W, "mv"),
        ("BLACKPINK - 'Sample' 뮤직비디오", W, "mv"),
        ("호시 (HOSHI) 'BOYFRIEND' Performance Film", W, "perf"),
        ("SEVENTEEN 'Sample' Dance Practice", W, "perf"),
        ("KATSEYE (캣츠아이) “Unloveu” Visualizer", W, "perf"),
        ("[안무 연습] RIIZE 'Sample'", W, "perf"),
        ("[가로 직캠] ILLIT 'Sample' (fancam)", W, "perf"),
        ("ATEEZ - Sample #엠카운트다운 EP.912 | Mnet 241010 방송", W, "show"),
        ("TWS - Sample | M COUNTDOWN 241010", W, "show"),
        ("[STUDIO CHOOM ORIGINAL] NCT WISH 'Sample'", W, "show"),
        ("aespa Weekly Message 🎵", W, "other"),
        ("Messages From #CORTIS", W, "other"),
    ],
)
def test_classify_video(title, url, kind):
    assert classify_video(title, url) == kind


@pytest.mark.parametrize(
    ("title", "song"),
    [
        ("KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV", "Lucky Seven"),       # ASCII '…'
        ("KATSEYE (캣츠아이) “Unloveu” Visualizer", "Unloveu"),                           # “…”
        ("정한X조슈아 (SEVENTEEN) 1st Mini Album ‘DREAMSCAPE' highlight medley", "DREAMSCAPE"),  # mixed ‘…'
        ("ILLIT (아일릿) ‘BREAK EVEN’ Concept Film", "BREAK EVEN"),
        ("[MV] IU(아이유) _ Love wins all", "Love wins all"),                               # 1theK ` _ `
        ("[MV] 임영웅 - 가을 편지 (Autumn Letter)", "가을 편지"),                            # ` - ` + trailing (...)
        ("BIBI - Sample Song [Official Audio]", "Sample Song"),
        ("aespa's 'Neon Tide' MV", "Neon Tide"),                                           # apostrophe in a word
        ("아이유 「밤편지」 Live Clip", "밤편지"),
        ("NewJeans 《Sample》 Dance Practice", "Sample"),
        ("NewJeans <Sample Album> Concept Film", "Sample Album"),
        ('IVE "Sample" Performance Video', "Sample"),
        ("aespa Weekly Message", None),                                                    # none
    ],
)
def test_extract_song(title, song):
    assert extract_song(title) == song


def test_extract_song_earliest_opening_character_wins():
    assert extract_song("“First” then 'Second'") == "First"
    assert extract_song("'First' then “Second”") == "First"


def test_extract_song_truncates_to_24():
    got = extract_song("ARTIST 'Supercalifragilisticexpialidocious Remix' MV")
    assert got == "Supercalifragilisticexp…"
    assert len(got) == 24
    spaced = extract_song("ARTIST 'An Extremely Long Song Title That Keeps Going' MV")
    assert spaced is not None and len(spaced) <= 24 and spaced.endswith("…")


# Regression (live feed, 2026-10-14): aespa's top_video was a travel-variety episode shown as "music show".
AESPA_VARIETY = "[EP.08] 아우디즈의 찾아서 투어✈️ | 멕시코 타코를 찾아서🇲🇽🌮"


@pytest.mark.parametrize("title", [
    AESPA_VARIETY,
    "[GOING SEVENTEEN] EP.163 촬영장에서 생긴 일 : 돈't Lie #2",
    "여기 사람이 갇혀 있어요!! | 밸런스 감옥 | [TO DO X TXT] EP.175",
    "K-발라더 이용복 (K-Balladeer Lee Yongbok) #2｜[SKZ CODE] Ep.105",
    "DaDa-ITZY EP.06 | ⏱️The Time Stopper!",
    "TREASURE TOUR [PULSE ON] IN SEOUL DOCUMENTARY - YG PRODUCTION EP.7",
    "Steal the showcase: a sample vlog-free day",
])
def test_episodic_variety_is_not_a_music_show(title):
    assert classify_video(title, W) == "other"


@pytest.mark.parametrize("title", [
    "ATEEZ - Sample #엠카운트다운 EP.912 | Mnet 241010 방송",           # music-show name still wins
    "KickFlip (킥플립).zip 📂 Sample부터 Sample까지 | Show! MusicCore",
    "[안방1열 풀캠4K] 크래비티 'LOUDER' (CRAVITY FullCam) @SBS Inkigayo 261011",
    "IVE - Sample | Show Champion | EP.512",
    "TWS - Sample | THE SHOW 241010",
])
def test_music_show_stages_still_classify_as_show(title):
    assert classify_video(title, W) == "show"
