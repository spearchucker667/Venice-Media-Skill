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
    assert record.get("request") is None
    assert record["schema_version"] == 2
    assert record["request_sha256"] is not None
    assert record["model"] == "model"
    assert record["queue_id"] == "queue-1"
    assert record["status"] == "queued"
    updated = store.update("queue-1", status="completed")
    assert updated["status"] == "completed"
    assert store.get("queue-1")["model"] == "model"
    assert len(store.list()) == 1


def test_job_store_redacts_data_url(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="image",
        model="m",
        queue_id="q-url-1",
        request={"prompt": "test"},
    )
    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    updated = store.update("q-url-1", download_url=data_url)
    assert "[REDACTED:" in updated["download_url"]
    assert "iVBORw0KGgo" not in updated["download_url"]


def test_job_store_redacts_signed_url_query(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-sig-1",
        request={},
    )
    signed = "https://cdn.venice.ai/output.mp4?token=abc123&sig=def456&foo=bar"
    updated = store.update("q-sig-1", download_url=signed)
    assert "abc123" not in updated["download_url"]
    assert "def456" not in updated["download_url"]
    assert "foo=bar" in updated["download_url"]
