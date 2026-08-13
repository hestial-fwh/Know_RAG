from app.services.mcp_access import crypto


def test_encrypt_decrypt_roundtrip():
    secret = crypto.generate_client_secret()
    blob = crypto.encrypt_secret(secret)
    assert blob.startswith("v1:")
    assert crypto.decrypt_secret(blob) == secret


def test_encrypt_is_nondeterministic():
    secret = crypto.generate_client_secret()
    blob1 = crypto.encrypt_secret(secret)
    blob2 = crypto.encrypt_secret(secret)
    assert blob1 != blob2  # random nonce
    assert crypto.decrypt_secret(blob1) == crypto.decrypt_secret(blob2)


def test_decrypt_tampered_blob_fails():
    blob = crypto.encrypt_secret("plain-secret")
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    try:
        crypto.decrypt_secret(tampered)
        raise AssertionError("tampered ciphertext must not decrypt")
    except crypto.McpSecretCryptoError:
        pass


def test_decrypt_invalid_format_fails():
    for bad in ("", "v1:", "plaintext", "v9:abc"):
        try:
            crypto.decrypt_secret(bad)
            raise AssertionError(f"{bad!r} must not decrypt")
        except crypto.McpSecretCryptoError:
            pass


def test_client_id_format_and_uniqueness():
    ids = {crypto.generate_client_id() for _ in range(50)}
    assert len(ids) == 50
    for cid in ids:
        assert cid.startswith("kr_")
        assert len(cid) == 3 + 32


def test_secret_strength():
    secret = crypto.generate_client_secret()
    assert len(secret) >= 32
    assert secret != crypto.generate_client_secret()
