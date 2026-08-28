from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from coding_agent.core.models import ContextSnapshot
from coding_agent.storage.sqlite import SQLiteStore


@pytest.fixture
def store(tmp_path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "state.db")
    result.initialize()
    return result


@pytest.fixture
def session(store: SQLiteStore):
    return store.create_session("/tmp/workspace", "Compaction")


def _snapshot(session_id: str, *, version: int, summary: str) -> ContextSnapshot:
    return ContextSnapshot(
        session_id=session_id,
        covered_through_message_seq=version * 2,
        summary=summary,
        created_at=datetime(2026, 8, 28, version, tzinfo=UTC),
        version=version,
        source_event_ids=(f"event-{version}-a", f"event-{version}-b"),
        model="claude-test",
        estimator_id="utf8-bytes-over-3-v1",
        token_estimate=20 + version,
        compaction_above_target=version > 1,
    )


def test_replacing_context_snapshot_round_trips_metadata_and_keeps_one_current_row(
    store: SQLiteStore,
    session,
) -> None:
    old = _snapshot(session.id, version=1, summary="old summary")
    new = _snapshot(session.id, version=2, summary="new summary")

    store.replace_context_snapshot(old)
    store.replace_context_snapshot(new)

    assert store.load_context_snapshot(session.id) == new
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM context_snapshots WHERE session_id = ?", (session.id,)
            ).fetchone()[0]
            == 1
        )


def test_failed_snapshot_replacement_preserves_old_snapshot_and_canonical_transcript(
    store: SQLiteStore,
    session,
) -> None:
    store.begin_run(session.id, "keep this exactly", {}, "start", "start-hash")
    old = _snapshot(session.id, version=1, summary="old summary")
    store.replace_context_snapshot(old)
    transcript_before = store.load_committed_transcript(session.id)
    with store.connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_new_snapshot BEFORE INSERT ON context_snapshots
            WHEN NEW.summary = 'new summary'
            BEGIN SELECT RAISE(ABORT, 'injected snapshot failure'); END
            """
        )

    with pytest.raises(sqlite3.DatabaseError, match="injected snapshot failure"):
        store.replace_context_snapshot(_snapshot(session.id, version=2, summary="new summary"))

    assert store.load_context_snapshot(session.id) == old
    assert store.load_committed_transcript(session.id) == transcript_before
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM context_snapshots WHERE session_id = ?", (session.id,)
            ).fetchone()[0]
            == 1
        )
