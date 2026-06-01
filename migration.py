#!/usr/bin/env python3
"""immurok firmware migration tool — upgrade an OLD-GATT-UUID device to latest.

WHY THIS EXISTS
---------------
Early firmware advertised the custom GATT service with 16-bit SIG short UUIDs
(immurok service 0x1234xxxx, OTA service 0xFEE0 / char 0xFEE1). Production
firmware moved to random 128-bit UUIDs (commit "production BLE asset
compliance"). The current daemon / ota-update.py only know the NEW UUIDs, so
they cannot even connect to an old-firmware device ("CMD characteristic not
found"), which means the normal socket-based OTA path can't run.

This tool talks BLE directly (via bleak), drives the WCH IAP OTA protocol over
the OLD OTA characteristic (0xFEE1), and pushes the latest signed firmware.
After it reboots, the device runs new firmware with the new UUIDs and the
normal daemon / ota-update.py take over.

IS IT SAFE? (no-brick guarantee)
--------------------------------
Yes, by design. The WCH dual-image scheme writes the new firmware to Image B
only. The device verifies SHA256 + HMAC at OTA:END *before* switching images.
Until that passes, Image A (the running firmware) is never touched — so a
failed/rejected migration just leaves the device on the OLD firmware, ready to
retry. The worst realistic outcome is "nothing changed", not "bricked".

THE ONE PRECONDITION (key match)
--------------------------------
The .imfw is AES-encrypted + HMAC-signed with the OTA keys in ota/ota_keys.py
(== firmware/APP/include/ota_keys.h at build time). The device decrypts +
verifies with the keys baked into its CURRENT firmware. So migration succeeds
only if the device's old firmware was built with the SAME OTA keys this .imfw
was signed with — i.e. the keys were NOT regenerated (generate_ota_keys.py
--force) since the device was last flashed. If they differ, OTA:END returns
HMAC_MISMATCH and the device stays on old firmware (no harm done).

USAGE
-----
    pip install bleak
    python3 ota/migration.py [firmware.imfw] [--yes] [--adapter hciX]

Default firmware path: firmware/build/immurok_CH592F.imfw (run ota/build-ota.sh
to produce it). Requires a BLE adapter on this machine. Stop the immurok daemon
first so it doesn't hold the connection:
    systemctl --user stop immurok-daemon     # Linux
"""

import argparse
import asyncio
import os
import pathlib
import struct
import sys

# bleak is imported lazily in amain() so --help works without it installed.
BleakClient = None
BleakScanner = None

# ── Constants (mirror app-linux-rs protocol.rs + ota/ota-update.py) ──
DEVICE_NAME_PREFIX = "immurok"

# OLD firmware GATT (16-bit SIG short UUIDs expanded to 128-bit)
OLD_OTA_CHAR = "0000fee1-0000-1000-8000-00805f9b34fb"
# NEW firmware OTA char — presence means the device is ALREADY migrated
NEW_OTA_CHAR = "c75f4c30-9a2d-4445-92e0-0e034c53d092"

IMFW_MAGIC = 0x494D4657          # "IMFW"
IMFW_HEADER_SIZE = 96
IMAGE_B_SIZE = 216 * 1024        # Image B region size
OTA_IMAGE_B_BLOCKS = 54          # 216KB / 4KB — erased before writing
DEFAULT_CHUNK = 240              # ≤243, 16-byte aligned

# WCH IAP commands (over the OTA characteristic)
CMD_IAP_PROM = 0x80              # write data block
CMD_IAP_ERASE = 0x81            # erase Image B
CMD_IAP_VERIFY = 0x82          # verify written block (unused here)
CMD_IAP_END = 0x83              # finalize: verify sig + reboot
CMD_IAP_INFO = 0x84            # query IAP info

READ_POLL_INTERVAL = 0.20        # seconds, mirrors OTA_READ_POLL_INTERVAL_MS
CMD_TIMEOUT = 5.0
ERASE_TIMEOUT = 15.0
SCAN_TIMEOUT = 15.0


