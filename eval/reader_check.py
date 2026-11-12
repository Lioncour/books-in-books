#!/usr/bin/env python3
"""
Read one gold book with the Reader as currently configured and score what it
finds against the gold set, without touching the library.

    python eval/reader_check.py eval/gold/small-things-like-these.json "books/<file>.epub"

Use it to compare providers or models: set BOOKS_PROVIDER / BOOKS_READER_MODEL
and run it again. It counts the Reader's raw finds, before the catalogue and
the Auditor, so the numbers sit a little below eval_extraction.py's.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval_extraction  # noqa: E402
import judge  # noqa: E402
import process_books as pb  # noqa: E402
import reader  # noqa: E402


def main():
    gold = json.load(open(sys.argv[1], encoding="utf-8"))
    text = pb.read_book_file(Path(sys.argv[2]))[0]
    source = gold["source"]
    print(f"Provider: {judge.provider()}   model: {reader.reader_model()}")
    print(f"{source['title']}: {len(text):,} characters, {len(reader.split_into_chunks(text))} parts")
    started = time.time()
    found = reader.read_for_mentions(source["title"], source.get("author") or "", text)
    if found is None:
        print("The Reader did not answer.")
        return
    required = [w for w in gold["works"] if not w.get("optional")]
    hit, extra = set(), []
    for mention in found:
        work = eval_extraction._best_work([mention["title"]], gold["works"])
        if work:
            hit.add(work["title"])
        else:
            extra.append(mention["title"])
    got = [w["title"] for w in required if w["title"] in hit]
    print(f"{len(found)} finds in {time.time() - started:.0f} seconds")
    print(f"Gold works found: {len(got)} of {len(required)} ({len(got) / len(required):.0%})")
    print("Missed:", [w["title"] for w in required if w["title"] not in hit])
    print("Not in the gold set:", extra)


if __name__ == "__main__":
    main()
