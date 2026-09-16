"""主入口：QQ 机器人 每日签到/问题反馈/看广告 全自动流程（adb + uiautomator 版）。

用法:
  python main.py                              # 全自动：自动抓机器人列表 -> 签到+反馈 -> 看广告轮转
  python main.py --list                       # 仅自动抓取并列出当前机器人（不执行）
  python main.py --robots "昵称A,昵称B"         # 手动指定机器人（覆盖自动抓取）
  python main.py --no-signin --no-feedback     # 关闭签到/反馈（只看广告）
  python main.py --ad-times 5                  # 每台机器人看广告次数（默认取 config.yaml）
  python main.py --ad-only                     # 只看广告（跳过签到反馈）
  python main.py --rotate                     # 签到+反馈+组制轮询看广告
  python main.py --no-ad                       # 不看广告（只签到+反馈，等价 --ad-times 0）
"""
import argparse
import logging
import os
import sys

import yaml

from adb_ui import AdbUI
from flow import Flow

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict):
    level = getattr(logging, cfg["logging"].get("level", "INFO").upper())
    handlers = [logging.StreamHandler(sys.stdout)]
    logfile = os.path.join(BASE_DIR, cfg["logging"].get("file", "logs/run.log"))
    os.makedirs(os.path.dirname(logfile), exist_ok=True)
    handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def main():
    parser = argparse.ArgumentParser(description="QQ 机器人自动签到 + 看广告")
    parser.add_argument("--config", default=os.path.join(BASE_DIR, "config.yaml"))
    parser.add_argument("--robots", help="逗号分隔的机器人昵称，覆盖自动抓取")
    parser.add_argument("--list", action="store_true", help="仅列出自动抓取的机器人")
    parser.add_argument("--no-signin", action="store_true", help="跳过每日签到")
    parser.add_argument("--no-feedback", action="store_true", help="跳过问题反馈")
    parser.add_argument("--ad-only", action="store_true",
                        help="只看广告（跳过签到和反馈）")
    parser.add_argument("--ad-times", type=int, help="每台机器人看广告次数")
    parser.add_argument("--rotate", action="store_true",
                        help="看广告改为组制轮询（机器人按组跨台轮换，每组"
                             " ad_rotate_group 台；覆盖 config workflow."
                             "ad_rotate）")
    parser.add_argument("--no-ad", action="store_true",
                        help="不看广告（等价 --ad-times 0）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    logger = logging.getLogger("main")

    # 感知层后端选择（阶段2 2026-09-15）：device.backend = adb（默认）| u2
    # u2 = uiautomator2 3.x 设备端常驻 agent，dump 中位 94ms（adb 路径 4021ms，
    # 探针 scripts_test/probe_u2.py 实测 42.6x）。两 backend 互斥（u2 agent
    # 常驻会杀 adb dump），进程退出时 U2AdbUI atexit 自动 stop agent 让出通道。
    _backend = (cfg["device"].get("backend") or "adb").lower()
    if _backend == "u2":
        from adb_u2 import U2AdbUI
        ui = U2AdbUI(cfg["device"]["udid"])
        logger.info("感知层: u2 backend（设备端 agent 常驻，dump 毫秒级）")
    else:
        ui = AdbUI(cfg["device"]["udid"])
    # 入口探活：设备/adb 不在线时直接退出，避免后续静默乱点兜底坐标
    if not ui.is_online():
        logger.error("设备 %s 不在线（模拟器未启动 / adb 断开），退出",
                     cfg["device"]["udid"])
        sys.exit(1)
    flow = Flow(cfg, ui)

    # 白名单（config workflow.robot_whitelist，用户维护）——需先于自动收集
    # 读取，供 #1 收集提前终止使用。
    wl = cfg["workflow"].get("robot_whitelist") or []

    # 确定机器人列表
    if args.robots:
        robots = [n.strip() for n in args.robots.split(",") if n.strip()]
        logger.info("使用命令行指定机器人: %s", robots)
    else:
        logger.info("自动抓取机器人列表...")
        # #1（优化 2026-09-09）：白名单模式按名单提前终止滚动收集
        #（常规 10 台名单在列表前 1~2 屏即可集齐，省滚动 dump 开销 ~40-60s）；
        # --list 需要完整列表，不做提前终止。
        stop_when = wl if not args.list else None
        robots = flow._collect_robot_names(stop_when=stop_when)
        if not robots:
            logger.error("自动抓取机器人列表为空，请先确认已进入 QQ 联系人->机器人 页面")
            return

    # 白名单过滤：仅对名单内昵称执行签到/反馈/看广告，其余一律跳过 —— 覆盖
    # 昵称重复、内测中、孤立噪声（如列表里的"1"）等无法操作的机器人。命令行
    # --robots 指定的名字同样受白名单约束；名单为空/未配置 = 不过滤（旧行为）。
    if wl:
        skipped = [r for r in robots if r not in wl]
        if skipped:
            logger.info("跳过白名单外机器人 %d 个: %s", len(skipped), skipped)
        robots = [r for r in robots if r in wl]
        if not robots:
            logger.error("白名单过滤后无机器人可处理（白名单 %d 个）", len(wl))
            return

    logger.info("最终处理 %d 个机器人: %s", len(robots), robots)

    if args.list:
        print("机器人列表 (%d):" % len(robots))
        for r in robots:
            print("  -", r)
        return

    do_signin = not args.no_signin and not args.ad_only
    do_feedback = not args.no_feedback and not args.ad_only
    ad_times = 0 if args.no_ad else args.ad_times

    # #6（QA 记录 LOW）：--no-ad + --ad-only 语义冲突 → 空跑（不签到/不反馈/
    # 不看广告）。不改行为（既有测试锁定"不抛异常"），但显式告警便于发现
    # 脚本写错。
    if args.no_ad and args.ad_only:
        logger.warning("--no-ad 与 --ad-only 同时指定：不看广告且不签到/反馈，"
                       "结果为空跑 —— 请检查启动脚本参数是否写错")

    # 看广告模式（2026-09-16）：rotate=组制轮询（每组 ad_rotate_group 台跨台
    # 轮换，每台每轮 1 支，靠切换吸收 CD，u2 实测 ~48-49s/支 vs 连看 ~89s）；
    # false=同机连看（旧行为）。--rotate 强制开启，否则取 config
    # workflow.ad_rotate（默认 false，现有 watch_ad.bat 行为零变化）。
    rotate = args.rotate or bool(cfg["workflow"].get("ad_rotate") or False)
    group = int(cfg["workflow"].get("ad_rotate_group") or 3)
    logger.info("开始全自动流程: 签到=%s 反馈=%s 看广告次数=%s 模式=%s",
                do_signin, do_feedback,
                ad_times if ad_times is not None else "默认",
                ("组制轮询(每组%d台)" % group) if rotate else "同机连看")
    flow.run_all(robots, do_signin, do_feedback, ad_times,
                 rotate=rotate, group=group)
    logger.info("全自动流程结束")


if __name__ == "__main__":
    main()
