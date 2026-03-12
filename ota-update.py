#!/usr/bin/env python3
"""immurok 固件 OTA 升级工具

通过 Unix socket 连接 immurok App，将加密签名固件写入设备 Image B。
设备收到 END 命令后验证 SHA256 + HMAC，通过后重启，IAP 将 Image B 拷贝到 Image A。

仅支持 .imfw 格式（加密 + 签名），不支持明文 .bin。
使用 ota-package.py 将 .bin 打包为 .imfw。

用法: python3 ota-update.py [--socket PATH] firmware.imfw
"""

import argparse
import base64
import os
import pathlib
import socket
import struct
import sys
import time

SOCKET_PATH = str(pathlib.Path.home() / ".immurok" / "pam.sock")
IMAGE_B_SIZE = 216 * 1024  # 216KB
CHUNK_SIZE = 240  # ≤243, 16-byte aligned
IMFW_MAGIC = 0x494D4657  # "IMFW"
IMFW_HEADER_SIZE = 96


def connect_socket(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect(path)
    return sock


def send_cmd(sock, cmd):
    """Send a command and read one line response."""
    sock.sendall((cmd + "\n").encode())
    # Read response line
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("连接断开")
        buf += chunk
        if b"\n" in buf:
            line, _ = buf.split(b"\n", 1)
            return line.decode().strip()


def progress_bar(current, total, prefix="", width=40):
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    sys.stdout.write(f"\r{prefix} [{bar}] {pct*100:5.1f}% ({current}/{total})")
    sys.stdout.flush()


def parse_imfw(data):
    """Parse .imfw file. Returns header info + encrypted firmware, or None."""
    if len(data) < IMFW_HEADER_SIZE:
        return None

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != IMFW_MAGIC:
        return None

    header = data[:IMFW_HEADER_SIZE]
    encrypted_fw = data[IMFW_HEADER_SIZE:]

    # Parse header fields for display
    _, version, flags, hw_id, fw_size = struct.unpack_from("<IBBHI", header, 0)

    return {
        "header": header,
        "firmware": encrypted_fw,
        "version": version,
        "hw_id": hw_id,
        "fw_size": fw_size,
    }


def read_fw_version(imfw_path):
    """Read firmware version from .fw_version file next to the .imfw, or from version.h."""
    # Try .fw_version in same directory
    build_dir = os.path.dirname(imfw_path)
    ver_file = os.path.join(build_dir, ".fw_version")
    if os.path.isfile(ver_file):
        with open(ver_file) as f:
            return f.read().strip()

    # Try version.h relative to build dir (build/../APP/include/version.h)
    version_h = os.path.join(build_dir, "..", "APP", "include", "version.h")
    if os.path.isfile(version_h):
        major = minor = patch = ""
        with open(version_h) as f:
            for line in f:
                if "FW_VERSION_MAJOR" in line:
                    major = line.split()[-1]
                elif "FW_VERSION_MINOR" in line:
                    minor = line.split()[-1]
                elif "FW_VERSION_PATCH" in line:
                    patch = line.split()[-1]
        if major and minor and patch:
            return f"{major}.{minor}.{patch}"

    return None


def main():
    parser = argparse.ArgumentParser(description="immurok 固件 OTA 升级工具")
    parser.add_argument("firmware", help="固件文件路径 (.imfw)")
    parser.add_argument("--socket", default=SOCKET_PATH, help=f"Unix socket 路径 (默认 {SOCKET_PATH})")
    args = parser.parse_args()

    # Read firmware file
    if not os.path.isfile(args.firmware):
        print(f"错误: 文件不存在: {args.firmware}")
        sys.exit(1)

    with open(args.firmware, "rb") as f:
        file_data = f.read()

    # Parse .imfw format
    imfw = parse_imfw(file_data)
    if imfw is None:
        print("错误: 不是有效的 .imfw 文件（仅支持加密签名固件）")
        print("提示: 使用 python3 ota/ota-package.py firmware.bin 将明文固件打包为 .imfw")
        sys.exit(1)

    # Read new firmware version
    new_fw_version = read_fw_version(os.path.abspath(args.firmware))

    firmware = imfw["firmware"]
    fw_size = len(firmware)
    print(f"固件: {args.firmware}")
    if new_fw_version:
        print(f"  更新版本: {new_fw_version}")
    print(f"  格式版本: {imfw['version']}")
    print(f"  硬件 ID: 0x{imfw['hw_id']:04X}")
    print(f"  明文大小: {imfw['fw_size']} bytes ({imfw['fw_size']/1024:.1f} KB)")
    print(f"  加密数据: {fw_size} bytes ({fw_size/1024:.1f} KB)")

    if fw_size > IMAGE_B_SIZE:
        print(f"错误: 固件太大 ({fw_size} bytes > {IMAGE_B_SIZE} bytes)")
        sys.exit(1)

    if fw_size == 0:
        print("错误: 固件文件为空")
        sys.exit(1)

    # Connect to socket
    print(f"\n连接 {args.socket} ...")
    try:
        sock = connect_socket(args.socket)
    except (FileNotFoundError, ConnectionRefusedError):
        print("错误: 无法连接 immurok App (socket 不存在或 App 未运行)")
        sys.exit(1)

    total_steps = 5
    step = 0

    try:
        # Query current firmware version
        resp = send_cmd(sock, "OTA:VERSION")
        if resp.startswith("OK:"):
            current_version = resp[3:]
            print(f"  当前版本: {current_version}")
            if new_fw_version:
                if current_version == new_fw_version:
                    print(f"  注意: 更新版本与当前版本相同")
                else:
                    print(f"  升级路径: {current_version} → {new_fw_version}")

        # Step 1: Get device info
        step += 1
        print(f"\n[{step}/{total_steps}] 查询设备信息...")
        resp = send_cmd(sock, "OTA:INFO")
        if not resp.startswith("OK:"):
            print(f"错误: {resp}")
            sys.exit(1)

        parts = resp.split(":")
        # OK:image_flag:image_size:block_size:chip_id
        if len(parts) >= 5:
            image_flag = int(parts[1], 16)
            image_size = int(parts[2], 16)
            block_size = int(parts[3], 16)
            chip_id = int(parts[4], 16)
            print(f"  Image Flag: 0x{image_flag:02X}")
            print(f"  Image Size: {image_size} bytes ({image_size/1024:.0f} KB)")
            print(f"  Block Size: {block_size} bytes")
            print(f"  Chip ID:    0x{chip_id:04X}")
        else:
            print(f"  响应: {resp}")

        # Step 2: Erase Image B
        step += 1
        print(f"\n[{step}/{total_steps}] 擦除 Image B (约需 3-5 秒)...")
        sock.settimeout(30)
        t0 = time.time()
        resp = send_cmd(sock, "OTA:ERASE")
        elapsed = time.time() - t0
        if resp != "OK":
            print(f"错误: 擦除失败 - {resp}")
            sys.exit(1)
        print(f"  擦除完成 ({elapsed:.1f}s)")

        # Step 3: Send HEADER
        step += 1
        print(f"\n[{step}/{total_steps}] 发送加密头部...")
        hdr_b64 = base64.b64encode(imfw["header"]).decode()
        resp = send_cmd(sock, f"OTA:HEADER:{hdr_b64}")
        if resp != "OK":
            print(f"错误: 头部被拒绝 - {resp}")
            sys.exit(1)
        print("  头部已接受")

        # Step 4: Write encrypted firmware
        step += 1
        total_chunks = (fw_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"\n[{step}/{total_steps}] 写入加密数据 ({total_chunks} 个数据包)...")
        t0 = time.time()

        for i in range(total_chunks):
            offset = i * CHUNK_SIZE
            chunk = firmware[offset:offset + CHUNK_SIZE]
            b64 = base64.b64encode(chunk).decode()
            cmd = f"OTA:WRITE:{offset:04x}:{b64}"
            resp = send_cmd(sock, cmd)
            if resp != "OK":
                print(f"\n错误: 写入失败 @ offset 0x{offset:04x} - {resp}")
                sys.exit(1)
            progress_bar(i + 1, total_chunks, prefix="  写入")

        elapsed = time.time() - t0
        speed = fw_size / elapsed / 1024 if elapsed > 0 else 0
        print(f"\n  写入完成 ({elapsed:.1f}s, {speed:.1f} KB/s)")

        # Step 5: End - verify signature + reboot
        step += 1
        print(f"\n[{step}/{total_steps}] 验证签名并重启...")
        sock.settimeout(10)
        resp = send_cmd(sock, "OTA:END")

        if resp == "OK":
            print(f"  设备即将重启")
        elif "SHA256" in resp:
            print(f"  错误: 固件完整性校验失败 (SHA256 不匹配)")
            sys.exit(1)
        elif "HMAC" in resp:
            print(f"  错误: 固件签名验证失败 (非官方固件)")
            sys.exit(1)
        else:
            print(f"  响应: {resp}")

    except socket.timeout:
        print("\n错误: 通信超时")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\n错误: {e}")
        sys.exit(1)
    finally:
        sock.close()

    print("\n升级完成！")
    if new_fw_version:
        print(f"新固件版本: {new_fw_version}")
    print("设备正在重启，IAP 将自动拷贝新固件...")
    print("请等待约 20 秒，设备将自动重新连接。")


if __name__ == "__main__":
    main()
