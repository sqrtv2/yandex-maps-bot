"""
Parsed Company model for storing data collected from Yandex Maps.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Float, Index
from datetime import datetime

from app.database import Base


class ParsedCompany(Base):
    """Company data parsed from Yandex Maps search results."""
    
    __tablename__ = "parsed_companies"

    id = Column(Integer, primary_key=True, index=True)
    
    # Parse task info
    parse_task_id = Column(Integer, nullable=True, index=True)
    search_query = Column(String(500), nullable=True, index=True)
    region = Column(String(200), nullable=True)
    
    # Company info
    name = Column(String(500), nullable=False, index=True)
    category = Column(String(500), nullable=True)
    address = Column(String(1000), nullable=True)
    
    # Contacts
    website = Column(String(2048), nullable=True)
    phone = Column(String(200), nullable=True)
    phone2 = Column(String(200), nullable=True)
    email = Column(String(500), nullable=True)
    telegram = Column(String(2048), nullable=True)
    whatsapp = Column(String(2048), nullable=True)
    vk = Column(String(2048), nullable=True)
    instagram = Column(String(2048), nullable=True)
    
    # Yandex Maps data
    yandex_maps_url = Column(String(2048), nullable=True)
    yandex_maps_id = Column(String(100), nullable=True, index=True)
    rating = Column(Float, nullable=True)
    reviews_count = Column(Integer, nullable=True)
    
    # Working hours
    working_hours = Column(Text, nullable=True)
    
    # Coordinates
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_company_search', 'search_query', 'region'),
        Index('idx_company_name_region', 'name', 'region'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'parse_task_id': self.parse_task_id,
            'search_query': self.search_query,
            'region': self.region,
            'name': self.name,
            'category': self.category,
            'address': self.address,
            'website': self.website,
            'phone': self.phone,
            'phone2': self.phone2,
            'email': self.email,
            'telegram': self.telegram,
            'whatsapp': self.whatsapp,
            'vk': self.vk,
            'instagram': self.instagram,
            'yandex_maps_url': self.yandex_maps_url,
            'yandex_maps_id': self.yandex_maps_id,
            'rating': self.rating,
            'reviews_count': self.reviews_count,
            'working_hours': self.working_hours,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
