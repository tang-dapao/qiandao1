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

name = sys.argv[1] if len(sys.argv) > 1 else "席恩"
do_signin = len(sys.argv) < 3 or sys.argv[2] != "0"
do_feedback = len(sys.argv) < 4 or sys.argv[3] != "0"

ok = flow.run_robot(name, do_signin, do_feedback)
print("RESULT:", "OK" if ok else "FAIL")
