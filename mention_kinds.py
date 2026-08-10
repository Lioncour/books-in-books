#!/usr/bin/env python3
"""
Mention kind classification + domain lookups (movie / music / cocktail).
Books stay the default path in process_books; these helpers branch Lucien's filing.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

import requests

VALID_KINDS = ("book", "movie", "music", "cocktail")

# Well-known franchises / titles that are usually film/TV when mentioned as works
MEDIA_AS_MOVIE = {
    "star trek",
    "star wars",
    "stargate",
    "battlestar galactica",
    "short circuit",
    "aliens",
    "blade runner",
    "the matrix",
    "jurassic park",
    "indiana jones",
    "back to the future",
    "the godfather",
    "pulp fiction",
    "fight club",
    "terminator",
    "the terminator",
    "alien",
    "predator",
    "mad max",
    "ghostbusters",
}


def is_known_media_title(title: str) -> bool:
    """Franchise names that exist as film/TV and often as novels too."""
    return _norm(title) in MEDIA_AS_MOVIE

FILM_CONTEXT_CUES = re.compile(
    r"\b(film|movie|cinema|screenwriter|directed by|on screen|watched|"
    r"box[\s-]?office|trailer|screening|feature film|motion picture)\b",
    re.I,
)

MUSIC_CONTEXT_CUES = re.compile(
    r"\b(song|album|track|lyrics|playlist|listening to|on the radio|"
    r"single|lp|ep|band|singer|musician|soundtrack)\b",
    re.I,
)

COCKTAIL_CONTEXT_CUES = re.compile(
    r"\b(cocktail|drink|drank|drinking|bartender|bar|ordered a|mixed|"
    r"glass of|shot of|neat|on the rocks|with a twist|"
    r"whiskey|whisky|bourbon|gin|vodka|rum|tequila|bitters|vermouth|"
    r"campari|soda|tonic|mojito|martini)\b",
    re.I,
)

# Curated cocktail names (normalized keys → display title)
COCKTAIL_NAMES = {
    "martini": "Martini",
    "dry martini": "Dry Martini",
    "vodka martini": "Vodka Martini",
    "negroni": "Negroni",
    "old fashioned": "Old Fashioned",
    "manhattan": "Manhattan",
    "margarita": "Margarita",
    "mojito": "Mojito",
    "daiquiri": "Daiquiri",
    "whiskey sour": "Whiskey Sour",
    "whisky sour": "Whiskey Sour",
    "gin and tonic": "Gin and Tonic",
    "gin tonic": "Gin and Tonic",
    "bloody mary": "Bloody Mary",
    "moscow mule": "Moscow Mule",
    "espresso martini": "Espresso Martini",
    "aperol spritz": "Aperol Spritz",
    "french 75": "French 75",
    "sidecar": "Sidecar",
    "cosmopolitan": "Cosmopolitan",
    "pina colada": "Piña Colada",
    "piña colada": "Piña Colada",
    "mai tai": "Mai Tai",
    "mint julep": "Mint Julep",
    "sazerac": "Sazerac",
    "boulevardier": "Boulevardier",
    "paper plane": "Paper Plane",
    "last word": "Last Word",
    "bee s knees": "Bee's Knees",
    "bees knees": "Bee's Knees",
    "tom collins": "Tom Collins",
    "white russian": "White Russian",
    "irish coffee": "Irish Coffee",
    "sangria": "Sangria",
    "mimosa": "Mimosa",
    "paloma": "Paloma",
    "dark and stormy": "Dark and Stormy",
    "penicillin": "Penicillin",
    "amaretto sour": "Amaretto Sour",
    "aviation": "Aviation",
    "rum and coke": "Rum and Coke",
    "rum coke": "Rum and Coke",
    "rum & coke": "Rum and Coke",
}

# Names that are also common English / place words — require drink context
AMBIGUOUS_COCKTAILS = {
    "manhattan",
    "aviation",
    "cosmopolitan",
    "old fashioned",
    "last word",
    "sidecar",
    "mimosa",
    "penicillin",
}


def _norm(s: str) -> str:
    text = (s or "").lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def match_cocktail(title: str) -> Optional[str]:
    key = _norm(title)
    if key in COCKTAIL_NAMES:
        return COCKTAIL_NAMES[key]
    for k, display in COCKTAIL_NAMES.items():
        if len(k) < 5:
            continue
        if key.endswith(" " + k) or key.startswith(k + " "):
            return display
    return None


def classify_kind(title: str, context: Optional[str] = None) -> str:
    """
    Guess mention kind from context + lexicons.
    Franchise names (Star Trek, etc.) are NOT forced to movie — there are novels too.
    Film/TV cues → movie; drink/music cues → those kinds; else book.
    """
    ctx = context or ""
    key = _norm(title)

    if key in COCKTAIL_NAMES:
        if key in AMBIGUOUS_COCKTAILS:
            if COCKTAIL_CONTEXT_CUES.search(ctx):
                return "cocktail"
        else:
            return "cocktail"
    elif match_cocktail(title) and COCKTAIL_CONTEXT_CUES.search(ctx):
        return "cocktail"

    # Explicit film/TV language wins even for franchises that also have novels
    if FILM_CONTEXT_CUES.search(ctx):
        return "movie"

    if MUSIC_CONTEXT_CUES.search(ctx):
        return "music"

    if re.search(r"\b(song|suite|symphony|sonata)\b", title, re.I):
        return "music"

    # Novel/book cues for franchise titles (e.g. "Star Trek novel")
    if re.search(r"\b(novel|novella|paperback|hardcover|ebook|trilogy)\b", ctx, re.I):
        return "book"

    # Ambiguous franchise bare titles: leave as book so Lucien can verify via
    # Open Library (novelizations exist). Film path still used when cues say so.
    return "book"


def lookup_movie(title: str) -> Optional[Dict]:
    """Prefer TMDb when TMDB_API_KEY is set; else Wikidata film search."""
    tmdb_key = os.environ.get("TMDB_API_KEY", "").strip()
    if tmdb_key:
        hit = _lookup_tmdb(title, tmdb_key)
        if hit:
            return hit
    return _lookup_wikidata_film(title)


def _lookup_tmdb(title: str, api_key: str) -> Optional[Dict]:
    from process_books import title_similarity, token_coverage

    try:
        response = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params={"api_key": api_key, "query": title, "include_adult": "false"},
            timeout=20,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    except Exception:
        return None

    best = None
    best_score = 0.0
    for item in results[:8]:
        candidate = item.get("title") or item.get("original_title") or ""
        if not candidate:
            continue
        score = title_similarity(title, candidate)
        coverage = token_coverage(title, candidate)
        if coverage < 0.7 or score < 0.72:
            continue
        if score > best_score:
            best_score = score
            year = (item.get("release_date") or "")[:4]
            poster = item.get("poster_path")
            best = {
                "title": candidate,
                "author": year or "Film",
                "open_library_id": f"tmdb_{item.get('id')}",
                "match_score": round(score, 3),
                "cover_id": None,
                "kind": "movie",
                "cover_url": (
                    f"https://image.tmdb.org/t/p/w185{poster}" if poster else None
                ),
            }
    if best and best["match_score"] >= 0.75:
        return best
    return None


def _lookup_wikidata_film(title: str) -> Optional[Dict]:
    from process_books import title_similarity, normalize_title

    try:
        response = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": title,
                "language": "en",
                "type": "item",
                "limit": 6,
                "format": "json",
            },
            headers={"User-Agent": "BooksInBooks/1.0 (literary catalog)"},
            timeout=20,
        )
        response.raise_for_status()
        hits = response.json().get("search") or []
    except Exception:
        return None

    best = None
    best_score = 0.0
    for hit in hits:
        label = hit.get("label") or ""
        desc = (hit.get("description") or "").lower()
        is_film = any(
            token in desc
            for token in (
                "film",
                "movie",
                "motion picture",
                "television series",
                "tv series",
                "television program",
            )
        )
        if not is_film:
            continue
        score = title_similarity(title, label)
        if score < 0.78:
            continue
        if score > best_score:
            best_score = score
            qid = hit.get("id") or normalize_title(label)
            best = {
                "title": label,
                "author": hit.get("description") or "Film",
                "open_library_id": f"wikidata_{qid}",
                "match_score": round(score, 3),
                "cover_id": None,
                "kind": "movie",
            }
            try:
                from process_books import lookup_wikipedia_thumb

                best["cover_url"] = lookup_wikipedia_thumb(label)
            except Exception:
                best["cover_url"] = None
    return best


def lookup_music(title: str) -> Optional[Dict]:
    """MusicBrainz recording search (no API key)."""
    from process_books import title_similarity, normalize_title

    try:
        response = requests.get(
            "https://musicbrainz.org/ws/2/recording",
            params={"query": f'recording:"{title}"', "fmt": "json", "limit": 5},
            headers={"User-Agent": "BooksInBooks/1.0 (literary catalog)"},
            timeout=20,
        )
        response.raise_for_status()
        recordings = response.json().get("recordings") or []
    except Exception:
        return None

    best = None
    best_score = 0.0
    for rec in recordings:
        candidate = rec.get("title") or ""
        if not candidate:
            continue
        score = title_similarity(title, candidate)
        if score < 0.82:
            continue
        artist = "Unknown"
        credits = rec.get("artist-credit") or []
        if credits:
            artist = credits[0].get("name") or artist
        if score > best_score:
            best_score = score
            best = {
                "title": candidate,
                "author": artist,
                "open_library_id": f"mb_{rec.get('id') or normalize_title(candidate)}",
                "match_score": round(score, 3),
                "cover_id": None,
                "kind": "music",
            }
    if best and best["match_score"] >= 0.85:
        return best
    return None


def lookup_cocktail(title: str) -> Optional[Dict]:
    display = match_cocktail(title)
    if not display:
        return None
    from process_books import normalize_title

    return {
        "title": display,
        "author": "Cocktail",
        "open_library_id": f"cocktail_{normalize_title(display)}",
        "match_score": 1.0,
        "cover_id": None,
        "kind": "cocktail",
    }


def lookup_by_kind(kind: str, title: str) -> Optional[Dict]:
    if kind == "movie":
        return lookup_movie(title)
    if kind == "music":
        return lookup_music(title)
    if kind == "cocktail":
        return lookup_cocktail(title)
    return None
