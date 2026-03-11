"""공통 헬퍼 함수 (매수/매도 로직에서 공유)."""

from __future__ import annotations

from dataclasses import dataclass


def extract_base_commission_qty(order_data: dict, base_asset: str) -> float:
    """주문 fills에서 base asset 수수료 총합 추출."""
    fills = order_data.get("fills", [])
    return sum(
        float(f.get("commission", 0)) for f in fills if str(f.get("commissionAsset", "")).upper() == base_asset.upper()
    )


def extract_fee_usdt(order_data: dict, quote_asset: str) -> float:
    """주문 fills에서 quote asset 수수료 총합 추출."""
    fills = order_data.get("fills", [])
    return sum(
        float(f.get("commission", 0)) for f in fills if str(f.get("commissionAsset", "")).upper() == quote_asset.upper()
    )


@dataclass(frozen=True)
class ParsedBuyOrder:
    """체결된 매수 주문의 파싱 결과."""

    bought_qty_net: float
    spent_usdt: float
    avg_price: float
    order_id: int
    update_time_ms: int


def parse_filled_buy_order(
    order_data: dict,
    base_asset: str,
    current_price: float,
    now_ms: int,
) -> ParsedBuyOrder:
    """체결된 매수 주문에서 공통 필드를 파싱한다.

    Args:
        order_data: Binance API 주문 응답 dict
        base_asset: 기준 자산 (수수료 계산용)
        current_price: 현재 가격 (avg_price 폴백용)
        now_ms: 현재 시각 ms (updateTime 폴백용)
    """
    bought_qty = float(order_data.get("executedQty", 0))
    spent_usdt = float(order_data.get("cummulativeQuoteQty", 0))
    order_id = int(order_data.get("orderId", 0))
    update_time_ms = int(order_data.get("updateTime", 0)) or now_ms

    base_fee_qty = extract_base_commission_qty(order_data, base_asset)
    bought_qty_net = bought_qty - base_fee_qty
    if bought_qty_net <= 0:
        bought_qty_net = bought_qty

    avg_price = spent_usdt / bought_qty_net if bought_qty_net > 0 else current_price
    return ParsedBuyOrder(bought_qty_net, spent_usdt, avg_price, order_id, update_time_ms)
