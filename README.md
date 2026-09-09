# QQ 机器人自动签到 + 问题反馈 + 看广告工具

在 Windows + **MuMu 12 模拟器**上，通过 **adb + uiautomator（优先）+ OCR 兜底**驱动 QQ 中机器人「任务中心」，
自动执行 **每日签到 / 问题反馈领取 / 看广告**。

> 技术栈已从旧 Appium 框架迁移为 adb 直连方案（详见「废弃代码」一节）。

## 环境要求

| 依赖 | 说明 |
|---|---|
| Windows | 本机实测环境 |
| Python 3.13 | 命令用 `py -3.13` |
| MuMu 12 模拟器 | 机型模拟小米 12（cupid），竖屏 1080×1920；adb 入口见下 |
| Tesseract OCR | 需安装中文语言包 chi_sim，路径配置在 `config.yaml → ocr.tesseract_cmd` |
| Python 依赖 | `pip install -r requirements.txt`（PyYAML / Pillow / pytesseract / requests） |

### MuMu 的 adb 入口（重要）

MuMu 12 有两个 adb 入口指向**同一个模拟器**：

- `emulator-5554`：MuMu 底层 QEMU 内核，adb server 自动发现，daemon 重启自动重连 —— **稳定，推荐（当前配置）**
- `127.0.0.1:16384`：网络 adb 转发端口，需手动 `adb connect`，daemon 重启会丢失

`config.yaml → device.udid` 当前固定为 `emulator-5554`。

## 目录结构

```
D:\qiandao\
├─ main.py                  # CLI 入口（参数调度 + 探活）
├─ flow.py                  # 核心流程：导航/签到/反馈/看广告/退出
├─ adb_ui.py                # adb 驱动：dump 文本节点(带缓存)/点击/滑动/OCR截图
├─ config.yaml              # 全部配置（设备/流程/OCR/日志）
├─ requirements.txt
├─ signin_feedback.bat      # 启动：仅 签到+问题反馈（main.py --no-ad）
├─ watch_ad.bat             # 启动：仅 看广告（main.py --ad-only）
│
├─ test_flow_ocr_close.py   # mock 单测：广告关闭三层策略 + ymax 过滤（19 用例）
├─ test_adb_cache.py        # mock 单测：dump 缓存 TTL 失效（14 用例）
├─ test_run_all_flags.py    # mock 单测：run_all 各 flag 组合（14 用例）
├─ test_plan_fullflow.md    # 全流程测试计划文档
│
├─ e2e_ad_once.py           # 真机验证：单机器人看一次广告（RESULT: OK/FAIL）
├─ e2e_robot_once.py        # 真机验证：单机器人 签到+反馈（RESULT: OK/FAIL）
├─ scripts_test/            # 真机专项验证脚本（保留）
│   ├─ verify_ymax.py       #   广告页验证 ymax=400 OCR 过滤
│   └─ observe_ad.py        #   广告页证据采集（OCR/uiautomator 摘要，不自动关闭）
│
├─ logs/                    # 运行日志
├─ screenshots/             # 调试截图
└─ legacy/                  # 废弃代码归档（见末节）
```

## 快速使用

1. 启动 MuMu 12，登录 QQ，停留到 **联系人 → 机器人** 列表（干净起点）。
2. 确认 `adb devices` 能看到 `emulator-5554`。
3. 二选一运行：

```bash
# 签到 + 问题反馈（等价 py -3.13 main.py --no-ad）
signin_feedback.bat

# 只看广告
watch_ad.bat
```

## main.py 命令参数

| 参数 | 说明 |
|---|---|
| （无参数） | 自动抓取机器人列表 → 签到+反馈 → 看广告轮转 |
| `--list` | 仅列出自动抓取的机器人，不执行 |
| `--robots "昵称A,昵称B"` | 手动指定机器人（覆盖自动抓取） |
| `--ad-only` | 只看广告（跳过签到/反馈） |
| `--no-ad` | 不看广告（等价 `--ad-times 0`） |
| `--ad-times N` | 每台机器人看广告次数（默认取 `config.yaml → workflow.ad_times_per_robot`） |
| `--no-signin` / `--no-feedback` | 跳过签到 / 跳过反馈 |

