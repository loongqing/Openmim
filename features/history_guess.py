"""看图猜时代/地区小游戏 — 图源获取、题目生成、难度选择、答案判定、游戏状态管理。

从 features/playables.py 拆分而来。
"""
import asyncio
import json
import logging
import random
import re
from io import BytesIO
from datetime import datetime, timezone
from typing import Optional

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, ApplicationHandlerStop, filters

from stores.playables_db import DB_PATH, HistoryGuessGameRow, orm_session
from stores.timestamps import utc_now_iso
from llm.llm_client import get_llm_client
from features.playables import _play_text, _play_list

logger = logging.getLogger(__name__)

_prepared_guess_item: dict | None = None
_prepare_guess_task: asyncio.Task | None = None

REVEAL_KEYWORDS = tuple(_play_list("guess.reveal_keywords", ["公布答案", "显示答案", "揭晓", "看答案", "公布下答案", "show answer", "reveal", "答案"]))
COMMAND_STARTS = ("guesshistory",)
TEXT_STARTS = tuple(_play_list("guess.text_starts", ["猜时代", "猜历史", "开始猜图", "历史猜图"]))

LOC_SEARCH_TERMS = [
    "street scene photograph",
    "city street photograph",
    "market street photograph",
    "railway station photograph",
    "harbor photograph",
    "historic building photograph",
    "public square photograph",
    "school children photograph",
    "factory workers photograph",
    "soldiers photograph",
    "main street photograph",
    "bridge photograph",
    "tram street photograph",
    "automobile street photograph",
    "horse carriage street photograph",
    "people street photograph",
]


COMMONS_SEARCH_TERMS = [
    "historical photograph Paris 1900",
    "historical photograph London 1900",
    "historical photograph New York 1900",
    "old photograph Tokyo 1930",
    "old photograph tram 1920",
    "old photograph railway station 1900",
    "Paris 1900 photograph",
    "London 1900 photograph",
    "New York 1900 photograph",
    "Tokyo 1930 photograph",
]


RANDOM_PLACES = [
    # Europe (11)
    ("Paris", "France", "Europe"), ("London", "United Kingdom", "Europe"),
    ("Berlin", "Germany", "Europe"), ("Vienna", "Austria", "Europe"),
    ("Amsterdam", "Netherlands", "Europe"), ("Rome", "Italy", "Europe"),
    ("Madrid", "Spain", "Europe"), ("Moscow", "Russia", "Europe"),
    ("Prague", "Czech Republic", "Europe"), ("Warsaw", "Poland", "Europe"),
    ("Athens", "Greece", "Europe"),
    # North America (8)
    ("New York", "United States", "North America"), ("Chicago", "United States", "North America"),
    ("San Francisco", "United States", "North America"), ("Mexico City", "Mexico", "North America"),
    ("Toronto", "Canada", "North America"), ("Havana", "Cuba", "North America"),
    ("New Orleans", "United States", "North America"), ("Montreal", "Canada", "North America"),
    # South America (6)
    ("Rio de Janeiro", "Brazil", "South America"), ("Buenos Aires", "Argentina", "South America"),
    ("Lima", "Peru", "South America"), ("Santiago", "Chile", "South America"),
    ("Bogotá", "Colombia", "South America"), ("São Paulo", "Brazil", "South America"),
    # Asia (14)
    ("Tokyo", "Japan", "Asia"), ("Shanghai", "China", "Asia"),
    ("Beijing", "China", "Asia"), ("Mumbai", "India", "Asia"),
    ("Istanbul", "Turkey", "Europe/Asia"), ("Seoul", "South Korea", "Asia"),
    ("Bangkok", "Thailand", "Asia"), ("Manila", "Philippines", "Asia"),
    ("Singapore", "Singapore", "Asia"), ("Jakarta", "Indonesia", "Asia"),
    ("Hanoi", "Vietnam", "Asia"), ("Kolkata", "India", "Asia"),
    ("Osaka", "Japan", "Asia"), ("Tehran", "Iran", "Asia"),
    # Africa (8)
    ("Cairo", "Egypt", "Africa"), ("Lagos", "Nigeria", "Africa"),
    ("Nairobi", "Kenya", "Africa"), ("Johannesburg", "South Africa", "Africa"),
    ("Casablanca", "Morocco", "Africa"), ("Addis Ababa", "Ethiopia", "Africa"),
    ("Accra", "Ghana", "Africa"), ("Dakar", "Senegal", "Africa"),
    # Oceania (5)
    ("Sydney", "Australia", "Oceania"), ("Melbourne", "Australia", "Oceania"),
    ("Auckland", "New Zealand", "Oceania"), ("Wellington", "New Zealand", "Oceania"),
    ("Christchurch", "New Zealand", "Oceania"),
]
RANDOM_YEARS = [
    # Pre-industrial era (1750-1849)
    1750, 1768, 1789, 1795, 1804, 1812, 1820, 1830, 1848,
    # Victorian / Meiji / Industrial (1850-1899)
    1851, 1858, 1863, 1870, 1876, 1883, 1889, 1892, 1898,
    # Early 20th century (1900-1949)
    1900, 1905, 1910, 1914, 1918, 1922, 1925, 1929, 1933, 1937, 1942, 1945, 1948,
    # Mid 20th century (1950-1999)
    1950, 1953, 1957, 1960, 1964, 1968, 1972, 1975, 1979, 1982, 1986, 1991, 1995, 1999,
    # Early 21st century (2000-2005)
    2000, 2003, 2005,
]
RANDOM_THEMES = ["street", "station", "market", "harbor", "bridge", "tram", "railway", "city", "photograph"]

