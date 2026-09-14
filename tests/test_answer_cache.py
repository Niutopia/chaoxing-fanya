import threading
from concurrent.futures import ThreadPoolExecutor

from api.answer import CacheDAO


def test_cache_instances_share_a_lock_for_the_same_file(tmp_path):
    cache_path = tmp_path / "answer-cache.json"
    first = CacheDAO(str(cache_path))
    second = CacheDAO(str(cache_path))
    writes = [
        (first, "question-a", "answer-a"),
        (second, "question-b", "answer-b"),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda item: item[0].add_cache(item[1], item[2]), writes))
    assert first.get_cache("question-a") == "answer-a"
    assert first.get_cache("question-b") == "answer-b"


def test_concurrent_first_construction_does_not_erase_intervening_answer(
    tmp_path,
    monkeypatch,
):
    cache_path = tmp_path / "answer-cache.json"
    first_write_started = threading.Event()
    allow_first_write = threading.Event()
    second_write_started = threading.Event()
    allow_second_write = threading.Event()
    write_lock = threading.Lock()
    initialization_writes = 0

    original_write = CacheDAO._write_cache

    def gated_write(self, data):
        nonlocal initialization_writes
        if data != {}:
            return original_write(self, data)
        with write_lock:
            initialization_writes += 1
            write_number = initialization_writes
        if write_number == 1:
            first_write_started.set()
            if not allow_first_write.wait(3):
                raise AssertionError("first cache initialization did not release")
        elif write_number == 2:
            second_write_started.set()
            if not allow_second_write.wait(2):
                raise AssertionError("second cache initialization did not release")
        return original_write(self, data)

    monkeypatch.setattr(CacheDAO, "_write_cache", gated_write)

    executor = ThreadPoolExecutor(max_workers=2)
    first_future = executor.submit(CacheDAO, str(cache_path))
    assert first_write_started.wait(2), (
        first_future.exception() if first_future.done() else "first constructor is still running"
    )
    second_future = executor.submit(CacheDAO, str(cache_path))

    def release_first_after_second_can_race():
        # The old constructor can invoke its second initialization while the
        # first write is held.  The fixed constructor waits on the shared
        # per-path lock, so the bounded wait releases the first task and lets
        # the second task observe the populated file.
        second_write_started.wait(1)
        allow_first_write.set()

    release_thread = threading.Thread(target=release_first_after_second_can_race)
    release_thread.start()
    try:
        first = first_future.result(timeout=3)
        first.add_cache("question", "answer")
        allow_second_write.set()
        second_future.result(timeout=3)
    finally:
        allow_first_write.set()
        allow_second_write.set()
        release_thread.join(timeout=2)
        executor.shutdown(wait=True)

    assert first.get_cache("question") == "answer"
