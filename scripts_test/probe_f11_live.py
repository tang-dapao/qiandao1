"""F11 实机探针：验证 banner 禁点区守卫在真实任务中心页面上的行为。

步骤（不臆测，全部在真机上跑）：
  1. 进入指定机器人的任务中心（走 flow 的真实导航）。
  2. 打印 _page_tc 状态位；dump 全节点，列出顶部区(y<500)节点 +
     任务行 y 坐标 —— 实证「banner 不在 dump」「任务行远在 y>1000」。
  3. 调用被守卫的 _tap(160,152)（历史事故坐标）→ 期望被拒绝。
  4. 复查屏幕：AI 好友 H5 未出现、仍在任务中心。
  5. 退出任务中心 → _page_tc 复位为 False。
  6. 存两张现场截图供人工核对。

用法: py -3.13 -u scripts_test/probe_f11_live.py [机器人昵称]
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import BANNER_TOP_STRIP, Flow  # noqa: E402


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    name = sys.argv[1] if len(sys.argv) > 1 else "代柯"
    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        print("!! 设备不在线")
        return 1
    flow = Flow(cfg, ui)

    print("=== F11 实机探针 (banner 禁点区) ===")
    print("BANNER_TOP_STRIP =", BANNER_TOP_STRIP)
    print("目标机器人 =", name)
    print("进入前 _page_tc =", flow._page_tc)

    if not flow._enter_taskcenter(name):
        print("!! 进入任务中心失败")
        flow._safe_back_to_robot_list()
        return 1
    print("进入后 _page_tc =", flow._page_tc, "(期望 True)")

    # ---- dump 分析 ----
    nodes = ui.nodes()
    print("\n-- dump 节点总数: %d --" % len(nodes))
    top = [(n.text, n.center) for n in nodes if n.text and (n.y1 + n.y2) // 2 < 500]
    print("顶部区(y<500) 有文字的节点: %s" % (top or "（空）"))
    labels = ("每日签到", "问题反馈", "看广告", "获取随机", "收支详情", "任务中心",
              "当前电量", "去充电")
    rows = [(n.text, n.center) for n in nodes if n.text in labels]
    print("关键行节点: %s" % rows)
    banner_nodes = [n for n in nodes
                    if n.text and (n.y1 + n.y2) // 2 < BANNER_TOP_STRIP[3]
                    and "AI好友" in n.text]
    print("banner 文案是否出现在 dump: %s" % ("是（意外）" if banner_nodes else "否（符合预期）"))

    flow._diag_shot("f11_tc_before")

    # ---- 守卫拒点实测 ----
    print("\n-- 守卫实测 A: _tap(160,152)（历史事故坐标）--")
    ret = flow._tap(160, 152)
    print("_tap(160,152) 返回 =", ret, "(期望 False=被拒绝)")
    time.sleep(1.0)
    print("-- 守卫实测 B: _tap(540,1200)（代柯版式 banner 纵区）--")
    ret2 = flow._tap(540, 1200)
    print("_tap(540,1200) 返回 =", ret2, "(期望 False=被拒绝)")
    time.sleep(1.5)
    ai = flow._ai_friend_page_visible()
    tc = flow._taskcenter_confirmed_by_ocr()
    print("AI 好友 H5 是否出现 =", ai, "(期望 False)")
    print("是否仍在任务中心   =", tc, "(期望 True)")
    flow._diag_shot("f11_tc_after")

    # 注：trusted=True 的放行路径就是广告页「关闭广告」真机路径，
    # 由后续真机跑广告时自然验证，这里不再做合成对照 tap（避免误点未知元素）。

    flow._exit_taskcenter()
    print("\n退出后 _page_tc =", flow._page_tc, "(期望 False)")
    print("=== 探针结束 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
