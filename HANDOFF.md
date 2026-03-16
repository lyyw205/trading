# HANDOFF

## Current [1773632655]
- **Task**: 일일 리포트 상세화 — 기존 로그 집계만 하던 리포트를 7개 섹션으로 확장
- **Completed**:
  - `daily_report_service.py` 리포트 생성 로직 확장 (3개 쿼리 → 12개+ DB 집계 쿼리)
  - 거래 성과: 계정별 매수/매도 건수, 실현 손익, 승률, 평균 보유시간, 수수료
  - 계정 상태: CB 상태(healthy/degraded/disabled/recovered), 복구 시도, 매수 일시중지, 미정산 수익
  - 포트폴리오: 계정별 심볼/수량/평균단가/원금 + 오픈 Lot + 전체 합산
  - 데이터 정합성: drift 감지/자동해소/수동필요, 유형별(포지션/잔고/Fill갭) 상세
  - 시간대별 에러 분포: KST 기준 24시간 히트맵
  - 서버 안정성: DB 풀, DB 크기, 활성 연결, 테이블 크기 Top 5, 프로세스 메트릭(psutil)
  - 건강 점수에 reconciliation drift 감점 추가 (drift × 5, 최대 -20)
  - 텔레그램 메시지 확장: 장애 현황 + 거래 성과 + 계정별 요약 + 정합성 경고
  - 대시보드 UI: 6탭 구조(거래 성과/계정 상태/포트폴리오/에러·시간대/정합성/서버 현황)
  - `pyproject.toml`에 psutil 의존성 추가
  - `/api/reports/` CSRF exempt 추가 (require_admin으로 이미 보호)
  - 프로덕션 배포 완료 (Docker 컨테이너 충돌 1회 후 재배포 성공)
- **Next Steps**:
  - 리포트 생성 테스트 ("지금 생성" 버튼으로 확인)
  - 텔레그램 알림 수신 확인 (AlertService 연동)
  - 대시보드에서 계정 이름이 account_id 대신 표시되는지 확인
  - CSP 폰트 경고는 브라우저 확장 이슈로 무시 가능
- **Blockers**: None
- **Related Files**:
  - `app/services/daily_report_service.py` — 리포트 생성 + 서버 헬스 수집 + 텔레그램 메시지
  - `app/dashboard/templates/admin_reports.html` — 6탭 대시보드 UI
  - `app/middleware/csrf_middleware.py` — CSRF exempt 경로 추가
  - `pyproject.toml` — psutil 의존성

## Past 1 [1773391391]
- **Task**: Dust lot 중복 방지 + 기존 중복 병합 + 배포
- **Completed**: insert_lot 중복 방지 가드, 마이그레이션 020~022, MERGED 상태 추가, 단위 테스트 5개, 프로덕션 DB 정리
- **Note**: OPEN 42개, MERGED 21개, CLOSED 33개 최종 상태

## Past 2 [1773209154]
- **Task**: CI 테스트 수정 + 서버 안정화 + 배포 파이프라인 개선
- **Completed**: CI 테스트 4건 수정, circuit breaker 완화, Decimal TypeError 수정, GHCR 배포 파이프라인 전환
- **Note**: `deploy.yml` GHCR 기반, `docker-compose.prod.yml` 업데이트
