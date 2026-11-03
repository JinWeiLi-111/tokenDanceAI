import pytest
from cryptography.fernet import InvalidToken

from tokendanceai.utils.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    """verify fernet encrypt and decrypt"""
    plain_text = "sk-sxwkcwcjwcnjncjencecn"

    assert plain_text == decrypt(encrypt(plain_text))


def test_encrypt_output_differs_from_plaintext():
    """confirm encrypt is valid"""
    plain_text = "sk-sxwkcwcjwcnjncjencecn"

    assert encrypt(plain_text) != plain_text


def test_same_plaintext_encrypts_to_differs_from_plaintext():
    """same plaintext, different output in twice encrypt"""
    plain_text = "sk-sxwkcwcjwcnjncjencecn"

    assert encrypt(plain_text) != encrypt(plain_text)
    assert decrypt(encrypt(plain_text)) == decrypt(encrypt(plain_text))


def test_decrypt_tampered_ciphertext_raises():
    ciphertext = encrypt("important")
    tampered = ciphertext[:-2] + ("AA" if ciphertext[-2:] != "AA" else "BB")

    with pytest.raises(InvalidToken):
        decrypt(tampered)


def test_decrypt_garbage_raise():
    """raise exception encountering illegal input"""
    with pytest.raises(InvalidToken):
        decrypt("this-is-a-illeagl-input")


def test_unicode_roundtrip():
    """中文/emoji 也可以被正确加解密"""
    plain_text = "密钥🔑测试"

    assert decrypt(encrypt(plain_text)) == plain_text
