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
