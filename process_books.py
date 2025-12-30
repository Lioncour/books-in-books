#!/usr/bin/env python3
"""
Literary Links AI Visualizer - Data Processing Script
Processes book text files to extract references and populate the SQLite database.
"""

import sqlite3
import requests
import spacy
import os
import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
try:
    from ebooklib import epub
except ImportError:
    print("Please install ebooklib: pip install ebooklib")
    exit(1)
import ebooklib

# Load spaCy model
try:
    nlp = spacy.load("en_core_web_trf")
except OSError:
    print("spaCy model not found. Please install with: python -m spacy download en_core_web_trf")
    exit(1)

def init_database():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Create books table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            open_library_id TEXT NOT NULL UNIQUE
        )
    ''')
    
    # Create book_references table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS book_references (
            source_book_id INTEGER,
            referenced_book_id INTEGER,
            FOREIGN KEY (source_book_id) REFERENCES books (id),
            FOREIGN KEY (referenced_book_id) REFERENCES books (id)
        )
    ''')
    
    # Create processed_files table to track which files have been processed
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_files (
            filename TEXT PRIMARY KEY,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_hash TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized successfully!")

def get_or_create_book(title: str, cursor, conn) -> int:
    """
    Get or create a book in the database.
    First checks local cache, then calls Open Library API if needed.
    Uses case-insensitive matching to avoid duplicates.
    """
    # First try exact match
    cursor.execute('SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))', (title,))
    result = cursor.fetchone()
    if result:
        return result[0]
    
    # Try fuzzy match (check if title is contained in existing titles or vice versa)
    cursor.execute('SELECT id, title FROM books')
    all_books = cursor.fetchall()
    title_lower = title.lower().strip()
    for book_id, existing_title in all_books:
        existing_lower = existing_title.lower().strip()
        # Check if titles are very similar (one contains the other or they're very close)
        if title_lower == existing_lower:
            return book_id
        # Check if one is a substring of the other (for variations like "The Girl on the Train" vs "Girl on the Train")
        if len(title_lower) > 10 and len(existing_lower) > 10:
            if title_lower in existing_lower or existing_lower in title_lower:
                # If one is 80% similar to the other, consider it a match
                similarity = len(set(title_lower.split()) & set(existing_lower.split())) / max(len(title_lower.split()), len(existing_lower.split()))
                if similarity > 0.8:
                    return book_id
    try:
        response = requests.get(f"https://openlibrary.org/search.json?q={title}")
        response.raise_for_status()
        data = response.json()
        if data.get('docs') and len(data['docs']) > 0:
            book_data = data['docs'][0]
            book_title = book_data.get('title', title)
            author_name = book_data.get('author_name', ['Unknown'])[0] if book_data.get('author_name') else 'Unknown'
            open_library_id = book_data.get('key', f"manual_{title}")
            cursor.execute(
                'INSERT INTO books (title, author, open_library_id) VALUES (?, ?, ?)',
                (book_title, author_name, open_library_id)
            )
            conn.commit()
            return cursor.lastrowid
        else:
            cursor.execute(
                'INSERT INTO books (title, author, open_library_id) VALUES (?, ?, ?)',
                (title, 'Unknown', f"manual_{title}")
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"Error fetching book data for '{title}': {e}")
        cursor.execute(
            'INSERT INTO books (title, author, open_library_id) VALUES (?, ?, ?)',
            (title, 'Unknown', f"manual_{title}")
        )
        conn.commit()
        return cursor.lastrowid

def process_text_file(file_path: Path, source_book_id: int, cursor, conn):
    """Process a single text file to extract book references."""
    print(f"Processing: {file_path.name}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
        
        # Process text with spaCy
        doc = nlp(text)
        
        # Find WORK_OF_ART entities (book titles)
        references_found = set()
        for ent in doc.ents:
            if ent.label_ == "WORK_OF_ART":
                referenced_title = ent.text.strip()
                if referenced_title and len(referenced_title) > 2:  # Filter out very short entities
                    references_found.add(referenced_title)
        
        # Store references in database
        for referenced_title in references_found:
            try:
                referenced_book_id = get_or_create_book(referenced_title, cursor, conn)
                
                # Skip self-references (book referencing itself)
                if source_book_id == referenced_book_id:
                    continue
                
                # Check if reference already exists
                cursor.execute(
                    'SELECT 1 FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?',
                    (source_book_id, referenced_book_id)
                )
                
                if not cursor.fetchone():
                    cursor.execute(
                        'INSERT INTO book_references (source_book_id, referenced_book_id) VALUES (?, ?)',
                        (source_book_id, referenced_book_id)
                    )
                    print(f"  Added reference: {referenced_title}")
                
            except Exception as e:
                print(f"  Error processing reference '{referenced_title}': {e}")
        
        conn.commit()
        print(f"  Found {len(references_found)} unique references")
        
    except Exception as e:
        print(f"Error processing file {file_path}: {e}")

def process_epub_file(file_path: Path, source_book_id: int, cursor, conn):
    """Process a single EPUB file to extract book references."""
    print(f"Processing EPUB: {file_path.name}")
    try:
        book = epub.read_epub(str(file_path))
        text = ''
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                text += item.get_content().decode('utf-8', errors='ignore') + '\n'
        # Remove HTML tags (simple)
        import re
        text = re.sub('<[^<]+?>', '', text)
        # Process text with spaCy
        doc = nlp(text)
        references_found = set()
        for ent in doc.ents:
            if ent.label_ == "WORK_OF_ART":
                referenced_title = ent.text.strip()
                if referenced_title and len(referenced_title) > 2:
                    references_found.add(referenced_title)
        # Store references in database
        for referenced_title in references_found:
            try:
                referenced_book_id = get_or_create_book(referenced_title, cursor, conn)
                
                # Skip self-references (book referencing itself)
                if source_book_id == referenced_book_id:
                    continue
                
                cursor.execute(
                    'SELECT 1 FROM book_references WHERE source_book_id = ? AND referenced_book_id = ?',
                    (source_book_id, referenced_book_id)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        'INSERT INTO book_references (source_book_id, referenced_book_id) VALUES (?, ?)',
                        (source_book_id, referenced_book_id)
                    )
                    print(f"  Added reference: {referenced_title}")
            except Exception as e:
                print(f"  Error processing reference '{referenced_title}': {e}")
        conn.commit()
        print(f"  Found {len(references_found)} unique references")
    except Exception as e:
        print(f"Error processing EPUB file {file_path}: {e}")

def extract_title_and_author(filename: str) -> tuple:
    """
    Extract title and author from filename.
    Handles formats like:
    - "Author - Title.epub" -> (Title, Author)
    - "Title - Author.epub" -> (Title, Author) 
    - "Title.epub" -> (Title, Unknown)
    
    Tries to detect which format by checking if first part looks like a common name pattern.
    """
    stem = Path(filename).stem
    
    if " - " in stem:
        parts = stem.split(" - ", 1)
        if len(parts) == 2:
            part1 = parts[0].strip()
            part2 = parts[1].strip()
            
            # Heuristic: if part1 is a single word or two words (likely author name)
            # and part2 is longer/more complex (likely title), use part1 as author
            part1_words = len(part1.split())
            part2_words = len(part2.split())
            
            # If part1 is 1-2 words and part2 is 3+ words, likely "Author - Title"
            if part1_words <= 2 and part2_words >= 3:
                return part2, part1  # Title, Author
            # If part2 is 1-2 words and part1 is 3+ words, likely "Title - Author"
            elif part2_words <= 2 and part1_words >= 3:
                return part1, part2  # Title, Author
            # Default: assume "Author - Title" format (most common)
            else:
                return part2, part1  # Title, Author
    
    # If no " - " separator, assume it's just the title
    return stem, "Unknown"

def get_file_hash(file_path: Path) -> str:
    """Calculate MD5 hash of file for change detection."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception:
        return ""

def is_file_processed(filename: str, file_hash: str, cursor) -> bool:
    """Check if file has already been processed with the same hash."""
    cursor.execute('SELECT file_hash FROM processed_files WHERE filename = ?', (filename,))
    result = cursor.fetchone()
    if result:
        return result[0] == file_hash
    return False

def mark_file_processed(filename: str, file_hash: str, cursor, conn):
    """Mark a file as processed."""
    cursor.execute('''
        INSERT OR REPLACE INTO processed_files (filename, file_hash, processed_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', (filename, file_hash))
    conn.commit()

def main():
    """Main function to process all book text files."""
    print("Literary Links AI Visualizer - Data Processing")
    print("=" * 50)
    
    # Initialize database
    init_database()
    
    # Create books directory if it doesn't exist
    books_dir = Path("books")
    books_dir.mkdir(exist_ok=True)
    
    # Get all .txt and .epub files in the books directory
    text_files = list(books_dir.glob("*.txt"))
    epub_files = list(books_dir.glob("*.epub"))
    
    if not text_files and not epub_files:
        print("No .txt or .epub files found in 'books' directory.")
        print("Please add some book files to the 'books' folder and run again.")
        return
    
    total_files = len(text_files) + len(epub_files)
    print(f"Found {len(text_files)} text files and {len(epub_files)} EPUB files to process")
    print(f"Total: {total_files} files\n")
    
    # Process each text file
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    processed_books = []
    
    for idx, file_path in enumerate(text_files, 1):
        try:
            file_hash = get_file_hash(file_path)
            if is_file_processed(file_path.name, file_hash, cursor):
                print(f"[{idx}/{total_files}] Skipping TXT (already processed): {file_path.name}")
                continue
            
            title, author = extract_title_and_author(file_path.name)
            print(f"[{idx}/{total_files}] Processing TXT: {title} by {author}")
            source_book_id = get_or_create_book(title, cursor, conn)
            # Update author if it was Unknown
            if author != "Unknown":
                cursor.execute('UPDATE books SET author = ? WHERE id = ?', (author, source_book_id))
                conn.commit()
            process_text_file(file_path, source_book_id, cursor, conn)
            mark_file_processed(file_path.name, file_hash, cursor, conn)
            processed_books.append(title)
            print(f"[SUCCESS] Processed TXT: {title}")
        except Exception as e:
            print(f"[ERROR] Failed to process TXT {file_path.name}: {e}")
            import traceback
            traceback.print_exc()
        print()
    
    # Process each EPUB file
    for idx, file_path in enumerate(epub_files, len(text_files) + 1):
        try:
            file_hash = get_file_hash(file_path)
            if is_file_processed(file_path.name, file_hash, cursor):
                print(f"[{idx}/{total_files}] Skipping EPUB (already processed): {file_path.name}")
                continue
            
            title, author = extract_title_and_author(file_path.name)
            print(f"[{idx}/{total_files}] Processing EPUB: {title} by {author}")
            source_book_id = get_or_create_book(title, cursor, conn)
            # Update author if it was Unknown
            if author != "Unknown":
                cursor.execute('UPDATE books SET author = ? WHERE id = ?', (author, source_book_id))
                conn.commit()
            process_epub_file(file_path, source_book_id, cursor, conn)
            mark_file_processed(file_path.name, file_hash, cursor, conn)
            processed_books.append(title)
            print(f"[SUCCESS] Processed EPUB: {title}")
        except Exception as e:
            print(f"[ERROR] Failed to process EPUB {file_path.name}: {e}")
            import traceback
            traceback.print_exc()
        print()
    
    # Print summary
    cursor.execute('SELECT COUNT(*) FROM books')
    total_books = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM book_references')
    total_references = cursor.fetchone()[0]
    
    conn.close()
    
    print("=" * 50)
    print("Processing complete!")
    print(f"Total books in database: {total_books}")
    print(f"Total references in database: {total_references}")
    print(f"Books processed this run: {processed_books}")
    print()
    print("You can now run the web application with: python app.py")

if __name__ == "__main__":
    main() 