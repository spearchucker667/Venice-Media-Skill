from __future__ import annotations

from pathlib import Path

from venice_media_skill.jobs import JobStore


def test_job_store_create_update_and_list(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    record = store.create(
        media_type="video",
        model="model",
        queue_id="queue-1",
        request={"authorization": "Bearer secret", "prompt": "hello"},
    )
    assert record["request"]["authorization"] == "[REDACTED]"
    updated = store.update("queue-1", status="completed")
    assert updated["status"] == "completed"
    assert store.get("queue-1")["model"] == "model"
    assert len(store.list()) == 1
