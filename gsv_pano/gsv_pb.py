"""Readable Google "pb" URL parameters (the !1m4!1s... strings).

Google Maps web requests carry a protobuf message flattened into a string:
each token is ``!<field number><type><value>``.  Types seen in Street View
requests: ``m`` = sub-message (value = number of tokens inside it), ``s`` =
string, ``e`` = enum, ``b`` = bool (0/1), ``i``/``j``/``u`` = integers,
``d``/``f`` = floating point.  SIM pastes such strings as opaque text (see
GSV_pano.getJsonfrmPanoID); this module parses them into nested lists and
writes them back, so a request can be written as data and every part can be
named.

    python gsv_pano/gsv_pb.py "!1m4!1smaps_sv.tactile!11m2!2m1!1b1"   # prints the tree

Round trip is exact: ``dumps(loads(s)) == s``.
"""
from __future__ import annotations

import re
import sys
from urllib.parse import quote, unquote

_TOKEN = re.compile(r"(\d+)([a-z])(.*)", re.S)
Field = tuple  # (number, type, value); value is a list of Field for type "m"


def loads(pb: str) -> list[Field]:
    """Parse a pb string (with or without the leading '!') into fields."""
    tokens = [t for t in pb.lstrip("!").split("!") if t != ""]
    pos = 0

    def take(count: int) -> list[Field]:
        nonlocal pos
        out, used = [], 0
        while used < count:
            m = _TOKEN.fullmatch(tokens[pos])
            if not m:
                raise ValueError(f"bad pb token {tokens[pos]!r}")
            num, typ, raw = int(m.group(1)), m.group(2), m.group(3)
            pos += 1
            used += 1
            if typ == "m":
                start = pos
                out.append((num, "m", take(int(raw))))
                used += pos - start
            else:
                out.append((num, typ, _value(typ, raw)))
        return out

    fields = take(len(tokens)) if tokens else []
    return fields


def _value(typ: str, raw: str):
    if typ in "eiju":
        return int(raw)
    if typ == "b":
        return raw == "1"
    if typ in "df":
        return float(raw)
    return unquote(raw) if typ == "s" else raw


def _raw(typ: str, value) -> str:
    if typ == "b":
        return "1" if value else "0"
    if typ == "s":
        return quote(str(value), safe="-_.~*'()")      # as JavaScript encodeURIComponent
    if typ in "df" and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _count(fields: list[Field]) -> int:
    return sum(1 + (_count(v) if t == "m" else 0) for _n, t, v in fields)


def dumps(fields: list[Field]) -> str:
    """Write fields back to a pb string; message sizes are recomputed."""
    out = []
    for num, typ, value in fields:
        if typ == "m":
            out.append(f"!{num}m{_count(value)}")
            out.append(dumps(value))
        else:
            out.append(f"!{num}{typ}{_raw(typ, value)}")
    return "".join(out)


def msg(*fields: Field) -> list[Field]:
    return list(fields)


def pretty(fields: list[Field], indent: int = 0, names: dict | None = None, path: str = "") -> str:
    """One line per field; ``names`` maps dotted paths ("4.1") to labels."""
    names = names or {}
    lines = []
    for num, typ, value in fields:
        p = f"{path}.{num}" if path else str(num)
        label = names.get(p, "")
        label = f"   <- {label}" if label else ""
        if typ == "m":
            lines.append(f"{'  ' * indent}{num}: {{{label}")
            lines.append(pretty(value, indent + 1, names, p))
            lines.append(f"{'  ' * indent}}}")
        else:
            lines.append(f"{'  ' * indent}{num} ({typ}) = {value!r}{label}")
    return "\n".join(x for x in lines if x)


if __name__ == "__main__":
    text = sys.argv[1]
    if "pb=" in text:
        text = re.search(r"pb=([^&]*)", text).group(1)
    tree = loads(text)
    print(pretty(tree))
    print("round trip exact:", dumps(tree) == text)
