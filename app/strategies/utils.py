"""공통 헬퍼 함수 (매수/매도 로직에서 공유)."""
from __future__ import annotations


def extract_base_commission_qty(order_data: dict, base_asset: str) -> float:
    """주문 fills에서 base asset 수수료 총합 추출."""
    fills = order_data.get("fills", [])
    total = 0.0
    for fill in fills:
        if str(fill.get("commissionAsset", "")).upper() == base_asset.upper():
            total += float(fill.get("commission", 0))
    return total


def extract_fee_usdt(order_data: dict, quote_asset: str) -> float:
    """주문 fills에서 quote asset 수수료 총합 추출."""
    fills = order_data.get("fills", [])
    total = 0.0
    for fill in fills:
        if str(fill.get("commissionAsset", "")).upper() == quote_asset.upper():
            total += float(fill.get("commission", 0))
    return total
