"""
Yandex Maps Parser — collects company data from Yandex Maps search results.
"""
import time
import random
import re
import logging
from typing import Dict, List, Optional
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    StaleElementReferenceException
)

logger = logging.getLogger(__name__)


def parse_yandex_maps_search(driver, search_url: str, max_items: int = 100,
                              on_progress=None) -> List[Dict]:
    """
    Open Yandex Maps search results and collect company data.
    
    Args:
        driver: Selenium WebDriver instance
        search_url: Yandex Maps search URL or will be constructed from query
        max_items: Maximum number of companies to collect
        on_progress: Optional callback(items_found, items_parsed) for progress updates
    
    Returns:
        List of company dicts with parsed data
    """
    companies = []
    seen_ids = set()
    
    logger.info(f"🔍 Opening Yandex Maps search: {search_url[:100]}...")
    driver.get(search_url)
    time.sleep(3 + random.uniform(1, 3))
    
    # Wait for search results to load
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                'div.search-list-view div.search-snippet-view, '
                'ul.search-list-view__list li.search-snippet-view'))
        )
    except TimeoutException:
        # Try alternative selectors
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    '[class*="SearchSnippet"], [class*="search-snippet"], '
                    '[class*="serp-item"], [class*="SearchSerp"]'))
            )
        except TimeoutException:
            logger.warning("⚠️ Could not find search results on page")
            return companies
    
    logger.info("✅ Search results loaded")
    
    # Scroll through the search results panel to load more items
    scroll_container = _find_scroll_container(driver)
    
    page_num = 0
    max_scroll_attempts = 50
    no_new_items_count = 0
    
    while len(companies) < max_items and page_num < max_scroll_attempts:
        page_num += 1
        
        # Collect visible company links
        links = _collect_company_links(driver)
        new_links = [(url, oid) for url, oid in links if oid not in seen_ids]
        
        if not new_links:
            no_new_items_count += 1
            if no_new_items_count >= 5:
                logger.info(f"📋 No more new results after {len(companies)} companies")
                break
        else:
            no_new_items_count = 0
        
        # Parse each new company
        for link_url, org_id in new_links:
            if len(companies) >= max_items:
                break
            if org_id in seen_ids:
                continue
            seen_ids.add(org_id)
            
            try:
                company = _parse_company_card(driver, link_url, org_id)
                if company and company.get('name'):
                    companies.append(company)
                    logger.info(f"  ✅ [{len(companies)}/{max_items}] {company['name']}")
                    if on_progress:
                        on_progress(len(seen_ids), len(companies))
            except Exception as e:
                logger.warning(f"  ⚠️ Error parsing {org_id}: {e}")
            
            # Small delay between card opens
            time.sleep(1 + random.uniform(0.5, 2))
        
        # Scroll to load more results
        if scroll_container and len(companies) < max_items:
            _scroll_results(driver, scroll_container)
            time.sleep(1.5 + random.uniform(0.5, 1.5))
    
    logger.info(f"🏁 Parsing complete: {len(companies)} companies collected")
    return companies


def _find_scroll_container(driver) -> Optional[object]:
    """Find the scrollable search results panel."""
    selectors = [
        'div.scroll__container',
        '[class*="SearchSerp"] [class*="scroll"]',
        'div.search-list-view',
        '[class*="searchResults"]',
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el.is_displayed():
                return el
        except NoSuchElementException:
            continue
    return None


def _scroll_results(driver, container):
    """Scroll the results panel to load more items."""
    try:
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight * 0.8",
            container
        )
    except Exception:
        # Fallback: scroll the whole page
        driver.execute_script("window.scrollBy(0, 800)")


def _collect_company_links(driver) -> List[tuple]:
    """Collect company links from search results. Returns list of (url, org_id)."""
    links = []
    
    # Find all links that look like org pages
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, 
            'a[href*="/org/"], a[href*="oid="]')
        
        for el in elements:
            try:
                href = el.get_attribute('href')
                if not href:
                    continue
                
                org_id = _extract_org_id(href)
                if org_id and org_id not in [l[1] for l in links]:
                    links.append((href, org_id))
            except StaleElementReferenceException:
                continue
    except Exception as e:
        logger.warning(f"Error collecting links: {e}")
    
    return links


