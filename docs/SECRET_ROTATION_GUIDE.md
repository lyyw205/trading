# 시크릿 로테이션 운영 가이드

이 문서는 `.env`에 있는 4개 시크릿의 로테이션 절차, 부작용, 롤백 방법을 설명합니다.

**권장 로테이션 순서:** ENCRYPTION_KEYS → SESSION_SECRET_KEY → CSRF_SECRET → DATABASE_URL

---

## 1. ENCRYPTION_KEYS (Fernet 암호화 키)

**용도:** Binance API 키/시크릿 암호화/복호화 (`app/utils/encryption.py`, `MultiFernet`)

**로테이션 절차 (zero-downtime):**
1. 새 Fernet 키 생성:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. `.env`의 `ENCRYPTION_KEYS`에 새 키를 **첫 번째**로 추가 (쉼표 구분):
   ```
   ENCRYPTION_KEYS=새키,기존키
   ```
3. 앱 재시작 — 새 데이터는 새 키로 암호화, 기존 데이터는 이전 키로 복호화
4. 기존 데이터를 새 키로 재암호화:
   ```bash
   python scripts/rotate_encryption_key.py --dry-run  # 먼저 시뮬레이션
   python scripts/rotate_encryption_key.py             # 본 실행
   ```
5. 모든 행 재암호화 확인 후, 이전 키 제거 가능

**부작용:**
- 스크립트 실행 전까지는 부작용 없음 (MultiFernet이 순서대로 키 시도)
- 이전 키를 제거하면 미재암호화 데이터 복호화 불가 → **Binance API 키 영구 손실**
- 스크립트가 중간에 실패해도 재실행 안전 (idempotent)

**롤백:** 새 키를 제거하고 기존 키만 남기면 원래 상태로 복귀

---

## 2. SESSION_SECRET_KEY (세션 쿠키 서명 키)

**용도:** 세션 쿠키 서명/검증 (`app/services/session_manager.py`, `itsdangerous`)

**로테이션 절차 (zero-downtime, 다중 키 지원):**
1. 새 키 생성:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. `.env`의 `SESSION_SECRET_KEY`에 새 키를 **첫 번째**로 추가:
   ```
   SESSION_SECRET_KEY=새키,기존키
   ```
3. 앱 재시작 — 새 세션은 새 키로 서명, 기존 세션은 이전 키로 검증 가능
4. `max_age` (24시간) 경과 후 기존 세션이 자연 만료
5. 이전 키 제거

**부작용:**
- 다중 키 사용 중에는 부작용 없음
- 이전 키를 `max_age` 전에 제거하면 해당 키로 서명된 세션 무효화 → 일부 사용자 강제 로그아웃

**롤백:** 새 키를 제거하고 기존 키만 남기면 원래 상태

---

## 3. CSRF_SECRET (CSRF 토큰 시크릿)

**용도:** CSRF 토큰 생성/검증 (`starlette-csrf` 미들웨어)

**로테이션 절차 (잠시 중단 발생):**
1. 새 키 생성:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. `.env`의 `CSRF_SECRET` 값 교체
3. 앱 재시작

**부작용:**
- `starlette-csrf`는 다중 키를 지원하지 않음
- 키 교체 시, 이미 브라우저에 캐시된 CSRF 토큰으로 폼 제출 시 실패
- 사용자가 페이지를 새로고침하면 새 CSRF 토큰을 받아 정상 작동

**권장:** 트래픽이 적은 시간대에 수행

**롤백:** 이전 키로 되돌리고 앱 재시작

---

## 4. DATABASE_URL (데이터베이스 접속 정보)

**용도:** PostgreSQL(Supabase) 연결 (`app/db/session.py`)

**로테이션 절차 (다운타임 발생):**
1. Supabase 대시보드에서 데이터베이스 비밀번호 변경
2. `.env`의 `DATABASE_URL`에 새 비밀번호 반영
3. 앱 재시작
4. (선택) 이전 비밀번호로 접속 불가 확인

**부작용:**
- 재시작 중 잠시 다운타임 발생
- 기존 DB 커넥션 풀이 즉시 끊김
- `lru_cache`로 캐싱된 설정은 프로세스 수명 동안 유지 → `.env` 변경만으로는 반영 안됨, 반드시 앱 재시작 필요

**롤백:** 이전 비밀번호로 `.env` 복구 + 앱 재시작 (Supabase에서 이전 비밀번호가 여전히 유효한 경우에만)

---

## 시크릿 강도 요구사항

프로덕션 환경(`ENVIRONMENT=production`)에서는 앱 시작 시 자동 검증:

| 시크릿 | 최소 요구사항 |
|--------|---------------|
| `SESSION_SECRET_KEY` | 각 키 32자 이상 |
| `CSRF_SECRET` | 32자 이상 |
| `ENCRYPTION_KEYS` | 유효한 Fernet 키 형식 |
| `DATABASE_URL` | 존재 여부만 확인 |

요구사항 미충족 시 `ValueError`와 함께 앱 시작이 차단됩니다.
