# Roadmap

Where Books in Books stands after the November 2026 overhaul, and what to do next.
Items are ordered by how much they improve the result for the effort.

## Done in this round

- **The Judge.** Claude reads each candidate mention in its passage and decides whether it is a work, which kind, and by whom. Open Library is then searched by title *and* author, and works newer than the source book are refused.
- **Library metadata.** Title and author come from the EPUB itself. Each file is tied to its library row by id, not by guessing from the filename.
- **Review queue** at `/review`, keyboard driven. Confirmed links survive a re-read and rejected ones stay out.
- **Connections.** Shared mentions, recurring authors, books that cite each other, a reading list, and same-author ties in the graph.
- **Security.** Local-only by default, debugger off, no default admin token, cross-site writes refused.
- **Tests.** 110 offline tests for matching, corrections, the Judge client and the write guard, run by GitHub Actions on every push.
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
- **Auditor spot-checked.** All 27 re-filings and both removals were read by a second AI pass: 27 of 29 were right. One was wrong (Proverbs went to a commentary on Proverbs; fixed, and anonymous works are now filed on the Auditor's word instead of a title-only search), and one is debatable (a pun on *Rocky* was removed as a description of a planet).
- **Reader model compared.** On *84, Charing Cross Road*, Haiku 5.5 as the Reader reached 98% precision and 80% recall against 100% and 95% for Opus 5.5. The gold set leans towards Opus, since it was widened with Opus finds, but the gap is large enough to keep Opus.
- **Italics from stylesheets.** EPUBs that put italics in a CSS class instead of `<i>` tags are now covered (*Careless People* went from 0 to 37 italic candidates).
- **Reset layout** button for the remembered graph positions.
- **Independent gold sets.** Two books were labelled from their full text by readers that never saw the links. Against those: precision 100% on both, recall 93% (*Small Things Like These*) and 88% (*To Kill a Mockingbird*). What is still missed is unnamed allusion ("a toadfrog that lived in a hall" for *The Wind in the Willows*) and a lower-case title in a "by the same author" list.
- **What the independent measurement found.** Two bugs, both fixed: title comparison matched "If" inside "Tom Swift" (containment is now by whole words), and works with no author were dropped when no catalogue had the identical title. Those are now filed on the Judge's word; 13 came back across the library, and the Bible now connects five of the ten books.
- **Sonnet 5.5 as the Reader** did no better than Haiku (79% recall on *84, Charing Cross Road*). Opus 5.5 stays.
- **Gold sets for the long books.** *Columbus Day* and *Careless People* were labelled from the full text, in parts, by readers that never saw the links. First measurement: precision 100% on both, but recall only 68% and 67%. The Reader was missing works pointed at through a character ("the Barney guy", "crazy Rambo shit", a Smurf painted on a truck, a meeting room named after the Wicked Witch of the West).
- **The Reader now follows characters to their work.** One paragraph in its instructions. After re-reading the library: *Columbus Day* 95% recall, *Careless People* 86%, *To Kill a Mockingbird* 96%, precision still 100% on all five gold sets. The library went from 308 to 347 links, and 19 works are now shared by several books, up from 10.
- **Readings vary.** Two readings of the same book do not find exactly the same works: *Small Things Like These* went from 93% to 87% and *84, Charing Cross Road* from 95% to 93% in the same re-read, one or two works each. The numbers above are one reading, good to a few points.
- **Two readings.** Measured at the Reader, over the five gold books (178 works): one reading found 87% and 90% on two tries, one reading with overlapping parts 91%, two plain readings together 90%, and a plain reading plus one cut at different places 93%. `BOOKS_READER_PASSES=2` does the last of these. With it on, across the library: precision 100% on all five gold sets, recall 97% (*84, Charing Cross Road*), 93% (*Small Things Like These*), 96% (*To Kill a Mockingbird*), 98% (*Columbus Day*), 86% (*Careless People*). 360 links, 19 works shared by several books. Each comparison is a single run, so read the differences as a few points either way.
- **A book no longer links to its own film or stage version**, which the second reading picked up from a jacket text.
- **Claude Code as a provider.** `BOOKS_PROVIDER=claude-code` runs the `claude` program on the machine instead of an API, so the work is drawn from a Claude subscription. Run for real on one book: the Reader found 13 of 15 gold works in *Small Things Like These*, the same as a single reading through Bedrock. Two things had to be fixed first: started from inside a Claude Code session the program borrowed that session's login, and an API key in the environment overrode the subscription. `process_books.py --check` asks one small question to show the provider answers; `eval/reader_check.py` scores one reading against a gold set. A long book works too: *Columbus Day* (950,000 characters, 24 parts) took 65 seconds and found 37 of 41 gold works, in line with single readings through Bedrock (38 and 39). The Auditor passed all 14 links of *Small Things Like These* through it. The library now runs on Claude Code; the links themselves were made through Bedrock and have not been re-read.
- **Versions of one work are one node.** A film and its sequels, a film and the book or comic behind it, a volume and its series, and two catalogue records of one title are merged after each read (*Star Wars* and its episodes, *Iron Man* the film and the comic, the *Harry Potter* films and the series, both *Wizard of Oz*). Different works that share a title stay apart: two authors' *Essays*, a song and a novel. Nine records merged in this library; 21 works are now shared by several books, and precision and recall are unchanged on all five gold sets.
- **The Cataloguer folds parts into wholes.** Claude is shown the list of mentioned works once and names the ones that belong inside another: Genesis, Proverbs, Daniel and two Gospels into the Bible, six Montaigne essays and the English title into *Essais*, a sermon into Donne's *Sermons*, a song into *Aladdin*, an episode into *Star Trek*. 24 folded, all read and found right. The Bible now joins seven of the ten books. What was folded is remembered, so the measurement still counts a part as found through its whole; precision and recall are unchanged on all five gold sets. It only asks again when new works have appeared. The detail panel lists what a work includes.
- **Add a book by title.** The manage page takes a title and puts the book in the library with no file. A work already in the graph joins the library with its links (*The Wind in the Willows* arrived already mentioned by two books); one no catalogue knows is filed as typed.
- **Filters and a list view.** The graph can be narrowed to one author or one period, and shown as a list of each library book with the works it mentions (the default on a phone). The Cataloguer dates the works: Open Library's own years turned out to be unusable (*Lean In* in 1799, *Macbeth* in 1508), so Claude was asked instead and dated 286 of 302, films and songs included.
- **`process_books.py` split.** From 3,470 lines to 2,417, with six modules cut along the seams that existed: `titles.py` (comparing titles and authors), `open_library.py` (catalogue lookup and its cache), `book_text.py` (reading a file, italics and quotation marks), `file_metadata.py` (title and author from a file), `work_merging.py` (versions, parts, dates) and `covers.py`. The old names still import from `process_books`, so nothing else had to change. Tests green before and after.
- **Mention filter.** The graph can show only works that are read, quoted, discussed, recommended, criticised or used as a comparison, and the Connections panel lists what your books recommend and criticise.
- **Live Judge test.** `tests/test_live_judge.py` runs five of this library's real mistakes through the actual Judge, behind a flag.

## Next: do these first

### 1. Make the graph connect
Ten books now share 22 works (the Bible in seven of them, then The Simpsons, Star Wars, Spider-Man, The Wizard of Oz, Hamlet, Winnie-the-Pooh, Star Trek and others), up from two. Two things would create more real links:
- **More books.** The network effect needs a larger library; batch upload already works.
- **Second-hop links from the catalogue.** For a mentioned book you do not own, pull its own mentions from the shared catalogue, so two of your books connect through a third.

## Then: product

### 2. A shelf for books you have no file for
Adding by title works, but such a book has no mentions of its own. The longer aim is a shared catalogue: read public-domain texts (Project Gutenberg) once, keep what they mention, and let a book added by title bring those mentions with it.

## Later: engineering

### 3. Split the remaining big files
`process_books.py` is down to about 2,400 lines; what is left is matching and storage, which lean on each other and on names the tests replace, so the next cut needs the tests rewritten first. `app.py` (about 2,000 lines) could give its review and admin routes to Flask blueprints. `static/main.js` (about 3,200 lines) is one closure sharing its state; splitting it means choosing a module system, and nothing tests it today.

### 4. Housekeeping
- The front end has no tests; the graph, the filters and the list view are checked by hand.
- Fable 5.2 could not be tried as the Reader: the Claude Code program on this machine is older than it requires. Update it (`claude update`) and compare with `eval/reader_check.py`.
