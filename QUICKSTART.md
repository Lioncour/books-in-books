# Quick Start Guide

## First Time Setup (5 minutes)

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   python -m spacy download en_core_web_trf
   ```

2. **Add your first book:**
   - Place an EPUB file in the `books/` folder
   - Use format: `Author - Title.epub`
   - Example: `Andy Weir - Project Hail Mary.epub`

3. **Process the book:**
   ```bash
   python process_books.py
   ```

4. **Start the server:**
   ```bash
   python app.py
   ```

5. **View your network:**
   - Open http://localhost:5000 in your browser

## Adding More Books

Simply:
1. Add new `.epub` or `.txt` files to `books/`
2. Run `python process_books.py` again
3. Refresh your browser

The system will automatically skip already-processed files!

## Tips

- **Gold nodes** = Books you own
- **Blue nodes** = Referenced books (darker = more connections)
- **Click nodes** to see details
- **Drag gold nodes** to rearrange the network
- **Search** to filter the view
- **Scroll/zoom** to navigate large networks

Enjoy exploring your literary network! 📚

