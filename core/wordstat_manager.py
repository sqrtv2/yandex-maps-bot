"""
Yandex Wordstat API client.
Uses the Search API v2 REST endpoint to get keyword frequency data.
API docs: https://aistudio.yandex.ru/docs/ru/search-api/api-ref/Wordstat/getTop.html

Frequency types:
  - broad:  keyword as-is (all word forms, any order)
  - phrase: "keyword" in quotes (exact phrase, all word forms)
  - exact:  "!word1 !word2" in quotes with ! (exact word forms)
"""
import requests
import time
import logging
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

WORDSTAT_TOP_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"


def _make_phrase_query(keyword: str) -> str:
    """Wrap keyword in quotes for phrase match: купить диван -> "купить диван" """
    kw = keyword.strip().strip('"')
    return f'"{kw}"'


def _make_exact_query(keyword: str) -> str:
    """Wrap each word with ! inside quotes: купить диван -> "!купить !диван" """
    kw = keyword.strip().strip('"')
    words = kw.split()
    exact_words = " ".join(f"!{w.lstrip('!')}" for w in words if w)
    return f'"{exact_words}"'


def _fetch_total_count(
    phrase: str,
    api_key: str,
    folder_id: str = "",
    regions: Optional[List[str]] = None,
) -> Optional[int]:
    """
    Call Wordstat GetTop and return totalCount.
    """
    headers = {
        "Authorization": f"Api-key {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "phrase": phrase,
        "numPhrases": "1",
        "devices": ["DEVICE_ALL"],
    }
    if folder_id:
        body["folderId"] = folder_id
    if regions:
        body["regions"] = regions

    try:
        resp = requests.post(WORDSTAT_TOP_URL, json=body, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("totalCount")
            return int(total) if total is not None else 0
        else:
            logger.warning(f"Wordstat API error {resp.status_code} for '{phrase}': {resp.text[:300]}")
            return None
    except Exception as e:
        logger.error(f"Wordstat request failed for '{phrase}': {e}")
        return None


def get_keyword_all_frequencies(
    keyword: str,
    api_key: str,
    folder_id: str = "",
    regions: Optional[List[str]] = None,
    delay: float = 0.15,
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Fetch all 3 frequency types for a single keyword.
    Returns (broad, phrase, exact).
    """
    broad = _fetch_total_count(keyword, api_key, folder_id, regions)
    time.sleep(delay)
    phrase = _fetch_total_count(_make_phrase_query(keyword), api_key, folder_id, regions)
    time.sleep(delay)
    exact = _fetch_total_count(_make_exact_query(keyword), api_key, folder_id, regions)
    return broad, phrase, exact


def get_keywords_frequency_batch(
    keywords: List[str],
    api_key: str,
    folder_id: str = "",
    regions: Optional[List[str]] = None,
    delay: float = 0.15,
    on_progress=None,
) -> Dict[str, Tuple[Optional[int], Optional[int], Optional[int]]]:
    """
    Get all 3 frequency types for multiple keywords.
    Returns dict {keyword: (broad, phrase, exact)}.
    on_progress(done, total) is called after each keyword.
    """
    result = {}
    total = len(keywords)
    for i, kw in enumerate(keywords):
        broad, phrase, exact = get_keyword_all_frequencies(kw, api_key, folder_id, regions, delay)
        result[kw] = (broad, phrase, exact)
        if on_progress:
            on_progress(i + 1, total)
        if delay > 0 and i < total - 1:
            time.sleep(delay)
    return result
