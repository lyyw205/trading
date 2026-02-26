"""
Binance 1분봉 수집 스크립트 (Parquet 저장)

Usage:
    python scripts/fetch_klines.py --symbol ETHUSDT --interval 1m --years 5
"""

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from binance.client import Client

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

KLINE_COLUMNS = ["ts_ms", "open", "high", "low", "close", "volume"]

SCHEMA = pa.schema(
    [
        pa.field("ts_ms", pa.int64()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
    ]
)


def parse_klines(raw_klines: list) -> list[dict]:
    """Binance kline 응답을 dict 리스트로 변환."""
    rows = []
    for k in raw_klines:
        rows.append(
            {
                "ts_ms": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return rows


def load_existing(parquet_path: Path) -> tuple[pa.Table | None, int | None]:
    """기존 parquet 파일이 있으면 로드하고 마지막 ts_ms 반환."""
    if not parquet_path.exists():
        return None, None
    table = pq.read_table(parquet_path)
    last_ts = table.column("ts_ms")[-1].as_py()
    print(f"[resume] 기존 파일 발견: {len(table)}행, 마지막 ts_ms={last_ts}")
    return table, last_ts


def generate_month_ranges(start_ms: int, end_ms: int) -> list[tuple[str, str]]:
    """시작~끝 사이를 월 단위 (start_str, end_str) 리스트로 분할."""
    ranges = []
    current = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    while current < end_dt:
        month_start = current
        # 다음 달 1일
        if current.month == 12:
            month_end = current.replace(year=current.year + 1, month=1, day=1)
        else:
            month_end = current.replace(month=current.month + 1, day=1)

        if month_end > end_dt:
            month_end = end_dt

        start_str = month_start.strftime("%d %b, %Y")
        end_str = month_end.strftime("%d %b, %Y")
        ranges.append((start_str, end_str))
        current = month_end

    return ranges


def fetch_klines(symbol: str, interval: str, years: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = DATA_DIR / f"{symbol}_{interval}.parquet"

    client = Client()  # public endpoint, API 키 불필요

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    default_start_ms = now_ms - (years * 365 * 24 * 60 * 60 * 1000)

    existing_table, last_ts = load_existing(parquet_path)
    if last_ts is not None:
        start_ms = last_ts + 1  # 마지막 ts 다음부터
    else:
        start_ms = default_start_ms

    if start_ms >= now_ms:
        print("이미 최신 데이터입니다.")
        return

    month_ranges = generate_month_ranges(start_ms, now_ms)
    total_months = len(month_ranges)
    all_rows: list[dict] = []

    print(f"[fetch] {symbol} {interval} | {total_months}개월 수집 시작")
    print(f"[fetch] 기간: {month_ranges[0][0]} ~ {month_ranges[-1][1]}")

    for i, (m_start, m_end) in enumerate(month_ranges, 1):
        raw = client.get_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=m_start,
            end_str=m_end,
        )
        rows = parse_klines(raw)
        all_rows.extend(rows)
        print(f"  [{i}/{total_months}] {m_start} ~ {m_end} : {len(raw)}건 (누적 {len(all_rows)})")
        time.sleep(0.5)

    if not all_rows:
        print("새로 수집된 데이터가 없습니다.")
        return

    new_table = pa.table(
        {col: [r[col] for r in all_rows] for col in KLINE_COLUMNS},
        schema=SCHEMA,
    )

    # 기존 데이터와 병합
    if existing_table is not None:
        merged = pa.concat_tables([existing_table, new_table])
    else:
        merged = new_table

    # ts_ms 기준 중복 제거 (정렬 후 unique)
    df_indices = pa.compute.sort_indices(merged, sort_keys=[("ts_ms", "ascending")])
    merged = merged.take(df_indices)
    ts_col = merged.column("ts_ms")
    unique_mask = pa.compute.list_flatten(
        pa.array(
            [[True] + [ts_col[i].as_py() != ts_col[i - 1].as_py() for i in range(1, len(ts_col))]],
            type=pa.list_(pa.bool_()),
        )
    )
    merged = merged.filter(unique_mask)

    pq.write_table(merged, parquet_path, compression="snappy")
    print(f"\n[done] {parquet_path} 저장 완료: {len(merged)}행")


def main():
    parser = argparse.ArgumentParser(description="Binance 1분봉 수집 → Parquet 저장")
    parser.add_argument("--symbol", default="ETHUSDT", help="거래쌍 (default: ETHUSDT)")
    parser.add_argument("--interval", default="1m", help="캔들 간격 (default: 1m)")
    parser.add_argument("--years", type=int, default=5, help="수집 기간 (년, default: 5)")
    args = parser.parse_args()

    fetch_klines(args.symbol, args.interval, args.years)


if __name__ == "__main__":
    main()
