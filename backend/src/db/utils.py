from datetime import datetime, timezone


def _utc_iso(dt: datetime | None) -> str | None:
	return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None
