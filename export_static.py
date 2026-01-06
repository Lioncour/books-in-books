#!/usr/bin/env python3
"""
Export database to static JSON files for GitHub Pages deployment.
This script generates JSON files that can be served statically.
"""

import sqlite3
import json
from pathlib import Path
import sys

# Fix Unicode encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

def get_db_connection():
    """Create a database connection."""
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def extract_title_from_filename(filename: str) -> str:
    """Extract title from filename."""
    stem = Path(filename).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        if len(parts) == 2:
            part1 = parts[0].strip()
            part2 = parts[1].strip()
            part1_words = len(part1.split())
            part2_words = len(part2.split())
            if part1_words <= 2 and part2_words >= 3:
                return part2
            elif part2_words <= 2 and part1_words >= 3:
                return part1
            else:
                return part2
    return stem

def export_books():
    """Export books data to JSON."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, title, author FROM books ORDER BY title')
    books = cursor.fetchall()
    
    # Check which books exist in the books folder
    books_dir = Path("books")
    file_books = []
    if books_dir.exists():
        for file_path in books_dir.glob("*"):
            if file_path.suffix.lower() in ['.txt', '.epub']:
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
                        file_books.append((title, author, title.lower().strip(), author.lower().strip()))
    
    matched_files = set()
    books_list = []
    
    for book in books:
        db_title = book['title'].lower().strip()
        db_author = (book['author'] or '').lower().strip()
        
        # Skip books without authors
        if not db_author or db_author == 'unknown':
            continue
        
        # Exclude summaries or derivative works
        if 'summary of' in db_title or 'paperback' in db_title.lower():
            is_starting = False
        else:
            is_starting = False
            matched_file_idx = -1
            
            # Try exact match first
            for idx, (file_title, file_author, file_title_lower, file_author_lower) in enumerate(file_books):
                if idx in matched_files:
                    continue
                
                # Exact match
                if db_title == file_title_lower and db_author == file_author_lower:
                    is_starting = True
                    matched_file_idx = idx
                    break
                
                # Fuzzy match
                title_similar = (db_title in file_title_lower or file_title_lower in db_title) and len(db_title) > 5
                author_match = db_author == file_author_lower or (db_author and file_author_lower and (db_author in file_author_lower or file_author_lower in db_author))
                
                if title_similar and author_match and 'summary' not in db_title:
                    is_starting = True
                    matched_file_idx = idx
                    break
            
            if is_starting and matched_file_idx >= 0:
                matched_files.add(matched_file_idx)
        
        books_list.append({
            'id': book['id'],
            'title': book['title'],
            'author': book['author'],
            'is_starting': is_starting
        })
    
    conn.close()
    return books_list

def export_references():
    """Export references data to JSON."""
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
    return references_list

def main():
    """Main export function."""
    print("Exporting database to static JSON files...")
    
    # Create docs directory for GitHub Pages
    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)
    
    # Export books
    print("Exporting books...")
    books = export_books()
    books_file = docs_dir / "books.json"
    with open(books_file, 'w', encoding='utf-8') as f:
        json.dump(books, f, indent=2, ensure_ascii=False)
    print(f"  Exported {len(books)} books to {books_file}")
    
    # Export references
    print("Exporting references...")
    references = export_references()
    references_file = docs_dir / "references.json"
    with open(references_file, 'w', encoding='utf-8') as f:
        json.dump(references, f, indent=2, ensure_ascii=False)
    print(f"  Exported {len(references)} references to {references_file}")
    
    # Copy static files to docs
    print("Copying static files...")
    static_dir = Path("static")
    if static_dir.exists():
        import shutil
        docs_static = docs_dir / "static"
        if docs_static.exists():
            shutil.rmtree(docs_static)
        shutil.copytree(static_dir, docs_static)
        print(f"  Copied static files to {docs_static}")
    
    # Create static index.html
    print("Creating static index.html...")
    create_static_index(docs_dir)
    
    print("\n✅ Export complete!")
    print(f"📁 Static files are in: {docs_dir}")
    print("\nTo deploy to GitHub Pages:")
    print("  1. Commit the docs/ folder")
    print("  2. Go to Settings > Pages in your GitHub repo")
    print("  3. Set source to 'Deploy from a branch'")
    print("  4. Select branch: main (or master)")
    print("  5. Select folder: /docs")
    print("  6. Save")

def create_static_index(docs_dir):
    """Create a static version of index.html that loads from JSON files."""
    # Read the template
    template_file = Path("templates/index.html")
    if not template_file.exists():
        print(f"  ⚠️  Template not found: {template_file}")
        return
    
    with open(template_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # Replace Flask url_for with direct paths
    html_content = html_content.replace(
        '{{ url_for(\'static\', filename=\'style.css\') }}',
        'static/style.css'
    )
    html_content = html_content.replace(
        '{{ url_for(\'static\', filename=\'main.js\') }}?v=5',
        'static/main.js?v=6'
    )
    
    # Update the verify link - keep it but make it point to a note about static mode
    html_content = html_content.replace(
        '<a href="/verify" class="verify-link">Verify References</a>',
        '<a href="#" class="verify-link" onclick="alert(\'Verification requires the Flask server. Run locally with: python app.py\'); return false;">Verify References</a>'
    )
    
    # Write static version
    index_file = docs_dir / "index.html"
    with open(index_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"  Created {index_file}")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

