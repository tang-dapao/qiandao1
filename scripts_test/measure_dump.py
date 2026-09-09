"""实测 uiautomator dump 分段耗时 —— 寻路优化的底层依据（优化方案 C）。

为什么需要：MuMu 上 uiautomator dump 单次 2~11.5s（广告播放期等 UI idle
超时 ~10s），是"进入任务中心 70~85s / 广告关闭轮询慢"的底层瓶颈。本工具
量化 dump 各段耗时，供决策：是否值得 --compressed / 调整 dump 频率 /
改变广告期轮询节奏。

用法:
  py -3.13 scripts_test/measure_dump.py                # 常规 3 轮
  py -3.13 scripts_test/measure_dump.py --rounds 5
  py -3.13 scripts_test/measure_dump.py --compressed   # 对比 --compressed

先决: 模拟器在线（默认取 config.yaml device.udid），QQ 停在任意稳定页面。
安全: 只读（dump/cat，不点击不滑动），可随时中断。
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from adb_ui import ADB  # noqa: E402


def load_device(config_path: str) -> str:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["device"]["udid"]


def probe(adb: str, dev: str) -> bool:
    p = subprocess.run([adb, "-s", dev, "get-state"],
                       capture_output=True, timeout=10)
    return p.returncode == 0 and p.stdout.strip() == b"device"


def measure(adb: str, dev: str, compressed: bool, rounds: int):
    flag = ["--compressed"] if compressed else []
    mode = "--compressed" if compressed else "常规"
    print(f"设备 {dev} | 模式 {mode} | {rounds} 轮\n")
    total = 0.0
    for i in range(rounds):
        t0 = time.time()
        p = subprocess.run(
            [adb, "-s", dev, "shell", "uiautomator", "dump"]
            + flag + ["/sdcard/ui.xml"],
            capture_output=True)
        t1 = time.time()
        ok = b"dumped to" in p.stdout
        if not ok:
            print(f"  第{i+1}轮: dump 失败 {t1-t0:.2f}s rc={p.returncode} "
                  f"out={p.stdout[:60]!r}")
            continue
        t2 = time.time()
        xml = subprocess.run([adb, "-s", dev, "shell", "cat", "/sdcard/ui.xml"],
                             capture_output=True).stdout
        t3 = time.time()
        cost = t1 - t0
        total += cost
        print(f"  第{i+1}轮: dump命令 {cost:.2f}s | cat {t3-t2:.2f}s | "
              f"xml {len(xml)/1024:.0f}KB | rc={p.returncode}")
    if total:
        print(f"\n  dump 平均耗时: {total / rounds:.2f}s")


def main():
    ap = argparse.ArgumentParser(description="测量 uiautomator dump 耗时")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--compressed", action="store_true",
                    help="对比 uiautomator dump --compressed 模式")
    ap.add_argument("--config", default=None,
                    help="config.yaml 路径（默认项目根目录）")
    args = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = args.config or os.path.join(base, "config.yaml")
    dev = load_device(cfg_path)
    adb = ADB

    if not probe(adb, dev):
        print(f"设备 {dev} 不在线，请先启动 MuMu 并等 adb devices 显示 device")
        sys.exit(1)
    measure(adb, dev, args.compressed, args.rounds)


if __name__ == "__main__":
    main()
