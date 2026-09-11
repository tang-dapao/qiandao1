"""决定性实验：看一次广告 → **只用物理 BACK 关闭（全程零坐标点击）** → 观察问卷。

目的（杜绝臆测）：区分「Badcase 反馈问卷」的来源
  (a) 我们某次坐标点击误触了 banner，还是
  (b) QQ 在看完广告后的自身行为。
做法：进任务中心 → 点「获取随机」(dump 节点，安全) → 等广告播完 →
      **只用 keyevent 4 (BACK) 退出广告页，不做任何坐标 tap** → 静默观察 150s。
若问卷仍出现 → 排除 (a)（本次没有任何坐标点击）→ 判定 (b)。

用法: py -3.13 -u scripts_test/probe_overlay_after_ad.py [机器人昵称]
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
    name = sys.argv[1] if len(sys.argv) > 1 else "古禹"
    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        print("!! 设备不在线")
        return 1
    flow = Flow(cfg, ui)
    taps = []

    # 记录所有 _tap 调用，最后断言"关广告阶段零坐标点击"
    real_tap = flow._tap
    def spy_tap(x, y, pause=None, trusted=False):
        taps.append((x, y))
        return real_tap(x, y, pause=pause, trusted=trusted)
    flow._tap = spy_tap

    print("=== 广告后问卷来源实验（BACK 关广告，零坐标点击）===")
    if not flow._enter_taskcenter(name):
        print("!! 进入任务中心失败")
        flow._safe_back_to_robot_list()
        return 1
    print("已进入任务中心，_page_tc =", flow._page_tc)

    row = flow._find_row("获取随机") or flow._find_row("看广告")
    if not row:
        print("!! 找不到 看广告 行")
        flow._exit_taskcenter()
        return 1
    btn = row[0]
    flow._tap_node(btn)                      # 节点点击（安全，非坐标）
    print("已点 获取随机（节点中心 %s），等待广告播放 26s" % (btn.center,))
    time.sleep(26)
    print("广告期 _back_at_taskcenter =", flow._back_at_taskcenter(),
          "(False=在广告页)")

    # 关广告：**只用 BACK**
    closed = False
    for i in range(1, 4):
        ui.back(pause=1.5)
        time.sleep(1.5)
        if flow._back_at_taskcenter():
            closed = True
            print("第 %d 次物理 BACK 后已回任务中心" % i)
            break
    print("BACK 关闭广告 %s" % ("成功" if closed else "失败（可能需点关闭按钮）"))
    print(">> 关广告阶段坐标点击次数 =", len(taps), "(期望 0)")

    # 静默观察
    print("静默观察 150s（零点击）…")
    t0 = time.time()
    hit = None
    while time.time() - t0 < 150:
        el = int(time.time() - t0)
        bad = flow._badcase_visible()
        ai = flow._ai_friend_page_visible()
        print("  +%3ds  badcase=%-5s aiH5=%-5s  坐标点击累计=%d"
              % (el, bad, ai, len(taps)))
        if (bad or ai) and hit is None:
            hit = el
            flow._diag_shot("after_ad_overlay")
        time.sleep(10)

    print("\n结论：")
    print("  关广告阶段坐标点击 = %d（若为 0，则问卷不可能来自我们的点击）" % len(taps))
    if hit is not None:
        print("  问卷在看广告后 +%ds **自发出现** → 属 QQ 自身行为 (b)" % hit)
    else:
        print("  150s 内问卷未出现 → 需继续排查")
    flow._dismiss_badcase()
    flow._exit_taskcenter()
    return 0


if __name__ == "__main__":
    sys.exit(main())
