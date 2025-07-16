import re

def clean_title_for_search(title: str) -> str:
    """
    عنوان بازی را برای جستجو در APIهای خارجی تمیز می‌کند.
    عبارات براکتی (مانند [Windows], [Multi-Platform], [iOS])،
    عبارات پرانتزی مربوط به قیمت/وضعیت (مانند ($X -> Free), (X% off), (Free))،
    و سایر جزئیات اضافی را حذف می‌کند.
    """
    original_title = title.strip()
    if not original_title:
        return ""

    # حذف عبارات براکتی (مانند [Windows], [Multi-Platform], [iOS])
    cleaned_title = re.sub(r'\[.*?\]', '', original_title).strip()
    
    # حذف عبارات پرانتزی مربوط به قیمت یا وضعیت (مانند ($X -> Free), (X% off), (Free))
    cleaned_title = re.sub(r'\s*\(\$.*?->\s*Free\)', '', cleaned_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r'\s*\(\d+%\s*off\)', '', cleaned_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r'\s*\(\s*free\s*\)', '', cleaned_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r'\s*\(\s*game\s*\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف (Game)
    cleaned_title = re.sub(r'\s*\(\s*app\s*\)', '', cleaned_title, flags=re.IGNORECASE).strip() # حذف (App)
    
    # حذف عبارات مربوط به قیمت و تخفیف که ممکن است در عنوان باقی مانده باشند
    cleaned_title = re.sub(r'\b(CA\$|€|\$)\d+(\.\d{1,2})?\s*→\s*Free\b', '', cleaned_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r'\b\d+(\.\d{1,2})?\s*-->\s*0\b', '', cleaned_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r'\b\d+(\.\d{1,2})?\s*to\s*free\s*lifetime\b', '', cleaned_title, flags=re.IGNORECASE).strip() # برای AppHookup
    
    # حذف هرگونه فاصله اضافی
    cleaned_title = re.sub(r'\s+', ' ', cleaned_title).strip()
    
    # Fallback به عنوان اصلی اگر تمیز کردن باعث خالی شدن عنوان شد
    if not cleaned_title:
        return original_title

    return cleaned_title
