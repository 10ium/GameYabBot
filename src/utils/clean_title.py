// ===== UTILITY FUNCTIONS =====
import re

def clean_title_for_search(raw_title: str) -> str:
    """
    Cleans a game title for searching by removing store tags, platform info,
    and other noise common in deal posts. This helps in finding a canonical
    version of the game on services like Steam or Metacritic.
    """
    if not raw_title:
        return ""

    # Remove store names in brackets, e.g., [Steam], [Epic Games]
    # Also removes platform names and DRM info.
    cleaned = re.sub(
        r'\[\s*(steam|epic\s*games?|gog|itch\.io|uplay|origin|drm-?free|pc|windows|mac|linux)\s*\]',
        '', raw_title, flags=re.IGNORECASE
    )
    
    # Remove platform names in parentheses
    cleaned = re.sub(r'\(\s*(pc|windows|mac|linux)\s*\)', '', cleaned, flags=re.IGNORECASE)

    # Remove deal-specific text like "(100% off)" or "(Free)"
    cleaned = re.sub(r'\(\s*(100%\s*off|free\s*to\s*keep|free)\s*\)', '', cleaned, flags=re.IGNORECASE)
    
    # Remove common promotional suffixes like "- 100% OFF"
    cleaned = re.sub(r'\s*-\s*100%\s*off', '', cleaned, flags=re.IGNORECASE)

    # Collapse multiple spaces into a single space and strip leading/trailing whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned
