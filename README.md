# Books in Books - Literary Links AI Visualizer

A web application that visualizes the network of book references found within your book collection. Discover which books reference other books, and explore the interconnected web of literary connections.

![Books in Books](https://img.shields.io/badge/status-active-success)
![Python](https://img.shields.io/badge/python-3.8+-blue)
![Flask](https://img.shields.io/badge/flask-2.3+-green)

## Features

- 📚 **EPUB & TXT Support**: Process your book files in EPUB or plain text format
- 🔍 **AI-Powered Extraction**: Uses spaCy NLP to automatically detect book references in text
- 🌐 **Interactive Network Graph**: Visualize book relationships with an interactive node network
- 🎨 **Synthwave UI**: Beautiful retro-futuristic interface
- 🔎 **Search & Filter**: Find books quickly by title or author
- 📊 **Real-time Stats**: Track total books and references in your network
- 🎯 **Starting Books**: Distinguishes books you own from referenced books

## Screenshots

The visualization shows:
- **Gold nodes**: Books you own (starting books)
- **Blue nodes**: Referenced books (varying intensity by connection count)
- **Gray nodes**: Isolated books
- **Edges**: References between books

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Step 1: Clone or Download

```bash
cd "books in books"
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Install spaCy Language Model

The project uses spaCy's English transformer model for entity recognition:

```bash
python -m spacy download en_core_web_trf
```

**Note**: This is a large model (~500MB). For a smaller alternative, you can use:
```bash
python -m spacy download en_core_web_sm
```
Then update `process_books.py` line 23 to use `en_core_web_sm` instead.

## Usage

### Step 1: Add Your Books

Place your book files in the `books/` directory. Supported formats:
- `.epub` files
- `.txt` files

**Recommended filename format**: `Author - Title.epub`
- Example: `Andy Weir - Project Hail Mary.epub`
- The system will automatically extract the title and author

### Step 2: Process Books

Run the processing script to extract book references:

```bash
python process_books.py
```

This will:
- Initialize the SQLite database
- Process all books in the `books/` directory
- Extract book references using NLP
- Store data in `database.db`

**First run**: This may take several minutes depending on:
- Number of books
- Book file sizes
- Internet connection (for Open Library API lookups)

### Step 3: Start the Web Server

```bash
python app.py
```

The server will start on `http://localhost:5000`

Open your browser and navigate to the URL to see your book network visualization!

## Adding More Books

To add more books to your network:

1. Add new `.epub` or `.txt` files to the `books/` directory
2. Run `python process_books.py` again
3. The script will:
   - Skip already processed books (based on filename)
   - Process only new books
   - Add new references to the database
4. Refresh your browser to see the updated network

## Project Structure

```
books in books/
├── app.py                 # Flask web server
├── process_books.py       # Book processing script
├── debug_titles.py        # Debugging utility
├── test_api.py           # API testing script
├── database.db            # SQLite database (created on first run)
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── books/                 # Place your book files here
│   ├── Author - Title.epub
│   └── ...
├── templates/
│   └── index.html        # Main web interface
└── static/
    ├── main.js           # Frontend JavaScript
    └── style.css        # Styling
```

## API Endpoints

- `GET /` - Main web interface
- `GET /api/books` - List all books with metadata
- `GET /api/references` - List all book reference pairs
- `GET /api/stats` - Get statistics (total books, references, starting books)
- `GET /api/search?q=query` - Search books by title or author

## How It Works

1. **Text Extraction**: EPUB files are parsed and HTML tags are removed
2. **NLP Processing**: spaCy identifies `WORK_OF_ART` entities (book titles)
3. **Book Lookup**: Each detected title is looked up in Open Library API
4. **Database Storage**: Books and references are stored in SQLite
5. **Visualization**: Sigma.js renders an interactive force-directed graph

## Troubleshooting

### "spaCy model not found"
Run: `python -m spacy download en_core_web_trf`

### "ebooklib not found"
Run: `pip install ebooklib`

### Database errors
Delete `database.db` and run `process_books.py` again to recreate it.

### No books found
- Ensure files are in the `books/` directory
- Check file extensions are `.epub` or `.txt`
- Verify file permissions

### Processing is slow
- Large EPUB files take time to process
- Open Library API calls add latency
- Consider processing books in smaller batches

## Future Enhancements

Potential improvements:
- [ ] Batch processing with progress bar
- [ ] Export network data (JSON/CSV)
- [ ] Book cover images from Open Library
- [ ] Reference context (quotes/excerpts)
- [ ] Community book database
- [ ] Advanced filtering (by author, genre, etc.)
- [ ] Network analysis metrics (centrality, clusters)

## Contributing

Feel free to submit issues or pull requests!

## License

This project is open source. Use it freely for your personal book collection.

## Credits

- **spaCy**: Natural language processing
- **Flask**: Web framework
- **Sigma.js**: Graph visualization
- **Open Library**: Book metadata API

---

**Happy reading and exploring! 📖✨**


