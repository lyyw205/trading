"""
DB monitoring admin endpoint.
Admin-only, always available.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import GlobalConfig
from app.db.session import engine_trading, get_trading_session
from app.dependencies import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)
settings = GlobalConfig()


@router.get("/db-health")
async def db_health(
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_trading_session),
):
    """
    DB monitoring snapshot: connections, pool stats, slow queries, dead tuples.
    """
    # Active connections from pg_stat_activity
    try:
        result = await session.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state IS NOT NULL AND datname = current_database()"
            )
        )
        active_connections = result.scalar_one()
    except Exception as exc:
        logger.warning("Failed to query pg_stat_activity: %s", exc)
        active_connections = None

    # Connection pool stats from SQLAlchemy
    pool = engine_trading.pool
    pool_stats = {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }

    # Slow queries via pg_stat_statements (optional — may not be installed)
    slow_queries_count = None
    try:
        result = await session.execute(
            text(
                "SELECT count(*) FROM pg_stat_statements "
                "WHERE mean_exec_time > 1000"  # >1s average
            )
        )
        slow_queries_count = result.scalar_one()
    except Exception:
        slow_queries_count = None  # extension not installed

    # Dead tuples info from pg_stat_user_tables
    dead_tuples = []
    try:
        result = await session.execute(
            text(
                "SELECT relname, n_dead_tup, n_live_tup "
                "FROM pg_stat_user_tables "
                "ORDER BY n_dead_tup DESC "
                "LIMIT 10"
            )
        )
        dead_tuples = [
            {"table": row[0], "dead_tuples": row[1], "live_tuples": row[2]}
            for row in result.fetchall()
        ]
    except Exception as exc:
        logger.warning("Failed to query pg_stat_user_tables: %s", exc)

    return {
        "active_connections": active_connections,
        "connection_pool": pool_stats,
        "slow_queries_count": slow_queries_count,
        "dead_tuples": dead_tuples,
    }
