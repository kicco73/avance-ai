from datetime import datetime, timezone


def _utc_iso(dt: datetime | None) -> str | None:
	return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None


def _parse_iso(value: str | None) -> datetime | None:
	"""The exact inverse of _utc_iso — every datetime this db layer
	stores is naive-UTC, so a round-tripped ISO string needs its
	timezone info stripped back off before it's usable as a column value."""
	if not value:
		return None
	dt = datetime.fromisoformat(value)
	return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo is not None else dt
