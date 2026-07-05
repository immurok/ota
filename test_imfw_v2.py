#!/usr/bin/env python3
"""Round-trip test for the v2 (ECDSA P-256) .imfw format.

Validates the exact byte formats the device's uECC_verify expects:
  - public key:     raw big-endian X||Y (64B)        -> OTA_PUBLIC_KEY[64]
  - signature:      raw big-endian r||s (64B)         -> header[0x40:0x80]
  - signed message: SHA256(header[0:0x40])
  - sec_version at header 0x0C is inside the signed region.

Uses an ephemeral keypair — never touches the real ota_keys.py. Also exercises
the real ota-package.py code paths via importlib to catch regressions.
"""

import importlib.util
import os
import sys
import types

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def device_verify(pub64: bytes, sig64: bytes, msg32: bytes) -> bool:
    """Emulate the device's uECC_verify(pub64, msg32, 32, sig64, secp256r1())."""
    x = int.from_bytes(pub64[:32], "big")
    y = int.from_bytes(pub64[32:], "big")
    pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    r = int.from_bytes(sig64[:32], "big")
    s = int.from_bytes(sig64[32:], "big")
    der = encode_dss_signature(r, s)
    try:
        pub.verify(der, msg32, ec.ECDSA(Prehashed(hashes.SHA256())))
        return True
    except Exception:
        return False


def load_package_module(priv_pem: str):
    """Import ota-package.py with a stub ota_keys (ephemeral EC key)."""
    fake_keys = types.ModuleType("ota_keys")
    fake_keys.OTA_AES_KEY = bytes(16)
    fake_keys.OTA_SIGNING_KEY = bytes(32)
    fake_keys.OTA_EC_PRIVATE_PEM = priv_pem
    sys.modules["ota_keys"] = fake_keys

    spec = importlib.util.spec_from_file_location(
        "ota_package", os.path.join(SCRIPT_DIR, "ota-package.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    import hashlib

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    nums = priv.public_key().public_numbers()
    pub64 = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")

    pkg = load_package_module(priv_pem)

    fw = os.urandom(50000)
    fw_sha256 = hashlib.sha256(fw).digest()
    iv = os.urandom(16)
    sec_version = 7

    header_prefix = pkg.build_header_prefix_v2(len(fw), sec_version, iv, fw_sha256)
    assert len(header_prefix) == 0x40, "signed region must be 64 bytes"
    # sec_version must be readable at 0x0C and inside the signed region.
    import struct
    assert struct.unpack_from("<H", header_prefix, 0x0C)[0] == sec_version
    assert header_prefix[4] == pkg.IMFW_VERSION_V2

    sig64 = pkg.sign_ecdsa(header_prefix)
    assert len(sig64) == 64, "signature must be raw 64-byte r||s"

    msg32 = hashlib.sha256(header_prefix).digest()

    # 1. Valid signature verifies with the device-side format.
    assert device_verify(pub64, sig64, msg32), "valid v2 signature failed to verify!"
    print("PASS: valid v2 signature verifies (pub64/sig64/SHA256(header) formats OK)")

    # 2. Tampering with the header (e.g. downgrading sec_version) breaks it.
    tampered = bytearray(header_prefix)
    tampered[0x0C] ^= 0x01  # flip a sec_version bit
    bad_msg = hashlib.sha256(bytes(tampered)).digest()
    assert not device_verify(pub64, sig64, bad_msg), "tampered header still verified!"
    print("PASS: header tamper (sec_version flip) is rejected")

    # 3. Tampering with the signature breaks it.
    bad_sig = bytearray(sig64)
    bad_sig[0] ^= 0x01
    assert not device_verify(pub64, bytes(bad_sig), msg32), "tampered sig still verified!"
    print("PASS: signature tamper is rejected")

    # 4. Wrong key is rejected.
    other = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    other64 = other.x.to_bytes(32, "big") + other.y.to_bytes(32, "big")
    assert not device_verify(other64, sig64, msg32), "wrong public key still verified!"
    print("PASS: wrong public key is rejected")

    print("\nAll v2 round-trip checks passed.")


if __name__ == "__main__":
    main()
