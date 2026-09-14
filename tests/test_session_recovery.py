import threading

from backend.app.paut.realtime import RealtimeService


def test_dead_worker_releases_session_flags():
    service = RealtimeService()
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()
    service._thread = worker
    service._running = True
    service._status["finalizing"] = True
    service._recover_inactive_session()
    assert not service._running
    assert not service._status["finalizing"]
    assert service._status["state"] == "处理已中断"
    assert service._stop_event.is_set()


def test_live_worker_cannot_be_reset():
    service = RealtimeService()
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(3))
    worker.start()
    try:
        service._thread = worker
        service._running = True
        service._recover_inactive_session()
        assert service._running
        assert service._thread is worker
    finally:
        release.set()
        worker.join()
