# Log Reference Guide

실매매 환경에서 발생하는 모든 로그의 상세 분석 문서.

---

## 1. 로그 포맷

### 1.1 일반 로그 (StructuredFormatter)

모든 일반 로그는 JSON 한 줄로 출력된다.

```json
{
  "ts": "2026-03-03 12:00:00,123",
  "level": "INFO",
  "account_id": "a1b2c3d4-...",
  "request_id": "-",
  "cycle_id": "abc123",
  "msg": "로그 메시지",
  "module": "account_trader"
}
```

| 필드 | 설명 | 비고 |
|------|------|------|
| `ts` | 타임스탬프 | Python logging 기본 포맷 |
| `level` | DEBUG / INFO / WARNING / ERROR / CRITICAL | |
| `account_id` | ContextVar, 기본값 `"system"` | `run_forever()` 진입 시 설정, 전체 루프 범위 유효 |
| `request_id` | ContextVar, 기본값 `"-"` | HTTP 요청 시 설정 |
| `cycle_id` | ContextVar, 기본값 `"-"` | 매 트레이딩 사이클마다 갱신 |
| `msg` | 로그 본문 | |
| `module` | Python 모듈명 | |
| `duration_ms` | (선택) 소요 시간 | `extra={"duration_ms": ...}` 사용 시 |
| `exception` | (선택) 스택 트레이스 | `exc_info=True` 사용 시 |

### 1.2 감사 로그 (audit_log)

별도 로거 `"audit"`, StructuredFormatter 미적용. 순수 JSON 문자열.

```json
{
  "event": "combo_created",
  "user_id": "user-uuid",
  "ts": "2026-03-03T12:00:00+00:00",
  "account_id": "acct-uuid",
  "combo_id": "combo-uuid"
}
```

---

## 2. 로그 레벨 통계

| 레벨 | 개수 | 설명 |
|------|------|------|
| CRITICAL | 2 | 서킷 브레이커 발동, 엔진 크래시 |
| ERROR | ~22 | API 실패, 주문 실패, DB 오류 |
| WARNING | ~27 | 동기화 실패, 레이트 리밋, 잔고 부족, 슬로우 쿼리 |
| INFO | ~35 | 라이프사이클 이벤트, 주문 체결, 상태 변경 |
| DEBUG | ~1 | 알림 레이트 리밋 |

---

## 3. 운영 환경 로그 발생 빈도

| 구분 | 주기 | 예시 |
|------|------|------|
| 사이클당 | 60초마다 2~5건 | 가격 조회, 매수/매도 평가 |
| 이벤트 발생 시 | 불규칙 | 주문 체결, 상태 전환 |
| 6시간 | 1~3건 | 캔들 집계 |
| 10분 | 0~1건 | CB 자동 복구 체크 |
| 시작/종료 | 8~10건 | 라이프사이클 메시지 |
| 에러 시만 | 정상 시 0건 | ERROR/CRITICAL 전부 |
| 관리자 액션 | 요청당 | 감사 로그, 재조정, 리어플라이 |

---

## 4. 모듈별 상세 로그

### 4.1 account_trader.py — 계정별 트레이딩 루프

