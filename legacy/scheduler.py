"""调度器：编排完整流程。

总体流程：
  [导航] 首页 -> 联系人 -> 机器人 -> 我添加的机器人
  阶段一: 对每个机器人：进入 -> 发消息 -> 个人 -> 任务中心 -> 签到 -> 去反馈
  阶段二: 看广告轮转（复用阶段一进入/离开机器人的函数）
"""
import logging

from ad_watcher import AdWatcher
from feedback import Feedback
from signin import Signin

logger = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, config: dict, locator, driver, robot_mgr):
        self.cfg = config
        self.locator = locator
        self.driver = driver
        self.robots = robot_mgr
        self.signin = Signin(config, locator, driver)
        self.feedback = Feedback(config, locator, driver)
        self.ad_watcher = AdWatcher(
            config, locator, driver,
            enter_robot=self._enter_robot,
            leave_robot=self._leave_robot,
        )

    # ----------------------------------------------------------
    # 导航基础
    # ----------------------------------------------------------
    def _go_to_robot_list(self):
        """从任意位置导航到「我添加的机器人」列表。"""
        timing = self.cfg["timing"]
        logger.info("导航到 联系人 -> 机器人 -> 我添加的机器人")
        self.locator.wait_and_tap("tab_contacts")
        self.driver.sleep(timing.get("page_wait", 2.5))
        self.locator.wait_and_tap("menu_robot")
        self.driver.sleep(timing.get("page_wait", 2.5))
        self.locator.wait_and_tap("my_robots")
        self.driver.sleep(timing.get("page_wait", 2.5))

    def _enter_robot(self, robot_name: str):
        """进入指定机器人的任务中心。"""
        timing = self.cfg["timing"]
        logger.info("进入机器人 %s 的任务中心", robot_name)
        # 点击机器人昵称
        self.locator.wait_and_tap("robot_name_text", text_override=robot_name)
        self.driver.sleep(timing.get("page_wait", 2.5))
        # 机器人首页 -> 发消息
        self.locator.wait_and_tap("btn_send_message")
        self.driver.sleep(timing.get("page_wait", 2.5))
        # 输入框上方 -> 个人
        self.locator.wait_and_tap("tab_personal")
        # 等待任务中心刷新
        self.driver.sleep(timing.get("taskcenter_wait", 3.0))

    def _leave_robot(self):
        """退出当前机器人，回到「我添加的机器人」列表。"""
        timing = self.cfg["timing"]
        logger.info("退出当前机器人")
        # 个人页/任务中心 -> 返回消息框 -> 返回机器人首页 -> 返回列表
        self.driver.back()
        self.driver.sleep(timing.get("page_wait", 2.0))
        self.driver.back()
        self.driver.sleep(timing.get("page_wait", 2.0))
        self.driver.back()
        self.driver.sleep(timing.get("page_wait", 2.0))

    # ----------------------------------------------------------
    # 阶段一：签到 + 去反馈
    # ----------------------------------------------------------
    def _phase_signin_feedback(self, robot_names):
        logger.info("==== 阶段一：签到 + 去反馈 ====")
        for name in robot_names:
            logger.info("--- 处理机器人: %s ---", name)
            try:
                self._enter_robot(name)
            except Exception as e:  # noqa: BLE001
                logger.error("进入 %s 失败: %s", name, e)
                continue
            self.signin.run()
            self.feedback.run()
            self._leave_robot()

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------
    def run(self):
        self._go_to_robot_list()
        robots = self.robots.filter_active()
        if not robots:
            logger.warning("没有可处理的机器人（请先配置 robot_state.json）")
            return

        self._phase_signin_feedback(robots)
        # 阶段二：看广告（回到列表后开始轮转）
        logger.info("==== 阶段二：看广告轮转 ====")
        self.ad_watcher.run(robots)
        logger.info("全部流程完成")
