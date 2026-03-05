# OTA Firmware Update

## Flash Partition Layout

immurok uses the WCH "Method 1" OTA scheme with three partitions:

```
Address         Size    Name        Description
────────────────────────────────────────────────────
0x00000000      4 KB    JumpIAP     First-stage jump loader
0x00001000    216 KB    Image A     Running application firmware
0x00037000    216 KB    Image B     OTA staging area
0x0006D000     12 KB    IAP         Bootloader (copies B → A)
────────────────────────────────────────────────────
Total: 448 KB (CH592F full flash)
```

### Boot Sequence

```
Power on
   │
   ▼
JumpIAP (0x00000)
   │
   ├─ Check ImageFlag in DataFlash
   │
   ├─ Flag == OTA_PENDING ──► Jump to IAP (0x6D000)
   │                              │
   │                              ▼
   │                         Copy Image B → Image A
   │                         Clear ImageFlag
   │                         Jump to Image A
   │
   └─ Flag == NORMAL ──────► Jump to Image A (0x01000)
```

### Components

| Directory | Output | Max Size | Description |
|-----------|--------|----------|-------------|
| `jumpapp/` | `immurok_JumpIAP.bin` | 4 KB | Reads ImageFlag, jumps to IAP or App |
| `iap/` | `immurok_IAP.bin` | 12 KB | Copies Image B to Image A, then boots |

Both components share the main firmware's SDK (`../firmware/SDK/`).

## OTA Package Format (.imfw)

OTA images are encrypted and signed before transmission:

```
┌──────────────────────────────────┐
│ Header (64 B)                    │
│   Magic: "IMFW"                  │
│   HW ID, version, image size    │
│   AES IV (16 B)                  │
│   HMAC-SHA256 of header (32 B)  │
├──────────────────────────────────┤
│ Encrypted firmware data          │
│   AES-128-CTR(plaintext image)  │
├──────────────────────────────────┤
│ SHA256 of plaintext image (32 B)│
└──────────────────────────────────┘
```

| Layer | Algorithm | Key Size |
|-------|-----------|----------|
| Encryption | AES-128-CTR | 128-bit |
| Header signature | HMAC-SHA256 | 256-bit |
| Image integrity | SHA256 | 256-bit |

## Usage

### Build (compile all components + package)

```bash
# From project root:
ota/build-ota.sh release          # Production (no debug, sleep enabled)
ota/build-ota.sh release-debug    # Debug logs + sleep
ota/build-ota.sh debug            # Debug logs, no sleep
```

This builds JumpIAP, Application, and IAP, then combines them into a single flashable image and packages the `.imfw` OTA file.

### Flash (wired, via WCH-LinkE)

```bash
# From project root:
ota/upload-ota.sh release         # Build + flash combined image
ota/upload-ota.sh -f              # Flash only (skip build)
```

### OTA Update (wireless, via BLE)

```bash
python3 ota/ota-update.py firmware/build/immurok_CH592F.imfw
```

The companion app receives the `.imfw` file over BLE, writes it to Image B, sets the ImageFlag, and reboots the device. The IAP bootloader then copies Image B to Image A.

### Generate OTA Keys

Keys are per-machine and must not be committed:

```bash
pip3 install cryptography
python3 ota/generate_ota_keys.py
```

This generates:
- `firmware/APP/include/ota_keys.h` (C header for firmware)
- `ota/ota_keys.py` (Python keys for packaging/update scripts)
