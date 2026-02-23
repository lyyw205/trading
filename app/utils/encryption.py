from cryptography.fernet import Fernet, MultiFernet


class EncryptionManager:
    """
    API 키 암호화/복호화.
    다중 키 지원으로 로테이션 가능.

    .env 설정:
      ENCRYPTION_KEYS=newest_key,older_key,oldest_key  (쉼표 구분)

    암호화: 첫 번째 키(newest)로 수행
    복호화: 모든 키를 순서대로 시도
    """

    def __init__(self, keys: list[str]):
        if not keys:
            raise ValueError("최소 1개의 암호화 키 필요")
        fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        self._multi = MultiFernet(fernets)

    def encrypt(self, plaintext: str) -> str:
        return self._multi.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._multi.decrypt(ciphertext.encode()).decode()
