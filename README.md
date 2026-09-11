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
├─ watch_ad.bat             # 启动：仅 看广告，补满每台每日配额（main.py --ad-only）
├─ ad_test_all.bat [COUNT]  # 启动：全白名单手动跑广告（默认每台 1 次，全链路快速验证）
│
├─ test_flow_ocr_close.py   # mock 单测：广告关闭 + ymax + F8 正向定位不盲点 + **F11 banner 盲点禁令 + 覆盖层闸门** + 跨词元拼接 + BACK 封顶 + 条带高度上限（63 用例）
├─ test_adb_cache.py        # mock 单测：dump 缓存 TTL 失效 + subprocess 超时看门狗（18 用例）
├─ test_run_all_flags.py    # mock 单测：run_all 各 flag 组合 + 单会话连看 + 首轮广告 + 配额收尾 + 白名单（29 用例）
├─ test_nav_optimize.py     # mock 单测：导航优化 + 心动卡错页守卫 + 机器人列表滚动收集（37 用例）
├─ test_task_safety.py      # mock 单测：任务安全（A3 Badcase 守卫顺序 + 签到浮层断言 + 行内 X/Y 计数，32 用例）→ 合计 179/179
├─ test_plan_fullflow.md    # 全流程测试计划文档
│
├─ e2e_ad_once.py           # 真机验证：单机器人看一次广告（RESULT: OK/FAIL）
├─ e2e_robot_once.py        # 真机验证：单机器人 签到+反馈（RESULT: OK/FAIL）
├─ scripts_test/            # 真机专项验证 / 取证脚本（保留）
│   ├─ verify_ymax.py       #   广告页验证 ymax=400 OCR 过滤
│   ├─ verify_f11.py        #   离线回放真机现场帧，验证 banner 禁令 + 覆盖层闸门
│   ├─ observe_ad.py        #   广告页证据采集（OCR/uiautomator 摘要，不自动关闭）
│   ├─ trace_ad.py          #   存帧追踪：记录每次 OCR 实际看到的画面（排查判定失灵）
│   ├─ replay_frames.py     #   离线回放上述帧，验证页面判定是否翻转（不连真机）
│   ├─ probe_f11_live.py    #   上机探针：验证 _page_tc 状态位与 _tap 拒绝行为
│   ├─ probe_banner.py      #   上机取证：查固定坐标落点节点 / banner 是否在 dump
│   ├─ probe_overlay_idle.py #  上机对照：任务中心零点击，观察问卷是否自发弹出
│   ├─ probe_dk_quota.py    #   上机取证：进台行 X/Y 配额计数读取
│   ├─ probe_list.py        #   上机取证：机器人列表页判定细节 + 全节点坐标
│   └─ measure_dump.py      #   dump 分段耗时测量（含 --compressed 对比）
│
├─ logs/                    # 运行日志
├─ screenshots/             # 调试截图
└─ legacy/                  # 废弃代码归档（见末节）
```

## 快速使用

1. 启动 MuMu 12，登录 QQ，停留到 **联系人 → 机器人** 列表（干净起点）。
2. 确认 `adb devices` 能看到 `emulator-5554`。
3. 按需选一个启动脚本：

```bash
signin_feedback.bat        # 签到 + 问题反馈（等价 py -3.13 main.py --no-ad）
watch_ad.bat               # 只看广告，把每台补满每日配额（等价 py -3.13 main.py --ad-only）
ad_test_all.bat [COUNT]    # 全白名单手动跑广告，默认每台 1 次（全链路快速验证用）
```

> 仅对 `config.yaml → workflow.robot_whitelist` 名单内的昵称执行签到/反馈/看广告；
> 名单为空或未配置 = 不过滤（跑全部）。命令行 `--robots` 指定的名字**同样受白名单约束**（防绕过）。
> 需要临时跑名单外的机器人，先改 `config.yaml` 里的 `robot_whitelist`。

## main.py 命令参数

| 参数 | 说明 |
|---|---|
| （无参数） | 自动抓取机器人列表 → 签到+反馈（第一轮同会话顺带看 1 次广告）→ 阶段二单会话连看补足剩余配额 |
| `--list` | 仅列出自动抓取的机器人，不执行 |
| `--robots "昵称A,昵称B"` | 手动指定机器人（覆盖自动抓取；**仍受白名单约束**） |
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
   0.8s TTL 缓存降低 dump 频率（导航提速）。**这是唯一"可信"的点击依据**（见 F11）。
2. **OCR 兜底**（`flow._ocr_find`）：uiautomator 读不到时截图识别文字。广告关闭按钮恒在
   左上 y≈107-187 → 只对顶部条带 `AD_TOP_REGION`(0,0,1080,320) 做 OCR（+ ymax 过滤）。
   **条带高度上限 320**：430 会把大片深色视频背景裁进来，tesseract 在渐变/噪声上整块
   返回空、连清晰的「关闭广告」都读不出（见关闭策略 F8 注）。
3. **任务中心页禁止盲点坐标点击**（F11，2026-09-10）：见下方「banner 根治」小节。

> 所有坐标基于 **1080×1920 竖屏固定分辨率**（flow.py 顶部常量），改分辨率需重新标定。
> **滑动锚点 `SWIPE_X=140`**（2026-09-10 #1 优化）：`swipe_up`/`swipe_down` 起止 x 固定左安全列 140，
> 避开中列 540 的「签到/问题反馈/banner」命中区，防止 WebView 自绘页把滑动误判为点按而飘页。

## banner 根治：任务中心页禁止盲点坐标点击（F11）

**问题**：任务中心页的推广 banner「QQAI好友·常见问题答疑」被点中 → 误开 AI 好友反馈 H5
→ ~37s 后延迟弹「QQ AI好友Badcase反馈问卷」。用户反复反馈的「点到 banner 图 / 广告时跳到
问题反馈」即此。

**实测关键（两台真机 dump + 截图，非臆测）**：

| 事实 | 证据 |
| --- | --- |
| banner **不在 uiautomator dump 里** | `probe_f11_live.py`：代柯任务中心整页 **20 个节点**，顶部区只有「客服」「收支详情」等真实控件，**无 banner 文案** |
| banner **纵坐标随版式漂移** | 游迦页 **y≈116-366**（顶格）；代柯页 **y≈1151-1410**（在「开通解锁以下专属权益」卡之下） |
| ⇒ 固定禁点区不可行 | 没有任何固定 y 区间能覆盖所有版式 |

**根治（与版式无关）**：**只要不盲点坐标，就永远命中不到它** —— banner 不在 dump，故所有
基于真实 dump 节点的 `_tap_node` **天然安全**。于是规定：

- `flow._tap(x, y, trusted=False)`：在任务中心页（`_page_tc=True`）**拒绝一切坐标点击**，
  除非显式 `trusted=True`。合法例外仅 2 处：
  1. `_close_ad`：广告页左上角「关闭广告」按钮（OCR 正向定位、非盲点）；
  2. `_signin`：签到浮层按钮（浮层已由 `wait_for("每日免费领")` / `_find("我知道了")` 确认在屏）。
- `_signin` 的关闭 ✕ 改为**浮层仍在屏才点**（该坐标在代柯版式里离 banner 顶仅 7px）。
- `_page_tc` 状态位：进任务中心置 True；退出 / `_nav_robot_list` / `_safe_back_to_robot_list`
  置 False —— 保证**列表页机器人首行（y≈410-500）、联系人页『机器人』分类行（y≈201-352）**
  仍可正常点击（否则导航直接瘫痪）。

## 看广告关闭策略（F8 + 17:45 顺序对调：uiautomator 优先 → OCR 正向定位 → BACK 封顶兜底）

等待 `ad_wait_min~ad_wait_max`（当前 `config.yaml` = **16~18s**，覆盖 ≤15s 视频广告 + 加载延迟）后：

0. **快路径**：`_taskcenter_confirmed_by_ocr()` 连续 2 次读到任务中心特征词
   （`每日签到 / 任务中心 / 获取随机 / 收支详情`）→ 广告已自动结束 → 收工，**不点任何坐标**。
   （**双检刻意保留**：广告创意素材可能含「每日签到」字样，单检误判会让整条广告白耗。）
1. **uiautomator 直关**（2026-09-10 17:45 与 OCR 对调）：`_find_close_node()` 精确「关闭广告」
   → 「跳过」/「关闭」候选，命中即点其 bounds 中心（F11 认可的**节点点击**）。等待结束后页面
   多数已 idle，dump 1-3s 即命中（17:29 实测一次命中 `(141,150)`，省 ~9s OCR 空试）。
   直关失败 → 先巡检一次 Badcase/AI 好友 H5 自愈 → 转 OCR / 兜底。
2. **OCR 正向直关**：`_ad_close_pos()` 在顶部条带正向定位「关闭广告 / 跳过 / 取消」，
   **点它自己读到的坐标**（`_tap(*pos, trusted=True)`，F11 显式放行）→ 2s 后校验。
   - **读不到 → 绝不盲点固定坐标**，打印告警后直接进入兜底轮询。

兜底轮询每轮顺序（重试轮数取 `config.yaml → workflow.ad_close_retries`，默认 12）：

1. 已回任务中心（`_back_at_taskcenter()`，广告自动结束）—— **必须先于 uiautomator**：
   防 dump 残留节点在任务中心页被误读误点（该坐标带与顶部 banner 重叠）；
2. uiautomator 定位「关闭广告 / 跳过 / 关闭」→ 点节点中心；
3. `_ad_close_pos()` OCR 顶部条带正向定位关闭按钮 → 点其坐标；
4. **无固定坐标盲点**（F8 删除）。改**物理 BACK**（`F6`：连续 BACK 封顶
   `workflow.ad_close_max_backs`，默认 3）—— 超限直接放弃本次关闭返回 False，
   交上层失败计数 + `_safe_back_to_robot_list()` 复位。**绝不允许无限 BACK**：
   2026-09-10 实测判定失灵时连按 10 次 BACK 把 QQ 一路退到手机桌面。

> **问题反馈 / 静默假成功防线（F11）**：`_back_at_taskcenter()` 在"即将判成功"时，额外用
> 问卷**独有词**（`Badcase` / `反馈问卷`；任务中心绝不含 —— 任务行只有「问题反馈」4 字）
> 扫顶部条带（`_overlay_visible_in_top()`）。命中即判"未回任务中心"，杜绝 dump 穿透读到
> 背景任务中心行造成的假成功。只在成功路径执行 1 次 OCR（~1s），不影响点击效率。

> **为什么不用 uiautomator dump 做判定**：视频播放期 UI 无法 idle，`dump` 必然失败
> （rc=139），基于它的稳定检查恒为 False → 固定坐标一次都点不到、广告再也关不掉
> （`dump()` 现已超时即失败、不重试，单次等待 ≤4s；关闭广告路径更用 2.5s，见 `workflow.ad_close_dump_timeout`）。
> （2026-09-10 实锤：单次关闭耗时达 125s，第二轮甚至死锁卡住）。OCR 不受视频/动画影响。

> **关闭确认优化 A+B（2026-09-11）**：`_close_ad` 点完关闭按钮后要"确认已回任务中心"，
> 这段确认曾是隐藏耗时点（确认路径 dump 仍走 4s，实测代柯单次关闭 32s）。现
> `_back_at_taskcenter(timeout)` 透传短超时，`_close_ad` 7 处确认调用统一用
> `ad_close_dump_timeout`（2.5s）；另加 `ad_close_settle`（默认 2.0s）在关闭 tap 后沉降，
> 等关闭动画/任务中心重载播完再确认。两者均可在 config.yaml 调，改数字不改代码。
> **实测基线**（定位关闭按钮 → 确认关闭，不含固定 ad_wait）：改动前中位 **18.7s → 现 11.3s**
> （≈40% 提速，每次省 ~7.4s），确认路径 4.0s 超时已清零。
> 注：确认阶段在任务中心**重载动画期**常见 2 条 `dump 超时 (>2.5s)` WARNING，属预期 —— 随后由
> `dump_fail_streak>0 → 以 OCR 判定为准` 收工，**不是又去关广告**。

> **⚠️ OCR 关键词必须走"跨词元拼接"匹配**（2026-09-10 核心修复）：tesseract(chi_sim)
> 实测会把连续中文切成单字/碎词 —— 任务中心标题读成 `任务`+`中`+`心`、按钮读成
> `获取`+`随机`。旧实现只做整词匹配 `k in t` → 任务中心四个特征词**全部失配** →
> `_taskcenter_confirmed_by_ocr()` 恒 False → `_back_at_taskcenter()` 恒 False →
> 认定"广告没关"而无限 BACK（真机实测连按 12 次退到桌面）。现 `_ocr_find` 在第 1 遍
> 整词匹配全部失配后，会把各词元按 OCR 阅读顺序拼接成一个字符串再做子串定位。
> 排查同类问题请用 `scripts_test/trace_ad.py` 存帧 + `replay_frames.py` 离线回放。

> **⚠️ 为什么条带高度必须是 320（F8 核心之一）**：430 高度会把大片深色视频背景裁进来，
> tesseract 在渐变/噪声上会放弃分页、**整块返回空** —— 实测 4399 视频广告 3 帧全读不出
> 肉眼极清晰的「关闭广告」（裁到 340×240 就能读出）。扩样（9 广告帧 + 10 任务中心帧）：
> **430 → 6/9 命中；320 / 300 / 260 → 9/9 命中；三种高度对任务中心均 0 误报**。
> 这正是旧版"关闭按钮时灵时不灵、只能靠固定坐标盲点"的真因。`AD_TOP_REGION` 现为
> `(0,0,1080,320)`，并由 `test_flow_ocr_close.py::TestAdTopRegion` 断言 `<=320` 防回退。

> **AI 好友 H5 的识别（F5）**：一旦仍误开 banner，全屏页是 AI 好友**帮助中心**（标题
> 「常见问题」，正文「额度消耗规则 / 免费额度、电量与心动卡」）—— `_dismiss_badcase()`
> 用该页**独有**词叠加"任务中心特征词读不到"识别并物理 BACK 退出（`F5`）。

## 看广告节奏（F10：单次任务中心会话连看多次）

每台机器人**只进出一次任务中心**，会话内按 `ad_cooldown`（60s）间隔原地连看
「获取随机」，直到每日配额（X/10）/ 本次目标次数 / 失败上限（3 次）才退出 ——
会话内已达标（跨进程残留/手动已看完）直接退出不空耗。相比旧"每看 1 次广告进出
一次任务中心"（单次进出 ~68s），进出开销摊薄到整轮。

**首轮广告同会话（2026-09-10 16:35）**：`run_all` 在 target>0 且有签到/反馈任务时，
对第一轮每台传 `run_robot(first_ad=True)` —— 签到 + 反馈完成后**不退出**，在同一会话
顺手看 1 次广告再退出，剩余次数交阶段二轮转补足。每台省一次完整进出（~70-95s）。
`--no-ad`（target=0）与 `--ad-only` 行为不变（跳过第一轮，避免空进出）。

**CD 重叠（2026-09-10 18:15）**：CD 从**广告关闭时刻**起算，配额复核挪到 CD 窗口末尾
（cd-10s 处，页面已稳定不再 dump 超时）—— 校验耗时与 CD 重叠，不再串行累加（旧实现
每次看完立即读屏复核，正撞重载动画期 dump 超时，两次复核烧 ~43s 加在 60s CD 之前）。
进台时 `_read_ad_ratio()` 读一次 X/10 基数，`基数 + 本会话已看 >= 配额` → **算术收尾**
（不读屏、不等 CD 直接退出）；基数读不到（行被覆盖）则回退 CD 窗口内的屏幕复核。

**满额直退（2026-09-10 18:55，真机验证通过）**：X/Y 计数在 dump 里可能被拆成多个节点
（`'10'` `'/'` `'10'` 三个独立节点），`_row_completion()` 第 2 遍把行内节点按 x 序拼接后
`search(r"(\d+)\s*/\s*(\d+)")` 提取；满额后按钮文案「获取随机」→「已完成」而**行标题「看广告」不变**，
故统一用 `_find_row("看广告", alt_labels=("获取随机",))` 同轮查找，避免按钮词失配导致的
28s 空滚（期间易被 ~40s 重弹的问卷盖住）。实测：代柯 10/10 从「4m52s 全失败」→「1m30s 秒退」。

## 退出任务中心

一律使用**系统 BACK 键**（`keyevent 4`）连续退出（任务中心 → 聊天页 → profile → 机器人列表），
**不再 tap 任何坐标箭头**。原因：任务中心顶部 banner「QQ AI好友·常见问题答疑」已升级为覆盖
y≈0-440 的可点击入口，固定坐标 tap 会误开 Badcase 反馈问卷（2026-09-09 实锤）。

**不再预先下滑到顶部**（2026-09-10 优化）：快路径直接三层 BACK（不做任何滚动）；仅当三层返回
后仍未回列表**且仍在子页**时，才补做一次「滑到顶 + 再退一轮」（省 ~14-18s/台；已回 QQ 主壳
则跳过补退，防退到桌面）。层间等待用局部常量 `EXIT_LAYER_WAIT=1.2s`（非全局 `page_wait`），
三层仍各自用 `_looks_like_robot_list()` **强判据**校验。

## Badcase 问卷 / 推广广告 防线（2026-09-10 新增）

- **L3**：`_badcase_visible()` 全屏 OCR 找 `Badcase/反馈问卷/开始填写/感谢大家一直`，
  命中即 `_dismiss_badcase()` 连续 BACK 退出。挂在广告循环每轮前 + `run_robot` 签到前（A3）。
- **已知缺口**：签到/反馈按钮点击后仍会随机弹 Badcase（操作触发，非停留触发），以及普通推广
  广告（无"关闭广告"文字）签到后随机弹 —— 详见待办（尚未实现完整兜底）。

## 测试

```bash
# mock 单测（无需设备 / 无 OCR 依赖，任意环境可跑）
py -3.13 -m unittest discover -p "test_*.py"          # 全量，当前 179/179 通过
py -3.13 -m unittest test_flow_ocr_close test_adb_cache test_run_all_flags test_nav_optimize test_task_safety