계정당 60초 주기로 돌며 매수/매도 사이클 수행. `account_id`는 StructuredFormatter가 ContextVar에서 자동 주입하므로 `msg`에 포함하지 않음.

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Circuit breaker already tripped | `Circuit breaker already tripped (5 failures), not starting` | 시작 시 CB 이미 활성 | 시작 시 1회 | **Critical** |
| 2 | WARNING | DB connection error (attempt N/3) | `DB connection error (attempt 1/3): conn refused` | `_do_step()` 중 DB 연결 실패 | 재시도 시 최대 3회 | **High** |
| 3 | WARNING | Balance check failed, skipping buy | `Balance check failed: timeout, skipping buy evaluation` | `get_free_balance()` API 실패 | 실패 사이클마다 | **High** |
| 4 | WARNING | Cannot parse symbol, skipping | `Cannot parse symbol INVALID, skipping` | 심볼 포맷 오류 | 잘못된 심볼당 사이클마다 | **Medium** |
| 5 | WARNING | Price is 0 for symbol, skipping | `Price is 0 for BTCUSDT, skipping` | WS/REST 가격 모두 실패 | 심볼당 사이클마다 | **High** |
| 6 | INFO | Sell detected + balance recovered -> will resume | `Sell detected + balance recovered → will resume` | PAUSED 중 매도 후 잔고 회복 | 이벤트 발생 시 | **High** |
| 7 | WARNING | Open orders sync failed | `Open orders sync failed for BTCUSDT: timeout` | `get_open_orders()` 실패 | 심볼당 사이클마다 | **High** |
| 8 | WARNING | Order sync failed | `Order 12345678 sync failed: timeout` | 개별 주문 조회 실패 | 실패 주문당 | **Medium** |
| 9 | WARNING | Fills sync failed | `Fills sync failed for BTCUSDT: timeout` | `get_my_trades()` API 실패 | 심볼당 사이클마다 | **High** |
| 10 | WARNING | Fill order sync failed | `Fill order 12345678 sync failed: timeout` | 체결 내 미확인 주문 조회 실패 | 건당 | **Medium** |
| 11 | WARNING | Fills processing failed | `Fills processing failed for BTCUSDT: <error>` | 체결 처리 블록 전체 예외 | 심볼당 사이클마다 | **High** |
| 12 | ERROR | _init_client() failed | `_init_client() failed: InvalidAPIKey` | 계정 초기화 실패 (잘못된 API 키 등) | 시작 시 1회 | **Critical** |
| 13 | INFO | Trading loop started | `Trading loop started` | 초기화 성공 후 루프 진입 | 시작 시 1회 | **Medium** |
| 14 | ERROR | step() timed out (180s) | `step() timed out (180s), failures: 3` | 사이클이 180초 초과 | 타임아웃 발생 시 | **Critical** |
| 15 | ERROR | Permanent error, triggering CB | `Permanent error, triggering CB: InvalidAPIKey` | 영구적 오류 (API 키 무효 등) | 드묾 | **Critical** |
| 16 | WARNING | Rate limited, backing off 120s | `Rate limited, backing off 120s: -1003` | 바이낸스 레이트 리밋 | 레이트 리밋 시 | **High** |
| 17 | ERROR | Transient error (Nx) | `Transient error (2x): ConnectionReset` | 일시적 오류 누적 | 실패 사이클마다 | **High** |
| 18 | CRITICAL | Circuit breaker triggered | `Circuit breaker triggered: 5 consecutive failures` | 연속 5회 실패 도달 | CB 발동 시 1회 | **Critical** |
| 19 | WARNING | Failed to send CB alert | `Failed to send CB alert: ConnectTimeout` | CB 발동 후 텔레그램 알림 실패 | 알림 실패 시 | **Medium** |

### 4.2 trading_engine.py — 엔진 관리 (계정 시작/중지/복구)

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | Starting trading engine with N accounts | `Starting trading engine with 3 active accounts` | 엔진 시작 | 앱 시작 시 1회 | **High** |
| 2 | ERROR | Failed to start account | `Failed to start account a1b2: InvalidAPIKey` | 계정 시작 실패 | 시작 시 실패 계정당 1회 | **Critical** |
| 3 | WARNING | Account has active circuit breaker, skipping | `Account a1b2 has active circuit breaker (5 failures), skipping start` | CB 활성 계정 건너뜀 | 시작 시 CB 계정당 1회 | **High** |
| 4 | INFO | Started trader for account | `Started trader for account a1b2` | 계정 트레이더 시작 성공 | 시작 시 1회 | **Medium** |
| 5 | INFO | Stopped trader for account | `Stopped trader for account a1b2` | 계정 중지 | 리로드/종료 시 | **Medium** |
| 6 | INFO | Refreshed subscriptions | `Refreshed subscriptions for a1b2: {'btcusdt'} -> {'btcusdt','ethusdt'}` | 콤보 CRUD로 심볼 변경 | 콤보 변경 시 | **Low** |
| 7 | INFO | Stopping all traders... | `Stopping all traders...` | 전체 종료 | 앱 종료 시 1회 | **Medium** |
| 8 | INFO | Auto-recovering CB-tripped account | `Auto-recovering CB-tripped account a1b2 (attempt 1)` | CB 자동 복구 시도 | 10분 체크, 쿨다운 30분 후 | **High** |
| 9 | ERROR | CB recovery loop error | `CB recovery loop error: DBError` | 복구 루프 예외 | 에러 시 | **High** |

