"""
Yandex Maps Parser — collects company data from Yandex Maps search results.
Uses click-based navigation: clicks on search snippets, parses the side panel card,
then uses browser back to return to search results. Never uses driver.get() for org pages.
"""
import time
import random
import re
import logging
from typing import Dict, List, Optional
from urllib.parse import quote, unquote

from core.playwright_driver import (
    By, EC, expected_conditions,
    PlaywrightWait as WebDriverWait,
    TimeoutException, NoSuchElementException, WebDriverException,
    StaleElementReferenceException,
)

logger = logging.getLogger(__name__)


def parse_yandex_maps_search(driver, search_url: str, max_items: int = 100,
                              on_progress=None, captcha_solver=None) -> List[Dict]:
    """
    Open Yandex Maps search results and collect company data via snippet clicks.
    """
    companies = []
    seen_ids = set()

    logger.info(f"🔍 Opening Yandex Maps search: {search_url[:100]}...")
    driver.get(search_url)
    time.sleep(5 + random.uniform(1, 3))

    # Log page state for debugging
    try:
        _title = driver.execute_script("return document.title")
        _url = driver.current_url
        logger.info(f"📄 Page loaded: title='{_title}', url={_url[:120]}")
    except Exception:
        pass

    # Check for captcha and solve if needed
    if not _wait_for_search_results(driver, timeout=10):
        if captcha_solver and _check_and_solve_captcha(driver, captcha_solver):
            logger.info("🔓 Captcha solved, waiting for search results...")
            time.sleep(3 + random.uniform(1, 2))
            # After solving, page may redirect — wait for results again
            if not _wait_for_search_results(driver):
                logger.warning("⚠️ Still no search results after captcha solve")
                try:
                    driver.save_screenshot("/app/screenshots/parser_no_results.png")
                except Exception:
                    pass
                return companies
        else:
            logger.warning("⚠️ Could not find search results on page")
            try:
                driver.save_screenshot("/app/screenshots/parser_no_results.png")
                logger.info("📸 Debug screenshot saved to /app/screenshots/parser_no_results.png")
            except Exception:
                pass
            return companies

    logger.info("✅ Search results loaded")

    scroll_round = 0
    max_rounds = 50
    no_new_count = 0

    while len(companies) < max_items and scroll_round < max_rounds:
        scroll_round += 1

        # Re-query snippets every iteration (DOM references go stale after card navigation)
        snippets = driver.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view")
        if not snippets:
            snippets = driver.find_elements(By.CSS_SELECTOR, ".search-snippet-view")

        # Find the FIRST new snippet we haven't processed yet
        next_snippet = None
        next_org_id = None
        for snip in snippets:
            org_id = _get_snippet_org_id(snip)
            if org_id and org_id not in seen_ids:
                next_snippet = snip
                next_org_id = org_id
                break

        if not next_snippet:
            no_new_count += 1
            if no_new_count >= 3:
                logger.info(f"📋 No more new results after {len(companies)} companies")
                break
            _scroll_results(driver)
            time.sleep(2 + random.uniform(0.5, 1.5))
            continue

        no_new_count = 0
        seen_ids.add(next_org_id)

        try:
            company = _click_and_parse_card(driver, next_snippet, next_org_id)
            if company and company.get('name'):
                companies.append(company)
                logger.info(f"  ✅ [{len(companies)}/{max_items}] {company['name']}")
                if on_progress:
                    on_progress(len(seen_ids), len(companies))
            else:
                logger.warning(f"  ⚠️ No data for org {next_org_id}")
        except Exception as e:
            logger.warning(f"  ⚠️ Error parsing {next_org_id}: {e}")

            time.sleep(0.5 + random.uniform(0.3, 1))

        # Scroll to load more results
        if len(companies) < max_items:
            _scroll_results(driver)
            time.sleep(1.5 + random.uniform(0.5, 1.5))

    logger.info(f"🏁 Parsing complete: {len(companies)} companies collected")
    return companies


