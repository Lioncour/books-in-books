#!/usr/bin/env python3
"""
Literary Links AI Visualizer - Flask Web Server
Serves the main HTML page and JSON API endpoints.
"""

from flask import Flask, render_template, jsonify, request
import sqlite3
from pathlib import Path

app = Flask(__name__)

def get_db_connection():
    """Create a database connection."""
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def extract_title_from_filename(filename: str) -> str:
    """Extract title from filename (handles both 'Author - Title' and 'Title - Author' formats)."""
    stem = Path(filename).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        if len(parts) == 2:
            part1 = parts[0].strip()
            part2 = parts[1].strip()
            
            # Heuristic: if part1 is 1-2 words and part2 is 3+ words, likely "Author - Title"
            part1_words = len(part1.split())
            part2_words = len(part2.split())
            
            if part1_words <= 2 and part2_words >= 3:
                return part2  # Title is part2
            elif part2_words <= 2 and part1_words >= 3:
                return part1  # Title is part1
            else:
                return part2  # Default: assume "Author - Title"
    return stem

@app.route('/')
def index():
    """Serve the main HTML page."""
    return render_template('index.html')

@app.route('/api/books')
def api_books():
    """Return a JSON list of all books from the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT id, title, author FROM books ORDER BY title')
        books = cursor.fetchall()
        
        # Check which books exist in the books folder - extract both title and author
        books_dir = Path("books")
        file_books = []  # List of (title, author, title_lower, author_lower) tuples
        if books_dir.exists():
            for file_path in books_dir.glob("*"):
                if file_path.suffix.lower() in ['.txt', '.epub']:
                    stem = file_path.stem
                    if " - " in stem:
                        parts = stem.split(" - ", 1)
                        if len(parts) == 2:
                            part1 = parts[0].strip()
                            part2 = parts[1].strip()
                            # Heuristic to determine which is title vs author
                            part1_words = len(part1.split())
                            part2_words = len(part2.split())
                            if part1_words <= 2 and part2_words >= 3:
                                title, author = part2, part1
                            elif part2_words <= 2 and part1_words >= 3:
                                title, author = part1, part2
                            else:
                                title, author = part2, part1  # Default
                            file_books.append((title, author, title.lower().strip(), author.lower().strip()))
        
        # Track which files have been matched to avoid duplicates
        matched_files = set()
        
        books_list = []
        for book in books:
            db_title = book['title'].lower().strip()
            db_author = (book['author'] or '').lower().strip()
            
            # Skip books without authors (they shouldn't be nodes)
            if not db_author or db_author == 'unknown':
                continue
            
            # Exclude summaries or derivative works
            if 'summary of' in db_title or 'paperback' in db_title.lower():
                is_starting = False
            else:
                is_starting = False
                matched_file_idx = -1
                
                # Try exact match first (title AND author)
                for idx, (file_title, file_author, file_title_lower, file_author_lower) in enumerate(file_books):
                    if idx in matched_files:
                        continue  # This file already matched to a book
                    
                    # Exact match
                    if db_title == file_title_lower and db_author == file_author_lower:
                        is_starting = True
                        matched_file_idx = idx
                        break
                    
                    # Fuzzy match: titles very similar and authors match
                    title_similar = (db_title in file_title_lower or file_title_lower in db_title) and len(db_title) > 5
                    author_match = db_author == file_author_lower or (db_author and file_author_lower and (db_author in file_author_lower or file_author_lower in db_author))
                    
                    if title_similar and author_match and 'summary' not in db_title:
                        is_starting = True
                        matched_file_idx = idx
                        break
                
                # Mark file as matched
                if is_starting and matched_file_idx >= 0:
                    matched_files.add(matched_file_idx)
            books_list.append({
                'id': book['id'],
                'title': book['title'],
                'author': book['author'],
                'is_starting': is_starting
            })
        
        conn.close()
        return jsonify(books_list)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/references')
def api_references():
    """Return a JSON list of all source-target reference pairs."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT source_book_id, referenced_book_id 
            FROM book_references 
            ORDER BY source_book_id, referenced_book_id
        ''')
        references = cursor.fetchall()
        
        references_list = []
        for ref in references:
            references_list.append({
                'source': ref['source_book_id'],
                'target': ref['referenced_book_id']
            })
        
        conn.close()
        return jsonify(references_list)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def api_stats():
    """Return basic statistics about the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) as count FROM books')
        total_books = cursor.fetchone()['count']
        
        cursor.execute('SELECT COUNT(*) as count FROM book_references')
        total_references = cursor.fetchone()['count']
        
        # Count starting books (books that exist as files)
        books_dir = Path("books")
        existing_titles = set()
        if books_dir.exists():
            for file_path in books_dir.glob("*"):
                if file_path.suffix.lower() in ['.txt', '.epub']:
                    title = extract_title_from_filename(file_path.name)
                    existing_titles.add(title.lower().strip())
        
        if existing_titles:
            placeholders = ','.join(['?' for _ in existing_titles])
            cursor.execute(f'SELECT COUNT(*) as count FROM books WHERE LOWER(TRIM(title)) IN ({placeholders})', 
                         list(existing_titles))
            starting_books = cursor.fetchone()['count']
        else:
            starting_books = 0
        
        conn.close()
        
        return jsonify({
            'total_books': total_books,
            'total_references': total_references,
            'starting_books': starting_books
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/search')
def api_search():
    """Search books by title or author."""
    try:
        query = request.args.get('q', '').lower().strip()
        if not query:
            return jsonify([])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, title, author 
            FROM books 
            WHERE LOWER(title) LIKE ? OR LOWER(author) LIKE ?
            ORDER BY title
            LIMIT 50
        ''', (f'%{query}%', f'%{query}%'))
        
        books = cursor.fetchall()
        books_list = [{'id': book['id'], 'title': book['title'], 'author': book['author']} 
                     for book in books]
        
        conn.close()
        return jsonify(books_list)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/book/<int:book_id>/references')
