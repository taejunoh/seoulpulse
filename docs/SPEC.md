# Seoul Rising — Product & Engineering Spec (v1)

> The product was called SeoulPulse until 2026-10-19. Only what users see changed (display name, wordmark, social
> hashtag, site and store text). Code identifiers below keep `seoulpulse` / `SeoulPulse` on purpose.

Korea-first K-pop trends for US fans: an iOS widget plus a small app. The data comes from a keyless Python pipeline that writes one static JSON file.
This spec is the contract that the backend and iOS agents build against. They work in parallel. **Normative artifacts:** this file,
`docs/trends.schema.json` (the JSON contract) and `docs/trends.example.json` (a valid 10-item payload). If these disagree, the schema file wins on structure and this file wins on semantics.

## 0. Non-negotiables

1. **Legal.** We store and show only headline text, a link, the source name, the publish time, and metrics we compute ourselves. Article bodies, RSS `<description>`, and `content:encoded` are discarded at parse time. They are never stored, translated, summarized, or sent to an LLM. Korean headlines are shown as-is, with no machine translation.
2. **Keyless.** Every feature works with no API keys. `YOUTUBE_API_KEY` (§3.3), `NAVER_CLIENT_ID` + `NAVER_CLIENT_SECRET` (§3.7) and `ANTHROPIC_API_KEY` only add optional enhancements, each off and harmless when its variables are absent: a keyless run is byte-identical to one built without that code. The LLM feature stays off unless `SP_WHY_LLM=1`. Secrets travel only in request headers and never reach logs, error strings, the DB or `trends.json` (asserted by tests).
3. **Deterministic.** Given the same DB state and the same `--now`, the pipeline produces byte-identical `trends.json`. Tests inject the clock and the HTTP transport. Tests never touch the live network.
4. **Times.** Everything is UTC internally and in JSON (`YYYY-MM-DDTHH:MM:SSZ`, no fractional seconds). KST (`Asia/Seoul`) is used only for display in social drafts.

## 1. Repo layout & commands

```
seoulpulse/
├── docs/            SPEC.md · trends.schema.json · trends.example.json
├── backend/
│   ├── pyproject.toml · uv.lock · .python-version (3.14)
│   ├── seoulpulse/
│   │   ├── cli.py         argparse entrypoint `seoulpulse`
│   │   ├── config.py      env → Config dataclass
│   │   ├── seed.py        load/validate artists.yaml, channels.yaml, channels.lock.json
│   │   ├── matching.py    normalize(), AliasMatcher, classify_video(), extract_song()
│   │   ├── http.py        HttpClient (UA, timeouts, retries, per-host pacing, conditional GET)
│   │   ├── fetchers/      base.py · youtube_rss.py · youtube_api.py · wikipedia.py · news_rss.py · naver_datalab.py
│   │   ├── store.py       SQLite schema/migrations + typed accessors
│   │   ├── scoring.py     pure functions (§5)   · why.py (§6) · publish.py (§7) · social.py (§9)
│   │   ├── resolve.py     handle→channel-id resolver · serve.py static server
│   │   └── pipeline.py    run orchestration (§3.6)
│   ├── data/        artists.yaml · channels.yaml · channels.lock.json (committed)
│   ├── tests/       test_*.py + fixtures/
│   └── var/         (gitignored) seoulpulse.db · run.lock
├── public/          (gitignored except .gitkeep) trends.json is written here
├── ios/             project.yml · SeoulPulse/ · SeoulPulseWidget/ · Shared/ · SeoulPulseTests/
└── .github/workflows/  pipeline.yml · backend-ci.yml
```

Dependencies: `httpx>=0.27`, `pyyaml>=6.0.2`, `defusedxml>=0.7.1`. Optional extra `llm = ["anthropic"]`. Dev group: `pytest>=8`, `jsonschema>=4.23`.
`requires-python = ">=3.12"`. Script entry: `seoulpulse = "seoulpulse.cli:main"`. Build backend: hatchling.

| Command (run in `backend/`) | Effect |
|---|---|
| `uv sync` | install |
| `uv run seoulpulse resolve-channels [--force]` | resolve `@handles` → `data/channels.lock.json` (§2.4) |
| `uv run seoulpulse validate-seed [--online]` | static checks; `--online` also verifies channel ids and canonical wiki titles |
| `uv run seoulpulse run [--now ISO] [--sources a,b] [--no-publish]` | one pipeline run → `../public/trends.json` |
| `uv run seoulpulse run --loop [--interval 1800]` | run forever; one failed run never stops the loop |
| `uv run seoulpulse serve [--port 8787] [--seed-example]` | serve `../public/` on 127.0.0.1. `--seed-example` copies `docs/trends.example.json` to `public/trends.json` if it is missing, so iOS can integrate before the pipeline exists |
| `uv run seoulpulse dev` | `serve` (thread) + `run --loop` |
| `uv run seoulpulse social [--input PATH] [--top 5] [--format x\|threads\|both]` | §9 |
| `uv run pytest` | all backend tests, offline |

iOS (in `ios/`): `xcodegen generate && xcodebuild -project SeoulPulse.xcodeproj -scheme SeoulPulse -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' -derivedDataPath build/DD build` (use `test` instead of `build` to run the tests).

Env config (`config.py`): `SP_DB_PATH` (default `backend/var/seoulpulse.db`), `SP_PUBLIC_DIR` (`<repo>/public`), `SP_INTERVAL_SEC` (1800),
`SP_CONTACT` (a contact URL or email for the User-Agent; warn if unset — Wikimedia throttles anonymous UAs), `SP_TOP_N` (20), `SP_RANK_COMPARE_HOURS` (6),
`YOUTUBE_API_KEY`, `SP_YOUTUBE_DAILY_UNITS` (`9000`, clamped to 0–10000), `NAVER_CLIENT_ID` + `NAVER_CLIENT_SECRET` (both required), `SP_NAVER_ENDPOINT` (`auto` | `hub` | `developers`; default `auto`),
`SP_WHY_LLM` (`0`), `ANTHROPIC_API_KEY`, `SP_LLM_MODEL` (`claude-haiku-5-5`). Paths resolve relative to `backend/`. `Config.__repr__` prints secrets as `<set>`.

## 2. Seed data

### 2.1 `backend/data/artists.yaml`
```yaml
version: 1
artists:
  - id: aespa                # slug ^[a-z0-9]+(-[a-z0-9]+)*$, unique, STABLE FOREVER (used in deep links)
    name: aespa              # official stylization, shown in UI
    name_ko: 에스파           # nullable
    aliases: [aespa, 에스파]  # EXPLICIT match terms; name/name_ko are NOT added implicitly
    youtube: ["@aespa"]      # artist-owned channels: @handle or UC… id; [] allowed
    wiki: {en: Aespa, ko: Aespa}   # CANONICAL titles (not redirects); either may be null
  - id: twice
    # …
    aliases: [{term: TWICE, case_sensitive: true}, 트와이스]   # alias = string | {term, case_sensitive}
```
Optional per artist: `search: [terms]`, explicit Naver DataLab keywords (§3.7). Same rules as aliases (deny-list, ≥2 Hangul syllables or ≥2 characters), at most 20. Without it the keywords are the artist's Hangul aliases.

**A starter roster is committed:** 82 acts in `backend/data/artists.yaml`. Of these, 64 have an own channel whose resolved title was checked against an alias, and the ids are in `channels.lock.json`. `channels.yaml` adds 13 label/broadcaster channels: HYBE LABELS, SMTOWN, JYP, 1theK, YG, THEBLACKLABEL, Cube (United CUBE), KQ, Mnet K-POP, SBSKPOP, MBCkpop, KBS Kpop, and STUDIO CHOOM. The wiki titles are canonical as of 2026-10-14. Edit the roster and fix it with the validator; never regenerate it blindly.

Findings from building the roster, which are now design rules:
- **Guessed handles often belong to unrelated channels.** Examples: `@GD` → "Geometry Dash", `@ENHYPEN_official` → "Gaming Aro", `@STARSHIPent` → "Kayo Preston". The resolver must verify the channel title (§2.4). Stone Music and Starship are currently unresolved, so their acts rely on 1theK, Mnet, and their own channels.
- **A matching title is not enough: squatters copy it.** Audit 2026-10-15 (public channel pages): 14 handles had resolved to channels with the right title but a handful of subscribers. Six had no uploads at all (`@ALLDAYPROJECT`, `@iznaofficial`, `@XdinaryHeroes`, `@KiiiKiiiofficial`, `@AHOF`, label `@wakeone`; the YouTube API answered `playlistNotFound` for them, §3.3), the others held a few stray videos that artist-owned attribution credited to the act (`@Apink`, `@TAEMIN`, `@KATSEYE`, `@QWER_Band`, `@ZICO`, `@UNIS` (a French channel), `@MnetKpop`, `@CUBEentertainment`). They were replaced by the official channels (`@ALLDAY_PROJECT`, `@izna_offcl`, Xdinary Heroes `UCO9GhtdQcCRf67GIdSQ0HNQ` (its handle URL redirects to a 404, so the id is pinned), `@KiiiKiii_starship`, `@AHOF_official`, `@officialapink`, `@taemin_xoalsox`, `@katseyeworld`, `@QWER_Band_official`, `@ZICOOFFICIAL`, `@unis_offcl`, `@Mnet`, `@theunitedcube`); WAKEONE has no label channel, so `@wakeone` was dropped and Kep1er got its own `@kep1er_offcl`. The resolver now rejects channels whose feed lists no uploads (§2.4). Check subscriber and video counts by hand when adding a handle.
- **Korean Wikipedia often uses Latin canonical titles** (`에스파` → `Aespa`, `스트레이 키즈` → `Stray_Kids`), and several Korean names (`더보이즈`, `레드벨벳`, `캣츠아이`, `백현`) are disambiguation pages. The pageviews API counts only the exact title, not redirects, so only canonical titles may be stored.
- **Ambiguous Korean words must not be aliases.** The deny-list, where the validator hard-fails, is `있지 아이들 정국 지수 빌리 비비 아홉 청하 방탄 진 뷔`. Examples: `있지` (ITZY; "there is"), `아이들` ("children"), `정국` ("political situation"), `지수` ("index"), `아홉` (AHOF; "nine"). Use a group-prefixed form such as `방탄소년단 정국`, or the English alias only.
- **English-word names need `case_sensitive: true`.** Examples: `TWICE`, `TREASURE`, `KISS OF LIFE`, `tripleS`, `Red Velvet`, `OH MY GIRL`, `Dreamcatcher`, `CLOSE YOUR EYES`. Otherwise "a national treasure" in a Soompi headline counts as a mention.
- **Yonhap mixes scripts.** It writes `NCT 위시` and `NCT 드림`, not `엔시티 위시`/`NCT WISH`, so those mixed forms are aliases too (found in the live feed 2026-10-15: two NCT WISH headlines matched nobody).

### 2.2 `backend/data/channels.yaml` (label & broadcaster channels)
```yaml
version: 1
channels:
  - {handle: "@HYBELABELS", name: HYBE LABELS, kind: label}         # kind: label | broadcaster
  - {handle: "@Mnet",       name: Mnet K-POP,  kind: broadcaster}
```
Artist channels come from `artists[].youtube` and are kind `artist` with `owner = artist.id`. A handle may be replaced with a literal `UC…` id.

### 2.3 Video → artist attribution and alias matching (`matching.py`)
- `normalize(s)`: apply NFKC, collapse whitespace, strip. Matching runs on this text.
- Alias pattern: build the left boundary from the alias's first character class and the right boundary from its last character class. Spaces inside an alias match `\s+`. Let `L` = ASCII letters/digits plus U+00C0–U+024F (regex `[A-Za-z0-9\u00C0-\u024F]`; covers accented Latin such as `É`).
  - **Latin/other end:** the neighbouring character must not be in `L`. Hangul may follow directly, so `BTS가`, `aespa는`, and `IVE의` all match.
  - **Hangul end:** the left neighbour must not be `[가-힣]`. On the right, the alias is followed by end of text, a non-Hangul character, or a particle followed by a non-Hangul character or the end of text. Particles: `은 는 이 가 을 를 의 와 과 도 로 으로 에 에게 에서 랑 이랑 만 까지 부터 처럼 보다 측`. So `뉴진스가` matches, while `한지민` (for `지민`) and `로제떡볶이` do not.
