"""对照实验：任务中心内**完全不点击**，观察 Badcase 反馈问卷是否自发弹出。

目的（不臆测）：确认「问题反馈/Badcase 问卷」到底是
  (a) 我们盲点命中 banner 的后果，还是
  (b) QQ 自身周期性行为。
做法：进入任务中心后**不做任何 tap**，每 8 秒采样一次 OCR 覆盖层判定，共 ~3 分钟。
若期间问卷自发出现（零点击）→ 判定为 (b) QQ 自身行为；若始终不出现 → 倾向 (a)。

用法: py -3.13 -u scripts_test/probe_overlay_idle.py [机器人昵称]
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import Flow  # noqa: E402


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    name = sys.argv[1] if len(sys.argv) > 1 else "代柯"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        print("!! 设备不在线")
        return 1
    flow = Flow(cfg, ui)

    print("=== Badcase 问卷「零点击」对照实验 ===")
    print("机器人=%s  时长=%ds  期间**不执行任何 tap**" % (name, seconds))
    if not flow._enter_taskcenter(name):
        print("!! 进入任务中心失败")
        flow._safe_back_to_robot_list()
        return 1
    print("已进入任务中心，_page_tc =", flow._page_tc)

    t0 = time.time()
    hit_t = None
    n = 0
    while time.time() - t0 < seconds:
        n += 1
        el = int(time.time() - t0)
        bad = flow._badcase_visible()
        ai = flow._ai_friend_page_visible()
        ver = flow._overlay_visible_in_top()
        print("  +%3ds  #%02d  badcase_full=%-5s aiH5=%-5s overlay_top=%-5s"
              % (el, n, bad, ai, ver))
        if (bad or ai) and hit_t is None:
            hit_t = el
            flow._diag_shot("idle_overlay")
        time.sleep(8)

    print("\n结论：%s" % ("问卷在**零点击**下自发出现（+%ds）→ 属 QQ 自身行为" % hit_t
                        if hit_t is not None else
                        "全程未出现 → 与盲点相关（需继续排查）"))
    # 收尾：把可能残留的覆盖层清掉再退出
    flow._dismiss_badcase()
    flow._exit_taskcenter()
    return 0


if __name__ == "__main__":
    sys.exit(main())
