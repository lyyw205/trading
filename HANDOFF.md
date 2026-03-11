# HANDOFF

## Current [1773209154]
- **Task**: CI 테스트 수정 + 서버 안정화 + 배포 파이프라인 개선
- **Completed**:
  - CI 테스트 4건 수정 — `app_client` fixture에서 patched `TradingSessionLocal` 사용 (AsyncEngine expected 에러)
  - Circuit breaker 완화 — TRANSIENT 에러는 매수만 일시정지, PERMANENT만 계정 차단
  - `BuyPauseManager.force_pause()` 추가
  - Decimal × float TypeError 수정 — `fixed_tp.py`에서 DB Decimal 값 float() 변환
  - 오라클 서버 swap 2GB 추가 (OOM 방지)
  - 서버 배포 + circuit breaker 리셋
  - DB 심볼 대소문자 중복 정리 (ethusdt → ETHUSDT 통합)
  - 배포 파이프라인 GHCR로 전환 — 서버에서 빌드 없이 pull만
- **Next Steps**:
  - `production` 브랜치 생성 후 GHCR 배포 파이프라인 실제 테스트
  - BTCUSDT 구독 상태 확인 (70건만 있음, 현재 미구독 가능)
  - Phase 2 감사 항목: H-7 reserve_locks, H-8 N+1, H-17~19 테스트 커버리지
  - 오라클 ARM 무료 인스턴스 재고 확보 시도 (24GB RAM)
- **Blockers**: None
- **Related Files**:
  - `tests/conftest.py` — app_client fixture 수정 (patched TradingSessionLocal)
  - `app/services/account_trader.py` — circuit breaker 완화, _pause_buying_on_error 추가
  - `app/services/buy_pause_manager.py` — force_pause() 추가
  - `app/strategies/sells/fixed_tp.py` — Decimal float() 변환
  - `.github/workflows/deploy.yml` — GHCR 기반 배포
  - `docker/docker-compose.prod.yml` — GHCR 이미지 + DB 서비스 포함

## Past 1 [1773111387]
- **Task**: 전체 프로젝트 감사 (9명 전문가) + Phase 1 수정 (런칭 전 필수)
- **Completed**: CRITICAL 4건 + HIGH 11건 + MEDIUM 4건 해결, 58/100 → 75/100 상향
- **Note**: `.omc/audit/report.md` 최종 업데이트, 19개 파일 수정

## Past 2 [1773030697]
- **Task**: OCI 서버 배포 계획 수립 및 점검
- **Completed**: 배포 준비 전체 점검, 6가지 문제점 식별, `docs/07.OCI_DEPLOY_PLAN.md` 작성
- **Note**: Dockerfile, docker-compose.prod.yml, deploy.yml 수정 포함
