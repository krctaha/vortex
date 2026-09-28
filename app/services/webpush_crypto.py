"""Web Push sifreleme — RFC 8291 (aes128gcm) + RFC 8292 (VAPID).

NEDEN KENDIMIZ YAZIYORUZ
------------------------
Standart cozum `pywebpush`, bagimliligi `http-ece` uzerinden geliyor.
`http-ece` bakimsiz ve setup.py'si guncel setuptools ile kurulamiyor
("Failed building wheel for http-ece"). Sunucuda da ayni duvara carpardik.

Gereken sey aslinda kucuk: bir ECDH anahtar anlasmasi, HKDF ve tek kayitlik
AES-128-GCM. Hepsi `cryptography` ile yapilabiliyor — o paket zaten kurulu
(argon2/TLS icin). Boylece bakimsiz bir bagimlilik yerine test edilebilir
60 satir kod kaliyor.

REFERANSLAR
  RFC 8188 — Encrypted Content-Encoding (aes128gcm cerceve formati)
  RFC 8291 — Message Encryption for Web Push (anahtar turetme)
  RFC 8292 — VAPID (gonderen kimligi, ES256 JWT)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from typing import Dict, Tuple
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256

RECORD_SIZE = 4096
KEY_INFO = b"WebPush: info\x00"
CEK_INFO = b"Content-Encoding: aes128gcm\x00"
NONCE_INFO = b"Content-Encoding: nonce\x00"


# --------------------------------------------------------------------------- #
# base64url yardimcilari
# --------------------------------------------------------------------------- #
def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256. Web Push'ta cikti hep <= 32 bayt oldugu icin tek tur yeter."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


# --------------------------------------------------------------------------- #
# Anahtar uretimi
# --------------------------------------------------------------------------- #
def generate_vapid_keys() -> Dict[str, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    return {"private": b64e(raw_private),
            "public": b64e(_raw_public(private.public_key()))}


def _load_private(b64_private: str) -> ec.EllipticCurvePrivateKey:
    value = int.from_bytes(b64d(b64_private), "big")
    return ec.derive_private_key(value, ec.SECP256R1())


# --------------------------------------------------------------------------- #
# Yuk sifreleme (RFC 8291)
# --------------------------------------------------------------------------- #
def encrypt(plaintext: bytes, ua_public_b64: str, ua_auth_b64: str) -> bytes:
    """Tarayicinin acabilecegi aes128gcm govdesi uretir."""
    ua_public_raw = b64d(ua_public_b64)
    auth_secret = b64d(ua_auth_b64)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ua_public_raw)

    # Her mesaj icin TEK KULLANIMLIK anahtar cifti. Ayni cifti tekrar
    # kullanmak nonce tekrarina ve sifrelemenin cokmesine yol acar.
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public_raw = _raw_public(as_private.public_key())

    shared = as_private.exchange(ec.ECDH(), ua_public)

    # RFC 8291 §3.4: once auth_secret ile IKM turetilir.
    key_info = KEY_INFO + ua_public_raw + as_public_raw
    ikm = _hkdf(auth_secret, shared, key_info, 32)

    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, CEK_INFO, 16)
    nonce = _hkdf(salt, ikm, NONCE_INFO, 12)

    # RFC 8188: son (ve tek) kayit 0x02 ayraci ile biter.
    padded = plaintext + b"\x02"
    max_plain = RECORD_SIZE - 16 - 1          # GCM etiketi + ayrac
    if len(padded) > max_plain:
        raise ValueError(f"yuk cok buyuk: {len(plaintext)} bayt (max {max_plain - 1})")

    ciphertext = AESGCM(cek).encrypt(nonce, padded, None)

    # Baslik: salt(16) | kayit boyu(4) | anahtar uzunlugu(1) | gonderen acik anahtari(65)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public_raw)) + as_public_raw
    return header + ciphertext


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey,
            ua_auth: bytes) -> bytes:
    """Yalnizca TEST icin: encrypt()'in ciktisini geri cozer.

    Sifrelemenin dogrulugunu tarayiciya gonderip ummak yerine burada
    gidis-donus testiyle kanitlayabilmek icin var.
    """
    salt, rs_and_len, rest = body[:16], body[16:21], body[21:]
    _rs, idlen = struct.unpack("!IB", rs_and_len)
    as_public_raw, ciphertext = rest[:idlen], rest[idlen:]

    as_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), as_public_raw)
    shared = ua_private.exchange(ec.ECDH(), as_public)
    ua_public_raw = _raw_public(ua_private.public_key())

    ikm = _hkdf(ua_auth, shared, KEY_INFO + ua_public_raw + as_public_raw, 32)
    cek = _hkdf(salt, ikm, CEK_INFO, 16)
    nonce = _hkdf(salt, ikm, NONCE_INFO, 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    return padded.rstrip(b"\x02").rstrip(b"\x00")


# --------------------------------------------------------------------------- #
# VAPID basligi (RFC 8292)
# --------------------------------------------------------------------------- #
def vapid_header(endpoint: str, private_b64: str, public_b64: str,
                 subject: str, ttl_seconds: int = 12 * 3600) -> str:
    """Authorization basligi uretir: 'vapid t=<jwt>,k=<acik anahtar>'."""
    parsed = urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"

    header = b64e(json.dumps({"typ": "JWT", "alg": "ES256"},
                             separators=(",", ":")).encode())
    claims = b64e(json.dumps({"aud": audience,
                              "exp": int(time.time()) + ttl_seconds,
                              "sub": subject},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()

    private = _load_private(private_b64)
    der_sig = private.sign(signing_input, ec.ECDSA(SHA256()))
    # JWS ES256 ham r||s ister; cryptography DER dondurur.
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    jwt = f"{header}.{claims}.{b64e(raw_sig)}"
    return f"vapid t={jwt},k={public_b64}"


def new_test_subscription() -> Tuple[Dict[str, str], ec.EllipticCurvePrivateKey, bytes]:
    """Test icin sahte bir tarayici abonesi uretir."""
    ua_private = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    sub = {"p256dh": b64e(_raw_public(ua_private.public_key())), "auth": b64e(auth)}
    return sub, ua_private, auth
