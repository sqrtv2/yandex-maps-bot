"""
Yandex Maps Parser — collects company data from Yandex Maps search results.
"""
import time
import random
import re
import logging
from typing import Dict, List, Optional
from datetime import datetime
from urllib.parse import quote, unquote

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
    """
    companies = []
    seen_ids = set()

    logger.info(f"🔍 Opening Yandex Maps search: {search_url[:100]}...")
    driver.get(search_url)
    time.sleep(4 + random.uniform(1, 3))

    # Remember the search URL (may redirect to yandex.com)
    actual_search_url = driver.current_url

    # Wait for search results to load
    if not _wait_for_search_results(driver):
        logger.warning("⚠️ Could not find search results on page")
        return companies

    logger.info("✅ Search results loaded")

    page_num = 0
    max_scroll_attempts = 50
    no_new_items_count = 0

    while len(companies) < max_items and page_num < max_scroll_attempts:
        page_num += 1

        # Collect visible company links (only base org links, no /reviews/ etc.)
        links = _collect_company_links(driver)
        new_links = [(url, oid) for url, oid in links if oid not in seen_ids]

        if not new_links:
            no_new_items_count += 1
            if no_new_items_count >= 3:
                logger.info(f"📋 No more new results after {len(companies)} companies")
                break
            # Scroll and retry
            _scroll_results(driver)
            time.sleep(2 + random.uniform(0.5, 1.5))
            continue
        else:
            no_new_items_count = 0

        # Parse each new company card
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
                else:
                    logger.warning(f"  ⚠️ No data for org {org_id}")
            except Exception as e:
                logger.warning(f"  ⚠️ Error parsing {org_id}: {e}")

            time.sleep(1 + random.uniform(0.5, 1.5))

        # Navigate back to search results for next batch
        if len(companies) < max_items:
            logger.info(f"🔄 Returning to search results (page {page_num})...")
            driver.get(actual_search_url)
            time.sleep(3 + random.uniform(1, 2))

            if not _wait_for_search_results(driver):
                logger.warning("⚠️ Could not reload search results, stopping")
                break

            # Scroll down to load items beyond what we already parsed
            for _ in range(page_num):
                _scroll_results(driver)
                time.sleep(0.5)
            time.sleep(1.5 + random.uniform(0.5, 1))

    logger.info(f"🏁 Parsing complete: {len(companies)} companies collected")
    return companies


def _wait_for_search_results(driver, timeout: int = 20) -> bool:
    """Wait for search results to appear on the page."""
    selectors = (
        'div.search-snippet-view, '
        'div.search-business-snippet-view, '
        '[class*="search-snippet-view"], '
        '[class*="search-list-view__list"]'
    )
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selectors))
        )
        return True
    except TimeoutException:
        return False


def _scroll_results(driver):
    """Scroll the results panel to load more items."""
    try:
        container = driver.find_element(By.CSS_SELECTOR, 'div.scroll__container')
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight * 0.8",
            container
        )
    except (NoSuchElementException, Exception):
        driver.execute_script("window.scrollBy(0, 800)")


def _collect_company_links(driver) -> List[tuple]:
    """Collect company links from search results. Returns list of (url, org_id)."""
    links = []
    seen_oids = set()

    try:
        elements = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/org/"]')

        for el in elements:
            try:
                href = el.get_attribute('href')
                if not href:
                    continue

                # Skip sub-pages like /reviews/, /gallery/, /prices/
                org_id = _extract_org_id(href)
                if not org_id or org_id in seen_oids:
                    continue

                # Only take the base org URL (/org/name/ID/ without extra path)
                match = re.search(r'(/org/[^/]+/\d+/?)', href)
                if not match:
                    continue

                # Reconstruct clean URL
                base = href.split('/org/')[0]
                clean_url = base + match.group(1)
                if not clean_url.endswith('/'):
                    clean_url += '/'

                seen_oids.add(org_id)
                links.append((clean_url, org_id))
            except StaleElementReferenceException:
                continue
    except Exception as e:
        logger.warning(f"Error collecting links: {e}")

    return links


def _extract_org_id(url: str) -> Optional[str]:
    """Extract organization ID from Yandex Maps URL."""
    match = re.search(r'/org/[^/]+/(\d+)', url)
    if match:
        return match.group(1)
    match = re.search(r'oid=(\d+)', url)
    if match:
        return match.group(1)
    return None


def _parse_company_card(driver, card_url: str, org_id: str) -> Optional[Dict]:
    """Open a company card and extract all available data."""
    driver.get(card_url)
    time.sleep(2.5 + random.uniform(1, 2))

    # Wait for card to load — use verified selectors
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                'h1.orgpage-header-view__header, '
                'div.business-card-view, '
                'div[class*="orgpage-header-view"]'))
        )
    except TimeoutException:
        logger.warning(f"Card did not load for org {org_id}")
        return None

    time.sleep(1)

    company = {
        'yandex_maps_id': org_id,
        'yandex_maps_url': driver.current_url,
    }

    # Extract name
    company['name'] = _extract_text(driver, [
        'h1.orgpage-header-view__header',
        'h1[class*="orgpage-header"]',
        'h1',
    ])

    # Extract category
    company['category'] = _extract_text(driver, [
        'a.orgpage-categories-info-view__link',
        '.orgpage-categories-info-view__item',
        '[class*="orgpage-categories"] a',
    ])

    # Extract address
    company['address'] = _extract_text(driver, [
        'a.orgpage-header-view__address',
        '.business-contacts-view__address',
        '[class*="orgpage-header-view__address"]',
    ])

    # Extract rating
    rating_text = _extract_text(driver, [
        'span.business-rating-badge-view__rating-text',
        '.business-summary-rating-badge-view__rating-text',
        '[class*="rating-badge-view__rating-text"]',
    ])
    if rating_text:
        try:
            company['rating'] = float(rating_text.replace(',', '.').strip())
        except ValueError:
            pass

    # Extract reviews count
    reviews_text = _extract_text(driver, [
        '.business-summary-rating-badge-view__rating-count',
        '.business-rating-amount-view',
        '[class*="rating-count"]',
    ])
    if reviews_text:
        nums = re.findall(r'\d+', reviews_text.replace('\xa0', ''))
        if nums:
            company['reviews_count'] = int(nums[0])

    # Extract phone numbers (may need to click expand button)
    phones = _extract_phones(driver)
    if phones:
        company['phone'] = phones[0]
        if len(phones) > 1:
            company['phone2'] = phones[1]

    # Extract website
    company['website'] = _extract_website(driver)

    # Extract social links
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

    # Try to expand phone list first (click "show phones" button)
    for expand_sel in [
        '.orgpage-phones-view__more',
        '.card-phones-view__more',
        '[class*="phones-view__more"]',
        '[class*="phones-view__control"]',
    ]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, expand_sel)
            if btn.is_displayed():
                btn.click()
                time.sleep(1)
                break
        except (NoSuchElementException, Exception):
            continue

    # Now collect phone links
    for sel in [
        '.card-phones-view__phone-number a[href^="tel:"]',
        '.orgpage-phones-view__phone-number a[href^="tel:"]',
        'a[href^="tel:"]',
    ]:
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

    # Fallback: extract phone numbers from visible card-phones text
    if not phones:
        for sel in [
            '.card-phones-view__phone-number',
            '.orgpage-phones-view__phone-number',
        ]:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    text = el.text.strip()
                    if text and re.search(r'\d{3}', text):
                        phones.append(text)
            except (NoSuchElementException, StaleElementReferenceException):
                continue
            if phones:
                break

    # Last resort: regex from page body
    if not phones:
        try:
            page_text = driver.find_element(By.TAG_NAME, 'body').text
            found = re.findall(r'[+7|8][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', page_text)
            for p in found[:2]:
                phones.append(p.strip())
        except Exception:
            pass

    return phones


def _extract_website(driver) -> Optional[str]:
    """Extract website URL from the company card."""
    for sel in [
        'a.business-urls-view__link',
        '.business-urls-view__url a',
        '.business-contacts-view__link a[href*="http"]',
    ]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            href = el.get_attribute('href')
            if href and 'yandex' not in href.lower() and 'ya.ru' not in href.lower():
                if 'redirect' in href.lower():
                    match = re.search(r'url=([^&]+)', href)
                    if match:
                        return unquote(match.group(1))
                return href
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return None


def _extract_socials(driver) -> Dict:
    """Extract social media links (Telegram, WhatsApp, VK, Instagram)."""
    result = {}

    # First try dedicated social links section
    try:
        social_links = driver.find_elements(By.CSS_SELECTOR, 
            '.card-social-links-view__link, .card-footer-view__social-links a')
        for link in social_links:
            try:
                href = (link.get_attribute('href') or '').lower()
                _classify_social(href, link, result)
            except StaleElementReferenceException:
                continue
    except Exception:
        pass

    # Also scan all links on the page
    if not result:
        try:
            all_links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
            for link in all_links:
                try:
                    href = (link.get_attribute('href') or '').lower()
                    _classify_social(href, link, result)
                except StaleElementReferenceException:
                    continue
        except Exception:
            pass

    return result


def _classify_social(href: str, link, result: Dict):
    """Classify a link as a social network."""
    if 't.me/' in href or 'telegram' in href:
        if 'telegram' not in result:
            result['telegram'] = link.get_attribute('href')
    elif 'wa.me/' in href or 'whatsapp' in href or 'api.whatsapp' in href:
        if 'whatsapp' not in result:
            result['whatsapp'] = link.get_attribute('href')
    elif 'vk.com/' in href:
        if 'vk' not in result:
            result['vk'] = link.get_attribute('href')
    elif 'instagram.com/' in href:
        if 'instagram' not in result:
            result['instagram'] = link.get_attribute('href')


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

    # Try regex in page text
    try:
        page_text = driver.find_element(By.TAG_NAME, 'body').text
        match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', page_text)
        if match:
            email = match.group(0)
            if 'yandex' not in email.lower() and 'ya.ru' not in email.lower():
                return email
    except Exception:
        pass

    return None


def _extract_working_hours(driver) -> Optional[str]:
    """Extract working hours text."""
    selectors = [
        '.business-card-working-status-view__text',
        '.business-working-status-view',
        '.business-working-status-flip-view',
        '[class*="working-status-view"]',
    ]
    text = _extract_text(driver, selectors)
    if text:
        return text[:500]
    return None


def _extract_coordinates(url: str) -> Optional[tuple]:
    """Extract lat/lng from Yandex Maps URL."""
    # Try ll= parameter (URL encoded comma)
    match = re.search(r'll=([-\d.]+)%2C([-\d.]+)', url)
    if match:
        try:
            return (float(match.group(2)), float(match.group(1)))
        except ValueError:
            pass
    # Try ll= parameter (regular comma)
    match = re.search(r'll=([-\d.]+),([-\d.]+)', url)
    if match:
        try:
            return (float(match.group(2)), float(match.group(1)))
        except ValueError:
            pass
    return None


def build_search_url(query: str, region: str = "Москва") -> str:
    """Build Yandex Maps search URL from query and region."""
    base = "https://yandex.ru/maps/"
    search_text = f"{query} {region}" if region else query
    return f"{base}?text={quote(search_text)}"
