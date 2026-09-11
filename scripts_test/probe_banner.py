"""取证：进入指定机器人任务中心，检查固定坐标 AD_CLOSE 落点上究竟是哪个节点。

目的：回答"盲点 (160,152) 为什么点到顶部 banner"——
  · 若该坐标上存在 **clickable** 节点且其 bounds 属顶部 banner 区 → 物证成立；
  · 同时打印该页 OCR 顶部条带结果（广告页应能读到"关闭"，任务中心应读不到）。

用法：py -3.13 scripts_test/probe_banner.py 游迦
"""
import logging
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import AD_CLOSE, AD_TOP_REGION, Flow  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    robot = sys.argv[1] if len(sys.argv) > 1 else "游迦"
    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        print("设备不在线")
        return
    f = Flow(cfg, ui)
    if not f._enter_taskcenter(robot):
        print("进入任务中心失败")
        return
    ui.refresh()
    nodes = ui.nodes()
    x, y = AD_CLOSE
    print("\n===== 任务中心页：AD_CLOSE=%s 落点分析 =====" % ((x, y),))
    print("屏内节点数 =", len(nodes))
    hit = []
    for n in nodes:
        if n.x1 <= x <= n.x2 and n.y1 <= y <= n.y2:
            hit.append(n)
    if not hit:
        print("  该坐标上没有节点（点击落到无节点区域 = WebView 自绘区域）")
    for n in hit:
        print("  命中节点: text=%r bounds=(%d,%d)-(%d,%d) clickable=%s desc=%r" % (
            n.text, n.x1, n.y1, n.x2, n.y2,
            getattr(n, "clickable", None), getattr(n, "desc", None)))
    print("\n  顶部区(y<430)可点击节点：")
    for n in nodes:
        if n.y1 < 430 and getattr(n, "clickable", False):
            print("    text=%r bounds=(%d,%d)-(%d,%d)" % (n.text, n.x1, n.y1, n.x2, n.y2))
    print("\n===== 同页 OCR 顶部条带（判据口径：关闭广告/关闭/跳过/取消, ymax=400）=====")
    pos = f._ocr_find("关闭广告", "关闭", "跳过", "取消", ymax=400, region=AD_TOP_REGION)
    print("  OCR 关闭按钮 =", pos if pos else "None（任务中心应恒为 None）")
    print("  任务中心判定 =", f._taskcenter_confirmed_by_ocr())
    f._diag_shot("probe_banner")
    f._exit_taskcenter()


if __name__ == "__main__":
    main()
