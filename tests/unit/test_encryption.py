import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.utils.encryption import EncryptionManager


@pytest.mark.unit
class TestEncryptionManager:
    def test_encrypt_decrypt_roundtrip(self):
        key = Fernet.generate_key().decode()
        manager = EncryptionManager([key])

        for plaintext in ["hello world", "", "한국어 유니코드 테스트"]:
            ciphertext = manager.encrypt(plaintext)
            assert manager.decrypt(ciphertext) == plaintext

    def test_key_rotation_old_key_still_decrypts(self):
        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()

        manager_a = EncryptionManager([key_a])
        ciphertext = manager_a.encrypt("rotation test")

        manager_rotated = EncryptionManager([key_b, key_a])
        assert manager_rotated.decrypt(ciphertext) == "rotation test"

    def test_invalid_key_raises_valueerror(self):
        with pytest.raises(ValueError):
            EncryptionManager(["not-valid"])

    def test_empty_keys_raises_valueerror(self):
        with pytest.raises(ValueError):
            EncryptionManager([])

    def test_wrong_key_cannot_decrypt(self):
        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()

        manager_a = EncryptionManager([key_a])
        ciphertext = manager_a.encrypt("secret data")

        manager_b = EncryptionManager([key_b])
        with pytest.raises(InvalidToken):
            manager_b.decrypt(ciphertext)
