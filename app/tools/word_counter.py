"""
app/tools/word_counter.py
──────────────────────────
LangChain Word Counter Tool.

Counts words, characters, and sentences in provided text.
All counting is done with stdlib — no external dependencies.

Definitions used:
  Words:      Whitespace-delimited tokens (same as str.split()).
              This matches how most people intuitively count words
              and is consistent with word-processor word counts.
  Characters: Total number of Unicode code points in the text,
              including spaces and punctuation (len(text)).
  Characters (no spaces): len(text.replace(" ", ""))
  Sentences:  Segments ending in ". ", "! ", "? " or at end of text.
              Abbreviations and decimal numbers cause minor over-counting,
              which is acceptable for a lightweight tool.
  Paragraphs: Blocks separated by one or more blank lines.
  Unique words: Case-insensitive distinct words (punctuation stripped).
  Average word length: mean number of characters per word.
"""

import re
from langchain_core.tools import tool


_MAX_INPUT_CHARS = 50_000   # 50 KB — keeps counting fast


def _count_text(text: str) -> str:
    """
    Compute all text statistics and format them as a readable report.

    Args:
        text: Source text to analyse.

    Returns:
        A formatted multi-line statistics report.
    """
    # ── Word count ────────────────────────────────────────────
    words = text.split()
    word_count = len(words)

    # ── Character counts ──────────────────────────────────────
    char_count          = len(text)
    char_no_spaces      = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

    # ── Sentence count ────────────────────────────────────────
    # Split on ./?/! followed by whitespace or end of string.
    # Minimum length of 2 chars to avoid counting "Dr." as a sentence.
    raw_sentences = re.split(r"(?<=[.!?])(?:\s|$)", text.strip())
    sentence_count = sum(1 for s in raw_sentences if len(s.strip()) > 2)
    sentence_count = max(sentence_count, 1)   # at least 1 sentence if any text exists

    # ── Paragraph count ───────────────────────────────────────
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    paragraph_count = max(len(paragraphs), 1)

    # ── Unique words (case-insensitive, punctuation stripped) ──
    clean_words   = [re.sub(r"[^a-zA-Z0-9'-]", "", w).lower() for w in words]
    clean_words   = [w for w in clean_words if w]
    unique_words  = len(set(clean_words))

    # ── Average word length ───────────────────────────────────
    if clean_words:
        avg_word_len = sum(len(w) for w in clean_words) / len(clean_words)
    else:
        avg_word_len = 0.0

    # ── Reading time estimate ─────────────────────────────────
    # Average adult reads ~238 words per minute (source: many reading-speed studies).
    wpm           = 238
    reading_secs  = (word_count / wpm) * 60 if word_count > 0 else 0
    if reading_secs < 60:
        reading_time = f"~{int(reading_secs)} seconds"
    else:
        reading_time = f"~{reading_secs / 60:.1f} minutes"

    return (
        f"Text Analysis:\n"
        f"  Words:                 {word_count:,}\n"
        f"  Unique words:          {unique_words:,}\n"
        f"  Characters (total):    {char_count:,}\n"
        f"  Characters (no spaces):{char_no_spaces:,}\n"
        f"  Sentences:             {sentence_count:,}\n"
        f"  Paragraphs:            {paragraph_count:,}\n"
        f"  Avg word length:       {avg_word_len:.1f} characters\n"
        f"  Est. reading time:     {reading_time}"
    )


@tool
def word_counter(text: str) -> str:
    """
    Count words, characters, and sentences in the provided text.

    Returns a full text analysis including:
      - Word count (total and unique)
      - Character count (with and without spaces)
      - Sentence count
      - Paragraph count
      - Average word length
      - Estimated reading time

    Args:
        text: The text to analyse. Maximum 50,000 characters.

    Returns:
        A formatted text analysis report.
    """
    if not text or not text.strip():
        return "Error: Please provide text to analyse."

    if len(text) > _MAX_INPUT_CHARS:
        return (
            f"Error: Text is too long ({len(text):,} characters). "
            f"Maximum is {_MAX_INPUT_CHARS:,} characters."
        )

    try:
        return _count_text(text)
    except Exception as exc:
        return f"Error counting text: {exc}"