def _extract_org_id(url: str) -> Optional[str]:
    """Extract organization ID from Yandex Maps URL."""
    # Pattern: /org/name/1234567890/
    match = re.search(r'/org/[^/]+/(\d+)', url)
    if match:
        return match.group(1)
    # Pattern: oid=1234567890
    match = re.search(r'oid=(\d+)', url)
    if match:
        return match.group(1)
    return None


def _parse_company_card(driver, card_url: str, org_id: str) -> Optional[Dict]:
    """
    Open a company card and extract all available data.
    """
    # Navigate to the company page
    driver.get(card_url)
    time.sleep(2 + random.uniform(1, 2))
    
    # Wait for card to load
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                '[class*="orgpage-header"], [class*="card-title"], '
                'h1[class*="title"], [class*="business-card"]'))
        )
    except TimeoutException:
        logger.warning(f"Card did not load for org {org_id}")
        return None
    
    company = {
        'yandex_maps_id': org_id,
        'yandex_maps_url': driver.current_url,
    }
    
    # Extract name
    company['name'] = _extract_text(driver, [
        'h1[class*="orgpage-header-view__header"]',
        'h1[class*="card-title-view__title"]',
        'h1[class*="title"]',
        '[class*="orgpage-header"] h1',
        '[class*="business-card"] h1',
    ])
    
    # Extract category
    company['category'] = _extract_text(driver, [
        'a[class*="orgpage-header-view__category"]',
        '[class*="business-card-title-view__category"]',
        '[class*="orgpage-categories"]',
        'a[class*="category"]',
    ])
    
    # Extract address
    company['address'] = _extract_text(driver, [
        '[class*="orgpage-contacts-view__address"] .orgpage-field-view__text',
        'a[class*="orgpage-header-view__address"]',
        '[class*="toponym-card-title-view__coords-address"]',
        '[class*="business-contacts-view__address"]',
        '[class*="address"]',
    ])
    
    # Extract rating
    rating_text = _extract_text(driver, [
        'span[class*="business-rating-badge-view__rating-text"]',
        '[class*="orgpage-header-view__rating"] span',
        '[class*="rating-badge"] span',
    ])
    if rating_text:
        try:
            company['rating'] = float(rating_text.replace(',', '.'))
        except ValueError:
            pass
    
    # Extract reviews count
    reviews_text = _extract_text(driver, [
        'span[class*="business-rating-badge-view__rating-count"]',
        '[class*="orgpage-header-view__rating-count"]',
        'a[class*="rating-count"]',
    ])
    if reviews_text:
        match = re.search(r'(\d+)', reviews_text.replace('\xa0', ''))
        if match:
            company['reviews_count'] = int(match.group(1))
    
    # Extract phone numbers
    phones = _extract_phones(driver)
    if phones:
        company['phone'] = phones[0]
        if len(phones) > 1:
            company['phone2'] = phones[1]
    
    # Extract website
    company['website'] = _extract_website(driver)
    
    # Extract social links and messengers
    socials = _extract_socials(driver)
    company.update(socials)
    
    # Extract email
    company['email'] = _extract_email(driver)
    
    # Extract working hours
    company['working_hours'] = _extract_working_hours(driver)
    
    # Extract coordinates from URL
    coords = _extract_coordinates(driver.current_url)
    if coords:
        company['latitude'] = coords[0]
        company['longitude'] = coords[1]
    
    return company


def _extract_text(driver, selectors: List[str]) -> Optional[str]:
    """Try multiple CSS selectors and return first matching text."""
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            text = el.text.strip()
            if text:
                return text
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return None


