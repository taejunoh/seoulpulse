from __future__ import annotations

import json
import shutil
from dataclasses import replace

import httpx

from seoulpulse.config import DEFAULT_CONTACT, load_config
from seoulpulse.resolve import extract_channel, resolve_channels, title_matches, validate_seed
from seoulpulse.seed import load_seed

from .conftest import DATA_DIR, FakeClock

PAGE = """<html><head>
<link rel="canonical" href="https://www.youtube.com/channel/UC9GtSLeksfK4yuJ_g1lgQbg">
<meta property="og:title" content="aespa &amp; friends">
</head><body>"externalId":"UC9GtSLeksfK4yuJ_g1lgQbg"</body></html>"""


def test_committed_seed_loads_and_resolves_every_handle():
    seed = load_seed(DATA_DIR)
    assert len(seed.artists) == 82
    assert len(seed.resolved_channels()) == 77
    assert seed.warnings == ()
    kinds = {c.kind for c in seed.channels}
    assert kinds == {"artist", "label", "broadcaster"}
    assert seed.artist_channel_url("aespa") == "https://www.youtube.com/channel/UC9GtSLeksfK4yuJ_g1lgQbg"
    assert seed.artist_channel_url("enhypen") is None            # no own channel; label coverage only


def test_validate_seed_offline_passes(capsys):
    cfg = load_config({}, warn=False)
    assert validate_seed(cfg) == 0
    assert "seed OK" in capsys.readouterr().out


def test_extract_channel_and_title_check():
    cid, title = extract_channel(PAGE)
    assert cid == "UC9GtSLeksfK4yuJ_g1lgQbg" and title == "aespa & friends"
    assert title_matches("AESPA Official", ("aespa",))
    assert not title_matches("Geometry Dash", ("g-dragon", "지드래곤"))


def test_resolve_writes_ok_and_skips_mismatch(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "artists.yaml").write_text(
        "version: 1\nartists:\n"
        "  - {id: aespa, name: aespa, name_ko: 에스파, aliases: [aespa, 에스파], youtube: ['@aespa'], wiki: {en: Aespa, ko: Aespa}}\n"
        "  - {id: g-dragon, name: G-DRAGON, name_ko: 지드래곤, aliases: [G-DRAGON], youtube: ['@GD'], wiki: {en: G-Dragon, ko: null}}\n",
        encoding="utf-8")
    (data / "channels.yaml").write_text("version: 1\nchannels: []\n", encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/@aespa":
            assert req.headers["Accept-Language"].startswith("en-US")
            assert "CONSENT=YES+1" in req.headers["Cookie"]
            assert req.headers["User-Agent"].startswith("Mozilla/5.0")
            return httpx.Response(200, text=PAGE)
        if req.url.path == "/@GD":
            return httpx.Response(200, text=PAGE.replace("aespa &amp; friends", "Geometry Dash")
                                  .replace("UC9GtSLeksfK4yuJ_g1lgQbg", "UCaaaaaaaaaaaaaaaaaaaaaa"))
        if req.url.path == "/feeds/videos.xml":
            return httpx.Response(200, text="<feed/>")
        return httpx.Response(404)

    cfg = replace(load_config({}, warn=False), data_dir=data)
    lines = []
    clock = FakeClock()
    code = resolve_channels(cfg, transport=httpx.MockTransport(handler), sleep=clock.sleep, out=lines.append)
    assert code == 3                                              # @GD unresolved (MISMATCH)
    lock = json.loads((data / "channels.lock.json").read_text(encoding="utf-8"))["channels"]
    assert lock["@aespa"]["channel_id"] == "UC9GtSLeksfK4yuJ_g1lgQbg"
    assert "@GD" not in lock                                      # MISMATCH is never written
    assert any(line.startswith("MISMATCH") and "@GD" in line for line in lines)


def test_resolve_noop_when_lock_is_complete(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    cfg = replace(load_config({}, warn=False), data_dir=data)
    lines = []
    assert resolve_channels(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                            out=lines.append) == 0
    assert "already resolved" in lines[0]


def test_default_contact_is_generic():
    cfg = load_config({}, warn=False)
    assert cfg.contact == DEFAULT_CONTACT and not cfg.contact_configured
    assert "@" not in cfg.user_agent
    assert cfg.user_agent.startswith("SeoulPulse/0.1 (+")
    cfg2 = load_config({"SP_CONTACT": "https://example.invalid/ops"}, warn=False)
    assert cfg2.user_agent == "SeoulPulse/0.1 (+https://example.invalid/ops)"


def test_config_paths_resolve_relative_to_backend(tmp_path):
    cfg = load_config({"SP_DB_PATH": "var/x.db", "SP_PUBLIC_DIR": str(tmp_path / "pub"), "SP_TOP_N": "50"},
                      warn=False)
    assert cfg.db_path == cfg.backend_dir / "var" / "x.db"
    assert cfg.public_dir == tmp_path / "pub"
    assert cfg.top_n == 20                                        # schema caps items at 20


def test_validate_seed_online_with_mock(tmp_path):
    """Online checks: redirect / missing / disambiguation titles are errors; canonical titles pass."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "artists.yaml").write_text(
        "version: 1\nartists:\n"
        "  - {id: aespa, name: aespa, name_ko: null, aliases: [aespa], youtube: [], wiki: {en: Aespa, ko: 에스파}}\n"
        "  - {id: the-boyz, name: THE BOYZ, name_ko: null, aliases: [THE BOYZ], youtube: [], wiki: {en: null, ko: 더보이즈}}\n"
        "  - {id: ghost, name: Ghost, name_ko: null, aliases: [Ghostband], youtube: [], wiki: {en: No Such Page, ko: null}}\n",
        encoding="utf-8")
    (data / "channels.yaml").write_text("version: 1\nchannels: []\n", encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "wikimedia.org":
            return httpx.Response(200, content=b'{"items": []}')
        title = req.url.params.get("titles")
        if title == "에스파":
            body = {"query": {"redirects": [{"from": "에스파", "to": "Aespa"}], "pages": {"1": {"title": "Aespa"}}}}
        elif title == "더보이즈":
            body = {"query": {"pages": {"2": {"title": "더보이즈", "pageprops": {"disambiguation": ""}}}}}
        elif title == "No Such Page":
            body = {"query": {"pages": {"-1": {"title": "No Such Page", "missing": ""}}}}
        else:
            body = {"query": {"pages": {"3": {"title": title}}}}
        return httpx.Response(200, content=json.dumps(body).encode())

    cfg = replace(load_config({"SP_CONTACT": "https://example.invalid/t"}, warn=False), data_dir=data)
    lines = []
    clock = FakeClock()
    code = validate_seed(cfg, online=True, transport=httpx.MockTransport(handler), sleep=clock.sleep,
                         out=lines.append)
    text = "\n".join(lines)
    assert code == 1
    assert "redirect to 'Aespa'" in text
    assert "disambiguation" in text
    assert "page is missing" in text
    assert "en:Aespa" not in text                                  # canonical title passes
