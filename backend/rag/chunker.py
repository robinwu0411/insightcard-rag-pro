"""Markdown-aware chunker — production version with metadata.

Splits documents by markdown headers, preserving section context.
Each chunk carries source_file, section_path, title, timestamp for traceability.
"""
import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return hashlib.md5(self.text.encode()).hexdigest()[:16]


def _extract_title(markdown: str) -> str:
    for line in markdown.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled"


def chunk_markdown(
    markdown: str,
    source_file: str = "",
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[Chunk]:
    doc_title = _extract_title(markdown)
    timestamp = datetime.now(timezone.utc).isoformat()

    sections: list[tuple[str, str]] = []
    current_section = doc_title
    current_lines: list[str] = []

    for line in markdown.split("\n"):
        header_match = re.match(r"^(#{1,4})\s+(.*)", line)
        if header_match:
            if current_lines:
                sections.append((current_section, "\n".join(current_lines).strip()))
                current_lines = []
            level = len(header_match.group(1))
            heading_text = header_match.group(2).strip()
            if level == 1:
                current_section = heading_text
            else:
                current_section = f"{current_section} > {heading_text}"
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_section, "\n".join(current_lines).strip()))

    chunks: list[Chunk] = []
    buffer = ""
    buffer_section = ""

    for section_path, text in sections:
        if not text:
            continue
        if buffer and len(buffer) + len(text) > chunk_size:
            chunks.append(Chunk(
                text=buffer.strip(),
                metadata={
                    "source": source_file,
                    "section": buffer_section,
                    "title": doc_title,
                    "timestamp": timestamp,
                },
            ))
            tail = buffer[-overlap:] if len(buffer) > overlap else ""
            buffer = tail + "\n" + text if tail else text
            buffer_section = section_path
        else:
            if not buffer:
                buffer_section = section_path
            buffer = buffer + "\n" + text if buffer else text

    if buffer.strip():
        chunks.append(Chunk(
            text=buffer.strip(),
            metadata={
                "source": source_file,
                "section": buffer_section,
                "title": doc_title,
                "timestamp": timestamp,
            },
        ))

    return chunks


def chunk_file(file_path: Path, chunk_size: int = 512, overlap: int = 64) -> list[Chunk]:
    """Read a file and chunk it. Supports .md, .txt, .html."""
    content = file_path.read_text(encoding="utf-8")
    source_name = file_path.name

    if file_path.suffix == ".md":
        return chunk_markdown(content, source_file=source_name, chunk_size=chunk_size, overlap=overlap)
    elif file_path.suffix == ".txt":
        return chunk_markdown(f"# {source_name}\n\n{content}", source_file=source_name, chunk_size=chunk_size, overlap=overlap)
    elif file_path.suffix == ".html":
        return chunk_markdown(f"# {source_name}\n\n{content}", source_file=source_name, chunk_size=chunk_size, overlap=overlap)
    else:
        return chunk_markdown(f"# {source_name}\n\n{content}", source_file=source_name, chunk_size=chunk_size, overlap=overlap)


def ingest_directory(docs_dir: str, chunk_size: int = 512, overlap: int = 64) -> list[Chunk]:
    """Read all supported files from a directory and chunk them."""
    docs_path = Path(docs_dir)
    all_chunks: list[Chunk] = []

    for f in sorted(docs_path.glob("*")):
        if f.suffix in (".md", ".txt", ".html"):
            file_chunks = chunk_file(f, chunk_size=chunk_size, overlap=overlap)
            all_chunks.extend(file_chunks)
            print(f"  [{f.name}] -> {len(file_chunks)} chunks")

    return all_chunks
