"""
AI Persona Generator — uses Google Gemini (Vertex AI) to create
realistic Russian-user personas for browser profiles.

Each persona is a coherent "digital person" with demographics, interests,
typical browsing sites, search queries, and activity hours.
This data is used to drive warmup behaviour and make profiles look natural.
"""
import json
import os
import random
import logging
import hashlib
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client singleton
# ---------------------------------------------------------------------------
_gemini_client = None


def _get_gemini_client():
    """Lazy-init Gemini client (Vertex AI or API key)."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        from google import genai as genai_sdk

        # Try Vertex AI first (service account)
        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if credentials_path and os.path.exists(credentials_path):
            _gemini_client = genai_sdk.Client(
                vertexai=True,
                project=os.environ.get("VERTEX_PROJECT", "elite-magpie-484909-t6"),
                location=os.environ.get("VERTEX_LOCATION", "us-central1"),
            )
            logger.info("Gemini client initialised via Vertex AI (service account)")
            return _gemini_client

        # Fallback: API key
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            _gemini_client = genai_sdk.Client(api_key=api_key)
            logger.info("Gemini client initialised via API key")
            return _gemini_client

        logger.warning("No Gemini credentials found — persona generation will use fallback")
        return None

    except ImportError:
        logger.warning("google-genai SDK not installed — persona generation will use fallback")
        return None
    except Exception as exc:
        logger.error(f"Failed to init Gemini client: {exc}")
        return None


# ---------------------------------------------------------------------------
# Gemini settings (read from DB or env)
# ---------------------------------------------------------------------------
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# ---------------------------------------------------------------------------
# Prompt for persona generation
# ---------------------------------------------------------------------------
PERSONA_PROMPT_TEMPLATE = """Сгенерируй {count} уникальных реалистичных персон русскоязычных пользователей интернета.
Каждая персона — это \"цифровой человек\" с когерентным набором характеристик.

Для каждой персоны верни JSON-объект со следующими полями:
- "name": имя и фамилия (русские)
- "gender": "male" или "female"
- "age": число от 18 до 65
- "city": город в России (не только Москва/Спб — разнообразие!)
- "timezone": таймзона IANA для этого города (Europe/Moscow, Asia/Yekaterinburg и т.д.)
- "profession": профессия (на русском)
- "interests": массив из 4-8 интересов/хобби (на русском)
- "typical_sites": массив из 8-15 URL сайтов, которые такой человек регулярно посещает
  (включай yandex.ru, dzen.ru, vk.com и другие реальные российские сайты, подходящие интересам)
- "search_queries": массив из 5-10 типичных поисковых запросов в Яндексе,
  которые такой человек мог бы искать (на русском)
- "activity_hours": массив часов (0-23) когда обычно активен в интернете
- "device_preference": "desktop", "mobile" или "mixed"
- "browser_behavior": объект с полями:
  - "avg_session_minutes": среднее время сессии (число)
  - "pages_per_session": среднее число страниц за сессию (число)
  - "scroll_speed": "slow", "medium" или "fast"
  - "reads_articles": true/false
  - "watches_video": true/false

Верни ТОЛЬКО валидный JSON-массив без комментариев и markdown-разметки.
Пример формата ответа (используй этот формат строго):
[
  {{
    "name": "Иванов Иван",
    "gender": "male",
    "age": 34,
    "city": "Новосибирск",
    "timezone": "Asia/Novosibirsk",
    "profession": "Инженер-программист",
    "interests": ["программирование", "горные лыжи", "электроника", "научная фантастика"],
    "typical_sites": ["https://ya.ru", "https://habr.com", "https://vk.com", "https://ozon.ru"],
    "search_queries": ["python asyncio tutorial", "горнолыжные курорты сибирь", "купить arduino"],
    "activity_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22],
    "device_preference": "desktop",
    "browser_behavior": {{
      "avg_session_minutes": 25,
      "pages_per_session": 8,
      "scroll_speed": "medium",
      "reads_articles": true,
      "watches_video": true
    }}
  }}
]
"""

# ---------------------------------------------------------------------------
# Prompt for warmup sites generation (50 sites per persona)
# ---------------------------------------------------------------------------
WARMUP_SITES_PROMPT_TEMPLATE = """Ты генератор списков сайтов для нагула браузерного профиля.
Профиль принадлежит следующему пользователю:

