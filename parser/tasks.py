"""
Celery task for Yandex Maps company data parsing.
"""
import logging
import time
import random
from datetime import datetime

from tasks.celery_app import celery_app, BaseTask
from app.database import get_db_session
from app.models.parsed_company import ParsedCompany
from app.models.parse_task import ParseTask, ParseTaskStatus

logger = logging.getLogger(__name__)


@celery_app.task(base=BaseTask, bind=True, max_retries=2, default_retry_delay=60,
                 soft_time_limit=3600, time_limit=3900,
                 name='tasks.parser.parse_yandex_maps_task')
def parse_yandex_maps_task(self, parse_task_id: int):
    """
    Celery task: open Yandex Maps, search for companies, collect their data.
    """
    from core.browser_manager import BrowserManager
    from parser import parse_yandex_maps_search, build_search_url, extract_emails_from_websites
    
    browser_manager = None
    browser_id = None
    
    with get_db_session() as db:
        task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
        if not task:
            logger.error(f"ParseTask {parse_task_id} not found")
            return {'error': 'task not found'}
        
        task.status = ParseTaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        task.celery_task_id = self.request.id
        task.add_log(f"Запуск парсинга: '{task.search_query}' (регион: {task.region})")
        db.commit()
        
        search_query = task.search_query
        region = task.region
        max_items = task.max_items or 100
        search_url = task.yandex_maps_url
    
    try:
        # Create browser
        browser_manager = BrowserManager()
        
        # Use a random profile for parsing
        profile_data = {
            'name': f'Parser-{parse_task_id}',
            'user_agent': None,
            'viewport_width': 1920,
            'viewport_height': 1080,
            'timezone': 'Europe/Moscow',
            'language': 'ru-RU',
        }
        
        # Get proxy if available
        proxy_data = _get_random_proxy()
        
        browser_id = browser_manager.create_browser_session(profile_data, proxy_data)
        driver = browser_manager.active_browsers.get(browser_id)
        
        if not driver:
            raise Exception("Failed to create browser session")
        
        with get_db_session() as db:
            task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
            task.add_log("✅ Браузер запущен")
            db.commit()
        
        # Build search URL if not provided
        if not search_url:
            search_url = build_search_url(search_query, region)
        
        # Progress callback
        def on_progress(found, parsed):
            try:
                with get_db_session() as db:
                    t = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
                    if t:
                        t.items_found = found
                        t.items_parsed = parsed
                        db.commit()
            except Exception:
                pass
        
        # Run parser
        companies = parse_yandex_maps_search(
            driver, search_url, max_items=max_items, on_progress=on_progress
        )
        
        # Extract emails from company websites (for those without email)
        with get_db_session() as db:
            task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
            task.add_log(f"📧 Извлечение email с сайтов компаний...")
            db.commit()
        
        try:
            companies = extract_emails_from_websites(driver, companies)
        except Exception as e:
            logger.warning(f"⚠️ Email extraction from websites failed: {e}")
        
        # Save to database
        saved_count = 0
        with get_db_session() as db:
            for comp_data in companies:
                # Check for duplicates by yandex_maps_id
                existing = None
                if comp_data.get('yandex_maps_id'):
                    existing = db.query(ParsedCompany).filter(
                        ParsedCompany.yandex_maps_id == comp_data['yandex_maps_id']
                    ).first()
                
                if existing:
                    # Update existing record
                    for key, value in comp_data.items():
                        if value is not None:
                            setattr(existing, key, value)
                    existing.parse_task_id = parse_task_id
                    existing.search_query = search_query
                    existing.region = region
                    existing.updated_at = datetime.utcnow()
                else:
                    # Create new record
                    company = ParsedCompany(
                        parse_task_id=parse_task_id,
                        search_query=search_query,
                        region=region,
                        **{k: v for k, v in comp_data.items() if v is not None}
                    )
                    db.add(company)
                saved_count += 1
            
            db.commit()
            
            # Update task status
            task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
            task.status = ParseTaskStatus.COMPLETED
            task.items_found = len(companies)
            task.items_parsed = len(companies)
            task.items_saved = saved_count
            task.completed_at = datetime.utcnow()
            task.add_log(f"🏁 Завершено: найдено {len(companies)}, сохранено {saved_count}")
            db.commit()
        
        logger.info(f"✅ Parse task {parse_task_id} completed: {saved_count} companies saved")
        return {
            'status': 'completed',
            'items_found': len(companies),
            'items_saved': saved_count,
        }
    
    except Exception as e:
        logger.error(f"❌ Parse task {parse_task_id} failed: {e}")
        
        # Try to retry first
        try:
            with get_db_session() as db:
                task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
                if task:
                    task.error_message = str(e)[:1000]
                    task.add_log(f"❌ Ошибка: {str(e)[:500]}")
                    db.commit()
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            # All retries exhausted — mark as failed
            with get_db_session() as db:
                task = db.query(ParseTask).filter(ParseTask.id == parse_task_id).first()
                if task:
                    task.status = ParseTaskStatus.FAILED
                    task.error_message = str(e)[:1000]
                    task.completed_at = datetime.utcnow()
                    task.add_log(f"❌ Все попытки исчерпаны. Финальная ошибка: {str(e)[:500]}")
                    db.commit()
            return {'status': 'failed', 'error': str(e)}
    
    finally:
        # Cleanup browser
        if browser_manager and browser_id:
            try:
                browser_manager.close_browser_session(browser_id)
            except Exception:
                pass