### 4.3 buy_pause_manager.py — 매수 일시정지 상태 머신

`ACTIVE -> THROTTLED -> PAUSED` 상태 전환을 관리. 트레이딩 사이클 내에서 호출되므로 `account_id`는 ContextVar에서 자동 주입.

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | Sell occurred but balance still low, staying PAUSED | `Sell occurred but balance still low, staying PAUSED (count=4)` | PAUSED 중 매도 발생했으나 잔고 부족 | 매도 이벤트 시 | **Medium** |
| 2 | INFO | Buy pause cleared -> ACTIVE | `Buy pause cleared → ACTIVE` | 잔고 회복 | 회복 시 1회 | **High** |
| 3 | WARNING | Buy pause -> STATE (consecutive=N) | `Buy pause → PAUSED (consecutive=3)` | 잔고 부족으로 상태 전환 | 전환 시 1회 | **High** |
| 4 | INFO | Buy pause manually resumed -> ACTIVE | `Buy pause manually resumed → ACTIVE` | 관리자 수동 재개 | 관리자 액션 시 | **Medium** |

### 4.4 kline_ws_manager.py — WebSocket 캔들 수집

바이낸스 kline WebSocket 관리. 심볼별 refcount 기반 구독.

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | AsyncClient created (public streams) | `KlineWsManager: AsyncClient created (public streams)` | 클라이언트 초기화 성공 | 시작 시 1회 | **Medium** |
| 2 | ERROR | Failed to create AsyncClient | `KlineWsManager: Failed to create AsyncClient: timeout` | 클라이언트 생성 실패 | 시작 시 1회 | **Critical** |
| 3 | INFO | stopped | `KlineWsManager: stopped` | 정상 종료 | 종료 시 1회 | **Low** |
| 4 | INFO | subscribed to symbol (new) | `KlineWsManager: subscribed to btcusdt (new)` | 신규 심볼 구독 (refcount 0->1) | 심볼 추가 시 | **Medium** |
| 5 | INFO | unsubscribed from symbol | `KlineWsManager: unsubscribed from btcusdt` | 마지막 참조 제거 (refcount -> 0) | 구독 해제 시 | **Medium** |
| 6 | INFO | no symbols, WS idle | `KlineWsManager: no symbols, WS idle` | 모든 심볼 해제 | 마지막 심볼 제거 시 | **Low** |
| 7 | ERROR | WS fatal error, retrying | `KlineWsManager: WS fatal error: ConnectionClosed, retrying in 10s` | WS 연결 끊김 | 연결 실패 시 (지수 백오프, 최대 300초) | **Critical** |
| 8 | INFO | recreated AsyncClient after failure | `KlineWsManager: recreated AsyncClient after failure` | WS 실패 후 클라이언트 재생성 성공 | 복구 시 | **Medium** |
| 9 | ERROR | failed to recreate client | `KlineWsManager: failed to recreate client: timeout` | 클라이언트 재생성도 실패 | 이중 장애 시 | **Critical** |
| 10 | INFO | backfilled N/M candles for SYMBOL | `KlineWsManager: backfilled 58/60 candles for BTCUSDT` | REST 백필 성공 | WS 재연결마다 심볼당 1회 | **Medium** |
| 11 | WARNING | backfill failed for symbol | `KlineWsManager: backfill failed for btcusdt: timeout` | REST 백필 실패 | 백필 실패 시 | **Medium** |
| 12 | INFO | starting multiplex for N symbols | `KlineWsManager: starting multiplex for 2 symbols: ['BTCUSDT','ETHUSDT']` | 멀티플렉스 WS 연결 시작 | WS (재)연결마다 | **Medium** |
| 13 | ERROR | failed to parse candle | `KlineWsManager: failed to parse candle for BTCUSDT: KeyError` | 캔들 데이터 파싱 실패 | 매우 드묾 | **Medium** |
| 14 | ERROR | failed to store candle | `KlineWsManager: failed to store candle for BTCUSDT: DBError` | 캔들 DB 저장 실패 | DB 오류 시 | **High** |