def _wait_for_search_results(driver, timeout: int = 20) -> bool:
    """Wait for search snippets to appear."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                '.search-business-snippet-view, .search-snippet-view'))
        )
        return True
    except TimeoutException:
        return False


def _check_and_solve_captcha(driver, captcha_solver, max_attempts: int = 3) -> bool:
    """Detect and solve Yandex captcha if present. Returns True if solved."""
    from tasks.yandex_maps import detect_captcha_or_block, handle_yandex_protection

    for attempt in range(1, max_attempts + 1):
        if not detect_captcha_or_block(driver):
            logger.info("✅ No captcha detected")
            return True

        logger.warning(f"🔒 Captcha detected (attempt {attempt}/{max_attempts})")
        try:
            solved = handle_yandex_protection(
                driver, captcha_solver,
                max_kaleidoscope_attempts=3,
                deadline=time.time() + 120,
            )
            if solved:
                logger.info(f"🔓 Captcha solved on attempt {attempt}")
                time.sleep(2 + random.uniform(1, 2))
                # Check if we're still on captcha
                if not detect_captcha_or_block(driver):
                    return True
                logger.warning("⚠️ Still on captcha after solve, retrying...")
            else:
                logger.warning(f"⚠️ Captcha solve failed on attempt {attempt}")
        except Exception as e:
            logger.error(f"❌ Captcha solve error: {e}")

        time.sleep(3 + random.uniform(1, 3))

    logger.error(f"❌ Failed to solve captcha after {max_attempts} attempts")
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


def _get_snippet_org_id(snippet) -> Optional[str]:
    """Extract org ID from a search snippet element."""
    try:
        links = snippet.find_elements(By.CSS_SELECTOR, "a[href*='/org/']")
        for link in links:
            href = link.get_attribute("href") or ""
            match = re.search(r'/org/[^/]+/(\d+)', href)
            if match:
                return match.group(1)
    except (StaleElementReferenceException, Exception):
        pass
    return None


def _click_and_parse_card(driver, snippet, org_id: str) -> Optional[Dict]:
    """Click on a search snippet, parse the card panel, then go back."""
    # Click the snippet title
    try:
        title_el = snippet.find_element(By.CSS_SELECTOR, ".search-business-snippet-view__title")
    except NoSuchElementException:
        try:
            title_el = snippet.find_element(By.CSS_SELECTOR, "a[href*='/org/']")
        except NoSuchElementException:
            title_el = snippet

    try:
        title_el.click()
    except Exception:
        driver.execute_script("arguments[0].click()", title_el)

    time.sleep(3 + random.uniform(1, 2))

    # Wait for card to appear
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                '.business-card-view, h1.card-title-view__title, '
                'h1.orgpage-header-view__header'))
        )
    except TimeoutException:
        logger.warning(f"Card panel did not appear for org {org_id}")
        driver.back()
        time.sleep(2)
        _wait_for_search_results(driver, 10)
        return None

    time.sleep(0.5)

    # Parse the card
    company = _parse_card_panel(driver, org_id)

    # Go back to search results
    driver.back()
    time.sleep(1.5 + random.uniform(0.5, 1))
    _wait_for_search_results(driver, 10)

    return company


def _parse_card_panel(driver, org_id: str) -> Dict:
    """Parse company data from the side card panel."""
    company = {
        'yandex_maps_id': org_id,
        'yandex_maps_url': driver.current_url,
    }

    # Name
    company['name'] = _extract_text(driver, [
        'h1.card-title-view__title',
        'h1.orgpage-header-view__header',
        '.business-card-view h1',
    ])

    # Category
    category_text = _extract_text(driver, [
        '.business-card-title-view__categories',
        '.card-title-view__subtitle',
    ])
    if category_text:
        # Take first line only (categories can be multiline)
        company['category'] = category_text.split('\n')[0].strip()

    # Address — from the card contacts section
    company['address'] = _extract_text(driver, [
        '.business-contacts-view__address',
        '.orgpage-header-view__address',
    ])

    # Rating
    rating_text = _extract_text(driver, [
        '.business-rating-badge-view__rating-text',
        '[class*="rating-badge-view__rating-text"]',
    ])
    if rating_text:
        try:
            company['rating'] = float(rating_text.replace(',', '.').strip())
        except ValueError:
            pass

    # Reviews count
    reviews_text = _extract_text(driver, [
        '.business-rating-amount-view',
        '.business-card-title-view__header-rating',
    ])
    if reviews_text:
        nums = re.findall(r'\d+', reviews_text.replace('\xa0', '').replace(' ', ''))
        if nums:
            company['reviews_count'] = int(nums[0])

    # Phone — text-based extraction (no tel: links in side panel)
    phones = _extract_phones(driver)
    if phones:
        company['phone'] = phones[0]
        if len(phones) > 1:
            company['phone2'] = phones[1]

    # Website
    company['website'] = _extract_website(driver)

    # Socials
    socials = _extract_socials(driver)
    company.update(socials)

    # Email
    company['email'] = _extract_email(driver)

    # Working hours
    company['working_hours'] = _extract_text(driver, [
        '.business-card-working-status-view__text',
        '.business-working-status-view',
    ])

    # Coordinates
    coords = _extract_coordinates(driver.current_url)
    if coords:
        company['latitude'] = coords[0]
        company['longitude'] = coords[1]

    return company


def _extract_text(driver, selectors: list) -> Optional[str]:
    """Try CSS selectors and return first matching text."""
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            text = el.text.strip()
            if text:
                return text
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return None


def _extract_phones(driver) -> list:
    """Extract phone numbers from the card panel."""
    phones = []

    # Try clicking "Показать телефон" expand button
    for sel in ['.card-phones-view__more', '.orgpage-phones-view__more']:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed():
                btn.click()
                time.sleep(1)
                break
        except (NoSuchElementException, Exception):
            continue

    # Get phone text from card-phones-view__number elements
    for sel in [
        '.card-phones-view__phone-number',
        '.orgpage-phones-view__phone-number',
    ]:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                text = el.text.strip()
                # Extract phone number from text like "+7 (495) 127-60-55\nПоказать телефон"
                for line in text.split('\n'):
                    line = line.strip()
                    if re.search(r'[+\d][\d\s\-()]{8,}', line):
                        if line not in phones:
                            phones.append(line)
            if phones:
                break
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    # Also try tel: links (present on full org pages)
    if not phones:
        try:
            tel_links = driver.find_elements(By.CSS_SELECTOR, 'a[href^="tel:"]')
            for el in tel_links:
                phone = (el.get_attribute('href') or '').replace('tel:', '').strip()
                if phone and phone not in phones:
                    phones.append(phone)
        except Exception:
            pass

    return phones


def _extract_website(driver) -> Optional[str]:
    """Extract website URL from the card."""
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


def _extract_socials(driver) -> dict:
    """Extract social media links."""
    result = {}

    # Check dedicated social links section first
    try:
        social_links = driver.find_elements(By.CSS_SELECTOR,
            '.business-contacts-view__social-button a, '
            '.card-social-links-view__link, '
            '.business-contacts-view__social-links a')
        for link in social_links:
            href = (link.get_attribute('href') or '').lower()
            _classify_social(href, link, result)
    except Exception:
        pass

    # Scan all page links
    if len(result) < 2:
        try:
            all_links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
            for link in all_links:
                href = (link.get_attribute('href') or '').lower()
                if any(k in href for k in ['t.me/', 'wa.me/', 'whatsapp', 'vk.com/', 'instagram.com/']):
                    _classify_social(href, link, result)
        except Exception:
            pass

    # Filter out Yandex's own social links
    for key in list(result.keys()):
        val = result[key].lower()
        if 'yandex' in val or 'mapsyandex' in val or 'yandex.maps' in val:
            del result[key]

    return result


def _classify_social(href: str, link, result: dict):
    """Classify a link as a social network."""
    original_href = link.get_attribute('href') or ''
    if 't.me/' in href and 'telegram' not in result:
        result['telegram'] = original_href
    elif ('wa.me/' in href or 'whatsapp' in href) and 'whatsapp' not in result:
        result['whatsapp'] = original_href
    elif 'vk.com/' in href and 'vk' not in result:
        result['vk'] = original_href
    elif 'instagram.com/' in href and 'instagram' not in result:
        result['instagram'] = original_href


def _extract_email(driver) -> Optional[str]:
    """Extract email from the card (mailto links + text-based search)."""
    # 1. mailto: links
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, 'a[href^="mailto:"]')
        for el in elements:
            email = (el.get_attribute('href') or '').replace('mailto:', '').strip()
            if email and '@' in email and _is_valid_company_email(email):
                return email
    except Exception:
        pass

    # 2. Text in contact blocks that looks like email
    try:
        contact_selectors = [
            '.business-contacts-view',
            '.orgpage-contacts-view',
            '.card-feature-view',
        ]
        for sel in contact_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in elements:
                text = el.text or ''
                found = _find_email_in_text(text)
                if found:
                    return found
    except Exception:
        pass

    return None


# Email patterns to exclude (Yandex service emails, single-char local parts)
_EMAIL_BLACKLIST = {'support@maps.yandex.ru', 'webmaps-revolution@yandex-team.ru',
                    'm-maps@support.yandex.ru'}
_EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]{2,}@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}')


def _is_valid_company_email(email: str) -> bool:
    """Check if email looks like a real company email (not Yandex service)."""
    email_lower = email.lower().strip()
    if email_lower in _EMAIL_BLACKLIST:
        return False
    if any(x in email_lower for x in ['yandex-team', 'maps.yandex', 'support.yandex']):
        return False
    local = email_lower.split('@')[0]
    if len(local) < 2:
        return False
    return True


def _find_email_in_text(text: str) -> Optional[str]:
    """Find a valid company email in a block of text."""
    matches = _EMAIL_REGEX.findall(text)
    for m in matches:
        if _is_valid_company_email(m):
            return m
    return None


def _extract_coordinates(url: str) -> Optional[tuple]:
    """Extract lat/lng from URL."""
    for pattern in [r'll=([-\d.]+)%2C([-\d.]+)', r'll=([-\d.]+),([-\d.]+)']:
        match = re.search(pattern, url)
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


# ---------------------------------------------------------------------------
# Website email extraction — fast HTTP-based (no browser needed)
# ---------------------------------------------------------------------------

def extract_emails_from_websites(driver, companies: List[Dict],
                                  on_progress=None) -> List[Dict]:
    """
    For companies that have a website but no email, fetch the website via HTTP
    and extract email from the HTML. Uses requests (fast) instead of Selenium.
    Returns the updated list of companies.
    """
    need_email = [c for c in companies if c.get('website') and not c.get('email')]
    if not need_email:
        logger.info("📧 All companies already have email or no website — skipping")
        return companies

    logger.info(f"📧 Extracting emails from {len(need_email)} company websites...")
    found_count = 0

    for i, company in enumerate(need_email):
        website = company['website']
        name = company.get('name', '?')

        try:
            email = _extract_email_from_website_http(website)
            if email:
                company['email'] = email
                found_count += 1
                logger.info(f"  📧 [{i+1}/{len(need_email)}] {name}: {email}")
        except Exception as e:
            logger.debug(f"  ⚠️ [{i+1}/{len(need_email)}] {name}: {e}")

        if on_progress:
            on_progress(i + 1, found_count)

    logger.info(f"📧 Email extraction complete: found {found_count}/{len(need_email)}")
    return companies


def _extract_email_from_website_http(website_url: str, timeout: int = 8) -> Optional[str]:
    """Fetch company website via HTTP and extract email from HTML."""
    import requests as req
    from urllib.parse import urlparse

    if not website_url.startswith(('http://', 'https://')):
        website_url = 'https://' + website_url

    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.3',
    }

    pages = [website_url, base_url + '/contacts', base_url + '/kontakty']
    seen = set()

    for page_url in pages:
        normalized = page_url.rstrip('/')
        if normalized in seen:
            continue
        seen.add(normalized)

        try:
            resp = req.get(page_url, headers=headers, timeout=timeout,
                          allow_redirects=True, verify=False)
            if resp.status_code != 200:
                continue

            html = resp.text[:200000]  # Limit to 200KB

            # 1. mailto: links in HTML
            mailto_matches = re.findall(r'href=["\']mailto:([^"\'?]+)', html, re.IGNORECASE)
            for m in mailto_matches:
                email = m.strip()
                if _is_valid_company_email(email):
                    return email

            # 2. Email patterns in visible text (strip tags roughly)
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            found = _find_email_in_text(text)
            if found:
                return found

        except req.exceptions.RequestException:
            continue
        except Exception:
            continue

    return None
