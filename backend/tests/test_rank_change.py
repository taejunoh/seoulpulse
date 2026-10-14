from __future__ import annotations

import json
from datetime import timedelta

from seoulpulse.scoring import ArtistInfo, apply_rank_changes, basis_ranks, score_artist
from seoulpulse.store import Store

from .conftest import utc

NOW = utc(2026, 10, 14, 9, 0, 0)


def payload(ranks: dict[str, int]) -> str:
    return json.dumps({"items": [{"id": k, "rank": v} for k, v in ranks.items()]})


def make_store(tmp_path) -> Store:
    return Store(tmp_path / "db.sqlite")


def test_basis_is_newest_row_at_least_6h_old(tmp_path):
    st = make_store(tmp_path)
    st.insert_published(1, NOW - timedelta(hours=9), payload({"a": 1}))
    st.insert_published(2, NOW - timedelta(hours=6), payload({"a": 2}))     # exactly 6 h: eligible
    st.insert_published(3, NOW - timedelta(hours=1), payload({"a": 3}))
    at, p = st.rank_basis(NOW, 6)
    assert at == NOW - timedelta(hours=6)
    assert basis_ranks(p) == {"a": 2}
    st.close()


def test_basis_falls_back_to_oldest_row(tmp_path):
    st = make_store(tmp_path)
    st.insert_published(1, NOW - timedelta(minutes=60), payload({"a": 5}))
    st.insert_published(2, NOW - timedelta(minutes=30), payload({"a": 6}))
    at, p = st.rank_basis(NOW, 6)
    assert at == NOW - timedelta(minutes=60)
    assert basis_ranks(p) == {"a": 5}
    st.close()


def test_compare_hours_zero_means_previous_run(tmp_path):
    st = make_store(tmp_path)
    st.insert_published(1, NOW - timedelta(minutes=60), payload({"a": 5}))
    st.insert_published(2, NOW - timedelta(minutes=30), payload({"a": 6}))
    at, _ = st.rank_basis(NOW, 0)
    assert at == NOW - timedelta(minutes=30)
    st.close()


def test_empty_table_means_no_basis(tmp_path):
    st = make_store(tmp_path)
    assert st.rank_basis(NOW, 6) is None
    st.close()


def _ranked(ids: list[str]):
    out = []
    for i, aid in enumerate(ids, start=1):
        s = score_artist(ArtistInfo(aid, aid), [], {}, [], NOW, None)
        s.rank = i
        out.append(s)
    return out


def test_empty_basis_makes_everything_new():
    ranked = _ranked(["a", "b", "c"])
    apply_rank_changes(ranked, None)
    assert [(s.change, s.change_amount, s.prev_rank) for s in ranked] == [("new", 0, None)] * 3


def test_up_down_same_and_amount():
    ranked = _ranked(["a", "b", "c", "d"])
    apply_rank_changes(ranked, {"a": 4, "b": 1, "c": 3})
    got = {s.id: (s.prev_rank, s.change, s.change_amount) for s in ranked}
    assert got == {"a": (4, "up", 3), "b": (1, "down", 1), "c": (3, "same", 0), "d": (None, "new", 0)}
