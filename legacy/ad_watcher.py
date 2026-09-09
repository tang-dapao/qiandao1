"""看广告流程（含 60s CD 与多机器人轮转）。

逻辑：
- 每个机器人需看满 ad_times_per_robot 次广告。
- 每次进入广告 -> 随机停留 ad_wait_min~ad_wait_max 秒 -> 关闭。
- 看完一次后该机器人进入 60s CD；通过切换到下一个机器人来消磨 CD。
- 轮转队列：依次处理各机器人；若某个已达目标次数则移出。
- 当所有机器人都在 CD 中时，等待最短剩余 CD 后继续。
"""
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger("ad")


@dataclass
class AdCounter:
    robot: str
    done: int = 0
    cd_until: float = 0.0
    cd_active: bool = False


class AdWatcher:
    def __init__(self, config: dict, locator, driver, enter_robot, leave_robot):
        """
        enter_robot: callable(robot_name) 进入指定机器人的任务中心（并处理签到/反馈后的状态）
        leave_robot: callable() 退出当前机器人回「我添加的机器人」列表
        """
        self.cfg = config
        self.locator = locator
        self.driver = driver
        self.enter_robot = enter_robot
        self.leave_robot = leave_robot
        wf = config["workflow"]
        self.target_times = wf["ad_times_per_robot"]
        self.ad_min = wf["ad_wait_min"]
        self.ad_max = wf["ad_wait_max"]
        self.cd = wf["ad_cooldown"]

    # ----------------------------------------------------------
    def _watch_once(self) -> bool:
        """在当前任务中心看一次广告。成功返回 True。"""
        timing = self.cfg["timing"]
        if not self.locator.wait_and_tap("btn_watch_ad"):
            logger.warning("未找到【获取随机】按钮")
            return False

        # 进入广告页，随机停留
        wait = random.uniform(self.ad_min, self.ad_max)
        logger.info("广告播放中，随机停留 %.1f 秒", wait)
        self.driver.sleep(wait)

        # 关闭广告（尝试 OCR 关闭/物理返回兜底）
        if not self.locator.tap("btn_ad_close"):
            logger.info("未找到广告关闭文字，改用物理返回")
            self.driver.back()
        self.driver.sleep(timing.get("ad_close_wait", 2.0))
        return True

    # ----------------------------------------------------------
    def run(self, robot_names):
        """对所有机器人执行看广告轮转，直到各自达到目标次数。"""
        if not robot_names:
            logger.warning("没有可处理的机器人")
            return

        queue = [AdCounter(r) for r in robot_names]
        logger.info("开始看广告轮转，目标每机器人 %d 次", self.target_times)

        while queue:
            # 移除已达标的机器人
            queue = [c for c in queue if c.done < self.target_times]
            if not queue:
                break

            now = time.time()
            # 找一个不在 CD 中的机器人
            current = next((c for c in queue if not c.cd_active or now >= c.cd_until), None)

            if current is None:
                # 全部在 CD 中，等待最短剩余
                earliest = min(c.cd_until for c in queue)
                wait = earliest - now
                logger.info("所有机器人均在 CD 中，等待 %.0f 秒", wait)
                self.driver.sleep(max(wait, 0.5))
                continue

            logger.info("==> 处理机器人: %s (已完成 %d/%d)",
                        current.robot, current.done, self.target_times)
            try:
                self.enter_robot(current.robot)
            except Exception as e:  # noqa: BLE001
                logger.error("进入机器人 %s 失败: %s", current.robot, e)
                continue

            if not self._watch_once():
                logger.warning("机器人 %s 看广告失败，跳过本次", current.robot)

            # 更新计数与 CD
            current.done += 1
            current.cd_until = time.time() + self.cd
            current.cd_active = True
            logger.info("机器人 %s 已看 %d/%d 次", current.robot,
                        current.done, self.target_times)

            # 离开当前机器人，回到列表（顺带消磨 CD）
            self.leave_robot()
            self.driver.sleep(0.5)

        logger.info("所有机器人看广告完成！")
