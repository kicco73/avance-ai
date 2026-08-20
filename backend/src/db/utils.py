from datetime import datetime, timezone


def _utc_iso(dt: datetime | None) -> str | None:
	return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None


def _parse_iso(value: str | None) -> datetime | None:
	"""The exact inverse of _utc_iso — every datetime this db layer ever
	stores is naive-UTC (see every model's own DateTimeField), so a
	round-tripped ISO string (see SessionImportManager.import_session_
	json/session_export.py) needs its own timezone info stripped back off
	before it's usable as a plain column value again, not just parsed."""
	if not value:
		return None
	dt = datetime.fromisoformat(value)
	return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo is not None else dt