FALLBACK_ITEMS = [
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/The_Madeleine%2C_Paris%2C_France%2C_ca._1890-1900.jpg/1280px-The_Madeleine%2C_Paris%2C_France%2C_ca._1890-1900.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:The_Madeleine,_Paris,_France,_ca._1890-1900.jpg",
        "title": "The Madeleine, Paris, France, ca. 1890-1900",
        "description": "Historic photograph of La Madeleine in Paris.",
        "year_text": "ca. 1890-1900",
        "year_start": 1890,
        "year_end": 1900,
        "era_label": "1890s",
        "country": "France",
        "region": "Europe",
        "city": "Paris",
        "answer_text": "1890s / Paris, France",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/54/Lange-MigrantMother02.jpg/640px-Lange-MigrantMother02.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Lange-MigrantMother02.jpg",
        "title": "Migrant Mother",
        "description": "Dorothea Lange photograph during the Great Depression.",
        "year_text": "1936",
        "year_start": 1936,
        "year_end": 1936,
        "era_label": "1930s",
        "country": "United States",
        "region": "North America",
        "city": "Nipomo, California",
        "answer_text": "1930s / Nipomo, California, United States",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9c/Lunch_atop_a_Skyscraper_-_Charles_Clyde_Ebbets.jpg/640px-Lunch_atop_a_Skyscraper_-_Charles_Clyde_Ebbets.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Lunch_atop_a_Skyscraper_-_Charles_Clyde_Ebbets.jpg",
        "title": "Lunch atop a Skyscraper",
        "description": "Construction workers eating lunch on a steel beam in New York City.",
        "year_text": "1932",
        "year_start": 1932,
        "year_end": 1932,
        "era_label": "1930s",
        "country": "United States",
        "region": "North America",
        "city": "New York",
        "answer_text": "1930s / New York, United States",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/Hindenburg_disaster.jpg/640px-Hindenburg_disaster.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Hindenburg_disaster.jpg",
        "title": "Hindenburg disaster",
        "description": "The Hindenburg airship disaster at Lakehurst.",
        "year_text": "1937",
        "year_start": 1937,
        "year_end": 1937,
        "era_label": "1930s",
        "country": "United States",
        "region": "North America",
        "city": "Lakehurst, New Jersey",
        "answer_text": "1930s / Lakehurst, New Jersey, United States",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d8/Heinkel_He_111_over_Wapping%2C_East_London.jpg/640px-Heinkel_He_111_over_Wapping%2C_East_London.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Heinkel_He_111_over_Wapping,_East_London.jpg",
        "title": "Heinkel He 111 over Wapping, East London",
        "description": "German bomber over East London during the Blitz.",
        "year_text": "1940",
        "year_start": 1940,
        "year_end": 1940,
        "era_label": "1940s",
        "country": "United Kingdom",
        "region": "Europe",
        "city": "London",
        "answer_text": "1940s / London, United Kingdom",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/Berlinermauer.jpg/640px-Berlinermauer.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Berlinermauer.jpg",
        "title": "Berlin Wall",
        "description": "Berlin Wall scene during the Cold War era.",
        "year_text": "1986",
        "year_start": 1986,
        "year_end": 1986,
        "era_label": "1980s",
        "country": "Germany",
        "region": "Europe",
        "city": "Berlin",
        "answer_text": "1980s / Berlin, Germany",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/West_and_East_Germans_at_the_Brandenburg_Gate_in_1989.jpg/640px-West_and_East_Germans_at_the_Brandenburg_Gate_in_1989.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:West_and_East_Germans_at_the_Brandenburg_Gate_in_1989.jpg",
        "title": "West and East Germans at the Brandenburg Gate in 1989",
        "description": "People gathered at the Brandenburg Gate during the fall of the Berlin Wall.",
        "year_text": "1989",
        "year_start": 1989,
        "year_end": 1989,
        "era_label": "1980s",
        "country": "Germany",
        "region": "Europe",
        "city": "Berlin",
        "answer_text": "1980s / Berlin, Germany",
    },
    {
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b0/Raising_the_Flag_on_Iwo_Jima%2C_larger_-_edit1.jpg/640px-Raising_the_Flag_on_Iwo_Jima%2C_larger_-_edit1.jpg",
        "source_name": "Wikimedia Commons",
        "source_url": "https://commons.wikimedia.org/wiki/File:Raising_the_Flag_on_Iwo_Jima,_larger_-_edit1.jpg",
        "title": "Raising the Flag on Iwo Jima",
        "description": "U.S. Marines raising the flag on Iwo Jima during World War II.",
        "year_text": "1945",
        "year_start": 1945,
        "year_end": 1945,
        "era_label": "1940s",
        "country": "Japan",
        "region": "Asia",
        "city": "Iwo Jima",
        "answer_text": "1940s / Iwo Jima, Japan",
    },
]

GAME_SYSTEM_PROMPT = _play_text("guess.judge_system_prompt", """你是咪姆酱的历史图片小游戏判题器，同时要保持猫娘人格给用户反馈。

规则：
- 只能依据元数据，不要臆测图片本身细节。
- 用户答案可能包含年代、地区、城市、国家、地标、模糊描述，也可能是低难度选择题里的选项编号。
- 年代判断允许合理误差：若用户猜的年份/年代落在 year_start/year_end 附近，或 era_label 等价，可判正确。
- 地点判断：城市/具体地点一致为正确；国家/大区域一致但城市缺失或不准为部分正确。
- 如果用户只答对时代或只答对地区，判 partial=true。
- 低难度时，元数据里会包含 era_options/place_options；用户可能回答 “A 2”“A/纽约”“选A和2”等。
- feedback 是直接发给用户看的话，必须是中文猫娘口吻：轻微傲娇、带“喵/哼”等，但不要过度卖萌，不要泄露标准答案，除非 reveal=true。
- 不要输出 markdown，不要输出解释 JSON 以外的文字。

请严格输出 JSON：
{
  "correct": true,
  "partial": false,
  "confidence": 0.95,
  "reason": "简短判题理由，给程序参考",
  "feedback": "给用户看的猫娘反馈",
  "reveal": false
}
""")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _json_from_text(text: str) -> dict:
    if not text:
        return {}
    s = text.strip()
    s = re.sub(r"^```(?:json)?", "", s, flags=re.I).strip()
    s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}


def _parse_year_range(value: str) -> tuple[Optional[int], Optional[int], str]:
    raw = value or ""
    text = raw.lower()
    nums = [int(x) for x in re.findall(r"(?:18|19|20)\d{2}", text)]
    if len(nums) >= 2:
        a, b = min(nums[:2]), max(nums[:2])
        return a, b, f"{a}-{b}"
    if len(nums) == 1:
        y = nums[0]
        if any(x in text for x in ("ca", "circa", "c.", "approx", "about", "approximately")):
            return y - 5, y + 5, f"c. {y}"
        return y, y, str(y)

    century = re.search(r"(\d{1,2})(?:st|nd|rd|th)\s+century", text)
    if century:
        c = int(century.group(1))
        start, end = (c - 1) * 100 + 1, c * 100
        if "early" in text:
            end = start + 32
        elif "mid" in text or "middle" in text:
            start, end = start + 33, start + 65
        elif "late" in text:
            start = start + 66
        return start, end, raw.strip() or f"{c}th century"

    return None, None, raw.strip()


