# HANDOFF

## Current [1772013600]
- **Task**: 매수/매도 로직 분리 및 조합 시스템 설계 (ralplan 컨센서스 완료)
- **Completed**:
  - 매수/매도 로직 분리 계획 1차 초안 작성
  - 1:1 조합 모델 확정 (1 매수 + 1 매도 = 1 Trading Combo)
  - Ralplan 컨센서스 워크플로 완료:
    - Planner: 계획 정제 (헬퍼 추출, base_price_update_mode, pending key 통일 등)
    - Architect: CONDITIONAL APPROVE (CRITICAL 1 + HIGH 3 + MEDIUM 3)
    - Critic 1차: REVISE (5건 수정 요구)
    - Critic 2차: REVISE (2건 코드 불일치)
    - Critic 3차: APPROVE
  - 최종 계획을 `docs/05.BUY_SELL_SPLIT_PLAN.md`로 저장
- **Next Steps**:
  - `docs/05.BUY_SELL_SPLIT_PLAN.md` 기반 구현 (5 Phase, 6.5~7일)
  - Phase 1: 기반 인프라 (BaseBuyLogic, BaseSellLogic, TradingCombo 모델, DB 마이그레이션)
  - Phase 2: 로직 분리 추출 (LotStackingBuy, TrendBuy, FixedTpSell)
  - Phase 3: 실행 엔진 전환 + 데이터 마이그레이션 (strategy_configs → trading_combos)
  - Phase 4: 대시보드 UI + API (combo CRUD, 조합 생성/편집 UI)
  - Phase 5: 정리 + 안전장치 + 백테스트 전환
  - 별도: `docs/04.ACCOUNT_PLAN.md` 기반 인증 시스템 전환 구현 (6개 태스크)
- **Blockers**: None
- **Related Files**:
  - `docs/05.BUY_SELL_SPLIT_PLAN.md` - 매수/매도 분리 계획서 (Architect/Critic 승인)
  - `docs/04.ACCOUNT_PLAN.md` - 인증 전환 계획서 (v2, Architect/Critic 승인)
  - `docs/03.STRATEGY_CUSTOMIZATION_PLAN.md` - 전략 커스터마이징 (05에 의해 supersede됨)
  - `app/strategies/base.py` - 현재 BaseStrategy (BaseBuyLogic/BaseSellLogic 추가 예정)
  - `app/strategies/lot_stacking.py` - 분리 대상 (매수→buys/lot_stacking.py, 매도→sells/fixed_tp.py)
  - `app/strategies/trend_buy.py` - 분리 대상 (매수→buys/trend.py, 매도→sells/fixed_tp.py)
  - `app/services/account_trader.py` - 실행 루프 전환 대상

## Past 1 [1772005306]
- **Task**: 인증 시스템 전환 계획 수립 + 매매 로직 분석
- **Completed**: Google OAuth/Supabase → 자체 로컬 계정 전환 계획 (ralplan 컨센서스), LOT/추세 매수 매매 로직 상세 분석
- **Note**: `docs/04.ACCOUNT_PLAN.md` (v2, Architect/Critic 승인 완료)
