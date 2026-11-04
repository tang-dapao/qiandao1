"""F11 实测验证（真机现场帧，不连真机、不做臆测）。

验证 2026-09-10 F11 两项改动在**真实设备帧**上的行为：

A) 覆盖层闸门 `_overlay_visible_in_top()`（问题反馈 / 静默假成功防线）
   - `screenshots/fail_overlay_*.png`：**逐帧按内容定期望值**（见下方 A1 说明）
   - 真·任务中心帧（trace/ocr_002,006..011 / trace_yj1/ocr_001,002）→ 必须全 False
   - 真·广告帧（trace/ocr_003..005）→ 必须全 False（不得误报）
   - 真·AI 好友帮助中心 H5 帧（trace/ocr_012..021）→ 允许 False（由 step1 兜住）

   A1（2026-11-03 修，用户需求）：`fail_overlay_*` 这批帧**并不都是问卷**。
   实测 299 帧里只有 23 帧真含问卷词，其余 276 帧是**个人资料卡**
   （有「她的QQ空间/语音通话/发消息」）。旧版对整批一刀切「期望 True」，
   导致 92% 恒定不符 —— 既刷屏，又让真实回归淹没在噪声里（假红掩盖真红）。
   现在改为**逐帧归类后分别断言**：
     ① 全屏命中问卷词 `Flow._BADCASE_KEYS`            → 归类「问卷」，期望 True
     ② 全屏命中资料卡词 `Flow._PROFILE_KEYS` 或非问卷
        标记（`QQ:` —— 问卷页不会有 QQ 号标签）        → 归类「非问卷」，期望 False
     ③ 两类都没命中                                    → 「未归类」，**只统计不判定**
        （宁可显式列出待人工归类，也不猜一个期望值制造假绿）
   ⚠️ 归类信号与闸门信号**刻意不同源**：归类扫**全屏**且用 `_BADCASE_KEYS` 全集，
   而闸门只扫顶部 `AD_TOP_REGION` 且只认 `_OVERLAY_TOP_KEYS` 两词。所以
   「问卷词只出现在屏幕下半部」这类真实回归仍然会被判定为不符，不会被归类逻辑
   一起放过去。归类结果缓存到 `logs/f11_frame_labels.json`（按文件名 + mtime
   失效），后续重复跑无需再 OCR。

B) banner 根治守卫 `_tap(..., trusted)`
   - 任务中心页 → 任何盲点坐标点击都被拒绝（含 y=152 与 y=1200 两种版式）
   - trusted=True → 放行（广告页关闭按钮 / 签到浮层）
   - 非任务中心页（列表） → 放行（机器人首行 y≈340 必须仍可点）

用法: py -3.13 -u scripts_test/verify_f11.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from flow import AD_TOP_REGION, BANNER_TOP_STRIP, Flow  # noqa: E402

SS = os.path.join(BASE, "screenshots")
LABELS_CACHE = os.path.join(BASE, "logs", "f11_frame_labels.json")
# 非问卷标记：腾讯问卷页不会出现 QQ 号标签，而资料卡/聊天页/列表页基本都有。
# 用途仅在**归类**（定期望值），绝不参与生产判定。
NON_QUESTIONNAIRE_MARKS = ("QQ:",)


def build_flow(cfg):
    f = Flow.__new__(Flow)
    f._init_cache_state()
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


# ---------------------------------------------------------------------------
# A1：fail_overlay_* 逐帧归类（2026-11-03）
# ---------------------------------------------------------------------------
def _tokens(img, lang):
    """整图 OCR → [(词元, x中心, y中心)]，与 `Flow._fullscreen_scan4` 同构。"""
    import pytesseract
    data = pytesseract.image_to_data(img, lang=lang,
                                     output_type=pytesseract.Output.DICT)
    out = []
    for i in range(len(data["text"])):
        t = (data["text"][i] or "").strip()
        if not t:
            continue
        out.append((t,
                    data["left"][i] + data["width"][i] // 2,
                    data["top"][i] + data["height"][i] // 2))
    return out


def _hit(f, toks, keys):
    """词元整词命中 或 跨词元拼接命中（判据同 `_ocr_find`）。"""
    for t, _x, _y in toks:
        for k in keys:
            if k in t:
                return k
    m = f._merged_match(toks, keys, None)
    return m if m else ""


def classify_frame(f, path, lang):
    """返回 ('问卷'|'非问卷'|'未归类', 依据)。问卷优先。"""
    toks = _tokens(Image.open(path).convert("RGB"), lang)
    q = _hit(f, toks, Flow._BADCASE_KEYS)
    if q:
        return "问卷", str(q)
    p = _hit(f, toks, Flow._PROFILE_KEYS)
    if p:
        return "非问卷", "资料卡:%s" % (p,)
    n = _hit(f, toks, NON_QUESTIONNAIRE_MARKS)
    if n:
        return "非问卷", "非问卷标记:%s" % (n,)
    return "未归类", "无问卷/资料卡/QQ号特征（%d 词元）" % len(toks)


def load_labels(f, files, lang, use_cache=True):
    """按 mtime 缓存归类结果，命中则不重 OCR。返回 {basename: (标签, 依据)}。"""
    cache = {}
    if use_cache and os.path.exists(LABELS_CACHE):
        try:
            cache = json.load(open(LABELS_CACHE, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[缓存读取失败，将重新归类] %s" % e)
            cache = {}
    labels, dirty, n_ocr = {}, False, 0
    for i, p in enumerate(files, 1):
        name = os.path.basename(p)
        mt = os.path.getmtime(p)
        hit = cache.get(name)
        if hit and abs(float(hit.get("mtime", 0)) - mt) < 1 and hit.get("label"):
            labels[name] = (hit["label"], hit.get("why", ""))
            continue
        lb, why = classify_frame(f, p, lang)
        labels[name] = (lb, why)
        cache[name] = {"mtime": mt, "label": lb, "why": why}
        dirty = True
        n_ocr += 1
        if n_ocr % 20 == 0:
            print("    [归类中] %d/%d …" % (i, len(files)))
    if dirty:
        try:
            os.makedirs(os.path.dirname(LABELS_CACHE), exist_ok=True)
            json.dump(cache, open(LABELS_CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception as e:  # noqa: BLE001
            print("[缓存写入失败，忽略] %s" % e)
    return labels


def main():
    import pytesseract
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    pytesseract.pytesseract.tesseract_cmd = cfg["ocr"]["tesseract_cmd"]
    f, frame, calls = build_flow(cfg)

    print("BANNER_TOP_STRIP = %s   AD_TOP_REGION = %s\n" % (BANNER_TOP_STRIP,
                                                            AD_TOP_REGION))

    # ---- A) 覆盖层闸门 ----
    print("== A) 覆盖层闸门 _overlay_visible_in_top() ==")
    all_ok = True

    # A1：fail_overlay_* 逐帧归类后断言（旧版一刀切期望 True，92% 恒不符）
    overlay_frames = collect("fail_overlay_*.png")
    n_mismatch = n_unknown = 0
    if not overlay_frames:
        print("  [跳过] fail_overlay_* —— 无帧")
    else:
        labels = load_labels(f, overlay_frames, cfg["ocr"]["lang"])
        buckets = {"问卷": [], "非问卷": [], "未归类": []}
        for p in overlay_frames:
            buckets[labels[os.path.basename(p)][0]].append(p)

        print("  归类（按内容定期望值，A1 2026-11-03）：问卷 %d / 非问卷 %d / "
              "未归类 %d" % (len(buckets["问卷"]), len(buckets["非问卷"]),
                             len(buckets["未归类"])))
        for group, expect, expect_txt in (("问卷", True, "True"),
                                          ("非问卷", False, "False")):
            if not buckets[group]:
                continue
            print("  · %s组（期望 %s）" % (group, expect_txt))
            for p in buckets[group]:
                frame["path"] = p
                f._invalidate_shot_cache()
                got = f._overlay_visible_in_top()
                ok = (got == expect)
                if not ok:
                    n_mismatch += 1
                    all_ok = False
                    why = labels[os.path.basename(p)][1]
                    print("    %-34s -> %-5s !! 期望 %s（依据 %s）"
                          % (os.path.basename(p), got, expect_txt, why))
        if buckets["未归类"]:
            n_unknown = len(buckets["未归类"])
            print("  · 未归类 %d 帧（**不判定**，需人工归类；列前 5）：" % n_unknown)
            for p in buckets["未归类"][:5]:
                print("    %-34s 依据：%s"
                      % (os.path.basename(p), labels[os.path.basename(p)][1]))
        if not n_mismatch:
            print("    （%d 帧全部符合，无不符）" % (len(overlay_frames) - n_unknown))

    # 固定帧组：任务中心 / 广告 / AI 好友 H5
    fixed = [
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
    for title, files, expect in fixed:
        if not files:
            print("  [跳过] %s —— 无帧" % title)
            continue
        print("  %s" % title)
        for p in files:
            frame["path"] = p
            f._invalidate_shot_cache()
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

    a_txt = "全部符合" if all_ok else "存在不符"
    if n_unknown:
        a_txt += "（另有 %d 帧未归类，未判定）" % n_unknown
    print("\n结论：A) %s   B) %s" % (a_txt,
                                   "全部符合" if all([b1, b2, b3, b4]) else "存在不符"))


if __name__ == "__main__":
    main()