def _era_label(year_start: Optional[int], year_end: Optional[int], year_text: str) -> str:
    if year_start and year_end:
        mid = (year_start + year_end) // 2
        if year_end - year_start <= 15:
            return f"{mid // 10 * 10}s"
        century = (mid - 1) // 100 + 1
        if year_end - year_start > 50:
            return f"{century}世纪"
        return f"约{mid}年"
    return year_text or "未知年代"


ERA_DISTRACTORS = [
    "1750s", "1760s", "1770s", "1780s", "1790s",
    "1800s", "1810s", "1820s", "1830s", "1840s",
    "1850s", "1860s", "1870s", "1880s", "1890s",
    "1900s", "1910s", "1920s", "1930s", "1940s",
    "1950s", "1960s", "1970s", "1980s", "1990s", "2000s",
    "early 19th century", "mid 19th century", "late 19th century",
    "early 20th century", "mid 20th century", "late 20th century",
    "turn of the century",
]

PLACE_DISTRACTORS = [
    # Countries
    "United States", "United Kingdom", "France", "Germany", "Italy", "Japan", "China", "India",
    "Russia", "Spain", "Netherlands", "Austria", "Canada", "Mexico", "Brazil", "Egypt",
    "Poland", "Greece", "Czech Republic", "Turkey", "South Korea", "Thailand", "Philippines",
    "Indonesia", "Vietnam", "Iran", "Nigeria", "Kenya", "South Africa", "Morocco",
    "Ethiopia", "Ghana", "Senegal", "Peru", "Chile", "Colombia", "Argentina",
    "Australia", "New Zealand", "Cuba", "Singapore",
    # Cities
    "New York", "London", "Paris", "Berlin", "Tokyo", "Rome", "Amsterdam", "Vienna",
    "Shanghai", "Beijing", "Mumbai", "Moscow", "Cairo", "Istanbul", "Mexico City",
    "Rio de Janeiro", "Buenos Aires", "Seoul", "Bangkok", "Lagos", "Sydney",
    "Lima", "Havana", "Prague", "Warsaw", "Casablanca", "Jakarta", "Tehran", "Nairobi",
]


def _answer_place(item_or_row) -> str:
    parts = []
    for key in ("city", "country"):
        try:
            val = item_or_row.get(key) if hasattr(item_or_row, "get") else item_or_row[key]
        except Exception:
            val = None
        if val and str(val).strip() and str(val).strip() not in parts:
            parts.append(str(val).strip())
    if parts:
        return ", ".join(parts)
    try:
        region = item_or_row.get("region") if hasattr(item_or_row, "get") else item_or_row["region"]
    except Exception:
        region = None
    return str(region or "未知地点").strip()


