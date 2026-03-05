#!/usr/bin/env python3
"""immurok 固件加密打包工具

将 .bin 固件打包为 .imfw 格式（加密 + 签名）。

.imfw 格式 (96 字节头 + 加密数据):
  0x00  4  Magic "IMFW"
  0x04  1  Format version
  0x05  1  Flags (reserved)
  0x06  2  Hardware ID (0x0592)
  0x08  4  Firmware size (plaintext)
  0x0C  4  Reserved
  0x10 16  AES-128-CTR IV
  0x20 32  SHA256(plaintext firmware)
  0x40 32  HMAC-SHA256(signing_key, header[0:0x40])
  0x60  .  AES-128-CTR encrypted firmware

用法: python3 ota-package.py firmware.bin [-o output.imfw]
"""

import argparse
import hashlib
import hmac
import os
import struct
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Import keys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    from ota_keys import OTA_AES_KEY, OTA_SIGNING_KEY
except ImportError:
    print("错误: 找不到 ota_keys.py，请先运行 generate_ota_keys.py")
    sys.exit(1)

IMFW_MAGIC = 0x494D4657  # "IMFW"
IMFW_VERSION = 0x01
IMFW_HARDWARE_ID = 0x0592
IMAGE_B_SIZE = 216 * 1024


def aes128_ctr_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CTR encrypt using IV[0:12] as nonce + 4-byte big-endian counter."""
    # Our CTR mode: IV[0:12] || counter[4] (big-endian, starting from 0)
    # Python cryptography uses standard CTR with 128-bit counter
    # We need to match the firmware's counter format
    nonce = iv[:12]
    encrypted = bytearray()
    block_size = 16
    offset = 0

    while offset < len(data):
        block_num = offset // block_size
        # Build counter block: nonce[12] || counter[4] big-endian
        counter_block = nonce + struct.pack(">I", block_num)

        # Encrypt counter block with ECB to get keystream
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        encryptor = cipher.encryptor()
        keystream = encryptor.update(counter_block) + encryptor.finalize()

        # XOR with plaintext
        chunk_len = min(block_size, len(data) - offset)
        for i in range(chunk_len):
            encrypted.append(data[offset + i] ^ keystream[i])
        offset += chunk_len

    return bytes(encrypted)


def main():
    parser = argparse.ArgumentParser(description="immurok 固件加密打包工具")
    parser.add_argument("firmware", help="固件文件路径 (.bin)")
    parser.add_argument("-o", "--output", help="输出文件路径 (默认: 同名 .imfw)")
    args = parser.parse_args()

    if not os.path.isfile(args.firmware):
        print(f"错误: 文件不存在: {args.firmware}")
        sys.exit(1)

    with open(args.firmware, "rb") as f:
        firmware = f.read()

    fw_size = len(firmware)
    if fw_size > IMAGE_B_SIZE:
        print(f"错误: 固件太大 ({fw_size} bytes > {IMAGE_B_SIZE} bytes)")
        sys.exit(1)
    if fw_size == 0:
        print("错误: 固件文件为空")
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        base = os.path.splitext(args.firmware)[0]
        output_path = base + ".imfw"

    print(f"固件: {args.firmware}")
    print(f"大小: {fw_size} bytes ({fw_size/1024:.1f} KB)")

    # Generate random IV
    iv = os.urandom(16)

    # Compute SHA256 of plaintext
    fw_sha256 = hashlib.sha256(firmware).digest()
    print(f"SHA256: {fw_sha256.hex()}")

    # Build header (first 0x40 bytes, without HMAC)
    header_prefix = struct.pack("<IBBHI4s",
        IMFW_MAGIC,        # magic
        IMFW_VERSION,      # version
        0,                 # flags
        IMFW_HARDWARE_ID,  # hw_id
        fw_size,           # fw_size
        b'\x00' * 4,       # reserved
    )
    header_prefix += iv          # 16 bytes IV
    header_prefix += fw_sha256   # 32 bytes SHA256
    assert len(header_prefix) == 0x40

    # Compute HMAC-SHA256 over header[0:0x40]
    header_hmac = hmac.new(OTA_SIGNING_KEY, header_prefix, hashlib.sha256).digest()
    print(f"HMAC:   {header_hmac[:16].hex()}...")

    # Full header (96 bytes)
    header = header_prefix + header_hmac
    assert len(header) == 96

    # Encrypt firmware
    print("加密固件...")
    encrypted = aes128_ctr_encrypt(OTA_AES_KEY, iv, firmware)

    # Write .imfw file
    with open(output_path, "wb") as f:
        f.write(header)
        f.write(encrypted)

    total_size = 96 + len(encrypted)
    print(f"\n输出: {output_path}")
    print(f"总大小: {total_size} bytes ({total_size/1024:.1f} KB)")
    print(f"  头部: 96 bytes")
    print(f"  加密数据: {len(encrypted)} bytes")


if __name__ == "__main__":
    main()
