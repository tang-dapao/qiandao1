"""F11 实测验证（真机现场帧，不连真机、不做臆测）。

验证 2026-09-10 F11 两项改动在**真实设备帧**上的行为：

A) 覆盖层闸门 `_overlay_visible_in_top()`（问题反馈 / 静默假成功防线）
   - 真·Badcase 反馈问卷帧（screenshots/fail_overlay_*.png）→ 必须全部 True
   - 真·任务中心帧（trace/ocr_002,006..011 / trace_yj1/ocr_001,002）→ 必须全 False
   - 真·广告帧（trace/ocr_003..005）→ 必须全 False（不得误报）
   - 真·AI 好友帮助中心 H5 帧（trace/ocr_012..021）→ 允许 False（由 step1 兜住）

B) banner 根治守卫 `_tap(..., trusted)`
   - 任务中心页 → 任何盲点坐标点击都被拒绝（含 y=152 与 y=1200 两种版式）
   - trusted=True → 放行（广告页关闭按钮 / 签到浮层）
   - 非任务中心页（列表） → 放行（机器人首行 y≈340 必须仍可点）

用法: py -3.13 -u scripts_test/verify_f11.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from flow import AD_TOP_REGION, BANNER_TOP_STRIP, Flow  # noqa: E402

SS = os.path.join(BASE, "screenshots")


def build_flow(cfg):
    f = Flow.__new__(Flow)
    f._ocr_lang = cfg["ocr"]["lang"]
    f.wf = {}
    f.t = {"click_min": 1.5, "click_max": 3.0}
    f._page_tc = True
    frame = {"path": None}

    def _shot():
        return Image.open(frame["path"]).convert("RGB") if frame["path"] else None

    f._ocr_shot = _shot
    # 记录 tap 而不真正执行（不连真机）
    calls = []
    f.ui = type("U", (), {"tap": lambda self, x, y, pause=None: calls.append((x, y))})()
    return f, frame, calls


def collect(*globs):
    out = []
    for g in globs:
        import glob as _g
        out.extend(sorted(_g.glob(os.path.join(SS, g))))
    return out


def main():
    import pytesseract
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    pytesseract.pytesseract.tesseract_cmd = cfg["ocr"]["tesseract_cmd"]
    f, frame, calls = build_flow(cfg)

    print("BANNER_TOP_STRIP = %s   AD_TOP_REGION = %s\n" % (BANNER_TOP_STRIP,
                                                            AD_TOP_REGION))

    # ---- A) 覆盖层闸门 ----
    cases = [
        ("真·Badcase 反馈问卷 (期望 True)", collect("fail_overlay_*.png"), True),
        ("真·任务中心 (期望 False)", collect("trace/ocr_002.png", "trace/ocr_006.png",
                                       "trace/ocr_007.png", "trace/ocr_008.png",
                                       "trace/ocr_009.png", "trace/ocr_010.png",
                                       "trace/ocr_011.png", "trace_yj1/ocr_001.png",
                                       "trace_yj1/ocr_002.png"), False),
        ("真·广告页 (期望 False)", collect("trace/ocr_003.png", "trace/ocr_004.png",
                                     "trace/ocr_005.png"), False),
        ("真·AI好友帮助中心 H5 (允许 False)", collect("trace/ocr_012.png",
                                            "trace/ocr_016.png",
                                            "trace/ocr_020.png"), False),
    ]
    print("== A) 覆盖层闸门 _overlay_visible_in_top() ==")
    all_ok = True
    for title, files, expect in cases:
        if not files:
            print("  [跳过] %s —— 无帧" % title)
            continue
        print("  %s" % title)
        for p in files:
            frame["path"] = p
            got = f._overlay_visible_in_top()
            ok = (got == expect)
            all_ok = all_ok and ok
            print("    %-34s -> %-5s %s" % (os.path.basename(p), got,
                                            "OK" if ok else "!! 期望 %s" % expect))

    # ---- B) banner 根治守卫 ----
    print("\n== B) banner 根治守卫 _tap()（任务中心页禁止盲点坐标点击）==")
    def check(desc, page_tc, x, y, trusted, expect_tap):
        f._page_tc = page_tc
        del calls[:]
        ret = f._tap(x, y, pause=0.0, trusted=trusted)
        tapped = bool(calls)
        ok = (tapped == expect_tap) and (ret == expect_tap)
        print("  %-56s -> tap=%-5s %s" % (desc, tapped, "OK" if ok else "!! 不符"))
        return ok

    b1 = check("任务中心页 tap(160,152) 盲点（期望拒绝）", True, 160, 152, False, False)
    b2 = check("任务中心页 tap(540,1200) 代柯版式 banner 区（期望拒绝）",
               True, 540, 1200, False, False)
    b3 = check("任务中心页 tap(120,152) trusted=True（期望放行）",
               True, 120, 152, True, True)
    b4 = check("列表页 tap(200,340) 机器人首行（期望放行）", False, 200, 340, False, True)

    print("\n结论：A) %s   B) %s" % ("全部符合" if all_ok else "存在不符",
                                   "全部符合" if all([b1, b2, b3, b4]) else "存在不符"))


if __name__ == "__main__":
    main()
