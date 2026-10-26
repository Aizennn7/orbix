"""Self-healing guard for stores whose data directory can vanish at runtime.

Every persistent store in Orbix creates its parent directory once, in
``__init__``. That is correct for a process that starts, opens its database and
runs. It is not correct for a long-lived server, which can easily outlive the
directory it was handed:

* a container volume is remounted or replaced,
* an operator or a cleanup job removes the data directory,
* a test harness deletes ``./data`` between runs while a server still holds it.

When that happens ``sqlite3.connect()`` raises
``OperationalError("unable to open database file")`` on *every* subsequent
request. Nothing in the process recovers, because the only code that created the
directory already ran during startup. One deleted directory becomes a
permanently broken dashboard that still returns HTTP 200 on ``/healthz``, so
uptime checks stay green while the app cannot serve anything.

The guard costs one ``stat()`` per connection and makes the stores self-healing
instead. It is deliberately shared by every store rather than copied into each
one: three identical copies of a recovery path is three chances for one of them
to be wrong, and the failure it prevents is invisible until production.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_parent(database: Path, label: str) -> bool:
    """Recreate ``database``'s parent directory if it disappeared.

    Returns True when the directory had to be recreated. A True return carries a
    second meaning the caller must act on: the database *file* is gone too, so a
    freshly created SQLite or DuckDB file has no tables. Callers that get True
    must rebuild their schema, otherwise every query fails with "no such table"
    instead of "unable to open database file" - a different exception, and the
    same broken app.

    Args:
        database: Path to the store's database file.
        label: Name used in the log line, so an operator can tell which store
            lost its directory when several share one data dir.
    """
    parent = database.parent
    if parent.is_dir():
        return False
    logger.warning(
        "[%s] data directory %s disappeared; recreating it and rebuilding the "
        "schema. Any rows stored in it are lost.",
        label,
        parent,
    )
    parent.mkdir(parents=True, exist_ok=True)
    return True
