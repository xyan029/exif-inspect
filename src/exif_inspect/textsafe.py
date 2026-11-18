"""Text taken from a file, made safe to print.

Everything this tool prints about a picture is read by a person deciding
whether the picture can be trusted, and part of it (camera names, software
names, dates, XMP fields, the file's own name) is text the file's author
chose. Printed as it stands, a value holding a line break can add lines
that look like the tool's own output, an escape sequence can move the
cursor and overwrite what was printed before it, and a bidirectional
override can show the characters of a name in another order.

`clean` keeps what the value says and takes away what it can do: every
control character, line or paragraph separator, bidirectional control,
lone surrogate and private-use character is replaced by a visible escape
such as `\\n`, `\\x1b` or `\\u202e`, so the reader sees that the value
held it. A backslash is written out the same way only when the value needs
an escape, which keeps ordinary Windows paths as they are.
"""

from __future__ import annotations

import unicodedata

MAX_SHOWN = 2000        # characters of one value worth printing

_NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def _unsafe(ch: str) -> bool:
    if ch in _NAMED:
        return True
    code = ord(ch)
    if 32 <= code < 127:
        return False
    return unicodedata.category(ch) in _UNSAFE_CATEGORIES


def _escape(ch: str) -> str:
    if ch in _NAMED:
        return _NAMED[ch]
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def clean(value: object, limit: int = MAX_SHOWN) -> str:
    """The value as one line of text that prints as itself and nothing more."""
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        text = text[:limit] + f"... ({len(text)} characters)"
    if text.isascii() and text.isprintable():
        return text
    if not any(_unsafe(ch) for ch in text):
        return text
    return "".join(_escape(ch) if _unsafe(ch) else "\\\\" if ch == "\\" else ch for ch in text)
