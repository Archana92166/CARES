"""CARES application backend.

The backend persists and serves outputs produced by the existing CARES
decision engine. It deliberately contains no LOW/MEDIUM/HIGH decision logic.
"""

from .db import Database
from .services import CARESBackend

__all__ = ["CARESBackend", "Database"]
