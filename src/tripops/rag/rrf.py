from collections import defaultdict

from tripops.rag.models import RetrievalHit, RetrievalResult


def reciprocal_rank_fusion(
    channels: tuple[tuple[RetrievalHit, ...], ...],
    *,
    rank_constant: int = 60,
    weights: dict[str, float] | None = None,
    limit: int = 20,
) -> tuple[RetrievalResult, ...]:
    if rank_constant < 1 or limit < 1:
        raise ValueError("RRF rank constant and limit must be positive")
    channel_weights = weights or {}
    scores: defaultdict[str, float] = defaultdict(float)
    ranks: defaultdict[str, dict[str, int]] = defaultdict(dict)
    raw_scores: defaultdict[str, dict[str, float]] = defaultdict(dict)
    chunks = {}

    for hits in channels:
        for hit in hits:
            chunk_id = hit.chunk.id
            weight = channel_weights.get(hit.channel, 1.0)
            scores[chunk_id] += weight / (rank_constant + hit.channel_rank)
            ranks[chunk_id][hit.channel] = hit.channel_rank
            raw_scores[chunk_id][hit.channel] = hit.channel_score
            chunks[chunk_id] = hit.chunk

    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:limit]
    return tuple(
        RetrievalResult(
            chunk=chunks[chunk_id],
            fused_score=scores[chunk_id],
            rank=rank,
            citation_id=f"CIT-{chunk_id}",
            channel_ranks=ranks[chunk_id],
            channel_scores=raw_scores[chunk_id],
        )
        for rank, chunk_id in enumerate(ordered, start=1)
    )

