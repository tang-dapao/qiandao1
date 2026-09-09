"""问题反馈流程。

流程：任务中心 -> 点【去反馈】-> 跳转新页 -> 点左上角【关闭】-> 领取成功
      -> 自动返回任务中心
"""
import logging

logger = logging.getLogger("feedback")


class Feedback:
    def __init__(self, config: dict, locator, driver):
        self.cfg = config
        self.locator = locator
        self.driver = driver

    def run(self) -> bool:
        timing = self.cfg["timing"]
        self.driver.sleep(1.0)

        # 1. 点击任务中心里的【去反馈】
        if not self.locator.wait_and_tap("btn_feedback"):
            logger.warning("未找到【去反馈】按钮")
            return False

        # 2. 等待跳转，点左上角【关闭】
        self.driver.sleep(timing.get("page_wait", 2.5))
        if not self.locator.wait_and_tap("btn_feedback_close"):
            logger.warning("未找到反馈页【关闭】按钮")
            return False

        # 3. 等待自动返回任务中心
        self.driver.sleep(timing.get("page_wait", 2.5))
        logger.info("反馈领取流程完成")
        return True