### 4.5 candle_aggregator.py — 캔들 집계 (1m->5m->1h->1d)

6시간 주기 백그라운드 루프.

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | SYMBOL tier: aggregated=N, deleted=N | `CandleAggregator: BTCUSDT 1m->5m: aggregated=288, deleted=288` | 집계 작업 수행 | 6시간마다 (오래된 캔들 있을 때) | **Low** |
| 2 | ERROR | tier failed | `CandleAggregator: BTCUSDT 1m->5m failed: DBError` | 집계/삭제 예외 | 에러 시 | **Medium** |
| 3 | INFO | background loop started | `CandleAggregator: background loop started (interval: 21600s)` | 루프 시작 | 앱 시작 시 1회 | **Low** |
| 4 | INFO | completed -- summary | `CandleAggregator: completed -- {'BTCUSDT': {'1m->5m': {...}}}` | 집계 완료 (작업 있었음) | 6시간마다 | **Low** |
| 5 | INFO | background loop cancelled | `CandleAggregator: background loop cancelled` | 종료 시 CancelledError | 종료 시 1회 | **Low** |
| 6 | ERROR | unexpected error | `CandleAggregator: unexpected error: DBError` | 예상치 못한 예외 | 에러 시 | **Medium** |
| 7 | INFO | background loop cancelled during sleep | `CandleAggregator: background loop cancelled during sleep` | sleep 중 종료 | 종료 시 1회 | **Low** |

### 4.6 alert_service.py — 텔레그램 알림

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | DEBUG | Alert rate-limited | `Alert rate-limited: Reconciliation drift detected...` | 시간당 한도 초과 (비-CRITICAL) | 알림 과다 시 | **Low** |
| 2 | WARNING | Alert send failed | `Alert send failed: ConnectTimeout` | 텔레그램 API 호출 실패 | API 실패 시 | **Medium** |
| 3 | ERROR | Alert service circuit breaker tripped | `Alert service circuit breaker tripped after 5 failures` | 연속 5회 텔레그램 실패 | CB 발동 시 1회 | **High** |
| 4 | WARNING | Telegram API returned N | `Telegram API returned 429: {"ok":false,...}` | 비-200 응답 | API 에러 시 | **Medium** |
| 5 | INFO | Alert service circuit breaker reset | `Alert service circuit breaker reset` | 수동 CB 리셋 | 관리자 액션 시 | **Low** |

### 4.7 price_collector.py — REST 가격 수집 (WS 백업)

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Price fetch failed for SYMBOL | `Price fetch failed for BTCUSDT: BinanceAPIException(-1003)` | REST 가격 조회 실패 | API 실패 시 심볼당 | **High** |

### 4.8 reconciliation.py — 포지션/체결 재조정

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Reconciliation drift | `Reconciliation drift for a1b2: 1 position diffs, 2 fill gaps` | DB와 거래소 간 불일치 감지 | 재조정 수행 시 | **Critical** |
| 2 | ERROR | Reconciliation error | `Reconciliation error for a1b2: DBError` | 재조정 중 예외 | 에러 시 | **High** |
| 3 | WARNING | Failed to fetch exchange account info | `Failed to fetch exchange account info for a1b2: timeout` | 거래소 잔고 조회 실패 | API 실패 시 | **Medium** |
| 4 | WARNING | Fill gap check failed | `Fill gap check failed for a1b2/BTCUSDT: timeout` | 최근 체결 조회 실패 | 심볼당 API 실패 시 | **Medium** |
| 5 | ERROR | Failed to fetch trades for repair | `Failed to fetch trades from exchange for repair: timeout` | 체결 갭 수리 실패 | 관리자 수리 요청 시 | **High** |
| 6 | INFO | Repaired N fills | `Repaired 15 fills for a1b2/BTCUSDT` | 체결 갭 수리 성공 | 관리자 수리 요청 시 | **Medium** |
| 7 | WARNING | Failed to send drift alert | `Failed to send drift alert: ConnectTimeout` | 불일치 알림 발송 실패 | 알림 실패 시 | **Medium** |

