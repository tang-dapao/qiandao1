"""专项验证 ymax 过滤：在真实广告页对比 全屏OCR(无ymax) vs 全屏OCR(ymax=400) 的命中坐标。

用法: py -3.13 scripts_test/verify_ymax.py <昵称>
流程: 进任务中心 -> 点广告 -> 等广告出现 -> 对比两种 OCR -> 手动关闭广告 -> 退出
"""
import logging
import sys
import time

import yaml

sys.path.insert(0, ".")
from adb_ui import AdbUI
from flow import Flow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                    stream=sys.stdout)
logger = logging.getLogger("verify_ymax")

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
ui = AdbUI(cfg["device"]["udid"])
flow = Flow(cfg, ui)

name = sys.argv[1] if len(sys.argv) > 1 else "古禹"

print("=" * 60)
print(f"进任务中心: {name}")
flow._enter_taskcenter(name)

print("点广告按钮...")
btn = flow._scroll_to_row("获取随机") or flow._scroll_to_row("看广告")
if not btn:
    print("未找到广告按钮，退出")
    sys.exit(1)
flow._tap_node(btn)

print("等待广告出现 (12s)...")
time.sleep(12)

print("-" * 60)
print("【对比】全屏 OCR 找「关闭广告/关闭/跳过」，无 ymax:")
pos_all = flow._ocr_find("关闭广告", "关闭", "跳过")
print(f"  -> 命中: {pos_all}")

print("-" * 60)
print("【对比】全屏 OCR 找「关闭广告/关闭/跳过」，ymax=400:")
pos_top = flow._ocr_find("关闭广告", "关闭", "跳过", ymax=400)
print(f"  -> 命中: {pos_top}")

print("-" * 60)
print(f"结论: 无ymax命中={pos_all} | ymax=400命中={pos_top}")
if pos_top and (pos_top[1] <= 400):
    print("✅ ymax=400 正确命中顶部关闭按钮")
elif pos_all and not pos_top:
    print("✅ 无ymax误命中正文(y>400)，ymax=400 正确过滤掉")
else:
    print("⚠️ 请人工判断上述坐标")

print("-" * 60)
print("手动关闭广告...")
flow._close_ad()
time.sleep(2)
print("退出任务中心...")
flow._exit_taskcenter()
print("DONE")
