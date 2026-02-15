#!/usr/bin/env python3
"""
Script to import warmup URLs from nagul.txt into database.
"""
import sys
import os
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.database import get_db, engine, Base
from app.models import WarmupUrl


def create_tables():
    """Create all database tables."""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created")


def validate_url(url: str) -> bool:
    """Validate URL format."""
    url = url.strip()
    if not url:
        return False

    try:
        parsed = urlparse(url)
        return bool(parsed.netloc and parsed.scheme in ['http', 'https'])
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def import_urls_from_file(file_path: str):
    """Import URLs from file into database."""
    if not os.path.exists(file_path):
        print(f"❌ File {file_path} not found")
        return

    print(f"📁 Reading URLs from {file_path}...")

    # Read and validate URLs
    valid_urls = []
    invalid_count = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            url = line.strip()

            if not url or url.startswith('#'):  # Skip empty lines and comments
                continue

            if validate_url(url):
                domain = extract_domain(url)
                valid_urls.append({
                    'url': url,
                    'domain': domain
                })
            else:
                invalid_count += 1
                if invalid_count <= 10:  # Show first 10 invalid URLs
                    print(f"⚠️  Invalid URL at line {line_num}: {url}")

    print(f"📊 Validation results:")
    print(f"   ✅ Valid URLs: {len(valid_urls)}")
    print(f"   ❌ Invalid URLs: {invalid_count}")

    if not valid_urls:
        print("❌ No valid URLs found!")
        return

    # Import to database
    print(f"\n💾 Importing {len(valid_urls)} URLs into database...")

    db = next(get_db())
    success_count = 0
    duplicate_count = 0
    error_count = 0

    try:
        # Clear existing URLs
        print("🗑️  Clearing existing warmup URLs...")
        db.query(WarmupUrl).delete()
        db.commit()

        # Batch insert
        batch_size = 1000
        for i in range(0, len(valid_urls), batch_size):
            batch = valid_urls[i:i + batch_size]

            try:
                # Create WarmupUrl objects
                url_objects = [
                    WarmupUrl(
                        url=item['url'],
                        domain=item['domain'],
                        is_active=True,
                        usage_count=0
                    )
                    for item in batch
                ]

                db.bulk_save_objects(url_objects)
                db.commit()
                success_count += len(batch)

                print(f"✅ Imported batch {i//batch_size + 1}/{(len(valid_urls) + batch_size - 1)//batch_size} ({success_count} URLs)")

            except IntegrityError as e:
                db.rollback()
                print(f"⚠️  Integrity error in batch {i//batch_size + 1}: {e}")

                # Try inserting one by one to skip duplicates
                for item in batch:
                    try:
                        url_obj = WarmupUrl(
                            url=item['url'],
                            domain=item['domain'],
                            is_active=True,
                            usage_count=0
                        )
                        db.add(url_obj)
                        db.commit()
                        success_count += 1
                    except IntegrityError:
                        db.rollback()
                        duplicate_count += 1
                    except Exception as e:
                        db.rollback()
                        error_count += 1
                        print(f"❌ Error inserting {item['url']}: {e}")

            except Exception as e:
                db.rollback()
                error_count += len(batch)
                print(f"❌ Error in batch {i//batch_size + 1}: {e}")

    except Exception as e:
        print(f"❌ Database error: {e}")
        db.rollback()
    finally:
        db.close()

    # Final statistics
    total_in_db = get_url_count()

    print(f"\n📈 Import completed!")
    print(f"   ✅ Successfully imported: {success_count}")
    print(f"   🔄 Duplicates skipped: {duplicate_count}")
    print(f"   ❌ Errors: {error_count}")
    print(f"   📊 Total URLs in database: {total_in_db}")


def get_url_count():
    """Get total count of URLs in database."""
    try:
        db = next(get_db())
        count = db.query(WarmupUrl).count()
        db.close()
        return count
    except Exception:
        return 0


def main():
    """Main function."""
    print("🚀 Starting warmup URLs import...")

    # Create tables first
    create_tables()

    # Import URLs
    import_urls_from_file('nagul.txt')

    print("🎉 Import process completed!")


if __name__ == "__main__":
    main()