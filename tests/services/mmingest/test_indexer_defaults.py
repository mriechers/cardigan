"""Default-value tests for MmingestIndexer (politeness spec compliance)."""

from __future__ import annotations

from api.services.mmingest.indexer import MmingestIndexer


def test_indexer_default_rate_per_second_is_one():
    """#203: the production indexer defaults to 1.0 req/s (the polite-crawl
    spec), not 2.0. The Sprint 5 soak overrode this explicitly; production
    rollout should ship the correct default."""
    indexer = MmingestIndexer(engine=None)
    assert indexer._rate_per_second == 1.0
