#!/usr/bin/env python3
"""
Shared literary catalog — works and mention edges (product data).
Separate from a user's personal library / EPUB processing tables.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple


def get_conn(db_path: str = "database.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_catalog(conn: Optional[sqlite3.Connection] = None, quiet: bool = True) -> None:
    owns = conn is None
    if owns:
        conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
            CREATE TABLE IF NOT EXISTS catalog_works (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            open_library_id TEXT UNIQUE,
            cover_id TEXT,
            notes TEXT,
            kind TEXT DEFAULT 'book',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_mentions (
            id INTEGER PRIMARY KEY,
            source_work_id INTEGER NOT NULL,
            target_work_id INTEGER NOT NULL,
            context TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_work_id, target_work_id),
            CHECK (source_work_id != target_work_id),
            FOREIGN KEY (source_work_id) REFERENCES catalog_works (id),
            FOREIGN KEY (target_work_id) REFERENCES catalog_works (id)
        )
        """
    )
    # Migrate kind onto older catalog DBs
    cursor.execute("PRAGMA table_info(catalog_works)")
    cat_cols = {row[1] for row in cursor.fetchall()}
    if "kind" not in cat_cols:
        cursor.execute(
            "ALTER TABLE catalog_works ADD COLUMN kind TEXT DEFAULT 'book'"
        )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_catalog_works_title ON catalog_works (title)"
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_catalog_mentions_source
        ON catalog_mentions (source_work_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_catalog_mentions_target
        ON catalog_mentions (target_work_id)
        """
    )
    conn.commit()
    if owns:
        conn.close()
    if not quiet:
        print("Catalog tables ready.")


def _cover_url(cover_id: Optional[str]) -> Optional[str]:
    if not cover_id:
        return None
    return f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"


def work_to_dict(row: sqlite3.Row, outgoing: int = 0, incoming: int = 0) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "open_library_id": row["open_library_id"],
        "cover_id": row["cover_id"],
        "cover_url": _cover_url(row["cover_id"]),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "outgoing_count": outgoing,
        "incoming_count": incoming,
    }


def catalog_stats(conn: sqlite3.Connection) -> Dict[str, int]:
    cur = conn.cursor()
    works = cur.execute("SELECT COUNT(*) FROM catalog_works").fetchone()[0]
    mentions = cur.execute("SELECT COUNT(*) FROM catalog_mentions").fetchone()[0]
    return {"works": works, "mentions": mentions}


def list_works(
    conn: sqlite3.Connection,
    query: str = "",
    limit: int = 200,
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    q = (query or "").strip()
    if q:
        like = f"%{q}%"
        rows = cur.execute(
            """
            SELECT w.*,
              (SELECT COUNT(*) FROM catalog_mentions m WHERE m.source_work_id = w.id) AS outgoing,
              (SELECT COUNT(*) FROM catalog_mentions m WHERE m.target_work_id = w.id) AS incoming
            FROM catalog_works w
            WHERE w.title LIKE ? OR IFNULL(w.author, '') LIKE ?
            ORDER BY w.title
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            SELECT w.*,
              (SELECT COUNT(*) FROM catalog_mentions m WHERE m.source_work_id = w.id) AS outgoing,
              (SELECT COUNT(*) FROM catalog_mentions m WHERE m.target_work_id = w.id) AS incoming
            FROM catalog_works w
            ORDER BY w.title
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [work_to_dict(r, r["outgoing"], r["incoming"]) for r in rows]


def get_work(conn: sqlite3.Connection, work_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    row = cur.execute(
        "SELECT * FROM catalog_works WHERE id = ?", (work_id,)
    ).fetchone()
    if not row:
        return None
    outgoing = cur.execute(
        "SELECT COUNT(*) FROM catalog_mentions WHERE source_work_id = ?",
        (work_id,),
    ).fetchone()[0]
    incoming = cur.execute(
        "SELECT COUNT(*) FROM catalog_mentions WHERE target_work_id = ?",
        (work_id,),
    ).fetchone()[0]
    return work_to_dict(row, outgoing, incoming)


def find_work_by_ol(
    conn: sqlite3.Connection, open_library_id: str
) -> Optional[sqlite3.Row]:
    if not open_library_id:
        return None
    return conn.execute(
        "SELECT * FROM catalog_works WHERE open_library_id = ?",
        (open_library_id,),
    ).fetchone()


def find_work_by_title_author(
    conn: sqlite3.Connection, title: str, author: Optional[str] = None
) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    if author:
        row = cur.execute(
            """
            SELECT * FROM catalog_works
            WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))
              AND LOWER(TRIM(IFNULL(author, ''))) = LOWER(TRIM(?))
            """,
            (title, author),
        ).fetchone()
        if row:
            return row
    return cur.execute(
        """
        SELECT * FROM catalog_works
        WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))
        """,
        (title,),
    ).fetchone()


def upsert_work(
    conn: sqlite3.Connection,
    *,
    title: str,
    author: Optional[str] = None,
    open_library_id: Optional[str] = None,
    cover_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Tuple[int, bool]:
    """
    Insert or update a catalog work. Returns (id, created).
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("Title is required")
    author = (author or "").strip() or None
    open_library_id = (open_library_id or "").strip() or None
    cover_id = (cover_id or "").strip() or None
    notes = (notes or "").strip() or None

    cur = conn.cursor()
    existing = None
    if open_library_id:
        existing = find_work_by_ol(conn, open_library_id)
    if not existing:
        existing = find_work_by_title_author(conn, title, author)

    if existing:
        cur.execute(
            """
            UPDATE catalog_works
            SET title = ?,
                author = COALESCE(?, author),
                open_library_id = COALESCE(?, open_library_id),
                cover_id = COALESCE(?, cover_id),
                notes = COALESCE(?, notes)
            WHERE id = ?
            """,
            (title, author, open_library_id, cover_id, notes, existing["id"]),
        )
        conn.commit()
        return existing["id"], False

    cur.execute(
        """
        INSERT INTO catalog_works (title, author, open_library_id, cover_id, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, author, open_library_id, cover_id, notes),
    )
    conn.commit()
    return cur.lastrowid, True


def update_work(
    conn: sqlite3.Connection,
    work_id: int,
    *,
    title: Optional[str] = None,
    author: Optional[str] = None,
    open_library_id: Optional[str] = None,
    cover_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    row = get_work(conn, work_id)
    if not row:
        return False
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE catalog_works
        SET title = ?,
            author = ?,
            open_library_id = ?,
            cover_id = ?,
            notes = ?
        WHERE id = ?
        """,
        (
            title if title is not None else row["title"],
            author if author is not None else row["author"],
            open_library_id if open_library_id is not None else row["open_library_id"],
            cover_id if cover_id is not None else row["cover_id"],
            notes if notes is not None else row["notes"],
            work_id,
        ),
    )
    conn.commit()
    return True


def delete_work(conn: sqlite3.Connection, work_id: int) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM catalog_mentions
        WHERE source_work_id = ? OR target_work_id = ?
        """,
        (work_id, work_id),
    )
    cur.execute("DELETE FROM catalog_works WHERE id = ?", (work_id,))
    conn.commit()
    return cur.rowcount > 0


def list_mentions_for_work(
    conn: sqlite3.Connection, work_id: int
) -> Dict[str, List[Dict[str, Any]]]:
    cur = conn.cursor()
    outgoing_rows = cur.execute(
        """
        SELECT m.id AS mention_id, m.context, m.confidence, w.*
        FROM catalog_mentions m
        JOIN catalog_works w ON w.id = m.target_work_id
        WHERE m.source_work_id = ?
        ORDER BY w.title
        """,
        (work_id,),
    ).fetchall()
    incoming_rows = cur.execute(
        """
        SELECT m.id AS mention_id, m.context, m.confidence, w.*
        FROM catalog_mentions m
        JOIN catalog_works w ON w.id = m.source_work_id
        WHERE m.target_work_id = ?
        ORDER BY w.title
        """,
        (work_id,),
    ).fetchall()

    def pack(rows):
        out = []
        for r in rows:
            item = work_to_dict(r)
            item["mention_id"] = r["mention_id"]
            item["context"] = r["context"]
            item["confidence"] = r["confidence"]
            out.append(item)
        return out

    return {"outgoing": pack(outgoing_rows), "incoming": pack(incoming_rows)}


def add_mention(
    conn: sqlite3.Connection,
    source_work_id: int,
    target_work_id: int,
    context: Optional[str] = None,
    confidence: float = 1.0,
) -> Tuple[int, bool]:
    if source_work_id == target_work_id:
        raise ValueError("A book cannot mention itself")
    if not get_work(conn, source_work_id) or not get_work(conn, target_work_id):
        raise ValueError("Source or target work not found")

    cur = conn.cursor()
    existing = cur.execute(
        """
        SELECT id FROM catalog_mentions
        WHERE source_work_id = ? AND target_work_id = ?
        """,
        (source_work_id, target_work_id),
    ).fetchone()
    if existing:
        cur.execute(
            """
            UPDATE catalog_mentions
            SET context = COALESCE(?, context),
                confidence = ?
            WHERE id = ?
            """,
            ((context or "").strip() or None, confidence, existing["id"]),
        )
        conn.commit()
        return existing["id"], False

    cur.execute(
        """
        INSERT INTO catalog_mentions
            (source_work_id, target_work_id, context, confidence)
        VALUES (?, ?, ?, ?)
        """,
        (
            source_work_id,
            target_work_id,
            (context or "").strip() or None,
            confidence,
        ),
    )
    conn.commit()
    return cur.lastrowid, True


def delete_mention(conn: sqlite3.Connection, mention_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM catalog_mentions WHERE id = ?", (mention_id,))
    conn.commit()
    return cur.rowcount > 0


def import_from_local_library(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Copy verified books + references from the personal library tables
    into the shared catalog (idempotent upsert).
    """
    cur = conn.cursor()
    # Ensure personal tables exist
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='books'"
    )
    if not cur.fetchone():
        return {"works_created": 0, "works_updated": 0, "mentions_added": 0}

    id_map: Dict[int, int] = {}
    created = 0
    updated = 0

    books = cur.execute(
        """
        SELECT id, title, author, open_library_id, cover_id, verified
        FROM books
        WHERE verified = 1 OR open_library_id NOT LIKE 'manual_%'
        """
    ).fetchall()

    for book in books:
        work_id, was_created = upsert_work(
            conn,
            title=book["title"],
            author=book["author"],
            open_library_id=book["open_library_id"],
            cover_id=book["cover_id"],
            notes="Imported from local library",
        )
        id_map[book["id"]] = work_id
        if was_created:
            created += 1
        else:
            updated += 1

    mentions_added = 0
    refs = cur.execute(
        """
        SELECT source_book_id, referenced_book_id, context
        FROM book_references
        """
    ).fetchall()
    for ref in refs:
        src = id_map.get(ref["source_book_id"])
        tgt = id_map.get(ref["referenced_book_id"])
        if not src or not tgt or src == tgt:
            continue
        _, was_new = add_mention(conn, src, tgt, context=ref["context"], confidence=1.0)
        if was_new:
            mentions_added += 1

    return {
        "works_created": created,
        "works_updated": updated,
        "mentions_added": mentions_added,
    }
