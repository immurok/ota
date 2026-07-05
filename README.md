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

### v2 (firmware 1.6.0+, current)

OTA images are encrypted, then the header is signed with ECDSA P-256.
The device verifies the signature on-chip (uECC) before accepting an image:

```
┌────────────────────────────────────────────┐
│ Header prefix (64 B)                       │
│   0x00  Magic "IMFW"                       │
│   0x04  Format version (0x02)              │
│   0x06  Hardware ID                        │
│   0x08  Firmware size                      │
│   0x0C  Security version (SVN)             │
│   0x10  AES IV (16 B)                      │
│   0x20  SHA256 of plaintext image (32 B)   │
├────────────────────────────────────────────┤
│ ECDSA P-256 signature over prefix (64 B)   │
│   raw r ‖ s, verified on-device            │
├────────────────────────────────────────────┤
│ 0x80: AES-128-CTR encrypted firmware data  │
└────────────────────────────────────────────┘
```

| Layer | Algorithm | Notes |
|-------|-----------|-------|
| Encryption | AES-128-CTR | 128-bit key |
| Header signature | ECDSA P-256 (SHA256 digest) | verified on-chip before flashing |
| Image integrity | SHA256 | checked after decryption |
| Anti-rollback | SVN (security version) | device refuses images below its SVN floor |

### v1 (firmware ≤1.5.x, legacy)

Same layout with a 32 B HMAC-SHA256 header signature instead of ECDSA
(96 B total header, payload at 0x60). Firmware 1.6.0 acts as a bridge:
it is the last HMAC-verified update a legacy device accepts; from then on
only ECDSA-signed v2 images are accepted.

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

### Release Tooling

```bash
ota/release-web.sh              # Build release .imfw + stage website firmware
                                # distribution (manifest.json + assets) in ota/web-dist/
ota/release-github.sh           # Build release .imfw + publish it as a GitHub
                                # Release asset (build is local; needs SDK + keys)
ota/deploy-fw.sh                # End-to-end web release: build, stage manifest,
                                # copy to website/fw/, deploy site, verify live
python3 ota/test_imfw_v2.py     # Packaging self-tests for the v2 (ECDSA) format
```
