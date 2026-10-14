"""Naver DataLab "Korean search spike" (spec §3.7, §5.3, §5.9, §6): off when keyless, compliant storage,
quota guard, endpoint fallback, failure isolation, LLM exclusion and the normative worked example."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import date, timedelta

import httpx
import jsonschema
import pytest

from seoulpulse.config import load_config
from seoulpulse.fetchers import default_fetchers
from seoulpulse.fetchers import naver_datalab as nd
from seoulpulse.fetchers.base import ApiUsage, SearchRow
from seoulpulse.pipeline import run_once
from seoulpulse.publish import build_item
from seoulpulse.scoring import (
    apply_rank_changes,
    rank_scores,
    score_all,
    search_signal,
    search_spike,
)
from seoulpulse.seed import SeedError, load_seed
from seoulpulse.store import Store
from seoulpulse.why import apply_llm_whys, llm_input, template_why

from .conftest import DATA_DIR, SCHEMA_PATH, FakeClock, utc
from .fakenet import FakeNet
from .test_scoring_worked_example import ARTISTS, BASIS, FIRST_RUN, MENTIONS, VIDEOS, WIKI
from .test_scoring_worked_example import NOW as EX_NOW

NOW = utc(2026, 10, 14, 9, 0, 0)            # 18:00 KST → target day (KST yesterday) 2026-10-13
TARGET = date(2026, 10, 13)
CID, SECRET = "NAVER-ID-FIXTURE-777", "NAVER-SECRET-FIXTURE-999"
RAW_SENTINEL = 37.13579                      # a raw Naver ratio that must never be persisted
FLAT = [20, 19, 21, 20, 20, 21, 19, 20, 20, 20, 21, 19, 20, 20]     # μ = 20, σ ≈ 0.65 → σ_eff = 5


def series_for(group: str, end: date, *, spike: str) -> list[dict]:
    days = [end - timedelta(days=27 - i) for i in range(28)]
    out = []
    for i, d in enumerate(days):
        v = float(FLAT[i % 14])
        if group == spike and d == end:
            v = 46.0                                       # 2.3x the baseline → z = 5.2
        if group == "bts" and i == 3:
            v = RAW_SENTINEL                               # outside the 14-day baseline window
        out.append({"period": d.isoformat(), "ratio": v})
    return out


class FakeNaver:
    """Both DataLab endpoints. Records every request; statuses are per endpoint."""

    def __init__(self, hub=200, developers=200, *, spike="lim-young-woong", landed=True, exc=None):
        self.status = {"hub": hub, "developers": developers}
        self.spike, self.landed, self.exc = spike, landed, exc
        self.requests: list[tuple[str, httpx.Request]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        name = "hub" if req.url.host == "naverapihub.apigw.ntruss.com" else "developers"
        self.requests.append((name, req))
        if self.exc is not None:
            raise self.exc(f"simulated {req.url}", request=req)
        status = self.status[name]
        if status != 200:
            body = {"error": {"errorCode": "200", "message": "Authentication Failed"}} if name == "hub" \
                else {"errorMessage": "Authentication failed", "errorCode": "024"}
            return httpx.Response(status, content=json.dumps(body).encode())
        body = json.loads(req.content)
        end = date.fromisoformat(body["endDate"]) - (timedelta(days=0) if self.landed else timedelta(days=1))
        results = [{"title": g["groupName"], "keywords": g["keywords"],
                    "data": series_for(g["groupName"], end, spike=self.spike)} for g in body["keywordGroups"]]
        return httpx.Response(200, content=json.dumps({"startDate": body["startDate"], "endDate": body["endDate"],
                                                       "timeUnit": "date", "results": results}).encode())

    def calls(self, name: str | None = None) -> list[httpx.Request]:
        return [r for n, r in self.requests if name is None or n == name]


def naver_config(config, **kw):
    return replace(config, naver_client_id=CID, naver_client_secret=SECRET, **kw)


def run(cfg, naver: FakeNaver | None, now=NOW, net: FakeNet | None = None, **kw):
    net = net or FakeNet()
    base = net.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host in ("naverapihub.apigw.ntruss.com", "openapi.naver.com"):
            assert naver is not None, "no Naver call expected"
            return naver(req)
        return base(req)

    clock = FakeClock()
    return run_once(cfg, now=now, transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock,
                    rand=lambda: 0.0, deadline_sec=10**6, **kw)


def db_dump(cfg) -> str:
    conn = sqlite3.connect(cfg.db_path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- pure parts


def test_spike_math_matches_the_worked_example():
    s = {TARGET - timedelta(days=14 - i): float(v) for i, v in enumerate(FLAT)}
    s[TARGET] = 46.0
    sp = search_spike(s, TARGET)
    assert (sp.missing, sp.day, sp.mu, sp.sigma_eff, sp.baseline_n) == (False, TARGET, 20.0, 5.0, 14)
    assert sp.sigma == pytest.approx(0.6547, abs=1e-4)
    assert sp.z == pytest.approx(5.2) and sp.ratio == pytest.approx(2.3)
    # scale-free: Naver rescales each response to max 100, which must not change z or the ratio
    scaled = search_spike({d: v * 2.17 for d, v in s.items()}, TARGET)
    assert scaled.z == pytest.approx(sp.z) and scaled.ratio == pytest.approx(sp.ratio)


def test_spike_missing_cases():
    assert search_spike({}, TARGET).reason == "no data"
    assert search_spike({TARGET - timedelta(days=3): 5.0}, TARGET).reason == "stale"
    short = {TARGET - timedelta(days=k): 5.0 for k in range(0, 6)}
    assert search_spike(short, TARGET).reason == "short baseline"
    tiny = {TARGET - timedelta(days=k): 0.004 for k in range(0, 15)}
    assert search_spike(tiny, TARGET).reason == "low volume"
    lag = {TARGET - timedelta(days=k): 10.0 for k in range(2, 17)}      # D = target − 2 is still usable
    assert search_spike(lag, TARGET).missing is False


def test_request_body_parse_and_error_codes():
    body = nd.request_body([("aespa", ("에스파",)), ("itzy", ("ITZY",))], date(2026, 9, 16), TARGET)
    assert body == {"startDate": "2026-09-16", "endDate": "2026-10-13", "timeUnit": "date",
                    "keywordGroups": [{"groupName": "aespa", "keywords": ["에스파"]},
                                      {"groupName": "itzy", "keywords": ["ITZY"]}]}
    content = json.dumps({"results": [{"title": "aespa", "keywords": ["에스파"],
                                       "data": [{"period": "2026-10-12", "ratio": 47.00101},
                                                {"period": "bad", "ratio": 1}]}]}).encode()
    assert nd.parse_trend(content) == {"aespa": {date(2026, 10, 12): 47.00101}}
    assert nd.error_code(b'{"error": {"errorCode": "200", "message": "x"}}') == "200"
    assert nd.error_code(b'{"errorMessage": "Authentication failed", "errorCode": "024"}') == "024"
    assert nd.error_code(b'{"errMsg": "bad", "errId": "E1"}') == "E1"
    assert nd.error_code(b"<html>") == ""
    assert nd.endpoint_order("auto") == ["hub", "developers"] and nd.endpoint_order("developers") == ["developers"]


@pytest.mark.parametrize(("row", "now", "due"), [
    (None, NOW, True),
    (SearchRow("a", TARGET - timedelta(days=1), True, None, None, None, 0, "", NOW), NOW, True),      # new day
    (SearchRow("a", TARGET, True, TARGET, 1.0, 1.1, 14, "", NOW), NOW, False),                       # landed
    (SearchRow("a", TARGET, False, None, None, None, 0, "", NOW), NOW + timedelta(hours=1), False),  # wait
    (SearchRow("a", TARGET, False, None, None, None, 0, "", NOW), NOW + timedelta(hours=2), True),   # re-poll
])
def test_needs_fetch(row, now, due):
    assert nd.needs_fetch(row, TARGET, now) is due


def test_search_keywords_from_roster():
    seed = load_seed(DATA_DIR)
    a = seed.by_id
    assert a["aespa"].search_keywords() == ("에스파",)
    assert a["itzy"].search_keywords() == ("ITZY",)              # explicit: 있지 is a deny-listed word
    assert a["jisoo"].search_keywords() == ("블랙핑크 지수",)      # name_ko 지수 is never added implicitly
    assert a["twice"].search_keywords() == ("트와이스",)          # Latin English-word aliases are not used
    assert all(0 < len(x.search_keywords()) <= 20 for x in seed.artists)


def test_search_override_is_validated(tmp_path):
    (tmp_path / "artists.yaml").write_text(
        "version: 1\nartists:\n  - id: x\n    name: X\n    aliases: [Xxx]\n    youtube: []\n"
        "    wiki: {en: null, ko: null}\n    search: [있지, a]\n", encoding="utf-8")
    (tmp_path / "channels.yaml").write_text("version: 1\nchannels: []\n", encoding="utf-8")
    with pytest.raises(SeedError) as e:
        load_seed(tmp_path)
    assert "deny-list" in str(e.value) and "≥2 characters" in str(e.value)


# ---------------------------------------------------------------------------- absent keys: a no-op


def test_absent_or_partial_keys_change_nothing(config):
    assert not load_config({"NAVER_CLIENT_ID": "x"}, warn=False).naver_enabled
    assert not load_config({"NAVER_CLIENT_SECRET": "y"}, warn=False).naver_enabled
    assert load_config({"NAVER_CLIENT_ID": "x", "NAVER_CLIENT_SECRET": "y"}, warn=False).naver_enabled
    assert [f.source_id for f in default_fetchers(config)] == ["youtube", "wikipedia", "yonhap", "soompi",
                                                               "koreaboo"]
    out = run(config, None)
    assert out.exit_code == 0
    assert [s["id"] for s in out.payload["sources"]] == ["youtube", "wikipedia", "yonhap", "soompi", "koreaboo"]
    assert all(set(it["signals"]) == {"youtube", "accel", "new_release", "wiki", "news"}
               for it in out.payload["items"])
    assert "naver" not in out.summary


def test_stale_rows_are_ignored_once_keys_are_removed(config):
    cfg = naver_config(config)
    run(cfg, FakeNaver())
    out = run(config, None, now=NOW + timedelta(minutes=30))           # keys gone, rows still in the DB
    lyw = next((it for it in out.payload["items"] if it["id"] == "lim-young-woong"), None)
    assert lyw is None or ("search" not in lyw["signals"] and "searches" not in lyw["why"])


# ---------------------------------------------------------------------------- configured


def test_configured_run_end_to_end(config, caplog):
    caplog.set_level(logging.DEBUG)
    cfg = naver_config(config)
    naver = FakeNaver()
    out = run(cfg, naver)
    assert out.exit_code == 0
    reqs = naver.calls()
    assert len(reqs) == 17                                    # 82 artists, 5 keyword groups per request
    for r in reqs:
        assert r.url == httpx.URL(nd.HUB_URL) and r.method == "POST"
        assert r.headers["x-ncp-apigw-api-key-id"] == CID and r.headers["x-ncp-apigw-api-key"] == SECRET
        assert r.headers["content-type"] == "application/json"
        body = json.loads(r.content)
        assert (body["startDate"], body["endDate"], body["timeUnit"]) == ("2026-09-16", "2026-10-13", "date")
        assert 1 <= len(body["keywordGroups"]) <= 5
        assert all(1 <= len(g["keywords"]) <= 20 for g in body["keywordGroups"])
        assert CID not in str(r.url) and SECRET not in str(r.url)

    payload = out.payload
    jsonschema.validate(payload, schema())
    assert [s["id"] for s in payload["sources"]] == ["youtube", "wikipedia", "yonhap", "soompi", "koreaboo",
                                                     "naver"]
    src = payload["sources"][5]
    assert (src["name"], src["status"]) == ("Naver DataLab", "ok")
    assert src["detail"] == "82/82 current · day 2026-10-13 · 17 calls (17/300 today)"
    assert all("search" in it["signals"] for it in payload["items"])
    lyw = next(it for it in payload["items"] if it["id"] == "lim-young-woong")
    assert lyw["signals"]["search"] == 0.867 and lyw["korea_first"] is True
    assert lyw["why"] == "Korean searches 2.3x normal, EN quiet"
    assert "naver=ok" in out.summary

    # Storage: one derived row per artist; Naver's raw series is never written anywhere.
    conn = sqlite3.connect(cfg.db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(search_signal)")]
    row = conn.execute("SELECT day, z, ratio, baseline_n FROM search_signal WHERE artist_id='lim-young-woong'"
                       ).fetchone()
    n = conn.execute("SELECT COUNT(*) FROM search_signal").fetchone()[0]
    usage = conn.execute("SELECT api, day, units FROM api_usage").fetchall()
    conn.close()
    assert cols == ["artist_id", "target", "landed", "day", "z", "ratio", "baseline_n", "reason", "fetched_at"]
    assert row[0] == "2026-10-13" and row[1] == pytest.approx(5.2) and row[2] == pytest.approx(2.3) and n == 82
    assert usage == [("naver", "2026-10-14", 17)]
    dump, text = db_dump(cfg), cfg.trends_path.read_text(encoding="utf-8")
    assert str(RAW_SENTINEL) not in dump and str(RAW_SENTINEL) not in text
    for secret in (CID, SECRET):
        assert secret not in dump and secret not in text and secret not in caplog.text
        assert secret not in repr(cfg)

    # Daily data: the next run on the same KST day makes no request at all.
    naver2 = FakeNaver()
    out2 = run(cfg, naver2, now=NOW + timedelta(minutes=30))
    assert naver2.requests == []
    assert out2.payload["sources"][5]["detail"] == "82/82 current · day 2026-10-13 · 0 calls (17/300 today)"
    assert next(it for it in out2.payload["items"] if it["id"] == "lim-young-woong")["signals"]["search"] == 0.867


def test_not_landed_yet_repolls_every_two_hours(config):
    cfg = naver_config(config)
    out = run(cfg, FakeNaver(landed=False))
    assert out.payload["sources"][5]["detail"].startswith("0/82 current")
    later = FakeNaver()
    run(cfg, later, now=NOW + timedelta(hours=1))
    assert later.requests == []
    later = FakeNaver()
    run(cfg, later, now=NOW + timedelta(hours=2))
    assert len(later.requests) == 17


def test_auto_falls_back_to_developers_endpoint(config):
    cfg = naver_config(config)
    naver = FakeNaver(hub=401)
    out = run(cfg, naver)
    assert len(naver.calls("hub")) == 1 and len(naver.calls("developers")) == 17     # locked after success
    for r in naver.calls("developers"):
        assert r.url == httpx.URL(nd.DEVELOPERS_URL)
        assert r.headers["x-naver-client-id"] == CID and r.headers["x-naver-client-secret"] == SECRET
    assert out.results["naver"].status == "ok"
    assert out.results["naver"].detail.endswith(" · legacy endpoint")


@pytest.mark.parametrize(("mode", "naver", "calls"), [
    ("hub", FakeNaver(hub=401), 1),
    ("auto", FakeNaver(hub=401, developers=401), 2),
])
def test_rejected_credentials_fail_only_this_source(config, mode, naver, calls):
    out = run(naver_config(config, naver_endpoint=mode), naver)
    assert len(naver.requests) == calls
    assert out.results["naver"].status == "error" and "credentials rejected" in out.results["naver"].detail
    assert out.exit_code == 0 and out.status == "partial" and out.published
    assert out.results["youtube"].status == "ok"
    assert not any(SECRET in e or CID in e for e in out.results["naver"].errors)


def test_quota_429_books_the_day_and_stops(config):
    cfg = naver_config(config)
    naver = FakeNaver(hub=429)
    out = run(cfg, naver)
    assert len(naver.requests) == 3                       # one logical call (2 HTTP retries), then stop
    assert "quota exhausted" in out.results["naver"].detail and "(300/300 today)" in out.results["naver"].detail
    again = FakeNaver()
    run(cfg, again, now=NOW + timedelta(hours=3))
    assert again.requests == []                           # same KST day: the ledger says no


def test_daily_and_run_caps(config, monkeypatch):
    cfg = naver_config(config)
    st = Store(cfg.db_path)
    with st.tx():
        st.add_api_usage(ApiUsage("naver", date(2026, 10, 14), nd.DAY_CAP - 2))
    st.close()
    naver = FakeNaver()
    out = run(cfg, naver)
    assert len(naver.requests) == 2 and "daily cap" in out.results["naver"].detail
    assert out.results["naver"].status == "error"          # 10 of 82 artists → < 50%

    monkeypatch.setattr(nd, "RUN_CAP", 3)
    cfg2 = replace(cfg, db_path=cfg.db_path.with_name("other.db"))
    naver = FakeNaver()
    out = run(cfg2, naver)
    assert len(naver.requests) == 3 and "run cap" in out.results["naver"].detail


def test_unreachable_endpoint_is_isolated_and_not_hammered(config):
    naver = FakeNaver(exc=httpx.ReadTimeout)
    out = run(naver_config(config), naver)
    assert out.results["naver"].status == "error"
    assert len(naver.requests) == 3 * nd.MAX_CONSECUTIVE_FAILURES       # 3 attempts per call, then give up
    assert out.exit_code == 0 and out.published
    jsonschema.validate(json.loads(config.trends_path.read_text(encoding="utf-8")), schema())


def test_naver_alone_never_counts_as_a_successful_run(config):
    config.public_dir.mkdir(parents=True)
    config.trends_path.write_text('{"sentinel": true}\n', encoding="utf-8")
    dead = FakeNet(youtube=lambda r: httpx.Response(500), wiki=lambda r: httpx.Response(500),
                   news={h: (lambda r: httpx.Response(500)) for h in ("www.soompi.com", "www.koreaboo.com",
                                                                      "www.yna.co.kr")})
    out = run(naver_config(config), FakeNaver(), net=dead)
    assert out.results["naver"].status == "ok"
    assert out.exit_code == 2 and config.trends_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'


def test_sources_flag_can_disable_a_configured_naver(config):
    out = run(naver_config(config), None, sources=["youtube"])
    src = out.payload["sources"]
    assert len(src) == 6 and src[5]["id"] == "naver" and src[5]["status"] == "disabled"
    jsonschema.validate(out.payload, schema())


# ---------------------------------------------------------------------------- §5.9 worked example


def example_scores(search):
    scores = score_all(ARTISTS, VIDEOS, WIKI, MENTIONS, EX_NOW, FIRST_RUN, search)
    ranked = rank_scores(scores)
    apply_rank_changes(ranked, BASIS)
    for s in scores:
        s.why, s.extra["why_rule"] = template_why(s, EX_NOW)
    return {s.id: s for s in scores}


def seventeen_row():
    s = {TARGET - timedelta(days=14 - i): float(v) for i, v in enumerate(FLAT)}
    s[TARGET] = 46.0
    sp = search_spike(s, TARGET)
    return SearchRow("seventeen", TARGET, True, sp.day, sp.z, sp.ratio, sp.baseline_n, "", EX_NOW)


def test_worked_example_with_korean_search_spike():
    """§5.9: the §5.8 inputs plus a Naver spike for SEVENTEEN (z 5.2, 2.3x). Other acts have no search data."""
    sig = search_signal(seventeen_row(), EX_NOW)
    assert sig.s_search == pytest.approx(0.8667, abs=1e-4)
    res = example_scores({"seventeen": sig})
    s = res["seventeen"]
    assert s.ko_lead_search is True                      # z 5.2 ≥ 2 while z_en = 0.6 < 1
    assert s.ko_lead_news is False and s.ko_lead_wiki is False and s.korea_first is True
    assert s.raw_score == pytest.approx(20.2057 + 8.6667 + 6, abs=1e-3)
    assert s.score == 34.9
    assert (s.rank, s.change, s.change_amount) == (3, "down", 1)
    assert s.why == "Korean searches 2.3x normal, EN quiet" and s.extra["why_rule"] == "R3S"
    # Acts without search data keep their §5.8 values exactly.
    assert (res["kiss-of-life"].score, res["aespa"].score) == (67.0, 63.1)
    assert res["kiss-of-life"].why == '"Lucky Seven" views up 4.0x vs. normal decay'
    item = build_item(s, None, include_search=True)
    assert item["signals"]["search"] == 0.867 and item["korea_first"] is True


def test_configured_without_data_equals_keyless():
    plain = example_scores(None)
    empty = example_scores({})
    for aid in plain:
        assert empty[aid].raw_score == plain[aid].raw_score and empty[aid].why == plain[aid].why


def test_r5s_when_english_wikipedia_is_not_quiet():
    row = replace(seventeen_row(), artist_id="aespa")          # aespa: z_en 2.8 → no ko_lead_search
    res = example_scores({"aespa": search_signal(row, EX_NOW)})
    a = res["aespa"]
    assert a.ko_lead_search is False and a.why.startswith("New MV")          # R1 still wins for aespa
    a.calcs, a.top = [], None                                    # without YouTube reasons...
    a.candidates = []
    a.ko_lead_news = a.ko_lead_wiki = False
    a.wiki_ko = replace(a.wiki_ko, z=1.0)
    a.wiki_en = replace(a.wiki_en, z=1.5)
    assert template_why(a, EX_NOW) == ("Korean searches 2.3x normal", "R5S")   # ...the search line appears


def test_llm_never_sees_naver_values(tmp_path):
    res = example_scores({"seventeen": search_signal(seventeen_row(), EX_NOW)})
    ranked = sorted(res.values(), key=lambda s: s.rank)
    cfg = load_config({"SP_WHY_LLM": "1", "ANTHROPIC_API_KEY": "k"}, warn=False)
    sent = []

    def fake_call(config, payload):
        sent.append(payload)
        return "Fresh numbers line"

    st = Store(tmp_path / "db.sqlite")
    apply_llm_whys(ranked, {s.id: {"headlines_24h": 1, "search_ko_ratio": 2.3} for s in ranked}, cfg, st, EX_NOW,
                   call=fake_call)
    st.close()
    assert [p["artist"] for p in sent] == ["KISS OF LIFE", "aespa"]          # the R3S line is never rewritten
    assert res["seventeen"].why_source == "template"
    for p in sent:
        assert "search" not in json.dumps(p) and set(p["signals"]) == {"youtube", "accel", "new_release", "wiki",
                                                                         "news"}
    payload = llm_input(res["seventeen"], "x", {"search_ko_ratio": 2.3})
    assert payload["korea_first"] is False and "search_ko_ratio" not in payload["metrics"]
