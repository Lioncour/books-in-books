# Roadmap

Where Books in Books stands after the November 2026 overhaul, and what to do next.
Items are ordered by how much they improve the result for the effort.

## Done in this round

- **The Judge.** Claude reads each candidate mention in its passage and decides whether it is a work, which kind, and by whom. Open Library is then searched by title *and* author, and works newer than the source book are refused.
- **Library metadata.** Title and author come from the EPUB itself. Each file is tied to its library row by id, not by guessing from the filename.
- **Review queue** at `/review`, keyboard driven. Confirmed links survive a re-read and rejected ones stay out.
- **Connections.** Shared mentions, recurring authors, books that cite each other, a reading list, and same-author ties in the graph.
- **Security.** Local-only by default, debugger off, no default admin token, cross-site writes refused.
- **Tests.** 79 offline tests for matching, corrections, the Judge client and the write guard, run by GitHub Actions on every push.
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
- **The Reader.** Optional (`BOOKS_READER=on`): Claude reads the book itself and lists the works it mentions, each backed by a sentence that is checked against the text. On one book it took the links from 42 to 66.
- **Amazon Bedrock.** `BOOKS_PROVIDER=bedrock` reaches Claude through an AWS profile instead of an Anthropic API key, with settings in a local `.env`.
- **First real run (12 Nov 2026).** The whole library was re-read through Bedrock with the Judge and the Reader on. Links went from 170 to 300, works shared by several books from 2 to 9. On *84, Charing Cross Road*: precision 58% to 98%, recall 52% to 93% (the gold set was widened with real mentions the Reader found, so that recall figure flatters a little).
- **Tuning from that run.** Works the Judge names with an author but no catalogue finds are filed on its word; judged music keeps the composer or is marked traditional; without an author only an identical title is accepted; later editions' afterwords may list later books.
- **The Auditor.** Claude checks every finished link against its passage and its catalogue record, re-files wrong works and removes non-references, so nobody has to walk the review queue. On this library: 264 passed, 27 re-filed, 2 removed, 3 left as unsure. Precision on the gold set reached 100%. Judged films and music are filed on Claude's word, because the film and recording searches kept returning remakes, games and random performers.
- **Faster Reader.** It reads four parts of a book at once.
- **Mention filter.** The graph can show only works that are read, quoted, discussed, recommended, criticised or used as a comparison, and the Connections panel lists what your books recommend and criticise.
- **Live Judge test.** `tests/test_live_judge.py` runs five of this library's real mistakes through the actual Judge, behind a flag.

## Next: do these first

### 1. Spot-check the Auditor
The Auditor replaced the manual review, but nothing checks the Auditor except one gold set. Sample twenty of its 27 re-filings and both removals by eye once, to see whether it can be trusted unattended. `/review` with **Show every link** lists them with its reasons.

### 2. More gold sets, then compare models
One gold set is not enough to trust a number, and this one was partly built from the tool's own output. Add one for a novel (*Columbus Day*) and one for non-fiction (*Careless People*), labelled by hand from the book. Then compare models: the run above used Opus 5.5 for both steps, and `claude-haiku-5-5` for the Reader would cut the cost by an order of magnitude if the numbers hold.

### 3. Make the Reader cheaper
The Reader now reads four parts at once, which fixes the wait. The price is unchanged: sending the parts through the Message Batches API would halve it, and a smaller model may do as well (see item 2). Results are cached per file and model, so this only matters for new books.

### 4. Make the graph connect
Ten books now share nine works (Star Trek, The Simpsons, Hamlet, Winnie-the-Pooh and others), up from two. Three things would create more real links:
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
