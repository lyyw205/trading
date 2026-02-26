# HANDOFF

## Current [1772084598]
- **Task**: Phase 5 - 레거시 정리 + 백테스트 combo 전환
- **Completed**:
  - Step 1: 마이그레이션 스크립트 (`scripts/migrate_strategy_to_combo.py`) — strategy_configs → trading_combos 변환, --dry-run 지원
  - Step 2: account_trader.py 레거시 fallback 제거 — `_strategy_instances`, `_get_or_create_strategy`, legacy else 블록 삭제
  - Step 3: 레거시 API 제거 — strategies.py 파일 삭제, tune 엔드포인트 삭제, strategy-access CRUD 삭제, 관련 스키마 정리
  - Step 4: 레거시 모델/레지스트리 정리 — StrategyRegistry, BaseStrategy, lot_stacking.py, trend_buy.py, strategy_account_access.py 삭제
  - Step 5: 백테스트 combo 전환 — BacktestRun에 combos JSONB 추가, isolated_runner.py combo-based 실행, backtest API/스키마 전환, alembic 005 마이그레이션
  - Step 6: JS/UI 정리 — _tuneData, _buildLotFilterTabs, loadBtStrategies, strategy-access UI 삭제, 백테스트 UI combo 기반 전환
  - __init__.py 파일들 (strategies, schemas, api) 깨진 import 수정
  - backtest/runner.py 미사용 레거시 파일 삭제
  - `python3 -c "from app.main import app"` import 검증 통과
- **Next Steps**:
  - `alembic upgrade head` 실행 (005_backtest_combos 적용)
  - `python scripts/migrate_strategy_to_combo.py --dry-run` 으로 마이그레이션 미리보기
  - 실제 마이그레이션 실행 후 combo CRUD, 백테스트 API, admin 페이지 E2E 검증
  - strategy_configs, strategy_states 테이블 향후 DROP 마이그레이션 (데이터 보존 기간 결정 후)
- **Blockers**: None
- **Related Files**:
  - `scripts/migrate_strategy_to_combo.py` - 데이터 마이그레이션 스크립트
  - `alembic/versions/005_backtest_combos.py` - DB 마이그레이션
  - `app/services/account_trader.py` - 레거시 fallback 제거됨
  - `app/strategies/base.py` - BaseStrategy 제거, BaseBuyLogic/BaseSellLogic만 유지
  - `app/strategies/registry.py` - StrategyRegistry 제거, BuyLogicRegistry/SellLogicRegistry만 유지
  - `backtest/isolated_runner.py` - combo 기반 실행으로 전환
  - `app/api/backtest.py` - combo 기반 요청/응답
  - `app/schemas/backtest.py` - BacktestComboConfig 추가
  - `app/dashboard/templates/admin.html` - strategy-access 섹션 제거, 백테스트 combo UI
  - `app/dashboard/static/js/main.js` - 레거시 tune/strategy-access 코드 제거, combo 백테스트 UI

## Past 1 [1772082642]
- **Task**: 매수/매도 로직 분리 및 조합 시스템 구현 (Phase 1~4)
- **Completed**: Phase 1 기반 인프라, Phase 2 로직 분리, Phase 3 실행 엔진 전환, Phase 4 대시보드 UI + combo CRUD API 전체 완료
- **Note**: combo 기반 실행 루프 동작, legacy fallback 유지 상태에서 Phase 5로 진행

## Past 2 [1772085600]
- **Task**: 매수/매도 로직 분리 및 조합 시스템 구현 (Phase 1 인프라 + 인증 전환)
- **Completed**: Phase 1 기반 인프라 대부분(1-1~1-6, 1-8~1-10), 인증 전환 전체 완료
- **Note**: 마이그레이션 003은 인증용, 004는 combo용
