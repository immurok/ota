#!/usr/bin/env python3
"""扫描附近的 immurok 设备，打印它们的 BLE 身份。

用途：验证双主机的槽位切换。设备每个槽用不同的 BLE 地址，因此切换后
macOS 会把它当作**另一台设备**，CoreBluetooth 的 peripheral UUID 随之改变。

macOS 不向应用暴露 BLE MAC（CoreBluetooth 只给不透明 UUID），且未配对的
设备不会出现在 system_profiler 里 —— 所以切到空槽之后，这个脚本是唯一
能看到「身份确实变了」的手段。

    python3 ota/scan_immurok.py          # 扫一次
    python3 ota/scan_immurok.py -w       # 持续扫，切换时实时看变化
"""
import argparse
import asyncio
import sys


async def scan_once(timeout: float):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=timeout)
    hits = [d for d in devices if (d.name or "").lower().startswith("immurok")]
    return hits


async def amain(watch: bool, timeout: float):
    seen = {}
    while True:
        hits = await scan_once(timeout)
        if not hits:
            print("(未发现 immurok 设备)")
        for d in hits:
            tag = "" if d.address in seen else "  ← 新身份"
            seen[d.address] = d.name
            print(f"{d.name:<16} {d.address}{tag}")
        if not watch:
            return 0
        print("-" * 52)


def main():
    ap = argparse.ArgumentParser(description="扫描 immurok 设备的 BLE 身份")
    ap.add_argument("-w", "--watch", action="store_true", help="持续扫描")
    ap.add_argument("-t", "--timeout", type=float, default=6.0, help="每轮扫描秒数")
    args = ap.parse_args()
    try:
        import bleak  # noqa: F401
    except ImportError:
        print("需要 bleak：pip3 install bleak")
        return 2
    try:
        return asyncio.run(amain(args.watch, args.timeout))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
