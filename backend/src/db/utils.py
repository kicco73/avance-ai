from datetime import datetime, timezone


def _utc_iso(dt: datetime) -> str:
	return dt.replace(tzinfo=timezone.utc).isoformat()
