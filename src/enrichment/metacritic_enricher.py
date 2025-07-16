import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional
import re # برای استفاده از regex
from bs4 import BeautifulSoup
import random # برای تأخیر تصادفی

# تنظیمات اولیه لاگ‌گیری
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MetacriticEnricher:
    """
    کلاسی برای غنی‌سازی داده‌های بازی با استفاده از اطلاعات Metacritic.
    این کلاس نمرات منتقدان و کاربران، ژانرها، و رده‌بندی سنی را از Metacritic استخراج می‌کند.
    """
    BASE_URL = "https://www.metacritic.com"
    SEARCH_URL = f"{BASE_URL}/search/game/"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Connection': 'keep-alive'
    }

    def _clean_title_for_metacritic_search(self, title: str) -> str:
        """
        عنوان بازی را برای جستجوی بهتر در Metacritic تمیز می‌کند.
        کاراکترهای خاص را حذف کرده و فواصل اضافی را از بین می‌برد.
        """
        # حذف کاراکترهای غیر الفبایی-عددی (به جز فاصله)
        cleaned_title = re.sub(r'[^\w\s]', '', title)
        # جایگزینی فواصل متعدد با یک فاصله
        cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()
        return cleaned_title.replace(' ', '-') # Metacritic از خط تیره برای فواصل استفاده می‌کند

    async def _search_metacritic(self, session: aiohttp.ClientSession, game_title: str) -> Optional[str]:
        """
        عنوان بازی را در Metacritic جستجو می‌کند و URL صفحه بازی را برمی‌گرداند.
        """
        search_query = self._clean_title_for_metacritic_search(game_title)
        if not search_query:
            logger.warning(f"عنوان بازی برای جستجوی Metacritic خالی است: '{game_title}'")
            return None

        full_search_url = f"{self.SEARCH_URL}{search_query}/results"
        logger.info(f"در حال جستجو در Metacritic برای: '{game_title}' (URL: {full_search_url})")

        try:
            await asyncio.sleep(random.uniform(1, 3)) # تأخیر تصادفی برای جلوگیری از بلاک شدن
            async with session.get(full_search_url, headers=self.HEADERS) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # Metacritic layout has changed. Look for search results.
                # The search results are typically in a <tbody> with class 'body'
                # and each result is an <a> tag within an <h3>.
                search_results_container = soup.find('ul', class_='search-results')
                if not search_results_container:
                    search_results_container = soup.find('div', class_='search_results') # Fallback for older layouts

                if search_results_container:
                    # Find the first game result link
                    first_game_link = search_results_container.find('a', class_='title')
                    if first_game_link and 'href' in first_game_link.attrs:
                        game_page_url = self.BASE_URL + first_game_link['href']
                        logger.info(f"لینک Metacritic برای '{game_title}' یافت شد: {game_page_url}")
                        return game_page_url
                    else:
                        logger.warning(f"هیچ لینک بازی در نتایج جستجوی Metacritic برای '{game_title}' یافت نشد.")
                else:
                    logger.warning(f"کانتینر نتایج جستجو در Metacritic برای '{game_title}' یافت نشد.")
                
                # Fallback: اگر صفحه جستجو مستقیماً به صفحه بازی ریدایرکت شد (برای تطابق دقیق)
                # این ممکن است در response.url پس از ریدایرکت موجود باشد
                if "game/" in str(response.url) and "search/" not in str(response.url):
                    logger.info(f"ریدایرکت مستقیم به صفحه بازی Metacritic برای '{game_title}' شناسایی شد: {response.url}")
                    return str(response.url)

        except aiohttp.ClientResponseError as e:
            logger.error(f"خطای HTTP در جستجوی Metacritic برای '{game_title}': {e.status} - {e.message}")
        except Exception as e:
            logger.error(f"خطا در جستجوی Metacritic برای '{game_title}': {e}", exc_info=True)
        return None

    async def _parse_game_page(self, session: aiohttp.ClientSession, game_page_url: str) -> Dict[str, Any]:
        """
        اطلاعات را از صفحه بازی Metacritic استخراج می‌کند.
        """
        game_info = {}
        logger.info(f"در حال تجزیه صفحه Metacritic: {game_page_url}")

        try:
            await asyncio.sleep(random.uniform(1, 3)) # تأخیر تصادفی
            async with session.get(game_page_url, headers=self.HEADERS) as response:
                response.raise_for_status()
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # نمره منتقدان (Metascore)
                metascore_tag = soup.find('div', class_='c-siteReviewScore_score')
                if metascore_tag:
                    score_text = metascore_tag.get_text(strip=True)
                    try:
                        game_info['metacritic_score'] = int(score_text)
                    except ValueError:
                        logger.warning(f"نمره متاکریتیک نامعتبر برای {game_page_url}: {score_text}")

                # نمره کاربران (User Score)
                userscore_tag = soup.find('div', class_='c-siteReviewScore_score u-flexbox-column')
                if userscore_tag:
                    score_text = userscore_tag.get_text(strip=True)
                    try:
                        game_info['metacritic_userscore'] = float(score_text)
                    except ValueError:
                        logger.warning(f"نمره کاربر متاکریتیک نامعتبر برای {game_page_url}: {score_text}")

                # ژانرها
                genres_section = soup.find('div', class_='c-gameDetails_sectionContainer u-flexbox-column')
                if genres_section:
                    genre_tags = genres_section.find_all('li', class_='c-gameDetails_listItem')
                    genres = []
                    for tag in genre_tags:
                        genre_text = tag.find('span', class_='c-gameDetails_listItem_value').get_text(strip=True)
                        genres.append(genre_text)
                    if genres:
                        game_info['genres'] = genres

                # رده‌بندی سنی (ESRB, PEGI و غیره)
                age_rating_tag = soup.find('li', class_='c-gameDetails_listItem', attrs={'data-cy': 'gameDetails-ageRating'})
                if age_rating_tag:
                    age_rating_value_tag = age_rating_tag.find('span', class_='c-gameDetails_listItem_value')
                    if age_rating_value_tag:
                        game_info['age_rating'] = age_rating_value_tag.get_text(strip=True)

        except aiohttp.ClientResponseError as e:
            logger.error(f"خطای HTTP در تجزیه صفحه Metacritic {game_page_url}: {e.status} - {e.message}")
        except Exception as e:
            logger.error(f"خطا در تجزیه صفحه Metacritic {game_page_url}: {e}", exc_info=True)
        
        return game_info

    async def enrich_data(self, game_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        داده‌های بازی را با اطلاعات Metacritic غنی‌سازی می‌کند.
        """
        title = game_data.get('title')
        if not title:
            logger.warning("عنوان بازی برای غنی‌سازی Metacritic موجود نیست.")
            return game_data

        async with aiohttp.ClientSession() as session:
            game_page_url = await self._search_metacritic(session, title)
            if game_page_url:
                metacritic_info = await self._parse_game_page(session, game_page_url)
                game_data.update(metacritic_info)
                logger.info(f"داده‌های Metacritic برای '{title}' غنی‌سازی شد.")
            else:
                logger.info(f"صفحه Metacritic برای '{title}' یافت نشد. غنی‌سازی انجام نشد.")
        return game_data

