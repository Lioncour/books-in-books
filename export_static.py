#!/usr/bin/env python3
"""
Export database + literary UI to docs/ for GitHub Pages (view-only).
"""

import json
import re
import shutil
import sys
from pathlib import Path

# Fix Unicode encoding for Windows console
if sys.platform == "win32":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")


def export_network():
    """Build books + references payloads matching the live API shape."""
    from app import get_db_connection, match_starting_books

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, title, author, verified, match_score, cover_id, cover_url,
               open_library_id, kind
        FROM books ORDER BY title
        """
    )
    books = match_starting_books(cursor.fetchall())
    cursor.execute(
        """
        SELECT source_book_id, referenced_book_id, context, manner
        FROM book_references
        ORDER BY source_book_id, referenced_book_id
        """
    )
    references = [
        {
            "source": ref["source_book_id"],
            "target": ref["referenced_book_id"],
            "context": ref["context"],
            "manner": ref["manner"],
        }
        for ref in cursor.fetchall()
    ]
    conn.close()

    portable_books = []
    for book in books:
        portable_books.append(
            {
                "id": book["id"],
                "title": book["title"],
                "author": book["author"],
                "is_starting": book["is_starting"],
                "verified": book["verified"],
                "match_score": book["match_score"],
                "open_library_id": book.get("open_library_id"),
                "kind": book.get("kind") or "book",
                "cover_id": book.get("cover_id"),
                "cover_url": book.get("cover_url"),
                "outgoing_count": book.get("outgoing_count"),
                "incoming_count": book.get("incoming_count"),
                "includes": book.get("includes") or [],
                "year": book.get("year"),
                "read_at": book.get("read_at"),
            }
        )
    return portable_books, references


def create_static_index(docs_dir: Path):
    """Create a static index.html that loads JSON instead of Flask APIs."""
    template_file = Path("templates/index.html")
    if not template_file.exists():
        print(f"  Warning: template not found: {template_file}")
        return

    html_content = template_file.read_text(encoding="utf-8")

    # Replace all Flask static url_for calls (with optional cache-bust query)
    html_content = re.sub(
        r"\{\{\s*url_for\('static',\s*filename='([^']+)'\)\s*\}\}(?:\?v=\d+)?",
        r"static/\1",
        html_content,
    )
    # Keep a cache-bust on main.js for Pages deploy
    html_content = html_content.replace(
        'src="static/main.js"',
        'src="static/main.js?v=46"',
    )

    # Explore-only: no local manage routes on Pages
    html_content = re.sub(
        r'href="/manage"',
        'href="#"',
        html_content,
    )
    html_content = re.sub(
        r'href="/admin"',
        'href="#"',
        html_content,
    )
    for local_route in ('href="/verify"', 'href="/review"'):
        html_content = html_content.replace(local_route, 'href="#"')

    # Mark body for explore-only chrome (CSS + JS)
    if "data-static" not in html_content:
        html_content = html_content.replace("<body>", '<body data-static="true">', 1)

    # Public-facing copy
    html_content = html_content.replace(
        "Explore the literary web of books that mention other books.",
        "A public map of books that mention other books. Browse freely — editing happens offline.",
    )

    index_file = docs_dir / "index.html"
    index_file.write_text(html_content, encoding="utf-8")
    print(f"  Created {index_file}")


def main():
    print("Exporting database to static JSON files...")
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    print("Exporting books + references...")
    books, references = export_network()
    books_file = docs_dir / "books.json"
    refs_file = docs_dir / "references.json"
    books_file.write_text(
        json.dumps(books, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    refs_file.write_text(
        json.dumps(references, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Exported {len(books)} books to {books_file}")
    print(f"  Exported {len(references)} references to {refs_file}")

    print("Copying static assets...")
    static_dir = Path("static")
    if static_dir.exists():
        docs_static = docs_dir / "static"
        if docs_static.exists():
            shutil.rmtree(docs_static)
        # The public page is explore-only: leave out the local manage, admin
        # and review assets.
        shutil.copytree(
            static_dir,
            docs_static,
            ignore=shutil.ignore_patterns("admin.*", "manage.*", "review.*"),
        )
        print(f"  Copied static files to {docs_static}")

    print("Creating static index.html...")
    create_static_index(docs_dir)

    print("\nExport complete.")
    print(f"Static files are in: {docs_dir}")
    print("\nTo deploy to GitHub Pages:")
    print("  1. Commit the docs/ folder")
    print("  2. Settings > Pages > Deploy from a branch")
    print("  3. Branch: main/master, folder: /docs")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
