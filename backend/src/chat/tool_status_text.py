from __future__ import annotations


def tool_status_text(payload: dict) -> str:
    """The transient line a live turn shows while a tool call is in
    flight, composed from an AiService "tool" event's own phase-"start"
    payload (see tracking.sources.ToolSet.tool_event) — `label` when the
    source declares one, its raw name otherwise. A `select` names the
    values it's searching for, matching what the model actually passed;
    an `update` never echoes the fields being written."""
    label = payload.get("label") or payload["source"]
    if payload.get("method") == "update":
        return f"Updating {label}…"
    values = (payload.get("arguments") or {}).get("values") or []
    if values:
        query = ", ".join(f'"{value}"' for value in values)
        return f"Searching {label} for {query}…"
    return f"Searching {label}…"
