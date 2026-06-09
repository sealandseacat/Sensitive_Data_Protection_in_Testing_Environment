"""Database connectivity. One SQLAlchemy-based connector handles every dialect."""

from datamask.connectors.base import ColumnRef, Connector
from datamask.connectors.sql import SQLConnector, build_url

__all__ = ["ColumnRef", "Connector", "SQLConnector", "build_url"]