def default_firmware_path():
    script_dir = pathlib.Path(__file__).resolve().parent
    return str(script_dir.parent / "firmware" / "build" / "immurok_CH592F.imfw")


def parse_imfw(data):
    """Parse .imfw: 96-byte header + AES-encrypted firmware. Returns dict or None."""
    if len(data) < IMFW_HEADER_SIZE:
        return None
    if struct.unpack_from("<I", data, 0)[0] != IMFW_MAGIC:
        return None
    header = data[:IMFW_HEADER_SIZE]
    encrypted_fw = data[IMFW_HEADER_SIZE:]
    _, version, flags, hw_id, fw_size = struct.unpack_from("<IBBHI", header, 0)
    return {
        "header": header,
        "firmware": encrypted_fw,
        "version": version,
        "hw_id": hw_id,
        "fw_size": fw_size,
    }


def progress_bar(current, total, prefix="", width=40):
    pct = current / total if total > 0 else 0
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    sys.stdout.write(f"\r{prefix} [{bar}] {pct*100:5.1f}% ({current}/{total})")
    sys.stdout.flush()


async def find_device(adapter):
    """Scan for an immurok device. Returns the BLEDevice or None."""
    print(f"Scanning for an immurok device (up to {SCAN_TIMEOUT:.0f}s)...")
    kwargs = {"adapter": adapter} if adapter else {}
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT, **kwargs)
    for d in devices:
        name = (d.name or "").lower()
        if name.startswith(DEVICE_NAME_PREFIX):
            print(f"  Found: {d.name} [{d.address}]")
            return d
    return None


def find_ota_char(client):
    """Return (char_uuid, already_migrated). Looks for OLD then NEW OTA char."""
    uuids = set()
    for service in client.services:
        for ch in service.characteristics:
            uuids.add(ch.uuid.lower())
    if OLD_OTA_CHAR in uuids:
        return OLD_OTA_CHAR, False
    if NEW_OTA_CHAR in uuids:
        return NEW_OTA_CHAR, True
    return None, False


async def ota_cmd(client, char, payload, timeout):
    """Write a command then poll-read the OTA char until a non-empty reply.

    Mirrors the daemon's ota_write_and_read: write-with-response, then read the
    characteristic value every 200ms until it returns data or we time out.
    """
    await client.write_gatt_char(char, bytes(payload), response=True)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(READ_POLL_INTERVAL)
        try:
            val = await client.read_gatt_char(char)
        except Exception:
            continue
        if val:
            return bytes(val)
    return b""


async def ota_write_block(client, char, payload):
    """Write a data block (fire-and-forget at the app level; BLE confirms write)."""
    await client.write_gatt_char(char, bytes(payload), response=True)