def _extract_phones(driver) -> List[str]:
    """Extract phone numbers from the company card."""
    phones = []
    selectors = [
        '[class*="orgpage-phones-view"] a[href^="tel:"]',
        '[class*="business-contacts-view__phone"] a[href^="tel:"]',
        'a[href^="tel:"]',
    ]
    for sel in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                href = el.get_attribute('href') or ''
                phone = href.replace('tel:', '').strip()
                if phone and phone not in phones:
                    phones.append(phone)
            if phones:
                break
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    
    # Also try to find phone in text
    if not phones:
        try:
            page_text = driver.find_element(By.TAG_NAME, 'body').text
            phone_patterns = re.findall(r'[\+7|8][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', page_text)
            for p in phone_patterns[:2]:
                phones.append(p.strip())
        except Exception:
            pass
    
    return phones


def _extract_website(driver) -> Optional[str]:
    """Extract website URL from the company card."""
    selectors = [
        '[class*="orgpage-contacts-view__website"] a',
        '[class*="business-contacts-view__link"] a[href*="http"]',
        'a[class*="business-urls-view__link"]',
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            href = el.get_attribute('href')
            if href and 'yandex' not in href.lower():
                # Clean tracking redirects
                if 'redirect' in href.lower():
                    match = re.search(r'url=([^&]+)', href)
                    if match:
                        from urllib.parse import unquote
                        return unquote(match.group(1))
                return href
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return None


def _extract_socials(driver) -> Dict:
    """Extract social media links (Telegram, WhatsApp, VK, Instagram)."""
    result = {}
    
    try:
        all_links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
        for link in all_links:
            try:
                href = (link.get_attribute('href') or '').lower()
                
                if 't.me/' in href or 'telegram' in href:
                    result['telegram'] = link.get_attribute('href')
                elif 'wa.me/' in href or 'whatsapp' in href or 'api.whatsapp' in href:
                    result['whatsapp'] = link.get_attribute('href')
                elif 'vk.com/' in href and 'vk' not in result:
                    result['vk'] = link.get_attribute('href')
                elif 'instagram.com/' in href and 'instagram' not in result:
                    result['instagram'] = link.get_attribute('href')
            except StaleElementReferenceException:
                continue
    except Exception as e:
        logger.warning(f"Error extracting socials: {e}")
    
    return result


def _extract_email(driver) -> Optional[str]:
    """Extract email from the company card."""
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, 'a[href^="mailto:"]')
        for el in elements:
            href = el.get_attribute('href') or ''
            email = href.replace('mailto:', '').strip()
            if email and '@' in email:
                return email
    except Exception:
        pass
    
    # Try to find email in page text
    try:
        page_text = driver.find_element(By.TAG_NAME, 'body').text
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', page_text)
        if email_match:
            email = email_match.group(0)
            # Filter out yandex internal emails
            if 'yandex' not in email.lower() and 'ya.ru' not in email.lower():
                return email
    except Exception:
        pass
    
    return None


def _extract_working_hours(driver) -> Optional[str]:
    """Extract working hours text."""
    selectors = [
        '[class*="business-working-status-view"]',
        '[class*="orgpage-schedule"]',
        '[class*="working-hours"]',
    ]
    text = _extract_text(driver, selectors)
    if text:
        return text[:500]  # Limit length
    return None


def _extract_coordinates(url: str) -> Optional[tuple]:
    """Extract lat/lng from Yandex Maps URL."""
    match = re.search(r'll=([-\d.]+)%2C([-\d.]+)', url)
    if match:
        try:
            lng = float(match.group(1))
            lat = float(match.group(2))
            return (lat, lng)
        except ValueError:
            pass
    return None


def build_search_url(query: str, region: str = "Москва") -> str:
    """Build Yandex Maps search URL from query and region."""
    from urllib.parse import quote
    base = "https://yandex.ru/maps/"
    
    # Moscow region params
    regions = {
        "Москва": "213",
        "Санкт-Петербург": "2",
        "Новосибирск": "65",
        "Екатеринбург": "54",
        "Казань": "43",
    }
    
    search_text = f"{query} {region}" if region else query
    url = f"{base}?text={quote(search_text)}"
    
    return url
