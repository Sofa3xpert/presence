"""Job sources behind one interface. v1: LinkedIn (library-based search)."""

from presence.connectors import linkedin  # noqa: F401  (registers "linkedin")
from presence.connectors.base import (
    REGISTRY,
    Connector,
    ConnectorError,
    Posting,
    SearchQuery,
    build_queries,
    create,
    run_search,
)
from presence.connectors.seen import SeenPostings

__all__ = ["REGISTRY", "Connector", "ConnectorError", "Posting", "SearchQuery", "SeenPostings",
           "build_queries", "create", "run_search"]
