#!/usr/bin/env python3
"""
Verification script to check what book references were found in the processed books.
Shows which references were detected and allows manual verification.
"""

import sqlite3
import spacy
from pathlib import Path
import re
from collections import defaultdict

# Load spaCy model
try:
    nlp = spacy.load("en_core_web_trf")
except OSError:
    print("spaCy model not found. Please install with: python -m spacy download en_core_web_trf")
    exit(1)

def get_db_connection():
    """Create a database connection."""
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def find_references_in_text(text, source_title):
    """Find all WORK_OF_ART entities in text and return with context."""
    doc = nlp(text)
    references = []
    
    for ent in doc.ents:
        if ent.label_ == "WORK_OF_ART":
            referenced_title = ent.text.strip()
            if referenced_title and len(referenced_title) > 2:
                # Get context (50 chars before and after)
                start = max(0, ent.start_char - 50)
                end = min(len(text), ent.end_char + 50)
                context = text[start:end].replace('\n', ' ').strip()
                
                references.append({
                    'title': referenced_title,
                    'context': context,
                    'start': ent.start_char,
                    'end': ent.end_char
                })
    
    return references

def verify_book_references():
    """Verify references found for each starting book."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get all starting books (books in the books folder)
    books_dir = Path("books")
    if not books_dir.exists():
        print("Books directory not found!")
        return
    
    print("=" * 80)
    print("BOOK REFERENCE VERIFICATION")
    print("=" * 80)
    print()
    
    # Get all files
    files = list(books_dir.glob("*.txt")) + list(books_dir.glob("*.epub"))
    
    if not files:
        print("No book files found in books/ directory!")
        return
    
    for file_path in files:
        print(f"\n{'='*80}")
        print(f"FILE: {file_path.name}")
        print(f"{'='*80}")
        
        # Get book from database
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
        
        cursor.execute('SELECT id FROM books WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))', (title,))
        result = cursor.fetchone()
        
        if not result:
            print(f"  [WARNING] Book '{title}' not found in database. Run process_books.py first.")
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
        
        print(f"\nBook: {title} by {author}")
        print(f"   Database ID: {source_book_id}")
        print(f"   References found in database: {len(db_references)}")
        
        if db_references:
            print(f"\n   Referenced books:")
            for ref in db_references:
                print(f"     - {ref['title']} by {ref['author'] or 'Unknown'}")
        
        # Now verify by re-processing the file
        print(f"\n   Re-analyzing file to verify references...")
        
        try:
            if file_path.suffix.lower() == '.epub':
                import ebooklib
                from ebooklib import epub
                book = epub.read_epub(str(file_path))
                text = ''
                for item in book.get_items():
                    if item.get_type() == ebooklib.ITEM_DOCUMENT:
                        text += item.get_content().decode('utf-8', errors='ignore') + '\n'
                text = re.sub('<[^<]+?>', '', text)
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            
            # Find references with context
            found_references = find_references_in_text(text, title)
            
            # Group by title
            refs_by_title = defaultdict(list)
            for ref in found_references:
                refs_by_title[ref['title']].append(ref)
            
            print(f"   Found {len(found_references)} WORK_OF_ART entities")
            print(f"   Unique titles: {len(refs_by_title)}")
            
            if refs_by_title:
                print(f"\n   Detected references (with context):")
                for ref_title, contexts in sorted(refs_by_title.items()):
                    print(f"\n     - '{ref_title}' ({len(contexts)} occurrence(s))")
                    # Show first 3 contexts
                    for i, ctx in enumerate(contexts[:3], 1):
                        try:
                            context_str = ctx['context'].encode('ascii', errors='replace').decode('ascii')
                            print(f"       [{i}] ...{context_str}...")
                        except:
                            print(f"       [{i}] [Context contains non-ASCII characters]")
                    if len(contexts) > 3:
                        print(f"       ... and {len(contexts) - 3} more occurrence(s)")
            
            # Compare with database
            db_titles = {ref['title'].lower().strip() for ref in db_references}
            found_titles = {title.lower().strip() for title in refs_by_title.keys()}
            
            print(f"\n   Verification:")
            print(f"      In database: {len(db_titles)} unique references")
            print(f"      Found in text: {len(found_titles)} unique references")
            
            # Find matches and mismatches
            matches = db_titles & found_titles
            only_in_db = db_titles - found_titles
            only_in_text = found_titles - db_titles
            
            if matches:
                print(f"      [OK] Matches: {len(matches)}")
            if only_in_db:
                print(f"      [WARNING] Only in database (not found in current scan): {len(only_in_db)}")
                for title_only_db in sorted(only_in_db)[:5]:
                    print(f"         - {title_only_db}")
            if only_in_text:
                print(f"      [WARNING] Only in text (not in database): {len(only_in_text)}")
                for title_only_text in sorted(only_in_text)[:5]:
                    print(f"         - {title_only_text}")
        
        except Exception as e:
            print(f"   [ERROR] Error processing file: {e}")
            import traceback
            traceback.print_exc()
    
    conn.close()
    print(f"\n{'='*80}")
    print("Verification complete!")
    print(f"{'='*80}")

if __name__ == "__main__":
    verify_book_references()

