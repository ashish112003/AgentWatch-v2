"""
app/tools/uuid_generator.py
────────────────────────────
LangChain UUID Generator Tool.

Generates one or more UUID version 4 (random) identifiers using the
stdlib `uuid` module.  No external dependencies.

UUID v4:
  128-bit random value formatted as 8-4-4-4-12 hex groups.
  Example: 550e8400-e29b-41d4-a716-446655440000
  Collision probability with 1 trillion UUIDs: ~6.4 × 10⁻¹⁶.
  Safe to use as primary keys, trace IDs, and correlation tokens.

Input formats accepted:
  "1"                  — generate 1 UUID (default)
  "5"                  — generate 5 UUIDs
  "3 uppercase"        — generate 3 UUIDs in UPPERCASE
  "2 no-hyphens"       — generate 2 UUIDs without hyphens
  "4 compact"          — alias for no-hyphens
  "1 braces"           — generate 1 UUID in {brace} format

Limits:
  • Minimum: 1 UUID
  • Maximum: 20 UUIDs per call (to keep output reasonable)
"""

import re
import uuid
from langchain_core.tools import tool


_MIN_COUNT = 1
_MAX_COUNT = 20


def _parse_query(query: str) -> tuple[int, str]:
    """
    Parse the input query to extract count and format modifier.

    Args:
        query: Raw input string, e.g. "3 uppercase" or "5".

    Returns:
        Tuple of (count, format_modifier).
        format_modifier is one of: "default", "uppercase", "no-hyphens", "braces".

    Raises:
        ValueError: Count is out of the allowed range or cannot be parsed.
    """
    query   = query.strip().lower()
    tokens  = query.split()

    # Extract count — first numeric token, default 1
    count = 1
    remaining_tokens = []
    for token in tokens:
        if re.match(r"^\d+$", token):
            count = int(token)
        else:
            remaining_tokens.append(token)

    # Validate count
    if count < _MIN_COUNT:
        raise ValueError(f"Count must be at least {_MIN_COUNT}.")
    if count > _MAX_COUNT:
        raise ValueError(
            f"Count must be at most {_MAX_COUNT} (requested {count}). "
            "For bulk generation, call the tool multiple times."
        )

    # Detect format modifier from remaining tokens
    modifier_text = " ".join(remaining_tokens)
    if any(kw in modifier_text for kw in ("upper", "uppercase", "caps")):
        fmt = "uppercase"
    elif any(kw in modifier_text for kw in ("no-hyphen", "nohyphen", "compact", "hex")):
        fmt = "no-hyphens"
    elif any(kw in modifier_text for kw in ("brace", "curly")):
        fmt = "braces"
    else:
        fmt = "default"

    return count, fmt


def _format_uuid(u: uuid.UUID, fmt: str) -> str:
    """
    Format a UUID object according to the requested style.

    Args:
        u:   uuid.UUID instance.
        fmt: One of "default", "uppercase", "no-hyphens", "braces".

    Returns:
        Formatted UUID string.
    """
    if fmt == "uppercase":
        return str(u).upper()
    if fmt == "no-hyphens":
        return u.hex
    if fmt == "braces":
        return "{" + str(u) + "}"
    return str(u)


def _generate(count: int, fmt: str) -> str:
    """
    Generate `count` UUID v4 values and format the result.

    Args:
        count: Number of UUIDs to generate.
        fmt:   Format modifier string.

    Returns:
        Formatted output string.
    """
    uuids = [_format_uuid(uuid.uuid4(), fmt) for _ in range(count)]

    if count == 1:
        return f"UUID (v4):\n{uuids[0]}"

    numbered = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(uuids))
    return f"Generated {count} UUID v4 values:\n{numbered}"


@tool
def uuid_generator(query: str = "1") -> str:
    """
    Generate one or more random UUID v4 identifiers.

    Input format: "[count] [format]"
      count:  How many UUIDs to generate (1–20, default 1)
      format: Optional modifier:
              "uppercase"  — UPPERCASE output
              "no-hyphens" — compact hex without hyphens (32 chars)
              "braces"     — {brace-wrapped} format

    Examples:
        "1"              → one standard UUID
        "5"              → five standard UUIDs
        "3 uppercase"    → three uppercase UUIDs
        "4 no-hyphens"   → four compact UUIDs (no hyphens)
        "1 braces"       → one UUID in {braces}

    Args:
        query: Count and optional format modifier (default: "1").

    Returns:
        One or more UUID v4 strings, formatted as requested.
    """
    if not query or not query.strip():
        query = "1"

    try:
        count, fmt = _parse_query(query)
        return _generate(count, fmt)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error generating UUIDs: {exc}"