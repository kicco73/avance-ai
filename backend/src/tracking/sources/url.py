"""Parsing for a `sources:` entry's own `url:` field — `<scheme>:<path>`,
e.g. `avance:behaviour/flights.csv`. The scheme selects which driver
(tracking/sources/) resolves this source; the path is that driver's own,
opaque to everything outside it."""
from __future__ import annotations


def parse_source_url(url: str) -> tuple[str, str]:
    scheme, sep, path = url.partition(":")
    if not sep or not scheme or not path:
        raise ValueError(
            f"'{url}' is not a valid source url — expected '<scheme>:<path>', e.g. 'avance:behaviour/flights.csv'."
        )
    return scheme, path
