from __future__ import annotations

from pathlib import Path

from venice_media_skill.jobs import SCHEMA_VERSION, JobStore


def test_job_store_create_update_and_list(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    record = store.create(
        media_type="video",
        model="model",
        queue_id="queue-1",
        request={"authorization": "******", "prompt": "hello"},
    )
    assert record.get("request") is None
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["request_sha256"] is not None
    assert record["model"] == "model"
    assert record["queue_id"] == "queue-1"
    assert record["status"] == "queued"
    assert record["download_url_display"] is None
    assert record["download_url_secret_ref"] is None
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
    # The record never carries the live URL inline; only the redacted
    # display copy is exposed.
    assert updated.get("download_url") is None
    assert "[REDACTED:" in updated["download_url_display"]
    assert "iVBORw0KGgo" not in updated["download_url_display"]
    # The runner can still fetch the live URL via the sidecar path.
    assert store.download_url_for("q-url-1") == data_url


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
    # Inside the persisted record, neither the signed token nor the
    # signature are reachable; only the redacted display copy and an
    # absolute path to the sidecar are present.
    assert updated.get("download_url_secret_ref")
    assert "abc123" not in updated["download_url_display"]
    assert "def456" not in updated["download_url_display"]
    assert "foo=bar" in updated["download_url_display"]
    assert "abc123" not in str(updated["download_url_secret_ref"])
    # The sidecar itself still carries the live signed URL so the
    # runner can hand it to the public downloader.
    assert store.download_url_for("q-sig-1") == signed


# ---------------------------------------------------------------------------
# P1-03: signed download URL must be separated from durable metadata records
# ---------------------------------------------------------------------------


def test_signed_url_sidecar_has_0600_permissions(tmp_path: Path) -> None:
    posix = __import__("sys").platform != "win32"
    if not posix:
        return  # chmod 0o600 is best-effort; skip perm assertion on Windows.
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-perm",
        request={},
    )
    store.update(
        "q-perm",
        download_url="https://cdn.venice.ai/out.mp4?token=secrettoken123",
    )
    sidecar = tmp_path / "download_secrets" / "q-perm.url"
    assert sidecar.exists()
    mode = sidecar.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_record_only_carries_redacted_display_and_secret_ref(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-split",
        request={},
    )
    signed = "https://cdn.venice.ai/out.mp4?token=abcdef&sig=ghij&k=v"
    updated = store.update("q-split", download_url=signed)
    # The legacy inline ``download_url`` key is never written.
    assert "download_url" not in updated
    display = updated["download_url_display"]
    ref = updated["download_url_secret_ref"]
    assert display is not None
    assert ref is not None
    # Display must NOT carry the signature or token.
    for needle in ("token=abcdef", "sig=ghij", "abcdef", "ghij"):
        assert needle not in display
    # Ref must be a path string, not the URL itself.
    assert "abcdef" not in ref
    assert "ghij" not in ref
    # Associated sidecar contains the live URL.
    assert store.download_url_for("q-split") == signed


def test_clearing_download_url_removes_sidecar_and_ref(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-clear",
        request={},
    )
    store.update("q-clear", download_url="https://cdn.venice.ai/out.mp4?token=xx")
    assert store.download_url_for("q-clear") is not None
    cleared = store.update("q-clear", download_url=None)
    assert cleared["download_url_display"] is None
    assert cleared["download_url_secret_ref"] is None
    assert store.download_url_for("q-clear") is None


def test_download_url_for_returns_none_when_no_secret_on_disk(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-empty",
        request={},
    )
    assert store.download_url_for("q-empty") is None


def test_legacy_v2_record_migrates_redacted_url_to_display(
    tmp_path: Path,
) -> None:
    legacy = {
        "queue_id": "q-legacy",
        "media_type": "video",
        "model": "m",
        "request_sha256": "abc",
        "status": "queued",
        "schema_version": 2,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "download_url": "[REDACTED: signed cdn url, 64 bytes]",
        "request": None,
        "result": None,
        "error": None,
        "last_retrieved_at": None,
        "completion_status": None,
    }
    (tmp_path / "q-legacy.json").write_text(__import__("json").dumps(legacy), encoding="utf-8")
    store = JobStore(tmp_path)
    migrated = store.get("q-legacy")
    # The legacy inline field is dropped after migration.
    assert "download_url" not in migrated
    assert migrated["download_url_display"] == legacy["download_url"]
    # No sidecar is recoverable from a v2 record.
    assert migrated["download_url_secret_ref"] is None
    assert store.download_url_for("q-legacy") is None
    # In-memory update path keeps the v3 invariants intact.
    new_signed = "https://cdn.venice.ai/out.mp4?token=t1"
    updated = store.update("q-legacy", download_url=new_signed)
    assert updated.get("download_url") is None
    assert store.download_url_for("q-legacy") == new_signed


def test_persisted_record_omits_signature_query_params(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    store.create(
        media_type="video",
        model="m",
        queue_id="q-clean",
        request={},
    )
    # All common signature-bearing keys must never surface in the
    # JSON-serialized record on disk.
    signed = "https://cdn.venice.ai/out.mp4?token=secretA&sig=secretB&signature=secretC&Expires=1700000000&KeyId=keyid1"
    store.update("q-clean", download_url=signed)
    persisted_text = (tmp_path / "q-clean.json").read_text(encoding="utf-8")
    for needle in ("secretA", "secretB", "secretC", "keyid1"):
        assert needle not in persisted_text, f"signature fragment {needle!r} leaked into the record"
