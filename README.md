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
├── process_books.py       # NLP extraction + Open Library matching
├── judge.py               # Claude judges each candidate mention in context
├── reader.py              # Optional: Claude reads the book and lists the works it mentions
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

**Borges** (spaCy) reads the EPUB/TXT and flags candidate titles. He also collects everything the publisher set in italics and short Title Case phrases in quotation marks, which catches titles spaCy misses in running prose. He is fast and noisy.  
**The Judge** (Claude) reads each candidate in its passage and decides whether it really refers to a work, what kind it is, its catalogue title and who wrote it.  
**Lucien** looks that title up *with the author* (Open Library / film / music / cocktail lexicon) and files what is real.

Why the Judge matters: matching on the title alone turned the tune "Pop Goes the Weasel" into a James Patterson novel and credited *Atlas Shrugged* to the wrong author. With an author to check against, a same-titled work by someone else is refused, and a work published after the book that mentions it is refused too.

### Turning the Judge on

```powershell
$env:ANTHROPIC_API_KEY = "<your key>"   # or run `ant auth login` once
.\.venv\Scripts\python app.py
```

The sidebar says whether the Judge is on. Without credentials the app still works, but falls back to title-only matching. Then re-read your books (**Retry** on each, or `python process_books.py --force`) so existing links are judged.

| Setting | Default | Purpose |
|---------|---------|---------|
| `ANTHROPIC_API_KEY` | unset | Credentials for the Judge |
| `BOOKS_JUDGE_MODEL` | `claude-opus-5-5` | Model used to judge mentions |
| `BOOKS_JUDGE` | on | `off` disables the Judge |
| `BOOKS_READER` | off | `on` lets Claude read the whole book (see below) |
| `BOOKS_READER_MODEL` | `claude-opus-5-5` | Model used by the Reader |
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

It is off by default because it sends the whole book to the API, which costs more than judging a few hundred short candidates. Turn it on for one book, compare `eval_extraction.py` before and after, and keep it on if the recall is worth it. `BOOKS_READER_MODEL=claude-haiku-5-5` is much cheaper. Results are cached per file and model.

## Reviewing and correcting

Open [http://localhost:5000/review](http://localhost:5000/review). **Links to check** lists every link with its quote: `A` confirms, `R` rejects, `U` undoes, `J`/`K` move. **Dropped mentions** lists what was left out and why: `L` links one to a work, `D` dismisses it. Confirmed links survive a re-read, and rejected ones stay out.

## Connections

The **Connections** button on the graph lists works mentioned by more than one of your books, authors who recur, your books that cite each other, and a reading list of books you do not own. **Same author** draws dotted ties between different works by one author. **Connections only** hides works that just one of your books mentions. Works shared by several books are drawn in gold, and zooming in labels every node. The layout is remembered between visits. With the Judge on, each mention also carries a small tag for how the work is used: read, quoted, discussed, recommended, criticised or comparison.

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

This compares the links in the database with hand-checked gold sets in `eval/gold/` and reports precision (links that point at the right work) and recall (real mentions that got a link). Run it before and after changing the Judge's prompt or model, or the matching rules. One gold set ships as a draft, for *84, Charing Cross Road*; correct it and add more for your own books.

## Useful commands

| Command | What it does |
|---------|--------------|
| `python process_books.py` | Read any new or changed books |
| `python process_books.py --force` | Re-judge every book. Candidates are cached per file, so this skips the slow spaCy pass |
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
