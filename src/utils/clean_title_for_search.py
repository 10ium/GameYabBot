import re

def clean_title_for_search(title: str) -> str:
    """
    عنوان بازی را برای جستجو تمیز می‌کند.
    کاراکترهای خاص، اطلاعات پلتفرم، و قیمت‌ها را حذف می‌کند.
    """
    if not isinstance(title, str):
        return ""

    # حذف اطلاعات قیمت و تخفیف
    # مثال: ($19.49/ -35% off), (€40.89 / $40.75 / -18% off), [$4.99–> Free]
    title = re.sub(r'\s*[\(\[]?(\$|€|£)\d+(\.\d{1,2})?(\s*-\s*(\$|€|£)\d+(\.\d{1,2})?)?\s*(\/\s*(-?\d+%? off|\s*FREE))?[\)\]]?', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s*(\d+% off|\s*FREE)\s*', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s*(\d+\.\d{2} ?[€$£])\s*', '', title, flags=re.IGNORECASE).strip() # حذف قیمت‌های تنها
    title = re.sub(r'\s*(\d+%\s*discount)\s*', '', title, flags=re.IGNORECASE).strip() # حذف "X% discount"

    # حذف پلتفرم‌ها و تگ‌های اضافی در براکت یا پرانتز
    # مثال: [PC], [Steam], (Epic Games), [iOS / Android]
    title = re.sub(r'\s*[\(\[]?(pc|steam|epic games|egs|gog|xbox|ps|playstation|switch|nintendo|android|googleplay|google play|ios|apple|mac|macos|linux|windows|multi-platform|multiplatform|vr|oculus)[\]\)]?', '', title, flags=re.IGNORECASE).strip()
    
    # حذف عبارات رایج مربوط به giveaway/freebie
    title = re.sub(r'\s*(100% off|free|complimentary|giveaway|gratis|freebie|for free|free to keep|free forever)\s*', '', title, flags=re.IGNORECASE).strip()

    # حذف نسخه‌ها و بسته‌ها که ممکن است در عنوان اصلی نباشند اما برای جستجو مزاحم باشند
    # مثال: Definitive Edition, Platinum Edition, Ultimate Edition, Deluxe Edition, Bundle, Pack
    title = re.sub(r'\s*(definitive|platinum|ultimate|deluxe|gold|collectors|remastered|remake|reforged|hd|vr|complete|anniversary|enhanced|game of the year|goty) edition\s*', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'\s*(bundle|pack|collection)\s*', '', title, flags=re.IGNORECASE).strip()
    
    # حذف کاراکترهای خاص و اضافی
    title = re.sub(r'[^a-zA-Z0-9\s]', '', title).strip() # فقط حروف الفبا، اعداد و فاصله را نگه دار
    title = re.sub(r'\s+', ' ', title).strip() # حذف فاصله‌های اضافی

    return title
