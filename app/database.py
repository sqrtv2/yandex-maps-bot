"""
Database connection and session management.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import sqlite3
from contextlib import contextmanager
from typing import Generator
import logging

from .config import settings

logger = logging.getLogger(__name__)

# Create engine based on database URL
if settings.database_url.startswith("sqlite"):
    # SQLite specific configuration
    engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        connect_args={
            "check_same_thread": False,
            "timeout": 20
        },
        poolclass=StaticPool,
    )

    # Enable WAL mode for SQLite for better concurrency
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            # Enable WAL mode
            cursor.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            cursor.execute("PRAGMA foreign_keys=ON")
            # Set timeout
            cursor.execute("PRAGMA busy_timeout=20000")
            cursor.close()

else:
    # PostgreSQL or other database configuration
    engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True
    )

# Create session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False
)

# Base class for all models
Base = declarative_base()


def create_tables():
    """Create all database tables."""
    try:
        # Import all models to register them with Base
        from .models import (
            BrowserProfile,
            ProxyServer,
            Task,
            UserSettings,
            ProfileTargetVisit
        )

        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")

        # Initialize default settings
        initialize_default_settings()

    except Exception as e:
        logger.error(f"Error creating database tables: {e}")
        raise


def initialize_default_settings():
    """Initialize default user settings."""
    try:
        from .models.user_settings import UserSettings

        with get_db_session() as db:
            # Check if settings already exist
            existing_settings = db.query(UserSettings).first()
            if existing_settings:
                logger.info("Settings already exist, skipping initialization")
                return

            # Create default settings
            default_settings = UserSettings.get_default_settings()
            for setting_data in default_settings:
                setting = UserSettings(**setting_data)
                db.add(setting)

            db.commit()
            logger.info(f"Initialized {len(default_settings)} default settings")

    except Exception as e:
        logger.error(f"Error initializing default settings: {e}")


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Usage:
        with get_db_session() as db:
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback()
        logger.error(f"Database session error: {e}")
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for FastAPI to get database session.

    Usage in FastAPI routes:
        @app.get("/")
        def read_root(db: Session = Depends(get_db)):
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback()
        logger.error(f"Database dependency error: {e}")
        raise
    finally:
        db.close()


class DatabaseManager:
    """Database manager for advanced operations."""

    def __init__(self):
        self.engine = engine
        self.SessionLocal = SessionLocal

    def create_tables(self):
        """Create all tables."""
        create_tables()

    def drop_tables(self):
        """Drop all tables (use with caution!)."""
        try:
            Base.metadata.drop_all(bind=engine)
            logger.info("All database tables dropped")
        except Exception as e:
            logger.error(f"Error dropping tables: {e}")
            raise

    def reset_database(self):
        """Reset database by dropping and recreating tables."""
        self.drop_tables()
        self.create_tables()
        logger.info("Database reset completed")

    def get_table_info(self) -> dict:
        """Get information about database tables."""
        try:
            with get_db_session() as db:
                inspector = engine.dialect.get_inspectors(engine)
                tables = {}

                for table_name in Base.metadata.tables.keys():
                    table = Base.metadata.tables[table_name]
                    row_count = db.execute(f"SELECT COUNT(*) FROM {table_name}").scalar()

                    tables[table_name] = {
                        'columns': [col.name for col in table.columns],
                        'row_count': row_count
                    }

                return tables

        except Exception as e:
            logger.error(f"Error getting table info: {e}")
            return {}

    def backup_database(self, backup_path: str):
        """Backup database (SQLite only)."""
        if not settings.database_url.startswith("sqlite"):
            raise NotImplementedError("Backup only supported for SQLite databases")

        try:
            import shutil
            db_path = settings.database_url.replace("sqlite:///", "")
            shutil.copy2(db_path, backup_path)
            logger.info(f"Database backed up to {backup_path}")
        except Exception as e:
            logger.error(f"Error backing up database: {e}")
            raise

    def restore_database(self, backup_path: str):
        """Restore database from backup (SQLite only)."""
        if not settings.database_url.startswith("sqlite"):
            raise NotImplementedError("Restore only supported for SQLite databases")

        try:
            import shutil
            db_path = settings.database_url.replace("sqlite:///", "")
            shutil.copy2(backup_path, db_path)
            logger.info(f"Database restored from {backup_path}")
        except Exception as e:
            logger.error(f"Error restoring database: {e}")
            raise


# Global database manager instance
db_manager = DatabaseManager()


# Utility functions for common database operations
def get_setting(key: str, default=None):
    """Get a setting value by key."""
    try:
        from .models.user_settings import UserSettings

        with get_db_session() as db:
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()
            if setting:
                return setting.get_typed_value()
            return default
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
        return default


def set_setting(key: str, value, description: str = None, category: str = None):
    """Set a setting value by key."""
    try:
        from .models.user_settings import UserSettings

        with get_db_session() as db:
            setting = db.query(UserSettings).filter(UserSettings.setting_key == key).first()

            if setting:
                setting.set_typed_value(value)
                if description:
                    setting.description = description
                if category:
                    setting.category = category
            else:
                # Determine type from value
                setting_type = "string"
                if isinstance(value, bool):
                    setting_type = "bool"
                elif isinstance(value, int):
                    setting_type = "int"
                elif isinstance(value, float):
                    setting_type = "float"
                elif isinstance(value, (dict, list)):
                    setting_type = "json"

                setting = UserSettings(
                    setting_key=key,
                    setting_type=setting_type,
                    description=description or f"Setting for {key}",
                    category=category or "general"
                )
                setting.set_typed_value(value)
                db.add(setting)

            db.commit()
            logger.info(f"Setting {key} updated successfully")

    except Exception as e:
        logger.error(f"Error setting {key}: {e}")
        raise