# 真机验证（需模拟器在线 + QQ 停在机器人列表）
py -3.13 e2e_ad_once.py "昵称"              # 看一次广告
py -3.13 e2e_robot_once.py "昵称"           # 签到+问题反馈
py -3.13 scripts_test/verify_ymax.py "昵称" # 广告页 ymax 过滤专项验证
py -3.13 scripts_test/observe_ad.py "昵称"  # 广告页证据采集（不自动关闭）
```

> 依赖：`pip install -r requirements.txt`（PyYAML / Pillow / pytesseract / requests）。
> 单测需要能 import `main.py`，故 **PyYAML 必须装**（缺它会导致 main 参数映射类用例报
> `ModuleNotFoundError: No module named 'yaml'`）。
> 详细技术档案、坑 1-14、恢复流程见仓库根 **`MEMORY.md`**。

## 已知注意点

- **uiautomator dump 的 rc=139**：MuMu 上 dump 进程退出时返回码为 139（段错误）但 dump 实际成功，
  代码以输出中的 `dumped to` 文本判断成功，**不要**以返回码判断。
- **adbd 首命令竞态**：进程刚启动时 adb daemon 尚未稳定，首条 `get-state` 可能误报 offline，
  `is_online()` 内置 3 次重试。
- **间歇性进入失败**：进入机器人任务中心偶发 `profile 没找到 发消息`（QQ 加载抖动），
  看广告阶段有重试可吸收；签到/反馈阶段若命中会跳过该机器人（见日志 ERROR 行后补跑）。
- **可能误入「心动卡会员 H5」**（2026-09-10 方案 A 防御）：该页是 WebView，dump 穿透读不到，
  由 `_enter_taskcenter` 在未见任务中心特征时 OCR 一次识别（命中 `心动卡 / 高级模型 / 立即续费`
  → 判进入失败 + 复位重试）。2026-09-11 00:32 小麦 实测命中并正确判失败。
- **导航偶发「未确认在机器人列表」（坑 12）**：dump 残留/穿透节点（如同屏混入「发消息」）会让
  `_looks_like_robot_list()` 误判为"不在列表"→ 导航空转。已记入 `MEMORY.md` 坑 12，**尚未根治**。
- 请使用**测试账号**，腾讯对模拟器/高频点击有风控。

## 废弃代码

`legacy/` 目录归档了已不再使用的代码，可整体删除（无 git 版本管理，删除前请确认）：

- 旧 **Appium 框架**（`driver.py` / `locators.py` / `robot.py` / `scheduler.py` /
  `signin.py` / `feedback.py` / `ad_watcher.py` / `ocr_utils.py` 及 `robot_state.json` 等）
- **一次性探索脚本**（`legacy/scripts_test/`：历史 dump/OCR/坐标探测脚本，功能已固化进 flow.py；
  可复用验证脚本保留在 `scripts_test/`）
