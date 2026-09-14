import threading
import time
from pathlib import Path

import numpy as np

from backend.app.paut.realtime import RealtimeConfig, RealtimeService
from backend.app.services import scan_receipt_service as receipts


def test_receives_independently_and_drains_after_stop(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    for i in range(3):
        np.savetxt(source / f"{i}.txt", np.column_stack([np.arange(40)*2e-8, np.zeros((40, 256))]))
    service = RealtimeService()
    service._config = RealtimeConfig(source_path=str(source), simulation_enabled=True, interval_seconds=0.1)
    service._session_id = "test-scan"
    monkeypatch.setattr(service, "_session_dir", lambda: tmp_path / "session")
    records = []
    monkeypatch.setattr(receipts, "save_receipt", lambda record, state, error="": records.append((dict(record), state)))
    service._receive_simulated_frames()
    assert service._receipt_queue.qsize() == 3
    assert all(state == "queued" for _, state in records)
    assert len({record["frame_id"] for record, _ in records}) == 3
    assert all(Path(record["input_path"]).exists() for record, _ in records)
    monkeypatch.setattr("backend.app.paut.realtime.require_processing_headroom", lambda: {})
    completed = []
    def process(path):
        record = service._active_receipt
        return {"frame_id": record["frame_id"], "received_at": record["received_at"], "image": {}, "analysis": {}}
    monkeypatch.setattr(service, "_process_single_group", process)
    monkeypatch.setattr(service, "_persist_frame", lambda frame: completed.append(dict(frame)))
    monkeypatch.setattr(service, "_write_session_manifest", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_finalize_session", lambda: None)
    service._running = False
    service._stop_event.set()
    service._process_loop()
    assert len(completed) == 3
    assert receipts.audit_scan_frames(completed)["passed"]
    broken = dict(completed[0], received_at="wrong")
    assert not receipts.audit_scan_frames([broken])["passed"]


def test_queue_is_bounded_and_stop_unblocks_receiver(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    for i in range(10):
        np.savetxt(source / f"{i}.txt", np.column_stack([np.arange(4)*2e-8, np.zeros((4, 256))]))
    service = RealtimeService()
    service._config = RealtimeConfig(source_path=str(source), simulation_enabled=True)
    service._session_id = "test-full"
    monkeypatch.setattr(service, "_session_dir", lambda: tmp_path / "session")
    monkeypatch.setattr(receipts, "save_receipt", lambda *a: None)
    worker = threading.Thread(target=service._receive_simulated_frames)
    worker.start()
    deadline = time.monotonic() + 8
    while not service._receipt_queue.full() and time.monotonic() < deadline:
        time.sleep(0.05)
    service._stop_event.set()
    worker.join(3)
    assert not worker.is_alive()
    assert service._receipt_queue.qsize() == 8