### 4.9 combo_reapply.py — 콤보 설정 재적용 (주문 취소/재생성)

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | cancelled pending buy order | `combo_reapply: cancelled pending buy order 12345 for combo c1d2 symbol BTCUSDT` | 대기 매수 주문 취소 성공 | 리어플라이 시 | **High** |
| 2 | WARNING | cancel pending buy failed | `combo_reapply: cancel pending buy 12345 failed: APIError` | 매수 주문 취소 실패 | API 에러 시 | **High** |
| 3 | INFO | cancelled TP sell order | `combo_reapply: cancelled TP sell order 67890 for lot 42` | TP 매도 주문 취소 성공 | 리어플라이 시 | **High** |
| 4 | WARNING | cancel TP sell failed | `combo_reapply: cancel TP sell 67890 failed: APIError` | TP 매도 주문 취소 실패 | API 에러 시 | **High** |
| 5 | INFO | combo result | `combo_reapply: combo c1d2 result: {'cancelled_buy':1,'cancelled_sell':2,'errors':[]}` | 리어플라이 완료 | 콤보당 1회 | **Medium** |

### 4.10 auth_service.py — 인증

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Account locked (failed N times) | `Account locked: user@example.com (failed 5 times)` | 로그인 5회 실패 → 30분 잠금 | 잠금 시 1회 | **High** |

### 4.11 lot_stacking.py — 랏 스태킹 매수 전략

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | ERROR | failed to fetch pending order | `lot_stacking_buy: failed to fetch pending order 12345: timeout` | 대기 주문 상태 조회 실패 | 사이클마다 | **High** |
| 2 | INFO | pending buy order FILLED | `lot_stacking_buy: pending buy order 12345 FILLED` | 대기 매수 주문 체결 | 체결 시 | **Critical** |
| 3 | INFO | pending buy order STATUS | `lot_stacking_buy: pending buy order 12345 CANCELED` | 주문 취소/거부/만료 | 상태 변경 시 | **Medium** |
| 4 | WARNING | pending buy order timed out | `lot_stacking_buy: pending buy order 12345 timed out, cancelling` | 3시간 미체결 | 타임아웃 시 | **Medium** |
| 5 | ERROR | cancel timed-out order failed | `lot_stacking_buy: cancel timed-out order 12345 failed: APIError` | 타임아웃 주문 취소 실패 | API 에러 시 | **High** |
| 6 | INFO | rebound detected, cancelling | `lot_stacking_buy: rebound detected (cur=97500.00 >= 97300.00), cancelling order 12345` | 가격 반등 → 주문 취소 | 반등 감지 시 | **Medium** |
| 7 | ERROR | cancel rebound order failed | `lot_stacking_buy: cancel rebound order 12345 failed: APIError` | 반등 취소 실패 | API 에러 시 | **High** |
| 8 | INFO | INIT buy filled | `lot_stacking_buy: INIT buy filled qty=0.01050000 cost=100.00 avg=95238.10` | 초기 리저브 매수 체결 | 계정 초기화 시 1회 | **High** |
| 9 | INFO | LOT buy filled | `lot_stacking_buy: LOT buy filled qty_net=0.00105000 avg=95000.00` | 일반 랏 매수 체결 | 체결마다 | **Critical** |
| 10 | INFO | recentering base_price | `lot_stacking_buy: recentering base_price from 95000.00 to EMA 97000.00 (pct=0.0200)` | EMA 상승으로 기준가 재조정 | 가격 상승 추세 시 | **Medium** |
| 11 | INFO | initialized base_price | `lot_stacking_buy: initialized base_price to 97000.00` | 콤보 최초 사이클 | 콤보당 1회 | **Medium** |
| 12 | WARNING | buy_usdt below min_trade_usdt | `lot_stacking_buy: buy_usdt 5.00 below min_trade_usdt 6.00` | 매수 금액이 최소 거래 금액 미만 | 잔고 부족 시 | **Medium** |
| 13 | WARNING | estimated notional below min_notional | `lot_stacking_buy: estimated notional below min_notional` | 바이낸스 최소 주문 금액 위반 | 소액 주문 시도 시 | **Medium** |
| 14 | ERROR | place LOT buy failed | `lot_stacking_buy: place LOT buy failed: BinanceAPIException(-2010)` | 매수 주문 실패 | API 에러 시 | **Critical** |
| 15 | INFO | placed LOT buy order | `lot_stacking_buy: placed LOT buy order 12345 at trigger=95000.00 usdt=100.00` | 매수 주문 성공 | 주문 배치마다 | **Critical** |

