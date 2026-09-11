"""诊断：把设备导航到机器人列表并打印判据细节（不执行任何任务）。

用于排查 `_looks_like_robot_list()` 间歇性失败（要求在列表页时中部「机器人」
分类节点带 selected=true，实测偶发取不到）以及列表页 dump 内容。

用法：py -3.13 scripts_test/probe_list.py
"""
import logging
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import Flow  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        print("设备不在线")
        return
    f = Flow(cfg, ui)
    f._nav_robot_list()
    ui.refresh()
    print("\n===== 判据状态 =====")
    print("_looks_like_robot_list :", f._looks_like_robot_list())
    print("_on_qq_main_shell      :", f._on_qq_main_shell())
    print("_robot_cat_selected    :", f._robot_cat_selected())
    print("_contacts_tab_active   :", f._contacts_tab_active())
    print("\n===== dump 全节点（含 selected / 坐标）=====")
    for n in ui.nodes():
        t = (n.text or "").strip()
        if not t:
            continue
        print("  %-14s x1=%-5s x2=%-5s y1=%-5s y2=%-5s sel=%s" % (
            t, n.x1, n.x2, n.y1, n.y2, getattr(n, "selected", None)))


if __name__ == "__main__":
    main()
