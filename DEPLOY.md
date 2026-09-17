# Deploying to GitHub Pages

Public site = **explore-only**. Adding and removing books happens on your machine at `/manage`.

## Architecture

| Surface | URL | What it does |
|---------|-----|----------------|
| GitHub Pages | `https://USER.github.io/REPO/` | Everyone explores the graph |
| Local manage | `http://localhost:5000/manage` | You add / remove / extract / export |
| Local explore | `http://localhost:5000/` | Same graph UI as Pages |

GitHub Pages cannot run Python, so uploads never happen on the public site.

## First-time Pages setup

1. Run the app locally and manage your library:
   ```powershell
   python app.py
   ```
   Open [http://localhost:5000/manage](http://localhost:5000/manage) (token default: `books-admin`).

2. Export the public site:
   - Click **Export docs/ for Pages** on the manage page, **or**
   ```powershell
   python export_static.py
   ```

3. Commit and push `docs/`:
   ```powershell
   git add docs/
   git commit -m "Publish explore-only site to GitHub Pages"
   git push
   ```

4. Enable Pages in the repo:
   - Settings → Pages
   - Source: **GitHub Actions** (preferred, uses `.github/workflows/deploy-pages.yml`), **or**
   - Deploy from branch → `main` → folder `/docs`

5. Site URL:
   ```
   https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/
   ```

## Updating the public site

After you add or remove books locally:

1. Open `/manage` → **Export docs/ for Pages**
2. Commit `docs/` and push
3. Actions deploys automatically (or wait a minute for branch `/docs` deploy)

## What the public site includes

- Interactive graph, search, covers, detail quotes
- `books.json` + `references.json` (no EPUB files)

## What stays private / local

- `/manage` — add, remove, extract
- `/admin` — shared catalog curation
- `/verify` — reference verification
- Your `books/` folder and `database.db`
