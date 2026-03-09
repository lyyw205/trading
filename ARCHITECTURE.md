# Crypto Multi-Trader 프로젝트 아키텍처

> **FastAPI 기반 멀티 계정 암호화폐 자동매매 봇** (Python 3.12, PostgreSQL, Binance API)



---

## 루트 파일

| 파일 | 기능 | 사용처 |
|---|---|---|
| `pyproject.toml` | 프로젝트 메타/의존성/도구 설정 (pytest, ruff, coverage) | pip install, 빌드, 린트, 테스트 |
| `.env` / `.env.example` | 환경변수 (DB URL, API키, 시크릿 등) | `app/config.py`에서 로드 |
| `.gitignore` | Git 추적 제외 목록 | Git |
| `.pre-commit-config.yaml` | pre-commit 훅 설정 (ruff 등) | 커밋 시 자동 lint |
| `.mcp.json` | MCP 서버 설정 | Claude Code 개발환경 |

---

## `app/` — 메인 애플리케이션

| 파일 | 기능 | 사용처 |
|---|---|---|
| `main.py` | **FastAPI 앱 진입점** — lifespan(엔진 시작/종료), 미들웨어 등록(CSRF, Auth, RequestId, NoCache), 라우터 마운트 | `uvicorn app.main:app` |
| `config.py` | `GlobalConfig` (pydantic-settings) — .env에서 DB URL, 암호화 키, 세션, 레이트리밋, Sentry, Telegram 설정 로드 | 전역에서 `GlobalConfig()` |
| `dependencies.py` | FastAPI 의존성 주입 — `get_db`, `get_current_user`, `require_admin`, `get_owned_account`, `limiter` | 모든 API 라우터에서 `Depends()` |

---

## `app/api/` — REST API 라우터

| 파일 | 기능 | 엔드포인트 |
|---|---|---|
| `auth.py` | 로그인/로그아웃 (세션 쿠키 기반) | `POST /api/auth/login`, `POST /api/auth/logout` |
| `accounts.py` | 계정 CRUD (API키 암호화 저장, 엔진 시작/정지) | `/api/accounts/*` |
| `combos.py` | 트레이딩 콤보 CRUD (매수/매도 전략 조합, 파라미터 튜닝, reapply) | `/api/combos/*` |
| `dashboard.py` | 대시보드 데이터 API (계정 상태, 로트, 포지션 등 JSON 반환) | `/api/dashboard/*` |
| `admin.py` | 관리자 전용 API (전체 계정 목록, 수익 관리, 시스템 상태) | `/api/admin/*` |
| `admin_db.py` | 관리자 DB 직접 조회 (유저/주문/로트 관리) | `/api/admin-db/*` |
| `backtest.py` | 백테스트 실행/조회/삭제 API | `/api/backtest/*` |
| `health.py` | 헬스체크 | `GET /health` |
| `metrics.py` | Prometheus 메트릭 엔드포인트 | `GET /metrics` |
| `debug.py` | 디버그용 API (전략 상태 조회, 수동 리셋 등) | `/api/debug/*` |

---

## `app/models/` — SQLAlchemy ORM 모델

| 파일 | 모델 | 역할 |
|---|---|---|
| `base.py` | `Base` | 모든 모델의 DeclarativeBase |
| `user.py` | `UserProfile` | 사용자 (이메일, 비밀번호 해시, 역할, 잠금 상태) |
| `account.py` | `TradingAccount` | 거래 계정 (암호화된 API키, 심볼, 루프 주기, 서킷 브레이커, buy-pause 상태) |
| `trading_combo.py` | `TradingCombo` | 전략 조합 (매수/매도 로직명, 파라미터, 심볼 목록, 참조 콤보) |
| `strategy_config.py` | `StrategyConfig` | 전략 설정 (레거시, 콤보로 대체됨) |
| `strategy_state.py` | `StrategyState` | 전략 KV 상태 (base_price, pending_order 등) |
| `order.py` | `Order` | 거래소 주문 (바이낸스 동기) |
| `fill.py` | `Fill` | 주문 체결 내역 |
| `lot.py` | `Lot` | 개별 매수 단위 (진입가, 수량, TP 주문, 손익) |
| `position.py` | `Position` | 심볼별 포지션 집계 (총 수량, 평균가) |
| `price_snapshot.py` | `PriceSnapshot` | 가격 스냅샷 (레거시) |
| `price_candle.py` | `PriceCandle1m/5m/1h/1d` | 멀티 타임프레임 캔들 데이터 (1m→5m→1h→1d 집계) |
| `core_btc_history.py` | `CoreBtcHistory` | BTC 기준 히스토리 (참고 데이터) |
| `backtest_run.py` | `BacktestRun` | 백테스트 실행 기록 (설정, 결과, equity curve) |

