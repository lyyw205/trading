# HANDOFF

## Current [1772093483]
- **Task**: Reserve Pool 리디자인 - 수동 승인 기반 적립 시스템
- **Completed**:
  - Architect + Critic consensus planning (ralplan) 완료
  - Step 1: 매도 수익 → `pending_earnings_usdt` 전환, 매수 시 `core_bucket→reserve` 자동 변환 제거
  - Step 2: `AccountStateManager` 확장 (원자적 SQL UPDATE, SELECT FOR UPDATE), 승인 API 2개 추가
  - Step 3: 대시보드 UI — 적립금 카드 + 승인 모달 (슬라이더, 100%/50%/0% 퀵버튼, book-value 고지)
  - Step 4: Alembic 006 마이그레이션 (컬럼 추가 + combo별 core_bucket SUM 합산 이관)
  - `python3 -c "from app.main import app"` import 검증 통과
  - Supabase MCP 설치 (`.mcp.json` 생성)
- **Next Steps**:
  - Claude Code 재시작 후 Supabase MCP 인증
  - `alembic upgrade head` 실행 (005 + 006 마이그레이션 적용)
  - 대시보드에서 적립금 카드 및 승인 모달 E2E 검증
  - strategy_configs, strategy_states 테이블 향후 DROP 마이그레이션 (데이터 보존 기간 결정 후)
  - reserve_qty/reserve_cost_usdt도 정식 컬럼 이전 검토 (strategy_states DROP 연관)
- **Blockers**: None
- **Related Files**:
  - `app/models/account.py` - `pending_earnings_usdt` NUMERIC 컬럼 추가
  - `app/services/account_state_manager.py` - pending_earnings 원자적 SQL + approve_earnings_to_reserve
  - `app/api/dashboard.py` - GET pending_earnings, POST approve_earnings 엔드포인트 추가
  - `app/schemas/dashboard.py` - ApproveEarningsRequest/Response 스키마 추가
  - `app/strategies/sells/fixed_tp.py` - account_state 전달 + pending_earnings 누적 (음수 방어)
  - `app/strategies/buys/lot_stacking.py` - core_bucket→reserve 자동 변환 제거
  - `app/strategies/buys/trend.py` - 동일 변환 제거 + CoreBtcHistory import 제거
  - `app/dashboard/static/js/main.js` - 적립금 카드 + 승인 모달
  - `app/dashboard/static/css/style.css` - earnings 카드/모달 스타일
  - `alembic/versions/006_reserve_pool_redesign.py` - 컬럼 추가 + 데이터 이관
  - `.mcp.json` - Supabase MCP 설정
  - `.omc/plans/reserve-pool-redesign.md` - 상세 계획서

## Past 1 [1772084598]
- **Task**: Phase 5 - 레거시 정리 + 백테스트 combo 전환
- **Completed**: 마이그레이션 스크립트, account_trader 레거시 fallback 제거, 레거시 API/모델/레지스트리 정리, 백테스트 combo 전환, JS/UI 정리, import 검증 통과
- **Note**: alembic 005 미적용 상태, strategy_configs/strategy_states DROP은 보류

## Past 2 [1772082642]
- **Task**: 매수/매도 로직 분리 및 조합 시스템 구현 (Phase 1~4)
- **Completed**: Phase 1 기반 인프라, Phase 2 로직 분리, Phase 3 실행 엔진 전환, Phase 4 대시보드 UI + combo CRUD API 전체 완료
- **Note**: combo 기반 실행 루프 동작, legacy fallback 유지 상태에서 Phase 5로 진행
