"""每日签到流程。

流程：任务中心 -> 点【签到】-> 弹窗出现 -> 点左下方【签到】-> 校验成功
      -> 关闭弹窗 -> 自动返回任务中心
"""
import logging

logger = logging.getLogger("signin")


class Signin:
    def __init__(self, config: dict, locator, driver):
        self.cfg = config
        self.locator = locator
        self.driver = driver

    def run(self) -> bool:
        """执行一次签到。返回是否成功。"""
        timing = self.cfg["timing"]
        self.driver.sleep(timing.get("taskcenter_wait", 3.0))

        # 1. 点击任务中心里的【签到】
        if not self.locator.wait_and_tap("btn_signin"):
            logger.warning("未找到【签到】按钮，可能今日已签或页面未加载")
            return False
        self.driver.sleep(1.5)

        # 2. 弹窗出现后，点左下方【签到】
        if not self.locator.wait_and_tap("btn_signin_confirm"):
            logger.warning("未找到弹窗中的【签到】，可能弹窗未出现或已签")
            return False

        # 3. 校验是否签到成功（按钮变"已签到"或出现成功提示）
        if self.locator.verify_text("已签到", retries=4):
            logger.info("签到成功（检测到已签到状态）")
        else:
            logger.info("未检测到明确成功标识，按默认逻辑继续")

        # 4. 关闭弹窗返回任务中心
        self.driver.sleep(1.0)
        if not self.locator.tap("btn_back"):
            # 兜底：点左上角关闭/空白处
            self.locator.wait_and_tap("btn_signin_confirm", retries=2)
        self.driver.sleep(timing.get("page_wait", 2.5))
        logger.info("签到流程完成")
        return True