---

## `app/schemas/` — Pydantic 스키마 (API 요청/응답 직렬화)

| 파일 | 용도 |
|---|---|
| `account.py` | 계정 생성/수정/응답 스키마 |
| `auth.py` | 로그인 요청/응답 |
| `backtest.py` | 백테스트 실행 요청/응답 |
| `dashboard.py` | 대시보드 데이터 응답 |
| `settings.py` | 설정 관련 |
| `strategy.py` | 전략 정보 응답 |
| `trade.py` | 거래 관련 |

---

## `app/services/` — 비즈니스 로직 (핵심 서비스 계층)

| 파일 | 클래스/함수 | 역할 |
|---|---|---|
| `trading_engine.py` | `TradingEngine` | **최상위 엔진** — 전체 계정의 트레이더 태스크 관리, 계정 시작/정지/리로드, KlineWS 구독 관리 |
| `account_trader.py` | `AccountTrader` | **단일 계정 매매 루프** — 콤보별 매수/매도 실행, 서킷 브레이커(5회 실패→비활성화), 지수 백오프, buy-pause 통합 |
| `account_state_manager.py` | `AccountStateManager` | 계정 레벨 공유 상태 (reserve pool, pending_earnings) — 원자적 SQL |
| `price_collector.py` | `PriceCollector` | 심볼별 가격 수집 (WS→캐시→REST 폴백 체인), 중복 조회 방지 |
| `kline_ws_manager.py` | `KlineWsManager` | Binance WebSocket 1m kline 스트림 — refcount 기반 구독, 완료 캔들 DB 저장, supervisor 자동 재연결, REST backfill |
| `candle_aggregator.py` | `CandleAggregator` | 6시간 주기 백그라운드 — 1m→5m(7일), 5m→1h(30일), 1h→1d(90일) 캔들 압축 |
| `candle_store.py` | `store_*`, `aggregate_*`, `get_candles` | 캔들 CRUD 서비스 — upsert, 조회, SQL GROUP BY 집계, 삭제 |
| `buy_pause_manager.py` | `BuyPauseManager` | 잔고 부족 시 매수 일시정지 — ACTIVE→THROTTLED→PAUSED 상태 전이, 동적 루프 주기 |
| `alert_service.py` | `AlertService` | Telegram 알림 (rate limiting, fire-and-forget, 자체 서킷 브레이커) |
| `combo_reapply.py` | `reapply_combo_orders()` | 콤보 파라미터 변경 시 미체결 주문 취소 → 다음 사이클에서 새 파라미터 적용 |
| `auth_service.py` | `AuthService` | 로컬 DB 인증 (bcrypt 해싱, brute-force 보호, 계정 잠금, timing-attack 방지) |
| `session_manager.py` | `SessionManager` | 서버사이드 세션 (itsdangerous 서명 쿠키, 7일 만료) |
| `rate_limiter.py` | `GlobalRateLimiter` | Binance API 호출 레이트 리미팅 (전역 공유) |

---

## `app/strategies/` — 전략 플러그인 시스템

| 파일 | 역할 |
|---|---|
| `base.py` | `BaseBuyLogic`, `BaseSellLogic` — 추상 기본 클래스, `StrategyContext`(불변 컨텍스트), `RepositoryBundle`(리포 묶음) |
| `registry.py` | `BuyLogicRegistry`, `SellLogicRegistry` — `@register` 데코레이터 기반 전략 등록/조회/인스턴스 생성 |
| `state_store.py` | `StrategyStateStore` — 전략별 KV 상태 영속화 (scope: `{combo_id}:{symbol}`) |
| `sizing.py` | 주문 크기 계산 유틸 (최소 수량, 노셔널 체크) |
| `utils.py` | 전략 공용 유틸 함수 |
| `buys/lot_stacking.py` | 분할 매수 전략 — 가격 하락 시 단계적 매수 (bucket sizing) |
| `buys/trend.py` | 추세 추종 매수 — base_price 기준 recenter, 동적 진입 |
| `sells/fixed_tp.py` | 고정 TP(Take Profit) 매도 — 로트별 목표가 도달 시 매도 |

