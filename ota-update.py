#!/usr/bin/env python3
"""immurok firmware OTA update tool

Connects to the immurok daemon via Unix socket, writes encrypted+signed
firmware to device Image B. After receiving the END command, the device
verifies SHA256 + HMAC, then reboots and IAP copies Image B to Image A.

Only .imfw format (encrypted + signed) is supported.
Use ota-package.py to package a .bin into .imfw.

Usage: python3 ota-update.py [--socket PATH] [firmware.imfw]

If firmware path is omitted, defaults to the latest build output:
  <project_root>/firmware/build/immurok_CH592F.imfw
"""

import argparse
import base64
import os
import pathlib
import socket
import struct
import sys
import time

def _default_socket_path():
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return os.path.join(xdg, "immurok", "pam.sock")
    return str(pathlib.Path.home() / ".immurok" / "pam.sock")

def _default_firmware_path():
    # Script lives at <project>/ota/ota-update.py; build output at
    # <project>/firmware/build/immurok_CH592F.imfw
    script_dir = pathlib.Path(__file__).resolve().parent
    return str(script_dir.parent / "firmware" / "build" / "immurok_CH592F.imfw")

SOCKET_PATH = _default_socket_path()
DEFAULT_FW_PATH = _default_firmware_path()
IMAGE_B_SIZE = 216 * 1024  # 216KB
CHUNK_SIZE = 240  # ≤243, 16-byte aligned
IMFW_MAGIC = 0x494D4657  # "IMFW"
IMFW_HEADER_SIZE_V1 = 96   # HMAC format (<=1.5.x bootstrap)
IMFW_HEADER_SIZE_V2 = 128  # ECDSA format (1.6.0+)


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
            if buf:
                return buf.decode().strip()
            raise ConnectionError("connection closed")
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
    if len(data) < IMFW_HEADER_SIZE_V1:
        return None

    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != IMFW_MAGIC:
        return None

    # Header size depends on the format version byte (0x04).
    version = data[4]
    header_size = IMFW_HEADER_SIZE_V2 if version >= 2 else IMFW_HEADER_SIZE_V1
    if len(data) < header_size:
        return None

    header = data[:header_size]
    encrypted_fw = data[header_size:]

    # Parse common header fields for display.
    _, version, flags, hw_id, fw_size = struct.unpack_from("<IBBHI", header, 0)
    sec_version = struct.unpack_from("<H", header, 0x0C)[0] if version >= 2 else None

    return {
        "header": header,
        "firmware": encrypted_fw,
        "version": version,
        "hw_id": hw_id,
        "fw_size": fw_size,
        "sec_version": sec_version,
    }