Имя: {name}
Пол: {gender}
Возраст: {age}
Город: {city}
Профессия: {profession}
Интересы: {interests}

Сгенерируй список из РОВНО 50 уникальных URL-адресов реальных российских и международных сайтов,
которые этот человек мог бы регулярно посещать в повседневной жизни.

ПРАВИЛА:
1. Сайты должны быть РЕАЛЬНЫМИ и РАБОТАЮЩИМИ (проверяемыми)
2. Обязательно включи:
   - 5-7 сайтов экосистемы Яндекса (ya.ru, dzen.ru, market.yandex.ru, pogoda.yandex.ru, news.yandex.ru, music.yandex.ru, kinopoisk.ru, translate.yandex.ru)
   - 3-5 сайтов Mail.ru/VK группы (vk.com, mail.ru, ok.ru, dzen.ru)
   - 5-8 новостных/информационных сайтов (rbc.ru, lenta.ru, ria.ru, tass.ru, gazeta.ru, kommersant.ru, kp.ru)
   - 5-8 сайтов маркетплейсов/магазинов (ozon.ru, wildberries.ru, avito.ru, dns-shop.ru, mvideo.ru, lamoda.ru, citilink.ru)
   - 10-15 тематических сайтов по интересам и профессии человека
   - 3-5 сервисных сайтов (banki.ru, hh.ru, 2gis.ru, gosuslugi.ru)
   - 2-3 международных сайта (google.com, youtube.com, wikipedia.org)
   - Остальные — разнообразные сайты, подходящие этому конкретному человеку
3. Все URL должны начинаться с https://
4. НЕ включай социальные сети с обязательной авторизацией (telegram, instagram)
5. Порядок сайтов должен быть случайным (не группируй по категориям)

Также сгенерируй 20 дополнительных поисковых запросов для Яндекса,
которые этот человек мог бы искать (с учётом города, профессии и интересов).

