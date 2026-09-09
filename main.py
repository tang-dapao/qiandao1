"""主入口：QQ 机器人 每日签到/问题反馈/看广告 全自动流程（adb + uiautomator 版）。

用法:
  python main.py                              # 全自动：自动抓机器人列表 -> 签到+反馈 -> 看广告轮转
  python main.py --list                       # 仅自动抓取并列出当前机器人（不执行）
  python main.py --robots "昵称A,昵称B"         # 手动指定机器人（覆盖自动抓取）
  python main.py --no-signin --no-feedback     # 关闭签到/反馈（只看广告）
  python main.py --ad-times 5                  # 每台机器人看广告次数（默认取 config.yaml）
  python main.py --ad-only                     # 只看广告（跳过签到反馈）
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
    parser.add_argument("--no-ad", action="store_true",
                        help="不看广告（等价 --ad-times 0）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    logger = logging.getLogger("main")

    ui = AdbUI(cfg["device"]["udid"])
    # 入口探活：设备/adb 不在线时直接退出，避免后续静默乱点兜底坐标
    if not ui.is_online():
        logger.error("设备 %s 不在线（模拟器未启动 / adb 断开），退出",
                     cfg["device"]["udid"])
        sys.exit(1)
    flow = Flow(cfg, ui)

    # 确定机器人列表
    if args.robots:
        robots = [n.strip() for n in args.robots.split(",") if n.strip()]
        logger.info("使用命令行指定机器人: %s", robots)
    else:
        logger.info("自动抓取机器人列表...")
        robots = flow._collect_robot_names()
        if not robots:
            logger.error("自动抓取机器人列表为空，请先确认已进入 QQ 联系人->机器人 页面")
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

    logger.info("开始全自动流程: 签到=%s 反馈=%s 看广告次数=%s",
                do_signin, do_feedback,
                ad_times if ad_times is not None else "默认")
    flow.run_all(robots, do_signin, do_feedback, ad_times)
    logger.info("全自动流程结束")


if __name__ == "__main__":
    main()
