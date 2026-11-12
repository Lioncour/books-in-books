#!/usr/bin/env python3
"""
Measure extraction quality against hand-checked gold sets.

    .venv\\Scripts\\python eval_extraction.py            # every gold set
    .venv\\Scripts\\python eval_extraction.py --json     # machine readable
    .venv\\Scripts\\python eval_extraction.py --details  # list every wrong link

A gold set (eval/gold/*.json) lists, for one library book, the works it really
mentions and the candidate strings that must not become links. The script
compares that with the links currently in database.db:

  precision  share of the book's links that point at the right work
  recall     share of the gold works that have a correct link

Run it before and after changing the Judge's prompt or model, the matching
thresholds, or the candidate finder. Without it those changes are guesses.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

from process_books import MIN_VERIFY_SCORE, author_matches, title_similarity

GOLD_DIR = Path(__file__).resolve().parent / "eval" / "gold"


def _titles(work: Dict) -> List[str]:
    return [work["title"]] + list(work.get("aliases") or [])


def _best_work(names: List[str], works: List[Dict]) -> Optional[Dict]:
    """The gold work whose title or alias best matches any of the given names."""
    best, best_score = None, 0.0
    for work in works:
        for gold_title in _titles(work):
            for name in names:
                score = title_similarity(name, gold_title) if name else 0.0
                if score > best_score:
                    best, best_score = work, score
    return best if best_score >= MIN_VERIFY_SCORE else None


def _matches_any(names: List[str], phrases: List[str]) -> bool:
    return any(
        name and title_similarity(name, phrase) >= MIN_VERIFY_SCORE
        for name in names
        for phrase in phrases
    )


def find_source_id(cursor, source: Dict) -> Optional[int]:
    cursor.execute(
        """
        SELECT b.id, b.title, b.author FROM books b
        WHERE b.id IN (SELECT book_id FROM processed_files WHERE book_id IS NOT NULL)
           OR b.id IN (SELECT source_book_id FROM book_references)
        """
    )
    for book_id, title, author in cursor.fetchall():
        if title_similarity(title, source["title"]) >= MIN_VERIFY_SCORE and (
            not source.get("author") or author_matches(source["author"], author)
        ):
            return book_id
    return None


def evaluate(gold: Dict, cursor) -> Optional[Dict]:
    source_id = find_source_id(cursor, gold["source"])
    if source_id is None:
        return None
    cursor.execute(
        """
        SELECT b.title, b.author, IFNULL(b.kind, 'book'), r.raw_title
        FROM book_references r JOIN books b ON b.id = r.referenced_book_id
        WHERE r.source_book_id = ?
        ORDER BY b.title
        """,
        (source_id,),
    )
    links = cursor.fetchall()
    works = gold.get("works") or []
    not_works = gold.get("not_works") or []
    unsure = gold.get("unsure") or []

    correct, wrong, false_positive, unlabelled, kind_mismatch = [], [], [], [], []
    found = set()
    for title, author, kind, raw_title in links:
        names = [n for n in (title, raw_title) if n]
        label = f"{title} [{author or '?'}]"
        work = _best_work(names, works)
        if work is not None:
            authors = work.get("authors") or []
            if not authors or any(author_matches(a, author) for a in authors):
                correct.append(label)
                found.add(work["title"])
                if work.get("kind", "book") != kind:
                    kind_mismatch.append(f"{label}: filed as {kind}, is {work.get('kind')}")
            else:
                wrong.append(f"{label}  (should be by {' / '.join(authors)})")
        elif _matches_any(names, not_works):
            false_positive.append(label)
        elif _matches_any(names, unsure):
            continue
        else:
            unlabelled.append(label)

    judged = len(correct) + len(wrong) + len(false_positive)
    missed = [w["title"] for w in works if w["title"] not in found]
    return {
        "source": gold["source"]["title"],
        "links": len(links),
        "correct": len(correct),
        "wrong_work": wrong,
        "not_a_work": false_positive,
        "unlabelled": unlabelled,
        "kind_mismatch": kind_mismatch,
        "missed": missed,
        "gold_works": len(works),
        "precision": round(len(correct) / judged, 3) if judged else None,
        "recall": round(len(found) / len(works), 3) if works else None,
    }


def main() -> int:
    args = sys.argv[1:]
    if not GOLD_DIR.exists():
        print(f"No gold sets found in {GOLD_DIR}")
        return 1
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    results = []
    for path in sorted(GOLD_DIR.glob("*.json")):
        gold = json.loads(path.read_text(encoding="utf-8"))
        result = evaluate(gold, cursor)
        if result is None:
            print(f"{path.name}: “{gold['source']['title']}” is not in the library, skipped")
            continue
        results.append(result)
    conn.close()

    if "--json" in args:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    for r in results:
        pct = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"
        print(f"\n{r['source']}")
        print(f"  precision {pct(r['precision'])}   recall {pct(r['recall'])}")
        print(
            f"  {r['links']} links: {r['correct']} right, {len(r['wrong_work'])} wrong work, "
            f"{len(r['not_a_work'])} not a work, {len(r['unlabelled'])} not in the gold set"
        )
        print(f"  {len(r['missed'])} of {r['gold_works']} gold works have no correct link")
        if "--details" in args:
            for heading, key in (
                ("Wrong work", "wrong_work"),
                ("Not a work", "not_a_work"),
                ("Missed", "missed"),
                ("Wrong kind", "kind_mismatch"),
                ("Not in the gold set (label these)", "unlabelled"),
            ):
                if r[key]:
                    print(f"  {heading}:")
                    for item in r[key]:
                        print(f"    - {item}")
    if not results:
        print("Nothing to evaluate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
