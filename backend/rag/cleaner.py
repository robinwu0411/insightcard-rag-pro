"""Data cleaner — removes noise, masks PII, deduplicates.

Production pipeline:
  1. Strip excessive whitespace, page headers/footers
  2. Mask sensitive info (phone, email, SSN/ID)
  3. Quality scoring (length, readability, language detection)
"""
import re
import logging

logger = logging.getLogger(__name__)

# PII patterns
PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")  # Chinese mobile
EMAIL_RE = re.compile(r"\b[\w.-]+@[\w.-]+\.\w+\b")
ID_CARD_RE = re.compile(r"\b\d{17}[\dXx]\b")  # Chinese ID card
BANK_CARD_RE = re.compile(r"\b\d{16,19}\b")  # Bank card number


def clean_text(text: str) -> str:
    """Remove noise and normalize whitespace."""
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces
    text = re.sub(r"[ \t]+", " ", text)
    # Strip trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def mask_pii(text: str) -> str:
    """Mask personally identifiable information."""
    text = PHONE_RE.sub("***-****-****", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = ID_CARD_RE.sub("[ID-***]", text)
    text = BANK_CARD_RE.sub("[CARD-****]", text)
    return text


def quality_score(text: str) -> float:
    """Score text quality 0-1. Low scores should be filtered."""
    if not text or len(text.strip()) < 20:
        return 0.0

    score = 1.0

    # Penalize too-short chunks
    if len(text) < 50:
        score -= 0.3

    # Penalize high ratio of non-alphanumeric (likely garbled)
    alpha_count = sum(c.isalnum() for c in text)
    if alpha_count / len(text) < 0.3:
        score -= 0.4

    # Penalize repeated characters (encoding artifacts)
    if re.search(r"(.)\1{10,}", text):
        score -= 0.2

    return max(0.0, score)


def clean_chunks(chunks: list) -> list:
    """Apply full cleaning pipeline to a list of Chunk objects."""
    cleaned = []
    seen_hashes = set()

    for chunk in chunks:
        # Clean text
        text = clean_text(chunk.text)

        # Mask PII
        text = mask_pii(text)

        # Quality filter
        score = quality_score(text)
        if score < 0.3:
            logger.warning(f"Low quality chunk filtered (score={score:.2f}): {chunk.metadata.get('source', '')}")
            continue

        # Dedup by content hash
        if chunk.hash in seen_hashes:
            logger.info(f"Duplicate chunk skipped: {chunk.metadata.get('source', '')}")
            continue
        seen_hashes.add(chunk.hash)

        chunk.text = text
        chunk.metadata["quality_score"] = round(score, 2)
        cleaned.append(chunk)

    logger.info(f"Cleaned {len(chunks)} chunks -> {len(cleaned)} valid chunks")
    return cleaned