---

## `app/exchange/` — 거래소 클라이언트 추상화

| 파일 | 역할 |
|---|---|
| `base_client.py` | `ExchangeClient` — 추상 인터페이스 (가격조회, 주문, 잔고 등 14개 메서드) |
| `binance_client.py` | `BinanceClient` — 실제 Binance API 호출 구현 (python-binance, asyncio.to_thread) |
| `backtest_client.py` | `BacktestClient` — 인메모리 시뮬레이션 (즉시체결, 잔고 추적, 캔들 가격 주입) |
| `faulty_backtest_client.py` | `FaultyBacktestClient` — 장애 시뮬레이션용 (타임아웃, 에러 주입 등 테스트용) |

---

## `app/db/` — 데이터 접근 계층 (Repository)

| 파일 | 역할 |
|---|---|
| `session.py` | SQLAlchemy 엔진/세션 설정 (asyncpg, 슬로우 쿼리 감지) |
| `repository.py` | 공통 리포지토리 베이스 |
| `account_repo.py` | 계정 CRUD + 서킷 브레이커 업데이트, 활성 계정 조회 |
| `lot_repo.py` | Lot CRUD — 열림/닫힘 로트 관리, 콤보별 조회 |
| `order_repo.py` | 주문 upsert, Fill 삽입, 미체결 주문 조회 |
| `position_repo.py` | Fill 기반 포지션 재계산 (`recompute_from_fills`) |
| `price_repo.py` | 가격 데이터 조회 함수 |
| `strategy_state_repo.py` | StrategyState KV 리포지토리 |

---

/oh-my-claudecode:analyze app/dashboard 폴더의 파일들에 대해 분석해줘. 구조,문제점,개선점을 먼저 분석한 후에 정합성, 보안, 성능, 안정성, 유지보수성 관점과 연관지어서 상세 리뷰해줘. 심각도별로 분류해서 알려줘. 심각도에서는 수정하지 않으면 어떤 상황이 발생할 수 있는지 명시해줘. 또, 다른 파일들과의 연결 관계도 고려해서 수정사항 체크해줘. 이 파일 한정해서 체크하다가는 전체 구조가 꼬일 수 있으니까

## `app/middleware/` — 미들웨어

| 파일 | 역할 |
|---|---|
| `csrf_middleware.py` | CSRF 면제 경로 정의 (`CSRF_EXEMPT_PATHS`) |
| `request_id.py` | `RequestIdMiddleware` — 요청별 고유 ID 부여 (로그 추적용) |

---

## `app/utils/` — 유틸리티

| 파일 | 역할 |
|---|---|
| `encryption.py` | `EncryptionManager` — API키 AES 암호화/복호화, 키 로테이션 지원 |
| `logging.py` | 구조화 로깅 설정, `current_account_id`/`current_cycle_id` ContextVar |
| `metrics.py` | Prometheus 메트릭 정의 (카운터, 히스토그램) |
| `symbol_parser.py` | 심볼 파싱 (`BTCUSDT` → `BTC`, `USDT`) |
| `trade_events.py` | 트레이드 이벤트 유틸 |

---

## `app/dashboard/` — 프론트엔드 (SSR)

| 파일 | 역할 |
|---|---|
| `routes.py` | SSR 페이지 라우터 — 로그인/계정/관리자 페이지 렌더링, 인증 가드 |

### `templates/` — Jinja2 HTML 템플릿

