from __future__ import annotations

from typing import Any


class MissingSignal:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING_SIGNAL"

    def __bool__(self) -> bool:
        return False

    def __hash__(self) -> int:
        return id(self)

    def _missing(self, *_: Any) -> "MissingSignal":
        return self

    def _false(self, _: Any) -> bool:
        return False

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _missing
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = _missing
    __pow__ = __rpow__ = __neg__ = __pos__ = __abs__ = _missing
    __lt__ = __le__ = __gt__ = __ge__ = __eq__ = __ne__ = _false


MISSING_SIGNAL = MissingSignal()