def _get_random_proxy():
    """Get a random active proxy from the database."""
    try:
        from app.models import ProxyServer
        with get_db_session() as db:
            proxy = db.query(ProxyServer).filter(
                ProxyServer.is_active == True,
                ProxyServer.is_working == True,
            ).order_by(ProxyServer.times_used).first()
            
            if proxy:
                return {
                    'host': proxy.host,
                    'port': proxy.port,
                    'username': proxy.username,
                    'password': proxy.password,
                    'type': proxy.proxy_type or 'http',
                }
    except Exception as e:
        logger.warning(f"Could not get proxy: {e}")
    return None


@celery_app.task(base=BaseTask, bind=True, max_retries=1, default_retry_delay=60,
                 soft_time_limit=1800, time_limit=2100,
                 name='tasks.parser.extract_emails_task')
def extract_emails_task(self, search_query: str = None):
    """
    Celery task: fetch company websites via HTTP and extract emails
    for parsed companies that have a website but no email.
    No browser needed — uses fast HTTP requests.
    """
    from parser import extract_emails_from_websites

    try:
        with get_db_session() as db:
            query = db.query(ParsedCompany).filter(
                ParsedCompany.website.isnot(None),
                ParsedCompany.website != '',
                (ParsedCompany.email.is_(None)) | (ParsedCompany.email == ''),
            )
            if search_query:
                query = query.filter(ParsedCompany.search_query == search_query)

            companies_db = query.all()
            if not companies_db:
                logger.info("📧 No companies need email extraction")
                return {'status': 'completed', 'found': 0, 'total': 0}

            companies = []
            company_ids = []
            for c in companies_db:
                companies.append({'website': c.website, 'name': c.name or ''})
                company_ids.append(c.id)

        logger.info(f"📧 Starting email extraction for {len(companies)} companies")

        # Extract emails via HTTP (no browser needed)
        companies = extract_emails_from_websites(None, companies)

        # Save results
        found_count = 0
        with get_db_session() as db:
            for i, comp in enumerate(companies):
                if comp.get('email'):
                    company_record = db.query(ParsedCompany).filter(
                        ParsedCompany.id == company_ids[i]
                    ).first()
                    if company_record and not company_record.email:
                        company_record.email = comp['email']
                        company_record.updated_at = datetime.utcnow()
                        found_count += 1
            db.commit()

        logger.info(f"📧 Email extraction complete: {found_count}/{len(companies)} emails found")
        return {'status': 'completed', 'found': found_count, 'total': len(companies)}

    except Exception as e:
        logger.error(f"❌ Email extraction failed: {e}")
        return {'status': 'failed', 'error': str(e)}
