# Trading Project 종합 리뷰 리포트

> **프로젝트:** Crypto Multi-Trader (FastAPI + SQLAlchemy async + Binance)
> **분석일:** 2026-03-02
> **분석 범위:** 전체 코드베이스 (app/, backtest/, tests/, scripts/, alembic/, templates, static)
> **파일 수:** Python 134개 (app 89 + tests 23 + scripts 6 + backtest 3 + alembic 13)
> **총 라인 수:** ~12,000+ (app 8,833 + tests 3,008)

---

## 목차

1. [프로젝트 구조 요약](#1-프로젝트-구조-요약)
2. [심각도별 전체 이슈 요약](#2-심각도별-전체-이슈-요약)
3. [CRITICAL 이슈](#3-critical-이슈)
4. [HIGH 이슈](#4-high-이슈)
5. [MEDIUM 이슈](#5-medium-이슈)
6. [LOW / INFO 이슈](#6-low--info-이슈)
7. [잘 설계된 부분](#7-잘-설계된-부분)
8. [권장 수정 우선순위](#8-권장-수정-우선순위)

---

## 1. 프로젝트 구조 요약

```
trading/
├── app/                          # 메인 애플리케이션 (89 files, 8,833 lines)
│   ├── main.py                   # FastAPI 엔트리포인트 + lifespan + 미들웨어
│   ├── config.py                 # pydantic-settings 설정
│   ├── dependencies.py           # DI (rate limiter, auth)
│   ├── api/                      # REST API 라우터 (11 files, 2,096 lines)
│   ├── models/                   # SQLAlchemy ORM 모델 (15 files, 518 lines)
│   ├── schemas/                  # Pydantic 스키마 (7 files, 532 lines)
│   ├── services/                 # 비즈니스 로직 (12 files, 1,700+ lines)
│   ├── strategies/               # 전략 플러그인 시스템 (7 files, 627 lines)
│   │   ├── buys/lot_stacking.py  # 로트 스태킹 매수 전략
│   │   ├── buys/trend.py         # 추세 추종 매수 전략
│   │   └── sells/fixed_tp.py    # 고정 익절 매도 전략
│   ├── exchange/                 # 거래소 클라이언트 (4 files, 638 lines)
│   ├── db/                       # DB 레포지토리 (8 files, 560+ lines)
│   ├── middleware/               # CSRF, RequestId
│   ├── utils/                    # 암호화, 로깅, 메트릭스
│   └── dashboard/                # Jinja2 SSR 대시보드 (13 templates)
├── backtest/                     # 백테스트 엔진 (3 files, 398 lines)
├── alembic/versions/             # DB 마이그레이션 (12 files)
├── tests/                        # 테스트 (23 files, 3,008 lines)
├── scripts/                      # 유틸리티 스크립트 (6 files)
└── docker/                       # Dockerfile + docker-compose.yml
```

**핵심 아키텍처 패턴:**
- Strategy Plugin Registry (`@BuyLogicRegistry.register`)
- ExchangeClient ABC (Live / Backtest 구현 교체)
- Combo 기반 매매 (buy logic + sell logic 쌍)
- Circuit Breaker (5회 연속 실패 시 계정 비활성화)
- Buy Pause State Machine (ACTIVE → THROTTLED → PAUSED)
- WebSocket + REST Fallback (Binance 1m Kline)

---

## 2. 심각도별 전체 이슈 요약

| 심각도 | 코드 품질 | 보안 | 성능 | 합계 |
|--------|----------|------|------|------|
| **CRITICAL** | 2 | 3 | 2 | **7** |
| **HIGH** | 6 | 4 | 6 | **16** |
| **MEDIUM** | 10 | 5 | 5 | **20** |
| **LOW** | 6 | 3 | 1 | **10** |
| **합계** | 24 | 15 | 14 | **53** |

---

## 3. CRITICAL 이슈

> 즉시 수정 필요. 자금 손실, 데이터 유출, 또는 시스템 장애를 유발할 수 있음.

---

### SEC-C1. .env 파일에 프로덕션 시크릿 평문 노출

| 항목 | 내용 |
|------|------|
| **관점** | 보안 |
| **카테고리** | OWASP A02 - Cryptographic Failures |
| **위치** | `.env:1-13` |
| **영향** | DB 전체 접근 + 모든 Binance API 키 복호화 + 세션 위조 = **전체 거래 자금 탈취** |

**문제:** `.env`에 Supabase DB 비밀번호, Fernet 암호화 키, 세션 서명 키가 평문으로 저장되어 있습니다.
```
DATABASE_URL=postgresql+asyncpg://postgres.bjivqzhvavoqwtllvwzi:superkingyw120@...
ENCRYPTION_KEYS=4U4SaQTAN8_TeNNJsK8l6NmO2MN8SZneOE3buDfBiO0=
SESSION_SECRET_KEY=c5NUsyZ-5biepbHoYgK4muXI1WIpz37SLxyl0Sxj51M
INITIAL_ADMIN_PASSWORD=admin1234
```

**조치:**
1. 모든 키/비밀번호 즉시 로테이션
2. `INITIAL_ADMIN_PASSWORD=admin1234` 제거 + 강력한 비밀번호로 관리자 재생성
3. 시크릿 매니저(AWS Secrets Manager, HashiCorp Vault 등) 도입 검토

---

### SEC-C2. 기본 관리자 비밀번호 `admin1234` 자동 부트스트랩

| 항목 | 내용 |
|------|------|
| **관점** | 보안 |
| **카테고리** | OWASP A07 - Authentication Failures |
| **위치** | `app/main.py:123-135` |
| **영향** | `admin@trading.local` / `admin1234`로 원격 관리자 로그인 가능 → 모든 거래 제어 |

**문제:** 서버 시작 시 `.env`의 `INITIAL_ADMIN_*` 값으로 자동 관리자 생성. 추측 가능한 비밀번호 사용.

**조치:** `.env`에서 해당 환경변수 제거 + `main.py`의 auto-bootstrap 블록 제거 (또는 1회성 스크립트로 전환)

---

### SEC-C3. 세션 쿠키가 DB 상태와 동기화되지 않음

| 항목 | 내용 |
|------|------|
| **관점** | 보안 |
| **카테고리** | OWASP A01 - Broken Access Control |
| **위치** | `app/main.py:254-278` (LazyAuthMiddleware) |
| **영향** | 비활성화/삭제된 사용자가 기존 쿠키(7일 TTL)로 계속 접근. role 변경(admin→user)도 무효화 |

**문제:** `LazyAuthMiddleware`가 쿠키의 `uid`, `email`, `role`을 DB 검증 없이 그대로 신뢰합니다.

**조치:** AuthMiddleware에서 DB lookup 추가 (30-60초 TTL 인메모리 캐시 사용 권장)
```python
# 수정 예시
db_user = await auth_service.get_user_by_id(session_data["uid"])
if not db_user or not db_user.is_active:
    # 강제 로그아웃 처리
```

---

### CODE-C1. Mutable class variable이 전략 인스턴스 간 공유

| 항목 | 내용 |
|------|------|
| **관점** | 코드 품질 |
| **카테고리** | 로직 결함 |
| **위치** | `app/strategies/base.py:47-48` |
| **영향** | in-place 변경 시 다중 계정 간 파라미터 오염 → **실제 자금 손실 가능** |

**문제:** `default_params`와 `tunable_params`가 mutable dict class variable로 선언되어 모든 인스턴스가 동일 객체를 공유합니다.

**조치:**
```python
from copy import deepcopy

class BaseBuyLogic(ABC):
    default_params: dict[str, Any] = {}
    tunable_params: dict[str, dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.default_params = deepcopy(cls.default_params)
        cls.tunable_params = deepcopy(cls.tunable_params)
```

---

### CODE-C2. `BacktestClient`에서 심볼 파싱 하드코딩 `replace("USDT", "")`

| 항목 | 내용 |
|------|------|
| **관점** | 코드 품질 |
| **카테고리** | 로직 결함 |
| **위치** | `app/exchange/backtest_client.py:87, 189, 274` + `backtest/isolated_runner.py:157` |
| **영향** | `SOLUSDC`, `USDTUSDT` 등 비표준 심볼에서 잔고 계산 오류 → 잘못된 PnL |

**문제:** `asset = order["symbol"].replace("USDT", "")` 패턴이 4곳에 하드코딩. `app/utils/symbol_parser.py`가 존재하지만 미사용.

**조치:** `parse_symbol()` 함수 활용으로 통일

---

### PERF-C1. 매 트레이딩 사이클마다 루프 인터벌 DB 재조회

| 항목 | 내용 |
|------|------|
| **관점** | 성능 |
| **카테고리** | 레이턴시 |
| **위치** | `app/services/account_trader.py:425-429` |
| **영향** | 계정 10개 기준 분당 10회 불필요한 DB 왕복 |

**문제:** `_get_loop_interval()`이 매 루프 끝에서 DB 세션을 새로 열어 `trading_accounts`를 조회. `step()` 내에서 이미 account 객체를 조회했음에도 불구.

**조치:** `step()`에서 `account.loop_interval_sec`를 반환하도록 변경

---

### PERF-C2. `_sync_orders_and_fills`에서 최대 50+N개 순차 API 호출

| 항목 | 내용 |
|------|------|
| **관점** | 성능 |
| **카테고리** | 레이턴시 |
| **위치** | `app/services/account_trader.py:303-370` |
| **영향** | 거래 활발 시 `step()` 수 초 소요 → 체결 감지 지연 |

**문제:** Step1(심볼당 get_open_orders) + Step2(미싱 주문 개별 refresh) + Step3(get_my_trades + 내부 순차 get_order) 호출이 모두 직렬화.

**조치:**
1. Step2/3의 개별 `get_order()` 호출을 `asyncio.gather()`로 병렬화
2. Step3의 `get_my_trades(limit=1000)` → `limit=50`으로 축소
3. `recompute_from_fills` 호출을 신규 fill 존재 시에만 실행

---

## 4. HIGH 이슈

> 배포 전 수정 권장. 보안 취약점, 데이터 정합성 문제, 또는 유의미한 성능 저하 유발.

---

### SEC-H1. Brute-force 잠금 임계값 `MAX_FAILED_ATTEMPTS = 20` 과도

| 항목 | 내용 |
|------|------|
| **위치** | `app/services/auth_service.py:16` |
| **조치** | `MAX_FAILED_ATTEMPTS = 5`, `LOCK_DURATION_MINUTES = 30` (또는 지수적 증가) |

---

### SEC-H2. CORS 미들웨어 미적용

| 항목 | 내용 |
|------|------|
| **위치** | `app/main.py` (전체) |
| **영향** | Cross-Origin 요청으로 세션 쿠키 탈취 / 거래 실행 가능 |
| **조치** | `CORSMiddleware` 추가 (`allow_origins`, `allow_credentials=True`) |

---

### SEC-H3. 보안 HTTP 헤더 전체 부재

| 항목 | 내용 |
|------|------|
| **위치** | `app/main.py` |
| **누락 헤더** | `X-Frame-Options`, `X-Content-Type-Options`, `Content-Security-Policy`, `Strict-Transport-Security`, `Referrer-Policy` |
| **조치** | SecurityHeaders 미들웨어 추가 |

---

### SEC-H4. `DEBUG=true` + `ENVIRONMENT=development`가 프로덕션에서 활성화

| 항목 | 내용 |
|------|------|
| **위치** | `.env:18`, `app/db/session.py:17`, `app/main.py:340` |
| **영향** | SQL 쿼리 stdout 출력, `/api/debug/*` 엔드포인트 노출 |
| **조치** | 프로덕션: `DEBUG=false`, `ENVIRONMENT=production` 필수 설정 |

---

### CODE-H1. `_running_tasks` 모듈 전역 변수 — 다중 워커에서 동시실행 제한 무효화

| 항목 | 내용 |
|------|------|
| **위치** | `app/api/backtest.py:35-36` |
| **조치** | DB의 `backtest_runs` 테이블 `status='RUNNING'` 행 수로 동시 실행 판단 |

---

### CODE-H2. `BinanceClient._balance_cache` thread-safety 미보장

| 항목 | 내용 |
|------|------|
| **위치** | `app/exchange/binance_client.py:22-23, 145-158` |
| **조치** | `threading.Lock()`으로 `_sync_get_balance` 보호 |

---

### CODE-H3. `TradingEngine.start()` 순차 jitter sleep → 느린 시작

| 항목 | 내용 |
|------|------|
| **위치** | `app/services/trading_engine.py:55-61` |
| **영향** | 20 계정 기준 마지막 계정 시작까지 23초+ 소요 |
| **조치** | `asyncio.gather()` + 개별 계정 내 jitter로 병렬화 |

---

### CODE-H4. `_sync_orders_and_fills`에서 `except Exception: pass` — 예외 무시

| 항목 | 내용 |
|------|------|
| **위치** | `app/services/account_trader.py:364-365` |
| **영향** | 로트 상태와 거래소 상태 불일치 누적 |
| **조치** | 최소한 `logger.warning()` 추가 |

---

### CODE-H5. non-production에서 빈 시크릿으로 세션 서명 가능

| 항목 | 내용 |
|------|------|
| **위치** | `app/config.py:17-18` |
| **조치** | 개발 환경에서도 `secrets.token_hex(32)` 랜덤 fallback 사용 |

---

### CODE-H6. `AlertService` httpx client 리소스 누수

| 항목 | 내용 |
|------|------|
| **위치** | `app/services/alert_service.py:137-146` |
| **조치** | lifespan에서 `await alert_service.close()` 호출 |

---

### PERF-H1. `recompute_from_fills` — 매 사이클 전체 fills 테이블 집계

| 항목 | 내용 |
|------|------|
| **위치** | `app/db/position_repo.py:63-76`, `account_trader.py:370` |
| **영향** | fills 5,000건 기준 최대 200ms/사이클 |
| **조치** | 신규 fill 존재 시에만 recompute 실행 + `fills` 인덱스 추가 |

---

### PERF-H2. `fills` 테이블 `(account_id, symbol)` 복합 인덱스 누락

| 항목 | 내용 |
|------|------|
| **위치** | `alembic/versions/001_initial_schema.py` |
| **조치** | `CREATE INDEX idx_fills_account_symbol ON fills (account_id, symbol);` |

---

### PERF-H3. `fixed_tp.py` — 매 tick 매 로트마다 개별 `get_order()` API 재호출

| 항목 | 내용 |
|------|------|
| **위치** | `app/strategies/sells/fixed_tp.py:72-93, 100-118` |
| **영향** | 로트 10개 기준 최대 2초 추가 지연 |
| **조치** | `_sync_orders_and_fills`에서 이미 fetch한 데이터를 DB에서 조회하도록 변경 |

---

### PERF-H4. `KlineWsManager` — WS 수신 루프에서 DB 커밋 블로킹

| 항목 | 내용 |
|------|------|
| **위치** | `app/services/kline_ws_manager.py:250-263` |
| **조치** | `asyncio.create_task()`로 DB 저장을 별도 task 분리 |

---

### PERF-H5. 커넥션 풀 `pool_size=30` 과잉 설정

| 항목 | 내용 |
|------|------|
| **위치** | `app/db/session.py:13-18` |
| **영향** | 최대 40개 커넥션 × 10MB = 400MB PostgreSQL 메모리 예약 |
| **조치** | `pool_size=15, max_overflow=10`으로 조정 |

---

### PERF-H6. 백테스트 `_load_candles` — PyArrow→Python 리스트 변환 오버헤드

| 항목 | 내용 |
|------|------|
| **위치** | `backtest/isolated_runner.py:344-349` |
| **영향** | 100만 캔들 기준 최대 1.2초 오버헤드 |
| **조치** | `to_numpy(zero_copy_only=False)` 사용으로 5-10배 가속 |

---

## 5. MEDIUM 이슈

> 계획적 수정 권장. 유지보수성, 확장성, 또는 잠재적 문제 예방.

---

### 보안 (5건)

| ID | 이슈 | 위치 |
|----|------|------|
| SEC-M1 | 세션 쿠키 `DEBUG=true` 시 `secure=False` | `app/api/auth.py:30-38` |
| SEC-M2 | `candle_store.py` f-string 동적 SQL 테이블명 (현재 안전하나 방어적 assert 필요) | `app/services/candle_store.py:132-148` |
| SEC-M3 | CSRF 면제 범위 과도 (`/api/auth/` 전체) | `app/middleware/csrf_middleware.py:14-18` |
| SEC-M4 | `innerHTML` 다수 사용 — XSS 벡터 가능성 | `app/dashboard/static/js/main.js` |
| SEC-M5 | `bcrypt` 3.x deprecated 버전 사용 | `requirements.txt` |

### 코드 품질 (10건)

| ID | 이슈 | 위치 |
|----|------|------|
| CODE-M1 | `KlineWsManager._rebuild_multiplex()` — lock 내 긴 await | `kline_ws_manager.py:66-72` |
| CODE-M2 | `BacktestClient` 주문 dict 직접 수정 — shallow copy 위험 | `backtest_client.py:79-86` |
| CODE-M3 | `AccountTrader.step()` 175줄 단일 트랜잭션 — pool 장기 점유 | `account_trader.py:123-298` |
| CODE-M4 | `pydantic_settings` deprecated `class Config` 사용 | `config.py:58-60` |
| CODE-M5 | `TradingEngine._kline_ws._subscriptions` private 멤버 직접 접근 | `trading_engine.py:184` |
| CODE-M6 | `fixed_tp.py` 매도 수량에 수수료 차감 미반영 edge case | `fixed_tp.py:75` |
| CODE-M7 | `IsolatedBacktestRunner._load_candles()` — blocking I/O in async context | `isolated_runner.py:307-351` |
| CODE-M8 | 백테스트 생성 후 동시실행 검사 — orphan PENDING 레코드 | `backtest.py:52-75` |
| CODE-M9 | `CandleAggregator` cutoff 경계 candle 정합성 문서화 미흡 | `candle_store.py:132-155` |
| CODE-M10 | `approve_earnings` user ID 추출 오류 (`"sub"` → `"id"`) | `dashboard.py:145-148` |

### 성능 (5건)

| ID | 이슈 | 위치 |
|----|------|------|
| PERF-M1 | `lots` 인덱스에 `symbol` 컬럼 누락 | `alembic/versions/004` |
| PERF-M2 | `orders` 테이블 정렬 인덱스 누락 | `order_repo.py:49-63` |
| PERF-M3 | `CandleAggregator` 심볼×tier 별도 트랜잭션 (15개) | `candle_aggregator.py:58-96` |
| PERF-M4 | `InMemoryLotRepository.get_open_lots_by_combo` 매 tick 정렬 | `mem_stores.py:187-194` |
| PERF-M5 | `GlobalRateLimiter.acquire()` weight만큼 1씩 루프 | `rate_limiter.py:21-24` |

---

## 6. LOW / INFO 이슈

> 선택적 개선. 코드 품질 향상에 도움.

---

### 코드 품질 (6건)

| ID | 이슈 | 위치 |
|----|------|------|
| CODE-L1 | `BaseBuyLogic`/`BaseSellLogic` 유틸리티 메서드 중복 | `base.py:81-88, 120-127` |
| CODE-L2 | `_PENDING_KEYS` 상수 lot_stacking/trend 두 곳 중복 | `lot_stacking.py:23, trend.py:22` |
| CODE-L3 | pending 주문 처리 로직 대부분 동일 — template method 추출 가능 | `lot_stacking.py:147-216, trend.py:125-176` |
| CODE-L4 | `BacktestClient` BUY/SELL 수수료 비대칭 (base vs quote asset) | `backtest_client.py:89-104` |
| CODE-L5 | `GlobalConfig()` 인스턴스 3곳에서 중복 생성 | `main.py, session.py, alert_service.py` |
| CODE-L6 | `get_trading_session()` commit/rollback 관리 비일관적 | `db/session.py:46-49` |

### 보안 (3건)

| ID | 이슈 | 위치 |
|----|------|------|
| SEC-L1 | Rate limiter IP 기반 — 프록시 뒤에서 우회 가능 | `dependencies.py:10` |
| SEC-L2 | 세션 만료 7일 — 금융 시스템에 과도함 (24h 이하 권장) | `session_manager.py:21` |
| SEC-L3 | `/health` 비인증 접근 시 내부 상태 노출 | `health.py:17-59` |

---

## 7. 잘 설계된 부분

프로젝트에는 다수의 우수한 설계 결정이 포함되어 있습니다:

### 아키텍처
- **Strategy Plugin Registry**: `@BuyLogicRegistry.register` 데코레이터 패턴으로 새 전략 추가 시 코드 변경 최소화. `tunable_params` 메타데이터로 UI 자동 렌더링.
- **ExchangeClient ABC**: Live/Backtest 클라이언트가 동일 인터페이스를 구현하여 전략 코드 수정 없이 전환 가능.
- **Combo 기반 매매**: buy/sell 로직을 독립적으로 조합 가능. JSONB 파라미터로 마이그레이션 없이 새 파라미터 추가.
- **Circuit Breaker + Buy Pause**: 장애 복구와 자금 보호 메커니즘이 체계적. 상태 머신 기반으로 점진적 대응.

### 보안
- **Binance API 키 Fernet 암호화**: 다중 키 지원 + 로테이션 스크립트 (`scripts/rotate_encryption_key.py`)
- **CSRF 미들웨어 적용**: `starlette-csrf` 기반 보호
- **Rate Limiting**: slowapi 기반 API별 rate limit 설정
- **bcrypt 비밀번호 해싱**: timing attack 대응 (`secrets.compare_digest`)
- **출금 기능 없음**: Binance withdraw API 미호출 (긍정적 설계 결정)

### 성능
- **PriceCollector**: WS→캐시→REST 폴백 체인. 복수 계정 동일 심볼 공유 시 중복 API 호출 없음.
- **StrategyStateStore**: PK `(account_id, scope, key)` 순서 적절.
- **InMemoryStateStore (백테스트)**: DB 없이 순수 dict 기반 — 고속 시뮬레이션.
- **BinanceClient 캐시**: 심볼 필터(영구) + 잔고(TTL 5초) 캐시 적용.
- **SQL 레벨 캔들 집계**: Python 행 처리 대신 DB에서 처리.

### 테스트
- **BacktestClient 테스트 (465줄)**: 주문 체결, 잔고 추적, 부분 체결 시나리오 커버
- **Failure Mode 테스트 (432줄)**: 타임아웃, API 오류, 부분 체결 등 장애 주입 테스트
- **Buy Pause 통합 테스트 (260줄)**: 상태 머신 전이 전체 검증

---

## 8. 권장 수정 우선순위

### Phase 1: 즉시 조치 (보안 긴급)

| 순위 | ID | 이슈 | 예상 공수 |
|------|-----|------|----------|
| 1 | SEC-C1 | `.env` 시크릿 로테이션 (DB PW, 암호화 키, 세션 키) | 1h |
| 2 | SEC-C2 | `admin1234` 비밀번호 변경 + auto-bootstrap 제거 | 30m |
| 3 | SEC-H4 | `DEBUG=false`, `ENVIRONMENT=production` 설정 | 10m |

### Phase 2: 보안 강화 (1-2일)

| 순위 | ID | 이슈 | 예상 공수 |
|------|-----|------|----------|
| 4 | SEC-C3 | 세션 쿠키 DB 검증 (LazyAuthMiddleware) | 3h |
| 5 | SEC-H2 | CORSMiddleware 추가 | 30m |
| 6 | SEC-H3 | 보안 HTTP 헤더 미들웨어 | 1h |
| 7 | SEC-H1 | Brute-force 임계값 5회로 하향 | 15m |
| 8 | SEC-L2 | 세션 만료 24h 이하로 단축 | 10m |

### Phase 3: 코드 정합성 (2-3일)

| 순위 | ID | 이슈 | 예상 공수 |
|------|-----|------|----------|
| 9 | CODE-C1 | Mutable class variable → `__init_subclass__` deepcopy | 1h |
| 10 | CODE-C2 | `replace("USDT","")` → `parse_symbol()` 통일 | 1h |
| 11 | CODE-H4 | `except Exception: pass` → warning 로깅 | 15m |
| 12 | CODE-M10 | `approve_earnings` user ID 키 수정 (`"sub"` → `"id"`) | 10m |
| 13 | CODE-H1 | 백테스트 동시실행 제한 → DB 기반 | 2h |
| 14 | CODE-H5 | 빈 시크릿 랜덤 fallback | 30m |

### Phase 4: 성능 최적화 (3-5일)

| 순위 | ID | 이슈 | 예상 개선 |
|------|-----|------|----------|
| 15 | PERF-C1 | `_get_loop_interval` DB 재조회 제거 | 계정당 분당 1회 DB 왕복 제거 |
| 16 | PERF-C2 | `_sync_orders_and_fills` 순차→병렬화 | API 호출 시간 80% 단축 |
| 17 | PERF-H3 | `fixed_tp.py` get_order 재호출 제거 | 로트당 50-200ms 절감 |
| 18 | PERF-H2 | `fills` 복합 인덱스 추가 | 집계 쿼리 10x 향상 |
| 19 | PERF-H4 | WS 루프에서 DB 저장 비동기 분리 | WS 메시지 수신 블로킹 제거 |
| 20 | PERF-H1 | `recompute_from_fills` 조건부 실행 | 사이클당 최대 200ms 절감 |

### Phase 5: 유지보수성 개선 (선택적)

| 순위 | ID | 이슈 |
|------|-----|------|
| 21 | CODE-L1~L3 | 전략 코드 중복 → template method 추출 |
| 22 | CODE-L5 | `GlobalConfig` 싱글턴 패턴 |
| 23 | CODE-M3 | `AccountTrader.step()` 분리 |
| 24 | CODE-M4 | `class Config` → `model_config` 마이그레이션 |

---

## 테스트 커버리지 현황

| 영역 | 테스트 파일 | 라인 수 | 커버리지 |
|------|-----------|---------|---------|
| Exchange (Backtest) | 3 files | 1,201 | 높음 |
| Services | 2 files | 436 | 중간 |
| API | 5 files | 384 | 낮음 |
| Unit | 4 files | 558 | 중간 |
| **Strategies** | **0 files** | **0** | **없음** |
| **Core Trading Loop** | **0 files** | **0** | **없음** |

**주요 커버리지 갭:**
- `app/strategies/buys/` — 매수 전략 테스트 전무
- `app/strategies/sells/` — 매도 전략 테스트 전무
- `app/services/account_trader.py` — 핵심 트레이딩 루프 테스트 전무
- `app/services/trading_engine.py` — 엔진 오케스트레이션 테스트 전무
- `app/api/admin.py` — 관리자 API (672줄) 테스트 전무

---

> **결론:** 전체적으로 건전한 아키텍처(플러그인 시스템, ABC 패턴, 서킷 브레이커)를 갖추고 있으나,
> 보안 시크릿 관리(CRITICAL 3건)와 트레이딩 루프 성능(순차 API 호출 패턴)이
> 프로덕션 운영의 가장 큰 리스크입니다. Phase 1~2(보안 긴급)를 즉시 수행하고,
> Phase 3~4를 1-2주 내에 완료하는 것을 권장합니다.