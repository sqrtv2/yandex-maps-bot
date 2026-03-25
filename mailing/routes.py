"""
Web routes for Email Mailing module.
"""
import os
import logging
from datetime import datetime
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.database import get_db
from app.models.mailing import (
    SmtpAccount, MailingCampaign, MailingMessage,
    MailingStatus, MessageStatus
)
from app.models.parsed_company import ParsedCompany

logger = logging.getLogger(__name__)

# Setup templates
templates_path = os.path.join(os.path.dirname(__file__), "..", "web", "templates")
templates = Jinja2Templates(directory=templates_path) if os.path.exists(templates_path) else None

# Create router
router = APIRouter()


# ─── HTML Page ───────────────────────────────────────────

@router.get("/mailing", response_class=HTMLResponse)
async def mailing_page(request: Request):
    """Mailing management page."""
    if not templates:
        return HTMLResponse("<h1>Templates not found</h1>")
    return templates.TemplateResponse("mailing.html", {"request": request})


# ─── SMTP Accounts API ──────────────────────────────────

@router.get("/api/mailing/smtp-accounts")
async def get_smtp_accounts(db: Session = Depends(get_db)):
    """Get all SMTP accounts."""
    accounts = db.query(SmtpAccount).order_by(SmtpAccount.id).all()
    return [a.to_dict() for a in accounts]


