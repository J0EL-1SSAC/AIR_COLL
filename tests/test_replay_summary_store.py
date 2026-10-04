import sqlite3

from backend.airwatch.replay_summary_store import ReplaySummaryStore


def test_replay_cycle_summaries_are_tagged_and_queryable(tmp_path):
    path = tmp_path / "summary.db"
    store = ReplaySummaryStore(path)
    store.initialize()
    store.begin("session", 10, 20, 2, 10)
    store.record("session", 10, 8, 2, 0, 3, 1, 10)
    store.finish("session", "COMPLETE", 20)

    rows, total = store.query(0, 30, 10, 0)
    assert total == 1
    assert rows == [{"time": 10.0, "aircraft": 8, "low_altitude": 2,
                     "on_ground": 0, "pairs": 3, "alerts_opened": 1}]
    assert store.job("session")["completed_cycles"] == 1

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT DISTINCT mode FROM replay_cycle_summaries").fetchone()[0] == "REPLAY"