def chunk_size_for(client):
    """Pick a 16-byte-aligned chunk that fits the negotiated ATT MTU.

    ATT write payload = MTU - 3; our IAP block header is 4 bytes, so
    data <= MTU - 7. Align down to 16 (addr is offset/16).
    """
    mtu = getattr(client, "mtu_size", 0) or 0
    if mtu <= 0:
        return DEFAULT_CHUNK
    usable = max(16, (mtu - 7) // 16 * 16)
    return min(DEFAULT_CHUNK, usable)


async def run_migration(device, imfw, adapter, assume_yes):
    firmware = imfw["firmware"]
    fw_size = len(firmware)

    async with BleakClient(device, adapter=adapter) as client:
        print(f"Connected. MTU = {getattr(client, 'mtu_size', '?')}")

        char, already = find_ota_char(client)
        if char is None:
            print("Error: no OTA characteristic found (neither old 0xFEE1 nor "
                  "new c75f4c30). This may not be an immurok device, or services "
                  "didn't resolve — try again.", file=sys.stderr)
            return 2
        if already:
            print("Device already exposes the NEW OTA characteristic — it is "
                  "already on new firmware. Use the normal ota-update.py instead.")
            return 0

        print("Detected OLD-firmware OTA characteristic (0xFEE1) — migration applies.")

        # ── Handshake: IAP INFO ──
        print("\n[1/5] Querying IAP info...")
        resp = await ota_cmd(client, char, [CMD_IAP_INFO, 0x02, 0x00, 0x00], CMD_TIMEOUT)
        if len(resp) < 9:
            print(f"Error: no/short IAP INFO response ({resp.hex() or 'empty'}). "
                  "Old firmware may not speak this IAP protocol.", file=sys.stderr)
            return 2
        image_flag = resp[0]
        image_size = int.from_bytes(resp[1:5], "little")
        block_size = int.from_bytes(resp[5:7], "little")
        chip_id = int.from_bytes(resp[7:9], "little")
        print(f"  Image Flag 0x{image_flag:02X}  Image Size {image_size} B  "
              f"Block {block_size} B  Chip 0x{chip_id:04X}")

        # ── Final confirmation before touching flash ──
        if not assume_yes:
            print("\nAbout to ERASE Image B and write new firmware.")
            print("Safe: Image A (running firmware) stays untouched until OTA:END "
                  "passes SHA256+HMAC. A rejected update leaves the device on old "
                  "firmware.")
            ans = input("Proceed? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted.")
                return 1

        # ── Erase Image B ──
        print("\n[2/5] Erasing Image B (~3-5s)...")
        blk = OTA_IMAGE_B_BLOCKS
        resp = await ota_cmd(
            client, char,
            [CMD_IAP_ERASE, 0x04, 0x00, 0x00, blk & 0xFF, (blk >> 8) & 0xFF],
            ERASE_TIMEOUT,
        )
        if not resp or resp[0] != 0x00:
            return _fail("erase", resp)
        print("  Erase complete.")

        # ── Header ──
        print("\n[3/5] Sending encrypted header...")
        hdr = imfw["header"]
        resp = await ota_cmd(client, char, [0x85, len(hdr)] + list(hdr), CMD_TIMEOUT)
        if not resp or resp[0] != 0x00:
            return _fail("header", resp)
        print("  Header accepted.")

        # ── Write firmware blocks ──
        chunk = chunk_size_for(client)
        total_chunks = (fw_size + chunk - 1) // chunk
        print(f"\n[4/5] Writing {fw_size} B in {total_chunks} blocks (chunk={chunk})...")
        for i in range(total_chunks):
            offset = i * chunk
            data = firmware[offset:offset + chunk]
            addr = offset // 16
            payload = [CMD_IAP_PROM, len(data), addr & 0xFF, (addr >> 8) & 0xFF] + list(data)
            await ota_write_block(client, char, payload)
            progress_bar(i + 1, total_chunks, prefix="  Writing")
        print()

        # ── End: verify + reboot ──
        print("\n[5/5] Finalizing (verify SHA256 + HMAC, then reboot)...")
        resp = await ota_cmd(client, char, [CMD_IAP_END, 0x02, 0x00, 0x00], CMD_TIMEOUT)
        if resp:
            if resp[0] == 0xF1:
                print("Error: SHA256 mismatch — firmware corrupted in transit. "
                      "Image A untouched; just re-run.", file=sys.stderr)
                return 3
            if resp[0] == 0xF2:
                print("Error: HMAC mismatch — this .imfw is signed with DIFFERENT "
                      "OTA keys than the device's firmware was built with. The "
                      "device stays on old firmware (not bricked). You need the "
                      "matching ota_keys, or flash via wired wlink instead.",
                      file=sys.stderr)
                return 3
            if resp[0] == 0xF4:
                print("Error: device refused — battery <5%. Charge and retry.",
                      file=sys.stderr)
                return 3
        print("  Accepted. Device is rebooting; IAP copies Image B -> Image A.")
        return 0


def _fail(stage, resp):
    if resp and resp[0] == 0xF4:
        print(f"\nError: {stage} refused — battery <5%. Charge and retry.",
              file=sys.stderr)
    elif resp and resp[0] == 0xF3:
        # SEC_ERR_NO_FP_ENROLLED — old firmware (1.2.20..1.3.3) blocks every
        # OTA command except INFO whenever the device has ZERO enrolled
        # fingerprints (regardless of pairing; see hidkbd.c no-FP gate). The
        # gate opens only once at least one fingerprint is enrolled — and
        # enrollment first requires the device to be paired.
        print(f"\nError: {stage} rejected (0xF3 = no fingerprint enrolled).\n"
              "  Old firmware refuses OTA until the device has >=1 enrolled "
              "fingerprint.\n"
              "  This tool only does OTA — it cannot pair/enroll. Do this "
              "first, using the\n"
              "  immurok CLI/daemon built with the OLD GATT UUIDs (so it can "
              "reach the device):\n"
              "    1. immurok-cli pair          # device must be unpaired; "
              "long-press 3s to reset if not\n"
              "    2. immurok-cli fp enroll 0    # opens the OTA gate\n"
              "  Then OTA — either re-run this tool, or (simpler, since the "
              "old-UUID daemon is\n"
              "  already up) just: python3 ota/ota-update.py "
              "firmware/build/immurok_CH592F.imfw",
              file=sys.stderr)
    else:
        code = f"0x{resp[0]:02X}" if resp else "no response"
        print(f"\nError: {stage} failed ({code}).", file=sys.stderr)
    return 3


async def amain():
    parser = argparse.ArgumentParser(
        description="Migrate an old-GATT-UUID immurok device to the latest firmware via OTA.")
    parser.add_argument("firmware", nargs="?", default=default_firmware_path(),
                        help="firmware .imfw (default: firmware/build/immurok_CH592F.imfw)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="skip the confirmation prompt")
    parser.add_argument("--adapter", default=None,
                        help="BLE adapter (e.g. hci0); default = system default")
    args = parser.parse_args()

    global BleakClient, BleakScanner
    try:
        from bleak import BleakClient as _Client, BleakScanner as _Scanner
    except ImportError:
        print("Error: bleak not installed.  Run:  pip install bleak", file=sys.stderr)
        return 1
    BleakClient, BleakScanner = _Client, _Scanner

    if not os.path.isfile(args.firmware):
        print(f"Error: file not found: {args.firmware}", file=sys.stderr)
        if args.firmware == default_firmware_path():
            print("Hint: run ota/build-ota.sh first to produce the default .imfw",
                  file=sys.stderr)
        return 1

    with open(args.firmware, "rb") as f:
        imfw = parse_imfw(f.read())
    if imfw is None:
        print("Error: not a valid .imfw (need ota-package.py output).", file=sys.stderr)
        return 1

    fw_size = len(imfw["firmware"])
    print(f"Firmware: {args.firmware}")
    print(f"  Format v{imfw['version']}  HW 0x{imfw['hw_id']:04X}  "
          f"plaintext {imfw['fw_size']} B  encrypted {fw_size} B")
    if fw_size == 0 or fw_size > IMAGE_B_SIZE:
        print(f"Error: bad firmware size {fw_size} (must be 1..{IMAGE_B_SIZE}).",
              file=sys.stderr)
        return 1

    device = await find_device(args.adapter)
    if device is None:
        print("Error: no immurok device found. Make sure it's powered/advertising "
              "and the immurok daemon is stopped (it holds the connection).",
              file=sys.stderr)
        return 1

    rc = await run_migration(device, imfw, args.adapter, args.yes)
    if rc == 0:
        print("\nMigration complete. Wait ~20s for reboot, then the device exposes "
              "the new GATT UUIDs — restart the daemon and re-pair if needed.")
    return rc


def main():
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