示例：

```bash
py -3.13 main.py --robots "李宥恩,小麦" --ad-times 1
```

启动时会做入口探活（设备不在线直接退出），避免后续静默乱点兜底坐标。

## 定位方式（按优先级）

1. **uiautomator 文本节点**（`adb_ui.nodes()`）：dump 当前 UI 树找目标文字，精确取 bounds。
   0.8s TTL 缓存降低 dump 频率（导航提速）。
2. **OCR 兜底**（`flow._ocr_find`）：uiautomator 读不到时全屏截图识别文字，支持 `ymax` 过滤
   （如广告关闭按钮恒在 y≤400，排除任务中心正文的 y>400 误命中）。
3. **固定坐标兜底**：以上都失败时点预设坐标。

> 所有坐标基于 **1080×1920 竖屏固定分辨率**（flow.py 顶部常量），改分辨率需重新标定。

## 看广告关闭策略（三层）

广告 ~12s 倒计时，倒计时结束前点击无效（观看期已停留 18~20s 覆盖）。关闭顺序：

1. uiautomator 定位「关闭广告 / 跳过 / 关闭」按钮；
2. 判断已回任务中心（广告自动结束）；
3. OCR 全屏找关闭按钮（`ymax=400`，视频播放期 dump 失效时的兜底）；
4. 左上角固定坐标 `(160,152)` 兜底（第三方无文字广告）。

重试轮数取 `config.yaml → workflow.ad_close_retries`（默认 12）。

## 退出任务中心

固定使用**左上角返回箭头三层返回**（任务中心 → 聊天页 → profile → 机器人列表），不使用系统返回键。

## 测试

```bash
# mock 单测（无需设备 / 无 OCR 依赖，任意环境可跑）
py -3.13 -m unittest test_flow_ocr_close test_adb_cache test_run_all_flags

# 真机验证（需模拟器在线 + QQ 停在机器人列表）
py -3.13 e2e_ad_once.py "昵称"              # 看一次广告
py -3.13 e2e_robot_once.py "昵称"           # 签到+问题反馈
py -3.13 scripts_test/verify_ymax.py "昵称" # 广告页 ymax 过滤专项验证
py -3.13 scripts_test/observe_ad.py "昵称"  # 广告页证据采集（不自动关闭）
```

## 已知注意点

- **uiautomator dump 的 rc=139**：MuMu 上 dump 进程退出时返回码为 139（段错误）但 dump 实际成功，
  代码以输出中的 `dumped to` 文本判断成功，**不要**以返回码判断。
- **adbd 首命令竞态**：进程刚启动时 adb daemon 尚未稳定，首条 `get-state` 可能误报 offline，
  `is_online()` 内置 3 次重试。
- **间歇性进入失败**：进入机器人任务中心偶发 `profile 没找到 发消息`（QQ 加载抖动），
  看广告阶段有重试可吸收；签到/反馈阶段若命中会跳过该机器人（见日志 ERROR 行后补跑）。
- 请使用**测试账号**，腾讯对模拟器/高频点击有风控。

## 废弃代码

`legacy/` 目录归档了已不再使用的代码，可整体删除（无 git 版本管理，删除前请确认）：

- 旧 **Appium 框架**（`driver.py` / `locators.py` / `robot.py` / `scheduler.py` /
  `signin.py` / `feedback.py` / `ad_watcher.py` / `ocr_utils.py` 及 `robot_state.json` 等）
- **一次性探索脚本**（`legacy/scripts_test/`：历史 dump/OCR/坐标探测脚本，功能已固化进 flow.py；
  可复用验证脚本保留在 `scripts_test/`）
