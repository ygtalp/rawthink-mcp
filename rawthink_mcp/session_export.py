"""JSONL session export pipeline.

Converts Claude Code JSONL conversations to:
- Clean dialog markdown (vault/sessions/)
- Full transcript markdown (vault/archive/raw/)
- HTML + PDF (vault/outputs/)

Usage:
    python session_export.py <jsonl_path> [--title TITLE] [--slug SLUG] [--vault-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def parse_jsonl(path: Path) -> list[dict]:
    """Read JSONL file, return list of entries sorted by timestamp."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    # Sort by timestamp if available
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


def extract_user_text(entry: dict) -> str | None:
    """Extract text from a user entry."""
    if entry.get("type") != "user":
        return None
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        text = content.strip()
        # Skip system-reminder only messages
        if text and not (text.startswith("<system-reminder>") and text.endswith("</system-reminder>")):
            return text
        return None
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                t = c["text"]
                # Skip system-reminder blocks
                if not (t.strip().startswith("<system-reminder>") and t.strip().endswith("</system-reminder>")):
                    parts.append(t)
            elif isinstance(c, str):
                parts.append(c)
            # Skip tool_result blocks in clean dialog
        text = "\n".join(parts).strip()
        # Remove inline system-reminder tags
        text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL).strip()
        return text or None
    return None


def extract_assistant_parts(entry: dict) -> dict:
    """Extract thinking, text, and tool_use from assistant entry.

    Returns: {"thinking": [...], "text": [...], "tool_use": [...]}
    """
    if entry.get("type") != "assistant":
        return {"thinking": [], "text": [], "tool_use": []}

    msg = entry.get("message", {})
    parts = {"thinking": [], "text": [], "tool_use": []}

    for c in msg.get("content", []):
        if not isinstance(c, dict):
            continue
        if c.get("type") == "thinking":
            parts["thinking"].append(c.get("thinking", ""))
        elif c.get("type") == "text":
            parts["text"].append(c.get("text", ""))
        elif c.get("type") == "tool_use":
            parts["tool_use"].append({
                "name": c.get("name", ""),
                "input": c.get("input", {}),
            })
    return parts


def extract_tool_results(entry: dict) -> list[str]:
    """Extract tool results from user entries (tool_result content blocks)."""
    if entry.get("type") != "user":
        return []
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if not isinstance(content, list):
        return []
    results = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "tool_result":
            rc = c.get("content", "")
            if isinstance(rc, str):
                results.append(rc)
            elif isinstance(rc, list):
                for item in rc:
                    if isinstance(item, dict) and item.get("type") == "text":
                        results.append(item["text"])
    return results


def generate_session_id(vault_dir: Path, date: str) -> str:
    """Generate next session ID for the given date."""
    sessions_dir = vault_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    existing = list(sessions_dir.glob(f"{date}_*.md"))
    seq = len(existing) + 1
    return f"{date}_{seq:03d}"


def slugify(text: str, max_len: int = 40) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len]


def _quote_multiline(text: str, prefix: str = "> ") -> str:
    """Quote each line of text with the given prefix."""
    lines = text.split("\n")
    return "\n".join(f"{prefix}{line}" for line in lines)


def build_clean_dialog(entries: list[dict], session_id: str, title: str,
                       date: str, tags: list[str]) -> str:
    """Build clean dialog markdown with YAML frontmatter."""
    lines = [
        "---",
        f'id: "{session_id}"',
        f'title: "{title}"',
        f"date: {date}",
        f'tags: {json.dumps(tags, ensure_ascii=False)}',
        "status: completed",
        "---",
        "",
    ]

    for entry in entries:
        # User message
        user_text = extract_user_text(entry)
        if user_text:
            lines.append("### User")
            lines.append("")
            lines.append(_quote_multiline(user_text))
            lines.append("")
            lines.append("---")
            lines.append("")
            continue

        # Assistant message
        if entry.get("type") == "assistant":
            parts = extract_assistant_parts(entry)

            # Thinking blocks (collapsible)
            for thinking in parts["thinking"]:
                if thinking.strip():
                    lines.append("<details><summary>Thinking...</summary>")
                    lines.append("")
                    lines.append(thinking.strip())
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

            # Text response
            for text in parts["text"]:
                if text.strip():
                    lines.append("### Assistant")
                    lines.append("")
                    lines.append(text.strip())
                    lines.append("")
                    lines.append("---")
                    lines.append("")

    return "\n".join(lines)


def build_full_transcript(entries: list[dict], session_id: str, title: str,
                          date: str) -> str:
    """Build full transcript markdown with everything included."""
    lines = [
        "---",
        f'id: "{session_id}"',
        f'title: "{title} (Full Transcript)"',
        f"date: {date}",
        "type: full-transcript",
        "---",
        "",
    ]

    for entry in entries:
        etype = entry.get("type", "")
        ts = entry.get("timestamp", "")

        if etype == "user":
            user_text = extract_user_text(entry)
            tool_results = extract_tool_results(entry)

            if user_text:
                lines.append(f"### User `{ts}`")
                lines.append("")
                lines.append(_quote_multiline(user_text))
                lines.append("")

            for tr in tool_results:
                lines.append("```result")
                lines.append(tr[:2000])  # Truncate very long results
                lines.append("```")
                lines.append("")

        elif etype == "assistant":
            parts = extract_assistant_parts(entry)

            for thinking in parts["thinking"]:
                if thinking.strip():
                    lines.append("<details><summary>Thinking...</summary>")
                    lines.append("")
                    lines.append(thinking.strip())
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

            for text in parts["text"]:
                if text.strip():
                    lines.append(f"### Assistant `{ts}`")
                    lines.append("")
                    lines.append(text.strip())
                    lines.append("")

            for tool in parts["tool_use"]:
                lines.append("```tool")
                lines.append(f"{tool['name']}({json.dumps(tool['input'], ensure_ascii=False, indent=2)[:500]})")
                lines.append("```")
                lines.append("")

        elif etype == "progress":
            data = entry.get("data", {})
            hook_name = data.get("hookName", data.get("type", ""))
            lines.append(f"> [progress: {hook_name}]")
            lines.append("")

        elif etype == "system":
            subtype = entry.get("subtype", "")
            lines.append(f"> [system: {subtype}]")
            lines.append("")

    return "\n".join(lines)


