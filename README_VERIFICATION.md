# Book Reference Verification

This document explains how to verify that book references are being correctly detected.

## Quick Verification

Run the verification script to see what references were found:

```bash
python verify_references.py
```

This will:
1. Show all references found in the database for each book
2. Re-analyze each book file to find WORK_OF_ART entities
3. Compare database references with newly found references
4. Show context snippets where references were detected

## Understanding the Results

### Matches
References that appear in both the database and the current text scan are likely correct.

### Only in Database
These might be:
- References found in a different part of the text (spaCy processes in chunks)
- Variations in text extraction (EPUB parsing differences)
- False positives that should be removed

### Only in Text
These might be:
- New references not yet in the database
- False positives (like TV shows, movies, or non-book works)
- Variations in entity recognition

## Common Issues

### False Positives
spaCy's WORK_OF_ART entity can detect:
- TV shows ("Friends", "The Wire")
- Movies ("Star Wars", "Scarface")
- Albums ("Kings of Leon")
- Other works of art

These are not books and may appear in the results.

### Self-References
Books sometimes reference themselves (e.g., in acknowledgments). These are automatically filtered out.

### Title Variations
The same book might be detected as:
- "The Lord of the Rings"
- "Lord of the Rings"
- "LOTR"

The system tries to match these, but some variations may slip through.

## API Endpoint

You can also check references via the API:

```
GET /api/book/<book_id>/references
```

This returns all references found for a specific book.

## Improving Accuracy

1. **Review false positives**: Manually check the verification output
2. **Adjust entity recognition**: The spaCy model can be fine-tuned
3. **Add filters**: Filter out common false positives (TV shows, movies, etc.)
4. **Manual curation**: Review and correct the database manually

