"""
app/tools/datetime_tool.py
───────────────────────────
LangChain DateTime Tool.

Provides date and time information without any external API calls.
All operations use Python's stdlib datetime module.

Supported queries (passed as the `query` argument):
  "date"       — current local date (YYYY-MM-DD)
  "time"       — current local time (HH:MM:SS)
  "utc"        — current UTC date and time
  "day"        — current day of the week
  "datetime"   — full local date and time
  "all"        — all of the above in one response
  "timestamp"  — Unix timestamp (seconds since epoch)

The tool is stateless and deterministic within a second — the same
query issued twice within the same second returns identical output.
"""

from datetime import datetime, timezone
from langchain_core.tools import tool


def _format_datetime_response(query: str) -> str:
    """
    Compute and format the requested datetime information.

    Args:
        query: One of the supported query strings (case-insensitive).

    Returns:
        A formatted string with the requested date/time information.

    Raises:
        ValueError: query is not a recognised keyword.
    """
    now_local = datetime.now()
    now_utc   = datetime.now(tz=timezone.utc)

    query = query.strip().lower()

    if query in ("date",):
        return f"Current date: {now_local.strftime('%Y-%m-%d')} ({now_local.strftime('%B %d, %Y')})"

    if query in ("time",):
        return f"Current local time: {now_local.strftime('%H:%M:%S')}"

    if query in ("utc", "utc time", "utcnow"):
        return (
            f"Current UTC date and time: "
            f"{now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

    if query in ("day", "weekday", "day of week"):
        return f"Today is {now_local.strftime('%A')} ({now_local.strftime('%Y-%m-%d')})"

    if query in ("datetime", "now", "current"):
        return (
            f"Current date and time: "
            f"{now_local.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({now_local.strftime('%A, %B %d, %Y')})"
        )

    if query in ("timestamp", "unix", "epoch"):
        return f"Unix timestamp: {int(now_utc.timestamp())}"

    if query in ("all",):
        return (
            f"Date:      {now_local.strftime('%Y-%m-%d')}\n"
            f"Time:      {now_local.strftime('%H:%M:%S')} (local)\n"
            f"UTC:       {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"Day:       {now_local.strftime('%A')}\n"
            f"Timestamp: {int(now_utc.timestamp())}"
        )

    raise ValueError(
        f"Unknown query '{query}'. "
        "Valid options: date, time, utc, day, datetime, timestamp, all."
    )


@tool
def datetime_tool(query: str) -> str:
    """
    Get the current date, time, or day of the week.

    Supported queries:
      "date"      — current date (YYYY-MM-DD)
      "time"      — current local time (HH:MM:SS)
      "utc"       — current UTC date and time
      "day"       — current day of the week
      "datetime"  — full local date and time
      "timestamp" — Unix timestamp (seconds since epoch)
      "all"       — all of the above

    Args:
        query: What date/time information to return (see above).

    Returns:
        A formatted string with the requested date/time information.
    """
    if not query or not query.strip():
        return "Error: Please provide a query. Options: date, time, utc, day, datetime, timestamp, all."

    try:
        return _format_datetime_response(query)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error retrieving date/time information: {exc}"