"""离线回放现场帧，验证 OCR 页面判定逻辑（诊断/回归用，不连真机）。

用途：`scripts_test/trace_ad.py` 跑一次会把每次 OCR 实际看到的画面存到
screenshots/trace/ocr_NNN.png。本脚本把这些帧喂给**当前真实**的
`Flow._ocr_find` / `_taskcenter_confirmed_by_ocr` / `_ai_friend_page_visible`，
从而在不重跑真机的前提下判断「判定会不会正确翻转」。

典型用法：
    # 默认回放 screenshots/trace
    py -3.13 scripts_test/replay_frames.py
    # 指定多个现场目录（F8 修复回归：广告帧应能读到关闭按钮，任务中心帧应判 YES）
    py -3.13 scripts_test/replay_frames.py screenshots/trace screenshots/trace_yj1 screenshots/trace_y3
"""
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from flow import AD_TOP_REGION, Flow  # noqa: E402

FRAMES = os.path.join(BASE, "screenshots", "trace")


def build_flow(cfg):
    """构造只做 OCR 判定的 Flow 外壳（不碰 adb）。"""
    f = Flow.__new__(Flow)
    f._ocr_lang = cfg["ocr"]["lang"]
    f.wf = {}
    f.t = {}
    _frame = {"path": None}

    def _shot():
        p = _frame["path"]
        return Image.open(p).convert("RGB") if p else None

    f._ocr_shot = _shot
    return f, _frame


def _collect(argv):
    """收集要回放的帧：给了命令行目录就用它们，否则用默认 FRAMES。"""
    dirs = argv or [FRAMES]
    files = []
    for d in dirs:
        p = d if os.path.isabs(d) else os.path.join(BASE, d)
        got = sorted(glob.glob(os.path.join(p, "ocr_*.png")))
        if not got:
            print("  [跳过] %s 无 ocr_*.png" % d)
        files.extend((d, g) for g in got)
    return files


def main():
    import pytesseract
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    pytesseract.pytesseract.tesseract_cmd = cfg["ocr"]["tesseract_cmd"]

    files = _collect(sys.argv[1:])
    if not files:
        print("没有可用帧，请先跑 scripts_test/trace_ad.py")
        return
    f, frame = build_flow(cfg)
    print("AD_TOP_REGION = %s（F8 修复后收到 320）" % (AD_TOP_REGION,))
    print("%-14s %-22s %-10s %-10s %s" % (
        "帧", "目录", "任务中心", "AI好友H5", "关闭按钮(_ad_close_pos)"))
    tc_hit = ad_hit = tc_total = ad_total = 0
    for d, p in files:
        frame["path"] = p
        tc = f._taskcenter_confirmed_by_ocr()
        ai = f._ai_friend_page_visible()
        pos = f._ad_close_pos()
        # F8 语义：任务中心帧必须 pos is None（不点固定坐标，否则盲点命中 banner）；
        # 广告帧期望 pos 有值（正向定位成功）。
        if tc:
            tc_total += 1
            tc_hit += 1 if pos is None else 0
        else:
            ad_total += 1
            ad_hit += 1 if pos else 0
        print("%-14s %-22s %-10s %-10s %s" % (
            os.path.basename(p),
            d,
            "YES" if tc else "-",
            "YES" if ai else "-",
            ("%s ✓可点" % (pos,)) if pos else "None（F8:不盲点）"))
    print("-" * 78)
    if tc_total:
        print("任务中心帧：%d/%d 判 YES 且不返回坐标（F8 防 banner 误触基准）"
              % (tc_hit, tc_total))
    if ad_total:
        print("广告帧　　：%d/%d 正向读到关闭按钮（F8 OCR 直关可用率）"
              % (ad_hit, ad_total))


if __name__ == "__main__":
    main()