### 4.12 trend.py — 트렌드 매수 전략

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | ERROR | failed to fetch pending order | `trend_buy: failed to fetch pending order 12345: timeout` | 대기 주문 조회 실패 | 사이클마다 | **High** |
| 2 | INFO | pending trend buy order FILLED | `trend_buy: pending trend buy order 12345 FILLED` | 트렌드 매수 체결 | 체결 시 | **Critical** |
| 3 | INFO | pending trend buy order STATUS | `trend_buy: pending trend buy order 12345 CANCELED` | 주문 취소/거부/만료 | 상태 변경 시 | **Medium** |
| 4 | WARNING | pending order timed out | `trend_buy: pending trend buy order 12345 timed out, cancelling` | 3시간 미체결 | 타임아웃 시 | **Medium** |
| 5 | ERROR | cancel timed-out order failed | `trend_buy: cancel timed-out order 12345 failed: APIError` | 취소 실패 | API 에러 시 | **High** |
| 6 | INFO | TREND buy filled | `trend_buy: TREND buy filled qty_net=0.00050000 avg=98000.00` | 트렌드 매수 체결 처리 | 체결마다 | **Critical** |
| 7 | WARNING | no reference_combo_id configured | `trend_buy: no reference_combo_id configured, skipping` | 참조 콤보 미설정 | 잘못된 설정 시 매 사이클 | **Medium** |
| 8 | INFO | initialized trend_base_price | `trend_buy: initialized trend_base_price to 98000.00` | 최초 기준가 설정 | 콤보당 1회 | **Low** |
| 9 | INFO | recentering trend_base | `trend_buy: recentering trend_base from 97000.00 to 99000.00` | 상승 추세로 기준가 재조정 | 가격 상승 시 | **Low** |
| 10 | WARNING | buy_usdt below min_trade_usdt | `trend_buy: buy_usdt 5.00 below min_trade_usdt 6.00` | 매수 금액 부족 | 잔고 부족 시 | **Medium** |
| 11 | WARNING | estimated notional below min_notional | `trend_buy: estimated notional below min_notional` | 최소 주문 금액 위반 | 소액 주문 시도 시 | **Medium** |
| 12 | ERROR | place TREND buy failed | `trend_buy: place TREND buy failed: APIError` | 트렌드 매수 주문 실패 | API 에러 시 | **Critical** |
| 13 | INFO | placed TREND buy order | `trend_buy: placed TREND buy order 12345 at trigger=97000.00 usdt=50.00` | 트렌드 매수 주문 성공 | 주문 배치마다 | **Critical** |