Верни ТОЛЬКО валидный JSON-объект без комментариев и markdown:
{{
  "warmup_sites": ["https://...", "https://...", ...],
  "extra_search_queries": ["запрос 1", "запрос 2", ...]
}}
"""


# ---------------------------------------------------------------------------
# Fallback personas (when Gemini is unavailable)
# ---------------------------------------------------------------------------
FALLBACK_PERSONAS: List[Dict] = [
    {
        "name": "Смирнова Анна",
        "gender": "female",
        "age": 28,
        "city": "Москва",
        "timezone": "Europe/Moscow",
        "profession": "Маркетолог",
        "interests": ["маркетинг", "путешествия", "кулинария", "фитнес", "фотография"],
        "typical_sites": [
            "https://ya.ru", "https://vk.com", "https://dzen.ru",
            "https://ozon.ru", "https://wildberries.ru", "https://instagram.com",
            "https://market.yandex.ru", "https://avito.ru", "https://mail.ru",
            "https://lenta.ru", "https://sports.ru"
        ],
        "search_queries": [
            "рецепт тирамису", "лучшие отели турция 2024",
            "курс smm онлайн", "фитнес браслет отзывы",
            "скидки ozon сегодня"
        ],
        "activity_hours": [8, 9, 10, 12, 13, 18, 19, 20, 21, 22],
        "device_preference": "mixed",
        "browser_behavior": {
            "avg_session_minutes": 20,
            "pages_per_session": 6,
            "scroll_speed": "medium",
            "reads_articles": True,
            "watches_video": True
        }
    },
    {
        "name": "Козлов Дмитрий",
        "gender": "male",
        "age": 42,
        "city": "Екатеринбург",
        "timezone": "Asia/Yekaterinburg",
        "profession": "Инженер",
        "interests": ["автомобили", "рыбалка", "ремонт", "спорт", "история"],
        "typical_sites": [
            "https://ya.ru", "https://dzen.ru", "https://drive2.ru",
            "https://auto.ru", "https://avito.ru", "https://rbc.ru",
            "https://sports.ru", "https://mail.ru", "https://dns-shop.ru",
            "https://2gis.ru", "https://vk.com"
        ],
        "search_queries": [
            "замена масла kia sportage", "рыбалка на урале весна",
            "купить дрель bosch", "новости екатеринбург",
            "расписание матчей рпл"
        ],
        "activity_hours": [7, 8, 12, 13, 18, 19, 20, 21, 22, 23],
        "device_preference": "desktop",
        "browser_behavior": {
            "avg_session_minutes": 15,
            "pages_per_session": 5,
            "scroll_speed": "fast",
            "reads_articles": True,
            "watches_video": False
        }
    },
    {
        "name": "Петрова Елена",
        "gender": "female",
        "age": 35,
        "city": "Санкт-Петербург",
        "timezone": "Europe/Moscow",
        "profession": "Учитель",
        "interests": ["педагогика", "литература", "театр", "вязание", "садоводство"],
        "typical_sites": [
            "https://ya.ru", "https://dzen.ru", "https://vk.com",
            "https://ok.ru", "https://7ya.ru", "https://lenta.ru",
            "https://wildberries.ru", "https://kinopoisk.ru",
            "https://pogoda.yandex.ru", "https://news.yandex.ru"
        ],
        "search_queries": [
            "конспект урока русский язык 5 класс",
            "афиша спб театры", "схемы вязания спицами",
            "рассада помидор когда сажать", "книжные новинки 2024"
        ],
        "activity_hours": [6, 7, 14, 15, 16, 19, 20, 21, 22],
        "device_preference": "mixed",
        "browser_behavior": {
            "avg_session_minutes": 30,
            "pages_per_session": 10,
            "scroll_speed": "slow",
            "reads_articles": True,
            "watches_video": True
        }
    },
    {
        "name": "Волков Артём",
        "gender": "male",
        "age": 22,
        "city": "Казань",
        "timezone": "Europe/Moscow",
        "profession": "Студент",
        "interests": ["игры", "аниме", "программирование", "музыка", "кино"],
        "typical_sites": [
            "https://ya.ru", "https://vk.com", "https://dzen.ru",
            "https://habr.com", "https://pikabu.ru", "https://kinopoisk.ru",
            "https://music.yandex.ru", "https://twitch.tv",
            "https://steam.com", "https://ozon.ru"
        ],
        "search_queries": [
            "python курс для начинающих", "лучшие аниме 2024",
            "игры для steam скидки", "кинопоиск топ фильмов",
            "казань мероприятия выходные"
        ],
        "activity_hours": [10, 11, 12, 14, 15, 16, 17, 20, 21, 22, 23, 0, 1],
        "device_preference": "desktop",
        "browser_behavior": {
            "avg_session_minutes": 45,
            "pages_per_session": 15,
            "scroll_speed": "fast",
            "reads_articles": False,
            "watches_video": True
        }
    },
    {
        "name": "Новикова Ольга",
        "gender": "female",
        "age": 53,
        "city": "Краснодар",
        "timezone": "Europe/Moscow",
        "profession": "Бухгалтер",
        "interests": ["кулинария", "садоводство", "здоровье", "сериалы", "дача"],
        "typical_sites": [
            "https://ya.ru", "https://ok.ru", "https://mail.ru",
            "https://dzen.ru", "https://wildberries.ru",
            "https://pogoda.yandex.ru", "https://lenta.ru",
            "https://ivi.ru", "https://7ya.ru", "https://kp.ru"
        ],
        "search_queries": [
            "рецепт борща классический", "давление нормы по возрасту",
            "когда подкармливать клубнику", "сериалы 2024 россия",
            "налоговый вычет за лечение документы"
        ],
        "activity_hours": [7, 8, 12, 13, 18, 19, 20, 21],
        "device_preference": "mobile",
        "browser_behavior": {
            "avg_session_minutes": 12,
            "pages_per_session": 4,
            "scroll_speed": "slow",
            "reads_articles": True,
            "watches_video": True
        }
    },
    {
        "name": "Кузнецов Максим",
        "gender": "male",
        "age": 31,
        "city": "Новосибирск",
        "timezone": "Asia/Novosibirsk",
        "profession": "Менеджер по продажам",
        "interests": ["бизнес", "инвестиции", "туризм", "бег", "кофе"],
        "typical_sites": [
            "https://ya.ru", "https://rbc.ru", "https://banki.ru",
            "https://vk.com", "https://hh.ru", "https://avito.ru",
            "https://ozon.ru", "https://dzen.ru", "https://2gis.ru",
            "https://market.yandex.ru"
        ],
        "search_queries": [
            "акции сбербанк прогноз", "вакансии менеджер новосибирск",
            "кроссовки для бега отзывы", "кофемашина для дома рейтинг",
            "горящие туры из новосибирска"
        ],
        "activity_hours": [8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21],
        "device_preference": "mixed",
        "browser_behavior": {
            "avg_session_minutes": 18,
            "pages_per_session": 7,
            "scroll_speed": "medium",
            "reads_articles": True,
            "watches_video": False
        }
    },
]


# ---------------------------------------------------------------------------
# Main generator class
# ---------------------------------------------------------------------------
class AIPersonaGenerator:
    """Generates coherent user personas via Gemini or fallback data."""

    def __init__(self):
        self._cache: List[Dict] = []  # pre-generated personas not yet assigned
        self._model = GEMINI_MODEL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_personas(self, count: int = 5) -> List[Dict]:
        """
        Generate *count* personas.
        Uses Gemini if available, otherwise returns from fallback pool.
        """
        client = _get_gemini_client()
        if client:
            personas = self._generate_via_gemini(client, count)
            if personas:
                return personas

        # Fallback
        logger.info(f"Using fallback personas (requested {count})")
        return self._generate_fallback(count)

    def generate_one(self) -> Dict:
        """Generate a single persona."""
        if self._cache:
            return self._cache.pop(0)

        results = self.generate_personas(count=5)
        if len(results) > 1:
            self._cache.extend(results[1:])
        return results[0]

    def generate_warmup_sites(self, persona_data: Dict) -> Dict:
        """
        Generate 50 personalised warmup sites + 20 extra search queries
        based on persona data. Returns dict with 'warmup_sites' and
        'extra_search_queries' lists.

        Uses Gemini if available, otherwise builds from fallback pools.
        """
        client = _get_gemini_client()
        if client:
            result = self._generate_warmup_sites_via_gemini(client, persona_data)
            if result:
                return result

        # Fallback
        logger.info("Using fallback warmup sites generation")
        return self._generate_warmup_sites_fallback(persona_data)

    def get_persona_for_profile(self, profile_name: str, is_mobile: bool = False) -> Dict:
        """Get a persona tailored for a specific profile.

        Optionally filters by device_preference matching is_mobile.
        """
        persona = self.generate_one()

        # If mobile profile requested, nudge device_preference
        if is_mobile:
            persona["device_preference"] = random.choice(["mobile", "mixed"])

        # Add metadata
        persona["assigned_profile"] = profile_name
        persona["generated_at"] = datetime.utcnow().isoformat()
        persona["persona_hash"] = hashlib.md5(
            json.dumps(persona, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:12]

        return persona

    # ------------------------------------------------------------------
    # Gemini generation
    # ------------------------------------------------------------------
    def _generate_via_gemini(self, client, count: int) -> Optional[List[Dict]]:
        """Call Gemini to generate personas."""
        try:
            prompt = PERSONA_PROMPT_TEMPLATE.format(count=count)

            logger.info(f"Requesting {count} personas from Gemini ({self._model})…")
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
            )

            raw = response.text.strip()

            # Strip possible markdown fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]  # remove first ``` line
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            personas = json.loads(raw)

            if not isinstance(personas, list):
                logger.error("Gemini returned non-list JSON")
                return None

            # Validate & normalise
            valid = []
            for p in personas:
                normalised = self._normalise_persona(p)
                if normalised:
                    valid.append(normalised)

            logger.info(f"Gemini returned {len(valid)} valid personas")
            return valid if valid else None

        except json.JSONDecodeError as exc:
            logger.error(f"Failed to parse Gemini JSON: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Gemini persona generation failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Fallback generation
    # ------------------------------------------------------------------
    def _generate_fallback(self, count: int) -> List[Dict]:
        """Return personas from the built-in pool with slight randomisation."""
        import copy

        results = []
        pool = copy.deepcopy(FALLBACK_PERSONAS)
        random.shuffle(pool)

        for i in range(count):
            base = pool[i % len(pool)]
            # Add small variations
            base["age"] = max(18, min(65, base["age"] + random.randint(-3, 3)))
            # Shuffle interests order
            random.shuffle(base["interests"])
            # Shuffle activity hours slightly
            if random.random() > 0.5:
                base["activity_hours"] = sorted(set(
                    [max(0, min(23, h + random.choice([-1, 0, 1])))
                     for h in base["activity_hours"]]
                ))
            results.append(base)

        return results

    # ------------------------------------------------------------------
    # Warmup sites generation via Gemini
    # ------------------------------------------------------------------
    def _generate_warmup_sites_via_gemini(self, client, persona_data: Dict) -> Optional[Dict]:
        """Call Gemini to generate 50 warmup sites tailored to persona."""
        try:
            interests_str = ", ".join(persona_data.get("interests", ["интернет"]))
            prompt = WARMUP_SITES_PROMPT_TEMPLATE.format(
                name=persona_data.get("name", "Пользователь"),
                gender=persona_data.get("gender", "male"),
                age=persona_data.get("age", 30),
                city=persona_data.get("city", "Москва"),
                profession=persona_data.get("profession", "Специалист"),
                interests=interests_str,
            )

            logger.info(f"Requesting warmup sites from Gemini for persona: {persona_data.get('name', '?')}")
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
            )

            raw = response.text.strip()

            # Strip possible markdown fences
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            data = json.loads(raw)

            if not isinstance(data, dict):
                logger.error("Gemini returned non-dict JSON for warmup sites")
                return None

            warmup_sites = data.get("warmup_sites", [])
            extra_queries = data.get("extra_search_queries", [])

            # Validate URLs
            valid_sites = []
            for url in warmup_sites:
                if isinstance(url, str) and url.startswith("https://"):
                    # Normalise: strip trailing slash
                    url = url.rstrip("/")
                    if url not in valid_sites:
                        valid_sites.append(url)

            # Ensure minimum Yandex coverage
            yandex_must = [
                "https://ya.ru", "https://dzen.ru", "https://market.yandex.ru",
                "https://pogoda.yandex.ru", "https://news.yandex.ru",
            ]
            for url in yandex_must:
                if not any(url in s for s in valid_sites):
                    valid_sites.insert(0, url)

            # Validate queries
            valid_queries = [q for q in extra_queries if isinstance(q, str) and len(q) > 2]

            logger.info(f"Gemini returned {len(valid_sites)} warmup sites, {len(valid_queries)} queries")
            return {
                "warmup_sites": valid_sites[:55],  # cap at 55 (a few extra ok)
                "extra_search_queries": valid_queries[:25],
            }

        except json.JSONDecodeError as exc:
            logger.error(f"Failed to parse Gemini warmup sites JSON: {exc}")
            return None
        except Exception as exc:
            logger.error(f"Gemini warmup sites generation failed: {exc}")
            return None

    def _generate_warmup_sites_fallback(self, persona_data: Dict) -> Dict:
        """Build warmup sites from hardcoded pools when Gemini is unavailable."""
        sites = []

        # Yandex ecosystem (all)
        yandex = [
            "https://ya.ru", "https://yandex.ru", "https://dzen.ru",
            "https://market.yandex.ru", "https://pogoda.yandex.ru",
            "https://news.yandex.ru", "https://music.yandex.ru",
            "https://kinopoisk.ru", "https://translate.yandex.ru",
            "https://yandex.ru/images",
        ]
        sites.extend(yandex)

        # Persona typical_sites
        ptsites = persona_data.get("typical_sites", [])
        for s in ptsites:
            if isinstance(s, str) and s not in sites:
                sites.append(s)

        # Popular Russian
        russian = [
            "https://vk.com", "https://mail.ru", "https://ok.ru",
            "https://rbc.ru", "https://lenta.ru", "https://ria.ru",
            "https://tass.ru", "https://gazeta.ru", "https://kommersant.ru",
            "https://avito.ru", "https://ozon.ru", "https://wildberries.ru",
            "https://habr.com", "https://pikabu.ru", "https://sports.ru",
            "https://hh.ru", "https://2gis.ru", "https://dns-shop.ru",
            "https://mvideo.ru", "https://drive2.ru", "https://banki.ru",
            "https://auto.ru", "https://ivi.ru", "https://kp.ru",
            "https://7ya.ru", "https://gosuslugi.ru", "https://sberbank.ru",
            "https://tinkoff.ru", "https://lamoda.ru", "https://citilink.ru",
        ]
        for s in russian:
            if s not in sites:
                sites.append(s)

        # International
        intl = [
            "https://google.com", "https://youtube.com",
            "https://ru.wikipedia.org", "https://github.com",
        ]
        for s in intl:
            if s not in sites:
                sites.append(s)

        # Shuffle and cap at 50
        random.shuffle(sites)
        sites = sites[:50]

        # Fallback extra queries from persona + generic
        extra_queries = list(persona_data.get("search_queries", []))
        generic_extra = [
            f"погода {persona_data.get('city', 'москва')}",
            f"новости {persona_data.get('city', 'москва')} сегодня",
            f"расписание автобусов {persona_data.get('city', 'москва')}",
            f"{persona_data.get('profession', 'работа')} вакансии",
            "курс доллара сегодня", "рецепт на ужин быстро",
            "афиша кино", "прогноз погоды на неделю",
            "скидки ozon", "новости спорт россия",
            "как оформить загранпаспорт", "калькулятор ипотеки",
            "доставка еды рядом", "отзывы стоматология",
            "расписание электричек",
        ]
        for q in generic_extra:
            if q not in extra_queries:
                extra_queries.append(q)

        random.shuffle(extra_queries)
        return {
            "warmup_sites": sites,
            "extra_search_queries": extra_queries[:20],
        }

    # ------------------------------------------------------------------
    # Validation / normalisation
    # ------------------------------------------------------------------
    def _normalise_persona(self, p: Dict) -> Optional[Dict]:
        """Validate and normalise a persona dict from Gemini."""
        required_fields = [
            "name", "gender", "age", "city", "timezone",
            "profession", "interests", "typical_sites",
        ]
        for field in required_fields:
            if field not in p:
                logger.warning(f"Persona missing required field: {field}")
                return None

        # Ensure correct types
        try:
            p["age"] = int(p["age"])
        except (ValueError, TypeError):
            p["age"] = random.randint(22, 50)

        if not isinstance(p.get("interests"), list):
            p["interests"] = []
        if not isinstance(p.get("typical_sites"), list):
            p["typical_sites"] = ["https://ya.ru", "https://vk.com", "https://dzen.ru"]
        if not isinstance(p.get("search_queries"), list):
            p["search_queries"] = []
        if not isinstance(p.get("activity_hours"), list):
            p["activity_hours"] = [9, 10, 11, 12, 18, 19, 20, 21]

        # Defaults for optional fields
        p.setdefault("device_preference", "desktop")
        p.setdefault("browser_behavior", {
            "avg_session_minutes": 20,
            "pages_per_session": 6,
            "scroll_speed": "medium",
            "reads_articles": True,
            "watches_video": True,
        })

        # Ensure Yandex ecosystem is in typical_sites
        yandex_must = ["https://ya.ru", "https://dzen.ru"]
        for url in yandex_must:
            if not any(url in s for s in p["typical_sites"]):
                p["typical_sites"].insert(0, url)

        return p


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------
_generator_instance = None


def get_persona_generator() -> AIPersonaGenerator:
    """Get or create the singleton generator."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = AIPersonaGenerator()
    return _generator_instance


def generate_persona_for_profile(profile_name: str, is_mobile: bool = False) -> Dict:
    """Convenience: generate one persona for the given profile."""
    return get_persona_generator().get_persona_for_profile(profile_name, is_mobile)


def generate_personas(count: int = 5) -> List[Dict]:
    """Convenience: generate multiple personas."""
    return get_persona_generator().generate_personas(count)


def generate_warmup_sites(persona_data: Dict) -> Dict:
    """Convenience: generate 50 warmup sites for a persona.
    Returns dict with 'warmup_sites' and 'extra_search_queries' lists.
    """
    return get_persona_generator().generate_warmup_sites(persona_data)
