"""
app/tools/json_formatter.py
────────────────────────────
LangChain JSON Formatter Tool.

Validates and pretty-prints JSON strings.  All processing uses the
stdlib `json` module — no external dependencies.

Supported operations (passed as `operation`):
  "format"    — pretty-print JSON with 2-space indentation (default)
  "validate"  — check whether the input is valid JSON (returns bool + detail)
  "minify"    — compact JSON with no whitespace (useful for payloads)
  "keys"      — list the top-level keys of a JSON object
  "stats"     — return statistics about the JSON structure

Input constraints:
  • Maximum 50,000 characters.
  • Must be a JSON string, object, array, number, or boolean.
  • Trailing commas and comments are NOT valid JSON and will be rejected.

Security notes:
  • `json.loads()` is safe — it does not execute code.
  • Output is truncated to _MAX_OUTPUT_CHARS to prevent context-window
    flooding from very large JSON structures.
"""

import json
from langchain_core.tools import tool


_MAX_INPUT_CHARS  = 50_000
_MAX_OUTPUT_CHARS = 4_000   # truncate formatted output to stay within LLM context


def _count_nodes(obj, depth: int = 0) -> dict:
    """Recursively count objects, arrays, and leaf values in a JSON structure."""
    counts = {"objects": 0, "arrays": 0, "strings": 0, "numbers": 0, "nulls": 0, "booleans": 0, "max_depth": depth}
    if isinstance(obj, dict):
        counts["objects"] += 1
        for v in obj.values():
            sub = _count_nodes(v, depth + 1)
            for k in counts:
                counts[k] = max(counts[k], sub[k]) if k == "max_depth" else counts[k] + sub[k]
    elif isinstance(obj, list):
        counts["arrays"] += 1
        for item in obj:
            sub = _count_nodes(item, depth + 1)
            for k in counts:
                counts[k] = max(counts[k], sub[k]) if k == "max_depth" else counts[k] + sub[k]
    elif isinstance(obj, str):
        counts["strings"] += 1
    elif isinstance(obj, bool):
        counts["booleans"] += 1
    elif isinstance(obj, (int, float)):
        counts["numbers"] += 1
    elif obj is None:
        counts["nulls"] += 1
    return counts


def _process(json_str: str, operation: str) -> str:
    """
    Parse the JSON and perform the requested operation.

    Args:
        json_str:  Raw JSON string from the LLM.
        operation: One of format, validate, minify, keys, stats.

    Returns:
        Result string appropriate for the operation.

    Raises:
        json.JSONDecodeError: Input is not valid JSON.
        ValueError:           Unknown operation or unsupported input type.
    """
    operation = operation.strip().lower()

    # ── validate ──────────────────────────────────────────────
    if operation == "validate":
        try:
            parsed = json.loads(json_str)
            type_name = type(parsed).__name__
            return (
                f"✓ Valid JSON\n"
                f"  Type:   {type_name}\n"
                f"  Length: {len(json_str):,} characters"
            )
        except json.JSONDecodeError as exc:
            return (
                f"✗ Invalid JSON\n"
                f"  Error:    {exc.msg}\n"
                f"  Position: line {exc.lineno}, column {exc.colno} "
                f"(character {exc.pos})"
            )

    # All other operations require a successful parse
    parsed = json.loads(json_str)   # raises JSONDecodeError if invalid

    # ── format ────────────────────────────────────────────────
    if operation in ("format", "pretty", "pretty-print"):
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        if len(formatted) > _MAX_OUTPUT_CHARS:
            truncated = formatted[:_MAX_OUTPUT_CHARS]
            # Don't cut in the middle of a line
            truncated = truncated.rsplit("\n", 1)[0]
            return (
                f"{truncated}\n"
                f"... [truncated — full output is {len(formatted):,} characters]"
            )
        return formatted

    # ── minify ────────────────────────────────────────────────
    if operation in ("minify", "compact", "minimize"):
        minified = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        saved    = len(json_str) - len(minified)
        return (
            f"{minified}\n\n"
            f"[Minified: {len(minified):,} chars "
            f"(saved {saved:,} chars, "
            f"{saved / max(len(json_str), 1) * 100:.1f}% reduction)]"
        )

    # ── keys ──────────────────────────────────────────────────
    if operation in ("keys", "list keys", "list_keys"):
        if not isinstance(parsed, dict):
            raise ValueError(
                f"'keys' operation requires a JSON object (dict), "
                f"but got {type(parsed).__name__}."
            )
        keys = list(parsed.keys())
        key_list = "\n".join(f"  {i+1}. {k!r}" for i, k in enumerate(keys))
        return f"Top-level keys ({len(keys)}):\n{key_list}"

    # ── stats ─────────────────────────────────────────────────
    if operation in ("stats", "statistics", "info"):
        counts = _count_nodes(parsed)
        type_name = type(parsed).__name__
        return (
            f"JSON Statistics:\n"
            f"  Root type:    {type_name}\n"
            f"  Input size:   {len(json_str):,} characters\n"
            f"  Objects:      {counts['objects']:,}\n"
            f"  Arrays:       {counts['arrays']:,}\n"
            f"  Strings:      {counts['strings']:,}\n"
            f"  Numbers:      {counts['numbers']:,}\n"
            f"  Booleans:     {counts['booleans']:,}\n"
            f"  Nulls:        {counts['nulls']:,}\n"
            f"  Max depth:    {counts['max_depth']}"
        )

    raise ValueError(
        f"Unknown operation '{operation}'. "
        "Valid options: format, validate, minify, keys, stats."
    )


@tool
def json_formatter(json_string: str, operation: str = "format") -> str:
    """
    Validate, format, or analyse a JSON string.

    Operations:
      "format"   — pretty-print JSON with 2-space indentation (default)
      "validate" — check if the string is valid JSON
      "minify"   — compact JSON with no whitespace
      "keys"     — list the top-level keys (JSON objects only)
      "stats"    — count objects, arrays, strings, numbers, depth

    Examples:
        json_string='{"name":"Alice","age":30}', operation="format"
        json_string='[1,2,3]', operation="validate"
        json_string='{"a":1}', operation="keys"

    Args:
        json_string: The JSON string to process.
        operation:   What to do with the JSON (default: "format").

    Returns:
        The processed result as a string, or an error description.
    """
    if not json_string or not json_string.strip():
        return "Error: Please provide a JSON string to process."

    if len(json_string) > _MAX_INPUT_CHARS:
        return (
            f"Error: Input is too long ({len(json_string):,} characters). "
            f"Maximum is {_MAX_INPUT_CHARS:,} characters."
        )

    try:
        return _process(json_string.strip(), operation or "format")

    except json.JSONDecodeError as exc:
        return (
            f"Invalid JSON — cannot perform '{operation}'.\n"
            f"  Error:    {exc.msg}\n"
            f"  Position: line {exc.lineno}, column {exc.colno}"
        )
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error processing JSON: {exc}"