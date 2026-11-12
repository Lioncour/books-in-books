# Roadmap

Where Books in Books stands after the November 2026 overhaul, and what to do next.
Items are ordered by how much they improve the result for the effort.

## Done in this round

- **The Judge.** Claude reads each candidate mention in its passage and decides whether it is a work, which kind, and by whom. Open Library is then searched by title *and* author, and works newer than the source book are refused.
- **Library metadata.** Title and author come from the EPUB itself. Each file is tied to its library row by id, not by guessing from the filename.
- **Review queue** at `/review`, keyboard driven. Confirmed links survive a re-read and rejected ones stay out.
- **Connections.** Shared mentions, recurring authors, books that cite each other, a reading list, and same-author ties in the graph.
- **Security.** Local-only by default, debugger off, no default admin token, cross-site writes refused.
- **Tests.** 24 offline tests for matching, corrections, the Judge client and the write guard.

## Next: do these first

### 1. Re-read the library with the Judge on
Nothing in the database has been judged yet. Set `ANTHROPIC_API_KEY`, run `python process_books.py --force`, then walk `/review`. Expect the link count to drop and the wrong ones (tunes filed as novels, wrong authors) to go. This is the single step that turns the work above into a better graph.

### 2. Measure extraction quality
Build a small gold set: for two or three books, the list of works they really mention. Add a script that reports precision and recall against it. Without this, changes to the prompt, the model or the matching thresholds are guesses. Use it to compare models too: `BOOKS_JUDGE_MODEL=claude-haiku-5-5` is far cheaper and may be good enough.

### 3. Find mentions spaCy misses
The Judge can only rule on what Borges flags. spaCy misses titles in running prose ("she was reading Middlemarch again") and catches almost nothing in non-English text. Two options, in order of cost:
- Add cheap pattern candidates: italic and quoted spans from the EPUB markup, which the current tag-stripping throws away.
- Let Claude read each chapter directly and list the works it mentions, then keep spaCy only as a cross-check. More accurate, and the cost is a few cents per book with a small model. The Message Batches API halves it.

### 4. Make the graph connect
Ten books give ten starbursts. Three things create real links:
- **More books.** The network effect needs a larger library; batch upload already works.
- **Second-hop links from the catalogue.** For a mentioned book you do not own, pull its own mentions from the shared catalogue, so two of your books connect through a third.
- **Normalise editions and series.** "Star Trek" the film and the novelisation, or two volumes of one series, should be able to count as one node.

## Then: product

### 5. Add a book by title, no file needed
The stated long-term direction. The catalogue tables and admin page exist. What is missing is a public shelf view: search a title, see what it mentions, add it to "my shelf". Needs the catalogue to be populated, which the Judge now makes trustworthy enough to do in bulk from public-domain texts (Project Gutenberg).

### 6. Better covers and records for owned books
Owned books keep the Open Library record they were first matched to, which is sometimes the wrong work (Zweig's *Montaigne* points at Montaigne's *Essais*). Re-resolve owned books by title and author, and let the manage page edit title, author and cover by hand.

### 7. Graph usability
- Search that highlights and zooms to a node.
- Filter the graph to one author, one decade, or "shared only".
- A list view for phones, where a force graph is hard to use.
- Persist node positions so the layout does not reshuffle on every load.

### 8. Annotate the kind of mention
The Judge already reads the passage. Have it also say *how* the work is mentioned: read by a character, quoted, criticised, recommended, a passing comparison. That turns a link into something worth reading and allows filtering to "books this author recommends".

## Later: engineering

### 9. Split the big files
`app.py` (about 1,900 lines), `process_books.py` (about 2,200) and `static/main.js` (about 2,800) each do several jobs. Split along the seams that now exist: text reading, candidate finding, judging, catalogue lookup, storage; and on the front end graph, panels, upload and processing. Do it with the tests green before and after.

### 10. Stop re-running spaCy for nothing
A re-read repeats the slow transformer pass even when only the judging or matching changed. Cache the candidates per file hash so a re-judge takes seconds. Cache Open Library answers too.

### 11. Packaging and CI
- Run the tests in GitHub Actions on every push.
- Pin dependency versions; the spaCy transformer model alone is about 500 MB, so consider the small model as an option now that the Judge does the quality work.
- A proper WSGI server (waitress) for anyone who exposes it beyond their own machine.

### 12. Housekeeping
- The duplicate file `The Boy, The Mole, The Fox and The Horse (Charlie Mackesy) (1).epub` is processed twice.
- `/verify` overlaps with `/review` and re-runs spaCy on every book when opened; retire it or fold it in.
- Orphaned works (no link in or out after rejections) stay in the database; prune them on startup.
- `.claude/` is untracked; add it to `.gitignore` or commit it.
