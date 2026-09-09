import logging
import sys
import yaml

from adb_ui import AdbUI
from flow import Flow

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
ui = AdbUI(cfg["device"]["udid"])
flow = Flow(cfg, ui)

name = sys.argv[1] if len(sys.argv) > 1 else "李宥恩"

if not flow._enter_taskcenter(name):
    print("RESULT: ENTER_FAIL")
    sys.exit(1)
ok = flow._watch_ad_once()
print("RESULT:", "OK" if ok else "FAIL")
if ok:
    flow._exit_taskcenter()
