import hmac

from app.services.mcp_access import signature


CANONICAL_ARGS = dict(
    client_id="kr_abc123",
    timestamp="1789300000",
    nonce="deadbeef01",
    method="POST",
    path="/mcp",
    body_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
)


def test_canonical_string_exact_format():
    canonical = signature.build_canonical_string(**CANONICAL_ARGS)
    expected = (
        "kr_abc123\n"
        "1789300000\n"
        "deadbeef01\n"
        "POST\n"
        "/mcp\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert canonical == expected


def test_hmac_matches_manual_computation():
    secret = "s3cret"
    sig = signature.compute_signature(client_secret=secret, **CANONICAL_ARGS)
    manual = hmac.new(
        secret.encode("utf-8"),
        signature.build_canonical_string(**CANONICAL_ARGS).encode("utf-8"),
        "sha256",
    ).hexdigest()
    assert sig == manual
    assert sig == sig.lower()  # lowercase hex


def test_verify_signature_ok():
    secret = "s3cret"
    sig = signature.compute_signature(client_secret=secret, **CANONICAL_ARGS)
    assert signature.verify_signature(client_secret=secret, signature=sig, **CANONICAL_ARGS)


def test_verify_wrong_secret_fails():
    sig = signature.compute_signature(client_secret="right", **CANONICAL_ARGS)
    assert not signature.verify_signature(client_secret="wrong", signature=sig, **CANONICAL_ARGS)


def test_verify_wrong_signature_fails():
    assert not signature.verify_signature(
        client_secret="s3cret", signature="00" * 32, **CANONICAL_ARGS
    )


def test_verify_tampered_body_hash_fails():
    secret = "s3cret"
    args = dict(CANONICAL_ARGS)
    sig = signature.compute_signature(client_secret=secret, **args)
    args["body_sha256"] = "deadbeef" * 8  # body changed after signing
    assert not signature.verify_signature(client_secret=secret, signature=sig, **args)


def test_verify_tampered_path_fails():
    secret = "s3cret"
    args = dict(CANONICAL_ARGS)
    sig = signature.compute_signature(client_secret=secret, **args)
    args["path"] = "/mcp/other"
    assert not signature.verify_signature(client_secret=secret, signature=sig, **args)


def test_method_normalized_uppercase():
    secret = "s3cret"
    sig = signature.compute_signature(client_secret=secret, method="post", **{k: v for k, v in CANONICAL_ARGS.items() if k != "method"})
    assert signature.verify_signature(client_secret=secret, method="POST", signature=sig, **{k: v for k, v in CANONICAL_ARGS.items() if k != "method"})


def test_empty_body_sha256_is_well_known():
    assert signature.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
