"""Job sources behind one interface — published APIs only, chosen by the user."""

from presence.connectors import boards  # noqa: F401  (registers the board providers)
from presence.connectors.base import (
    REGISTRY,
    Connector,
    ConnectorError,
    Posting,
    create,
    fetch_description,
    matches,
    run_sources,
)
from presence.connectors.catalog import CATALOG, available
from presence.connectors.seen import SeenPostings

__all__ = ["CATALOG", "REGISTRY", "Connector", "ConnectorError", "Posting", "SeenPostings",
           "available", "create", "fetch_description", "matches", "run_sources"]
