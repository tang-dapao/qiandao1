"""方案 A 回归测试：最后一个账号的完成告警必须在进程退出前推送完成。

背景（2026-10-27 真机日志实测）：
  账号完成告警走 notify 后台 daemon 线程，进程退出时 atexit flush 只有
  3s 等待窗口。多账号模式**最后一个账号**（唐灵固定 4/4）的告警发出后
  主流程往往 ~1ms 就结束 → 推送被退出竞态丢弃，企业微信收不到「获得
  电量」结果（10-26 12:35 / 10-27 13:56 均无「告警已推送」确认行）。
方案 A：main.py 在 run_all_accounts 之后显式 notify.flush(8.0)。

运行：py -3.13 -m unittest test_notify_flush -v
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

import notify


class _FakeChannel:
    """只记录 send 调用、可选人为延迟的假通道（不联网）。"""

    name = "fake"
    supports_image = False

    def __init__(self, delay: float = 0.0, recorder: list | None = None):
        self.delay = delay
        self.recorder = recorder

    def send(self, title, content):
        if self.recorder is not None:
            self.recorder.append(title)
        if self.delay:
            time.sleep(self.delay)
        return {"ok": True}


def _make_worker_notifier(delay: float = 0.0, recorder: list | None = None):
    """构造 enabled=True 且已启动 worker 的 Notifier（绕开网络/落盘）。

    2026-10-30：`flush()` 现在会顺带补发 `pending_file`，必须把队列文件指到
    临时目录 —— 否则单测会去读/删仓库里真实的 `state/notify_pending.jsonl`
    （2026-11-09 由 logs/ 迁至 state/，见 config.yaml 的 state 段）。
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="notify_flush_")
    n = notify.Notifier({"enabled": False,
                         "pending_file": os.path.join(tmp, "pending.jsonl")})
    n.channels = [_FakeChannel(delay=delay, recorder=recorder)]
    n.enabled = True
    n._worker = threading.Thread(target=n._run, name="notify-worker", daemon=True)
    n._worker.start()
    return n


class ModuleFlushTest(unittest.TestCase):
    def setUp(self):
        self._saved = notify._NOTIFIER

    def tearDown(self):
        notify._NOTIFIER = self._saved

    def test_flush_waits_for_pending_alert(self):
        """flush 返回时，队列里的告警必须已真正发出（否则退出即丢）。"""
        rec: list = []
        n = _make_worker_notifier(delay=0.05, recorder=rec)
        notify._NOTIFIER = n
        n._q.put({"ts": "2026-10-27 13:56:52", "epoch": 0.0,
                  "kind": "account", "level": "info",
                  "title": "✅ 账号 唐灵", "detail": "+1559（179 → 1738）",
                  "shot": ""})
        notify.flush(2.0)
        self.assertEqual(rec, ["✅ 账号 唐灵"],
                         "flush 返回时告警仍未发出 → 进程退出会把它丢掉")

    def test_flush_noop_without_notifier(self):
        """未初始化告警器时 flush 静默返回、不抛异常。"""
        notify._NOTIFIER = None
        notify.flush(0.1)                        # 不抛即通过

    def test_flush_terminates_worker(self):
        """flush 会投递哨兵让 worker 退出（收尾语义）。"""
        n = _make_worker_notifier(recorder=[])
        notify._NOTIFIER = n
        notify.flush(2.0)
        self.assertFalse(n._worker.is_alive(), "flush 后 worker 应已退出")


class MainFlushOrderTest(unittest.TestCase):
    """main.main() 多账号分支：必须先跑完账号、再 flush 告警队列。"""

    def test_multi_account_flushes_after_run(self):
        import main as main_mod

        order: list = []
        cfg = {
            "logging": {"level": "INFO", "file": "logs/run.log"},
            "device": {"backend": "adb", "udid": "emulator-5554"},
            "workflow": {"robot_whitelist": ["代柯"],
                         "ad_rotate": True, "ad_rotate_group": 3},
            "accounts": {"enabled": True,
                         "list": [{"nick": "唐灵", "uin": "2593292352",
                                   "enabled": True}]},
            "notify": {"account_summary": True},
        }
        fake_flow = mock.Mock()
        fake_flow.run_all_accounts.side_effect = lambda *a, **k: order.append("run")
        fake_ui = mock.Mock()
        fake_ui.is_online.return_value = True

        with mock.patch.object(main_mod, "load_config", return_value=cfg), \
                mock.patch.object(main_mod, "setup_logging"), \
                mock.patch.object(main_mod, "AdbUI", return_value=fake_ui), \
                mock.patch.object(main_mod, "Flow", return_value=fake_flow), \
                mock.patch.object(main_mod.notify, "flush",
                                  side_effect=lambda *a, **k: order.append("flush")), \
                mock.patch.object(sys, "argv", ["main.py"]):
            main_mod.main()

        self.assertEqual(order, ["run", "flush"],
                         "多账号收尾必须等账号跑完再 flush，否则最后一条告警会丢")


if __name__ == "__main__":
    unittest.main()