def read_fw_version(imfw_path):
    """Firmware version of the .imfw being pushed.

    The .imfw header carries no semver field, so this is only known when a
    .fw_version sidecar (written by the build) sits next to the file. Do NOT
    fall back to parsing the local version.h: that reports whatever is checked
    out now (e.g. 1.6.0) regardless of which .imfw you push — misleading when
    flashing an older package from dist/. Return None when unknown; the caller
    just omits the line.
    """
    ver_file = os.path.join(os.path.dirname(imfw_path), ".fw_version")
    if os.path.isfile(ver_file):
        with open(ver_file) as f:
            return f.read().strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="immurok firmware OTA update tool")
    parser.add_argument(
        "firmware",
        nargs="?",
        default=DEFAULT_FW_PATH,
        help=f"firmware file path (.imfw) (default: {DEFAULT_FW_PATH})",
    )
    parser.add_argument("--socket", default=SOCKET_PATH, help=f"Unix socket path (default {SOCKET_PATH})")
    args = parser.parse_args()

    # Read firmware file
    if not os.path.isfile(args.firmware):
        print(f"Error: file not found: {args.firmware}")
        if args.firmware == DEFAULT_FW_PATH:
            print("Hint: run ota/build-ota.sh first to produce the default .imfw")
        sys.exit(1)

    with open(args.firmware, "rb") as f:
        file_data = f.read()

    # Parse .imfw format
    imfw = parse_imfw(file_data)
    if imfw is None:
        print("Error: not a valid .imfw file (only encrypted+signed firmware supported)")
        print("Hint: use python3 ota/ota-package.py firmware.bin to package a .bin into .imfw")
        sys.exit(1)

    # Read new firmware version
    new_fw_version = read_fw_version(os.path.abspath(args.firmware))

    firmware = imfw["firmware"]
    fw_size = len(firmware)
    print(f"Firmware: {args.firmware}")
    if new_fw_version:
        print(f"  Version:        {new_fw_version}")
    print(f"  Format version: {imfw['version']}")
    print(f"  Hardware ID:    0x{imfw['hw_id']:04X}")
    print(f"  Plaintext size: {imfw['fw_size']} bytes ({imfw['fw_size']/1024:.1f} KB)")
    print(f"  Encrypted size: {fw_size} bytes ({fw_size/1024:.1f} KB)")

    if fw_size > IMAGE_B_SIZE:
        print(f"Error: firmware too large ({fw_size} bytes > {IMAGE_B_SIZE} bytes)")
        sys.exit(1)

    if fw_size == 0:
        print("Error: firmware file is empty")
        sys.exit(1)

    # Connect to socket
    print(f"\nConnecting to {args.socket} ...")
    try:
        sock = connect_socket(args.socket)
    except (FileNotFoundError, ConnectionRefusedError):
        print("Error: cannot connect to immurok daemon (socket missing or daemon not running)")
        sys.exit(1)

    total_steps = 5
    step = 0

    try:
        # Query current firmware version
        resp = send_cmd(sock, "OTA:VERSION")
        if resp.startswith("OK:"):
            current_version = resp[3:]
            print(f"  Current version: {current_version}")
            if new_fw_version:
                if current_version == new_fw_version:
                    print(f"  Note: update version matches current version")
                else:
                    print(f"  Upgrade path: {current_version} -> {new_fw_version}")

        # Step 1: Get device info
        step += 1
        print(f"\n[{step}/{total_steps}] Querying device info...")
        resp = send_cmd(sock, "OTA:INFO")
        if not resp.startswith("OK:"):
            print(f"Error: {resp}")
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
            print(f"  Response: {resp}")

        # Step 2: Erase Image B
        step += 1
        print(f"\n[{step}/{total_steps}] Erasing Image B (~3-5 seconds)...")
        sock.settimeout(30)
        t0 = time.time()
        resp = send_cmd(sock, "OTA:ERASE")
        elapsed = time.time() - t0
        if resp != "OK":
            print(f"Error: erase failed - {resp}")
            sys.exit(1)
        print(f"  Erase complete ({elapsed:.1f}s)")

        # Step 3: Send HEADER
        step += 1
        print(f"\n[{step}/{total_steps}] Sending encrypted header...")
        hdr_b64 = base64.b64encode(imfw["header"]).decode()
        resp = send_cmd(sock, f"OTA:HEADER:{hdr_b64}")
        if resp != "OK":
            print(f"Error: header rejected - {resp}")
            sys.exit(1)
        print("  Header accepted")

        # Step 4: Write encrypted firmware
        step += 1
        total_chunks = (fw_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"\n[{step}/{total_steps}] Writing encrypted data ({total_chunks} chunks)...")
        t0 = time.time()

        for i in range(total_chunks):
            offset = i * CHUNK_SIZE
            chunk = firmware[offset:offset + CHUNK_SIZE]
            b64 = base64.b64encode(chunk).decode()
            cmd = f"OTA:WRITE:{offset:04x}:{b64}"
            resp = send_cmd(sock, cmd)
            if resp != "OK":
                print(f"\nError: write failed @ offset 0x{offset:04x} - {resp}")
                sys.exit(1)
            progress_bar(i + 1, total_chunks, prefix="  Writing")

        elapsed = time.time() - t0
        speed = fw_size / elapsed / 1024 if elapsed > 0 else 0
        print(f"\n  Write complete ({elapsed:.1f}s, {speed:.1f} KB/s)")

        # Step 5: End - verify signature + reboot
        step += 1
        print(f"\n[{step}/{total_steps}] Verifying signature and rebooting...")
        sock.settimeout(10)
        resp = send_cmd(sock, "OTA:END")

        if resp == "OK":
            print(f"  Device rebooting")
        elif "SHA256" in resp:
            print(f"  Error: integrity check failed (SHA256 mismatch)")
            sys.exit(1)
        elif "HMAC" in resp:
            print(f"  Error: signature verification failed (unofficial firmware)")
            sys.exit(1)
        else:
            print(f"  Response: {resp}")

    except socket.timeout:
        print("\nError: communication timeout")
        sys.exit(1)
    except ConnectionError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        sock.close()

    print("\nUpdate complete!")
    if new_fw_version:
        print(f"New firmware version: {new_fw_version}")
    print("Device is rebooting, IAP will copy the new firmware automatically...")
    print("Please wait ~20 seconds for the device to reconnect.")


if __name__ == "__main__":
    main()
