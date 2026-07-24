"""News dedupe and event clustering."""

from __future__ import annotations

from news_pipeline.fetchers.base import RawNews, ensure_utc

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None

SOURCE_PRIORITY = {
    "reuters": 10,
    "sec_edgar": 10,
    "finnhub": 7,
    "yfinance": 5,
}


def _source_score(source: str) -> int:
    for prefix, score in SOURCE_PRIORITY.items():
        if source.startswith(prefix):
            return score
    return 1


def _title_similarity(a: str, b: str) -> float:
    if fuzz is not None:
        return float(fuzz.token_sort_ratio(a, b))
    aset = set(a.lower().split())
    bset = set(b.lower().split())
    if not aset or not bset:
        return 0.0
    return 100.0 * len(aset & bset) / len(aset | bset)


def dedupe_exact(news_list: list[RawNews]) -> list[RawNews]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[RawNews] = []
    for item in news_list:
        title_key = item.title.lower().strip()
        if item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        out.append(item)
    return out


def cluster_news(news_list: list[RawNews], time_window_hours: int = 2) -> list[list[RawNews]]:
    if not news_list:
        return []
    clusters: list[list[RawNews]] = []
    for item in sorted(news_list, key=lambda n: ensure_utc(n.published_at)):
        item.published_at = ensure_utc(item.published_at)
        matched: list[RawNews] | None = None
        for cluster in clusters:
            cluster[0].published_at = ensure_utc(cluster[0].published_at)
            t_diff = abs((item.published_at - cluster[0].published_at).total_seconds())
            if t_diff > time_window_hours * 3600:
                continue
            for member in cluster:
                ticker_overlap = bool(set(item.affected_tickers) & set(member.affected_tickers))
                if ticker_overlap and _title_similarity(item.title, member.title) >= 70:
                    matched = cluster
                    break
            if matched is not None:
                break
        if matched is None:
            clusters.append([item])
        else:
            matched.append(item)
    return clusters


def pick_best_from_cluster(cluster: list[RawNews]) -> RawNews:
    return max(cluster, key=lambda n: _source_score(n.source))


def dedupe_and_cluster(news_list: list[RawNews]) -> tuple[list[RawNews], dict[str, list[str]]]:
    clusters = cluster_news(dedupe_exact(news_list))
    reps: list[RawNews] = []
    cluster_map: dict[str, list[str]] = {}
    for cluster in clusters:
        best = pick_best_from_cluster(cluster)
        reps.append(best)
        cluster_map[best.id] = [n.id for n in cluster]
    return reps, cluster_map
