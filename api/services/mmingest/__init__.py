# mmingest service package — Sprint 2.
#
# Public API re-exported from sub-modules so callers import from here:
#
#   from api.services.mmingest import (
#       MmingestCrawler, SidecarFetcher, FileWorkItem, SidecarResult,
#       MmingestIndexer, IndexerRun,
#       get_mmingest_scheduler, configure_mmingest_jobs, start_mmingest_scheduler,
#   )

from api.services.mmingest.crawler import FileWorkItem, MmingestCrawler
from api.services.mmingest.indexer import IndexerRun, MmingestIndexer
from api.services.mmingest.parsers import (
    KNOWN_VARIANT_VOCAB,
    AutoindexParser,
    DirEntry,
    ParsedFilename,
    ParseError,
    parse_filename,
    select_primary,
)
from api.services.mmingest.scheduler import (
    configure_mmingest_jobs,
    get_mmingest_scheduler,
    start_mmingest_scheduler,
    stop_mmingest_scheduler,
)
from api.services.mmingest.sidecar_fetcher import SidecarFetcher, SidecarResult

__all__ = [
    # Crawler
    "MmingestCrawler",
    "FileWorkItem",
    # Indexer (Sprint 2)
    "MmingestIndexer",
    "IndexerRun",
    # Parsers
    "AutoindexParser",
    "DirEntry",
    "ParsedFilename",
    "ParseError",
    "parse_filename",
    "select_primary",
    "KNOWN_VARIANT_VOCAB",
    # Scheduler
    "get_mmingest_scheduler",
    "configure_mmingest_jobs",
    "start_mmingest_scheduler",
    "stop_mmingest_scheduler",
    # Sidecar fetcher
    "SidecarFetcher",
    "SidecarResult",
]
