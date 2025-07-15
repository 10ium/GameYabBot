import logging
import aiohttp
from typing import List, Dict, Any
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class ITADSource:
    # استفاده از صفحه اصلی که همیشه در دسترس است
    DEALS_URL = "https://isthereanydeal.com/"
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'}

    def _normalize_game_data(self, card: Any) -> Dict[str, str]:
        title_element = card.select_one('a.card-title')
        title = title_element.text.strip() if title_element else 'بدون عنوان'
        relative_url = title_element['href'] if title_element else ''
        url = f"https://isthereanydeal.com{relative_url}"
        
        store_element = card.select_one('.shop-tag .name')
        store = store_element.text.strip() if store_element else 'نامشخص'
        
        return {"title": title, "store": store, "url": url, "id_in_db": url}

    async def fetch_free_games(self) -> List[Dict[str, str]]:
        logging.info("شروع فرآیند دریافت بازی‌های رایگان با اسکرپینگ از IsThereAnyDeal...")
        free_games_list = []
        try:
            async with aiohttp.ClientSession(headers=self.HEADERS) as session:
                async with session.get(self.DEALS_URL) as response:
                    response.raise_for_status()
                    html_content = await response.text()
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # پیدا کردن تمام کارت‌های بازی که برچسب "100% off" دارند
                    game_cards = soup.select('.card-container .card')
                    for card in game_cards:
                        discount_tag = card.select_one('.deal-cut')
                        if discount_tag and '100%' in discount_tag.text:
                            normalized_game = self._normalize_game_data(card)
                            free_games_list.append(normalized_game)
                            logging.info(f"بازی رایگان از ITAD (Scraping) یافت شد: {normalized_game['title']} در فروشگاه {normalized_game['store']}")
        except Exception as e:
            logging.error(f"خطا در ماژول ITAD (Scraping): {e}", exc_info=True)
        return free_games_list
