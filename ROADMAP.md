# Roadmap

Where Books in Books stands after the November 2026 overhaul, and what to do next.
Items are ordered by how much they improve the result for the effort.

## Done in this round

- **The Judge.** Claude reads each candidate mention in its passage and decides whether it is a work, which kind, and by whom. Open Library is then searched by title *and* author, and works newer than the source book are refused.
- **Library metadata.** Title and author come from the EPUB itself. Each file is tied to its library row by id, not by guessing from the filename.
- **Review queue** at `/review`, keyboard driven. Confirmed links survive a re-read and rejected ones stay out.
- **Connections.** Shared mentions, recurring authors, books that cite each other, a reading list, and same-author ties in the graph.
- **Security.** Local-only by default, debugger off, no default admin token, cross-site writes refused.
- **Tests.** 49 offline tests for matching, corrections, the Judge client and the write guard, run by GitHub Actions on every push.
- **More candidates.** Italic spans and quoted Title Case phrases from the EPUB markup are offered to the Judge, so titles spaCy misses in running prose are found.
- **Candidate cache.** Candidates are stored per file, so re-judging a book skips the slow spaCy pass.
- **A yardstick.** `eval_extraction.py` reports precision and recall against gold sets in `eval/gold/`. Baseline before any judging, for *84, Charing Cross Road*: 58% precision, 52% recall.
- **Library records.** Owned books are re-filed under their real title and author (`--fix-library`), which fixed the record and cover for *Montaigne* and *Careless People*.
- **Housekeeping.** Works nothing links to are pruned, an identical upload is refused, and the old `/verify` page is retired.
- **Catalogue cache.** Open Library answers are kept for 30 days in `lookup_cache.db`, so a re-judge is not one request per mention.
- **Manner of mention.** The Judge also says how a work is used (read, quoted, discussed, recommended, criticised, comparison). It is stored on the link and shown as a tag. Empty until the library is re-read with the Judge on.
- **Edit by hand.** A library book's title and author can be corrected on `/manage`; the edit is kept and the book is re-filed in the catalogue.
- **Graph.** A "Connections only" filter, and node positions remembered between visits.
- **Packaging.** Dependency ranges are pinned, the app serves through waitress when it is installed, `start.ps1` sets everything up on first run, and the small spaCy model is an option (and the fallback).
- **The Reader.** Optional (`BOOKS_READER=on`): Claude reads the book itself and lists the works it mentions, each backed by a sentence that is checked against the text. Built and tested against a stand-in for the API; not yet run for real.
- **Amazon Bedrock.** `BOOKS_PROVIDER=bedrock` reaches Claude through an AWS profile instead of an Anthropic API key, with settings in a local `.env`. Not yet run against AWS.
- **Live Judge test.** `tests/test_live_judge.py` runs five of this library's real mistakes through the actual Judge, behind a flag.

## Next: do these first

### 1. Re-read the library with the Judge on
Nothing in the database has been judged yet. Set `ANTHROPIC_API_KEY`, or point the app at Amazon Bedrock in `.env`, then:
1. `python -m unittest tests.test_live_judge -v` with `BOOKS_LIVE_TESTS=1`: a one-minute check that the Judge behaves.
2. `python eval_extraction.py --details` and note the baseline (58% / 52% for *84, Charing Cross Road*).
3. `python process_books.py --force`, then the eval again, then walk `/review`.

Expect the link count to drop and the wrong ones (tunes filed as novels, wrong authors) to go. This is the single step that turns the work above into a better graph.

### 2. Measure, then tune
Run `python eval_extraction.py --details` before and after step 1 and write the numbers down. Then:
- Correct the draft gold set for *84, Charing Cross Road* and add one for a novel and one for non-fiction, since they fail in different ways.
- Compare models: `BOOKS_JUDGE_MODEL=claude-haiku-5-5` is far cheaper and may be good enough.
- Look at the "Missed" list. Each entry is either a candidate nobody flagged or a lookup that failed, and they need different fixes.

### 3. Try the Reader, and decide
The Reader is built but unmeasured. Run one book with `BOOKS_READER=on` and compare the eval numbers and the cost against the Judge alone. Then decide whether it becomes the default, stays optional, or runs on a cheaper model. If it is kept, sending the parts through the Message Batches API halves the price.

### 4. Make the graph connect
Ten books give ten starbursts. Three things create real links:
- **More books.** The network effect needs a larger library; batch upload already works.
- **Second-hop links from the catalogue.** For a mentioned book you do not own, pull its own mentions from the shared catalogue, so two of your books connect through a third.
- **Normalise editions and series.** "Star Trek" the film and the novelisation, or two volumes of one series, should be able to count as one node.

## Then: product

### 5. Add a book by title, no file needed
The stated long-term direction. The catalogue tables and admin page exist. What is missing is a public shelf view: search a title, see what it mentions, add it to "my shelf". Needs the catalogue to be populated, which the Judge now makes trustworthy enough to do in bulk from public-domain texts (Project Gutenberg).

### 6. Graph usability
- Filter the graph to one author, one decade, or one manner of mention ("books this author recommends").
- A list view for phones, where a force graph is hard to use.
- Positions are remembered per browser; a "Reset layout" control would make that visible.

## Later: engineering

### 7. Split the big files
`app.py` (about 1,900 lines), `process_books.py` (about 2,200) and `static/main.js` (about 2,800) each do several jobs. Split along the seams that now exist: text reading, candidate finding, judging, catalogue lookup, storage; and on the front end graph, panels, upload and processing. Do it with the tests green before and after.

### 8. Housekeeping
- `process_books.py` still holds matching, storage, file reading and the command line in one file (see item 7).
- EPUBs that mark italics with their own CSS class names yield no italic candidates (*Careless People*); read the stylesheet to find which classes are italic.
