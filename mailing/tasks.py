"""
Celery tasks for email mailing campaigns.
"""
import logging
import random
import time
from datetime import datetime

from tasks.celery_app import celery_app
from app.database import get_db_session
from app.models.mailing import (
    MailingCampaign, MailingMessage, SmtpAccount,
    MailingStatus, MessageStatus
)
from app.models.parsed_company import ParsedCompany
from mailing import get_available_smtp_account, send_email, personalize_text

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=7200,
    time_limit=7500,
    name='tasks.mailing.run_campaign'
)
def run_campaign_task(self, campaign_id: int):
    """Execute a mailing campaign — sends emails with delays and account rotation."""
    logger.info(f"📧 Starting mailing campaign {campaign_id}")

    with get_db_session() as db:
        campaign = db.query(MailingCampaign).filter(
            MailingCampaign.id == campaign_id
        ).first()
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        if campaign.status == MailingStatus.COMPLETED.value:
            logger.info(f"Campaign {campaign_id} already completed")
            return

        campaign.status = MailingStatus.RUNNING.value
        campaign.started_at = campaign.started_at or datetime.utcnow()
        db.commit()

        # Get pending messages for this campaign
        messages = db.query(MailingMessage).filter(
            MailingMessage.campaign_id == campaign_id,
            MailingMessage.status == MessageStatus.PENDING.value
        ).all()

        if not messages:
            campaign.status = MailingStatus.COMPLETED.value
            campaign.completed_at = datetime.utcnow()
            db.commit()
            logger.info(f"Campaign {campaign_id}: no pending messages")
            return

        logger.info(f"Campaign {campaign_id}: {len(messages)} messages to send")

        for msg in messages:
            # Check if campaign was paused
            db.refresh(campaign)
            if campaign.status == MailingStatus.PAUSED.value:
                logger.info(f"Campaign {campaign_id} paused, stopping")
                return

            # Get available SMTP account
            smtp_account = get_available_smtp_account(db)
            if not smtp_account:
                logger.warning(f"No available SMTP accounts — daily limits exhausted")
                campaign.status = MailingStatus.PAUSED.value
                db.commit()
                return

            # Personalize subject and body
            company_name = msg.company_name or ""
            # Fetch company for extra personalization data
            company = db.query(ParsedCompany).filter(
                ParsedCompany.id == msg.company_id
            ).first()
            region = company.region if company else ""
            category = company.category if company else ""

            subject = personalize_text(campaign.subject, company_name, region, category)
            body_html = personalize_text(campaign.body_html, company_name, region, category)
            body_text = personalize_text(campaign.body_text, company_name, region, category) if campaign.body_text else None

            # Send email
            success, error = send_email(
                smtp_account=smtp_account,
                to_email=msg.recipient_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                sender_name=campaign.sender_name,
            )

            if success:
                msg.status = MessageStatus.SENT.value
                msg.sent_at = datetime.utcnow()
                msg.smtp_account_id = smtp_account.id
                campaign.sent_count = (campaign.sent_count or 0) + 1
                smtp_account.sent_today = (smtp_account.sent_today or 0) + 1
            else:
                msg.status = MessageStatus.FAILED.value
                msg.error_message = error[:1000] if error else None
                msg.smtp_account_id = smtp_account.id
                campaign.failed_count = (campaign.failed_count or 0) + 1

                # If auth error — deactivate account
                if "Auth" in (error or "") or "authentication" in (error or "").lower():
                    smtp_account.is_active = False
                    smtp_account.last_error = error[:500]

            db.commit()

            # Random delay between emails
            delay = random.randint(campaign.delay_min or 30, campaign.delay_max or 90)
            time.sleep(delay)

        # All done
        db.refresh(campaign)
        if campaign.status == MailingStatus.RUNNING.value:
            campaign.status = MailingStatus.COMPLETED.value
            campaign.completed_at = datetime.utcnow()
            db.commit()

        logger.info(
            f"✅ Campaign {campaign_id} finished: "
            f"sent={campaign.sent_count}, failed={campaign.failed_count}"
        )


@celery_app.task(
    bind=True,
    name='tasks.mailing.test_smtp_account'
)
def test_smtp_account_task(self, account_id: int):
    """Test SMTP connection for an account."""
    from mailing import test_smtp_account

    with get_db_session() as db:
        account = db.query(SmtpAccount).filter(SmtpAccount.id == account_id).first()
        if not account:
            return {"success": False, "error": "Account not found"}

        success, error = test_smtp_account(account)
        if not success:
            account.last_error = error[:500]
            account.is_active = False
        else:
            account.last_error = None
            account.is_active = True
        db.commit()

        return {"success": success, "error": error}