- Case: case-insensitive by default. Matching is case-sensitive when `case_sensitive: true` is set, or automatically for ASCII aliases that are all uppercase and ≤4 non-space characters (`IU`, `IVE`, `BTS`, `TWS`, `EXO`, `ITZY`, `SVT`). So `IVE` never matches `five` or `I've`. Use a mixed-case alias such as `Lisa` or `Jennie` when title-case headlines must match too.
- Validator rules: ASCII aliases need ≥2 characters. Hangul aliases need ≥2 syllables and must not be on the deny-list. An alias may not be shared by two artists.
- **Overlap resolution:** collect every match from every artist, sort by (length desc, start asc), and accept a match only if it does not overlap an accepted span. So `NCT DREAM` consumes its span and plain `NCT` cannot match there.
- **Headline mentions:** the set of accepted artist ids. One headline counts at most once per artist.
- **Video attribution:** a video on an `artist` channel goes to its owner. On a `label`/`broadcaster` channel, it goes to the artist of the accepted match with the smallest start offset. With no match, `artist_id = NULL` and the video is ignored in scoring. Attribution is recomputed every run for videos seen in the last 30 days, so alias fixes apply retroactively.
- **`classify_video(title, url, channel_kind)`**, first rule wins, regexes case-insensitive on the normalized title:
  1. `short`: url contains `/shorts/`. Excluded from scoring entirely. (Label feeds are about 70% Shorts.)
  1b. `other`: the acts' own variety series and concert live-viewing promos, `time to twice|going seventeen|to do x txt|skz code|run bts|bangtan bomb|dada-itzy|live viewing`. An episode or a cinema screening announced with "Teaser"/"Trailer" is not a comeback teaser (live 2026-10-14/15: `[TIME TO TWICE] … Crossing Teaser` produced "New teaser out: comeback incoming", and BTS got the teaser new-release term from `… LIVE VIEWING - Special Trailer`).
  2. `teaser`: `teaser|티저|trailer|트레일러|spoiler|스포일러|highlight medley|하이라이트 메들리|concept film|preview|sneak peek|coming soon|teasing (video|film|clip)` (HYBE titles comeback teasers "Teasing Video"). **On a `broadcaster` channel a rule-2 match is `other` instead:** a broadcaster's teaser/trailer/preview promotes its own programme (live 2026-10-15: KBS Kpop's `[Teaser] … 돌박이일 MEOVV Mood Sampler` made MEOVV's #1 line "New teaser out: comeback incoming"; festival line-up teasers). Label channels such as 1theK keep their comeback teasers. `channel_kind` is the channel's seed kind (§2.2); re-classification passes it too, so the rule applies retroactively.
  3. `other`: `behind|비하인드|making|메이킹|reaction|리액션|vlog|브이로그|interview|인터뷰|unboxing`
  4. `mv`: `\bm/?v\b|music video|뮤직비디오|official video`
  5. `perf`: `performance|퍼포먼스|dance practice|안무|choreography|special video|visualizer|lyric video|official audio|live clip|relay dance|릴레이 댄스|fancam|직캠`
  6. `show`: `엠카운트다운|m countdown|인기가요|inkigayo|music bank|뮤직뱅크|음악중심|music core|show champion|쇼챔피언|the show|더쇼|studio choom|it's live|방송|ep\.\s?\d+`
  6b. `perf`: `\bstage\b|스테이지`. A stage that is not a Korean music-show upload (a concert "Stage Video", "Stage Cam @ iHeartRadio", an overseas TV "stage @ CDTV") is a performance. It runs after rule 6, so "Comeback Stage | M COUNTDOWN" stays `show`, and "backstage" never matches.
  7. `other`: everything else
- **`extract_song(title)`**: return the inner text of the first quoted segment, chosen by the earliest opening character among `‘’ “” 「」 『』 《》 <>`, ASCII `"…"`, and ASCII `'…'`. Labels mix quote styles (`‘DREAMSCAPE'`), so `‘` may close with `’` or `'`, and `“` with `”` or `"`. For ASCII `'…'`, use `'([^']+?)'(?![A-Za-z])`. If there is no quoted segment, take the text after ` _ ` (the 1theK form `[MV] IU(아이유) _ Love wins all`), else the text after ` - `, then strip any trailing `(…)` or `[…]`. Otherwise return `None`. Truncate to 24 characters with `…`.

### 2.4 Channel resolution & validation
`resolve-channels`, for each handle missing from the lock file (or all handles with `--force`):
1. GET `https://www.youtube.com/<handle>` with a browser UA, `Accept-Language: en-US,en;q=0.8`, and cookie `CONSENT=YES+1`.
2. Extract the id with the first regex that matches: `feeds/videos\.xml\?channel_id=(UC[\w-]{22})`, then `<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})`, then `"externalId":"(UC[\w-]{22})"`. Extract the title from `og:title`.
3. **Title check.** For `artist` channels, `normalize(title).casefold()` must contain one alias (casefolded) of the owner. For label channels, it must contain the yaml `name`. Otherwise the result is `MISMATCH` and the channel is not written. For generic names the check is necessary but not sufficient: two different "Kickflip" channels both pass, so KickFlip is left unresolved. The lock file stores `title` for human review.
4. Verify that `https://www.youtube.com/feeds/videos.xml?channel_id=<id>` returns 200 **and lists at least one upload**. An empty feed is `EMPTY` and is not written: an official artist or label channel always has uploads, a squatter with a copied title often has none (§2.1).
5. Write `{handle: {channel_id, title, resolved_at}}`. Pace at 1 request/s. Print a table of `OK / MISMATCH / EMPTY / NOT_FOUND`. Exit 3 if any handle is unresolved. Unresolved artists still get coverage through label channels.

`validate-seed` runs offline checks on the schema, slugs, the alias rules in §2.3, owners, and channel ids being present in the yaml or the lock file. `--online` also fetches every resolved channel's feed (HTTP 200 with at least one upload) and runs these per wiki title, one request per second each:
- `https://{lang}.wikipedia.org/w/api.php?action=query&format=json&redirects=1&titles=<t>`: a missing page is an error, a redirect is an error that names the canonical title, and a disambiguation page (`pageprops`) is an error.
- A pageviews request for the last 3 days must return 200.

## 3. Collection

### 3.1 HTTP policy (`http.py`, a single shared `HttpClient`)
- httpx sync client with `follow_redirects=True`, timeouts `connect=5s read=10s write=10s pool=5s`, and a response cap of 5 MB.
- UA: `SeoulPulse/0.1 (+{SP_CONTACT})`. Only the resolver uses a browser UA.
- Retries apply to connect/read timeouts and to 429/500/502/503/504: at most 2 retries, sleeping 1 s then 4 s (each with 0–0.5 s jitter). `Retry-After` is honored up to 30 s. Other 4xx responses are not retried. `sleep` and `clock` are injectable.
- Per-host pacing, sequential within a host: `www.youtube.com` 0.5 s, `wikimedia.org` 0.4 s (≈150/min, under the 200/min limit), `*.wikipedia.org` 1.0 s, news hosts 1.0 s, `googleapis.com` 0.2 s, other hosts (Naver DataLab) 1.0 s.
- `get()` for everything; `post_json()` only for Naver DataLab (a read-only POST). It shares pacing, retries, the byte cap and the deadline, and never follows redirects.
- Conditional GET: ETag and Last-Modified per URL go in the `http_cache` table. A 304 means "no new items" and counts as success.

### 3.2 Fetcher contract (`fetchers/base.py`)
```python
@dataclass(frozen=True)
class FetchResult:
    source_id: str            # "youtube" | "wikipedia" | "yonhap" | "soompi" | "koreaboo" | "naver" (key-gated)
    status: str               # "ok" | "degraded" | "error" | "disabled"
    started_at: datetime; finished_at: datetime
    units_ok: int; units_total: int
    detail: str               # ≤80 chars, human readable; becomes sources[].detail
    errors: list[str]         # ≤20 entries, logged + stored in source_runs.error

@dataclass
class FetchOutput:
    result: FetchResult
    videos: list[VideoObs] = field(default_factory=list)      # §3.3
    wiki: list[WikiDay] = field(default_factory=list)         # §3.4
    headlines: list[Headline] = field(default_factory=list)   # §3.5
    api_snapshots, http_cache                                 # YouTube API snapshots (§3.3), conditional-GET validators
    search: list[SearchRow]; api_usage: list[ApiUsage]        # §3.7 derived rows; quota units spent (§3.3, §3.7)

class Fetcher(Protocol):
    source_id: str
    def fetch(self, ctx: RunContext) -> FetchOutput: ...     # MUST NOT raise
```
`RunContext` holds `now`, `http`, `seed`, `config`, a read-only `store` (for example `wiki_latest_day(lang, title)`), and `log`. Fetchers do no DB writes. The pipeline persists their output in the main thread.
Parsers are pure, module-level functions such as `parse_youtube_feed(xml: bytes, observed_at) -> list[VideoObs]`. Unit tests target these.
Status rule for multi-unit sources: `ok` when all units succeeded, `degraded` when ≥50% succeeded, `error` when <50% succeeded. Single-feed sources are `ok` or `error`.
`disabled` applies when the source is excluded through `--sources`.

### 3.3 YouTube (`source_id = "youtube"`)
- **RSS** (always): one GET per channel (≈90 total): `https://www.youtube.com/feeds/videos.xml?channel_id=<UC…>`. Parse with defusedxml using namespaces `atom=http://www.w3.org/2005/Atom`, `yt=http://www.youtube.com/xml/schemas/2015`, `media=http://search.yahoo.com/mrss/`.
  Per `atom:entry`, read: `yt:videoId`, the entry-level `yt:channelId` (the feed-level one lacks the `UC` prefix, so ignore it), `title`, `link[@rel=alternate]/@href` (to detect Shorts), `published`, `media:group/media:community/media:statistics/@views` (int; if missing, skip the snapshot but keep the video), and `media:group/media:thumbnail/@url`.
  `VideoObs(video_id, channel_id, title, url, published_at, views|None, observed_at=time the response arrived)`. Each feed holds only the latest 15 uploads. On high-volume label channels that is about 1–4 days, so a video drops out of tracking when it leaves the feed (see the carry-forward in §5.2).