### 4.13 fixed_tp.py — 고정 TP(이익실현) 매도 전략

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | lot notional below minimum, skipping TP | `fixed_tp: lot 42 notional 4.50 below minimum, skipping TP` | 매도 금액이 바이낸스 최소 미만 | 소액 랏 매 사이클 | **Medium** |
| 2 | ERROR | failed to get sell order | `fixed_tp: failed to get sell order 67890 for lot 42: APIError` | TP 주문 상태 조회 실패 | API 에러 시 | **High** |
| 3 | INFO | lot TP filled | `fixed_tp: lot 42 TP filled sell=98135.00 profit=3.2500 pending_earnings+=3.2500` | TP 매도 체결 | 체결마다 | **Critical** |
| 4 | INFO | sell order STATUS, clearing | `fixed_tp: sell order 67890 for lot 42 CANCELED, clearing` | TP 주문 취소/거부/만료 | 상태 변경 시 | **Medium** |
| 5 | ERROR | place TP sell failed | `fixed_tp: place TP sell for lot 42 failed: APIError` | TP 매도 주문 실패 | API 에러 시 | **Critical** |
| 6 | INFO | placed TP sell order | `fixed_tp: placed TP sell order 67890 for lot 42 at 98135.00 qty=0.00105000` | TP 매도 주문 성공 | 랏당 1회 (체결 전까지) | **Critical** |

### 4.14 main.py — 애플리케이션 라이프사이클

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | INFO | Starting crypto-multi-trader... | `Starting crypto-multi-trader...` | 앱 시작 | 1회 | **High** |
| 2 | CRITICAL | Trading engine crashed | `Trading engine crashed: RuntimeError(...)` | 엔진 태스크 예외 | 치명적 장애 시 | **Critical** |
| 3 | INFO | Marked N orphan backtests as FAILED | `Marked 2 orphan backtests as FAILED` | 이전 크래시의 고아 백테스트 정리 | 시작 시 (고아 있을 때) | **Low** |
| 4 | WARNING | Failed to clean up orphan backtests | `Failed to clean up orphan backtests: DBError` | 고아 백테스트 정리 실패 | 시작 시 에러 | **Low** |
| 5 | INFO | Shutting down trading engine... | `Shutting down trading engine...` | 종료 시작 | 종료 시 1회 | **Medium** |
| 6 | INFO | Trading engine stopped | `Trading engine stopped` | 정상 종료 완료 | 종료 시 1회 | **Medium** |

### 4.15 session.py — 슬로우 쿼리 감지

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | SLOW_QUERY duration_ms=N query=... | `SLOW_QUERY duration_ms=1250.3 query=SELECT lots.lot_id, lots.account_id...` | 쿼리 시간 > `slow_query_threshold_ms` (기본 200ms) | 느린 쿼리 발생 시 | **High** |

### 4.16 health.py — 헬스체크

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | ERROR | Database health check failed | `Database health check failed: ConnectionRefused` | DB `SELECT 1` 실패 | `/health` 호출 시 | **Critical** |

### 4.17 backtest.py — 백테스트

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | ERROR | Backtest failed | `Backtest a1b2 failed: ValueError(...)` | 백테스트 태스크 예외 | 실패 시 | **Low** |
| 2 | WARNING | Failed to auto-save backtest | `Failed to auto-save backtest a1b2` | JSON 파일 저장 실패 | 최초 리포트 뷰 시 | **Low** |

### 4.18 combos.py — 콤보 API

**일반 로그:**

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | ERROR | combo reapply failed | `combo reapply failed: APIError` | 콤보 업데이트 시 리어플라이 실패 | API 에러 시 | **High** |

**감사 로그:**

| # | 이벤트 | 샘플 JSON | 트리거 |
|---|--------|----------|--------|
| 1 | `combo_created` | `{"event":"combo_created","user_id":"u1","ts":"...","account_id":"a1","combo_id":"c1","combo_name":"Main BTC"}` | POST 콤보 생성 |
| 2 | `combo_updated` | `{"event":"combo_updated","user_id":"u1","ts":"...","account_id":"a1","combo_id":"c1","reapply":true}` | PUT 콤보 수정 |
| 3 | `combo_deleted` | `{"event":"combo_deleted","user_id":"u1","ts":"...","account_id":"a1","combo_id":"c1"}` | DELETE 콤보 삭제 |
| 4 | `combo_enabled` | `{"event":"combo_enabled","user_id":"u1","ts":"...","account_id":"a1","combo_id":"c1"}` | POST 콤보 활성화 |
| 5 | `combo_disabled` | `{"event":"combo_disabled","user_id":"u1","ts":"...","account_id":"a1","combo_id":"c1"}` | POST 콤보 비활성화 |

