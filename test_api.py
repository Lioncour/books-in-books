#!/usr/bin/env python3
import requests
import json

# Test the books API
response = requests.get('http://localhost:5000/api/books')
books = response.json()

print("Books API Response:")
print("=" * 50)
for book in books:
    print(f"ID: {book['id']}, Title: {book['title'][:50]}..., Starting: {book['is_starting']}")

# Show starting books
starting_books = [b for b in books if b['is_starting']]
print(f"\nStarting books ({len(starting_books)}):")
for book in starting_books:
    print(f"- {book['title']}") 