@router.post("/api/mailing/smtp-accounts")
async def create_smtp_account(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create or update an SMTP account."""
    email = data.get('email', '').strip()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    # Check if account with this email already exists
    existing = db.query(SmtpAccount).filter(SmtpAccount.email == email).first()
    if existing:
        # Update existing
        existing.name = data.get('name', email.split('@')[0]).strip()
        existing.password = data.get('password', '').strip()
        existing.smtp_server = data.get('smtp_server', 'smtp.yandex.ru').strip()
        existing.smtp_port = int(data.get('smtp_port', 465))
        existing.use_ssl = data.get('use_ssl', True)
        existing.daily_limit = int(data.get('daily_limit', 450))
        existing.is_active = data.get('is_active', True)
        db.commit()
        return existing.to_dict()

    account = SmtpAccount(
        name=data.get('name', email.split('@')[0]).strip(),
        email=email,
        password=data.get('password', '').strip(),
        smtp_server=data.get('smtp_server', 'smtp.yandex.ru').strip(),
        smtp_port=int(data.get('smtp_port', 465)),
        use_ssl=data.get('use_ssl', True),
        daily_limit=int(data.get('daily_limit', 450)),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account.to_dict()


@router.delete("/api/mailing/smtp-accounts/{account_id}")
async def delete_smtp_account(account_id: int, db: Session = Depends(get_db)):
    """Delete an SMTP account."""
    account = db.query(SmtpAccount).filter(SmtpAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(account)
    db.commit()
    return {"status": "deleted"}


@router.post("/api/mailing/smtp-accounts/{account_id}/test")
async def test_smtp_account_route(account_id: int, db: Session = Depends(get_db)):
    """Test SMTP connection for an account."""
    account = db.query(SmtpAccount).filter(SmtpAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    from mailing import test_smtp_account
    success, error = test_smtp_account(account)
    if success:
        account.is_active = True
        account.last_error = None
    else:
        account.last_error = error[:500]
    db.commit()

    return {"success": success, "error": error}


@router.post("/api/mailing/smtp-accounts/bulk")
async def bulk_create_smtp_accounts(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Bulk create SMTP accounts from text (email\\npassword pairs)."""
    text = data.get('text', '').strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
    created = 0
    errors = []

    i = 0
    while i < len(lines):
        email = lines[i].strip()
        if i + 1 < len(lines) and '@' not in lines[i + 1]:
            password = lines[i + 1].strip()
            i += 2
        else:
            errors.append(f"No password for {email}")
            i += 1
            continue

        if '@' not in email:
            errors.append(f"Invalid email: {email}")
            continue

        # Determine SMTP server from email domain
        domain = email.split('@')[1].lower()
        smtp_server = 'smtp.yandex.ru'
        smtp_port = 465
        if 'gmail' in domain:
            smtp_server = 'smtp.gmail.com'
            smtp_port = 587
        elif 'mail.ru' in domain or 'inbox.ru' in domain or 'list.ru' in domain or 'bk.ru' in domain:
            smtp_server = 'smtp.mail.ru'
            smtp_port = 465

        existing = db.query(SmtpAccount).filter(SmtpAccount.email == email).first()
        if existing:
            existing.password = password
            existing.smtp_server = smtp_server
            existing.smtp_port = smtp_port
            created += 1
        else:
            account = SmtpAccount(
                name=email.split('@')[0],
                email=email,
                password=password,
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                use_ssl=(smtp_port == 465),
                daily_limit=450,
            )
            db.add(account)
            created += 1

    db.commit()
    return {"created": created, "errors": errors}


# ─── Campaigns API ───────────────────────────────────────

@router.get("/api/mailing/campaigns")
async def get_campaigns(db: Session = Depends(get_db)):
    """Get all campaigns."""
    campaigns = db.query(MailingCampaign).order_by(desc(MailingCampaign.created_at)).all()
    return [c.to_dict() for c in campaigns]


@router.post("/api/mailing/campaigns")
async def create_campaign(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create a new mailing campaign."""
    name = data.get('name', '').strip()
    subject = data.get('subject', '').strip()
    body_html = data.get('body_html', '').strip()
    if not name or not subject or not body_html:
        raise HTTPException(status_code=400, detail="name, subject and body_html are required")

    campaign = MailingCampaign(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=data.get('body_text', '').strip() or None,
        sender_name=data.get('sender_name', '').strip() or None,
        filter_search_query=data.get('filter_search_query', '').strip() or None,
        filter_region=data.get('filter_region', '').strip() or None,
        filter_category=data.get('filter_category', '').strip() or None,
        filter_has_email=True,
        delay_min=int(data.get('delay_min', 30)),
        delay_max=int(data.get('delay_max', 90)),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Prepare messages — select companies matching filters
    query = db.query(ParsedCompany).filter(
        ParsedCompany.email.isnot(None),
        ParsedCompany.email != ''
    )
    if campaign.filter_search_query:
        query = query.filter(ParsedCompany.search_query.ilike(f"%{campaign.filter_search_query}%"))
    if campaign.filter_region:
        query = query.filter(ParsedCompany.region.ilike(f"%{campaign.filter_region}%"))
    if campaign.filter_category:
        query = query.filter(ParsedCompany.category.ilike(f"%{campaign.filter_category}%"))

    companies = query.all()

    # Deduplicate by email (case-insensitive)
    seen_emails = set()
    messages_count = 0
    for company in companies:
        email_lower = company.email.strip().lower()
        if email_lower in seen_emails:
            continue
        # Skip already emailed in other campaigns
        already_sent = db.query(MailingMessage).filter(
            MailingMessage.recipient_email == email_lower,
            MailingMessage.status == MessageStatus.SENT.value
        ).first()
        if already_sent:
            continue

        seen_emails.add(email_lower)
        msg = MailingMessage(
            campaign_id=campaign.id,
            company_id=company.id,
            recipient_email=email_lower,
            company_name=company.name,
        )
        db.add(msg)
        messages_count += 1

    campaign.total_recipients = messages_count
    db.commit()

    return campaign.to_dict()


@router.post("/api/mailing/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Start a mailing campaign."""
    campaign = db.query(MailingCampaign).filter(
        MailingCampaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status == MailingStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail="Campaign is already running")

    # Check we have SMTP accounts
    active_accounts = db.query(SmtpAccount).filter(SmtpAccount.is_active == True).count()
    if active_accounts == 0:
        raise HTTPException(status_code=400, detail="No active SMTP accounts configured")

    # Check we have pending messages
    pending = db.query(MailingMessage).filter(
        MailingMessage.campaign_id == campaign_id,
        MailingMessage.status == MessageStatus.PENDING.value
    ).count()
    if pending == 0:
        raise HTTPException(status_code=400, detail="No pending messages in this campaign")

    try:
        from mailing.tasks import run_campaign_task
        result = run_campaign_task.delay(campaign_id)
        campaign.celery_task_id = result.id
        campaign.status = MailingStatus.RUNNING.value
        campaign.started_at = campaign.started_at or datetime.utcnow()
        db.commit()
        return campaign.to_dict()
    except Exception as e:
        logger.error(f"Failed to start campaign: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/mailing/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Pause a running campaign."""
    campaign = db.query(MailingCampaign).filter(
        MailingCampaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign.status = MailingStatus.PAUSED.value
    db.commit()
    return campaign.to_dict()


@router.delete("/api/mailing/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Delete a campaign and its messages."""
    campaign = db.query(MailingCampaign).filter(
        MailingCampaign.id == campaign_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    db.query(MailingMessage).filter(
        MailingMessage.campaign_id == campaign_id
    ).delete()
    db.delete(campaign)
    db.commit()
    return {"status": "deleted"}


@router.get("/api/mailing/campaigns/{campaign_id}/messages")
async def get_campaign_messages(
    campaign_id: int,
    status: str = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Get messages for a campaign."""
    query = db.query(MailingMessage).filter(
        MailingMessage.campaign_id == campaign_id
    )
    if status:
        query = query.filter(MailingMessage.status == status)
    total = query.count()
    messages = query.order_by(MailingMessage.id).offset(offset).limit(limit).all()
    return {
        "total": total,
        "messages": [m.to_dict() for m in messages]
    }


# ─── Stats API ───────────────────────────────────────────

@router.get("/api/mailing/stats")
async def get_mailing_stats(db: Session = Depends(get_db)):
    """Get overall mailing statistics."""
    total_accounts = db.query(SmtpAccount).count()
    active_accounts = db.query(SmtpAccount).filter(SmtpAccount.is_active == True).count()
    total_campaigns = db.query(MailingCampaign).count()
    running_campaigns = db.query(MailingCampaign).filter(
        MailingCampaign.status == MailingStatus.RUNNING.value
    ).count()
    total_sent = db.query(func.count(MailingMessage.id)).filter(
        MailingMessage.status == MessageStatus.SENT.value
    ).scalar() or 0
    total_failed = db.query(func.count(MailingMessage.id)).filter(
        MailingMessage.status == MessageStatus.FAILED.value
    ).scalar() or 0
    total_pending = db.query(func.count(MailingMessage.id)).filter(
        MailingMessage.status == MessageStatus.PENDING.value
    ).scalar() or 0

    # Companies with email count
    companies_with_email = db.query(ParsedCompany).filter(
        ParsedCompany.email.isnot(None),
        ParsedCompany.email != ''
    ).count()

    # Daily quota remaining
    daily_remaining = 0
    for acc in db.query(SmtpAccount).filter(SmtpAccount.is_active == True).all():
        daily_remaining += max(0, (acc.daily_limit or 0) - (acc.sent_today or 0))

    return {
        'total_accounts': total_accounts,
        'active_accounts': active_accounts,
        'total_campaigns': total_campaigns,
        'running_campaigns': running_campaigns,
        'total_sent': total_sent,
        'total_failed': total_failed,
        'total_pending': total_pending,
        'companies_with_email': companies_with_email,
        'daily_remaining': daily_remaining,
    }


# ─── Available filters ───────────────────────────────────

@router.get("/api/mailing/filters")
async def get_available_filters(db: Session = Depends(get_db)):
    """Get distinct search queries, regions, and categories for filter dropdowns."""
    queries = db.query(ParsedCompany.search_query).filter(
        ParsedCompany.email.isnot(None),
        ParsedCompany.email != '',
        ParsedCompany.search_query.isnot(None)
    ).distinct().all()

    regions = db.query(ParsedCompany.region).filter(
        ParsedCompany.email.isnot(None),
        ParsedCompany.email != '',
        ParsedCompany.region.isnot(None)
    ).distinct().all()

    categories = db.query(ParsedCompany.category).filter(
        ParsedCompany.email.isnot(None),
        ParsedCompany.email != '',
        ParsedCompany.category.isnot(None)
    ).distinct().all()

    return {
        'search_queries': sorted([q[0] for q in queries if q[0]]),
        'regions': sorted([r[0] for r in regions if r[0]]),
        'categories': sorted([c[0] for c in categories if c[0]]),
    }