### 4.19 admin.py — 관리자 API

**감사 로그:**

| # | 이벤트 | 샘플 JSON | 트리거 |
|---|--------|----------|--------|
| 1 | `admin_role_changed` | `{"event":"admin_role_changed","user_id":"admin1","ts":"...","target_user":"u2","new_role":"admin"}` | PUT 역할 변경 |
| 2 | `admin_user_created` | `{"event":"admin_user_created","user_id":"admin1","ts":"...","target_email":"new@test.com","target_role":"user"}` | POST 사용자 생성 |
| 3 | `admin_password_reset` | `{"event":"admin_password_reset","user_id":"admin1","ts":"...","target_user":"u2"}` | POST 비밀번호 리셋 |
| 4 | `admin_user_active_changed` | `{"event":"admin_user_active_changed","user_id":"admin1","ts":"...","target_user":"u2","is_active":false}` | PUT 활성 상태 변경 |

**일반 로그:**

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Failed to query pg_stat_activity | `Failed to query pg_stat_activity: PermissionDenied` | 시스템 헬스 조회 실패 | 관리자 API 호출 시 | **Low** |

### 4.20 admin_db.py — DB 헬스 API

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Failed to query pg_stat_activity | `Failed to query pg_stat_activity: PermissionDenied` | pg_stat_activity 조회 실패 | 관리자 API 호출 시 | **Low** |
| 2 | WARNING | Failed to query pg_stat_user_tables | `Failed to query pg_stat_user_tables: PermissionDenied` | pg_stat_user_tables 조회 실패 | 관리자 API 호출 시 | **Low** |

### 4.21 binance_client.py — 바이낸스 API 클라이언트

| # | 레벨 | 메시지 패턴 | 샘플 `msg` | 발생 조건 | 주기 | 중요도 |
|---|------|-----------|-----------|----------|------|--------|
| 1 | WARNING | Binance time sync failed | `Binance time sync failed, using offset=0 (may cause signature errors)` | `get_server_time()` 실패 | 클라이언트 생성 시 | **High** |

---

## 5. 모니터링/알림 우선순위

즉시 대응이 필요한 로그를 중요도 순으로 정리.

### 5.1 CRITICAL — 즉시 대응

| 모듈 | 메시지 | 의미 |
|------|--------|------|
| `account_trader` | Circuit breaker triggered: N consecutive failures | 계정 거래 자동 중단 (5회 연속 실패) |
| `main` | Trading engine crashed | 전체 엔진 크래시, 모든 거래 중단 |

### 5.2 HIGH — 주의 필요

| 모듈 | 메시지 | 의미 |
|------|--------|------|
| `account_trader` | _init_client() failed / Permanent error | 계정 영구 장애 |
| `account_trader` | step() timed out (180s) | 사이클 타임아웃, CB 접근 중 |
| `kline_ws_manager` | WS fatal error / failed to recreate client | 실시간 가격 중단 |
| `health` | Database health check failed | DB 연결 불가 |
| `reconciliation` | Reconciliation drift | 거래소-DB 불일치 |
| `lot_stacking` / `trend` | place buy failed | 매수 주문 실패 |
| `fixed_tp` | place TP sell failed | TP 매도 주문 실패 = 이익 보호 없음 |
| `alert_service` | circuit breaker tripped | 알림 시스템 자체 장애 |

### 5.3 로그 볼륨 가장 높은 상위 3개

1. 전략 틱 로그 (lot_stacking, trend, fixed_tp): 콤보당 사이클마다
2. `account_trader` 동기화 경고 (order/fills sync): API 불안정 시 심볼당 사이클마다
3. `kline_ws_manager` INFO backfill/multiplex: WS 재연결마다
