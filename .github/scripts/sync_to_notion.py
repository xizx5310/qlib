"""
Sync GitHub repo files to Notion pages.
Skip: .csv, .pkl, and other binary/data files.
Each file becomes a sub-page under the target Notion page.
Optimized: packs multiple rich_text objects into a single code block
to minimize block count and API calls.
"""

import os
from pathlib import Path
from notion_client import Client

# ── Config ──────────────────────────────────────────────
NOTION_TOKEN = os.environ["NOTION_API_TOKEN"]
PARENT_PAGE_ID = os.environ["NOTION_PAGE_ID"]

SKIP_EXTENSIONS = {
    ".csv", ".pkl", ".pickle", ".parquet", ".h5", ".hdf5",
    ".pyc", ".pyo", ".so", ".dll", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".bin", ".dat", ".npy", ".npz",
}

SKIP_DIRS = {
    ".git", "__pycache__", ".github", "node_modules",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    "egg-info",
}

MAX_RICH_TEXT_LEN = 2000          # Notion API: single rich_text limit
MAX_RICH_TEXTS_PER_BLOCK = 90     # safe margin (API max ~100)

notion = Client(auth=NOTION_TOKEN)


# ── Helpers ─────────────────────────────────────────────
def should_skip(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    return False


def get_language(suffix: str) -> str:
    mapping = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".sh": "shell", ".bash": "shell", ".zsh": "shell",
        ".yml": "yaml", ".yaml": "yaml", ".json": "json",
        ".toml": "plain text", ".cfg": "plain text", ".ini": "plain text",
        ".md": "markdown", ".rst": "plain text",
        ".html": "html", ".css": "css", ".sql": "sql",
        ".r": "r", ".R": "r", ".cpp": "c++", ".c": "c",
        ".java": "java", ".go": "go", ".rs": "rust",
    }
    return mapping.get(suffix, "plain text")


def chunk_text(text: str, size: int = MAX_RICH_TEXT_LEN):
    """Split text into chunks of ≤ size chars, respecting line boundaries."""
    lines = text.split("\n")
    chunks, current = [], []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_code_blocks(content: str, lang: str):
    """Build code blocks, packing multiple rich_text per block.

    Each code block can hold up to MAX_RICH_TEXTS_PER_BLOCK rich_text
    objects (each ≤ 2000 chars), so one block can store ~180k chars.
    Most files fit in a single block.
    """
    chunks = chunk_text(content, MAX_RICH_TEXT_LEN)
    children = []
    for i in range(0, len(chunks), MAX_RICH_TEXTS_PER_BLOCK):
        batch = chunks[i : i + MAX_RICH_TEXTS_PER_BLOCK]
        rich_texts = [
            {"type": "text", "text": {"content": c}} for c in batch
        ]
        children.append({
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": rich_texts,
                "language": lang,
            },
        })
    return children


# ── Notion Operations ───────────────────────────────────
def get_existing_pages(parent_id: str) -> dict:
    """Get existing child pages: {title: page_id}"""
    pages = {}
    start_cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=parent_id,
            start_cursor=start_cursor,
            page_size=100,
        )
        for block in resp["results"]:
            if block["type"] == "child_page":
                title = block["child_page"]["title"]
                pages[title] = block["id"]
        if not resp["has_more"]:
            break
        start_cursor = resp["next_cursor"]
    return pages


def clear_page(page_id: str):
    """Remove all blocks from a page."""
    start_cursor = None
    while True:
        resp = notion.blocks.children.list(
            block_id=page_id, start_cursor=start_cursor, page_size=100
        )
        for block in resp["results"]:
            notion.blocks.delete(block_id=block["id"])
        if not resp["has_more"]:
            break
        start_cursor = resp["next_cursor"]


def create_or_update_page(parent_id: str, title: str, content: str,
                          lang: str, existing: dict):
    """Create or update a Notion page with file content."""
    children = build_code_blocks(content, lang)

    if title in existing:
        page_id = existing[title]
        clear_page(page_id)
        for i in range(0, len(children), 100):
            notion.blocks.children.append(
                block_id=page_id, children=children[i : i + 100]
            )
        print(f"  ✅ Updated: {title}")
    else:
        resp = notion.pages.create(
            parent={"page_id": parent_id},
            properties={"title": [{"text": {"content": title}}]},
            children=children[:100],
        )
        new_id = resp["id"]
        for i in range(100, len(children), 100):
            notion.blocks.children.append(
                block_id=new_id, children=children[i : i + 100]
            )
        print(f"  ✨ Created: {title}")


# ── Main ────────────────────────────────────────────────
def main():
    repo_root = Path(".")
    existing = get_existing_pages(PARENT_PAGE_ID)
    file_count = 0

    for filepath in sorted(repo_root.rglob("*")):
        if not filepath.is_file():
            continue
        if should_skip(filepath):
            continue

        rel_path = filepath.relative_to(repo_root)
        title = str(rel_path)
        lang = get_language(filepath.suffix)

        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"  ⚠️ Skip (read error): {rel_path} — {e}")
            continue

        if not content.strip():
            continue

        create_or_update_page(PARENT_PAGE_ID, title, content, lang, existing)
        file_count += 1

    print(f"\n🎉 Synced {file_count} files to Notion.")


if __name__ == "__main__":
    main()
