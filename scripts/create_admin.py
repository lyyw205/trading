#!/usr/bin/env python3
"""초기 관리자 계정 생성 스크립트.

Usage:
    python scripts/create_admin.py --email admin@example.com --password <password>
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import TradingSessionLocal
from app.services.auth_service import AuthService


async def main(email: str, password: str) -> None:
    auth_service = AuthService(session_factory=TradingSessionLocal)
    try:
        user = await auth_service.create_user(email=email, password=password, role="admin")
        print(f"Admin user created: {user['email']} (id: {user['id']})")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create initial admin user")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--password", required=True, help="Admin password (min 8 chars)")
    args = parser.parse_args()

    asyncio.run(main(args.email, args.password))
