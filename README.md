# Books in Books

Visualize how the books you own mention other books — a literary network of your shelves.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Flask](https://img.shields.io/badge/flask-2.3+-green)
![D3](https://img.shields.io/badge/graph-D3-teal)

## What it does

1. **Add** EPUB or TXT files from your library  
2. **Extract** book titles mentioned in the text (spaCy finds candidates, Claude judges them in context, Open Library verifies)  
3. **Explore** an interactive force-directed graph of connections  

Owned books sit in the sidebar with covers; referenced titles appear as linked nodes. Mentions that fail a strong Open Library match are skipped (and listed in the detail panel).

## Quick start (Windows)

The short way: `.\start.ps1` creates the environment on first run, installs the small spaCy model and starts the app (`-Full` for the large model). By hand:

```powershell
cd "books in books"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m spacy download en_core_web_trf
.\.venv\Scripts\python app.py
```

Open [http://localhost:5000](http://localhost:5000).

**macOS / Linux:** use `source .venv/bin/activate` and `python app.py`.

### spaCy model

The default model is `en_core_web_trf` (~500MB, best quality). First extraction can take several minutes per book.

## Using the app

1. Click **Add books** and upload `.epub` / `.txt` files  
   Prefer names like `Author - Title.epub`
2. Wait for **Extract references** (progress banner survives page reloads)
3. Click a library book to focus its mentions; click nodes for detail + quote context
4. Optional: **Export network** (JSON), **Import network**, or **Save image** (PNG of the graph)

Keyboard: `/` focuses search, `Esc` closes panels.

## Shared catalog (product direction)

Long-term: a curated **mention graph** so users can add a book by title and get “books mentioned inside it” without uploading an EPUB.

**Catalog admin (local):** [http://localhost:5000/admin](http://localhost:5000/admin)  
**Library manage (local):** [http://localhost:5000/manage](http://localhost:5000/manage)

- No token is needed on your own machine. There is no default token: to manage from another machine, start with `HOST=0.0.0.0` and an `ADMIN_TOKEN` of your choosing
- `/review` — confirm or reject links with the keyboard; decisions are remembered
- `/manage` — add/remove library books, correct a title or author by hand (**Edit**), extract references, export for Pages
- `/admin` — curated catalog works + mention edges
- **Import from my library** copies your existing verified EPUB extractions into the catalog as a starting point

Public read APIs (for a future shelf UI): `GET /api/catalog/search?q=…`, `GET /api/catalog/works/<id>/mentions`

## Share a view-only site (GitHub Pages)

Public Pages = explore only. Editing stays on `/manage`.

```powershell
.\.venv\Scripts\python app.py
# Open http://localhost:5000/manage → Export docs/ for Pages
# or: .\.venv\Scripts\python export_static.py
```

Commit `docs/` and enable Pages from the branch folder `/docs`. See [DEPLOY.md](DEPLOY.md).

## Project structure

```
books in books/
├── app.py                 # Flask server + APIs
├── process_books.py       # The pipeline: candidates, matching, storage, command line
├── titles.py              # Comparing titles and authors
├── open_library.py        # Catalogue lookup, with a cache
├── book_text.py           # Reading a file; italics and quotation marks
├── file_metadata.py       # Title and author from a file name or EPUB metadata
├── work_merging.py        # Versions, parts of a whole, dates
├── covers.py              # Cover pictures
├── gutenberg.py           # Free texts for the shared catalogue
├── judge.py               # Claude judges each candidate mention in context
├── reader.py              # Optional: Claude reads the book and lists the works it mentions
├── auditor.py             # Claude checks the finished links and fixes or removes wrong ones
├── cataloguer.py          # Claude folds parts into wholes and dates the works
├── claude_code.py         # Ask Claude through the Claude Code program instead of an API
├── start.ps1              # First-run setup and start (Windows)
├── tests/                 # Offline tests
├── eval_extraction.py     # Precision / recall against eval/gold/
├── eval/gold/             # Hand-checked gold sets
├── export_static.py       # Build docs/ for GitHub Pages
├── requirements.txt
├── books/                 # Your EPUB/TXT files
├── templates/             # index, manage, admin, review
├── static/                # CSS, JS, brand mark, favicon
└── docs/                  # Static export (GitHub Pages)
```

## API (local server)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/books` | Books + ownership, covers, verification |
| GET | `/api/references` | Source → target edges |
| GET | `/api/book/<id>/references` | Outgoing, incoming, skipped mentions |
| POST | `/api/upload` | Upload EPUB/TXT |
| POST | `/api/process` | Start extraction |
| GET | `/api/process/status` | Progress (also `process_status.json`) |
| POST | `/api/book/<id>/reprocess` | Re-extract one owned book |
| GET | `/api/network/export` | Portable `{books, references}` JSON |
| POST | `/api/network/import` | Merge a network export into the DB |
| GET | `/api/health` | spaCy / books dir / database checks |
| GET | `/review` | Review queue UI |
| GET | `/api/review/queue` | Links awaiting a decision + dropped mentions |
| POST | `/api/review/link` | Confirm, reject or restore a link |
| POST | `/api/review/dropped` | Link or dismiss a dropped mention |
| PATCH | `/api/library/books/<id>` | Set a library book's title and author by hand |

## How extraction works

**Borges** (spaCy) reads the EPUB/TXT and flags candidate titles. He also collects everything the publisher set in italics (whether with `<i>` tags or through a stylesheet class) and short Title Case phrases in quotation marks, which catches titles spaCy misses in running prose. He is fast and noisy.  
**The Judge** (Claude) reads each candidate in its passage and decides whether it really refers to a work, what kind it is, its catalogue title and who wrote it.  
**Lucien** looks that title up *with the author* (Open Library / film / music / cocktail lexicon) and files what is real.

Why the Judge matters: matching on the title alone turned the tune "Pop Goes the Weasel" into a James Patterson novel and credited *Atlas Shrugged* to the wrong author. With an author to check against, a same-titled work by someone else is refused. Measured on *84, Charing Cross Road*, precision went from 58% to 95% and recall from 52% to 83% with the Judge alone, and to 98% and 93% with the Reader on as well. On four gold sets labelled independently from the full text, with the Reader and the Auditor on, and each book read twice, precision is 100% on all of them and recall is 93% (*Small Things Like These*), 96% (*To Kill a Mockingbird*), 98% (*Columbus Day*) and 86% (*Careless People*).

How a judged mention is filed:
- **With an author:** the catalogue is searched by title and author. If no catalogue has it, the work is filed on the Judge's word and marked "Identified by the Judge".
- **Without an author:** only an identical title is accepted from the catalogue, and a work newer than the book mentioning it is refused. Failing that, the work is filed on the Judge's word as anonymous; this is how the Bible, a proclamation or a commission's report get in. The Auditor checks these.
- **Music:** the composer the Judge names is kept, instead of whoever recorded something with that title.

### Turning the Judge on

```powershell
$env:ANTHROPIC_API_KEY = "<your key>"   # or run `ant auth login` once
.\.venv\Scripts\python app.py
```

The sidebar says whether the Judge is on. Without credentials the app still works, but falls back to title-only matching. Then re-read your books (**Retry** on each, or `python process_books.py --force`) so existing links are judged.

### Marking what you have read

Each book in **Your library** has a **Mark read** button; it records today's date and can be taken off again. A book added by title is marked as read when it is added, since there is no file to read later. The date shows in the library list.

### Adding a book without a file

On the manage page, **Add a book by title** puts a book in your library from its title alone (and the author, if the title is a common one). If the book is old enough to be free, **Read free text** on the manage page fetches it from Project Gutenberg and reads it, so it brings its own mentions; otherwise it has none. It becomes a library book the others can point at, and if one of them already mentions it, that existing node is the one that joins your library, links and all.

### The shared catalogue and second-hop links

A work your books mention is only a dot until its own text is read. For works old enough to be in the public domain, `python process_books.py --catalogue 3` finds the text on Project Gutenberg, reads it like one of your own books, and keeps what it mentions. Two of your books can then meet through a book neither of them is: one mentions *Hamlet*, *Hamlet* mentions the *Aeneid*, another mentions the *Aeneid*. The graph draws these as dashed **Second hop** lines, and only between works already on the map, so a much-quoted classic does not flood it.

- It takes the works most of your books point at first, and tries each one once. `--catalogue 10 --list` shows what would be read, and whether each one is free, without reading anything.
- The search is local. Project Gutenberg's catalogue (about 20 MB, 62,000 English texts) is kept in `catalogue/` and refreshed when it is a month old, or with `--catalogue --refresh`, so a lookup takes a millisecond instead of a minute.
- The texts are kept in `catalogue/`, apart from your own books, and such a work does not join your library.
- Each one costs as much as reading a book of your own.
- In the graph, the **Catalogue** switch decides what you see: off, only the books you read yourself and what they mention; on, everything read from a free text as well, with what it mentions, drawn as dashed lines. A library book read from Project Gutenberg stays visible either way; only its mentions follow the switch.

### Using Amazon Bedrock

If you already reach Claude through AWS, you do not need an Anthropic API key. Copy `.env.example` to `.env` and set:

```
BOOKS_PROVIDER=bedrock
AWS_PROFILE=<your AWS profile>
AWS_REGION=eu-central-1
```

Install the extras once (`pip install "anthropic[bedrock]"`), log in (`aws sso login --profile <your AWS profile>`), and start the app. The sidebar then reads "The Judge is on (… via Amazon Bedrock)".

Who pays: Bedrock usage is billed per token to **the AWS account behind that profile**. It has nothing to do with a Claude subscription or its weekly limits. If the profile belongs to an employer, the employer is billed, so check that a private project is allowed on it.

If the app reports that your role is "not authorized to perform: bedrock-mantle:CreateInference", your account only allows the older InvokeModel endpoint. List what you can use with `aws bedrock list-inference-profiles`, then set two more lines: `BOOKS_BEDROCK_API=runtime` and `BOOKS_JUDGE_MODEL=<your inference profile id>`, for example `eu.anthropic.claude-opus-5-5`. On Bedrock a refused request is not retried on a fallback model; that book simply falls back to title-only matching.

### Using Claude Code

If you have a Claude subscription and the Claude Code command-line tool, the app can ask that instead of an API. Set:

```
BOOKS_PROVIDER=claude-code
```

Log in once by running `claude` in a terminal and typing `/login`. The app then runs `claude -p` in the background for each batch of mentions and each part of a book, with no tools, so it can only answer.

Who pays: nothing is billed per token. The work counts against **your Claude subscription's usage limits**, and reading a whole library with the Reader on uses a good share of them. It is also slower than the API, since every request starts the program afresh. Check that it answers with `python process_books.py --check`, run from your own terminal: started from inside a Claude Code session the program cannot reach its login. On *Small Things Like These* the Reader found the same 13 of 15 gold works through Claude Code as through Bedrock, in 14 seconds. `python eval/reader_check.py <gold set> <book file>` repeats that comparison for any gold book without touching the library.

### Settings

Settings can live in the terminal or in a `.env` file in the project folder (gitignored). The terminal wins.

| Setting | Default | Purpose |
|---------|---------|---------|
| `ANTHROPIC_API_KEY` | unset | Credentials for the Judge (Claude API) |
| `BOOKS_PROVIDER` | `anthropic` | `bedrock` reaches Claude through Amazon Bedrock, `claude-code` through the Claude Code program on this machine |
| `AWS_PROFILE` / `AWS_REGION` | unset | Bedrock only: which AWS login and region to use |
| `BOOKS_BEDROCK_API` | `mantle` | Bedrock only: `runtime` for the older InvokeModel endpoint |
| `BOOKS_JUDGE_MODEL` | `claude-opus-5-5` | Model used to judge mentions (`anthropic.claude-opus-5-5` on Bedrock) |
| `BOOKS_JUDGE` | on | `off` disables the Judge |
| `BOOKS_CATALOGUER` | on | `off` stops Claude folding parts into wholes (a book of the Bible into the Bible) |
| `BOOKS_READER` | off | `on` lets Claude read the whole book (see below) |
| `BOOKS_READER_PASSES` | `1` | `2` reads each book a second time, cut at different places, and keeps what either reading found: a few points more recall for twice the price |
| `BOOKS_READER_MODEL` | `claude-opus-5-5` | Model used by the Reader. `claude-fable-5-2` found more in one reading (97% of the gold works across three books, against about 90% for Opus 5.5) |
| `BOOKS_READER_PARALLEL` | `4` | Parts of a book the Reader reads at once |
| `BOOKS_AUDITOR` | on | `off` stops Claude checking the finished links |
| `BOOKS_AUDITOR_MODEL` | the Judge's | Model used by the Auditor |
| `BOOKS_SPACY_MODEL` | `en_core_web_trf` | spaCy model; `en_core_web_sm` is 12 MB and is used automatically if the configured one is missing |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Where the server listens |
| `ADMIN_TOKEN` | unset | Required for changes from another machine |

The graph **defaults to books only**. Use **Also show → Movies / Music / Drinks** to opt into other layers.

1. Text is pulled from EPUB/TXT; title and author come from the EPUB's own metadata, the filename is only a fallback  
2. Borges tags candidates with the passage around them  
3. Your own corrections are applied first, then the Judge rules on the rest  
4. Lucien verifies per domain, by title and author  
5. Dropped mentions are logged with the reason and can be rescued on `/review`  
6. SQLite stores works with a `kind` field (`book|movie|music|cocktail`)  

### The Reader (optional)

Borges, italics and quotation marks only find titles that look like titles. A work named in plain prose is invisible to them. With `BOOKS_READER=on`, Claude reads the book itself, part by part, and lists every work it refers to with the sentence it found it in. An entry whose sentence is not really in the text is discarded, and the rest go straight to the catalogue lookup.

It is off by default because it sends the whole book to the API, which costs more than judging a few hundred short candidates. On *84, Charing Cross Road* it took the links from 42 to 66, nearly all of them real. A short book takes a few minutes and a long novel a quarter of an hour. `BOOKS_READER_MODEL=claude-haiku-5-5` is much cheaper, but on that book it found fewer of the real mentions (80% against 95% for Opus 5.5), and Sonnet 5.5 did no better than Haiku (79%). Results are cached per file and model.

### The Auditor

Once a book's links are filed, Claude looks at each one the way a reviewer would: the passage, the wording found, and the record it was filed under. It marks the link checked, re-files it under the right work, removes it when the passage is not a reference at all, or flags it when it cannot tell. This catches what the Judge cannot see, because the Judge rules before the catalogue is searched: a study about a work filed as the work, a folk tale credited to one reteller, a film matched to a remake.

The Auditor runs by itself after every judged read. Its fixes and removals are remembered, so a re-read does not undo them, and your own decisions on `/review` always outrank it. On this library it passed 264 links, re-filed 27 and removed 2, leaving 3 it was unsure about.

```powershell
.\.venv\Scripts\python process_books.py --audit   # check any links not yet audited
```

Set `BOOKS_AUDITOR=off` to turn it off.

## Reviewing and correcting

Open [http://localhost:5000/review](http://localhost:5000/review). With the Auditor on you rarely need to: **Links to check** lists only what it was unsure about (tick **Show every link** for the rest). Each link comes with its quote: `A` confirms, `R` rejects, `U` undoes, `J`/`K` move. **Dropped mentions** lists what was left out and why: `L` links one to a work, `D` dismisses it. Confirmed links survive a re-read, and rejected ones stay out.

## Connections

The **Connections** button on the graph lists works mentioned by more than one of your books, authors who recur, your books that cite each other, and a reading list of books you do not own. **Same author** draws dotted ties between different works by one author. **Connections only** hides works that just one of your books mentions. Works shared by several books are drawn in gold, and zooming in labels every node. The layout is remembered between visits; **Reset layout** starts it over. The toolbar can narrow the mentioned works to one author or one period, and **List view** shows the same connections as a plain list, which is the default on a phone. With the Judge on, each mention also carries a small tag for how the work is used: read, quoted, discussed, recommended, criticised or comparison.

## Tests

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

They run offline: no network, spaCy model or Claude key needed. GitHub Actions runs them on every push.

One extra file, `tests/test_live_judge.py`, checks the real Judge against five passages this library got wrong. It is skipped unless you set `BOOKS_LIVE_TESTS=1` and have Claude credentials.

## Measuring accuracy

```powershell
.\.venv\Scripts\python eval_extraction.py --details
```

This compares the links in the database with hand-checked gold sets in `eval/gold/` and reports precision (links that point at the right work) and recall (real mentions that got a link). Run it before and after changing the Judge's prompt or model, or the matching rules. Five gold sets ship. The one for *84, Charing Cross Road* was built partly from the tool's own output, so it flatters. The other four (*Small Things Like These*, *To Kill a Mockingbird*, *Columbus Day*, *Careless People*) were labelled from the full text by readers that never saw the links, and are the ones to trust. A gold work marked `"optional": true` counts as right when linked but is not a miss when absent; newspaper headlines are marked that way, because the tool leaves them out on purpose.

## Useful commands

| Command | What it does |
|---------|--------------|
| `python process_books.py` | Read any new or changed books |
| `python process_books.py --force` | Re-judge every book. Candidates are cached per file, so this skips the slow spaCy pass |
| `python process_books.py --check` | Ask Claude one small question, to see that the chosen provider answers |
| `python process_books.py --merge-versions` | Make versions of one work one node (runs by itself after each read) |
| `python process_books.py --merge-parts` | Let the Cataloguer fold parts into their wholes (runs by itself after each judged read; `--again` asks about every work afresh) |
| `python process_books.py --check` | Ask Claude one small question, to see that the chosen provider answers |
| `python process_books.py --years` | Let the Cataloguer date works it has not dated yet (`--again` re-dates everything) |
| `python process_books.py --catalogue N` | Read N mentioned works from Project Gutenberg for second-hop links (`--list` only shows them, `--refresh` updates the local catalogue) |
| `python process_books.py --audit` | Let the Auditor check links it has not seen yet |
| `python process_books.py --rescue` | Link works that were dropped only for lack of a catalogue record (after a rule change) |
| `python process_books.py --fix-library` | Re-file library books under their real title and author (fixes wrong covers) |
| `python eval_extraction.py` | Precision and recall against the gold sets |

Duplicate rows with the same Open Library id or compatible title/author are merged on startup.

## Troubleshooting

**spaCy model not found**  
`python -m spacy download en_core_web_trf`

**Processing interrupted after refresh**  
Status is restored from `process_status.json`; click Extract again if needed.

**No nodes for a mention**  
It may have failed Open Library verification — check **Dropped mentions** on `/review`, where you can link it by hand.

**Database reset**  
Delete `database.db` and re-run processing (files in `books/` are kept).

## License

Open source — use freely for your personal book collection.

## Credits

- spaCy — NLP  
- Claude (Anthropic) — judging mentions in context  
- Flask — web server  
- D3 — graph visualization  
- Open Library — metadata and covers  
