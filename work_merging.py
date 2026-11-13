"""
Making one node of what is one work: versions, parts of a whole, and the dates that go with them.
"""

from __future__ import annotations

from titles import _PLACEHOLDER_CREATORS, version_key, author_matches
from typing import Dict, List, Optional
import sqlite3


def _remap_book_id(cursor, old_id: int, new_id: int):
    if old_id == new_id:
        return
    cursor.execute(
        """
        UPDATE book_references
        SET source_book_id = ?
        WHERE source_book_id = ?
        """,
        (new_id, old_id),
    )
    cursor.execute(
        """
        UPDATE book_references
        SET referenced_book_id = ?
        WHERE referenced_book_id = ?
        """,
        (new_id, old_id),
    )
    cursor.execute(
        "DELETE FROM book_references WHERE source_book_id = referenced_book_id"
    )
    cursor.execute(
        """
        UPDATE skipped_mentions
        SET source_book_id = ?
        WHERE source_book_id = ?
        """,
        (new_id, old_id),
    )
    # Keep people's corrections and file ownership pointing at the surviving row
    for statement in (
        "UPDATE OR IGNORE reference_overrides SET source_book_id = ? WHERE source_book_id = ?",
        "UPDATE reference_overrides SET target_book_id = ? WHERE target_book_id = ?",
        "UPDATE processed_files SET book_id = ? WHERE book_id = ?",
    ):
        try:
            cursor.execute(statement, (new_id, old_id))
        except sqlite3.OperationalError:
            pass  # table/column not created yet on a very old database
    cursor.execute("DELETE FROM books WHERE id = ?", (old_id,))


def _same_work(a: Dict, b: Dict) -> bool:
    """Whether two records with the same version key are versions of one work."""
    kinds = {a["kind"], b["kind"]}
    if kinds == {"movie"} or kinds == {"book", "movie"}:
        # the film, its sequels and series, and the book or comic it came from
        return True
    if len(kinds) > 1:
        return False  # a song is not the novel that shares its title
    known = [
        r["author"]
        for r in (a, b)
        if (r["author"] or "").strip().lower() not in _PLACEHOLDER_CREATORS
    ]
    return (
        len(known) < 2
        or author_matches(known[0], [known[1]])
        or author_matches(known[1], [known[0]])
    )


def fill_years(cursor, conn, quiet: bool = True, finder=None) -> Optional[int]:
    """
    Give mentioned works the year they first appeared, as dated by the
    Cataloguer, so the graph can be narrowed to a period. The catalogue's own
    years are not used: Open Library dates a work by its oldest record, which
    put Lean In in 1799 and Macbeth in 1508. Claude is asked once per work.
    Returns how many works got a year, or None when Claude is unavailable.
    """
    import cataloguer

    if finder is None:
        if not cataloguer.cataloguer_enabled():
            return 0
        finder = cataloguer.find_years
    cursor.execute(
        """
        SELECT id, title, author, IFNULL(kind, 'book') FROM books
        WHERE IFNULL(year_checked, 0) = 0 AND IFNULL(kind, 'book') != 'cocktail'
          AND (id IN (SELECT referenced_book_id FROM book_references)
               OR id IN (SELECT source_book_id FROM book_references)
               OR IFNULL(on_shelf, 0) = 1)
        ORDER BY title
        """
    )
    works = [{"id": i, "title": t, "author": a, "kind": k} for i, t, a, k in cursor.fetchall()]
    if not works:
        return 0
    years = finder(works)
    if years is None:
        return None
    dated = 0
    for work_id, year in years.items():
        cursor.execute(
            "UPDATE books SET year = ?, year_checked = 1 WHERE id = ?", (year, work_id)
        )
        dated += 1 if year is not None else 0
    conn.commit()
    if not quiet:
        print(f"  Dated {dated} of {len(works)} work(s)")
    return dated


def _fold_work_into(cursor, other_id: int, keeper_id: int):
    """Give one mentioned work's links to another and remove it."""
    # Remember what was folded in, so the whole can still be found by its part's name
    cursor.execute(
        """
        INSERT OR IGNORE INTO work_aliases (book_id, title)
        SELECT ?, title FROM books WHERE id = ?
        """,
        (keeper_id, other_id),
    )
    cursor.execute(
        "UPDATE OR IGNORE work_aliases SET book_id = ? WHERE book_id = ?", (keeper_id, other_id)
    )
    cursor.execute("DELETE FROM work_aliases WHERE book_id = ?", (other_id,))
    # A book that mentions both keeps one link, not two
    cursor.execute(
        """
        DELETE FROM book_references
        WHERE referenced_book_id = ?
          AND source_book_id IN (
              SELECT source_book_id FROM book_references WHERE referenced_book_id = ?
          )
        """,
        (other_id, keeper_id),
    )
    _remap_book_id(cursor, other_id, keeper_id)


