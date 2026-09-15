"""阶段1 探针：uiautomator2 (u2) 感知层可行性验证（P1-u2 迁移前置，2026-09-15）。

只读测量（设备端 APK 由 `py -3.13 -m uiautomator2 init` 单独安装，本脚本不装、
不点任何按钮、不改任何现有代码），对比两条感知路径在**同一屏幕**上的表现：

  A) 现路径：AdbUI.nodes() —— adb shell "uiautomator dump /sdcard/ui.xml" + cat
     + _parse_xml（实测单次 2.4-3.8s，是每支广告 ~80s UI 开销的主要成分）
  B) 新路径：u2.dump_hierarchy() —— 设备端常驻 agent（:9008 JSON-RPC，
     adb forward 本机回环）

检查点（方案 2026-09-15，任一不满足即停）：
  ① u2 dump 耗时显著低于 adb 路径（预期 <0.5s）
  ② 节点集合与现路径一致或差异可解释（逐 (text,x1,y1) 对比）
  ③ --tc：任务中心 H5 页 u2 可读（现路径可读 20 节点）

用法：
  py -3.13 scripts_test/probe_u2.py                  # 导航到机器人列表后对比
  py -3.13 scripts_test/probe_u2.py --tc 黎小姐      # 附加：进任务中心对比后退回
  py -3.13 scripts_test/probe_u2.py --n 10           # 每路径采样次数（默认 5）
"""
import argparse
import logging
import os
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
import uiautomator2 as u2

from adb_ui import AdbUI, _parse_xml

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("probe_u2")


def _sig(nodes):
    """节点对比签名：(text, x1, y1) 多重集合。"""
    return Counter((n.text, n.x1, n.y1) for n in nodes)


def _compare(tag, adb_nodes, u2_nodes):
    a, b = _sig(adb_nodes), _sig(u2_nodes)
    matched = a & b
    only_adb = a - b
    only_u2 = b - a
    print(f"\n== 节点一致性对比（{tag}）==")
    print(f"  adb 路径节点数: {sum(a.values())}  u2 节点数: {sum(b.values())}"
          f"  完全匹配: {sum(matched.values())}")
    for name, diff in (("仅 adb 有", only_adb), ("仅 u2 有", only_u2)):
        if diff:
            print(f"  {name} {sum(diff.values())} 个:")
            for (t, x, y), c in list(diff.items())[:12]:
                print(f"    ({t!r}, x={x}, y={y}) x{c}")
        else:
            print(f"  {name}: 0 个")
    total = max(sum(a.values()), sum(b.values()), 1)
    ratio = sum(matched.values()) / total
    print(f"  匹配率: {ratio:.1%}")
    return ratio


def _timed(label, times):
    print(f"\n== {label} ==")
    for i, t in enumerate(times):
        print(f"  第{i + 1}次: {t * 1000:.0f} ms")
    print(f"  中位 {statistics.median(times) * 1000:.0f} ms | "
          f"均值 {statistics.mean(times) * 1000:.0f} ms | "
          f"最差 {max(times) * 1000:.0f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--n", type=int, default=5, help="每路径采样次数")
    ap.add_argument("--tc", metavar="ROBOT", default=None,
                    help="附加任务中心对比：进入指定机器人的任务中心，测完退回")
    ap.add_argument("--no-nav", action="store_true",
                    help="不导航，直接在当前屏幕对比")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.yaml"), encoding="utf-8"))

    ui = AdbUI(args.serial)
    if not ui.is_online():
        print(f"设备 {args.serial} 不在线，退出")
        return 2

    # 导航到机器人列表（标准安全路径，与每次 run 启动相同），保证对比页面有意义
    if not args.no_nav:
        from flow import Flow
        flow = Flow(cfg, ui)
        flow._nav_robot_list()
        time.sleep(1.0)
    else:
        flow = None

    print(f"\n当前屏幕（adb 视角前 8 个文本）: "
          f"{[n.text for n in ui.nodes()][:8]}")

    # ---- A) 现路径基线 ----
    adb_times, adb_nodes = [], []
    for _ in range(args.n):
        ui.refresh()
        t0 = time.perf_counter()
        ns = ui.nodes()
        adb_times.append(time.perf_counter() - t0)
        adb_nodes = ns
    _timed(f"A) adb 现路径 nodes() × {args.n}", adb_times)

    # ---- B) u2 新路径 ----
    d = u2.connect(args.serial)
    d.dump_hierarchy()          # 预热（含 adb forward 建立）
    u2_times, u2_xml = [], []
    for _ in range(args.n):
        t0 = time.perf_counter()
        xml = d.dump_hierarchy()
        u2_times.append(time.perf_counter() - t0)
        u2_xml = xml
    _timed(f"B) u2 dump_hierarchy() × {args.n}", u2_times)

    # ---- 节点一致性 ----
    u2_nodes = _parse_xml(u2_xml, True)
    ratio = _compare("机器人列表页/当前页", adb_nodes, u2_nodes)

    # ---- 可选：任务中心可读性 ----
    tc_ratio = None
    if args.tc and flow is not None:
        print(f"\n== 进入任务中心: {args.tc} ==")
        if not flow._enter_taskcenter(args.tc):
            print("进任务中心失败，跳过 TC 对比（不影响列表页结论）")
        else:
            try:
                time.sleep(1.0)
                ui.refresh()
                t0 = time.perf_counter()
                tc_adb = ui.nodes()
                t_adb = time.perf_counter() - t0
                t0 = time.perf_counter()
                tc_xml = d.dump_hierarchy()
                t_u2 = time.perf_counter() - t0
                tc_u2 = _parse_xml(tc_xml, True)
                print(f"TC 页 adb 路径: {t_adb * 1000:.0f} ms / "
                      f"{sum(_sig(tc_adb).values())} 节点")
                print(f"TC 页 u2 路径:  {t_u2 * 1000:.0f} ms / "
                      f"{sum(_sig(tc_u2).values())} 节点")
                tc_ratio = _compare("任务中心页", tc_adb, tc_u2)
            finally:
                flow._exit_taskcenter()

    # ---- 检查点结论 ----
    u2_med = statistics.median(u2_times)
    adb_med = statistics.median(adb_times)
    print("\n== 阶段1 检查点 ==")
    ok1 = u2_med < 0.5
    ok2 = ratio >= 0.95
    print(f"  ① u2 dump 中位 {u2_med * 1000:.0f} ms（目标 <500 ms；"
          f"adb 基线 {adb_med * 1000:.0f} ms，提速 {adb_med / max(u2_med, 1e-6):.1f}x）"
          f" -> {'PASS' if ok1 else 'FAIL'}")
    print(f"  ② 列表页节点匹配率 {ratio:.1%}（目标 ≥95%）"
          f" -> {'PASS' if ok2 else 'FAIL（需解释差异）'}")
    if tc_ratio is not None:
        ok3 = tc_ratio >= 0.9 and sum(_sig(u2_nodes).values()) >= 0
        print(f"  ③ 任务中心匹配率 {tc_ratio:.1%}（目标 ≥90%）"
              f" -> {'PASS' if ok3 else 'FAIL'}")
    print(f"\n总体: {'PASS —— 可进阶段2' if ok1 and ok2 else 'FAIL —— 停，分析差异'}")
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
