# Deploying to GitHub Pages

This project can be deployed to GitHub Pages as a static site. The Flask server and verification features require a Python backend, but the main visualization works perfectly as a static site.

## Quick Deploy

1. **Export static files:**
   ```bash
   python export_static.py
   ```

2. **Commit the `docs/` folder:**
   ```bash
   git add docs/
   git commit -m "Deploy to GitHub Pages"
   git push
   ```

3. **Enable GitHub Pages:**
   - Go to your repository Settings > Pages
   - Under "Source", select "Deploy from a branch"
   - Select branch: `main` (or `master`)
   - Select folder: `/docs`
   - Click Save

4. **Your site will be available at:**
   ```
   https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/
   ```

## Automatic Deployment

The repository includes a GitHub Actions workflow (`.github/workflows/deploy.yml`) that automatically:
- Exports the database to JSON files
- Deploys to GitHub Pages on every push to `main`/`master`

To use automatic deployment:
1. Make sure the workflow file exists: `.github/workflows/deploy.yml`
2. Push to the `main` branch
3. The workflow will run automatically
4. Check the "Actions" tab in your GitHub repository to see deployment status

## Manual Deployment

If you prefer manual deployment:

1. **Run the export script:**
   ```bash
   python export_static.py
   ```

2. **The script creates:**
   - `docs/books.json` - All books data
   - `docs/references.json` - All reference links
   - `docs/index.html` - Static HTML page
   - `docs/static/` - CSS and JavaScript files

3. **Commit and push:**
   ```bash
   git add docs/
   git commit -m "Update static site"
   git push
   ```

## Updating the Site

Whenever you:
- Add new books
- Process new references
- Update the database

Simply run:
```bash
python export_static.py
git add docs/
git commit -m "Update static site with new data"
git push
```

## Limitations

The static version on GitHub Pages:
- ✅ Shows the interactive graph visualization
- ✅ Allows clicking books to show/hide nodes
- ✅ All visualization features work
- ❌ Verification page requires Flask server (run locally)
- ❌ Cannot process new books (requires Python backend)

For full functionality, run the Flask server locally:
```bash
python app.py
```

Then visit `http://localhost:5000`

