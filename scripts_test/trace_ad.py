"""一次性诊断脚本（非生产）——追踪 `_close_ad` 决策时**实际画面**。

三路取证：
1. `Flow._ocr_shot` 包装 → 每次 OCR 看到的画面存 `ocr_NNN.png`；
2. `AdbUI.tap` 包装 → 每次**手指落下前**的画面存 `pretap_NNN_x_y.png`
   （回答"这次 tap 点在了哪个页面" —— 与排查"点到问题反馈/点到 banner"同法）；
3. 日志按时间戳对齐，可与帧一一对应。

输出目录：`screenshots/trace/`（可用环境变量 TRACE_OUT 覆盖），
点击前帧存同级 `<TRACE_OUT>_taps/`。

用法（需先把 QQ 切到前台/机器人列表）：
    py -3.13 scripts_test/trace_ad.py 游迦 [广告次数]
"""
import io
import logging
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import yaml  # noqa: E402
from PIL import Image  # noqa: E402

from adb_ui import AdbUI  # noqa: E402
from flow import Flow  # noqa: E402

OUT = os.environ.get("TRACE_OUT") or os.path.join(BASE, "screenshots", "trace")
OUT = OUT if os.path.isabs(OUT) else os.path.join(BASE, OUT)
TAPS = OUT.rstrip("/\\") + "_taps"
os.makedirs(OUT, exist_ok=True)
os.makedirs(TAPS, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
tlog = logging.getLogger("trace")

_orig_shot = Flow._ocr_shot
_counter = {"ocr": 0, "tap": 0}


def _traced_shot(self):
    img = _orig_shot(self)
    _counter["ocr"] += 1
    n = _counter["ocr"]
    if img is not None:
        try:
            img.save(os.path.join(OUT, "ocr_%03d.png" % n))
        except Exception as e:  # noqa: BLE001
            tlog.warning("保存第 %d 帧失败: %s", n, e)
    tlog.info("OCR#%03d 截屏=%s", n, "ok" if img is not None else "None")
    return img


Flow._ocr_shot = _traced_shot

_orig_tap = AdbUI.tap


def _traced_tap(self, x, y, pause=1.2):
    """点击前抓帧：直接 screencap（不依赖 Flow 实例）。"""
    _counter["tap"] += 1
    n = _counter["tap"]
    try:
        out = subprocess.run(
            [self.adb, "-s", self.device, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=15)
        Image.open(io.BytesIO(out.stdout)).convert("RGB").save(
            os.path.join(TAPS, "pretap_%03d_%d_%d.png" % (n, int(x), int(y))))
        tlog.info("TAP#%03d (%d,%d) ← 点击前现场已存", n, int(x), int(y))
    except Exception as e:  # noqa: BLE001
        tlog.warning("TAP#%03d 点击前抓帧失败: %s", n, e)
    return _orig_tap(self, x, y, pause=pause)


AdbUI.tap = _traced_tap


def main():
    cfg = yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))
    robot = sys.argv[1] if len(sys.argv) > 1 else "游迦"
    times = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    ui = AdbUI(cfg["device"]["udid"])
    if not ui.is_online():
        tlog.error("设备不在线")
        sys.exit(1)
    fl = Flow(cfg, ui)
    tlog.info("=== 追踪开始: %s x%d 广告 (ocr→%s, taps→%s) ===",
              robot, times, OUT, TAPS)
    fl.run_all([robot], False, False, times)
    tlog.info("=== 追踪结束: %s ===", robot)


if __name__ == "__main__":
    main()
