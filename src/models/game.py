# ===== TYPES & INTERFACES =====

from typing import TypedDict, List, Optional

class GameData(TypedDict, total=False):
    """
    Defines the structure for a game's data throughout the pipeline.
    `total=False` means keys are optional, which is perfect for a pipeline
    where data is progressively enriched.

    Attributes:
        title (str): The main title of the game or content.
        store (str): The canonical name of the store (e.g., 'steam', 'epicgames').
        url (str): The direct URL to the store page for the deal.
        id_in_db (str): A unique identifier from the source to prevent re-processing.
        is_free (bool): True if the item is 100% free, False if it's a discount.
        discount_text (Optional[str]): A display string for the discount (e.g., '80% off').

        description (Optional[str]): The original English description of the game.
        persian_summary (Optional[str]): The translated Persian summary.
        image_url (Optional[str]): URL for the game's header or box art.
        trailer (Optional[str]): URL to a promotional video/trailer.
        
        is_dlc_or_addon (bool): True if the content is classified as a DLC or add-on.

        steam_app_id (Optional[str]): The unique application ID on Steam.
        steam_overall_score (Optional[int]): Overall review score percentage from Steam.
        steam_overall_reviews_count (Optional[int]): Total number of reviews on Steam.

        metacritic_score (Optional[int]): The critic score from Metacritic (0-100).
        metacritic_userscore (Optional[float]): The user score from Metacritic (0-10).

        genres (Optional[List[str]]): List of genres in English.
        persian_genres (Optional[List[str]]): List of translated genres in Persian.
        is_multiplayer (bool): True if the game has multiplayer features.
        is_online (bool): True if the game has online features.
        age_rating (Optional[str]): The original age rating string (e.g., 'ESRB Teen').
        persian_age_rating (Optional[str]): The translated age rating.
        
        # Metadata from sources
        subreddit (Optional[str]): The name of the subreddit if the source was Reddit.
        productSlug (Optional[str]): The product slug from Epic Games.
    """
    # Core fields
    title: str
    store: str
    url: str
    id_in_db: str
    is_free: bool
    discount_text: Optional[str]

    # Enriched fields
    description: Optional[str]
    persian_summary: Optional[str]
    image_url: Optional[str]
    trailer: Optional[str]
    
    # Type classification
    is_dlc_or_addon: bool

    # Steam-specific fields
    steam_app_id: Optional[str]
    steam_overall_score: Optional[int]
    steam_overall_reviews_count: Optional[int]

    # Metacritic-specific fields
    metacritic_score: Optional[int]
    metacritic_userscore: Optional[float]

    # General metadata
    genres: Optional[List[str]]
    persian_genres: Optional[List[str]]
    is_multiplayer: bool
    is_online: bool
    age_rating: Optional[str]
    persian_age_rating: Optional[str]
    
    # Source metadata
    subreddit: Optional[str]
    productSlug: Optional[str]