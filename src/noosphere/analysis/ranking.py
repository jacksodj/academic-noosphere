"""Composite gap ranking with visible component scores (#12)."""

from noosphere.models import Gap


def composite_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of the component scores present in both dicts."""
    keys = [k for k in scores if k in weights and weights[k] != 0.0]
    total_weight = sum(weights[k] for k in keys)
    if total_weight == 0.0:
        return 0.0
    return sum(scores[k] * weights[k] for k in keys) / total_weight


def rank_gaps(gaps: list[Gap], weights: dict[str, float]) -> list[Gap]:
    """Set each Gap's composite_score from ``weights`` and sort descending."""
    for gap in gaps:
        gap.composite_score = composite_score(gap.scores, weights)
    return sorted(gaps, key=lambda g: (-g.composite_score, g.gap_id))
