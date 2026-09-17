# Books in Books

Visualize how the books you own mention other books — a literary network of your shelves.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Flask](https://img.shields.io/badge/flask-2.3+-green)
![D3](https://img.shields.io/badge/graph-D3-teal)

## What it does

1. **Add** EPUB or TXT files from your library  
2. **Extract** book titles mentioned in the text (spaCy + Open Library verification)  
3. **Explore** an interactive force-directed graph of connections  

Owned books sit in the sidebar with covers; referenced titles appear as linked nodes. Mentions that fail a strong Open Library match are skipped (and listed in the detail panel).

## Quick start (Windows)

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

- Login token defaults to `books-admin` (set `ADMIN_TOKEN` to change)
- `/manage` — add/remove library books, extract references, export for Pages
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

Commit `docs/`, enable Pages (GitHub Actions workflow or branch folder `/docs`). See [DEPLOY.md](DEPLOY.md).

## Project structure

```
books in books/
├── app.py                 # Flask server + APIs
├── process_books.py       # NLP extraction + Open Library matching
├── export_static.py       # Build docs/ for GitHub Pages
├── requirements.txt
├── books/                 # Your EPUB/TXT files
├── templates/             # index, manage, admin, verify
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
| GET | `/verify` | Manual verification UI |

## How extraction works

**Borges** reads the EPUB/TXT and finds mentions (books, and with cues: films, music, drinks).  
**Lucien** checks the right shelves (Open Library / film / music / cocktail lexicon) and files what is real.

The graph **defaults to books only**. Use **Also show → Movies / Music / Drinks** to opt into other layers.

1. Text is pulled from EPUB/TXT  
2. Borges tags candidates and guesses kind  
3. Lucien verifies per domain  
4. Weak hits are logged as skipped mentions  
5. SQLite stores works with a `kind` field (`book|movie|music|cocktail`)  

Duplicate rows with the same Open Library id or compatible title/author are merged on startup.

## Troubleshooting

**spaCy model not found**  
`python -m spacy download en_core_web_trf`

**Processing interrupted after refresh**  
Status is restored from `process_status.json`; click Extract again if needed.

**No nodes for a mention**  
It may have failed Open Library verification — check the detail panel’s skipped list or `/verify`.

**Database reset**  
Delete `database.db` and re-run processing (files in `books/` are kept).

## License

Open source — use freely for your personal book collection.

## Credits

- spaCy — NLP  
- Flask — web server  
- D3 — graph visualization  
- Open Library — metadata and covers  
