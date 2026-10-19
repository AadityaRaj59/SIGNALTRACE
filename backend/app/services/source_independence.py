"""Independent-source clustering.

Two sources are treated as the SAME evidence cluster when they share a registrable
domain (same publisher) or their text is near-duplicate (syndicated / mirrored copy).
Evidence strength counts distinct clusters, not distinct URLs.
"""
import re
from urllib.parse import urlparse

_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "nic"}


def registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    if len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _shingles(text: str, k: int = 5, cap: int = 4000) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())[:cap]
    if len(words) < k:
        return set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_sources(sources: list[dict], threshold: float = 0.6) -> dict[int, dict]:
    """sources: [{id,url,content_text}] -> {source_id: {cluster, domain, reason}}"""
    parent = {s["id"]: s["id"] for s in sources}
    reasons: dict[int, str] = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b, why):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
            reasons[b] = why

    shingles = {s["id"]: _shingles(s.get("content_text", "")) for s in sources}
    domains = {s["id"]: registrable_domain(s["url"]) for s in sources}
    ids = [s["id"] for s in sources]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if domains[a] and domains[a] == domains[b]:
                union(a, b, f"same publisher domain ({domains[a]})")
            elif jaccard(shingles[a], shingles[b]) >= threshold:
                union(a, b, "near-duplicate text (syndicated or mirrored)")
    roots = {}
    result = {}
    for sid in ids:
        r = find(sid)
        roots.setdefault(r, len(roots) + 1)
        result[sid] = {"cluster": roots[r], "domain": domains[sid], "reason": reasons.get(sid)}
    return result
