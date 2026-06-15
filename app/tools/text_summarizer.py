"""
app/tools/text_summarizer.py
─────────────────────────────
LangChain Text Summarizer Tool.

Produces an extractive summary of provided text without making any
additional LLM API calls.  "Extractive" means the summary is built
from actual sentences in the source text, ranked by importance — as
opposed to "abstractive" summarisation, which generates new text.

Algorithm (TF-score sentence ranking):
  1. Tokenise the text into sentences (split on ". ", "! ", "? ").
  2. Compute a word-frequency table for non-stopword terms.
  3. Score each sentence by the sum of its word frequencies.
  4. Select the top N sentences (default: 3) in their original order.
  5. Return them joined as the summary.

Limitations:
  • Works best on factual, informational prose.
  • Very short texts (< 3 sentences) are returned unchanged.
  • Does not handle bullet lists, tables, or code blocks specially.
  • English stopword list only — other languages are summarised but
    with lower quality because stopwords are not filtered.

Input constraints:
  • Maximum 10,000 characters to keep processing fast.
  • Minimum 20 characters to be worth summarising.
"""

import re
from langchain_core.tools import tool


_MAX_INPUT_CHARS  = 10_000
_DEFAULT_SENTENCES = 3

# Common English stopwords — excluded from word-frequency scoring
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "was", "are", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall",
    "it", "its", "this", "that", "these", "those", "i", "you", "he",
    "she", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "our", "their", "not", "no", "so", "as", "if",
    "then", "than", "when", "where", "which", "who", "what", "how",
    "also", "just", "more", "some", "such", "can", "into", "up",
    "out", "about", "after", "before", "there", "here", "all",
})


def _tokenise_sentences(text: str) -> list[str]:
    """
    Split text into sentences using common punctuation boundaries.

    Handles ". ", "! ", "? " as sentence endings.  Consecutive
    whitespace is collapsed.  Empty strings are filtered out.
    """
    # Normalise line endings and collapse whitespace runs
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()

    # Split on sentence-ending punctuation followed by a space
    raw = re.split(r"(?<=[.!?])\s+", text)

    # Filter out very short "sentences" (likely abbreviations or bullets)
    return [s.strip() for s in raw if len(s.strip()) > 15]


def _word_frequencies(sentences: list[str]) -> dict[str, int]:
    """
    Build a word-frequency table from all words across all sentences,
    excluding stopwords and single-character tokens.
    """
    freq: dict[str, int] = {}
    for sentence in sentences:
        for word in re.findall(r"[a-zA-Z]+", sentence.lower()):
            if word not in _STOPWORDS and len(word) > 1:
                freq[word] = freq.get(word, 0) + 1
    return freq


def _score_sentences(
    sentences: list[str],
    freq: dict[str, int],
) -> list[tuple[int, float]]:
    """
    Score each sentence by summing the frequencies of its content words.

    Returns a list of (original_index, score) tuples.
    """
    scored: list[tuple[int, float]] = []
    for idx, sentence in enumerate(sentences):
        words  = re.findall(r"[a-zA-Z]+", sentence.lower())
        score  = sum(freq.get(w, 0) for w in words if w not in _STOPWORDS)
        # Normalise by sentence length to avoid bias toward long sentences
        length = max(len(words), 1)
        scored.append((idx, score / length))
    return scored


def _extractive_summarise(text: str, num_sentences: int) -> str:
    """
    Build an extractive summary of `text` using `num_sentences` top-ranked
    sentences returned in their original document order.

    Args:
        text:          Source text to summarise.
        num_sentences: Number of sentences to include in the summary.

    Returns:
        Summary string, or the original text if it is too short to summarise.
    """
    sentences = _tokenise_sentences(text)

    if len(sentences) <= num_sentences:
        # Too short to meaningfully summarise — return as-is
        return text.strip()

    freq   = _word_frequencies(sentences)
    scored = _score_sentences(sentences, freq)

    # Pick the top-N sentences by score
    top_indices = sorted(
        sorted(scored, key=lambda x: x[1], reverse=True)[:num_sentences],
        key=lambda x: x[0],  # restore original document order
    )

    summary = " ".join(sentences[idx] for idx, _ in top_indices)
    return summary.strip()


@tool
def text_summarizer(text: str) -> str:
    """
    Summarise a block of text by extracting its most important sentences.

    Uses a word-frequency ranking algorithm (no additional LLM calls).
    Returns the top 3 most informative sentences from the source text,
    in their original order.

    Best suited for factual prose, articles, and paragraphs.
    Not designed for code, tables, or bullet-list content.

    Args:
        text: The text to summarise. Maximum 10,000 characters.

    Returns:
        A concise extractive summary of the most important sentences,
        or an error message if the input is invalid.
    """
    if not text or not text.strip():
        return "Error: Please provide text to summarise."

    text = text.strip()

    if len(text) < 20:
        return "Error: Text is too short to summarise (minimum 20 characters)."

    if len(text) > _MAX_INPUT_CHARS:
        return (
            f"Error: Text is too long ({len(text):,} characters). "
            f"Maximum is {_MAX_INPUT_CHARS:,} characters."
        )

    try:
        summary      = _extractive_summarise(text, _DEFAULT_SENTENCES)
        input_words  = len(re.findall(r"\S+", text))
        output_words = len(re.findall(r"\S+", summary))

        return (
            f"Summary ({output_words} words from {input_words}-word input):\n\n"
            f"{summary}"
        )

    except Exception as exc:
        return f"Error summarising text: {exc}"