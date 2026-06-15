"""
app/tools/wikipedia_search.py
──────────────────────────────
LangChain Wikipedia Search Tool.

Fetches a short summary for a topic from the Wikipedia REST API.

API used:
  https://en.wikipedia.org/api/rest_v1/page/summary/{title}
  This endpoint returns a JSON object with a plain-text `extract` field
  containing the introductory paragraph of the article.  No API key required.

Design decisions:
  • Uses stdlib urllib only — no additional dependencies.
  • Graceful failure: network errors, missing articles, and API failures
    all return human-readable error strings rather than raising exceptions,
    so the LLM can relay the failure to the user and continue the run.
  • Title normalisation: spaces are replaced with underscores and the
    string is URL-encoded to handle special characters.
  • Summary is truncated to _MAX_SUMMARY_CHARS to keep tool output within
    a reasonable token budget for the LLM context window.
  • A User-Agent header is sent per Wikipedia's API etiquette guidelines.

Network availability:
  The tool handles `urllib.error.URLError` gracefully.  If the Wikipedia
  API is unreachable (e.g. in sandboxed or offline environments), it
  returns a clear error message rather than crashing the agent run.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from langchain_core.tools import tool


_WIKIPEDIA_API   = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
_USER_AGENT      = "AgentWatch/1.0 (educational AI observability project)"
_TIMEOUT_SECONDS = 8
_MAX_SUMMARY_CHARS = 600


def _fetch_summary(title: str) -> str:
    """
    Fetch the Wikipedia summary for a given article title.

    Normalises the title (spaces → underscores), URL-encodes it, and
    calls the Wikipedia REST summary endpoint.

    Args:
        title: Article title to look up (e.g. "Python programming language").

    Returns:
        The article summary text, truncated to _MAX_SUMMARY_CHARS.

    Raises:
        urllib.error.HTTPError:  Article not found (404) or server error.
        urllib.error.URLError:   Network unreachable.
        ValueError:              API returned unexpected JSON structure.
    """
    # Normalise: strip whitespace, replace spaces with underscores
    normalised = title.strip().replace(" ", "_")
    encoded    = urllib.parse.quote(normalised, safe="")
    url        = _WIKIPEDIA_API.format(encoded)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept":     "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as response:
        data    = json.loads(response.read().decode("utf-8"))

    # The REST summary endpoint returns `extract` (plain text) and
    # `extract_html` (HTML version).  We use the plain text.
    extract = data.get("extract", "").strip()
    title   = data.get("title", title)
    url_out = data.get("content_urls", {}).get("desktop", {}).get("page", "")

    if not extract:
        raise ValueError(f"No extract found for '{title}'.")

    # Truncate long extracts and indicate truncation
    if len(extract) > _MAX_SUMMARY_CHARS:
        extract = extract[:_MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"

    result = f"Wikipedia: {title}\n\n{extract}"
    if url_out:
        result += f"\n\nSource: {url_out}"

    return result


@tool
def wikipedia_search(topic: str) -> str:
    """
    Search Wikipedia and return a short summary of the topic.

    Fetches the introductory paragraph of the Wikipedia article for
    the given topic.  Returns a plain-text summary of up to ~600 characters.

    If Wikipedia is unavailable or the article does not exist, returns
    a descriptive error message so the agent can inform the user.

    Args:
        topic: The topic or article title to look up on Wikipedia.
               Examples: "Python programming language", "Albert Einstein",
               "Machine learning", "Great Wall of China"

    Returns:
        A plain-text summary from Wikipedia, or an error message.
    """
    if not topic or not topic.strip():
        return "Error: Please provide a topic to search for."

    if len(topic.strip()) > 200:
        return "Error: Topic is too long. Please use a shorter search term."

    try:
        return _fetch_summary(topic.strip())

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return (
                f"No Wikipedia article found for '{topic}'. "
                "Try a more specific or differently-worded search term."
            )
        return (
            f"Wikipedia returned an error (HTTP {exc.code}) for '{topic}'. "
            "Please try again later."
        )

    except urllib.error.URLError as exc:
        return (
            f"Could not connect to Wikipedia: {exc.reason}. "
            "The service may be unavailable in this environment."
        )

    except TimeoutError:
        return f"Wikipedia request timed out after {_TIMEOUT_SECONDS}s. Please try again."

    except ValueError as exc:
        return f"Error: {exc}"

    except Exception as exc:
        return f"Error searching Wikipedia for '{topic}': {exc}"