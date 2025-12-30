#!/usr/bin/env python3
from pathlib import Path
import sqlite3

# Check what titles are extracted from files
books_dir = Path("books")
existing_files = set()
print("Files in books/ folder:")
print("=" * 30)
for file_path in books_dir.glob("*"):
    if file_path.suffix.lower() in ['.txt', '.epub']:
        title = file_path.stem
        if " - " in title:
            # The format is "Author - Title", so we want the second part
            parts = title.split(" - ")
            if len(parts) >= 2:
                title = parts[1].strip()  # Take the title part
            else:
                title = parts[0].strip()  # Fallback
        existing_files.add(title.lower())
        print(f"File: {file_path.name}")
        print(f"  Extracted title: '{title}'")
        print(f"  Lowercase: '{title.lower()}'")
        print()

print("Extracted titles (lowercase):")
for title in existing_files:
    print(f"  '{title}'")

# Check database titles
print("\nDatabase titles:")
print("=" * 30)
conn = sqlite3.connect('database.db')
cursor = conn.cursor()
cursor.execute('SELECT id, title, author FROM books ORDER BY title')
books = cursor.fetchall()

for book in books:
    db_title = book[1]  # title is at index 1
    db_title_lower = db_title.lower()
    is_match = db_title_lower in existing_files
    print(f"ID: {book[0]}")
    print(f"  Title: '{db_title}'")
    print(f"  Lowercase: '{db_title_lower}'")
    print(f"  Matches file: {is_match}")
    print()

conn.close() 