def api_book_references(book_id):
    """Get all references found for a specific book with details."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get the source book
        cursor.execute('SELECT id, title, author FROM books WHERE id = ?', (book_id,))
        source_book = cursor.fetchone()
        
        if not source_book:
            return jsonify({'error': 'Book not found'}), 404
        
        # Get all referenced books
        cursor.execute('''
            SELECT b.id, b.title, b.author
            FROM book_references br
            JOIN books b ON br.referenced_book_id = b.id
            WHERE br.source_book_id = ?
            ORDER BY b.title
        ''', (book_id,))
        
        references = cursor.fetchall()
        
        references_list = []
        for ref in references:
            references_list.append({
                'id': ref['id'],
                'title': ref['title'],
                'author': ref['author']
            })
        
        conn.close()
        
        return jsonify({
            'source_book': {
                'id': source_book['id'],
                'title': source_book['title'],
                'author': source_book['author']
            },
            'references': references_list,
            'count': len(references_list)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/verify')
def verify_page():
    """Serve the verification page."""
    return render_template('verify.html')

@app.route('/api/verify')
def api_verify():
    """Run verification and return results."""
    try:
        import spacy
        import re
        from collections import defaultdict
        try:
            from ebooklib import epub
            import ebooklib
        except ImportError:
            return jsonify({'error': 'ebooklib not installed'}), 500
        
        # Load spaCy model
        try:
            nlp = spacy.load("en_core_web_trf")
        except OSError:
            return jsonify({'error': 'spaCy model not found. Run: python -m spacy download en_core_web_trf'}), 500
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        books_dir = Path("books")
        if not books_dir.exists():
            return jsonify({'error': 'Books directory not found'}), 404
        
        files = list(books_dir.glob("*.txt")) + list(books_dir.glob("*.epub"))
        if not files:
            return jsonify({'error': 'No book files found'}), 404
        
        results = []
        
        for file_path in files:
            # Extract title and author from filename
            stem = file_path.stem
            if " - " in stem:
                parts = stem.split(" - ", 1)
                if len(parts) == 2:
                    part1 = parts[0].strip()
                    part2 = parts[1].strip()
                    part1_words = len(part1.split())
                    part2_words = len(part2.split())
                    if part1_words <= 2 and part2_words >= 3:
                        title, author = part2, part1
                    elif part2_words <= 2 and part1_words >= 3:
                        title, author = part1, part2
                    else:
                        title, author = part2, part1
                else:
                    title, author = stem, "Unknown"
            else:
                title, author = stem, "Unknown"
            
            # Get book from database
            cursor.execute('SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))', (title,))
            result = cursor.fetchone()
            
            if not result:
                continue
            
            source_book_id = result[0]
            
            # Get references from database
            cursor.execute('''
                SELECT b.id, b.title, b.author
                FROM book_references br
                JOIN books b ON br.referenced_book_id = b.id
                WHERE br.source_book_id = ?
                ORDER BY b.title
            ''', (source_book_id,))
            
            db_references = cursor.fetchall()
            db_references_list = [{'id': r['id'], 'title': r['title'], 'author': r['author']} for r in db_references]
            db_titles = {r['title'].lower().strip() for r in db_references}
            
            # Re-process file to find references
            try:
                if file_path.suffix.lower() == '.epub':
                    book = epub.read_epub(str(file_path))
                    text = ''
                    for item in book.get_items():
                        if item.get_type() == ebooklib.ITEM_DOCUMENT:
                            text += item.get_content().decode('utf-8', errors='ignore') + '\n'
                    text = re.sub('<[^<]+?>', '', text)
                else:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                
                # Process with spaCy
                doc = nlp(text)
                references_found = []
                
                for ent in doc.ents:
                    if ent.label_ == "WORK_OF_ART":
                        referenced_title = ent.text.strip()
                        if referenced_title and len(referenced_title) > 2:
                            start = max(0, ent.start_char - 50)
                            end = min(len(text), ent.end_char + 50)
                            context = text[start:end].replace('\n', ' ').strip()
                            references_found.append({
                                'title': referenced_title,
                                'context': context
                            })
                
                # Group by title
                refs_by_title = defaultdict(list)
                for ref in references_found:
                    refs_by_title[ref['title']].append(ref['context'])
                
                found_references_list = []
                for ref_title, contexts in sorted(refs_by_title.items()):
                    found_references_list.append({
                        'title': ref_title,
                        'count': len(contexts),
                        'contexts': contexts[:3]  # Limit to 3 contexts
                    })
                
                found_titles = {title.lower().strip() for title in refs_by_title.keys()}
                
                # Compare
                matches = db_titles & found_titles
                only_in_db = list(db_titles - found_titles)
                only_in_text = [t for t in refs_by_title.keys() if t.lower().strip() not in db_titles]
                
                results.append({
                    'title': title,
                    'author': author,
                    'db_references_count': len(db_references),
                    'found_references_count': len(found_references_list),
                    'matches': len(matches),
                    'db_references': db_references_list,
                    'found_references': found_references_list,
                    'matched_titles': list(matches),
                    'only_in_database': only_in_db[:10],  # Limit to 10
                    'only_in_text': only_in_text[:10]  # Limit to 10
                })
            
            except Exception as e:
                results.append({
                    'title': title,
                    'author': author,
                    'error': str(e)
                })
        
        conn.close()
        
        return jsonify({
            'books': results,
            'total_books': len(results)
        })
        
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)
    
    # Create static directory if it doesn't exist
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)
    
    print("Literary Links AI Visualizer - Web Server")
    print("=" * 50)
    print("Server starting on http://localhost:5000")
    print("Press Ctrl+C to stop")
    print()
    
    app.run(debug=True, host='0.0.0.0', port=5000) 