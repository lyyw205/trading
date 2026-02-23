#!/usr/bin/env python3
"""
rotate_encryption_key.py
------------------------
Re-encrypt all trading_accounts.api_key_encrypted / api_secret_encrypted
using the first (newest) key in ENCRYPTION_KEYS, decrypting with the full
MultiFernet key chain.

Usage:
    ENCRYPTION_KEYS="new_key,old_key" \
    DATABASE_URL="postgresql+asyncpg://..." \
    python scripts/rotate_encryption_key.py

Or pass --db-url explicitly.

The script:
1. Reads all rows from trading_accounts.
2. Decrypts api_key_encrypted / api_secret_encrypted with the full key list.
3. Re-encrypts with the FIRST key only (newest key).
4. Updates the row in-place + sets encryption_key_version = 1.
5. Reports how many accounts were rotated.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _build_multi_fernet(keys: list[str]) -> MultiFernet:
    fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
    return MultiFernet(fernets)


def _build_primary_fernet(key: str) -> Fernet:
    return Fernet(key.encode() if isinstance(key, str) else key)


async def rotate(db_url: str, enc_keys: list[str], dry_run: bool) -> None:
    if len(enc_keys) < 1:
        logger.error("At least one encryption key is required")
        sys.exit(1)

    multi = _build_multi_fernet(enc_keys)
    primary = _build_primary_fernet(enc_keys[0])

    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT id, api_key_encrypted, api_secret_encrypted, encryption_key_version "
                "FROM trading_accounts ORDER BY created_at"
            )
        )
        rows = result.fetchall()

    logger.info("Found %d trading_accounts", len(rows))

    rotated = 0
    errors = 0

    async with SessionLocal() as session:
        for row in rows:
            account_id = row[0]
            api_key_enc = row[1]
            api_secret_enc = row[2]

            try:
                api_key_plain = multi.decrypt(api_key_enc.encode()).decode()
                api_secret_plain = multi.decrypt(api_secret_enc.encode()).decode()
            except (InvalidToken, Exception) as exc:
                logger.error(
                    "account_id=%s: failed to decrypt – %s", account_id, exc
                )
                errors += 1
                continue

            new_key_enc = primary.encrypt(api_key_plain.encode()).decode()
            new_secret_enc = primary.encrypt(api_secret_plain.encode()).decode()

            if dry_run:
                logger.info(
                    "[DRY RUN] Would rotate account_id=%s", account_id
                )
                rotated += 1
                continue

            await session.execute(
                text(
                    """
                    UPDATE trading_accounts
                    SET api_key_encrypted = :key_enc,
                        api_secret_encrypted = :secret_enc,
                        encryption_key_version = 1,
                        updated_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "key_enc": new_key_enc,
                    "secret_enc": new_secret_enc,
                    "id": account_id,
                },
            )
            rotated += 1

        if not dry_run:
            await session.commit()

    await engine.dispose()

    logger.info(
        "Key rotation complete. rotated=%d  errors=%d  dry_run=%s",
        rotated,
        errors,
        dry_run,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rotate encryption keys for trading_accounts"
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", ""),
        help="PostgreSQL async URL (postgresql+asyncpg://...)",
    )
    parser.add_argument(
        "--encryption-keys",
        default=os.getenv("ENCRYPTION_KEYS", ""),
        help="Comma-separated Fernet keys (newest first)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt/re-encrypt in memory but do not write to DB",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not args.db_url:
        logger.error("--db-url is required (or set DATABASE_URL)")
        sys.exit(1)

    raw_keys = args.encryption_keys or os.getenv("ENCRYPTION_KEYS", "")
    if not raw_keys:
        logger.error("--encryption-keys is required (or set ENCRYPTION_KEYS)")
        sys.exit(1)

    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    asyncio.run(rotate(db_url=args.db_url, enc_keys=keys, dry_run=args.dry_run))
