# HANDOFF

## Current [1772099400]
- **Task**: Buy Pause 기능 - 잔고 부족 시 매수만 일시정지, 매도 계속
- **Completed**:
  - Ralplan 합의 (Architect APPROVE + Critic REJECT → 수정 계획 합의)
  - `BuyPauseState` enum (ACTIVE/THROTTLED/PAUSED) + 4개 DB 컬럼 추가
  - `BuyPauseManager` 서비스 신규 생성 (상태 전이, throttle 판정, 동적 주기)
  - `AccountTrader.step()` 잔고 프리체크 + 매수 가드 + 매도 감지 (로트 수 비교)
  - `pre_tick()` 항상 실행 (PAUSED에서도 base_price 유지)
  - 매도 발생 시 잔고 재체크 → 자동 ACTIVE 복귀
  - `_interruptible_sleep` + `asyncio.Event`로 수동 재개 즉시 반응
  - PAUSED+포지션 없음 → 7200s deep sleep, 그 외 정상 주기
  - THROTTLED: 루프 주기 유지, 5사이클 중 1회만 매수 (throttle_cycle 카운터 AccountTrader에 보존)
  - `TradingEngine.resume_buying()` + `POST /buy-pause/resume` API
  - Dashboard에 BuyPauseInfo (state/reason/since/count) 노출
  - 대시보드 UI: Buy Pause Status 카드 + 배지 (PAUSED=노랑, THROTTLED=주황) + 수동 재시작 버튼
  - Alembic 007 마이그레이션
  - Architect 검증 → throttle counter 버그 발견 → 수정 완료
  - 전체 Python 파일 syntax check 통과
- **Next Steps**:
  - `alembic upgrade head` 실행 (005 + 006 + 007 마이그레이션 적용)
  - Docker 빌드 후 통합 테스트 (THROTTLED/PAUSED 상태 전이, 수동 재개)
  - 대시보드 E2E 검증 (Buy Pause 카드, 수동 재시작 버튼)
  - strategy_configs, strategy_states 테이블 향후 DROP 마이그레이션
- **Blockers**: None
- **Related Files**:
  - `app/models/account.py` - BuyPauseState enum + 4개 컬럼
  - `app/services/buy_pause_manager.py` - **신규** 상태 전이/판정/주기 계산
  - `app/services/account_trader.py` - step() 매수 가드 + interruptible sleep
  - `app/services/trading_engine.py` - resume_buying() 메서드
  - `app/api/accounts.py` - POST buy-pause/resume 엔드포인트
  - `app/schemas/dashboard.py` - BuyPauseInfo 스키마
  - `app/schemas/account.py` - AccountResponse에 buy_pause 필드
  - `app/api/dashboard.py` - BuyPauseInfo 응답 포함
  - `app/dashboard/static/js/main.js` - loadBuyPauseStatus, resumeBuying, 배지
  - `app/dashboard/static/css/style.css` - buy-pause 스타일
  - `app/dashboard/templates/account_detail.html` - buy-pause-panel 섹션
  - `alembic/versions/007_buy_pause.py` - 4개 컬럼 마이그레이션

## Past 1 [1772093483]
- **Task**: Reserve Pool 리디자인 - 수동 승인 기반 적립 시스템
- **Completed**: Step 1~4 전체 (pending_earnings 전환, AccountStateManager 확장, 대시보드 UI, Alembic 006)
- **Note**: Supabase MCP 설치 완료, alembic 005+006 미적용 상태

## Past 2 [1772084598]
- **Task**: Phase 5 - 레거시 정리 + 백테스트 combo 전환
- **Completed**: 마이그레이션 스크립트, account_trader 레거시 fallback 제거, 레거시 API/모델/레지스트리 정리, 백테스트 combo 전환
- **Note**: alembic 005 미적용 상태, strategy_configs/strategy_states DROP은 보류