def build_html(md_content: str, title: str) -> str:
    """Convert markdown to styled HTML."""
    import markdown as md

    html_body = md.markdown(md_content, extensions=["extra", "codehilite", "toc"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{ --bg: #1e1e2e; --fg: #cdd6f4; --surface: #313244; --accent: #89b4fa; --dim: #6c7086; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--fg);
               max-width: 800px; margin: 0 auto; padding: 2rem; line-height: 1.7; }}
        h1 {{ color: var(--accent); border-bottom: 2px solid var(--surface); padding-bottom: 0.5rem; }}
        h3 {{ color: var(--accent); margin-top: 2rem; }}
        blockquote {{ border-left: 3px solid var(--accent); padding-left: 1rem; color: var(--fg);
                     background: var(--surface); padding: 0.5rem 1rem; border-radius: 4px; }}
        hr {{ border: none; border-top: 1px solid var(--surface); margin: 1.5rem 0; }}
        code {{ background: var(--surface); padding: 0.2rem 0.4rem; border-radius: 3px; font-size: 0.9em; }}
        pre {{ background: var(--surface); padding: 1rem; border-radius: 6px; overflow-x: auto; }}
        pre code {{ background: none; padding: 0; }}
        details {{ background: var(--surface); border-radius: 6px; padding: 0.5rem 1rem; margin: 0.5rem 0; }}
        summary {{ cursor: pointer; color: var(--dim); font-style: italic; }}
        a {{ color: var(--accent); }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {html_body}
</body>
</html>"""


def build_pdf(html_content: str, output_path: Path) -> bool:
    """Convert HTML to PDF using weasyprint. Returns True on success."""
    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(str(output_path))
        return True
    except ImportError:
        print("weasyprint not available, skipping PDF generation", file=sys.stderr)
        return False
    except Exception as e:
        print(f"PDF generation failed: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Export Claude Code JSONL to vault formats")
    parser.add_argument("jsonl_path", help="Path to JSONL conversation file")
    parser.add_argument("--title", default="", help="Session title")
    parser.add_argument("--slug", default="", help="URL slug for filename")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--vault-dir", default="vault", help="Vault directory path")
    parser.add_argument("--reindex", action="store_true", help="Reindex vault after export")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl_path)
    if not jsonl_path.exists():
        print(f"Error: {jsonl_path} not found", file=sys.stderr)
        sys.exit(1)

    vault_dir = Path(args.vault_dir).resolve()
    today = datetime.now().strftime("%Y-%m-%d")
    session_id = generate_session_id(vault_dir, today)

    # Parse
    entries = parse_jsonl(jsonl_path)

    # Title and slug
    title = args.title
    if not title:
        for e in entries:
            text = extract_user_text(e)
            if text:
                title = text[:60].strip()
                break
        title = title or "Untitled Session"

    slug = args.slug or slugify(title)
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    # Generate outputs
    clean_md = build_clean_dialog(entries, session_id, title, today, tags)
    full_md = build_full_transcript(entries, session_id, title, today)
    html = build_html(clean_md, title)

    # Write files
    sessions_dir = vault_dir / "sessions"
    archive_dir = vault_dir / "archive" / "raw"
    outputs_dir = vault_dir / "outputs"
    for d in [sessions_dir, archive_dir, outputs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    clean_path = sessions_dir / f"{session_id}_{slug}.md"
    full_path = archive_dir / f"{session_id}_full-transcript.md"
    html_path = outputs_dir / f"{session_id}_session.html"
    pdf_path = outputs_dir / f"{session_id}_session.pdf"

    clean_path.write_text(clean_md, encoding="utf-8")
    full_path.write_text(full_md, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")

    pdf_ok = build_pdf(html, pdf_path)

    # Reindex
    if args.reindex:
        try:
            from .indexer import Indexer
            idx = Indexer()
            idx.initialize()
            count = idx.index_vault()
        except Exception as e:
            print(f"Reindex failed: {e}", file=sys.stderr)
            count = -1
    else:
        count = None

    # Output result as JSON for the skill to parse
    result = {
        "session_id": session_id,
        "title": title,
        "date": today,
        "files": {
            "clean_dialog": str(clean_path),
            "full_transcript": str(full_path),
            "html": str(html_path),
            "pdf": str(pdf_path) if pdf_ok else None,
        },
        "entry_count": len(entries),
        "user_messages": sum(1 for e in entries if e.get("type") == "user"),
        "assistant_messages": sum(1 for e in entries if e.get("type") == "assistant"),
    }
    if count is not None:
        result["reindex_chunks"] = count

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
