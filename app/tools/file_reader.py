"""
app/tools/file_reader.py
─────────────────────────
LangChain File Reader Tool — sandboxed to a safe directory.

Security model:
  This tool is deliberately the most dangerous of the three because
  it reads from the filesystem.  Real agents should NOT have file
  access unless absolutely necessary — which is exactly why it exists
  in AgentWatch: to demonstrate governance violations when an agent
  without permission tries to call it.

  Two security layers are applied:
    1. Path sandboxing — all paths are resolved to an absolute path
       and checked against an allowed base directory.  Path traversal
       attacks like "../../etc/passwd" are rejected.
    2. Extension whitelist — only safe plain-text file types are
       readable.  Binary files (.exe, .pkl, etc.) are blocked.

  The sandbox directory defaults to a `sandbox/` folder inside the
  project root.  Only files explicitly placed there by an admin are
  readable by agents.

  In a real system you would:
    • Mount an isolated volume with read-only content.
    • Enforce file size limits (current: 50 KB).
    • Log every file access for the compliance audit trail.
    • Perhaps encrypt sensitive files at rest and decrypt with agent-
      specific keys.

Sandbox setup:
  The tool creates the sandbox directory on first use if it doesn't
  exist, and populates it with a sample file so new deployments have
  something to read immediately.
"""

import os
import pathlib
from langchain_core.tools import tool


# ── Sandbox configuration ─────────────────────────────────────────────────────

# The sandbox root is relative to the project root (two levels up from
# this file's location: app/tools/ → app/ → project root).
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()
_SANDBOX_DIR  = _PROJECT_ROOT / "sandbox"

# Maximum file size in bytes — prevents agents from reading huge files
# that would overflow the LLM context window.
_MAX_FILE_SIZE_BYTES = 50 * 1024   # 50 KB

# Allowed file extensions — plain text only.
# Blocking .py, .sh, .bat prevents accidental source code leaks.
# Blocking .json, .yaml, .env prevents config/secret leaks.
_ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".rst"}


def _ensure_sandbox_exists() -> None:
    """
    Create the sandbox directory and a sample file if they don't exist.

    Called lazily on first tool invocation so the directory is always
    present without requiring a manual setup step.
    """
    _SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

    sample_file = _SANDBOX_DIR / "readme.txt"
    if not sample_file.exists():
        sample_file.write_text(
            "AgentWatch Sandbox\n"
            "==================\n\n"
            "This is the sandbox directory for the file_reader tool.\n"
            "Place .txt, .md, .csv, .log, or .rst files here to make\n"
            "them available to agents with file_reader permission.\n\n"
            "Files outside this directory cannot be accessed.\n",
            encoding="utf-8",
        )

    # Write a second sample so agents have something interesting to read.
    report_file = _SANDBOX_DIR / "agent_report.md"
    if not report_file.exists():
        report_file.write_text(
            "# Agent Performance Report\n\n"
            "## Summary\n"
            "- Total runs: 42\n"
            "- Successful runs: 38\n"
            "- Governance violations: 4\n\n"
            "## Top Tools Used\n"
            "1. calculator  — 28 calls\n"
            "2. weather     — 19 calls\n"
            "3. file_reader —  7 calls\n\n"
            "## Notes\n"
            "All violations were caused by agents attempting to call\n"
            "tools outside their allowed_tools list.\n",
            encoding="utf-8",
        )


def _read_file_safe(filename: str) -> str:
    """
    Read a file from the sandbox with full security validation.

    Security checks applied (in order):
      1. Empty filename guard.
      2. Extension whitelist check.
      3. Absolute path resolution (resolves .., symlinks, etc.).
      4. Sandbox containment check — resolved path must start with
         the sandbox directory absolute path.
      5. File existence and type check.
      6. File size guard — rejects files over _MAX_FILE_SIZE_BYTES.

    Args:
        filename: Relative filename within the sandbox (e.g. "readme.txt").
                  Must NOT contain path separators like "/" or "..".

    Returns:
        The file content as a UTF-8 string.

    Raises:
        ValueError: Any security check failed, or file not found.
    """
    _ensure_sandbox_exists()

    filename = filename.strip()
    if not filename:
        raise ValueError("Filename cannot be empty.")

    # ── Check 2: Extension whitelist ──────────────────────────────────
    suffix = pathlib.Path(filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{suffix}' is not allowed. "
            f"Allowed types: {sorted(_ALLOWED_EXTENSIONS)}"
        )

    # ── Check 3: Resolve absolute path ────────────────────────────────
    # Joining with the sandbox dir and calling resolve() expands any
    # ".." components and resolves symlinks to the canonical path.
    candidate = (_SANDBOX_DIR / filename).resolve()

    # ── Check 4: Sandbox containment ─────────────────────────────────
    # str.startswith() on resolved paths prevents directory traversal.
    # We append os.sep to the sandbox path so "/sandbox2/evil.txt"
    # is not incorrectly accepted as a child of "/sandbox".
    sandbox_prefix = str(_SANDBOX_DIR.resolve()) + os.sep
    if not str(candidate).startswith(sandbox_prefix):
        # Don't leak the actual sandbox path in the error message —
        # it could help an attacker identify the filesystem layout.
        raise ValueError(
            f"Access denied: '{filename}' is outside the allowed directory."
        )

    # ── Check 5: Existence and type ───────────────────────────────────
    if not candidate.exists():
        available = [f.name for f in _SANDBOX_DIR.iterdir() if f.is_file()]
        raise ValueError(
            f"File '{filename}' not found. "
            f"Available files: {available or ['(none)']}"
        )
    if not candidate.is_file():
        raise ValueError(f"'{filename}' is not a file.")

    # ── Check 6: Size guard ───────────────────────────────────────────
    size = candidate.stat().st_size
    if size > _MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File '{filename}' is too large ({size:,} bytes). "
            f"Maximum allowed: {_MAX_FILE_SIZE_BYTES:,} bytes."
        )

    # ── Read ──────────────────────────────────────────────────────────
    # errors="replace" substitutes the Unicode replacement character
    # for any bytes that can't be decoded, preventing a UnicodeDecodeError
    # from crashing the tool on partially binary files.
    content = candidate.read_text(encoding="utf-8", errors="replace")

    # Prepend a metadata header so the LLM knows what it's reading.
    return (
        f"=== File: {filename} ({size:,} bytes) ===\n\n"
        f"{content}"
    )


# ── LangChain Tool ────────────────────────────────────────────────────────────

@tool
def file_reader(filename: str) -> str:
    """
    Read a text file from the agent sandbox directory.

    Only files in the designated sandbox folder are accessible.
    Supported file types: .txt, .md, .csv, .log, .rst
    Maximum file size: 50 KB

    To list available files, call with filename="list".

    Args:
        filename: Name of the file to read (e.g. "readme.txt", "report.md").
                  Use "list" to see all available files.

    Returns:
        The file contents as a string, or a list of available files.
    """
    # Special command: list available files.
    if filename.strip().lower() in ("list", "ls", "dir"):
        _ensure_sandbox_exists()
        files = [f.name for f in _SANDBOX_DIR.iterdir() if f.is_file()]
        if not files:
            return "Sandbox is empty. No files are currently available."
        file_list = "\n".join(f"  - {f}" for f in sorted(files))
        return f"Files available in sandbox:\n{file_list}"

    try:
        return _read_file_safe(filename)
    except ValueError as exc:
        return f"Error: {exc}"
    except PermissionError:
        return f"Error: Permission denied reading '{filename}'."
    except Exception as exc:
        return f"Error reading '{filename}': {exc}"