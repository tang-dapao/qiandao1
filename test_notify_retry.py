# -*- coding: utf-8 -*-
"""告警推送重试 + 待补发队列回归测试（2026-10-30）。

背景（当天真丢过 2 条告警）：
  13:18「连续 3 支广告没打开」、14:08「✅ 账号 觅夏 第 2/4」，
  两条都是 `qyapi.weixin.qq.com` 读超时（`timeout: 5.0`），
  而 `notify.fallback` 为空 → 单通道一次超时 = 整条丢，
  只在 `logs/anomaly.jsonl` 留了记录，手机端什么都收不到。

修复后必须守住：
  1. **网络层异常**（Timeout/ConnectionError）会重试，重试期间放宽单次超时；
  2. **配置类异常**（errcode/webhook 失效/IP 未授信）**不重试**（重试不会变好）；
  3. 全部通道仍失败 → 写进待补发队列 `logs/notify_pending.jsonl`；
  4. 补发成功 → 该行从队列删除；仍失败 → 原样保留；
  5. `flush()` 收尾时自动补发一次（有配额与耗时预算，不能拖住退出）；
  6. 成功路径零额外开销（不写文件、不多发请求）。

运行：py -3.13 -m unittest test_notify_retry -v
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import requests

import notify


class _Ch:
    """假通道：前 `errors` 次抛 `exc`，之后成功；记录每次调用时的 timeout。"""

    name = "fake"
    supports_image = False

    def __init__(self, errors=0, exc=None, timeout=5.0, name="fake"):
        self.errors = errors
        self.exc = exc or requests.exceptions.ReadTimeout("read timeout=5.0")
        self.timeout = timeout
        self.calls = []
        self.name = name

    def send(self, title, content):
        self.calls.append(self.timeout)
        if self.errors > 0:
            self.errors -= 1
            raise self.exc
        return {"ok": True}


EV = {"ts": "2026-10-30 14:08:01", "epoch": 0.0, "kind": "account",
      "level": "info", "title": "✅ 账号 觅夏", "detail": "+1131", "shot": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notify_retry_")
        self.pending = os.path.join(self.tmp, "notify_pending.jsonl")
        self.record = os.path.join(self.tmp, "anomaly.jsonl")
        self._sleep = mock.patch.object(notify.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _mk(self, channels, **over):
        conf = {"enabled": False, "send_retries": 2, "retry_backoff": 0.0,
                "retry_timeout": 10.0, "pending_file": self.pending,
                "record_file": self.record, "resend_max": 5}
        conf.update(over)
        n = notify.Notifier(conf)
        n.channels = channels
        n.enabled = True
        return n

    def _pending_lines(self):
        if not os.path.isfile(self.pending):
            return []
        with open(self.pending, "r", encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]


class DeliverRetryTest(Base):
    def test_transient_retried_then_success(self):
        ch = _Ch(errors=1)
        n = self._mk([ch])
        self.assertTrue(n._deliver(dict(EV)))
        self.assertEqual(len(ch.calls), 2, "网络超时应重试一次后成功")
        self.assertEqual(self._pending_lines(), [], "成功时不该写待补发队列")

    def test_retry_bumps_and_restores_timeout(self):
        ch = _Ch(errors=2, timeout=5.0)
        n = self._mk([ch])
        self.assertTrue(n._deliver(dict(EV)))
        self.assertEqual(ch.calls, [5.0, 10.0, 10.0],
                         "首次用原超时，重试抬到 retry_timeout")
        self.assertEqual(ch.timeout, 5.0, "结束后必须还原通道超时，别污染后续告警")

    def test_non_transient_not_retried(self):
        ch = _Ch(errors=99, exc=RuntimeError("errcode=93000 webhook key 无效"))
        n = self._mk([ch])
        self.assertFalse(n._deliver(dict(EV)))
        self.assertEqual(len(ch.calls), 1, "配置类错误重试也没用，不许重试")
        self.assertEqual(len(self._pending_lines()), 1)

    def test_all_transient_fail_writes_pending(self):
        ch = _Ch(errors=99)
        n = self._mk([ch])
        self.assertFalse(n._deliver(dict(EV)))
        self.assertEqual(len(ch.calls), 3, "1 次 + 2 次重试 = 3 次")
        lines = self._pending_lines()
        self.assertEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertEqual(obj["ev"]["title"], "✅ 账号 觅夏")
        self.assertIn("timeout", obj["err"].lower())

    def test_retries_zero_keeps_old_behaviour(self):
        ch = _Ch(errors=99)
        n = self._mk([ch], send_retries=0)
        self.assertFalse(n._deliver(dict(EV)))
        self.assertEqual(len(ch.calls), 1, "send_retries=0 应回到「只试一次」")

    def test_fallback_channel_used_after_primary_exhausted(self):
        bad = _Ch(errors=99, name="bad")
        good = _Ch(name="good")
        n = self._mk([bad, good])
        self.assertTrue(n._deliver(dict(EV)))
        self.assertEqual(len(bad.calls), 3)
        self.assertEqual(len(good.calls), 1)
        self.assertEqual(self._pending_lines(), [])

    def test_pending_append_keeps_existing_lines(self):
        n = self._mk([_Ch(errors=99)])
        n._deliver(dict(EV))
        n._deliver(dict(EV, title="第二条"))
        self.assertEqual(len(self._pending_lines()), 2, "待补发队列只能追加，不能覆盖")


class ResendPendingTest(Base):
    def _seed(self, items):
        with open(self.pending, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps({"ev": it, "err": "x",
                                    "failed_at": "2026-10-30 14:08:01"},
                                   ensure_ascii=False) + "\n")

    def test_resend_success_clears_file(self):
        self._seed([dict(EV)])
        ch = _Ch()
        n = self._mk([ch])
        self.assertEqual(n.resend_pending(), (1, 0))
        self.assertEqual(self._pending_lines(), [], "补发成功后队列应被清空")
        self.assertEqual(ch.calls, [5.0], "补发只试一遍，不走重试链")

    def test_resend_failure_keeps_line(self):
        self._seed([dict(EV)])
        n = self._mk([_Ch(errors=99)])
        self.assertEqual(n.resend_pending(), (0, 1))
        self.assertEqual(len(self._pending_lines()), 1, "仍失败的行要留着下次再试")

    def test_resend_bad_json_skipped(self):
        with open(self.pending, "w", encoding="utf-8") as f:
            f.write("{这不是 json\n")
            f.write(json.dumps({"ev": EV}) + "\n")
        n = self._mk([_Ch()])
        self.assertEqual(n.resend_pending(), (1, 0))
        self.assertEqual(self._pending_lines(), [])

    def test_resend_respects_max_items(self):
        self._seed([dict(EV, title="A"), dict(EV, title="B"), dict(EV, title="C")])
        n = self._mk([_Ch()])
        self.assertEqual(n.resend_pending(max_items=2), (2, 0))
        self.assertEqual(len(self._pending_lines()), 1, "超配额的行要留到下次")

    def test_resend_no_file_is_noop(self):
        n = self._mk([_Ch()])
        self.assertEqual(n.resend_pending(), (0, 0))

    def test_flush_triggers_resend(self):
        """收尾 flush 必须把待补发队列清掉（否则等于没修）。"""
        self._seed([dict(EV)])
        n = self._mk([_Ch()])
        n._worker = mock.Mock()      # join() 立即可用，不需要真线程
        n.flush(0.1)
        self.assertEqual(self._pending_lines(), [])
        self.assertTrue(n._worker.join.called)


class ConfigDefaultsTest(unittest.TestCase):
    def test_new_keys_have_defaults(self):
        conf = notify.load_notify_config({})
        self.assertEqual(conf["send_retries"], 2)
        self.assertEqual(conf["retry_backoff"], 2.0)
        self.assertEqual(conf["retry_timeout"], 10.0)
        self.assertEqual(conf["pending_file"], "logs/notify_pending.jsonl")
        self.assertEqual(conf["resend_max"], 5)

    def test_new_keys_overridable(self):
        conf = notify.load_notify_config({"notify": {
            "send_retries": 0, "retry_backoff": 0.5,
            "retry_timeout": 20, "pending_file": "logs/x.jsonl", "resend_max": 9}})
        self.assertEqual(conf["send_retries"], 0)
        self.assertEqual(conf["retry_backoff"], 0.5)
        self.assertEqual(conf["retry_timeout"], 20.0)
        self.assertEqual(conf["pending_file"], "logs/x.jsonl")
        self.assertEqual(conf["resend_max"], 9)


if __name__ == "__main__":
    unittest.main()
