"""
Keyword Wordstat frequency cache model.
Stores broad / phrase / exact frequency from Yandex Wordstat API.
"""
from sqlalchemy import Column, Integer, String, DateTime, BigInteger
from datetime import datetime

from app.database import Base


class KeywordFrequency(Base):
    """Cached Wordstat frequency for a keyword."""

    __tablename__ = "keyword_frequencies"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, nullable=False, index=True)
    keyword = Column(String(500), nullable=False)

    # 3 types of frequency
    freq_broad = Column(BigInteger, nullable=True)    # общая (без операторов)
    freq_phrase = Column(BigInteger, nullable=True)    # "фразовая" (в кавычках)
    freq_exact = Column(BigInteger, nullable=True)     # "!точная" (кавычки + !)

    updated_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "keyword": self.keyword,
            "freq_broad": self.freq_broad,
            "freq_phrase": self.freq_phrase,
            "freq_exact": self.freq_exact,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
