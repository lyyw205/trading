from sqlalchemy import BigInteger, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class PriceSnapshot(CreatedAtMixin, Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (Index("idx_price_snapshots_symbol_ts", "symbol", "ts_ms", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