- **Data API** (only if `YOUTUBE_API_KEY` is set; `fetchers/youtube_api.py`). RSS stays the base and the fallback; the API only enriches it. It fixes four RSS weaknesses: only the latest 15 uploads (label channels: 1–4 days, ~70% Shorts), coarse view counts cached for ~15 min, videos dropping out of tracking when they leave the feed, and Shorts detection for videos RSS never showed. After the RSS pass:
  1. **Discovery** — `GET https://www.googleapis.com/youtube/v3/playlistItems?part=snippet,contentDetails&playlistId=UU<id[2:]>&maxResults=50` (the uploads playlist; 1 unit for the latest 50 uploads, so uploads 16–50 become visible). Channels: those whose RSS fetch failed this run (all of them, in this same run), those with no stored video yet (cold start backfill), then a rotating share: channel `c` is read when `crc32(c) % 12 == floor(epoch(now)/1800) % 12`, i.e. every channel about every 6 h. RSS failures are ordered by rotation distance from the current slot, `(crc32(c) % 12 − slot) mod 12`, so if the discovery cap ever cuts them (a long global outage on a larger roster) a different part is cut each run and no channel waits more than 12 runs. Keep uploads with `contentDetails.videoPublishedAt` in `[now−30d, now]` that are not stored yet, not in this run's RSS, attributed (§2.3), and without `#shorts` in the title. Private/deleted entries (no `videoPublishedAt`) are skipped. Discovery may spend at most half of the run's units.
     **When a playlist is not found.** A `404 playlistNotFound` for the derived `UU…` id (or a stored playlist) is answered by one `GET …/channels?part=contentDetails,statistics&id=<≤50 channel ids>` (1 unit) and the result is stored per channel in `yt_uploads` (§4) as a discovery `method`:
     - not returned, or `statistics.videoCount = 0` → `empty`: there is nothing to discover. Logged as a **warning** (the seed most likely points at the wrong channel, §2.1), skipped for 7 days, never searched. Live 2026-10-15: the six "RSS-only" channels of the day before were exactly this, squatter channels with no uploads behind wrong seed handles; neither UULF nor search could have found anything there.
     - `contentDetails.relatedPlaylists.uploads` differs from the derived id → read it straight away and keep using it (`resolved`).
     - otherwise the long-form playlist `UULF<id[2:]>` (the channel's Videos tab: no Shorts, no live streams; 1 unit) → `uulf` if it answers. UULF is a filtered view of the uploads list, so it rarely exists when `UU…` does not; it is a cheap probe, made once per 7 days at most.
     - neither playlist readable → `search` (API-blind) for 7 days, then probed again.
     All of this adds nothing to `errors` and never degrades the source by itself; every call is booked like any other. Rows written before methods existed are probed again.
     **API-blind channels: `search.list`.** A `search` channel is discovered with `GET …/search?part=snippet&channelId=<id>&type=video&order=date&publishedAfter=<now−30d>&maxResults=50` (**100 units**), only while its RSS fails this run (RSS still lists its latest 15 otherwise), at most once per 6 h per channel (`searched_at`, least recently searched first, stamped even when the call fails) and at most one search per run. It runs after playlist discovery and only with headroom: with `R` = runs left in the Pacific quota day at one per 20 min and `U` = this run's regular units (discovery so far + the statistics calls it still needs, plus one batch for what the search may find), it needs `used + units + 100 + stats + R·U ≤ SP_YOUTUBE_DAILY_UNITS` and `U ≤ RUN_CAP`; otherwise `detail` flags `search deferred`. Snippet titles are HTML-unescaped; live/upcoming broadcasts are skipped (found by a later search once they are VODs). Found videos take the playlist path from here: the same filters (new, in window, attributed, no `#shorts`), then statistics (b) and the Shorts rule (3). A searched channel counts as covered for status.
  2. **Statistics** — `GET …/videos?part=statistics,contentDetails&id=<≤50 ids>` (1 unit per call), in this priority order, each newest first: (a) tracked videos without an RSS view count this run (`kind ≠ short`, attributed, `published_at ≥ now−30d`; this is what keeps videos alive after they leave the 15-item feed), (b) newly discovered uploads, (c) RSS videos. For (c) the exact API count **replaces** the RSS count for that run (one `source='api'` snapshot instead of a coarse `rss` one). Missing ids (deleted/private) get no snapshot.
  3. **Shorts for discovered uploads.** RSS's `/shorts/` link stays authoritative whenever RSS saw the video. For uploads only the API saw there is no "is a Short" field, and duration alone is ambiguous (Shorts may run 180 s; horizontal teasers are often ≤60 s). Rule: `#shorts` in the title and duration ≤180 s → Short; duration ≤60 s and the title is not a teaser (§2.3 rule 2) → Short; zero/unknown duration (premieres, live) → skip and rediscover later; otherwise a regular video. The decision is stored as the video URL (`/shorts/<id>` or `/watch?v=<id>`), so `classify_video()` stays the single classifier and re-classification stays retroactive.
- **Quota guard (normative).** The free quota is 10,000 units per Google Cloud project per day, reset at midnight Pacific time. Every call costs 1 unit (`search.list`: 100) and is booked before it is sent (failed calls are billed too). Caps: `RUN_CAP = 200` units per run for regular calls, plus at most one 100-unit search (so ≤300), and `SP_YOUTUBE_DAILY_UNITS` (default 9,000) per Pacific day, tracked in the `api_usage` ledger (§4). The ledger persists in the Actions cache. The schedule is `7,22,37,52 * * * *` because GitHub skips many scheduled runs on new repos; if the ledger were ever lost (cache eviction) and runs fired at the full 15-minute rate, Google would answer `quotaExceeded`, which stops calls for the rest of the day as described below, so the worst case is RSS-only data until midnight Pacific. When the budget is reached the remaining work is skipped and RSS values stand (`detail` flags `discovery cap`, `budget cap`, `search deferred` or `daily budget used`). A `403 quotaExceeded`/`dailyLimitExceeded` answer stops all calls and books the rest of the day's budget (`quota exhausted`); `401` or a key-level `400/403` (`API_KEY_INVALID`, `accessNotConfigured`, referrer/IP blocks …) stops the run's calls (`key rejected`).

  | per run (82 acts, ~77 resolved channels) | units | per day (48 runs) |
  |---|---|---|
  | cold start: discovery 77 + statistics ~1–2k ids / 50 | ~80–120 | once |
  | steady state: discovery ~7 (rotation) + statistics ~1–2.5k tracked ids / 50 | ~30–60 | ~1,500–2,900 |
  | global RSS outage: discovery 77 (every channel, every run) + statistics ~0.6–2.5k ids / 50 | ~90–130 | ~4,300–6,200 |
  | API-blind channel: 1 search per 6 h while its RSS fails | +100 | +400 per channel |
  | hard caps | ≤200 (+100 search) | ≤9,000 (of 10,000) |
  During the 2026-10-14 RSS outage a run cost 84 units (71 playlists + 13 statistics calls). The worst row still leaves ≥2,800 units a day, so a full outage needs no discovery throttling; the search headroom rule keeps searches from ever eating into it.
- **Secrets.** The key is sent only as the `X-Goog-Api-Key` header, never as a `key=` URL parameter, so it cannot appear in URLs, logged exceptions, `source_runs.error`, the DB or `trends.json` (`redact()` remains as a backstop).
- **Status.** Units are channels (§3.2). A channel counts as fetched when its RSS feed answered, or when its RSS feed failed but the API read its uploads playlist (or searched it) this run (its new uploads were discovered and its tracked videos got exact counts). So an RSS outage that the API bridges reads `ok`/`degraded`, not `error`, and `last_success_at` keeps moving; channels the API cannot read this run (`empty`, a `search` channel between searches, discovery cap, deadline) still count as failed. API problems appear in `errors` and `detail`, e.g. `RSS 77/77 channels · API 41 units (2310/9000 today)`, or during an RSS outage `RSS 0/77 + API 77 channels · 190 units (1210/9000 today) · 1 channel via search`; `N channels empty` counts channels with no public videos. Without a key the status is the RSS status, unchanged. (Live 2026-10-14 23:50 UTC on: YouTube's feed server answered 404 for every channel; the API kept all data fresh while the source read `error` for hours.)
- **Terms.** YouTube API Services policies allow storing API data for at most 30 days without refreshing it: `source='api'` snapshots are deleted after 30 days, and videos only the API ever saw are deleted 30 days after their last refresh (§4).

### 3.4 Wikipedia pageviews (`source_id = "wikipedia"`)
`GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{quote(title.replace(' ','_'), safe='')}/daily/{start}00/{end}00`
`end` is yesterday (UTC). With no stored data, `start = end − 29 d` (backfill, so wiki has no cold start). Otherwise `start = latest_stored − 2 d`. Skip the article if `latest_stored == yesterday`. After the first run, a cron run therefore makes almost no requests.
Response `items[].{timestamp:"YYYYMMDD00", views}` becomes `WikiDay(lang, title, day, views)`. A 404 is a unit error ("no data/not found"). Yesterday's data usually lands between 01:00 and 06:00 UTC.
**Freshness.** The expected day is yesterday (UTC), the newest day Wikimedia normally has published. A day `D` is usable while it trails the expected day by at most `WIKI_MAX_LAG_D = 1` day (so `D ≥ today − 2 d`): that covers the hours before publication and a short delay. When even the newest day held for any article (stored or fetched this run) is older, nothing is usable: the scorer ignores it (§5.3) and the source is `degraded` although every request answered, with `detail` `no Wikimedia data after 2026-10-15 (upstream delay) · 146 articles` and the same fact first in `errors`. "(upstream delay)" is said only when Wikimedia answered this run; if the requests failed too, the unit status (usually `error`) stands and `detail` reads `no Wikimedia data after D · ok/total articles`. One day behind stays `ok` with the usual `0/146 articles current (day D)`. Live 2026-10-18: Wikimedia published no daily data after 2026-10-15 for 3+ days (per-article 404 for 10-16 and 10-17) while the source still read `ok · 0/146 articles current (day 2026-10-15)`.

### 3.5 Headlines (`soompi`, `koreaboo`: lang `en`; `yonhap`: lang `ko`)
URLs: `https://www.soompi.com/feed`, `https://www.koreaboo.com/feed/`, `https://www.yna.co.kr/rss/entertainment.xml`. All three are RSS 2.0. Volume is about 60, 10, and 120 items respectively. Yonhap covers all entertainment, not only K-pop. The alias matcher filters it.
Per `item`, read `title` (CDATA, `html.unescape`, collapse whitespace, ≤300 chars), `link`, `guid` (falling back to `link`), and `pubDate` (RFC 822 → UTC). `description` and `content:encoded` are **never read into any object**.
`headline_id = sha1(f"{source_id}|{guid}").hexdigest()[:16]`. `published_at = min(pubDate, first_seen_at)`. A pubDate that is missing, unparseable, or more than 10 minutes in the future is replaced by `first_seen_at`.
Source weights and language: yonhap 1.5 / ko, soompi 1.0 / en, koreaboo 0.8 / en.

### 3.6 Run sequence & failure isolation (`pipeline.py`)
1. Take a non-blocking `fcntl.flock` on `var/run.lock`. If another run holds it, log and exit 0.
2. Load config and seed. An invalid seed exits 1. Open the DB (WAL mode, `busy_timeout=5000`) and migrate. Insert a `runs` row. `now` is the run start, truncated to seconds, unless `--now` is given.
3. Run the 5 fetchers (plus `naver` when configured, §3.7) concurrently, each wrapped in `try/except Exception`. An escaped exception becomes `status=error` with the exception string. Overall deadline: 8 minutes. After that, unfinished fetchers count as `error`.
4. Persist each `FetchOutput` in its own transaction, so one failure cannot roll back another. Insert `source_runs`.
5. Re-attribute and re-classify videos (§2.3). Score all artists (§5). Rank, compute changes, generate whys (§5.7, §6).
6. Build the payload (§7). Write `public/trends.json.tmp` then `os.replace` it. Insert into `published`. Exception: if **every** keyless source is `error` this run and `public/trends.json` exists, do not publish and exit 2 (a key-gated source succeeding alone never counts; its failure still makes the run `partial`). If there are zero eligible items and a file exists, keep the old file. If no file exists, publish with `items: []`.
7. Retention (§4). Release the lock. Log one summary line: `run 412 ok items=20 youtube=ok wikipedia=ok yonhap=ok soompi=ok koreaboo=error 74.2s`.
Exit codes: 0 for ok or partial, 1 for config or crash, 2 when all sources failed. In `--loop` mode, catch everything and sleep `interval − elapsed` (minimum 60 s).

### 3.7 Naver DataLab search trend (`source_id = "naver"`, key-gated; `fetchers/naver_datalab.py`)
Runs only when **both** `NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET` are set. Without them nothing changes: no fetcher, no `naver` source entry, no `signals.search`, no score term, and stale rows from an earlier configuration are ignored.

**Status of the API (researched 2026-10-14).**
- Naver moved its Search API, Search Trend (DataLab) and Shopping Insight APIs to **NAVER API HUB** on Naver Cloud Platform (launched 2026-06-25). New applications at developers.naver.com closed on **2026-07-31**; keys applied for by 2026-07-30 keep working until **2027-06-30**. Search Trend is migrated and available to new users through the HUB (it is not among the shopping/book/academic search APIs that were shut down on 2026-07-31).
- Endpoints, identical request/response bodies:
  - HUB: `POST https://naverapihub.apigw.ntruss.com/search-trend/v1/search`, headers `X-NCP-APIGW-API-KEY-ID` and `X-NCP-APIGW-API-KEY` (the Client ID/Secret of a HUB *Application*, not an NCP IAM key). Quota: 50,000 calls/month per app (temporarily free, metered later), 50 requests/s per key, `429` when exceeded.
  - developers.naver.com (legacy keys only): `POST https://openapi.naver.com/v1/datalab/search`, headers `X-Naver-Client-Id` and `X-Naver-Client-Secret`. Quota: 1,000 calls/day. Legacy and HUB credentials are not interchangeable.
  - `SP_NAVER_ENDPOINT=auto` tries the HUB and, if it answers 401/403, the legacy endpoint once; the first endpoint that answers 200 is used for the rest of the run. `hub` or `developers` pins one.
- Body: `startDate`, `endDate` (`yyyy-mm-dd`, from 2016-01-01), `timeUnit` (`date|week|month`), `keywordGroups` (≤5 × `{groupName, keywords ≤20}`); optional `device`, `gender`, `ages` (unused). Response: `results[].{title, keywords, data[].{period, ratio}}`, where `ratio` is relative: the largest value in the whole response is 100.
- **Terms.** The September 2026 amendment (effective 2026-09-07 at developers.naver.com, 2026-09-20 on the HUB, as reported) is a special clause for the **Search API** ("네이버 검색 API 서비스"): use only to show Naver search results; no input into AI (training, evaluation, answer generation); no copying/storing/caching of search data including derivatives beyond narrow exceptions (server history ≤21 days, on-device ≤24 h); attribution; no ads on result pages; no resale. Secondary sources read DataLab as outside that clause, and the clause's purpose ("to provide Naver search results") does not fit trend statistics, but the primary text was not available to verify. We therefore comply with the strictest reading anyway (below).

**Our use.** Relative daily search interest for each artist's Korean keywords, used as one numeric input to the ranking. Never shown as Naver content (no Naver series, chart or result list is published), never sent to an LLM, never resold.

**Fetch.** `target` = yesterday in KST; request `[target−27 d, target]`, `timeUnit=date`, one keyword group per artist (`groupName` = artist id; keywords = `Artist.search_keywords()`, §2.1), 5 groups per request in roster order → 17 requests for 82 acts. Data is daily, so an artist is requested only when its stored row is for an older target, or for this target but without data for `target` yet (`landed=false`) and fetched ≥1 h 55 min ago. In practice ~17 calls a day plus a few morning re-polls. Per response, each group's series goes through `search_spike()` (§5.3) in memory.
**Caps.** ≤20 calls per run, ≤300 per KST day (ledger `api_usage`); after 3 consecutive failed requests the run stops asking. `48 × 20 = 960/day` stays under the legacy 1,000/day and `300 × 31 = 9,300/month` under the HUB's 50,000 even with retries. A 429 books the rest of the day (`quota exhausted`); credentials rejected by every endpoint → `credentials rejected`.
**Status/detail.** Multi-unit over artists with keywords (§3.2): `82/82 current · day 2026-10-13 · 17 calls (17/300 today)`, plus ` · legacy endpoint` when the developers.naver.com endpoint answered. `current` counts artists whose row has data for `target` (KST yesterday); `day` is the newest KST day with data among the rows (D, the day the scores use), not the target. Before DataLab has published KST yesterday (the KST morning), `0/82 current · day <target − 1>` is normal and still scored (D ≥ target − 2 d, §5.3). (Until 2026-10-18 `day` printed the target, so the first morning with Naver enabled read `0/82 current · day 2026-10-18` while the data ended on 10-17.) When the newest D of every row is older than `target − SEARCH_STALE_D`, Naver contributes nothing: the source is `degraded` with `no DataLab data after D (upstream delay) · N calls (…)`, "(upstream delay)" only if DataLab answered for this target.
**Storage (compliance by design).** Naver's ratio series is never written: only our derived values, one row per artist in `search_signal` (latest `day` D, `z`, `ratio = x_D/μ`, `baseline_n`, `reason`, fetch bookkeeping), deleted after 7 days. Naver-derived numbers elsewhere (`artist_scores.components_json`, the `published` payloads) are kept ≤14 and ≤21 days. All of this is under the strictest 21-day reading. (Copies of the DB inside GitHub Actions cache entries can outlive this by up to the cache's 7-day eviction window.)

## 4. Storage — SQLite (`backend/var/seoulpulse.db`)
```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);          -- schema_version=1, first_run_at
CREATE TABLE runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed')), published INTEGER NOT NULL DEFAULT 0,
  cold_start INTEGER NOT NULL DEFAULT 0);
CREATE TABLE source_runs (run_id INTEGER NOT NULL REFERENCES runs, source_id TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT NOT NULL, units_ok INTEGER, units_total INTEGER, detail TEXT, error TEXT,
  PRIMARY KEY (run_id, source_id));
CREATE TABLE videos (video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, artist_id TEXT, title TEXT NOT NULL,
  url TEXT NOT NULL, kind TEXT NOT NULL, published_at TEXT NOT NULL, thumbnail_url TEXT,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
CREATE INDEX videos_artist ON videos(artist_id, published_at);
CREATE TABLE video_snapshots (video_id TEXT NOT NULL REFERENCES videos, observed_at TEXT NOT NULL,
  views INTEGER NOT NULL, source TEXT NOT NULL CHECK (source IN ('rss','api')), PRIMARY KEY (video_id, observed_at));
CREATE TABLE wiki_daily (lang TEXT NOT NULL, title TEXT NOT NULL, day TEXT NOT NULL, views INTEGER NOT NULL,
  fetched_at TEXT NOT NULL, PRIMARY KEY (lang, title, day));             -- upsert (late revisions)
CREATE TABLE headlines (headline_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, lang TEXT NOT NULL,
  title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL);
CREATE INDEX headlines_pub ON headlines(published_at);
CREATE TABLE artist_scores (run_id INTEGER NOT NULL, artist_id TEXT NOT NULL, score REAL NOT NULL, rank INTEGER,
  korea_first INTEGER NOT NULL, components_json TEXT NOT NULL, PRIMARY KEY (run_id, artist_id));
CREATE TABLE published (run_id INTEGER PRIMARY KEY, generated_at TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE http_cache (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, fetched_at TEXT NOT NULL);
CREATE TABLE why_cache (key TEXT PRIMARY KEY, why TEXT NOT NULL, created_at TEXT NOT NULL);   -- LLM only
CREATE TABLE api_usage (api TEXT NOT NULL, day TEXT NOT NULL, units INTEGER NOT NULL,         -- quota ledger:
  PRIMARY KEY (api, day));                       -- youtube (Pacific day, units) · naver (KST day, calls)
CREATE TABLE search_signal (artist_id TEXT PRIMARY KEY, target TEXT NOT NULL, landed INTEGER NOT NULL,
  day TEXT, z REAL, ratio REAL, baseline_n INTEGER NOT NULL, reason TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE TABLE yt_uploads (channel_id TEXT PRIMARY KEY, playlist_id TEXT, unavailable_until TEXT,  -- §3.3 discovery
  checked_at TEXT NOT NULL, method TEXT, searched_at TEXT);  -- method: resolved | uulf | search | empty (7 d)
```
The new tables are created with `IF NOT EXISTS`, so existing databases migrate in place (`schema_version` stays 1). Columns added later (`yt_uploads.method`, `yt_uploads.searched_at`) are added by `ALTER TABLE … ADD COLUMN` when missing, so a cached DB keeps its rows.
Headline mentions are not stored. They are recomputed each run from `headlines.title`, which is cheap and keeps alias fixes retroactive. `components_json` keeps every intermediate value from §5 for debugging.
Retention, applied every run: delete snapshots older than 35 days. Delete videos when `last_seen_at` and `published_at` are both older than 35 days. Delete headlines published more than 30 days ago. Delete `wiki_daily` rows more than 60 days old, `artist_scores` and `runs`/`source_runs` older than 14 days, and keep only the newest 500 `published` rows, none older than 21 days.
Keyed-API data: `source='api'` snapshots older than 30 days; videos that have only API snapshots and were last refreshed >30 days ago (YouTube API policy); `search_signal` rows fetched >7 days ago; `api_usage` days older than 40 days; `yt_uploads` rows without a playlist once their `unavailable_until` has passed (or is null), so the channel is probed again. A keyless DB has no such rows, so these rules never touch it.

## 5. Scoring (`scoring.py`, pure functions of DB rows + `now`)

### 5.1 Constants
| name | value | meaning |
|---|---|---|
| `P`, `T0`, `REF` | 0.8, 2 h, 24 h | view-rate decay model `vph(a) = c·(a+T0)^−P`, normalized to age 24 h |
| `MIN_GAP`, `VEL_WIN` | 25 min, 6 h | velocity pair: oldest snapshot in `[t_L−6h, t_L−25min]` |
| `KIND_W` | mv 1.0 · perf 0.6 · teaser 0.35 · show 0.25 · other 0.15 | short = excluded |
| `TRACK_DAYS`, `STALE_H`, `DROP_H`, `STALE_HALF` | 30 d, 6 h, 72 h, 24 h | eligibility and carry-forward |
| `SRC_W` | yonhap 1.5 · soompi 1.0 · koreaboo 0.8 | headline weights |
| `NEWS_HALF`, `NEWS_WIN`, `K_BASE` | 12 h, 48 h, 0.676 | `K_BASE` = expected decayed sum per 1 headline/day (`∫₀⁴⁸2^(−a/12)da / 24`) |
| `KO_LEAD_H` | 6 h | Korean lead needed for korea_first |
| weights | yt 0.35 · accel 0.15 · new 0.15 · wiki 0.20 · news 0.15 · kf bonus +6 | final combination |
| `W_SEARCH`, `SEARCH_Z_FULL` | 0.10 (additive), 6 | Korean search term, only when `naver` is configured (§5.3) |
| `SEARCH_SIGMA_FLOOR`, `SEARCH_MIN_MU`, `SEARCH_STALE_D`, `SEARCH_KF_Z` | 0.25, 0.01, 2 d, 2.0 | search spike guards and the Korea-first threshold |
| `WIKI_MAX_LAG_D`, `EN_QUIET_Z` | 1 d, 1.0 | a wiki day may trail yesterday (UTC) by 1 day (§3.4); English Wikipedia is "quiet" below z 1.0 on fresh data (§5.4) |

### 5.2 Per-video intensity
For each eligible video, all of the following must hold:
- `kind ≠ short` and `artist_id` is not null
- `published_at ≥ now − 30d`
- it has at least 1 snapshot, and its latest snapshot is ≤72 h old

Define `t_L, V_L` as the latest snapshot and `age_L = max(0.25, hours(t_L − published_at))`.
- Lifetime fit (the cold-start estimate): `c_life = V_L·(1−P) / ((age_L+T0)^(1−P) − T0^(1−P))`.
- Recent pair: take the oldest snapshot `(t_O, V_O)` with `t_L−6h ≤ t_O ≤ t_L−25min`. It is valid only if `V_L > V_O`. The guard exists because RSS stats can freeze between polls, and a zero delta must not read as zero velocity.
  - **One count source per pair.** Snapshots carry their source (`rss` = the feed's cached count, `api` = the exact Data API count, §4), and a pair never mixes them: across sources it would measure the gap between the two counters, not views. The pair is taken within the latest snapshot's source. If that source has no valid pair, as on the first run after the API takes over or when the API falls back to RSS (quota, key), the other source's newest snapshot `(t_P, V_P)` in `[t_L−6h, t_L]` ends the pair instead, with its own oldest `t_O ≥ t_L−6h`, `t_O ≤ t_P−25min` and `V_P > V_O`; below, read `t_L, V_L` as `t_P, V_P` for vph and age_mid only (c_life, staleness and the displayed views keep the latest snapshot). With one source (keyless, §5.8) this is exactly the single-series rule.
  - `vph = (V_L−V_O)/hours(t_L−t_O)`
  - `age_mid = hours((t_O+t_L)/2 − published_at)`
  - `c_recent = vph·(age_mid+T0)^P`
- `c = c_recent` if a valid pair exists, else `c_life`. Intensity `I = c·(REF+T0)^−P`, in "views/hour at age 24 h". This makes a 3-hour-old MV and a 9-day-old MV comparable.
- Acceleration `r = clamp(c_recent/c_life, 0.25, 4.0)`. It is computed only if a valid pair exists and `V_L ≥ 1000`, otherwise `None`. A value of `r > 1` means the video earns more now than its own history predicts: a resurgence (역주행), viral challenge, or news bump.
- Displayed velocity: `vph` if a pair exists. Otherwise `c_life·(age_L+T0)^−P` with `estimated=true`.
- Carry-forward: if `stale = hours(now−t_L) > 6`, then `I ← I·2^(−stale/24)` and `r ← None`. This happens when a video has left the RSS window or its channel fetch failed.
- Contribution `e = KIND_W[kind]·I`.

### 5.3 Per-artist signals
- **YouTube:** sort `e` descending. `yt_raw = e₁ + 0.5·e₂ + 0.25·e₃` (missing terms are 0). `S_yt = clamp((log10(1+yt_raw) − 3)/2.5, 0, 1)`. Roughly, 1K/h maps to 0 and 316K/h to 1. `top_video = argmax e`.
- **Accel:** `S_accel = clamp(log2(r_top)/2, −0.5, 1)` if `r_top` exists, else 0. The gate `g = clamp(S_yt/0.4, 0, 1)` keeps tiny videos from earning accel points.
- **New release:** `S_new = max(0, max over mv of (1 − age_now/48), max over teaser of 0.4·(1 − age_now/72))`, where `age_now = hours(now − published_at)`, terms are floored at 0, and the result is 0 if there are none.
- **Wikipedia,** per lang L ∈ {ko, en}:
  - `D` = the latest stored day for the article. It must be ≥ today−2 d (at most `WIKI_MAX_LAG_D` behind yesterday, §3.4), else the signal is missing (`stale`): a stale day contributes nothing to `wiki_z`, fires no wiki why-line (R3, R5) and is not published as a metric (§7).
  - Baseline: days `D−14 … D−1`, with at least 7 present, else missing.
  - `μ` = mean and `σ` = population std over the present baseline days. `σ_eff = max(σ, 0.25·μ, 10)`.
  - `z_L = clamp((x_D − μ)/σ_eff, −3, 10)`. If `z_L > 0`, apply the volume gate `z_L ← z_L·min(1, x_D/200)`. `ratio_L = x_D / max(μ, 1)`.
  - Combined: `wiki_z = 0.6·max(z_ko,0) + 0.4·max(z_en,0)` (missing counts as 0), and `S_wiki = clamp(wiki_z/8, 0, 1)`. Korea is weighted higher on purpose.
- **News:**
  - Mentions use `age = max(0, hours(now − published_at))`. `raw = Σ_{age≤48} SRC_W·2^(−age/12)`.
  - Baseline: `hist_days = min(7, days(now − 24h − meta.first_run_at))`. If `hist_days < 2`, then `base_daily = 0`. Otherwise `base_daily` = Σ `SRC_W` over mentions published in `[now−24h−hist_days·24h, now−24h)`, divided by `hist_days`.
  - `excess = max(0, raw − K_BASE·base_daily)` and `S_news = clamp(log2(1+excess)/4, 0, 1)`. Acts that are always in the news must exceed their own baseline.
  - `headlines_24h` / `headlines_ko_24h` are unweighted counts of mentions with age ≤24 h.
- **Korean search** (only when `naver` is configured, §3.7):
  - At fetch time, per keyword group, `search_spike(series, target)`: `D` = latest day in the series, which must be ≥ `target − 2 d` (else missing, "stale"). Baseline: days `D−14 … D−1`, at least 7 present (else "short baseline"). `μ` = mean, `σ` = population std; `μ < 0.01` → missing ("low volume": Naver rounds ratios, so tiny groups are noise). `σ_eff = max(σ, 0.25·μ)` (so a 2x day scores at most z = 4). `z_s = clamp((x_D − μ)/σ_eff, −3, 10)`, `ratio_s = x_D/μ`. Naver rescales each response to max 100, but z and the ratio are scale-free, so values from different batches and days compare. Only `D`, `z_s`, `ratio_s` are stored.
  - At scoring time the stored row is missing if it has no z or `D < target − 2 d` (target = KST yesterday at `now`). `S_search = clamp(max(z_s, 0)/6, 0, 1)`; 0 when missing or not configured.

### 5.4 Korea-first flag
- `ko_lead_news`: let K be the Korean mentions with age ≤48 h **and `published_at ≥ en_from + 24h`**, where `en_from` is the `published_at` of the oldest stored English headline (`now` when there is none). At least one of them must have age ≤24 h. Let `t_ko = min(published_at over K)` and E be the English mentions with `published_at ≥ t_ko − 24h`. The flag is true when E is empty or `min(E.published_at) ≥ t_ko + 6h`.
  - Why `en_from`: "no English mention since `t_ko − 24h`" is evidence only if we held English headlines over that span. The feeds' backlogs differ (Yonhap ~3 days, Soompi ~2, Koreaboo ~half a day), so after a cold start an old Yonhap item "led" English coverage that was never collected. Live 2026-10-14: BTS showed `Korean press led English by 20h` (+6, rank 1) for 10+ hours, from a Yonhap backlog item 38 h older than the first run, while Soompi had covered the actual story 9 h *before* Yonhap. Once the DB holds 3+ days of English headlines the rule is unchanged. Pure scoring calls without `en_from` (the §5.8 example) are unrestricted.
- **English Wikipedia is quiet** (`en_wiki_quiet`) when the EN signal is fresh and `z_en < 1.0`, or when the act has no English article (`wiki.en: null` in the seed: a fact, not a data gap). EN that is `stale`, has `no data` or a `short baseline` is **unknown, not quiet**, and never supports a Korean lead.
- `ko_lead_wiki`: `z_ko ≥ 2.0` (after the gate) and English Wikipedia is quiet.
- `ko_lead_search` (only when configured): `z_s ≥ 2.0` and English Wikipedia is quiet (a Korean search spike while English attention is low).
  - Why "unknown is not quiet": until 2026-10-18 a missing `z_en` counted as quiet. When Wikimedia stopped publishing (§3.4) every EN signal went `stale`, so every Naver spike became Korea-first: live #1 KATSEYE and #3 ILLIT read `Korean searches 1.9x normal, EN quiet` (+6 each) with no English data at all. Now such an act keeps its search points and reads R5S (`Korean searches 1.9x normal`) without the bonus. With fresh data nothing changes (§5.8, §5.9).
- `korea_first = ko_lead_news or ko_lead_wiki or ko_lead_search`. `S_kf = 1` if true, else 0.

### 5.5 Combination, eligibility, ranking
```
score = clamp( 100·( 0.35·S_yt + 0.15·S_accel·g + 0.15·S_new·(0.5+0.5·g) + 0.20·S_wiki + 0.15·S_news [+ 0.10·S_search] ) + 6·S_kf , 0, 100 )
```
The bracketed term exists only when the `naver` source is configured; otherwise `S_search = 0` and the sum is bit-identical to the keyless formula (§5.8 must keep reproducing exactly). It is additive (a Korean search spike can only raise a score) rather than a re-weighting, so configuring Naver never lowers any act and acts without search data keep their keyless score.
Round to 1 decimal (Python `round`). An artist is eligible if `score ≥ 3.0` and `max(S_yt, S_wiki, S_news, S_search) > 0`. Sort by `(−score, −yt_raw, id)`. Ranks run 1..n, and the top `SP_TOP_N` (20) are published.

### 5.6 Cold start
The first run has no history. It still ranks sensibly because:
- every video uses `c_life` (the cumulative fit from views and age)
- wiki backfills 30 days on the first fetch
- headlines use the feeds' backlog (about 2 days, real pubDates) with `base_daily = 0`
- accel is neutral (`r=None`)

`cold_start = true` when no eligible video had a valid recent pair this run. The app then shows a "calibrating" banner. With a 30-minute cadence, the second run already has pairs.

### 5.7 Rank change
- Basis: the newest `published` row with `generated_at ≤ now − SP_RANK_COMPARE_HOURS` (6 h). If there is none, use the oldest `published` row. If the table is empty, there is no basis. Comparing against the immediately previous 30-minute run would make the arrows twitch, and the 6-hour window reads as "movement today". Setting it to 0 means "previous run".
- `prev_rank` = the item's rank in the basis payload's `items`, or null.
  - `change` is `new` (prev null), `up` (prev > rank), `down` (prev < rank), or `same`.
  - `change_amount = |prev − rank|`, which is 0 for new and same.
- `rank_change_basis_at` = the basis `generated_at`, or null.

### 5.8 Worked example (normative — `test_scoring_worked_example.py` must reproduce every value: S terms ±0.001, scores exactly)
`now = 2026-10-14T09:00:00Z`. Three artists. Wiki baselines are the 14 values listed below, where D = 2026-10-13. Basis ranks: aespa 4, seventeen 2, kiss-of-life absent.

| | **kiss-of-life** | **aespa** | **seventeen** |
|---|---|---|---|
| videos (all `artist_id` set) | MV, pub 10-05 09:00 (216 h); snaps 03:00→1,500,000, 09:00→1,620,000 | MV pub 10-13 15:00 (18 h); snaps 06:00→4,200,000, 08:30→4,520,000, 09:00→4,580,000 · teaser pub 10-10 09:00; snaps 06:00→2,950,000, 09:00→2,962,000 | perf, pub 10-12 09:00 (48 h); single snap 09:00→900,000 |
| pair / vph | 03:00 (6 h) → 20,000/h | 06:00 (3 h) → 126,666.7/h · teaser 4,000/h | none → est. 7,583.8/h |
| c_life / c_recent | 181,323.3 / 1,468,861.2 | 1,363,367.4 / 1,307,373.6 · teaser I=11,421.0 | 173,406.1 / — |
| r · I · e | 8.10→**4.0** · 108,393.2 · 108,393.2 | 0.9589 · 96,476.4 · 96,476.4 (teaser e=3,997.3) | — · 12,796.3 · 7,677.8 |
| yt_raw → S_yt · S_accel · g | 108,393.2 → 0.8140 · 1.0000 · 1 | 98,475.1 → 0.7973 · −0.0303 · 1 | 7,677.8 → 0.3541 · 0 · 0.8853 |
| S_new | 0 | 1−18/48 = 0.6250 | 0 |
| wiki ko baseline → x_D | 400,380,420,410,390,400,405,395,400,410,390,400,400,400 → 1300: μ400 σ_eff100 z=9.0 ratio 3.25 | 3100,2900,3000,3200,2800,3050,2950,3100,3000,2900,3150,2850,3000,3000 → 9000: μ3000 σ_eff750 z=8.0 ratio 3.00 | 5000×14 → 5200: σ_eff1250 z=0.16 ratio 1.04 |
| wiki en baseline → x_D | 1500,1450,1550,1500,1480,1520,1500,1500,1490,1510,1500,1500,1500,1500 → 1560: z=0.16 ratio 1.04 | 8000,7600,8400,8100,7900,8200,7800,8000,8300,7700,8100,7900,8000,8000 → 13600: σ_eff2000 z=2.8 ratio 1.70 | 20000,21000,19000,20500,19500,20000,20000,21000,19000,20000,20000,20500,19500,20000 → 23000: σ_eff5000 z=0.6 ratio 1.15 |
| wiki_z → S_wiki | 5.464 → 0.6830 | 5.92 → 0.7400 | 0.336 → 0.0420 |
| mentions (source, age h) · base_daily | (yonhap 4) · 0.2 | (yonhap 5), (yonhap 20), (soompi 3) · 1.0 | soompi 2,6,10,30 · koreaboo 8,12 · yonhap 1 · 3.0 |
| raw → excess → S_news | 1.1906 → 1.0554 → 0.2598 | 2.4371 → 1.7611 → 0.3663 | 4.6558 → 2.6278 → 0.4648 |
| ko_lead_news / ko_lead_wiki | true (no EN) / true | true (EN 17 h later) / false (z_en 2.8) | false (EN 12 h *before* KO) / false |
| **score** | 61.05+6 = **67.0** | 57.12+6 = **63.1** | **20.2** |
| rank · change · why (§6) | 1 · new · `"Lucky Seven" views up 4.0x vs. normal decay` | 2 · up 2 · `New MV "Neon Tide" at 127K views/hr` | 3 · down 1 · `6 headlines in 24h (1 Korean)` |

Takeaway: a Korea-first resurgence edges out a big fresh release. That is the product thesis, working as intended.
(Song titles: KISS OF LIFE `KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV`, aespa `aespa 에스파 'Neon Tide' MV`.)

### 5.9 Worked example: Korean search spike (normative — `test_naver.py` must reproduce it)
The §5.8 inputs, with the `naver` source configured and one search row: SEVENTEEN, target and D = 2026-10-13.
- Series (one keyword group, Naver ratio units): baseline D−14…D−1 = `20,19,21,20,20,21,19,20,20,20,21,19,20,20`, x_D = 46.
- `μ = 20.0`, `σ = 0.6547`, `σ_eff = max(0.6547, 0.25·20) = 5.0`, `z_s = (46 − 20)/5 = 5.2`, `ratio_s = 2.30`. Multiplying the whole series by any constant (Naver's rescaling) gives the same z and ratio.
- `S_search = 5.2/6 = 0.8667` → `+100·0.10·0.8667 = +8.67`.
- `ko_lead_search`: z_s 5.2 ≥ 2 and z_en = 0.6 < 1 → true. SEVENTEEN had no Korea-first lead before (`ko_lead_news` false, `ko_lead_wiki` false), so it gains `S_kf = 1` → `+6`.
- Score: `20.2057 + 8.6667 + 6 = 34.87 → 34.9`. Rank stays 3 (67.0, 63.1, 34.9), change `down 1`. Why: rule R3S → `Korean searches 2.3x normal, EN quiet`. `signals.search = 0.867`.
- KISS OF LIFE and aespa have no search row (missing → 0): 67.0 and 63.1 with the same whys as §5.8. Configured with no rows at all reproduces §5.8 exactly.

## 6. "Why" line (`why.py`)
Use the first rule whose condition holds. Render the text. If it exceeds **60 code points** with the song truncated to 24 characters, use that rule's no-song form. If that is still too long, fall through to the next rule.
Number format `fmt(n)`:
- `<1000` → integer
- `<10K` → `7.6K` (one decimal, trailing `.0` dropped)
- `<1M` → `127K`
- otherwise → `1.2M` (one decimal, `.0` dropped)

Ratios and r use `{:.1f}x`. Headline text is **never** included.

| # | rule | condition | template (no-song form) |
|---|---|---|---|
| R1 | NEW_MV | an eligible `mv` with `age_now ≤ 48h` (newest one) | `New MV "{song}" at {fmt(vph)} views/hr` · (`New MV out, {fmt(vph)} views/hr`) |
| R2 | ACCEL | `r_top ≥ 2.0` and `S_yt ≥ 0.2` | `"{song}" views up {r}x vs. normal decay` · (`MV views up {r}x vs. normal decay`; use `Video` instead of `MV` if top kind ≠ mv) |
| R3 | KO_WIKI | `ko_lead_wiki` (EN known quiet, §5.4) | `Korean Wikipedia views {ratio_ko}x normal, EN quiet` |
| R3S | KO_SEARCH | `ko_lead_search` (Naver configured; EN known quiet, §5.4) | `Korean searches {ratio_s}x normal, EN quiet` |
| R4 | KO_NEWS | `ko_lead_news` | no EN mention: `{n_ko} Korean headline(s), no English coverage yet` (`1 Korean headline, …` / `2 Korean headlines, …`); else `Korean press led English by {h}h` |
| R5 | WIKI | `max(z_ko, z_en) ≥ 2.0` | `{Korean\|English} Wikipedia views {ratio}x the 2-week avg` (higher z; tie → Korean) |
| R5S | SEARCH | `z_s ≥ 2.0` (Naver configured) | `Korean searches {ratio_s}x normal` (also the line for a search spike while EN is unknown) |
| R6 | NEWS | `headlines_24h ≥ 2` | `{n} headlines in 24h ({n_ko} Korean)`; drop the parenthetical if `n_ko = 0` |
| R7 | TEASER | `teaser` with `age_now ≤ 72h` | `New teaser "{song}": comeback incoming` · (`New teaser out: comeback incoming`) |
| R8 | MOMENTUM | `top_video` exists; wording by `r_top` | `r_top ≥ 1.5`: R2's text, `"{song}" views up {r}x vs. normal decay` · (`MV`/`Video views up {r}x vs. normal decay`) — `0.7 ≤ r_top < 1.5` or no `r_top`: `"{song}" steady at {fmt(vph)} views/hr` · (`Steady YouTube pull: {fmt(vph)} views/hr`) — `r_top < 0.7`: `"{song}" still pulling {fmt(vph)} views/hr` · (`Still pulling {fmt(vph)} views/hr on YouTube`) |
| R9 | FALLBACK | always | `Buzzing across Korean sources` |

Here `n_ko` is the count of Korean mentions in 24 h, and `h` is the integer hours between `t_ko` and the first English mention.
R8 picks its wording from the top video's acceleration so it never calls a sharply slowing video "steady" (live 2026-10-15: Rosé, ALLDAY PROJECT and KickFlip read "steady" with acceleration at its floor). R2 keeps priority for `r_top ≥ 2.0` with `S_yt ≥ 0.2`; R8's "views up" form covers `1.5 ≤ r_top < 2.0` and small videos (`S_yt < 0.2`) that R2 skips. "Still pulling" states the current rate without judging the act. A missing `r_top` (no pair yet, `V_L < 1000`, or carried forward) keeps "steady", as before.
**Optional LLM.** It runs only when `SP_WHY_LLM=1`, `ANTHROPIC_API_KEY` is set, and `anthropic` is importable. It covers only the top 10 items, with at most 10 calls per run. Results are cached in `why_cache` for 6 h under the key `sha256(input JSON)`.
- Input: the item's metrics and signals, plus the template line. Headline titles are not sent. **Naver-derived values are never sent:** the five keyless signals only, `korea_first` is the keyless lead (`ko_lead_news or ko_lead_wiki`), any `search*` metric is stripped, and items whose template rule is R3S/R5S keep their template line without an LLM call.
- Call: `anthropic.Anthropic(timeout=20, max_retries=1).beta.messages.create(model=SP_LLM_MODEL, max_tokens=1024, output_config={"effort":"low"}, betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks":"default"}, system=<"Write ONE English line ≤60 chars explaining why this K-pop act is trending, using only the given numbers. No quotes from headlines, no hashtags, no emoji.">, messages=[{"role":"user","content":<json>}])`
- Accept the result only if `stop_reason != "refusal"` and the text is one line of 1–60 characters with no Hangul. Then set `why_source="llm"`. Any exception or rejection falls back to the template.

## 7. JSON contract — `GET {base}/trends.json`
Normative files: `docs/trends.schema.json` (draft 2020-12; producer-strict, `additionalProperties:false`) and `docs/trends.example.json` (valid, 10 items).
UTF-8 JSON with `ensure_ascii=False`, 2-space indent, keys in the order shown. All URLs are ASCII (percent-encoded). Timestamps use `YYYY-MM-DDTHH:MM:SSZ`.

| field | type | notes |
|---|---|---|
| `schema_version` | int | `1`. A bump means a breaking change. Additive fields don't bump it |
| `generated_at` | ts | run `now` |
| `next_update_hint` | ts | `generated_at + SP_INTERVAL_SEC` |
| `rank_change_basis_at` | ts \| null | §5.7 |
| `cold_start` | bool | §5.6 |
| `items` | Item[0..20] | sorted by `rank`; ranks contiguous from 1 |
| `sources` | Source[5..6] | fixed order: youtube, wikipedia, yonhap, soompi, koreaboo; then `naver` ("Naver DataLab") only when its credentials are configured (it is the attribution for the search signal) |

**Item:**
- `id` (slug; deep-link key), `rank` (int), `prev_rank` (int|null), `change` (`new|up|down|same`), `change_amount` (int ≥0)
- `name`, `name_ko` (str|null), `score` (number 0–100, 1 dp), `korea_first` (bool), `why` (1–60 chars), `why_source` (`template|llm`)
- `image_url` (`https://i.ytimg.com/vi/{top_video.video_id}/hqdefault.jpg` | null)
- `metrics`:
  - `yt_views_per_hour` (int|null), `yt_views_per_hour_estimated` (bool), `yt_accel` (number 2 dp | null)
  - `wiki_day` (`YYYY-MM-DD` | null; the latest D used), `wiki_ko_views` (int|null), `wiki_ko_ratio` (2 dp | null), `wiki_en_views`, `wiki_en_ratio`. Only fresh days are used (§5.3): while Wikimedia is behind, all five are null, `signals.wiki` is 0, and the `wikipedia` source (`degraded`) names the last published day in its `detail`.
  - `headlines_24h` (int), `headlines_ko_24h` (int)
- `signals` (3 dp): `youtube` (0–1), `accel` (−0.5–1), `new_release`, `wiki`, `news` (0–1). These are S_yt, S_accel, S_new, S_wiki, S_news. Optional `search` (0–1, S_search) only when `naver` is configured. Naver's ratio is not published as a metric (data minimization); it appears only inside an R3S/R5S `why`.
- `top_video` (null | `{video_id, title, url: https://www.youtube.com/watch?v=…, kind: mv|perf|teaser|show|other, published_at, views (latest), channel_name}`)
- `headlines`: up to 5 mentions from the last 48 h, newest first, each `{title, url, source_id: yonhap|soompi|koreaboo, source_name, lang: ko|en, published_at}`
- `links`:
  - `youtube_channel`: the artist's first resolved own channel, `https://www.youtube.com/channel/UC…`, or null
  - `wikipedia_en` / `wikipedia_ko`: `https://{lang}.wikipedia.org/wiki/` + `quote(title.replace(' ','_'), safe="()_,'")`, or null

**Source:** `{id, name, status: ok|degraded|error|disabled, last_success_at: ts|null (finished_at of the latest ok/degraded run), detail: ≤80 chars}`.

Excerpt from `docs/trends.example.json`. The sample data uses `[Sample]` headlines linking to outlet home pages, `SAMPLE…` video ids, and real channel ids. Its headline lists are abbreviated; real output lists up to 5:
```json
{ "schema_version": 1, "generated_at": "2026-10-14T09:00:00Z", "next_update_hint": "2026-10-14T09:30:00Z",
  "rank_change_basis_at": "2026-10-14T03:00:00Z", "cold_start": false,
  "items": [ { "id": "kiss-of-life", "rank": 1, "prev_rank": null, "change": "new", "change_amount": 0,
      "name": "KISS OF LIFE", "name_ko": "키스오브라이프", "score": 67.0, "korea_first": true,
      "why": "\"Lucky Seven\" views up 4.0x vs. normal decay", "why_source": "template",
      "image_url": "https://i.ytimg.com/vi/SAMPLE0001A/hqdefault.jpg",
      "metrics": { "yt_views_per_hour": 20000, "yt_views_per_hour_estimated": false, "yt_accel": 4.0,
        "wiki_day": "2026-10-13", "wiki_ko_views": 1300, "wiki_ko_ratio": 3.25, "wiki_en_views": 1560,
        "wiki_en_ratio": 1.04, "headlines_24h": 1, "headlines_ko_24h": 1 },
      "signals": { "youtube": 0.814, "accel": 1.0, "new_release": 0.0, "wiki": 0.683, "news": 0.26 },
      "top_video": { "video_id": "SAMPLE0001A", "title": "KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV",
        "url": "https://www.youtube.com/watch?v=SAMPLE0001A", "kind": "mv",
        "published_at": "2026-10-05T09:00:00Z", "views": 1620000, "channel_name": "KISS OF LIFE" },
      "headlines": [ { "title": "[Sample] 키스오브라이프 '럭키 세븐' 역주행…음원차트 재진입",
        "url": "https://www.yna.co.kr/entertainment/all", "source_id": "yonhap", "source_name": "Yonhap",
        "lang": "ko", "published_at": "2026-10-14T05:00:00Z" } ],
      "links": { "youtube_channel": "https://www.youtube.com/channel/UCvEEeBssb4XxIfWWIB8IjMw",
        "wikipedia_en": "https://en.wikipedia.org/wiki/Kiss_of_Life_(group)", "wikipedia_ko": null } },
    "… 9 more (ranks 2–10) …" ],
  "sources": [
    { "id": "youtube", "name": "YouTube", "status": "ok", "last_success_at": "2026-10-14T09:00:00Z", "detail": "RSS 86/87 channels" },
    { "id": "wikipedia", "name": "Wikipedia", "status": "ok", "last_success_at": "2026-10-14T09:00:00Z", "detail": "168/170 articles current (day 2026-10-13)" },
    { "id": "yonhap", "name": "Yonhap", "status": "ok", "last_success_at": "2026-10-14T09:00:00Z", "detail": "120 items, 4 new" },
    { "id": "soompi", "name": "Soompi", "status": "ok", "last_success_at": "2026-10-14T09:00:00Z", "detail": "60 items, 2 new" },
    { "id": "koreaboo", "name": "Koreaboo", "status": "error", "last_success_at": "2026-10-14T08:30:00Z", "detail": "timeout after 2 retries" } ] }
```
**Contract change for the key-gated sources (backward compatible, no `schema_version` bump).** The schema now allows `sources` to hold 5 or 6 entries (`naver` added to the id enum) and an optional `signals.search`. Every payload valid before stays valid; keyless producers emit exactly the old shape. The iOS decoder already tolerates both: `sources` decodes as a lossy array rendered with `ForEach`/`FlowLayout` (any count), unknown keys are ignored, and `TrendSignals` reads only its five keys. Chosen over a separate top-level status object because the app then shows Naver's attribution and status with no app change.

**Consumer rules (iOS):**
- Ignore unknown keys. Render `sources` in the given order without assuming a count.
- Map an unknown `change` to `.same`, an unknown `kind` to `.other`, and an unknown `status` to `.degraded`.
- Treat `schema_version > 1` as "try to decode, and fall back on failure".
- Decode `wiki_day` as a String, not a Date.

## 8. iOS app + widget

### 8.1 Targets & IDs (iOS 18.0 minimum; Swift language mode 5; SwiftUI)
| target | type | bundle id | sources |
|---|---|---|---|
| `SeoulPulse` | application | `com.taejunoh.seoulpulse` | `SeoulPulse/`, `Shared/` |
| `SeoulPulseWidget` | app-extension (WidgetKit) | `com.taejunoh.seoulpulse.widget` | `SeoulPulseWidget/`, `Shared/` |
| `SeoulPulseTests` | bundle.unit-test (host: app) | `com.taejunoh.seoulpulse.tests` | `SeoulPulseTests/` |

- App Group `group.com.taejunoh.seoulpulse`, set in both entitlements. URL scheme `seoulpulse`, plus `seoulrising` as an alias (registered and parsed; links the app builds always use `seoulpulse://`).
- Signing: `CODE_SIGN_STYLE: Automatic`, `DEVELOPMENT_TEAM: 3BMF4LM6TM`, and `CODE_SIGN_IDENTITY[sdk=iphonesimulator*]: "-"`. The simulator honors App Group entitlements under ad-hoc signing.
- If `containerURL(forSecurityApplicationGroupIdentifier:)` returns nil, fall back to each process's own Caches directory.

`ios/project.yml` (normative shape):
```yaml
name: SeoulPulse
options: {bundleIdPrefix: com.taejunoh, deploymentTarget: {iOS: "18.0"}, createIntermediateGroups: true}
settings: {base: {SWIFT_VERSION: "5.0", MARKETING_VERSION: "1.0.0", CURRENT_PROJECT_VERSION: "1",
  CODE_SIGN_STYLE: Automatic, DEVELOPMENT_TEAM: 3BMF4LM6TM, "CODE_SIGN_IDENTITY[sdk=iphonesimulator*]": "-"}}
targets:
  SeoulPulse:
    type: application
    platform: iOS
    sources: [SeoulPulse, Shared]          # Shared/Resources/sample-trends.json becomes a resource
    dependencies: [{target: SeoulPulseWidget, embed: true}]
    entitlements: {path: SeoulPulse/SeoulPulse.entitlements,
                   properties: {com.apple.security.application-groups: [group.com.taejunoh.seoulpulse]}}
    info:
      path: SeoulPulse/Info.plist
      properties:
        CFBundleDisplayName: Seoul Rising
        SPTrendsURL: http://localhost:8787/trends.json
        SPAppGroupID: group.com.taejunoh.seoulpulse
        CFBundleURLTypes: [{CFBundleURLName: com.taejunoh.seoulpulse, CFBundleURLSchemes: [seoulpulse, seoulrising]}]
        NSAppTransportSecurity: {NSAllowsLocalNetworking: true}
        UILaunchScreen: {}
        UIUserInterfaceStyle: Dark
    settings: {base: {PRODUCT_BUNDLE_IDENTIFIER: com.taejunoh.seoulpulse}}
  SeoulPulseWidget:
    type: app-extension
    platform: iOS
    sources: [SeoulPulseWidget, Shared]
    entitlements: {path: SeoulPulseWidget/SeoulPulseWidget.entitlements,
                   properties: {com.apple.security.application-groups: [group.com.taejunoh.seoulpulse]}}
    info:
      path: SeoulPulseWidget/Info.plist
      properties: {CFBundleDisplayName: Seoul Rising, NSExtension: {NSExtensionPointIdentifier: com.apple.widgetkit-extension},
                   SPTrendsURL: http://localhost:8787/trends.json, SPAppGroupID: group.com.taejunoh.seoulpulse,
                   NSAppTransportSecurity: {NSAllowsLocalNetworking: true}}
    settings: {base: {PRODUCT_BUNDLE_IDENTIFIER: com.taejunoh.seoulpulse.widget}}
  SeoulPulseTests:
    type: bundle.unit-test
    platform: iOS
    sources: [SeoulPulseTests]
    dependencies: [{target: SeoulPulse}]
    settings: {base: {PRODUCT_BUNDLE_IDENTIFIER: com.taejunoh.seoulpulse.tests}}
schemes:
  SeoulPulse:
    build: {targets: {SeoulPulse: all, SeoulPulseWidget: all}}
    test: {targets: [SeoulPulseTests], environmentVariables: {SP_SNAPSHOT_DIR: "$(SRCROOT)/build/snapshots"}}
```

### 8.2 Shared code (`ios/Shared/`)
- `Models/Trends.swift`: Codable structs decoded with `JSONDecoder` (`.convertFromSnakeCase` keys, `.iso8601` dates).
  - Structs: `TrendsSnapshot`, `TrendItem`, `TrendMetrics`, `TrendSignals`, `TopVideo`, `Headline`, `TrendLinks`, `SourceStatus`.
  - Enums with an unknown fallback: `RankChange`, `VideoKind`, `SourceState`.
  - Swift property names follow the convention: `headlines24h`, `headlinesKo24h`, `ytViewsPerHour`, `wikiKoRatio`, `youtubeChannel`, and so on.
- `Data/AppConfig.swift`: `trendsURL` resolves in this order:
  1. App Group `UserDefaults(suiteName:)` key `trendsURLOverride`, when non-empty and a valid http(s) URL with a host
  2. Info.plist `SPTrendsURL`
  3. the constant `http://localhost:8787/trends.json`
- `Data/TrendStore.swift`: `func load(network: Bool = true, timeout: TimeInterval = 8) async -> LoadedTrends`. It never throws.
  - Chain: network (ephemeral `URLSession`, `reloadIgnoringLocalCacheData`, HTTP 200 required, then decode) → App Group cache `trends-cache.json` → bundled `sample-trends.json`.
  - A successful network load writes the cache atomically together with `fetchedAt`.
  - `LoadedTrends { snapshot, origin: .network | .cache | .sample, fetchedAt: Date? }`.
- `Design/Theme.swift` (tokens, §8.6), `Design/Format.swift` (`compact(_:)` with the same thresholds as §6, plus relative time).
- `WidgetViews/`: `SmallTrendsView`, `MediumTrendsView`, `LargeTrendsView`, `RectangularTrendView`, `TrendRow`, `RankChangeBadge`, `KRFirstBadge`. These are pure views over `TrendsSnapshot`, so the app gallery and the snapshot tests can render them too.
- `Resources/sample-trends.json`: a **byte-identical copy** of `docs/trends.example.json`.

### 8.3 Widget (`SeoulPulseWidget/`)
- A single `StaticConfiguration`, kind `"SeoulPulseTrendsWidget"`, display name "Korea-first K-pop", description "What's spiking in Korea right now." Families: `.systemSmall`, `.systemMedium`, `.systemLarge`, `.accessoryRectangular`.
- Provider:
  - `placeholder` shows the sample snapshot with `.redacted(.placeholder)`.
  - `getSnapshot` serves cache-or-sample with no network (always when `context.isPreview`).
  - `getTimeline` uses `await TrendStore.load(timeout: 8)` and returns one entry dated now with policy `.after(refresh)`.
    - On a network success, `refresh = clamp(nextUpdateHint + 120s, now+15min, now+60min)`.
    - Otherwise, `refresh = now + 15min`.
- Layouts. Every family uses `containerBackground(for: .widget) { Theme.ink }`. No images in widgets.
  - **Small, top 3:** wordmark row ("Seoul Rising" plus a pink pulse dot). Three rows of rank numeral, name (1 line), change badge, and a cyan dot if KR-first. Footer: `Text(generatedAt, style: .relative)`. Tap target `.widgetURL(seoulpulse://item/<top1 id>)`.
  - **Medium, top 6:** two columns × 3 rows of the same row style. Each row is a `Link` to `seoulpulse://item/<id>`.
  - **Large, top 10:** header (wordmark, "Korea-first K-pop", updated time; the subtitle shows only when the status is short, the content box is at least 316 pt wide and text is at the default size or smaller, so 6.1-inch and smaller iPhones drop it). Rows 1–3 are 2 lines (name + badges, then `why` in caption). Rows 4–10 are 1 line. Each row is a `Link`. The rough height budget is header 28 + 3×40 + 7×26 + padding.
  - **Accessory rectangular, top 1:** line 1 is `#1 {name} {▲2}`, and `why` follows on up to 2 lines. Use system fonts and `.widgetAccentable()` on the rank. Tap target `widgetURL` to item 1.
- Footer states: `Sample data` when origin is `.sample`; `Offline` when origin is `.cache`; `Stale` in amber when `generatedAt` is more than 3 h old.

### 8.4 App screens (`SeoulPulse/`)
- **Routing:** `NavigationStack(path:)` with `enum Route { case item(String), settings, widgetGallery }`. `.onOpenURL` parses:
  - `seoulpulse://item/<id>` → `[.item(id)]`
  - `seoulpulse://settings` → `[.settings]`
  - `seoulpulse://widget-gallery` → `[.widgetGallery]`
  - `seoulrising://…` parses the same way (alias)
  - anything else → home
- **Home:**
  - Header: wordmark, "Korea-first K-pop trends", and "Updated 8m ago".
  - Banners: `cold_start` shows "Calibrating — rankings sharpen over the next few hours". Origin `.sample` shows "Showing sample data — set your feed URL in Settings". Data over 24 h old shows "Data is over a day old".
  - Up to 20 cards, each with rank numeral, name + `name_ko`, change badge, KR FIRST badge, `why`, and a 3 pt gradient score bar (width ∝ score/100).
  - Footer: source status pills (dot colored by status, plus name; tap shows the detail text). Pull-to-refresh. When the scene becomes active and the last fetch is more than 5 minutes old, refresh.
  - After every successful network load, call `WidgetCenter.shared.reloadTimelines(ofKind:)`.
- **Item detail** (`.item(id)`):
  - Large rank, name, `name_ko`, change badge. When KR-first, a KR FIRST badge plus the line "Korean sources moved before English-language coverage." Then `why`.
  - **Metric chips:** `{compact} views/hr` (with an "est." suffix when estimated), `Accel {x}x`, `KO Wiki {ratio}x`, `EN Wiki {ratio}x`, `{n} headlines · {k} KR`. Hide a chip when its value is null.
  - **Signal bars:** 5 labeled horizontal bars (YouTube, Acceleration, New release, Wikipedia, Headlines). Accel can be negative, so draw it from a center line.
  - **Top video card:** `AsyncImage` thumbnail with a placeholder, title, kind label, views, and a "Watch on YouTube" button.
  - **Headlines list:** each row shows a source pill (`Yonhap · KR`), the headline exactly as given, and a relative time.
  - **Links:** YouTube channel, Wikipedia EN, Wikipedia KO.
  - Every external link opens Safari via `@Environment(\.openURL)`.
  - Footnote: "Headlines link to the original publishers. Seoul Rising shows headlines and its own metrics only."
  - If the id is unknown, show "This trend isn't in the latest snapshot" with a "Back to top trends" button.
- **Settings:**
  - Feed URL text field, validated inline as http(s) with a host. Save writes the App Group default and reloads the widgets. "Reset to default" and "Test connection" (fetch and decode, then show `OK · 20 items · generated 18:00 KST` or the error) are alongside it.
  - It also shows the effective URL and where it came from (override or Info.plist), a "Widget preview" link to the gallery, the app version, and a data-sources disclaimer.
- **Widget gallery:** renders all 4 widget views at their family sizes using the current snapshot. It is used for screenshots.

### 8.5 Offline / fallback behavior
- The network may fail at any time, and the UI never shows an empty or error-only screen.
- Order: network → cache → bundled sample, always with an origin label.
- An empty `items` array shows "No trends yet — the pipeline hasn't published". The widget shows the same message in short form.
- Decode failures are logged with `os.Logger`, category `TrendStore`, and treated as a network failure.

### 8.6 Visual design: dark, neon-on-ink, restrained
| token | hex | use |
|---|---|---|
| `ink` | #0A0A12 | app & widget background |
| `surface` / `surface2` | #15151F / #1E1E2C | cards / chips |
| `hairline` | #2A2A3A | separators |
| `textPrimary` / `textSecondary` / `textTertiary` | #F5F5FA / #A1A1B5 / #6B6B80 | |
| `pulsePink` | #FF2E88 | brand accent, NEW badge, pulse dot |
| `neonViolet` | #7C5CFF | gradient end; `pulseGradient` = pink→violet, 135° |
| `seoulCyan` | #2EE6D6 | KR FIRST (text #2EE6D6 on 16% opacity fill) |
| `up` / `down` / `same` | #3DDC97 / #FF5A5F / #6B6B80 | rank change |
| `warn` | #FFB020 | degraded / stale |

- Typography:
  - Rank numerals: `.system(size: 20–44, weight: .black, design: .rounded).monospacedDigit()`. Rank 1 is filled with `pulseGradient`.
  - Wordmark: `.system(.headline, design: .rounded).weight(.heavy)` filled with the gradient.
  - Names: `.subheadline.weight(.semibold)`. Korean names: `.caption2` in `textTertiary`. Why: `.caption` in `textSecondary`.
  - System fonts render Hangul automatically. Widgets use `minimumScaleFactor(0.8)`.
- Rank-change badge:
  - up: `arrowtriangle.up.fill` at 8 pt plus the amount, in `up`
  - down: `arrowtriangle.down.fill` at 8 pt plus the amount, in `down`
  - same: `minus` in `same`
  - new: a capsule "NEW" at 9 pt heavy, white on `pulsePink`
  - VoiceOver labels: "Up 2 places", "Down 1 place", "No change", "New entry".
- KR FIRST: a capsule "KR FIRST" at 9 pt bold in the app and the large widget. Small and medium widgets use a 6 pt cyan dot.
- Cards use radius 16, padding 12, and 10 pt between cards. The app is dark-only (`UIUserInterfaceStyle: Dark`). An app icon is optional for MVP; if it is missing, drop `ASSETCATALOG_COMPILER_APPICON_NAME` rather than break the build.

## 9. Social draft generator (`seoulpulse social`) — manual posting only; we never post
- Input: `public/trends.json`, or `--input`. If `items` is empty, print "nothing to post" and exit 1.
- Take the top N (default 5). Date: `generated_at` in Asia/Seoul as `%b %-d` (and `%H:%M`). Delta tokens are `NEW`, `▲n`, `▼n`, or empty for same. KR-first items get a ` 🇰🇷` suffix. Item text is `why` exactly as given; no headlines ever appear.
- **X variant, ≤280 weighted.**
  - Weight per code point: 1 for U+0000–10FF, 2000–200D, 2010–201F, 2032–2037; 2 for everything else. This is conservative versus X's counter.
  - Lines: `Korea-first K-pop TOP {N} · {date}`, blank, `{rank}. {name}[ {delta}][ 🇰🇷]` per item, blank, `🇰🇷 = Korea moved first`, `#kpop #SeoulRising`.
  - Greedy: for item 1..N in order, append ` — {why}` to that item's line. If the total then exceeds 280, undo that append and stop.
- **Threads variant, ≤500 code points.**
  - Header `Korea-first K-pop Trends — TOP {N} ({date}, {HH:MM} KST)`, blank. Then per item, `{rank}. {name}[ ({name_ko})][ {delta}][ 🇰🇷]` followed by `   {why}`. Then a blank line and the footer.
  - Try in order and keep the first that fits:
    1. with `name_ko`, long footer (`🇰🇷 = Korean sources moved before English-language coverage.` + `Signals: YouTube view velocity, Wikipedia pageviews, KR/EN headline counts. Fan-made, not affiliated.`)
    2. without `name_ko`, long footer
    3. short footer `🇰🇷 = Korea moved first · fan-made, not affiliated`
    4. as (3), with whys truncated to 40 characters plus `…`
- Output: both variants with headers `=== X (266/280) ===` and `=== Threads (412/500) ===`.
- **Golden output for `docs/trends.example.json`** (test must match exactly). X (weighted 266):
```
Korea-first K-pop TOP 5 · Oct 14

1. KISS OF LIFE NEW 🇰🇷 — "Lucky Seven" views up 4.0x vs. normal decay
2. aespa ▲2 🇰🇷 — New MV "Neon Tide" at 127K views/hr
3. Lim Young-woong 🇰🇷
4. SEVENTEEN ▼2
5. LE SSERAFIM ▲2

🇰🇷 = Korea moved first
#kpop #SeoulRising
```
Threads (412 chars; variant 3 was the first to fit):
```
Korea-first K-pop Trends — TOP 5 (Oct 14, 18:00 KST)

1. KISS OF LIFE NEW 🇰🇷
   "Lucky Seven" views up 4.0x vs. normal decay
2. aespa ▲2 🇰🇷
   New MV "Neon Tide" at 127K views/hr
3. Lim Young-woong 🇰🇷
   2 Korean headlines, no English coverage yet
4. SEVENTEEN ▼2
   6 headlines in 24h (1 Korean)
5. LE SSERAFIM ▲2
   English Wikipedia views 2.1x the 2-week avg

🇰🇷 = Korea moved first · fan-made, not affiliated
```

## 10. Testing plan
**Backend** (`uv run pytest`, all offline; an autouse fixture fails any real socket. HTTP goes through `httpx.MockTransport`, and the clock and sleep are injected):
- `test_matching.py`:
  - `IVE` must not match "five" or "I've". `NCT 127` must win over `NCT`.
  - Particles: `뉴진스가` matches. `한지민` must not match `지민`. `로제떡볶이` must not match.
  - A multi-artist headline yields the right set. Video primary = the earliest match. The deny-list rejects `있지`.
- `test_classify.py`: at least 15 titles (the real formats from §2.3 and §3.3, including Shorts URLs, `MV Teaser` → teaser, and M COUNTDOWN → show). `extract_song` cases: `'…'`, `“…”`, 1theK ` _ `, ` - `, none.
- `test_parse_youtube.py`: fixture with 4 entries in the real HYBE LABELS feed structure (a feed-level channelId without `UC`, one `/shorts/` link, one entry missing statistics).
- `test_parse_rss.py`: soompi, koreaboo and yonhap fixtures, each with 3–5 items: synthetic titles, a CDATA title, and a `+0900` pubDate. Each fixture contains a `<description>`, and the test asserts it appears nowhere in the outputs. No real article text goes into fixtures.
- `test_parse_pageviews.py`: 200 response, 404 → unit error, URL encoding of Korean titles and of `Kiss_of_Life_(group)`.
- `test_freshness.py` (live 2026-10-18): the wiki freshness window (one day behind ok, two stale); a stale day scores 0, fires no wiki why-line and publishes no `wiki_day`; Korean leads need known-quiet English Wikipedia (stale / no data / short baseline → no lead, no article → quiet); a Naver spike with stale EN reads R5S without Korea-first; a Naver row older than `target − 2 d` adds nothing; an upstream delay degrades the source with `no Wikimedia data after D (upstream delay)`.
- `test_scoring_worked_example.py`: §5.8 exactly. `test_cold_start.py`: one snapshot per video, empty history and empty wiki → items produced, `cold_start=true`, order by lifetime intensity, no exceptions.
- `test_rank_change.py`: basis ≥6 h; fallback to oldest; empty → all `new`; up/down/same/amount.
- `test_why.py`: every rule R1–R9 fires; long song titles truncate; 60-character limit enforced; Korean song title.
- `test_failure_isolation.py`: wikipedia always 500, one YouTube channel 404, koreaboo timeout → run completes. Statuses error/degraded/error, and `trends.json` is still written and valid. All sources failing with an existing file → no overwrite, exit 2.
- `test_http.py`: 503 then 200 retries; `Retry-After` honored with a fake sleep; 404 is not retried.
- `test_publish_contract.py`: a generated payload and `docs/trends.example.json` both validate against `docs/trends.schema.json`. Ranks are contiguous. The write is atomic: the temp file is used and the destination is never partially written.
- `test_social.py`: golden strings from §9; weighted ≤280; ≤500.
- `test_live_audit.py` (live-feed audit 2026-10-15): pairs never mix `rss`/`api` counts and fall back to the other source's own pair; an RSS outage bridged by the API is `ok`/`degraded`, not `error`; no Korean lead from mentions older than the English coverage; Yonhap's `NCT 위시`; stage videos → perf; "Teasing Video" → teaser; variety-series, live-viewing and broadcaster teasers → other.
- `test_youtube_api.py`: no key → no calls; discovery + exact stats + RSS-count replacement; Shorts rule; rotation covers every channel once per 6 h; per-run and per-day caps are hard ceilings (ledger == calls); quota-exhausted and rejected-key stops; the key only in the `X-Goog-Api-Key` header and never in URLs, errors, the DB, `trends.json` or logs; 30-day API retention.
- `test_naver.py`: absent/partial keys are a no-op (5 sources, no `signals.search`); end-to-end with a fake DataLab (17 batched requests, headers, body limits, 6th source, schema-valid, §5.9 worked example, R3S/R5S); no raw series and no secrets stored; daily cadence and 2 h re-poll (`day` = the data day); stale DataLab data → `degraded`; a Naver spike with 3-day-stale Wikipedia end-to-end (no Korea-first, R5S); HUB→legacy fallback; rejected credentials, 429 and timeouts isolated; daily/run caps; Naver alone never counts as a successful run; the LLM never sees Naver values.
- Manual live smoke run, not in CI: `resolve-channels` → `validate-seed --online` → `run`. It must produce ≥10 items in under 5 minutes, and a second run 30 minutes later must report `cold_start:false`.

**iOS:**
- `xcodegen generate` and `xcodebuild build` succeed for the iPhone 17 Pro Max simulator with zero errors.
- `SeoulPulseTests`:
  - (a) Decode the bundled sample → 10 items with expected field values.
  - (b) Tolerance: unknown keys, `"change":"sideways"` → `.same`, null optionals.
  - (c) AppConfig resolution order.
  - (d) Deep-link parsing.
  - (e) `compact()` table: 999→"999", 7584→"7.6K", 126667→"127K", 1250000→"1.2M".
  - (f) **Widget snapshot test:** `ImageRenderer` renders each family view from the sample at 170×170, 364×170, 364×382, and 172×76 pt, scale 3. Assert a non-nil image with the expected pixel size. Write the PNGs to `$SP_SNAPSHOT_DIR` and add each as an `XCTAttachment`.
- Simulator check:
  1. `xcrun simctl install booted …/SeoulPulse.app`, then `xcrun simctl launch booted com.taejunoh.seoulpulse`.
  2. `xcrun simctl openurl booted seoulpulse://item/aespa` → screenshot. Repeat for `seoulpulse://widget-gallery`.
  3. With `seoulpulse serve` running, the Home origin must be "network". With the server stopped and the app relaunched, the origin must be "cache".

## 11. Definition of done
- [ ] `uv run pytest` is green offline. The worked example and the social golden output match exactly.
- [ ] `seoulpulse run` against the live sources, keyless, writes a schema-valid `public/trends.json` with ≥10 items. A second run gives `cold_start:false` and non-null `prev_rank` values.
- [ ] Failure isolation is shown by a test. A live run with `--sources` that omits one source marks it `disabled` and still publishes.
- [ ] No stored row or emitted field contains RSS `<description>`, `content:encoded`, or article text (asserted by tests). Korean headlines are untranslated.
- [ ] `validate-seed` passes with 60–100 acts. `resolve-channels` reports no `MISMATCH` written into the lock file. The deny-list is enforced.
- [ ] `seoulpulse serve --seed-example` serves `docs/trends.example.json` at `http://localhost:8787/trends.json` with `Cache-Control: no-cache`.
- [ ] `.github/workflows/pipeline.yml` exists:
  - triggers: cron `*/30 * * * *` plus `workflow_dispatch`; `concurrency: pipeline`
  - steps: checkout → `astral-sh/setup-uv` → restore `backend/var` with `actions/cache/restore` (key `sp-state-${{ github.run_id }}`, restore-keys `sp-state-`) → `uv sync --frozen` → `uv run seoulpulse run` (env `SP_CONTACT` from vars; `YOUTUBE_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` from secrets; optional vars `SP_YOUTUBE_DAILY_UNITS`, `SP_NAVER_ENDPOINT`) → `actions/cache/save` (if: always()) → `actions/upload-pages-artifact` (path `public`) → a `deploy-pages` job
  - `backend-ci.yml` runs pytest on push and PR. Neither workflow is executed in this session.
- [ ] The iOS project generates and builds, and its tests pass, including the 4 snapshot PNGs. `sample-trends.json` is byte-identical to `docs/trends.example.json` (`cmp`).
- [ ] In the simulator: home shows 10+ cards from the local server; `seoulpulse://item/<id>` opens detail; external links open Safari; Settings changes the URL and "Test connection" reports OK; widget gallery screenshots exist for all 4 families.
- [ ] With the server off, the widget and app fall back to cache, then to the sample, with the correct origin labels and no crash.
- [ ] `seoulpulse social` prints both variants within limits from the latest snapshot.

## 12. Known risks (accepted for MVP)
- **YouTube coverage and freshness.** RSS view counts can be cached or frozen; the `V_L > V_O` guard handles it. Label feeds roll over in 1–4 days; mitigated by carry-forward and the optional API key. Guessed artist handles are often wrong; mitigated by the resolver title check, plus label channels keep coverage.
- **Thresholds are first guesses.** Wiki daily granularity means the wiki signal updates once a day (after about 01:00–06:00 UTC). The decay exponent `P=0.8` and the weights are first guesses; tune them using `components_json`. GitHub cron is best-effort and can run 5–15 minutes late, so `next_update_hint` is only a hint.
- **Publisher terms.** Yonhap's feed carries a reuse and AI-use notice. We use headline and link only, never descriptions, and we never send headlines to the LLM.
- **Naver DataLab.** New credentials exist only on NAVER API HUB, which is free "temporarily"; metering could start later (the caps keep us at ~500–3,000 calls a month). Legacy developers.naver.com keys stop working after 2027-06-30. The DataLab-specific terms text could not be verified; storage and LLM rules follow the strictest reading (§3.7). Bare keyword queries carry no context, so ambiguous names need an explicit `search:` list (§2.1).
- **YouTube Shorts for API-only discoveries** use a duration/title heuristic (§3.3); a 61–180 s Short without `#shorts` in its title is scored as a regular video (usually `other`, weight 0.15).
