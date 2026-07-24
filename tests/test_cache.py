from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_json_ttl_cache_concurrent_set_uses_distinct_temp_files(monkeypatch, tmp_path) -> None:
    from data_layer import cache as cache_mod

    monkeypatch.setattr(cache_mod, "repo_root", lambda: tmp_path)
    c = cache_mod.JsonTTLCache("concurrent", ttl_seconds=60)

    def write_one(i: int) -> None:
        c.set(c.key(str(i)), {"i": i})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_one, range(24)))

    for i in range(24):
        assert c.get(c.key(str(i))) == {"i": i}