def merge_parts(cursor, conn, quiet: bool = True, finder=None) -> Optional[int]:
    """
    Fold parts into the wholes they belong to (a book of the Bible into the
    Bible, a story into its collection), as named by the Cataloguer. Claude is
    only asked when works have appeared that it has not seen. Books in the
    library are never folded away. Returns None when Claude is unavailable.
    """
    import cataloguer

    if finder is None:
        if not cataloguer.cataloguer_enabled():
            return 0
        finder = cataloguer.find_parts
    cursor.execute(
        """
        SELECT b.id, b.title, b.author, IFNULL(b.kind, 'book'), IFNULL(b.parts_checked, 0)
        FROM books b
        WHERE b.id IN (SELECT referenced_book_id FROM book_references)
          AND IFNULL(b.on_shelf, 0) = 0
          AND b.id NOT IN (SELECT book_id FROM processed_files WHERE book_id IS NOT NULL)
          AND b.id NOT IN (SELECT source_book_id FROM book_references)
        ORDER BY b.title
        """
    )
    rows = cursor.fetchall()
    if not any(not checked for *_rest, checked in rows):
        return 0
    works = [{"id": i, "title": t, "author": a, "kind": k} for i, t, a, k, _ in rows]
    pairs = finder(works)
    if pairs is None:
        return None
    known = {w["id"]: w for w in works}
    folded = 0
    for pair in pairs:
        part, whole = known.get(pair.get("part")), known.get(pair.get("whole"))
        if not part or not whole or part["id"] == whole["id"]:
            continue
        cursor.execute("SELECT 1 FROM books WHERE id = ?", (whole["id"],))
        if not cursor.fetchone():
            continue  # the whole was itself folded away in this pass
        _fold_work_into(cursor, part["id"], whole["id"])
        folded += 1
        if not quiet:
            print(f"  '{part['title']}' is part of '{whole['title']}' ({pair.get('reason') or ''})")
    cursor.execute(
        f"UPDATE books SET parts_checked = 1 WHERE id IN ({','.join('?' * len(works))})",
        [w["id"] for w in works],
    )
    conn.commit()
    return folded


def merge_versions(cursor, conn, quiet: bool = True) -> int:
    """
    Make versions of one work one node: the film and its sequels, the film and
    the book it was made from, two catalogue records of the same title. The
    record more books point at survives and takes the others' links. Books in
    the library are never merged away.
    """
    cursor.execute(
        """
        SELECT b.id, b.title, b.author, IFNULL(b.kind, 'book'),
               (SELECT COUNT(*) FROM book_references r WHERE r.referenced_book_id = b.id)
        FROM books b
        WHERE b.id NOT IN (SELECT book_id FROM processed_files WHERE book_id IS NOT NULL)
          AND IFNULL(b.on_shelf, 0) = 0
          AND b.id NOT IN (SELECT source_book_id FROM book_references)
        """
    )
    groups: Dict[str, List[Dict]] = {}
    for book_id, title, author, kind, links in cursor.fetchall():
        key = version_key(title)
        if key:
            groups.setdefault(key, []).append(
                {"id": book_id, "title": title, "author": author, "kind": kind, "links": links}
            )

    # Fold a longer title into the shorter one it belongs to: "harry potter and
    # the sorcerer s stone" into "harry potter", "wonderful wizard of oz" into
    # "wizard of oz". The shorter title must be specific enough to stand alone.
    for key in sorted(groups, key=len, reverse=True):
        words = key.split()
        for parent in groups:
            if parent == key or parent not in groups or key not in groups:
                continue
            parent_words = parent.split()
            series = len(parent_words) >= 2 and key.startswith(parent + " and the ")
            one_word_more = len(parent_words) >= 3 and words[1:] == parent_words
            if series or one_word_more:
                groups[parent].extend(groups.pop(key))
                break

    merged = 0
    for records in groups.values():
        if len(records) < 2:
            continue
        # Most-linked first; a bare title before one with a subtitle; then the oldest
        records.sort(key=lambda r: (-r["links"], len(r["title"]), r["id"]))
        keeper = records[0]
        for other in records[1:]:
            if not _same_work(keeper, other):
                continue
            _fold_work_into(cursor, other["id"], keeper["id"])
            merged += 1
            if not quiet:
                print(f"  Merged '{other['title']}' into '{keeper['title']}'")
    conn.commit()
    return merged