| 파일 | 역할 |
|---|---|
| `base.html` | 공통 레이아웃 (네비게이션, 테마, JS/CSS 포함) |
| `login.html` | 로그인 페이지 |
| `accounts.html` | 계정 목록 (관리자) |
| `account_detail.html` | 개별 계정 상세 (콤보, 로트, 포지션, 차트) |
| `admin_overview.html` | 관리자 대시보드 (시스템 상태, 요약) |
| `admin_lots.html` | 로트 관리 |
| `admin_positions.html` | 포지션 관리 |
| `admin_strategies.html` | 전략 목록/정보 |
| `admin_earnings.html` | 수익 관리 (적립금 approve) |
| `admin_trades.html` | 거래 내역 |
| `admin_system.html` | 시스템 설정/모니터링 |
| `admin_backtest.html` | 백테스트 실행/관리 |
| `backtest_report.html` | 백테스트 결과 보고서 |
| `admin_users.html` | 유저 관리 |
| `admin_accounts.html` | 계정 관리 (관리자) |

### `static/` — 정적 파일

| 파일 | 역할 |
|---|---|
| `css/style.css` | 전역 CSS 스타일 |
| `js/main.js` | 프론트엔드 JS (API 호출, UI 인터랙션) |

---

## `backtest/` — 백테스트 엔진 (독립 모듈)

| 파일 | 역할 |
|---|---|
| `isolated_runner.py` | `IsolatedBacktestRunner` — Parquet 캔들 로드, 인메모리 리플레이(DB 미사용), 결과 저장. 동시 실행 1개 제한 |
| `mem_stores.py` | `InMemoryLotRepository`, `InMemoryOrderRepository`, `InMemoryStateStore`, `InMemoryAccountStateManager` — 순수 dict 기반 인메모리 저장소 |

---

## `alembic/` — DB 마이그레이션

| 파일 | 역할 |
|---|---|
| `env.py` | Alembic 설정 (async 지원) |
| `versions/001_initial_schema.py` | 초기 스키마 (account, order, fill, lot, position, strategy_state 등) |
| `versions/002_backtest_runs.py` | 백테스트 테이블 추가 |
| `versions/003_local_auth.py` | 로컬 인증 (user_profiles 테이블, password_hash 등) |
| `versions/004_trading_combos.py` | 트레이딩 콤보 테이블 |
| `versions/005_backtest_combos.py` | 백테스트 콤보 지원 |
| `versions/006_reserve_pool_redesign.py` | 리저브 풀 재설계 (pending_earnings 컬럼) |
| `versions/007_buy_pause.py` | buy-pause 컬럼 추가 |
| `versions/008_candle_tables.py` | 멀티 타임프레임 캔들 테이블 |
| `versions/009_combo_symbols.py` | 콤보별 멀티 심볼 지원 |
| `versions/010_candle_volume_columns.py` | 캔들에 volume/quote_volume/trade_count 추가 |
| `versions/011_create_remaining_candle_tables.py` | 나머지 캔들 테이블 (1m, 1h, 1d) 생성 |

---

## `scripts/` — 운영 스크립트

| 파일 | 역할 |
|---|---|
| `create_admin.py` | 초기 관리자 계정 생성 |
| `fetch_klines.py` | Binance에서 과거 캔들 데이터 수집 (Parquet 저장) |
| `migrate_from_old.py` | 구 시스템에서 데이터 마이그레이션 |
| `migrate_strategy_to_combo.py` | 레거시 전략 → 콤보 체계 마이그레이션 |
| `preview_ui.py` | UI 프리뷰용 스크립트 |
| `rotate_encryption_key.py` | 암호화 키 로테이션 |

---

## `tests/` — 테스트

| 파일 | 대상 |
|---|---|
| `conftest.py` | 공통 픽스처 |
| `test_api/conftest.py` | API 테스트 공통 픽스처 |
| `test_api/test_auth.py` | 인증 API 테스트 |
| `test_api/test_debug.py` | 디버그 API 테스트 |
| `test_api/test_health.py` | 헬스체크 API 테스트 |
| `test_api/test_metrics.py` | 메트릭 API 테스트 |
| `test_exchange/test_backtest_client.py` | BacktestClient 테스트 |
| `test_exchange/test_failure_modes.py` | 거래소 장애 모드 테스트 |
| `test_exchange/test_faulty_client.py` | FaultyBacktestClient 테스트 |
| `test_services/conftest.py` | 서비스 테스트 공통 픽스처 |
| `test_services/test_alert_service.py` | AlertService 테스트 |
| `test_services/test_buy_pause_integration.py` | BuyPause 통합 테스트 |
| `unit/conftest.py` | 유닛 테스트 공통 픽스처 |
| `unit/test_buy_pause.py` | BuyPause 순수 로직 테스트 |
| `unit/test_mem_stores.py` | 인메모리 저장소 테스트 |
| `unit/test_sizing.py` | 주문 크기 계산 테스트 |

