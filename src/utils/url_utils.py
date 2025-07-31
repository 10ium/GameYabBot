// ===== IMPORTS & DEPENDENCIES =====
import re
from typing import Dict
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from src.models.game import GameData
from src.config import STORE_KEYWORD_MAP

// ===== UTILITY FUNCTIONS =====

def infer_store_from_game_data(game: GameData) -> str:
    """
    Infers the canonical store name from game data by checking URL and title.
    Returns 'other' if no specific store can be determined.
    """
    url = game.get('url', '').lower()
    title = game.get('title', '').lower()
    subreddit = game.get('subreddit', '').lower()

    # Check URL for known store domains
    for domain, store_name in STORE_KEYWORD_MAP.items():
        if domain in url:
            return store_name
            
    # Check title/subreddit for keywords as a fallback
    for keyword, store_name in STORE_KEYWORD_MAP.items():
        if keyword in title or keyword in subreddit:
            return store_name

    return 'other'

def normalize_url_for_key(url: str) -> str:
    """
    Normalizes a URL to create a consistent key for deduplication.
    Removes tracking parameters, fragments, and standardizes the path.
    """
    try:
        parsed = urlparse(url)
        
        # Remove common tracking parameters
        query_params = parse_qs(parsed.query)
        tracking_params = [
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 
            'ref', 'source', 'mc_cid', 'mc_eid', 'snr'
        ]
        for param in tracking_params:
            query_params.pop(param, None)
        
        # Standardize path: remove trailing slashes
        path = parsed.path.rstrip('/')
        
        # Special handling for Steam to use app ID as the most stable key
        if 'steampowered.com' in parsed.netloc:
            match = re.search(r'/(app|sub)/(\d+)', path)
            if match:
                return f"steam_{match.group(1)}_{match.group(2)}"
        
        # Special handling for Epic Games using product slug
        if 'epicgames.com' in parsed.netloc:
            match = re.search(r'/(?:p|product)/([^/]+)', path)
            if match:
                slug = match.group(1).replace('/home', '')
                return f"epic_product_{slug}"

        # Reconstruct the URL without fragment and with cleaned query
        cleaned_query = urlencode(query_params, doseq=True)
        
        # Use a combination of domain and path as the key
        key = f"{parsed.netloc}{path}"
        if cleaned_query:
            key += f"?{cleaned_query}"
            
        return key

    except Exception:
        # Fallback for malformed URLs
        match = re.search(r'https?://(?:www\.)?([^/?#]+)', url)
        return match.group(1) if match else url
