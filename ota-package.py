#!/usr/bin/env python3
"""immurok firmware encryption/packaging tool.

Packages a .bin firmware into .imfw format (AES-CTR encrypted + signed).

Two header formats:

  v1 (96-byte header, HMAC-SHA256) — legacy, used ONLY to sign the 1.6.0
  bootstrap for fielded <=1.5.x devices:
    0x00  4  Magic "IMFW"        0x10 16  AES-128-CTR IV
    0x04  1  Format version (1)  0x20 32  SHA256(plaintext firmware)
    0x05  1  Flags               0x40 32  HMAC-SHA256(signing_key, header[0:0x40])
    0x06  2  Hardware ID         0x60  .  AES-128-CTR encrypted firmware
    0x08  4  Firmware size
    0x0C  4  Reserved

  v2 (128-byte header, ECDSA P-256) — 1.6.0+ :
    0x00  4  Magic "IMFW"        0x10 16  AES-128-CTR IV
    0x04  1  Format version (2)  0x20 32  SHA256(plaintext firmware)
    0x05  1  Flags               0x40 64  ECDSA(P-256) over SHA256(header[0:0x40]),
    0x06  2  Hardware ID                  raw big-endian r||s
    0x08  4  Firmware size       0x80  .  AES-128-CTR encrypted firmware
    0x0C  2  Security version (SVN, anti-rollback)
    0x0E  2  Reserved

Usage:
  python3 ota-package.py firmware.bin                 # v2 (default), SVN=1
  python3 ota-package.py firmware.bin --format v1     # v1 bootstrap (HMAC)
  python3 ota-package.py firmware.bin --sec-version 2 # bump SVN
"""

import argparse
import hashlib
import hmac
import os
import struct
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Import keys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import ota_keys
except ImportError:
    print("Error: ota_keys.py not found, run generate_ota_keys.py first")
    sys.exit(1)

IMFW_MAGIC = 0x494D4657  # "IMFW"
IMFW_VERSION_V1 = 0x01
IMFW_VERSION_V2 = 0x02
IMFW_HARDWARE_ID = 0x0592
IMAGE_B_SIZE = 216 * 1024


def aes128_ctr_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CTR encrypt using IV[0:12] as nonce + 4-byte big-endian counter."""
    nonce = iv[:12]
    encrypted = bytearray()
    block_size = 16
    offset = 0

    while offset < len(data):
        block_num = offset // block_size
        counter_block = nonce + struct.pack(">I", block_num)

        cipher = Cipher(algorithms.AES(key), modes.ECB())
        encryptor = cipher.encryptor()
        keystream = encryptor.update(counter_block) + encryptor.finalize()

        chunk_len = min(block_size, len(data) - offset)
        for i in range(chunk_len):
            encrypted.append(data[offset + i] ^ keystream[i])
        offset += chunk_len

    return bytes(encrypted)


def build_header_prefix_v1(fw_size: int, iv: bytes, fw_sha256: bytes) -> bytes:
    prefix = struct.pack(
        "<IBBHI4s",
        IMFW_MAGIC, IMFW_VERSION_V1, 0, IMFW_HARDWARE_ID, fw_size, b"\x00" * 4,
    )
    prefix += iv + fw_sha256
    assert len(prefix) == 0x40
    return prefix


def build_header_prefix_v2(fw_size: int, sec_version: int, iv: bytes,
                           fw_sha256: bytes) -> bytes:
    prefix = struct.pack(
        "<IBBHIHH",
        IMFW_MAGIC, IMFW_VERSION_V2, 0, IMFW_HARDWARE_ID, fw_size,
        sec_version, 0,
    )
    prefix += iv + fw_sha256
    assert len(prefix) == 0x40
    return prefix


def sign_hmac(header_prefix: bytes) -> bytes:
    """v1: HMAC-SHA256(signing_key, header[0:0x40]) -> 32 bytes."""
    return hmac.new(ota_keys.OTA_SIGNING_KEY, header_prefix, hashlib.sha256).digest()


def sign_ecdsa(header_prefix: bytes) -> bytes:
    """v2: ECDSA P-256 over SHA256(header[0:0x40]) -> raw big-endian r||s (64B).

    Output format matches uECC_verify on the device (raw 32-byte r, 32-byte s).
    """
    if not hasattr(ota_keys, "OTA_EC_PRIVATE_PEM"):
        print("Error: OTA_EC_PRIVATE_PEM missing — run generate_ota_keys.py")
        sys.exit(1)
    priv = serialization.load_pem_private_key(
        ota_keys.OTA_EC_PRIVATE_PEM.encode(), password=None)
    digest = hashlib.sha256(header_prefix).digest()
    der = priv.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def main():
    parser = argparse.ArgumentParser(
        description="immurok firmware encryption/packaging tool")
    parser.add_argument("firmware", help="firmware file path (.bin)")
    parser.add_argument("-o", "--output",
                        help="output file path (default: same name with .imfw)")
    parser.add_argument("--format", choices=["v1", "v2"], default="v2",
                        help="header format: v1=HMAC bootstrap, v2=ECDSA (default)")
    parser.add_argument("--sec-version", type=int, default=1,
                        help="security version (SVN) for v2 anti-rollback (default 1)")
    args = parser.parse_args()

    if not os.path.isfile(args.firmware):
        print(f"Error: file not found: {args.firmware}")
        sys.exit(1)

    with open(args.firmware, "rb") as f:
        firmware = f.read()

    fw_size = len(firmware)
    if fw_size > IMAGE_B_SIZE:
        print(f"Error: firmware too large ({fw_size} bytes > {IMAGE_B_SIZE} bytes)")
        sys.exit(1)
    if fw_size == 0:
        print("Error: firmware file is empty")
        sys.exit(1)

    if args.format == "v2" and not (0 <= args.sec_version <= 0xFFFF):
        print(f"Error: --sec-version must fit in uint16 (0..65535)")
        sys.exit(1)

    output_path = args.output or (os.path.splitext(args.firmware)[0] + ".imfw")

    print(f"Firmware: {args.firmware}")
    print(f"Size:     {fw_size} bytes ({fw_size/1024:.1f} KB)")
    print(f"Format:   {args.format}")

    iv = os.urandom(16)
    fw_sha256 = hashlib.sha256(firmware).digest()
    print(f"SHA256:   {fw_sha256.hex()}")

    if args.format == "v1":
        header_prefix = build_header_prefix_v1(fw_size, iv, fw_sha256)
        sig = sign_hmac(header_prefix)
        print(f"HMAC:     {sig[:16].hex()}...")
    else:
        header_prefix = build_header_prefix_v2(fw_size, args.sec_version, iv, fw_sha256)
        sig = sign_ecdsa(header_prefix)
        print(f"SVN:      {args.sec_version}")
        print(f"ECDSA:    {sig[:16].hex()}... ({len(sig)}B r||s)")

    header = header_prefix + sig
    hdr_len = 96 if args.format == "v1" else 128
    assert len(header) == hdr_len, f"header {len(header)} != {hdr_len}"

    print("Encrypting firmware...")
    encrypted = aes128_ctr_encrypt(ota_keys.OTA_AES_KEY, iv, firmware)

    with open(output_path, "wb") as f:
        f.write(header)
        f.write(encrypted)

    total_size = hdr_len + len(encrypted)
    print(f"\nOutput: {output_path}")
    print(f"Total size:   {total_size} bytes ({total_size/1024:.1f} KB)")
    print(f"  Header:     {hdr_len} bytes")
    print(f"  Encrypted:  {len(encrypted)} bytes")


if __name__ == "__main__":
    main()
