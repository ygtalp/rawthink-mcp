"""Markdown text chunker for RAWThink vault content.

Splits markdown files into chunks by section headings, with overlap
for context continuity. Extracts YAML frontmatter as metadata.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class ThoughtChunk:
    """A chunk of thought content from a vault markdown file."""

    session_id: str
    title: str
    date: str
    tags: list[str]
    section_heading: str
    chunk_text: str
    chunk_index: int
    line_start: int
    line_end: int
    content_hash: str
    source_type: str  # "session" | "qnote"

    def to_payload(self) -> dict:
        """Convert to Qdrant payload dict."""
        return {
            "session_id": self.session_id,
            "title": self.title,
            "date": self.date,
            "tags": self.tags,
            "section_heading": self.section_heading,
            "chunk_text": self.chunk_text,
            "chunk_index": self.chunk_index,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "content_hash": self.content_hash,
            "source_type": self.source_type,
        }


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from markdown text.

    Returns (metadata_dict, remaining_text).
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    remaining = text[end + 3:].strip()

    meta: dict = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Strip quotes
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]

        # Parse list values [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
            value = [i.strip().strip('"') for i in items if i.strip()]

        meta[key] = value

    return meta, remaining


def _content_hash(text: str) -> str:
    """SHA256 hash of content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class MarkdownChunker:
    """Chunks markdown files into sections with overlap.

    Chunking strategy:
    - Parse YAML frontmatter -> metadata (not embedded)
    - Split by ### headings (speaker alternation)
    - If a section is too long (> max_chars), split at paragraph boundaries
    - Overlap: last overlap_chars from previous chunk prepended as context
    - Each chunk gets contextual header with session metadata
    """

    def __init__(
        self,
        max_chars: int = 1500,
        overlap_chars: int = 100,
    ) -> None:
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_file(self, text: str, file_path: str) -> list[ThoughtChunk]:
        """Chunk a markdown file into ThoughtChunks."""
        meta, content = _parse_frontmatter(text)

        session_id = meta.get("id", file_path.split("/")[-1].replace(".md", ""))
        title = meta.get("title", "")
        date = meta.get("date", "")
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        # Determine source type from path
        source_type = "qnote" if "qnote" in file_path.lower() else "session"

        # Split content into sections by ### headings
        sections = self._split_sections(content)

        chunks: list[ThoughtChunk] = []
        chunk_index = 0
        prev_tail = ""

        for heading, section_text, line_start, line_end in sections:
            if not section_text.strip():
                continue

            # Build full text with context header
            context_header = f"[Session: {session_id}] [Date: {date}] [Tags: {', '.join(tags)}]"

            # If section is small enough, keep as one chunk
            if len(section_text) <= self.max_chars:
                full_text = f"{context_header}\n{heading}\n{prev_tail}{section_text}"
                chunks.append(ThoughtChunk(
                    session_id=session_id,
                    title=title,
                    date=date,
                    tags=tags,
                    section_heading=heading,
                    chunk_text=full_text.strip(),
                    chunk_index=chunk_index,
                    line_start=line_start,
                    line_end=line_end,
                    content_hash=_content_hash(section_text),
                    source_type=source_type,
                ))
                prev_tail = section_text[-self.overlap_chars:] + "\n" if len(section_text) > self.overlap_chars else ""
                chunk_index += 1
            else:
                # Split long sections at paragraph boundaries
                paragraphs = re.split(r"\n\n+", section_text)
                current_text = ""
                current_line = line_start

                for para in paragraphs:
                    if len(current_text) + len(para) > self.max_chars and current_text:
                        full_text = f"{context_header}\n{heading}\n{prev_tail}{current_text}"
                        para_lines = current_text.count("\n") + 1
                        chunks.append(ThoughtChunk(
                            session_id=session_id,
                            title=title,
                            date=date,
                            tags=tags,
                            section_heading=heading,
                            chunk_text=full_text.strip(),
                            chunk_index=chunk_index,
                            line_start=current_line,
                            line_end=current_line + para_lines,
                            content_hash=_content_hash(current_text),
                            source_type=source_type,
                        ))
                        prev_tail = current_text[-self.overlap_chars:] + "\n" if len(current_text) > self.overlap_chars else ""
                        chunk_index += 1
                        current_line += para_lines
                        current_text = ""

                    current_text += para + "\n\n"

                # Remaining text
                if current_text.strip():
                    full_text = f"{context_header}\n{heading}\n{prev_tail}{current_text}"
                    para_lines = current_text.count("\n") + 1
                    chunks.append(ThoughtChunk(
                        session_id=session_id,
                        title=title,
                        date=date,
                        tags=tags,
                        section_heading=heading,
                        chunk_text=full_text.strip(),
                        chunk_index=chunk_index,
                        line_start=current_line,
                        line_end=current_line + para_lines,
                        content_hash=_content_hash(current_text),
                        source_type=source_type,
                    ))
                    prev_tail = current_text[-self.overlap_chars:] + "\n" if len(current_text) > self.overlap_chars else ""
                    chunk_index += 1

        return chunks

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, str, int, int]]:
        """Split markdown by ### headings.

        Returns list of (heading, body_text, line_start, line_end).
        """
        lines = text.split("\n")
        sections: list[tuple[str, str, int, int]] = []

        current_heading = ""
        current_lines: list[str] = []
        current_start = 1

        for i, line in enumerate(lines, 1):
            if line.startswith("### "):
                # Save previous section
                if current_lines:
                    body = "\n".join(current_lines)
                    sections.append((current_heading, body, current_start, i - 1))
                current_heading = line.strip()
                current_lines = []
                current_start = i
            elif line.startswith("## "):
                # Also split on ## headings
                if current_lines:
                    body = "\n".join(current_lines)
                    sections.append((current_heading, body, current_start, i - 1))
                current_heading = line.strip()
                current_lines = []
                current_start = i
            else:
                current_lines.append(line)

        # Final section
        if current_lines:
            body = "\n".join(current_lines)
            sections.append((current_heading, body, current_start, len(lines)))

        return sections