def _unique_keep_order(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for v in values:
        v = re.sub(r"\s+", " ", str(v or "")).strip()
        if not v:
            continue
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _place_label(city: str, country: str) -> str:
    city = re.sub(r"\s+", " ", str(city or "")).strip()
    country = re.sub(r"\s+", " ", str(country or "")).strip()
    if city and country:
        return f"{city}, {country}"
    return city or country or "未知地点"


def _same_region_places(item: dict, limit: int = 8) -> list[str]:
    correct = _place_label(item.get("city"), item.get("country"))
    region = str(item.get("region") or "")
    places = []
    for city, country, reg in RANDOM_PLACES:
        label = _place_label(city, country)
        if label.lower() == correct.lower():
            continue
        if region and reg == region:
            places.append(label)
    if len(places) < limit:
        for city, country, _reg in RANDOM_PLACES:
            label = _place_label(city, country)
            if label.lower() != correct.lower() and label not in places:
                places.append(label)
    random.shuffle(places)
    return places[:limit]


def _nearby_eras(item: dict) -> list[str]:
    correct_era = item.get("era_label") or item.get("year_text") or "未知年代"
    eras = [correct_era]
    if item.get("year_start") and item.get("year_end"):
        mid = (int(item["year_start"]) + int(item["year_end"])) // 2
        decade = mid // 10 * 10
        for delta in (-30, -20, -10, 10, 20, 30):
            d = decade + delta
            if 1800 <= d <= 2020:
                eras.append(f"{d}s")
    return _unique_keep_order(eras)


def _pick_quiz_distractor_places(item: dict, n: int = 4) -> list[str]:
    correct = _place_label(item.get("city"), item.get("country"))
    region = str(item.get("region") or "")
    same_region = []
    other = []
    for city, country, reg in RANDOM_PLACES:
        label = _place_label(city, country)
        if label.lower() == correct.lower():
            continue
        if region and reg == region:
            same_region.append(label)
        else:
            other.append(label)
    random.shuffle(same_region)
    random.shuffle(other)
    return _unique_keep_order(same_region + other)[:n]


def _build_low_difficulty_options(item: dict) -> tuple[list[str], list[str]]:
    correct_era = item.get("era_label") or item.get("year_text") or "未知年代"
    correct_place = _answer_place(item)

    era_pool = [correct_era]
    if item.get("year_start") and item.get("year_end"):
        mid = (int(item["year_start"]) + int(item["year_end"])) // 2
        decade = mid // 10 * 10
        era_pool += [f"{decade - 20}s", f"{decade - 10}s", f"{decade + 10}s", f"{decade + 20}s"]
    era_pool += random.sample(ERA_DISTRACTORS, min(6, len(ERA_DISTRACTORS)))
    era_options = _unique_keep_order(era_pool)[:4]
    while len(era_options) < 4:
        era_options.append(random.choice(ERA_DISTRACTORS))
    era_options = _unique_keep_order(era_options)[:4]
    random.shuffle(era_options)

    # 低难度地区题只放一个标准答案，其余尽量用不同地点干扰项，避免 Paris/France/Europe 多个选项都算对。
    place_options = [correct_place]
    distractors = [x for x in PLACE_DISTRACTORS if x.lower() not in correct_place.lower() and correct_place.lower() not in x.lower()]
    random.shuffle(distractors)
    place_options += distractors[:6]
    place_options = _unique_keep_order(place_options)[:4]
    while len(place_options) < 4:
        candidate = random.choice(PLACE_DISTRACTORS)
        if candidate.lower() not in correct_place.lower():
            place_options.append(candidate)
        place_options = _unique_keep_order(place_options)
    place_options = place_options[:4]
    random.shuffle(place_options)
    return era_options, place_options


def _format_options(era_options: list[str], place_options: list[str]) -> str:
    letters = ["A", "B", "C", "D"]
    era_lines = [f"{letters[i]}. {v}" for i, v in enumerate(era_options)]
    place_lines = [f"{i + 1}. {v}" for i, v in enumerate(place_options)]
    return "年代选项：\n" + "\n".join(era_lines) + "\n\n地区选项：\n" + "\n".join(place_lines)


def _build_quiz_options(item: dict) -> tuple[list[str], int]:
    correct_era = item.get("era_label") or item.get("year_text") or "未知年代"
    correct_place = _place_label(item.get("city"), item.get("country"))
    correct = f"{correct_era} / {correct_place}"

    eras = [e for e in _nearby_eras(item) if e != correct_era]
    places = _pick_quiz_distractor_places(item, 6)
    if not eras:
        eras = ["1890s", "1900s", "1910s"]
    if not places:
        places = [_place_label(c, co) for c, co, _ in RANDOM_PLACES if _place_label(c, co) != correct_place]

    candidates = [
        f"{correct_era} / {places[0]}",              # 年代对，地点错
        f"{eras[0]} / {correct_place}",              # 地点对，年代近似错
        f"{eras[1 if len(eras) > 1 else 0]} / {places[1 if len(places) > 1 else 0]}",
        f"{eras[2 if len(eras) > 2 else 0]} / {places[2 if len(places) > 2 else 0]}",
    ]
    wrong = [x for x in _unique_keep_order(candidates) if x != correct]
    random.shuffle(wrong)
    options = [correct] + wrong[:3]
    while len(options) < 4:
        options.append(f"{random.choice(eras)} / {random.choice(places)}")
        options = _unique_keep_order(options)
    options = options[:4]
    random.shuffle(options)
    correct_id = options.index(correct)
    options = [(o[:96] + "…") if len(o) > 100 else o for o in options]
    return options, correct_id


def _difficulty_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    text = " ".join(context.args or []) if context and getattr(context, "args", None) else ""
    msg_text = (update.effective_message.text or "") if update.effective_message else ""
    raw = f"{text} {msg_text}".lower()
    if any(x in raw for x in ("easy", "low", "简单", "低难度", "选择题")):
        return "low"
    if any(x in raw for x in ("hard", "high", "困难", "高难度", "自由", "文字", "自由作答")):
        return "hard"
    # 默认给用户友好的低难度选择题；高手可显式加“困难/高难度”。
    return "low"


def _loc_image_url(item: dict) -> str:
    images = item.get("image_url") or []
    candidates: list[str] = []
    if isinstance(images, list):
        candidates.extend(str(x) for x in images if x)
    elif isinstance(images, str):
        candidates.append(images)
    resources = item.get("resources") or []
    for r in resources:
        imgs = r.get("image") if isinstance(r, dict) else None
        if isinstance(imgs, list):
            candidates.extend(str(x) for x in imgs if x)
        elif isinstance(imgs, str):
            candidates.append(imgs)
    # Telegram 更喜欢明确图片文件/iiif/full 链接；避开缩略图过小或网页链接。
    for url in reversed(candidates):
        if not url.startswith("http"):
            continue
        if any(x in url.lower() for x in (".jpg", ".jpeg", ".png", "/full/")):
            return url
    return candidates[-1] if candidates else ""


def _loc_location(item: dict) -> tuple[str, str, str]:
    location = item.get("location") or item.get("locations") or []
    parts: list[str] = []
    if isinstance(location, list):
        for x in location:
            if isinstance(x, dict):
                val = x.get("title") or x.get("name") or x.get("location")
            else:
                val = str(x)
            if val:
                parts.append(_strip_html(str(val)))
    elif isinstance(location, str):
        parts.append(_strip_html(location))
    joined = ", ".join(dict.fromkeys([p for p in parts if p]))
    return "", "", joined


def _loc_to_item(item: dict) -> Optional[dict]:
    image_url = _loc_image_url(item)
    if not image_url or not image_url.startswith("http"):
        return None

    date_text = str(item.get("date") or item.get("created_published_date") or item.get("timestamp") or "")
    year_start, year_end, year_text = _parse_year_range(date_text)
    if not year_start and not year_end:
        return None

    title = _strip_html(str(item.get("title") or ""))
    description_raw = item.get("description") or item.get("notes") or ""
    if isinstance(description_raw, list):
        description = _strip_html(" ".join(map(str, description_raw[:3])))
    else:
        description = _strip_html(str(description_raw))

    country, region, city = _loc_location(item)
    subjects = item.get("subject") or item.get("subjects") or []
    if not city and isinstance(subjects, list):
        # LoC 的 subject 经常包含地点词；不做范围限制，只做元数据提示。
        possible = [str(x) for x in subjects if isinstance(x, str) and len(str(x)) <= 60]
        if possible:
            city = _strip_html(", ".join(possible[:3]))

    source_url = str(item.get("url") or item.get("id") or "")
    era = _era_label(year_start, year_end, year_text)
    place_parts = [x for x in (city, country, region) if x]
    place_text = ", ".join(place_parts) if place_parts else "地点见馆藏元数据"

    return {
        "image_url": image_url,
        "source_name": "Library of Congress",
        "source_url": source_url,
        "title": title,
        "description": description,
        "year_text": year_text or date_text,
        "year_start": year_start,
        "year_end": year_end,
        "era_label": era,
        "country": country,
        "region": region,
        "city": city,
        "answer_text": f"{era} / {place_text}",
    }



def _commons_meta_value(ext: dict, key: str) -> str:
    val = (ext or {}).get(key) or {}
    if isinstance(val, dict):
        return _strip_html(str(val.get("value") or ""))
    return _strip_html(str(val or ""))


def _commons_to_item(page: dict) -> Optional[dict]:
    ii = (page.get("imageinfo") or [{}])[0]
    ext = ii.get("extmetadata") or {}
    image_url = ii.get("thumburl") or ii.get("url") or ""
    if not image_url.startswith("http"):
        return None
    title = str(page.get("title") or "").replace("File:", "")
    desc = _commons_meta_value(ext, "ImageDescription") or _commons_meta_value(ext, "ObjectName")
    date_text = _commons_meta_value(ext, "DateTimeOriginal") or _commons_meta_value(ext, "DateTime") or title
    year_start, year_end, year_text = _parse_year_range(date_text)
    if not year_start and not year_end:
        year_start, year_end, year_text = _parse_year_range(title + " " + desc)
    if not year_start and not year_end:
        return None

    # Commons 元数据地点不总是结构化；从标题/描述里保留线索给 LLM 判题。
    combined = f"{title}. {desc}"
    city = ""
    country = ""
    region = ""
    for name in PLACE_DISTRACTORS:
        if re.search(rf"\b{re.escape(name)}\b", combined, flags=re.I):
            if name in {"United States", "United Kingdom", "France", "Germany", "Italy", "Japan", "China", "India", "Russia", "Spain", "Netherlands", "Austria", "Canada", "Mexico", "Brazil", "Egypt"}:
                country = country or name
            else:
                city = city or name
    # 如果没有结构化地点，也把题名作为 city/地点线索，不限制范围。
    if not city and not country:
        city = title[:80]

    source_url = ii.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{page.get('title','').replace(' ', '_')}"
    era = _era_label(year_start, year_end, year_text)
    place_text = _answer_place({"city": city, "country": country, "region": region})
    return {
        "image_url": image_url,
        "source_name": "Wikimedia Commons",
        "source_url": source_url,
        "title": title,
        "description": desc,
        "year_text": year_text,
        "year_start": year_start,
        "year_end": year_end,
        "era_label": era,
        "country": country,
        "region": region,
        "city": city,
        "answer_text": f"{era} / {place_text}",
    }



def _random_commons_terms(limit: int = 8) -> list[tuple[str, str, str, str, int]]:
    combos = []
    places = RANDOM_PLACES[:]
    random.shuffle(places)
    for city, country, region in places[:limit]:
        year = random.choice(RANDOM_YEARS)
        theme = random.choice(RANDOM_THEMES)
        # 用 file search 找真实图片；年份/地点作为元数据候选，LLM 判题时仍以这些结构化字段为准。
        query = f"{city} {year} {theme} photograph"
        combos.append((query, city, country, region, year))
    return combos



def _commons_thumb_large(url: str, width: int = 900) -> str:
    if not url:
        return ""
    return re.sub(r"/\d+px-([^/]+)$", rf"/{width}px-\1", url)


def _rest_page_to_item(page: dict, city: str, country: str, region: str, year: int) -> Optional[dict]:
    title = str(page.get("title") or page.get("key") or "")
    if not title.lower().startswith("file:"):
        return None
    if any(title.lower().endswith(ext) for ext in (".pdf", ".djvu", ".webm", ".ogg", ".svg")):
        return None
    thumb = (page.get("thumbnail") or {}).get("url") or ""
    image_url = _commons_thumb_large(thumb, 900)
    if not image_url:
        return None
    desc = _strip_html(str(page.get("description") or page.get("excerpt") or ""))
    year_start, year_end, year_text = _parse_year_range(title + " " + desc)
    if not year_start:
        year_start = year_end = year
        year_text = str(year)
    era = _era_label(year_start, year_end, year_text)
    clean_title = title.replace("File:", "")
    source_url = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
    return {
        "image_url": image_url,
        "source_name": "Wikimedia Commons",
        "source_url": source_url,
        "title": clean_title,
        "description": desc,
        "year_text": year_text,
        "year_start": year_start,
        "year_end": year_end,
        "era_label": era,
        "country": country,
        "region": region,
        "city": city,
        "answer_text": f"{era} / {city}, {country}",
    }


async def _fetch_commons_candidates() -> list[dict]:
    timeout = httpx.Timeout(8.0, connect=4.0)
    headers = {"User-Agent": "MimuBotHistoryGuess/1.0 (Telegram bot; educational game)", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for term, city, country, region, year in _random_commons_terms(limit=12):
            try:
                resp = await client.get(
                    "https://api.wikimedia.org/core/v1/commons/search/page",
                    params={"q": term, "limit": 10},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.info(f"Commons REST 随机查询跳过 term={term!r}: {e}")
                continue
            pages = data.get("pages", [])
            random.shuffle(pages)
            items = []
            for page in pages:
                parsed = _rest_page_to_item(page, city, country, region, year)
                if parsed:
                    items.append(parsed)
            if items:
                return items
    return []

async def _image_downloadable(url: str) -> bool:
    try:
        timeout = httpx.Timeout(8.0, connect=4.0)
        headers = {"User-Agent": "MimuBotHistoryGuess/1.0 (Telegram bot; educational game)", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            ctype = resp.headers.get("content-type", "").lower()
            return resp.status_code == 200 and len(resp.content) > 1024 and ("image" in ctype or url.lower().endswith((".jpg", ".jpeg", ".png")))
    except Exception:
        return False


async def _first_downloadable(items: list[dict]) -> Optional[dict]:
    for item in items[:12]:
        if await _image_downloadable(item.get("image_url", "")):
            return item
    return None


async def _fetch_loc_candidates() -> list[dict]:
    term = random.choice(LOC_SEARCH_TERMS)
    params = {
        "fo": "json",
        "c": "20",
        "q": term,
        "fa": "original-format:photo, print, drawing",
    }
    timeout = httpx.Timeout(6.0, connect=3.0)
    headers = {"User-Agent": "MimuBotHistoryGuess/1.0 (Telegram bot; educational game)", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = await client.get("https://www.loc.gov/photos/", params=params)
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    random.shuffle(results)
    items = []
    for raw in results:
        parsed = _loc_to_item(raw)
        if parsed:
            items.append(parsed)
    return items



async def _fetch_internet_archive_candidates() -> list[dict]:
    city, country, region = random.choice(RANDOM_PLACES)
    year = random.choice(RANDOM_YEARS)
    # IA 的 year 字段较粗，围绕年代窗口搜，提升命中率。
    y1, y2 = max(1800, year - 5), min(2000, year + 5)
    q = f'(title:"{city}" OR description:"{city}") AND year:[{y1} TO {y2}] AND mediatype:image'
    params = {
        "q": q,
        "fl[]": ["identifier", "title", "year"],
        "rows": "12",
        "page": "1",
        "output": "json",
    }
    timeout = httpx.Timeout(12.0, connect=5.0)
    headers = {"User-Agent": "MimuBotHistoryGuess/1.0 (Telegram bot; educational game)"}
    items: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        resp = await client.get("https://archive.org/advancedsearch.php", params=params)
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
        random.shuffle(docs)
        for doc in docs[:6]:
            ident = doc.get("identifier")
            if not ident:
                continue
            try:
                meta_resp = await client.get(f"https://archive.org/metadata/{ident}")
                meta_resp.raise_for_status()
                meta = meta_resp.json()
            except Exception:
                continue
            files = meta.get("files", [])
            image_files = []
            for f in files:
                name = f.get("name", "")
                low = name.lower()
                if not low.endswith((".jpg", ".jpeg", ".png")):
                    continue
                if "_thumb" in low or "__ia_thumb" in low:
                    continue
                size = int(f.get("size") or 0)
                image_files.append((size, name))
            if not image_files:
                for f in files:
                    name = f.get("name", "")
                    if name.lower().endswith((".jpg", ".jpeg", ".png")):
                        image_files.append((int(f.get("size") or 0), name))
            if not image_files:
                continue
            image_files.sort(reverse=True)
            # 避免超大图，取中等图；如果只有原图也能由下载超时兜底处理。
            name = image_files[0][1]
            image_url = f"https://archive.org/download/{ident}/{name}"
            doc_year_text = str(doc.get("year") or year)
            ys, ye, yt = _parse_year_range(doc_year_text)
            ys = ys or y1
            ye = ye or y2
            era = _era_label(ys, ye, yt or str(year))
            title = _strip_html(str(doc.get("title") or meta.get("metadata", {}).get("title") or ident))
            items.append({
                "image_url": image_url,
                "source_name": "Internet Archive",
                "source_url": f"https://archive.org/details/{ident}",
                "title": title,
                "description": title,
                "year_text": yt or doc_year_text,
                "year_start": ys,
                "year_end": ye,
                "era_label": era,
                "country": country,
                "region": region,
                "city": city,
                "answer_text": f"{era} / {city}, {country}",
            })
    return items


async def _generate_guess_seed(chat_id: int | None = None) -> dict:
    """Use LLM to randomly design one history/location guessing prompt."""
    prompt = """
Generate one quiz seed for a history/location image guessing game.
Return strict JSON only with keys:
- year_start: integer between 1750 and 2005
- year_end: integer, same decade or close range (max 25 years apart)
- era_label: e.g. "1790s", "1920s", "mid 19th century"
- city: real city/place — must be a name that a typical history-interested person can recognize
- country: real country
- region: continent or sub-region (Africa, Asia, Europe, Middle East, North America, South America, Oceania, Caribbean, Central Asia, Southeast Asia, etc.)
- title: short title (8-15 words)
- description: brief historical clue (1-2 sentences) providing context about what life was like there at that time. Do NOT directly reveal the year or city name.
- visual_prompt: English image generation prompt. It must depict a plausible historical-looking scene from that era and place, but must NOT include readable text, flags, maps, captions, labels, or famous landmarks that make the answer too obvious. Photographic/documentary style, period clothing/vehicles/architecture.

CRITICAL DIVERSITY RULES:
- Rotate aggressively across all inhabited continents. Roughly equal chance for Africa, Asia (include South/Southeast/East), Europe (include Eastern), Middle East, North America (include Latin America), South America, Oceania.
- Prefer lesser-chosen countries and cities. Avoid overused defaults (Paris, London, New York, Tokyo). If you must pick a famous city, pair it with an unusual decade.
- Mix well-known and medium-tier cities — not just capitals.
- Vary year ranges: include pre-industrial (1750-1820), 19th century (1820-1900), early 20th century (1900-1945), mid 20th century (1945-1980), and late 20th/early 21st (1980-2005).
- Avoid repeating the same continent or decade twice in a row.
""".strip()
    try:
        llm = get_llm_client()
        raw = await llm.generate_text(prompt, max_tokens=500, temperature=0.9)
        data = _json_from_text(raw)
        if data:
            ys = int(data.get("year_start") or 1900)
            ye = int(data.get("year_end") or ys)
            city = str(data.get("city") or "")[:80]
            country = str(data.get("country") or "")[:80]
            visual_prompt = str(data.get("visual_prompt") or "").strip()
            if city and country and visual_prompt:
                data["year_start"] = ys
                data["year_end"] = ye
                data["year_text"] = f"{ys}-{ye}" if ys != ye else str(ys)
                data["era_label"] = str(data.get("era_label") or _era_label(ys, ye, str(ys)))
                data["answer_text"] = f"{data['era_label']} / {city}, {country}"
                return data
    except Exception as e:
        logger.warning(f"LLM 生成猜图 seed 失败，使用随机兜底: {e}")

    city, country, region = random.choice(RANDOM_PLACES)
    year = random.choice(RANDOM_YEARS)
    era = f"{year // 10 * 10}s"
    return {
        "year_start": year,
        "year_end": year,
        "year_text": str(year),
        "era_label": era,
        "city": city,
        "country": country,
        "region": region,
        "title": f"Generated historical scene: {city} {era}",
        "description": f"AI-generated historical-looking scene for {city}, {country}, {era}.",
        "visual_prompt": (
            f"documentary historical photograph style, {city}, {country}, around {year}, "
            "street life scene with period-appropriate clothing, vehicles and architecture, "
            "black and white or early color film, natural composition, no readable text, no captions, no maps, no flags, no watermark"
        ),
        "answer_text": f"{era} / {city}, {country}",
    }


async def _prepare_guess_item(chat_id: int | None = None) -> dict | None:
    seed = await _generate_guess_seed(chat_id)
    try:
        from integrations.image_gen_tool import (
    _call_image_api,
    _resolve_image,
)
        image_url_or_b64 = await _call_image_api(
            prompt=seed["visual_prompt"],
            mode="text_to_image",
            reference_b64=None,
            chat_id=chat_id,
        )
        if not image_url_or_b64:
            return None
        image_bytes = await _resolve_image(image_url_or_b64)
        if not image_bytes:
            return None
        return {
            "image_bytes": image_bytes,
            "image_url": f"generated://history-guess/{int(datetime.now(timezone.utc).timestamp())}",
            "source_name": "AI Generated",
            "source_url": "",
            "title": seed.get("title", "AI-generated historical scene"),
            "description": seed.get("description", ""),
            "year_text": seed.get("year_text"),
            "year_start": seed.get("year_start"),
            "year_end": seed.get("year_end"),
            "era_label": seed.get("era_label"),
            "country": seed.get("country"),
            "region": seed.get("region"),
            "city": seed.get("city"),
            "answer_text": seed.get("answer_text"),
            "visual_prompt": seed.get("visual_prompt"),
        }
    except Exception as e:
        logger.warning(f"准备猜图生图题失败: {e}")
        return None


def _kickoff_prepare_next(chat_id: int | None = None):
    global _prepare_guess_task
    if _prepare_guess_task and not _prepare_guess_task.done():
        return
    async def runner():
        global _prepared_guess_item
        item = await _prepare_guess_item(chat_id)
        if item:
            _prepared_guess_item = item
            logger.info("🎮 已后台准备下一道生图猜图题")
    _prepare_guess_task = asyncio.create_task(runner(), name="prepare-history-guess")


async def stop_prepare_guess_task() -> None:
    global _prepare_guess_task
    task = _prepare_guess_task
    _prepare_guess_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _generate_guess_item(chat_id: int | None = None) -> dict:
    """Prefer prepared generated item. If not ready, try Internet Archive fallback, and keep background generation running."""
    global _prepared_guess_item
    if _prepared_guess_item:
        item = _prepared_guess_item
        _prepared_guess_item = None
        _kickoff_prepare_next(chat_id)
        item["quality_tier"] = "generated"
        return item

    # 没预热好时：立刻继续后台生图，同时先给一题 IA 临时图，避免用户空等。
    _kickoff_prepare_next(chat_id)
    try:
        ia_items = await _fetch_internet_archive_candidates()
        item = await _first_downloadable(ia_items)
        if item:
            item["quality_tier"] = "fallback_ia"
            return item
    except Exception as e:
        logger.warning(f"冷却期间 IA 临时题获取失败: {e}")
    return {}

def _active_game(chat_id: int):
    with orm_session(DB_PATH) as session:
        row = session.get(HistoryGuessGameRow, str(chat_id))
        return row if row and int(row.active) == 1 else None


def _clear_active_game(chat_id: int):
    with orm_session(DB_PATH) as session:
        row = session.get(HistoryGuessGameRow, str(chat_id))
        if row is not None:
            row.active = 0


def get_active_game_context(chat_id: int) -> str:
    """返回当前活跃游戏的答案上下文，供主对话流程注入系统提示。"""
    game = _active_game(chat_id)
    if not game:
        return ""
    
    def row_get(key: str, default=None):
        return getattr(game, key, default)

    difficulty = row_get("difficulty", "low")
    answer_text = row_get("answer_text", "")
    era_label = row_get("era_label") or row_get("year_text") or "未知年代"
    city = row_get("city") or ""
    country = row_get("country") or ""
    place = f"{city}, {country}" if city and country else (city or country or "未知地点")
    
    template = _play_text("guess.active_game_context", """
## 🎮 当前群正在进行历史图片猜谜游戏

**游戏状态：** 活跃中
**难度：** {difficulty_label}
**标准答案：** {answer_text}
  - 年代：{era_label}
  - 地点：{place}

**你的任务：**
- 当用户回复猜测答案时，根据标准答案自然判断对错。
- 年代判断：允许合理误差（±10年内、同一年代段如1940s等）。
- 地点判断：城市/国家对上即可，不要求完全精确拼写。
- 只答对年代或只答对地点 → 部分正确，鼓励继续猜。
- 全部答对 → 明确祝贺，并在回复末尾加上隐藏标记 `[GAME_SOLVED]`。
- 答错 → 给提示，不要直接说答案。
- 用户要求公布答案 → 直接告知答案，并在回复末尾加上 `[GAME_END]`。
""")
    return template.format(
        difficulty_label=_play_text("guess.difficulty_hard", "困难模式（自由作答）") if difficulty == "hard" else _play_text("guess.difficulty_low", "简单模式（选择题）"),
        answer_text=answer_text,
        era_label=era_label,
        place=place,
    ).strip()


async def _send_game_photo(message, image_source, caption: str):
    """Upload prepared image bytes, or download URL then upload."""
    if isinstance(image_source, (bytes, bytearray)):
        content = bytes(image_source)
    else:
        timeout = httpx.Timeout(10.0, connect=4.0)
        headers = {"User-Agent": "MimuBotHistoryGuess/1.0 (Telegram bot; educational game)", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            resp = await client.get(str(image_source))
            resp.raise_for_status()
            content = resp.content
    if not content or len(content) < 1024:
        raise ValueError("image is too small")
    bio = BytesIO(content)
    bio.name = "history_guess.jpg"
    return await message.reply_photo(photo=bio, caption=caption)


async def start_history_guess(update: Update, context: ContextTypes.DEFAULT_TYPE, difficulty_override: str | None = None):
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)

    old_game = _active_game(chat_id)
    difficulty = difficulty_override or _difficulty_from_update(update, context)
    try:
        item = await asyncio.wait_for(_generate_guess_item(chat_id), timeout=3.0)
    except Exception as e:
        logger.warning(f"历史猜图取题超时/失败: {e}")
        item = {}
    if not item:
        _clear_active_game(chat_id)
        await update.effective_message.reply_text(_play_text("guess.no_item", "咪姆酱还在后面偷偷画下一题喵……这会儿连临时图都翻不出来，稍等再来嘛。"))
        raise ApplicationHandlerStop

    era_options, place_options = ([], [])
    quiz_options, quiz_correct_id = ([], 0)
    if difficulty == "low":
        era_options, place_options = _build_low_difficulty_options(item)
        quiz_options, quiz_correct_id = _build_quiz_options(item)

    prefix = _play_text("guess.previous_game_prefix", "上一局已结束，") if old_game else ""
    quality_tier = item.get("quality_tier", "generated")
    fallback_note = _play_text("guess.fallback_note", "\n（这题先用临时图顶一下喵，等会再来会有更好的图。）") if quality_tier == "fallback_ia" else ""
    if difficulty == "low":
        caption = _play_text("guess.caption_low", "🎲 {prefix}猜图喵 · 简单\n看完图就点下面的 Quiz 喵～{fallback_note}").format(prefix=prefix, fallback_note=fallback_note)
    else:
        caption = _play_text("guess.caption_hard", "🎲 {prefix}猜图喵 · 困难\n自己猜猜年代和地区喵，哼。{fallback_note}").format(prefix=prefix, fallback_note=fallback_note)
    caption += _play_text("guess.reveal_hint", "\n要投降的话，就回“公布答案”喵。")

    # 新局开始即终止旧局；但只有图片发出成功后才写入 active game，避免卡在“找图中”。
    _clear_active_game(chat_id)
    try:
        sent = await _send_game_photo(update.effective_message, item.get("image_bytes") or item["image_url"], caption)
    except Exception as e:
        logger.warning(f"历史猜图发图失败: {e}")
        await update.effective_message.reply_text(_play_text("guess.send_photo_failed", "呜，这张图怎么都扒不下来喵……你再戳一次试试嘛。"))
        raise ApplicationHandlerStop

    with orm_session(DB_PATH) as session:
        session.merge(HistoryGuessGameRow(
            chat_id=str(chat_id),
            active=1,
            created_at=utc_now_iso(),
            started_by=user_id,
            message_id=sent.message_id,
            image_url=item["image_url"],
            source_name=item.get("source_name"),
            source_url=item.get("source_url"),
            title=item.get("title"),
            description=item.get("description"),
            year_text=item.get("year_text"),
            year_start=item.get("year_start"),
            year_end=item.get("year_end"),
            era_label=item.get("era_label"),
            country=item.get("country"),
            region=item.get("region"),
            city=item.get("city"),
            answer_text=item.get("answer_text"),
            solved=0,
            reveal_requested=0,
            revealed=0,
            difficulty=difficulty,
            era_options_json=json.dumps(era_options, ensure_ascii=False),
            place_options_json=json.dumps(place_options, ensure_ascii=False),
        ))

    if difficulty == "low":
        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=_play_text("guess.poll_question", "这张图是什么时代/地区喵？"),
                options=quiz_options,
                type="quiz",
                correct_option_id=quiz_correct_id,
                is_anonymous=False,
                explanation=_play_text("guess.poll_explanation", "答案：{answer_text} 喵").format(answer_text=item.get("answer_text", "")),
                reply_to_message_id=sent.message_id,
            )
        except Exception as e:
            logger.warning(f"发送 quiz poll 失败: {e}")
            await update.effective_message.reply_text(_play_text("guess.poll_failed", "Quiz 这会儿闹别扭了喵，不过图已经发出来啦。想直接看答案就回“公布答案”。"))


async def handle_history_guess_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Only intercept low-mode quiz/reveal. Hard mode is handled by the normal chat workflow with injected game context."""
    msg = update.effective_message
    if not msg or not msg.chat or not msg.reply_to_message:
        return
    chat_id = msg.chat.id
    game = _active_game(chat_id)
    if not game:
        return
    if not getattr(game, "message_id", None) or msg.reply_to_message.message_id != getattr(game, "message_id", None):
        return

    text = (msg.text or msg.caption or "").strip()
    if any(k.lower() in _normalize_text(text) for k in REVEAL_KEYWORDS):
        with orm_session(DB_PATH) as session:
            row = session.get(HistoryGuessGameRow, str(chat_id))
            if row is not None:
                row.reveal_requested = 1
        await _reveal_answer(update, game)
        raise ApplicationHandlerStop

    if getattr(game, "difficulty", None) == "low":
        await msg.reply_text(_play_text("guess.low_mode_reply", "简单模式直接点 Quiz 就好喵～想偷看答案就回“公布答案”。"))
        raise ApplicationHandlerStop

    # hard mode: do not stop; let chat_handler answer using injected context
    return


async def _reveal_answer(update: Update, game):
    if not update.effective_message or not update.effective_chat:
        return
    parts = [
        _play_text("guess.reveal_answer", "答案揭晓啦喵：{answer_text}").format(answer_text=getattr(game, "answer_text", "")),
        _play_text("guess.reveal_era", "年代：{era}").format(era=getattr(game, "era_label", None) or getattr(game, "year_text", None) or _play_text("guess.unknown", "未知")),
    ]
    place = ", ".join([x for x in (getattr(game, "city", None), getattr(game, "country", None), getattr(game, "region", None)) if x])
    if place:
        parts.append(_play_text("guess.reveal_place", "地区：{place}").format(place=place))
    if getattr(game, "title", None):
        parts.append(_play_text("guess.reveal_title", "题名：{title}").format(title=getattr(game, "title", None)))
    if getattr(game, "source_name", None):
        parts.append(_play_text("guess.reveal_source", "来源：{source}").format(source=getattr(game, "source_name", None)))
    if getattr(game, "source_url", None):
        parts.append(str(getattr(game, "source_url", None)))
    await update.effective_message.reply_text("\n".join(parts))
    with orm_session(DB_PATH) as session:
        row = session.get(HistoryGuessGameRow, str(update.effective_chat.id))
        if row is not None:
            row.revealed = 1
    _clear_active_game(update.effective_chat.id)


async def choose_history_guess_difficulty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_play_text("guess.button_low", "🟢 简单喵"), callback_data="history_guess:low"),
            InlineKeyboardButton(_play_text("guess.button_hard", "🔥 困难喵"), callback_data="history_guess:hard"),
        ]
    ])
    await update.effective_message.reply_text(_play_text("guess.choose_difficulty", "主人，选难度喵～"), reply_markup=keyboard)
    raise ApplicationHandlerStop


async def history_guess_difficulty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data not in ("history_guess:low", "history_guess:hard"):
        return
    difficulty = data.split(":", 1)[1]
    label = _play_text("guess.difficulty_low", "简单选择题") if difficulty == "low" else _play_text("guess.difficulty_hard", "困难自由作答")
    try:
        await query.edit_message_text(_play_text("guess.loading", "{label}，咪姆酱翻图中喵…").format(label=label))
    except Exception:
        pass
    await start_history_guess(update, context, difficulty_override=difficulty)
    raise ApplicationHandlerStop

async def start_history_guess_by_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    text = (msg.text or "").strip()
    if text in TEXT_STARTS:
        await choose_history_guess_difficulty(update, context)
        raise ApplicationHandlerStop
    if text.startswith("猜历史 ") or text.startswith("猜时代 "):
        difficulty = _difficulty_from_update(update, context)
        await start_history_guess(update, context, difficulty_override=difficulty)
        raise ApplicationHandlerStop

