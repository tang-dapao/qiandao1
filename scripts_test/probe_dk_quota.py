"""代柯满额配额不直退现场取证探针（2026-09-10）。

背景：`py -3.13 main.py --ad-only --robots 代柯` 实测 4m52s 全失败：
代柯广告已满（用户确认 10/10），但进台 `_read_ad_ratio` 读不到行 →
未直退 → `_dismiss_badcase` 反复"退出"问卷 → `_find_row("获取随机")` 空滚 28s ×3。

本探针回答三个问题（按 F11 原则，只读屏/节点点击，不盲点任何坐标）：
  1. 进入代柯任务中心后，屏幕是什么？问卷是否在场？
  2. dump 里「获取随机」行的真实文本/计数长什么样（满额后文案是否变了）？
  3. 若在问卷页：按一次物理 BACK，立即抓屏 —— 落在哪个页面（问卷还在？
     还是已回任务中心）？据此区分 dismiss 假阴性 vs 落点错误。

用法：py -3.13 scripts_test/probe_dk_quota.py
"""
import io
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import Flow  # noqa: E402

SHOTS = os.path.join(BASE, "screenshots")


def save_shot(flow, tag):
    img = flow._ocr_shot()
    if img is None:
        print("  [截图失败]")
        return None
    p = os.path.join(SHOTS, "probe_dk_%s.png" % tag)
    img.save(p)
    print("  [截图已存] %s" % p)
    return p


def show_nodes(ui, title):
    print("  ---- %s ----" % title)
    nodes = ui.nodes()
    print("  节点数: %d" % len(nodes))
    for n in nodes:
        print("    %r bounds=(%d,%d)-(%d,%d)" % (n.text, n.x1, n.y1, n.x2, n.y2))
    return nodes


def ocr_texts(flow, title):
    pos = flow._ocr_find("获取随机", "看广告", "每日签到", "收支详情",
                         "已看满", "Badcase", "反馈问卷", "开始填写",
                         retries=1)
    print("  [%s] 关键词命中坐标: %s" % (title, pos))


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    ui = AdbUI(cfg["device"]["udid"])
    flow = Flow(cfg, ui)

    print("== 0. 当前屏幕 ==")
    save_shot(flow, "00_now")

    print("== 1. 进入代柯任务中心 ==")
    ok = flow._enter_taskcenter("代柯")
    print("  _enter_taskcenter -> %s" % ok)
    time.sleep(2)
    save_shot(flow, "01_taskcenter")
    show_nodes(ui, "进入后 dump 节点")
    print("  问卷可见? %s" % flow._badcase_visible())
    print("  AI好友H5可见? %s" % flow._ai_friend_page_visible())
    print("  OCR 任务中心特征? %s" % flow._taskcenter_confirmed_by_ocr())
    ocr_texts(flow, "关键行词")
    ratio = flow._read_ad_ratio()
    print("  _read_ad_ratio -> %s" % (ratio,))

    if flow._badcase_visible() or flow._ai_friend_page_visible():
        print("== 2. 问卷在场 → 物理 BACK 一次，立即抓屏 ==")
        ui.back(pause=1.5)
        save_shot(flow, "02_after_back1")
        print("  问卷可见? %s" % flow._badcase_visible())
        print("  AI好友H5可见? %s" % flow._ai_friend_page_visible())
        print("  OCR 任务中心特征? %s" % flow._taskcenter_confirmed_by_ocr())
        show_nodes(ui, "BACK 后 dump 节点")
        print("  _read_ad_ratio -> %s" % (flow._read_ad_ratio(),))
    else:
        print("== 2. 无问卷，直接进入行取证 ==")

    print("== 3. 结论摘要 ==")
    print("  探针结束。重点看 01/02 两张截图与 dump 节点文本。")


if __name__ == "__main__":
    main()