---

## `data/backtests/` — 백테스트 결과 데이터

| 내용 | 역할 |
|---|---|
| `{uuid}.json` | 개별 백테스트 실행 결과 (summary, trade_log, equity_curve) |

---

## `docker/`

| 파일 | 역할 |
|---|---|
| `docker-compose.yml` | PostgreSQL + 앱 컨테이너 구성 |

---

## `docs/`

| 파일 | 역할 |
|---|---|
| `buy-plan-calculator.html` | 매수 계획 시뮬레이터 (독립 HTML 도구) |

---

## `.github/workflows/`

| 파일 | 역할 |
|---|---|
| `test.yml` | CI 워크플로우 (pytest 실행) |

---

## ETC — `__init__.py` 패키지 마커

> 빈 파일 또는 단순 re-export만 수행하는 Python 패키지 초기화 파일들.

| 파일 | 내용 |
|---|---|
| `app/__init__.py` | 빈 파일 |
| `app/api/__init__.py` | 빈 파일 |
| `app/dashboard/__init__.py` | 빈 파일 |
| `app/db/__init__.py` | 빈 파일 |
| `app/exchange/__init__.py` | 빈 파일 |
| `app/middleware/__init__.py` | 빈 파일 |
| `app/models/__init__.py` | 전체 모델 re-export (`Base`, `UserProfile`, `TradingAccount` 등 15개 모델) |
| `app/schemas/__init__.py` | 빈 파일 |
| `app/services/__init__.py` | 빈 파일 |
| `app/strategies/__init__.py` | 전략 기본 클래스 + 레지스트리 import, `buys/`·`sells/` 하위 모듈 import로 `@register` 데코레이터 실행 |
| `app/strategies/buys/__init__.py` | 하위 매수 전략 모듈 import |
| `app/strategies/sells/__init__.py` | 하위 매도 전략 모듈 import |
| `app/utils/__init__.py` | 빈 파일 |
| `backtest/__init__.py` | 빈 파일 |
| `tests/__init__.py` | 빈 파일 |
| `tests/test_api/__init__.py` | 빈 파일 |
| `tests/test_exchange/__init__.py` | 빈 파일 |
| `tests/test_services/__init__.py` | 빈 파일 |
| `tests/test_strategies/__init__.py` | 빈 파일 |
| `tests/test_utils/__init__.py` | 빈 파일 |

---

## 핵심 데이터 흐름

```
[시작] main.py lifespan
  ├─ TradingEngine.start()
  │   ├─ KlineWsManager.start()  ← Binance WebSocket 1m kline
  │   └─ AccountTrader.run_forever() × N개 계정
  │       └─ step() 매 사이클:
  │           ├─ PriceCollector.get_price()  (WS→캐시→REST)
  │           ├─ 콤보별 심볼 순회:
  │           │   ├─ BuyLogic.pre_tick()  (recenter)
  │           │   ├─ SellLogic.tick()     (TP 체결 → pending_earnings)
  │           │   └─ BuyLogic.tick()      (buy-pause 가드 → 주문)
  │           └─ BuyPauseManager.update_state()
  └─ CandleAggregator (6h 주기) ← 1m→5m→1h→1d 압축
```

## 계층 의존 관계

```
[API Layer]        app/api/*          ← FastAPI 라우터, 스키마 검증
     │
[Dependencies]     app/dependencies.py ← DI: 인증, DB 세션, 엔진
     │
[Service Layer]    app/services/*     ← 비즈니스 로직, 트레이딩 엔진
     │
[Strategy Layer]   app/strategies/*   ← 플러그인 매수/매도 전략
     │
[Exchange Layer]   app/exchange/*     ← 거래소 추상화 (Binance / Backtest)
     │
[Repository Layer] app/db/*           ← 데이터 접근, ORM 리포지토리
     │
[Model Layer]      app/models/*       ← SQLAlchemy ORM 모델
     │
[Infrastructure]   PostgreSQL (asyncpg) + Binance API + Telegram API
```
