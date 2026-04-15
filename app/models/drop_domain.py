"""
Drop Domain model — tracks domains from backorder.ru and their Yandex Maps presence.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
from datetime import datetime

from app.database import Base


class DropDomain(Base):
    """Domain from backorder.ru drop list, checked against Yandex Maps."""

    __tablename__ = "drop_domains"

    id = Column(Integer, primary_key=True, index=True)

    # Domain info (from backorder.ru CSV)
    domain = Column(String(500), nullable=False, index=True)
    hotness = Column(Integer, default=0)           # 1-3 popularity score
    price = Column(Integer, default=0)             # backorder price RUB
    yandex_tic = Column(Integer, default=0)        # Yandex TIC
    links = Column(Integer, default=0)             # backlinks count
    visitors = Column(Integer, default=-1)         # visitors (-1 = unknown)
    domain_age = Column(Integer, default=0)        # years
    delete_date = Column(String(20), nullable=True)  # release date
    registrar = Column(String(200), nullable=True)

    # Yandex Maps check results
    maps_checked = Column(Boolean, default=False)
    maps_found = Column(Boolean, default=False)     # company found on maps?
    maps_company_name = Column(String(500), nullable=True)
    maps_category = Column(String(500), nullable=True)  # e.g. "Ресторан", "Автосервис"
    maps_address = Column(String(500), nullable=True)
    maps_rating = Column(Float, nullable=True)
    maps_reviews = Column(Integer, nullable=True)
    maps_phone = Column(String(200), nullable=True)
    maps_checked_at = Column(DateTime, nullable=True)

    # Geo
    maps_city = Column(String(200), nullable=True)
    maps_region = Column(String(200), nullable=True)

    # Batch info
    batch_date = Column(String(20), nullable=True)  # e.g. "2026-04-14" — drop date
    batch_id = Column(String(50), nullable=True)     # group imports

    # Status
    is_interesting = Column(Boolean, default=False)  # manually marked
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DropDomain(id={self.id}, domain={self.domain}, category={self.maps_category})>"

    def to_dict(self):
        return {
            "id": self.id,
            "domain": self.domain,
            "hotness": self.hotness,
            "price": self.price,
            "yandex_tic": self.yandex_tic,
            "links": self.links,
            "visitors": self.visitors,
            "domain_age": self.domain_age,
            "delete_date": self.delete_date,
            "registrar": self.registrar,
            "maps_checked": self.maps_checked,
            "maps_found": self.maps_found,
            "maps_company_name": self.maps_company_name,
            "maps_category": self.maps_category,
            "maps_address": self.maps_address,
            "maps_rating": self.maps_rating,
            "maps_reviews": self.maps_reviews,
            "maps_phone": self.maps_phone,
            "maps_city": self.maps_city,
            "maps_region": self.maps_region,
            "maps_checked_at": self.maps_checked_at.isoformat() if self.maps_checked_at else None,
            "batch_date": self.batch_date,
            "batch_id": self.batch_id,
            "is_interesting": self.is_interesting,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
