# 项目记忆 / 进度存档（用于下次接着做）

> 更新日期：2026-09-15
> 项目路径：D:\qiandao
> 运行方式：Windows 上 `py -3.13 xxx.py`，模拟器 MuMu（ADB **emulator-5554**，QEMU 底层入口；
> 旧的 `127.0.0.1:16384` 是网络转发口，daemon 重启即丢，已弃用）
> 目标：QQ 机器人任务中心 每日签到 / 问题反馈 / 看广告（每天每台 10 次，60s CD 多机轮转）
> **分辨率已固定为 1080×1920 竖屏（SurfaceOrientation=0）**，所有坐标基于此。

---

## 一、当前进度概览（已完成 / 验证通过的）

### 坐标体系（1080×1920 竖屏，实测确认）
- 截图 / OCR / input tap / uiautomator 共用同一 1080×1920 空间。
- uiautomator 能读出任务中心 WebView 中文文本与真实坐标（滚动后仍可靠）。
- 任务中心行按钮固定 `X≈879`（各行右侧对齐），按钮中心在标签中心下方约 24px。
- 滑动锚点 `SWIPE_X=140`（左安全列，避开中列 540 的「签到/问题反馈/banner」命中区）：
  `swipe_up`/`swipe_down` 起止 x 固定 140，纯纵向滑动由 ScrollView 接管，防 WebView 自绘页
  把滑动误判为点按按钮而飘到对应页面（2026-09-10 #1 优化，根治任务中心页滑动漂移）。

### 返回路径（2026-09-09/10 起：一律系统 BACK 键，EXIT_*_ARROW 坐标已弃用）
- `_exit_taskcenter()`：三层全部改为 `self.ui.back()`（keyevent 4），不再 tap 任何坐标箭头。
- **弃用根因**：banner「QQ AI好友·常见问题答疑」已升级为覆盖 y≈0-440 的可点击入口，
  任何固定坐标 tap（哪怕 EXIT_*_ARROW 原坐标）都可能误开 Badcase 问卷。
- 退出前 `_exit_taskcenter` 仍先调 `_scroll_to_top_of_taskcenter()`（到顶锚点「开通解锁以下
  专属权益」/「收支详情」）再 BACK —— 历史坐标方案遗留步骤，BACK 本身不依赖箭头出现，保留无害。
- BACK ×3 后校验回到机器人列表（"账户及设置"等特征）；广告/Badcase 覆盖时连退次数上限由调用方控制。

### 已单独验证通过的功能（在各机器人上）
| 功能 | 验证机器人 | 证据 |
|------|-----------|------|
| 签到 0→1 | 古禹 | 按钮变「×签到1天」，计数 1/1 |
| 问题反馈 0→1 | 古禹 | 按钮变「去反馈」，计数 1/1 |
| 看广告 0→1 | 裴旖 | 1/10 + 电量 +12，CD 倒计时出现 |
| 三层返回箭头退出 | 古禹/裴旖 | 历史验证（2026-09-09 起退出改系统 BACK 键，见下） |

### 签到/看广告相关浮层坐标（1080×1920，实测）
- 签到浮层「每日免费领」：签到按钮 @(301,1800)、右上角✕ @(996,1143)、看广告+ @(781,1800)
- 签到成功浮层「恭喜获得」：我知道了 @(540,1189)、✕ @(540,1498)
- 反馈问卷页返回：优先文本「返回」，兜底 @(81,139)
- 广告页「关闭广告」按钮：左上角，OCR 实测定位到 @(99,152) / @(120,152)，兜底固定坐标 AD_CLOSE=(160,152)
  - **盲点前提**：只有 `_stable_in_ad_page()` 确认在广告页才允许盲点（B+ 双保险），
    dump 失败（`ui.dump_fail_streak > 0`）时拒绝盲点（L1）
- 广告关闭词 OCR 顶部条带：`AD_TOP_REGION=(0,0,1080,320)`（430→320，见坑 13），找 关闭广告/关闭/跳过
- Badcase 问卷识别词（全屏 OCR）：`Badcase / 反馈问卷 / 开始填写 / 感谢大家一直`
- 心动卡会员 H5 错页识别词（进入任务中心守卫）：`心动卡 / 高级模型 / 立即续费`
- 任务中心 OCR 特征词（方案 A 负向排除，判定"能不能盲点 AD_CLOSE"）：
  `每日签到 / 任务中心 / 获取随机 / 收支详情`
- 子进程超时常量（2026-09-10 看门狗）：`adb_ui.CMD_TIMEOUT=20s`（tap/swipe/back/cat）、
  `adb_ui.DUMP_TIMEOUT=4s`（uiautomator dump，视频期快速失败）、`flow.SHOT_TIMEOUT=15s`（screencap）
  - **#2 优化（2026-09-10）**：`dump()` 超时即失败、**不再 `range(2)` 重试** —— 视频/动画期每次失败从
    `4s×2≈8s` 降到 ~4s；`dump(timeout)` 新增形参，`nodes()/find()` 透传。`_close_ad` 关闭广告时传更短
    `workflow.ad_close_dump_timeout`（默认 **2.5s**，见 config.yaml）。
  - **A+B 优化（2026-09-11）**：确认路径 `_back_at_taskcenter(timeout)` 也透传 `timeout`，`_close_ad` 各
    确认处传 `ad_close_dump_timeout`（2.5s）——此前短超时只覆盖「关闭按钮查找」，确认路径 dump 仍 4s
    （09-11 实测代柯 32s、游迦 21s 的浪费主因）。另加 `workflow.ad_close_settle`（默认 **2.0s**，见
    config.yaml）：关闭 tap 后沉降等待，让关闭动画/任务中心重载播完再确认，避免动画期撞超时；快路径
    机器人会多等该秒数，实测变慢可调小到 1.0~0。

---

## 二、代码文件结构与当前状态

### flow.py（核心，正在改，已验证）
- `_nav_robot_list()`：联系人 tab(y>=1850) → 机器人分类(y 900~1250) → 展开「我添加的机器人」
- `_collect_robot_names(max_scroll=12)`：**自动抓取机器人列表**，已稳定抓到完整 12 个
  - 识别规则：昵称节点 `x1∈[180,195] && y1∈[350,1850] && 宽≤230`
  - 滚动停止判据（**2026-09-10 修复死代码**）：`before` 取在本屏节点**遍历之前**，
    `len(seen)==before` 表示本轮零新增 → `no_new+1`，连续 3 屏零新增才停；有新名字则归零
    （旧写法把 before 放在 swipe 之后比较 → 条件恒真 → 第 3 屏必停 → 超长列表静默漏收，见坑 9）
  - 先无条件 swipe_down ×7 滚回列表顶部，再向上滚动收集，去重、跳过「内测中」
  - 带编号重复昵称（小麦/小麦1/小麦2）视为不同条目保留
  - 实测稳定：12 个 = 代柯、尔尔、古禹、李宥恩、黎小姐、裴旖、藤非、小麦、小麦1、小麦2、席恩、游迦
- `_enter_taskcenter(name)`：进列表 → 点机器人 → profile「发消息」(wait_for×6) → 聊天页「个人」(y>=600) → 任务中心
  - **方案 A（2026-09-10）**：等待窗口未确认任务中心特征时 OCR 一次，命中 心动卡/高级模型/
    立即续费 → 判定误入心动卡会员 H5（WebView，dump 穿透读不到，只能 OCR）→ return False
    走上层 `_safe_back_to_robot_list` 复位重试；OCR 未命中 → 维持"版式差异，继续流程"
    （零影响看广告路径，用户明确要求不能动已调好的看广告）
- `_exit_taskcenter()`：**系统 BACK 键 ×3 退出**（EXIT_*_ARROW 坐标弃用，原因见坑 6），
  退出前仍调 `_scroll_to_top_of_taskcenter()`
- `_stable_in_ad_page()`：广告页二次确认（连续 N 次 `_back_at_taskcenter=False` 才放行盲点）；
  **L1**：`ui.dump_fail_streak > 0` 时直接 return False（dump 失败=状态未知，禁盲点）
- `_badcase_visible()` / `_dismiss_badcase(max_back=3)`（**L3**）：OCR 全屏查
  Badcase/反馈问卷/开始填写/感谢大家一直，命中即连续 BACK 退出
- `_signin()` / `_feedback()`：已验证
- `run_robot()`：进入任务中心成功后先 `_dismiss_badcase()`（**A3**，覆盖签到+反馈两动作，
  进入失败路径不清理），再按 flag 执行签到/反馈；run_all 广告循环每轮前也调 `_dismiss_badcase()`
- **首轮广告（2026-09-10 用户优化）**：`run_robot(first_ad=True)` 在签到/反馈完成后
  **同一会话**再巡检一次 + 看 1 次广告才退出；`run_all` 在 target>0 且有签到/反馈任务时
  对第一轮每台传 `first_ad=True`，剩余次数交阶段二轮转补足 —— 每台省一次完整进出
  （~70-95s × N 台）。首轮广告失败不重试不影响 run_robot 返回值（交轮转补看）。
  `--no-ad`（target=0）与 `--ad-only`（跳过第一轮）行为不变（单测锁定，172/172）。
- `_taskcenter_confirmed_by_ocr()`（**方案 A 2026-09-10**）：OCR 查任务中心特征词，
  判定"画面是否在任务中心" —— **替代**依赖 dump 的 `_stable_in_ad_page()`
- `_watch_ad_once()`：**广告关闭有坑，见第三节"坑"**
  - `_close_ad(max_tries)`：**关闭顺序 2026-09-10 傍晚对调（用户优化）：uiautomator 优先** ——
  0) 步骤 0：OCR 连续 2 次判"已在任务中心"（快路径，广告自动播完直接收工；
  **双检保留**——广告创意可能含「每日签到」字样，单检误判会让整条广告白耗）；
  1) **uiautomator 直关**：`_find_close_node()`（精确「关闭广告」→「跳过」/「关闭」
  候选）命中即点其 bounds 中心（F11 认可的节点点击，17:29 实测 OCR 漏读药丸、
  dump 一次命中 (141,150)，省 ~9s OCR 空试）；
  2) OCR 正向定位关闭按钮 → 点其自身坐标（视频播放期 dump 有界失败后的可靠读屏）；
  3) 兜底循环：**任务中心校验 → uiautomator → OCR → 物理 BACK 封顶**（校验必须先于
  uiautomator：防 dump 残留节点在任务中心页被误点，坐标带与 banner 重叠）。
  历史注：OCR 曾排第一（坑 9 时代 dump 无超时会 25-50s 死等）；DUMP_TIMEOUT=4s
  看门狗上线后 dump 失败已有界（≤9s），长广告（>23s 未播完）多花 ≤9s 可接受。
  相关辅助：`_ocr_shot()` / `_ocr_find(texts, region)` / `_dismiss_badcase()` / `_find_close_node()`

### main.py（已重写为 adb+flow 版 CLI）
- `py -3.13 main.py`：全自动（自动抓列表 → 第一轮每台 签到+反馈+看 1 次广告（同会话）
  → 阶段二轮转补足剩余广告至 10/10）
- `--list`：只抓取并列出机器人
- `--robots "A,B"`：手动指定覆盖自动抓取（**仍受白名单约束**）
- `--no-signin` / `--no-feedback` / `--ad-only`：功能开关
- `--ad-times N`：每台看广告次数
- **白名单过滤（2026-09-09 18:18）**：只对 `workflow.robot_whitelist` 内昵称执行；
  名单为空 = 不过滤。命令行 `--robots` 同样受约束；要跑名单外需临时改 config。

### 其它文件
- `adb_ui.py`：adb 驱动层（swipe 已适配 1080×1920；nodes 跳过零尺寸节点；dump 看 "dumped to" 输出而非 rc）
  - **2026-09-10 看门狗**：`_run()`（tap/swipe/back/cat 通用入口）超时 `CMD_TIMEOUT=20s`、
    `dump()` 超时 `DUMP_TIMEOUT=4s` —— 超时转 `AdbCommandError` / 叠加 `dump_fail_streak`
  - 此前**全部 subprocess.run 都无 timeout**（仅 is_online 有），adb daemon 挂起会让
    无人值守流程永久卡住
- `config.yaml`：udid(`emulator-5554`)、app 包名、timing、workflow
  （`ad_times_per_robot=10`、`ad_wait_min/max=16/18`、`ad_cooldown=60`、`ad_close_retries=12`、
  `ad_close_max_backs=3`、`robot_whitelist` 10 个昵称）
- `e2e_ad_once.py` / `e2e_robot_once.py`：真机验证脚本（单机器人看一次广告 / 签到+反馈）
- `signin_feedback.bat`（--no-ad） / `watch_ad.bat`（--ad-only） / `ad_test_all.bat [COUNT]`
  （全白名单手动跑广告，默认每台 1 次）
- `scripts_test/snap.py`：截图+OCR+uiautomator 三合一 dump 工具（`py -3.13 scripts_test\snap.py <tag>` → logs\snap_<tag>.txt）
- `scripts_test/shownodes.py`：解析 dump 里的 Node（Windows 控制台避免中文乱码时用）
- `legacy/`：**旧 Appium/OCR 框架已整体移入**（driver/locators/ocr_utils/robot/scheduler/signin/feedback/ad_watcher
  + robot_state.json/collected.json + appium_caps/），无任何活跃代码引用，仅作历史参考，随时可删。

---

## 三、踩过的坑（重要！下次别重蹈覆辙）

### 坑 1：广告关闭 —— 2026-09-08 已基于实机证据重写并双机验证通过 ✅
**现行方案（flow.py `_close_ad`，2026-09-10 17:45 定稿）**：快路径(OCR 双检) → uiautomator 直关
→ OCR 正向定位 → 兜底轮询（任务中心校验 → uiautomator → OCR → 物理 BACK 封顶）。
**固定坐标盲点已于 F8 全部删除**（见坑 13）。
- 实机发现：广告倒计时期间 uiautomator **能读到**广告页节点（旧结论"WebView 穿透读不到"不成立，
  那只发生在 35s 后的视频播放期，此期 dump 持续失败、OCR 仍可读）。
- 广告 ~12s 倒计时后才能关闭；点击左上关闭一次成功。
- 回任务中心判定：uiautomator 见"每日签到" + OCR 顶部无关闭按钮 **双重确认**（防穿透误判）。
- E2E 实证：古禹（uiautomator 路径第 1 次关成功）、裴旖（视频广告 dump 失败 ×4 自动降级 OCR 第 1 次关成功）。
- 遗留观察点：类型 B 第三方广告（OCR 读不到关闭文字）本轮未复现，遇到再补；FAQ 中间页未复现。
- **MuMu 特性**：uiautomator dump 正常也可能 rc=139 结尾（段错误），判断成功看 "dumped to" 输出+文件新鲜度，
  不能看 rc；但视频播放期 rc=139 且无输出是真失败，adb_ui.dump 已能区分并优雅降级。

### 坑 2：tesseract OCR 慢 + bash 超时
- pytesseract 全屏 OCR 每张 3~5 秒，导致整个流程很容易超过 bash 工具默认 2 分钟超时 → 进程被中间杀掉、设备卡半途、需手动恢复。
- **修复方向**：传参时把 bash timeout 调大（如 300s）；或只对广告关闭用 OCR（区域裁剪顶部 region 加速）；`Flow` 初始化时已从 config 读 `ocr.tesseract_cmd`（不再报 TesseractNotFound）。
- **重要**：tesseract_cmd 必须设置，路径 `C:\Program Files\Tesseract-OCR\tesseract.exe`（flow.py 已从 config.ocr.tesseract_cmd 读取）。

### 坑 3：设备当前状态残留 / 导航前提
- `_enter_taskcenter` / `_collect_robot_names` 假设当前在 QQ 主界面（或联系人-机器人列表）。
- 若上一流程被中断（bash 超时杀死进程），设备可能停在 聊天页/profile/广告页/FAQ页/甚至桌面。
- 手动恢复：多次按 `input keyevent 4` 或用 `am start -n com.tencent.mobileqq/...SplashActivity` 拉回 QQ，再导航到机器人列表。

### 坑 4：Windows 控制台中文乱码
- uiautomator Node 文本在 PowerShell 打印是乱码（GBK/UTF-8 编码问题），**但 Python 内部字符串是正确的**，`_find("中文")` 精确匹配不受影响。
- 排查定位时用 `scripts_test/shownodes.py`（把 Node 写到文件再读，或 print 时避免中文）避免乱码干扰。

### 坑 5：重复昵称机器人
- QQ 对重复昵称自动加编号：小麦、小麦1、小麦2 是**3 个不同条目**，都要处理，不能当成同一个。
- `_collect_robot_names` 已按字符串去重（保留编号区分）。

### 坑 6：banner 升级 → 一切坐标 tap 退出都误开 Badcase（2026-09-09/10 已修复 ✅）
- banner「QQ AI好友·常见问题答疑」升级为覆盖 y≈0-440 的可点击入口后，EXIT_*_ARROW 三层
  坐标 tap 全部作废（原坐标也落在 banner 区域内 → 误开《QQ AI好友 Badcase 反馈问卷》）。
- **修复**：三层退出全部改 `self.ui.back()`（keyevent 4）。
- **Badcase 真实触发机制**（用户反例确认）：不是"停留任务中心够久就触发"（程序停任务中心
  4 分钟无问卷），而是**操作触发** —— 点签到/反馈按钮后随机弹；此外**普通推广广告**
  （悦己官/种植牙等 H5，无"关闭广告"文字）也会在签到后随机弹出覆盖任务中心。
- 曾误判"停留触发"的中间态：uiautomator dump 失败时 `_back_at_taskcenter` 恒 False → B+
  误判"在广告页" → 放行盲点 AD_CLOSE 命中 banner（dump 失败是前置条件）→ L1 修复。

### 坑 7：心动卡会员 H5 误入 → 签到点错行（2026-09-10 方案 A 防御 ✅ 待真机复核）
- `_enter_taskcenter` 等待窗口没确认到任务中心特征时曾用"版式差异，继续流程"放行 →
  `_signin` 在错页把"看广告入口"当签到行点 → 弹游戏激励广告（超自然行动组 40s+）挡反馈。
- 已存在的"签到次数 1/1 校验"拦不住：因为根本不在真任务中心，ratio 读到 None/0/1。
- **修复（方案 A）**：`_enter_taskcenter` 未见任务中心特征时 OCR 一次，命中 心动卡/高级模型/
  立即续费 → return False → 上层 `_safe_back_to_robot_list` 复位重试（最多 3 次）；
  OCR 未命中 → 维持放行（行为不变，零影响看广告）。
- **关键**：心动卡 H5 是 WebView，uiautomator dump 穿透读不到 → 错页检测必须 OCR。

### 坑 8：Badcase 防线只挂广告循环，漏了签到/反馈链路（2026-09-10 A3 部分修复）
- L3（_dismiss_badcase）初版只挂在 run_all 广告循环每轮前，run_robot（签到+反馈）裸奔 →
  00:34 实测 Badcase 在 _feedback 操作中途弹出，脚本 dump 穿透读到背景"返回"完成反馈，未察觉问卷。
- **A3**：run_robot 进入任务中心成功后、签到前调一次 _dismiss_badcase()（覆盖签到+反馈两动作，
  进入失败路径不清理）。开销 ~2.3s/台（全屏 OCR 1.3s + sleep 1s），10 台 +23s（~2.5%），用户拍板接受。
- **仍管不到**：_feedback **操作中途**（tap 反馈按钮后）弹出的 Badcase（A 类）与签到后随机弹的
  普通推广广告（B 类）→ 见待办 P0-1 / P0-2。

### 坑 9：安全守卫过严 → 固定坐标永远点不到 → 广告关不掉（2026-09-10 方案 A 修复 ✅）
- **症状**：用户自测 `--ad-only`，广告播完后迟迟关不掉；run.log 01:11 那次播完到关闭
  耗时 **2 分 05 秒**，01:15 那次直接卡到手动停止（**完全死锁**）。
- **根因**：视频播放期 UI 无法 idle → uiautomator dump 必然失败（rc=139 无输出）→
  L1 让 `_stable_in_ad_page()` 恒 False → **第 0 步与第 4 步两条通往 `AD_CLOSE` 的路
  全被拦死** → 只剩 OCR 一条活路，OCR 再读不到"关闭/跳过"文字就彻底失败。
- **性能放大**：`adb_ui.py:148` 无 timeout → dump 每次空等 ~12s，`dump()` 内还重试 2 次
  → 一次稳定检查 25-50s。
- **修复（方案 A）**：判定"能否盲点"改用 **OCR 负向排除**（`_taskcenter_confirmed_by_ocr`）：
  读不到任务中心特征词 = 仍在广告页 → 直接 tap AD_CLOSE；读得到 = 已在任务中心 → 不盲点。
  既不依赖 dump，又保住了 banner 防误触；再加 `_dismiss_badcase()` 自愈兜 OCR 漏读。
- `_stable_in_ad_page()` 随之退役（保留函数 + 单测，docstring 已标注）。

### 坑 10：滚动收集的停止判据是死代码 + adb 无超时（2026-09-10 修复 ✅）
- `_collect_robot_names`：`before = len(seen)` 取在 swipe 前、比较在 swipe 后，中间没人
  改 seen → 条件恒 True → `no_new` 每轮必 +1 → **第 3 屏无条件 break**（`else` 永不可达）
  → 列表超过 3 屏被静默漏收。**实锤**：run.log 01:10「最终处理 9 个机器人」缺白名单内的
  **游迦**（昨日误判为"滚动深度问题"）。修复：`before` 移到遍历之前。
- 所有 `subprocess.run` 无 timeout（除 is_online）—— 无人值守跑 2h+ 时 adb daemon
  挂起会永久卡死。修复：`_run` 20s / `dump` 4s / `_ocr_shot` 15s。

### 坑 11：OCR 整词匹配 vs tesseract 中文分词 → 任务中心判定恒 False → 无限 BACK 退穿 App（2026-09-10 修复 ✅）
- **症状**：11:58 复测 4 台 ×3 次广告，代柯第一条广告播完（21.3s）后**连续 12 轮关不掉**，
  日志从头到尾只有「未定位到关闭按钮(N次)…改为物理 BACK」。停脚本抓现场：
  **前台已是 MuMu 桌面（launcher）**，QQ 进程仍在后台 —— 即 BACK 一路把整个 App 退穿了。
- **取证方法**：新增 `scripts_test/trace_ad.py`（monkey-patch `Flow._ocr_shot`，把每次
  OCR 实际看到的画面存成 `screenshots/trace/ocr_NNN.png`）+ `scripts_test/replay_frames.py`
  （离线把帧喂给**当前真实**判定函数）。这套"存帧 → 回放"是后面定位/回归的关键工具。
- **根因（帧 #001/#006 铁证）**：tesseract(chi_sim) 把连续中文**切成单字/碎词** ——
  任务中心标题读到 `任务`+`中`+`心`，行名读到 `每`+`日`+`签到`，按钮读到 `获取`+`随机`。
  而 `_TC_KEYS_OCR = ("每日签到","任务中心","获取随机","收支详情")` 用的是**整词子串匹配**
  `k in t` → **四个关键词全部失配** → `_taskcenter_confirmed_by_ocr()` 恒 False →
  `_back_at_taskcenter()` 恒 False → `_close_ad` 认定"广告没关" → BACK 到底。
- **为什么"关闭按钮"却读得到**：广告页同样被切成 `关闭`+`广告`，但 `_close_ad` 的关键词表里
  额外带了短词 `关闭`，所以勉强命中 —— 这就是"关闭能读到、任务中心读不到"的根因，两类页面
  判定口径本来就不一致。
- **时序真相**（帧 #003→#006）：21.8s 等待期内广告**已自动播完并回到任务中心**
  （帧 #006 显示「看广告 10/10 已完成」），但判定读不到任务中心 → 盲点 (160,152) 落到
  顶部 banner（banner 占 y≈125-380，正好盖住 `AD_CLOSE`）→ 帧 #012-#021 是误开的
  **AI 好友「常见问题」帮助中心 H5**，而 `_dismiss_badcase()` 也认不出来（见下），
  于是继续 BACK 直到退出 QQ。
- **修复 A（核心）**：`_ocr_find` 增加**第 2 遍跨词元拼接匹配** —— 整词全部失配时，把各词元
  按 OCR 阅读顺序直接拼接成字符串再定位子串，返回命中跨度内各词元中心的均值。实测回放：
  加第 2 遍后，#001/#002/#006-#011/#022-#025 全部正确命中任务中心，#003-#005（广告页）
  仍正确不命中。
- **修复 B（F6）**：第 4 步"盲点超限改 BACK"的**连续 BACK 封顶**（`ad_close_max_backs`，
  默认 3），超限直接 `return False` 交 `run_all` 失败计数 + `_safe_back_to_robot_list()` 复位。
  旧实现按 `ad_close_retries=12` 一路退到桌面（越退越远且不可逆）。
- **修复 C（F5）**：`_AI_FRIEND_HINTS` 补入帮助中心 H5 的**独有**词（`额度消耗规则`/`心动卡`/
  `必须付费才能`/`免费额度的途径`）。旧词表只有 `常见问题答疑`/`QQ AI好友`，而打开后的页面
  标题只有「常见问题」（无"答疑"）→ 旧词表两项都命中不了 → H5 全屏 10 帧毫无察觉。
  注意 OCR 把 "AI" 读成 `Al`（小写 L），`AI好友` 也不可靠。
- **遗留风险**：`AD_CLOSE=(160,152)` 与 banner 区（y≈125-380）物理重叠，只要出现"广告已关
  但判定认为没关"的窗口期，盲点仍可能点开 banner H5。现靠 修复 A（判定准）+ 修复 C（自愈快）
  双重覆盖；根治需要把 banner 滚出视口或改为"仅在 OCR 正向读到关闭按钮时才点其坐标"。
- **真机复验（2026-09-10 12:43，尔尔 ×1，带存帧）**：✅ 全链路通过 ——
  `12:42:29 广告播放 22.6s → 12:43:20 广告已关闭（固定坐标直关路径）→ 12:43:48 已回到
  机器人列表 → 看广告结束（本会话 1/1，失败 0）`。回放 7 帧确认：#001/#002 任务中心 YES、
  #003-#005 广告页（关闭按钮 (99,152)）、**#006/#007 任务中心 YES（修复前这两帧必然 MISS）**、
  AI 好友 H5 全程未误报。对比修复前同场景 = 12 次 BACK 直到退出 QQ。

### 坑 12：`_looks_like_robot_list()` 被 dump 残留节点误伤 → 导航空转（2026-09-10 发现；D1 + P0 两步已修 ✅）
- **现象**：12:29 那轮从异常页恢复后，`_nav_robot_list()` 反复报「未能确认在机器人列表」，
  但截图显示页面**就在机器人列表**（机器人分类已选中）。连续 2 次告警后带 `looks=False`
  硬点行 → 误开机器人的**个人资料卡** → 之后整轮漂移。
- **根因**：那次 dump 是**多页残留节点穿透混合** —— 同一次 `nodes()` 里同时含机器人列表
  （机器人 sel=True）、联系人页、**代柯个人资料卡**（含「发消息」按钮）。而
  `_looks_like_robot_list()` 的否决条件是 `any(k in texts for k in ("任务中心","每日签到","发消息"))`
  → 残留的「发消息」直接把它判成"不在列表" → 永远无法确认 → 导航空转。
- **本质**：MuMu/WebView 的 dump 穿透已知问题（代码注释里多处提到），但用"**任意**节点文本
  存在"做否决判据会被残留节点污染。
- **修复 1（D1，2026-09-12）**：判据重构为【先看正信号再谈 veto】—— 正信号
  （QQ 主壳 `_on_qq_main_shell` + 机器人分类 selected `_robot_cat_selected`）成立即是列表
  充分证据；`任务中心/每日签到` 整页特征任意位置否决；`发消息` 仅在 y>=FORBIDDEN_ACTION_Y(1400)
  才否决，中区残留视为**残影容忍**并打 warning。新增常量 `FORBIDDEN_ACTION_Y=1400`。
- **修复 2（P0 单快照，2026-09-15）**：判据内部原 3 次 `ui.nodes()`（自身 + 两个子判据），
  而 nodes() 0.8s TTL 以 **dump 开始时刻**计龄，MuMu 单 dump 2.4-3.8s > TTL → 3 次调用必然
  各自重新 dump（一层校验 3 次 dump ≈ 7.2s）。改为**单快照**（一次 nodes() 判全部信号，
  新增 `_robot_cat_on_nodes(cur)` helper）—— 每层校验 1 次 dump，退台层 9.9s→5.2s、
  退台合计 30.6s→17.2s 中位，进台找行 11.4s→6.8s。**教训：TTL 缓存的时间戳取在 dump 之前，
  长 dump 必然击穿 TTL，多判据共用时要么传快照要么重新校准 TTL。**
- **残余局限（TC 卡死家族，未根治）**：任务中心重载动画期 dump 连续超时 → 前提校验/确认
  判不出 TC → OCR 也未命中 → `_safe_back_to_robot_list()` 过度返回 → 落在列表但强判据
  False（残影混合）持续数分钟（09-14 小麦、09-15 代柯/黎小姐各 1 次；09-15 午间全量场
  12 次换台 0 发作，18 次 dump 超时全部自愈）。治理方向：前提校验 dump 超时分支加 OCR
  优先复核，抬高安全返回触发门槛（未实施）。

### 坑 13：固定坐标盲点 + 条带过大 → 盲点落到顶部 banner 误开 AI 好友 H5（2026-09-10 F8 修复 ✅）
- **现象**：11:43 / 11:58 / 12:25 三次实测，广告自动播完、页面已回任务中心后，程序仍
  tap 固定坐标 `AD_CLOSE=(160,152)` → 命中任务中心顶部「QQAI好友·常见问题答疑」banner
  → 误开 **AI 好友帮助中心 H5**（`常见问题` 页）→ 后续导航漂移。
- **根因（四段，缺一不可）**：
  1. **坐标物理重叠**：`(160,152)` 在广告页 = 左上「关闭广告」药丸，在任务中心页 = 顶部
     banner 命中区（banner y≈125-380 与药丸 y≈107-187 重叠）。
  2. **banner 不在 dump 里**：它是 WebView **自绘**元素，任务中心整页仅 ~20 节点、顶部区
     无可点击节点 → **任何 uiautomator dump 守卫都拦不住**（`probe_banner.py` 实证）。
  3. **"方案 A" 把放行写成负向**：读不到任务中心特征词就点 → 广告自动播完、页面已切回任务
     中心而 OCR 还在读过渡帧时，负向条件成立 → 盲点落 banner。**这是对 09-09 B+ 修复（正向
     OCR 判定）的回退**。
  4. **条带高度 430 过大**：tesseract 在大片深色视频背景上放弃分页、整块返回空 → 连肉眼极
     清晰的「关闭广告」也读不出（裁到 340×240 就能读出）→ 当时只能靠固定坐标盲点兜底。
     扩样实测：**430→6/9 命中；320/300/260→9/9 命中；三种高度对任务中心均 0 误报**。
- **修复 F8（`flow.py`）**：
  - `AD_TOP_REGION`: `(0,0,1080,430)` → **`(0,0,1080,320)`**。
  - **删除所有固定坐标盲点**（第 0 步直关 + 第 4 步兜底都删），`AD_CLOSE` 标注「已弃用」。
  - 新增 `_ad_close_pos()`：OCR 正向定位关闭按钮，**点它自己的坐标**；读不到 → **绝不盲点**，
    落进 OCR/uiautomator 正向定位 + 物理 BACK（封顶 `ad_close_max_backs`）兜底。
  - 回归证据（`replay_frames.py` 回放现场帧）：任务中心帧 **19/19** 判 YES 且不返回坐标；
    H5/资料卡反例帧（代柯 012-021、026-041）**全部返回 None、零盲点**；真实广告帧 **9/9**
    正向读到关闭按钮（`(99,152)`）。
- **本质**：**在无法可靠读屏时，"点一个固定坐标" 永远是危险的** —— 坐标在不同页面语义不同。
  正确做法是「读不到就不点」，把不确定性交给有上限的通用兜底（BACK）。

### 坑 14：推广 banner 纵坐标随版式漂移 → 任何"固定禁点区"都盖不全（2026-09-10 F11 根治 ✅）
- **现象（用户指定：必做项·最高优先级）**：任务中心页的推广 banner「QQAI好友·常见问题
  答疑」被点中 → 误开 AI 好友反馈 H5 → ~37s 后延迟弹「QQ AI好友Badcase反馈问卷」。用户
  反复反馈「点到 banner 图」「广告时跳到问题反馈」。
- **关键实测（非臆测，2026-09-10 两台真机 dump + 截图）**：
  - banner **不在 uiautomator dump 里**：`probe_f11_live.py` 进代柯任务中心 → dump 仅
    **20 个节点**，顶部区只有「客服」「收支详情」等真实控件，**无 banner 文案**。
  - banner **纵坐标随版式漂移**：**游迦页 y≈116-366**（顶格，电量卡之上）；
    **代柯页 y≈1151-1410**（在「开通解锁以下专属权益」卡之下、电量卡之上）。
    ⇒ **没有任何固定 y 区间能覆盖所有版式** —— 我第一版按 y<440 做"顶部禁点区"，
    被这一实测直接否定（差点写成臆测）。
- **根治（F11，与版式无关）**：**只要不盲点坐标就永远命中不到它** —— banner 不在 dump，
  故所有基于真实 dump 节点的 `_tap_node` **天然安全**。于是规定：
  - `flow._tap()` 在**任务中心页**（`_page_tc=True`）**拒绝一切坐标点击**，除非显式
    `trusted=True`。合法例外仅 2 处：① `_close_ad` 广告页关闭按钮（OCR 正向定位、非盲点）；
    ② `_signin` 签到浮层按钮（浮层已由 `wait_for("每日免费领")` 确认在屏）。
  - `_signin` 的关闭 ✕(996,1143) 改为**浮层仍在屏才点**（该坐标在代柯版式里离 banner 顶
    y=1150 仅 7px，是残余盲点）。
  - `_page_tc` 状态位：进任务中心置 True，退出 / `_nav_robot_list` / `_safe_back_to_robot_list`
    置 False —— 保证**列表页机器人首行（y≈410-500）、联系人页『机器人』分类行（y≈201-352）**
    仍可正常点击（否则导航直接瘫痪）。
- **另一问题：问题反馈/静默假成功**——`_back_at_taskcenter()` 增加**覆盖层闸门**
  `_overlay_visible_in_top()`：判成功前用问卷**独有词**（`Badcase`/`反馈问卷`，任务中心
  绝不含「反馈问卷」，任务行只有「问题反馈」4 字）扫顶部条带，命中即判"未回任务中心"。
  只在成功路径执行 1 次 OCR（~1s），不影响点击效率。
- **实测证据**：
  - 离线（真机现场帧，`scripts_test/verify_f11.py`）：4 张真问卷帧 → 覆盖层 4/4 命中；
    9 张任务中心帧 + 3 张广告帧 + 3 张 H5 帧 → 覆盖层 **0 误报**；守卫 4/4 符合预期。
  - 上机（`scripts_test/probe_f11_live.py`）：`_page_tc` False→True→False；dump 无 banner
    文案；`_tap(160,152)` 与 `_tap(540,1200)` 均被拒绝、H5 未出现、仍在任务中心。
  - 上机端到端（古禹 ×2 广告）：**2/2 成功、失败 0**，日志 **0 次「拒绝 tap」**（说明流程
    已无任何盲点尝试，守卫无需触发=理想态），OCR 正向关闭正常。
- **本质教训**：**"固定坐标/固定区域"在版式会变的 WebView 页面上不可靠；正确的不可变前提
  是"只点 dump 里真实存在的节点"** —— banner 不在 dump，这条规则自动免疫。

### 坑 15：机器人列表收集从列表中段开始 → 顶部机器人静默漏收（2026-09-15 P1 修复 ✅）
- **症状（两次实锤）**：09-14 11:55 收集 10 个缺 代柯/尔尔/古禹；09-15 14:25 收集 8 个
  连 小麦1/2/3 也缺。两次共同点：**首屏从 李宥恩 开始**（正常 run 首屏 = 代柯→尔尔→古禹），
  且都以「连续 3 屏无新增 → 判定底部」提前结束。
- **根因**：**新进程启动导航后，列表页会恢复上次的滚动位置**（上次 run 收集结束时停在
  底部附近），旧写法盲滑 7 次 `swipe_down` 且没有任何"已在顶部"的确认 → 实测停在列表
  中段，起点上方的机器人**永远收不到**；随后向下收集 +「3 屏 seen 无新增」误判到底提前
  结束。与坑 10 同族（收集完整性），但触发条件是"跨进程滚动位置恢复"，坑 10 修复管不到。
- **修复（两阶段收集，d406456）**：
  - 新增 `_screen_robot_names()`：本屏昵称过滤逻辑抽出，两阶段共用。
  - Phase 1：快滑 4 次 + **下滑收敛**——反复下滑对比本屏名字序列，连续 2 屏完全相同 =
    已到顶部（列表中部每次下滑必然露出新行；不依赖标记节点，实机 dump 确认列表页**无
    「我添加的机器人」分组头**，无标记可用）。8 次未收敛告警降级。
  - Phase 2：底部判定同步改为**「连续 2 屏内容不变」**——旧判据「3 屏 seen 无新增」会被
    Phase 1 已收的中段内容干扰（从顶部重访中段时 seen 零新增连续累积 → 提前误停）。
- **代价**：起点在底部时 Phase 1 多 ~4 次 dump+swipe（每进程仅 1 次收集，+8~12s）；
  起点已在顶部时比旧 7 次盲滑**更快**（2 次 dump 即收敛）。
- **单测**：236 → 238（中段起点收齐顶部 / stop_when 到顶即停 2 条新增 P1 回归）。

---

### 看广告 CD 重叠优化（2026-09-10 傍晚，用户提出）
- **CD 从广告关闭时刻起算**（`t_close`），配额复核挪进 CD 窗口末尾（cd-10s 处）——
  校验耗时与 CD 重叠，不再串行累加。旧实现每次看完立即读屏复核，正撞任务中心
  WebView 重载动画期 → dump 必超时（4s×2），两次复核烧 ~43s 加在 60s CD 之前
  （18:02 黎小姐实测蓝框段）。
- **算术收尾**：进台 `_read_ad_ratio()` 读一次 X/10 基数，`基数 + 本会话已看 >= 配额`
  → 必满 → **不读屏、不等 CD** 直接退出；基数读不到（行被覆盖）回退 CD 窗口内的
  屏幕复核（此刻页面已稳定，dump 不再超时）。
- 单测 174/174（新增 CD 窗口屏幕复核兜底用例 + 算术收尾不读屏用例）。

### 满额直退（2026-09-10 18:55，真机验证通过）
- **根因（真机 dump 实锤，两层叠加）**：① X/Y 计数在 dump 里被拆成多节点
  （`'10'` `'/'` `'10'` 三个独立节点），`_row_completion` 只做单节点整词 fullmatch → 恒 None
  → 配额校验整体失效；② 满额后按钮文案「获取随机」→「已完成」（行标题「看广告」不变）
  → `_find_row("获取随机")` 空滚 28s，期间易被 ~40s 重弹的问卷盖住。
- **修复（flow.py）**：
  - `_row_completion` 第 2 遍：行内节点按 x 序拼接后 `search(r"(\d+)\s*/\s*(\d+)")`；
  - `_find_scroll_match` 谓词化 + 新增 `_find_scroll_any` 多词同轮；`_find_row` 加 `alt_labels`；
    `_read_ad_ratio` / `_watch_ad_once` 统一用 `_find_row("看广告", alt_labels=("获取随机",))`；
  - `run_all` 进台首检前先 `_dismiss_badcase()` 清场（问卷盖屏会让 ratio 读到 None 误判"未满"）。
- **验证**：单测 179/179；实机 `--ad-only --robots "代柯"` → 18:53:26 启动 → 18:54:27
  「看广告已达每日配额，本会话直接退出」→ 18:54:53 回列表，**1m30s**（修复前同命令 4m52s 全失败）。

### 广告等待区间逐步收紧（2026-09-10 21:43 → 2026-09-11 00:06）
- `config.yaml` `workflow.ad_wait_min/max`：20-23s → 18-20s → **16-18s**（用户逐次确认）。
  机制不变（等待窗口覆盖 ≤15s 视频广告 + 加载延迟，等待结束进 `_close_ad`）。

### 设备重启恢复（2026-09-11 00:06 实测）
- 设备重启后 `adb devices` 空列表 → `adb kill-server && adb start-server` 后自动找回
  `emulator-5554`。**不要再用 `adb connect 127.0.0.1:16384`**（网络转发口，daemon 重启必丢）。

### 2026-09-11 00:09–00:34 全量实测（首轮广告同会话 首次全量跑）
- 命令：`py -3.13 main.py`（默认全流程，白名单 10 台，脚本自动收集 12 台 → 跳过 姐姐/老婆）。
- **首轮广告（`first_ad=True`）10 台全部生效**：代柯/尔尔/古禹/李宥恩/黎小姐/裴旖/藤非 均出现
  「首轮广告完成（剩余次数进入轮转阶段补看）」，签到 1/1 + 反馈 1/1 + 同会话看 1 次广告 → 一次退出。
  关闭路径：uiautomator 直关（`(141,150)`）与 OCR 直关（`(99,152)`）都有命中，符合 17:45 对调预期。
- **小麦 00:32 误入心动卡会员页**（`fail_enter_wrongpage_003221.png`）：OCR 命中 → 判进入失败 →
  `_safe_back_to_robot_list` 复位 → **方案 A 防御正确生效**。
- 复位后 00:33 出现 **坑 12**：`未能确认在机器人列表；屏文=联系人|分组|…|机器人|…|裴旖|…
  饶枝枝x内测中` → `fail_nav_not_list_003330.png`。日志止于 00:34:11（进程被中断），
  该轮未跑完；小麦 + 坑 12 仍未闭环（与 P0-1/P0-2 同属待修）。

### P0（当前卡点，2026-09-10 全量验证暴露的三类问题）
- ~~彻底解决广告关闭~~ → **已完成（2026-09-10 F8 终版）**：先修 dump 守卫过严导致
  `AD_CLOSE` 点不到（坑 9）→ 一度改成「OCR 负向排除 + 固定坐标首选」，**但该方案回退
  了正向判定、引发盲点误触 banner（坑 13）** → **F8 定稿：删除所有固定坐标盲点，一律
  OCR 正向定位并点其自身坐标；条带 430→320 让深色视频广告也能读全**。单次关闭 ~10s 级，
  死锁消除且无 banner 误触（见坑 1 / 6 / 9 / 13）。
- ~~Badcase 误开（banner 升级 / 盲点误触）~~ → **已完成**：系统 BACK 退出 + L3 OCR 巡检 + A3 入口守卫（见坑 6/8）。
- **P0-1 推广广告兜底（B 类，未实现）**：签到后 QQ 会随机弹**普通推广广告**（悦己官/种植牙等 H5，
  无"关闭广告"文字，`_close_ad` OCR 关不掉）覆盖任务中心 → `_feedback` 找不到行被跳过、退出时
  BACK 连退也出不去。预计方案：`_feedback` 前/中 OCR 检测推广页特征 → BACK 关掉再继续。
- **P0-2 Badcase 真正兜底（A 类，未实现）**：A3 只挡 run_robot 入口一次，管不到 `_feedback`
  **操作中途**弹出的 Badcase。预计方案：`_feedback` 每次 tap 后加 `_dismiss_badcase()`。
- **P1 `_exit_taskcenter` 强化（未实现）**：BACK 前先 OCR 检测是否仍在广告页（B 类无关闭文字，
  只能 BACK 连退 + 判回任务中心特征再走退出），避免安全返回失败。
- **P2 `_feedback` 容错（未实现）**：找不到反馈行时区分"已完成 1/1"与"被广告遮挡"，被遮挡走
  关闭/重试而非直接跳过。
- 以上修复落地后：10 台全量复验（签到 1/1 + 反馈 1/1 + 退出回列表），目标零手工干预。

### P1（全自动完善）
- **端到端实测 `main.py` 完整流程**（多机 + 60s CD 看广告轮转）→ 今日已大量真机验证（10 台签到+
  反馈、广告轮转连续跑），主体达成；剩余缺口即上方 P0 三类问题。
- 设备不在 QQ 主界面时的完整启动兜底（已做任务中心残留检测；还需覆盖 桌面/其它 app 场景，
  如 am start 拉起 QQ）。

### P2（健壮性/打磨）
- ~~清理临时测试脚本（test_e2e.py / test_ad.py）~~ → **已完成**：已改名为 `e2e_ad_once.py`
  （真机看一次广告）/ `e2e_robot_once.py`（真机签到+反馈），与 `test_*.py` mock 单测区分。
- 是否删掉已废弃的旧框架文件（driver.py/locators.py/ocr_utils.py/robot.py/scheduler.py/
  signin.py/feedback.py/ad_watcher.py）——注意 robot.py 的 RobotManager 还有列表读取逻辑可参考，
  但新 main.py 已不用。

### 2026-09-11 晚：#1 滑动漂移 / #2 dump 超时 / A+B 关闭确认优化（已落地，179/179）

**#1 滑动漂移**（用户 09-10 人工复现：任务中心上下滑会飘进 问题反馈/banner/签到）
- `adb_ui.SWIPE_X = 140`（左安全列）；`swipe_up`/`swipe_down` 起止 x 由中列 **540 → 140**。
- 原因：中列 540 正是「签到」按钮 /「问题反馈」/ 顶部 banner 命中区，滑动起止点落中列会被
  WebView 自绘页误判为点按这些元素而飘页；左列是列表留白/头像区，纯纵向滑动由 ScrollView 接管。
- 覆盖 `_find_scroll_match` / `_collect_robot_names` / `_scroll_to_top_of_taskcenter` 全部滚动调用。

**#2 dump 超时浪费**（日志 123 次 `uiautomator dump 超时` ≈8.2min/run 的主因）
- `adb_ui.dump(timeout=None)`：**超时即失败、不再 `range(2)` 重试**（视频/动画期每次失败
  4s×2≈8s → ~4s）；新增 `timeout` 形参，`nodes()`/`find()` 透传。
- `flow._find` / `_find_close_node` 透传 `timeout`；`_close_ad` 关闭按钮查找用
  `ad_close_dump_timeout`（默认 2.5s，config）。

**A+B 关闭确认优化**（09-11 实测代柯关闭 32s，定位到浪费在确认路径）
- **A**：`_back_at_taskcenter(timeout=None)` 透传 `timeout`，`_close_ad` 全部 7 处确认调用传
  `ad_close_dump_timeout`(2.5s) —— 此前短超时只覆盖「关闭按钮查找」，确认路径 dump 仍 4s。
- **B**：`ad_close_settle`（默认 2.0s，config）：关闭 tap 后沉降等待，让关闭动画/任务中心重载
  播完再确认。权衡：快路径机器人会多等该秒数；实测变慢可调小到 1.0~0。

**效率实测基线**（指标 = 定位关闭按钮 → 确认关闭，不含固定 ad_wait 16-18s；2026-09-11 全天）
| 时期 | 中位/均值 | 最差 | dump 超时 |
|---|---|---|---|
| 改动前（早 11-14，旧逻辑，145 次） | 18.7s / 19.6s | 32.9s | 61 次全 4.0s |
| #2 后（20:49 签到流，33 次） | 11.3s / 14.7s | 31.7s | 41 次（4.0s×4 + 2.5s×37） |
| A+B 后（当前循环，23 次） | 11.4s / 15.0s | 27.4s | 35 次全 2.5s |

- 结论：单次关闭中位 **18.7s → 11.3s，提速 ~40%（每次省 ~7.4s）**；A 把确认路径 4.0s 超时清零
  （B 期还有 4 次 4.0s → C 期 0）。按全天 ~100 次广告估，关闭阶段每天省 **~12 分钟**。
- 诚实：B→C 中位没再降（11.3→11.4），因沉降给快路径固定 +2s；换来更稳（最差 31.7→27.4s）。

**导航耗时基线**（52 样本）：点进机器人首页 → 任务中心可用 **中位 25.4s**（签到阶段 ~24s 最接近纯
导航；广告循环阶段名义 ~36s，多算了进任务中心后的配额读取）；列表定位（进入→点到该行）中位 16.0s；
全程（进入机器人 → 任务中心可用）中位 **50.5s**。主要是多次页面跳转的固定等待累积，非 dump 超时。

**CD 语义澄清**：`广告 CD 60 秒（从关闭起算）` 的起点是 **`t_close`**（flow.py:1565，
`_watch_ad_once()` 返回 True 后立即取）= 日志「广告已关闭，回到任务中心」那一刻，
**不是**「定位到关闭按钮」。

### 2026-09-12 性能优化第 1 批（更像人 + 更快；已落地，单测 181/181 全绿）

**背景**：用户要求「更像人类 + 更快」，四方案权衡后分 4 批实施，本批只含低风险快收益项。
（完整清单：第 2 批 D1 坑12修复 + A1 事件驱动替换固定 sleep + E2 导航基线对比；第 3 批
A5 RapidOCR + A6 半分辨率 + A7 onnxruntime-directml GPU + E3 回放验证；第 4 批 B1 CD 感知
EDF 轮换 + C1/C4 分布抖动 + C5/C6/C7 + E1 量化验收。第 2 批已落地，见下节；第 3-4 批尚未开始。）

**本批改动（全部已落地）：**
- **A2 截图 TTL 缓存**：`_ocr_shot` 加 0.8s TTL 缓存（`flow.py` `_invalidate_shot_cache` /
  缓存字段）；失效时机：界面动作 / TTL 过期 / 显式失效。`AdbUI` 新增 `on_action` 回调钩子
  （tap/tap_node/swipe_up/swipe_down/back 动作后 `_notify_action()` 触发），`Flow.__init__`
  挂 `ui.on_action = self._invalidate_shot_cache`。
- **A3 OCR 结果短缓存**：`_ocr_find` 加结果缓存（命中 0.5s TTL、未命中 0.25s TTL），
  cache_key = (texts, region, ymax)。
- **A4 `_dismiss_badcase` 廉价路径优先**：新增 `_overlay_scan()`（flow.py:1041-1073）——
  单次全屏 OCR 推理同判 Badcase/AI好友/任务中心三组词；`_dismiss_badcase` 的检测+复核
  均改用它，~2.3s → ~1.5s/次。
- **A8**：config.yaml `ad_close_settle` 2.0 → **1.0**。
- **C2 `_tap_node` 坐标抖动**（flow.py:342-356）：短边 25% 边距内随机偏移，落点必仍在
  bounds 内（F11 安全前提不变：点的是 dump 真实节点）。
- **测试配套**：Flow 新增类级缓存默认值 + `_init_cache_state()`（各测试 make_flow/setUp
  已补调，防类级共享字典单测互污）；test_flow_ocr_close.py TestBadcaseGuard 改 stub
  `_overlay_scan`；新增 test_overlay_scan_badcase_hit / test_overlay_scan_words_merged；
  test_nav_optimize.py ContactsNavUI 的 `tap_node` 改为 `tap(x,y)` 按落点 bounds 切 stage
  （适配 C2：`_tap_node` 不再走 `ui.tap_node` 而直接 `ui.tap(抖动后坐标)`）。

**单测基线更新：181/181 全绿**（原 179 + 新增 2）。

**A2/A3 缓存重要语义**：单测用 `Flow.__new__` 绕过 `__init__` → 必须显式 `_init_cache_state()`
建立实例级缓存；类级默认 `_ocr_result_cache = {}` 是共享字典，漏建实例缓存会单测互污
（已实锤：test_exact_token_wins_over_merged 的 (150,160) 泄漏）。

**后续批次备忘（勿忘）**：第 2 批先修坑 12（`_looks_like_robot_list` 被残留 dump 节点污染）
再上 A1 事件驱动（导航 50s→30s 目标）；第 3 批 OCR 换 RapidOCR（已装 rapidocr-onnxruntime，
30MB，实测质量显著优于 tesseract：完整行文本、同帧均值 ~1.3s vs tesseract 1.24-1.68s 且
切碎「天下归心」）+ 半分辨率 + onnxruntime-directml（GTX 1650 驱动 462.30 只支持 CUDA 11.x，
新版 onnxruntime-gpu 装不上，DirectML 是唯一 GPU 路线）；第 4 批调度与人味增强。

### 2026-09-12 性能优化第 2 批（D1 坑12 + A1 事件驱动导航；已落地，单测 191/191 全绿）

**第 1 批真机实测（用户日志复盘，已确认优化生效）**：正常机器人单台 129-155s（代柯 129s 最低），
导航 26s→15-16s（+约 10s/台），广告关闭链路稳定 42-44s（含固定 16-18s ad_wait），零固定坐标盲点。
裴旖/藤非两类 dump 失联场景（4-5 分钟瞎退）属 D1 待修，即本批。

**D1（坑 12 根治，flow.py `_looks_like_robot_list`）**：
- 判据重构为【先看正信号再谈 veto】：正信号（`_on_qq_main_shell()` + `_robot_cat_selected()`，
  x-bounds 校验）成立即是列表的充分证据；之后仅「确凿位置」的任务中心/聊天页特征才否决：
  - `任务中心`/`每日签到`：**整页特征，任意位置出现即否决**（防把真·任务中心当列表）；
  - `发消息`：仅当 `y >= FORBIDDEN_ACTION_Y(=1400)`（真 profile 底部操作栏按钮位置）才否决；
    中区/随机位置的残留「发消息」视为**残影容忍**并打 warning 日志（诊断用）。
- 修复目标：MuMu 多页残影 dump（穿透）里残留的「发消息」不再把真·列表页打成 False → 消除
  藤非/裴旖 4-5 分钟导航空转 + safe_back 瞎退。
- 新增常量 `FORBIDDEN_ACTION_Y = 1400`（flow.py:131）。
- **已知残余局限（不 over-engineering）**：残留节点若恰落在 y>=1400 仍会触发 veto（概率低，可后续
  用 dump 新鲜度/前台 Activity 判据根治）。

**A1（事件驱动导航，flow.py + config.yaml）**：
- 新增 `Flow._wait_until(pred, timeout, interval=0.5, desc)`：轮询 pred() 至 True 或超时；
  谓词异常（dump 失败）视为不满足继续等到 timeout；超时打 warning 回退到固定等待语义。
- 新增 `Flow._nav_target_wait()`：`max(timing.nav_wait=8.0, page_wait=2.5)` —— 保证最坏等待
  不短于旧固定 page_wait（只在页面就绪更快时提前返回，即省时来源）。
- `_nav_robot_list` 三处 tap 后固定 `sleep(page_wait)` 改为 `_wait_until` 轮询真实目标态：
  点联系人 tab → 等 `_contacts_tab_active()`；点机器人分类/分组头 → 等 `_looks_like_robot_list()`。
- 构造控制流/重试语义（dump_stuck→safe_back、BACK、4 次循环、break）**完全不变**。
- 页面动画起步 floor 由 `_tap_node` 点击 pause（click_min~click_max，真机 1.5-3.0s）兜底，
  不加额外固定睡眠。
- 新增 config `timing.nav_wait: 8.0`。
- **未动**：`_exit_taskcenter` 的 EXIT_LAYER_WAIT(1.2s)、`_signin` 固定 settle、`ad_close_settle`(A8)
  —— 均为真机调好的稳定性 settle，不在 A1 导航主范围。
- **风险（未单测）**：慢页最坏情况轮询会比单次固定 sleep 发更多 adb dump（~16 次/8s），
  正常快路径不受影响；真机实测验证。

**单测基线更新：191/191 全绿**（185 + D1 新增 4 + A1 新增 6）。
- D1 新增：TestLooksLikeRobotListResidue（残影容忍 / 真 profile 否决 / 任务中心否决 / F1 消息首页保留）
- A1 新增：TestWaitUntil（早满足 True/超时 False/谓词异常不满足）+ TestNavEventDriven（免全 page_wait、
  nav_target_wait>=page_wait）+ TestNavTimeoutSafety（目标永不出现→安全超时不盲点、_diag_shot 触发）

**E2（导航基线对比验证）**：待用户真机实测后对比导航耗时（历史基线：完整导航中位 50.5s，
第 1 批实测回列表→点到行 15-16s；A1 目标再降，进入→任务中心可用 50s→30s）。

### 2026-09-13：探活自愈内置 + 两类卡死根治（f6eb43f / f03e230 / 583e5c5）

- **is_online 两级探活自愈（f6eb43f）**：adb daemon 被后台回收后 `adb devices` 为空，
  重试无用；实测 `adb kill-server && adb start-server` 后 emulator-5554 被 QEMU 底层入口
  自动重新发现。`is_online()` 改两级：常规探活 3 次 → 失败则重启 adb server 再探 2 次。
  成功路径零开销，**设备掉线无需再手动恢复**。
- **小麦资料卡卡死根治（f03e230）**：BACK 过度返回顶出**个人资料卡浮层**卡死后续全部
  机器人。修复：overlay 识别补 资料卡 特征 + 导航前清浮层 + 退出 OCR 复核 + 盲按熔断。
- **插屏广告残留死锁修复（583e5c5）**：插屏广告（无「关闭广告」文字）盖屏导致 find_row
  失败空滚死锁。修复：find_row 失败先 OCR 认广告清场再重试 + 关闭链路 OCR 复读防过渡帧误判。

### 2026-09-14：看广告效率四连优化 D/ABC/E/方案1（c0a7d83 / fb94cbb / ba758d4 / a3374e1 / 64cfa66）

- **D 优化（c0a7d83）**：CD 窗口内预取行节点（省一次 dump）+ dismiss 前置。
- **ABC 提速（fb94cbb）**：A 收尾等待 2s→1s；B 移除 P1 预检；C `ad_wait_min/max`
  16-18 → **15-17**（config 现值）。
- **E 优化（ba758d4）**：广告等待进行到 `ad_prefetch_at`(10s) 时顶条 OCR **预定位**关闭
  按钮缓存坐标（TTL 20s），`_close_ad` 步骤 0.5 直接消费（实测 82/82 命中）；
  **方案 1**：`_back_at_taskcenter` 确认改「OCR + 顶条 + 全屏」三信号免 dump 快速通道
  （`tc_confirm_skip_dump: true`）。重载动画期 2 条 `dump 超时 (>2.5s)` WARNING 属预期快失败。
- **E1/E2/E3（a3374e1）**：E1 顶条快检直关（命中即关，跳过全屏守卫 ~3.4s）；
  E2 `_top_strip_scan` → `_fullscreen_scan`（单次全屏 OCR 三合一：TC 特征/关闭按钮/覆盖层）；
  E3 `_log_page_snapshot()` 放弃点页面快照留痕（小麦事故归因利器）。
- **E4 计时打点（64cfa66）**：`_stage(t0, tag)` helper，进台 5 段 / 退台 4 段 `[计时]` 前缀
  INFO 日志（退台.0前提校验/第1-3层/合计；进台.1导航列表/2找行+现场/3点行到发消息/
  4发消息到个人/5TC加载探测/合计）。
- 效果（09-15 实测）：点开→关闭 36.1→27.7s；关闭路径分布 顶条快检 66% / uiautomator 24% /
  预定位 8% / OCR 2% / 免关 ~9%。

### 2026-09-15：P0 退台减 dump + 全量场验证 + 轮换结论（d813da5）

- **P0 单快照**（详见坑 12 修复 2）：退台 30.6→17.2s 中位（-44%）、进台 35.4→26.0s、
  找行 11.4→6.8s。单测 236/236 绿。
- **全量场（10:53-13:28，2h35m）**：10 台 × 10 支配额 100% 达成、失败 0、12 次换台
  0 卡死、18 次 dump 超时全部自愈。同机 CD 连看 90 组周期中位 **89.2s**（CD 60s 占 67%
  是硬底，顺序连看天花板 ~89s/支已贴住）；全含 93.2s/支。
- **轮换结论（实测）**：跨机关闭→关闭 83-91s vs 同机连看 87.8s → **打平略优**；
  P0 前测算轮换 104.6s/支（+16%）的劣势已消除。轮换无 CD 硬下限、还能继续压，
  但每支广告多一次换台、暴露于 TC 卡死家族 —— 建议治理该家族后再全量轮换。


### 单测基线（2026-09-15：236/236 全绿）
- `py -3.13 -m unittest discover -p "test_*.py"`
- test_flow_ocr_close.py **92**（F8/E1 快检/E2 全屏三合一/E3 快照/E 预定位 + F11 盲点禁令
  + 覆盖层闸门 + 跨词元拼接 + BACK 封顶 + 条带高度断言）
  + test_adb_cache.py **21**（+4 超时看门狗）
  + test_run_all_flags.py **29**（单会话连看 + 首轮广告 + 配额收尾/算术退出 + 白名单）
  + test_nav_optimize.py **48**（A1 事件驱动 + P0 单快照 + 滚动收集）
  + test_task_safety.py **36**（签到/反馈断言 + 行内 X/Y 计数 + scroll-to-top 省 dump）
  + test_3bot_rotate.py **10**（多机器人轮转场景）
- **环境注意**：单测要 import `main.py` → **必须装 PyYAML**；否则 main 参数映射/白名单类
  用例会以 `ModuleNotFoundError: No module named 'yaml'` 报错（12 个 error），不是业务失败。

### 诊断工具（scripts_test/，非生产）
- `trace_ad.py <机器人> [次数]` —— 包一层 `Flow._ocr_shot`，把每次 OCR 看到的画面存到
  `screenshots/trace/ocr_NNN.png`。**排查"判定失灵"类问题的首选**。
- `replay_frames.py [目录...]` —— 离线把上述帧喂给当前真实判定函数，验证判定是否翻转
  （不连真机）；支持命令行指定多个现场目录。
- `verify_f11.py` —— 用**真机现场帧**离线验证 F11 两项：覆盖层闸门（真问卷帧必命中、
  任务中心/广告/H5 帧必不误报）+ banner 盲点禁令（任务中心页拒绝、列表页放行）。
- `probe_f11_live.py [机器人]` —— **上机**探针：进任务中心后打印 `_page_tc`、dump 顶部
  节点（实证 banner 不在 dump）、实测 `_tap` 拒绝行为。
- `probe_overlay_idle.py [机器人] [秒]` —— **上机**对照实验：任务中心内**零点击**观察
  Badcase 问卷是否自发弹出（区分"我们误触" vs "QQ 自身行为"）。
- `probe_banner.py` —— 进指定机器人任务中心，查 (160,152) 落点节点 + 顶部可点击节点。
- `probe_dk_quota.py` —— **上机**取证：进台行 X/Y 配额计数（dump 节点 + 问卷检测 + ratio 读取
  + BACK 落点）。定位"满额不直退"用。
- `probe_list.py` —— **上机**：打印机器人列表页判定细节（`_looks_like_robot_list` 各项）+ 全节点
  selected/坐标。排查坑 12（残留 dump 误导导航）用。
- `probe_overlay_after_ad.py [机器人]` —— **上机**：看广告后连续采样，观察 Badcase 问卷出现时刻
  （验证"广告关闭后 ~35-40s 延迟弹出"）。
- `measure_dump.py [--rounds N] [--compressed]` —— dump 分段耗时测量（结论：稳定页 ~2.33s，
  `--compressed` 更慢，故提速靠**减 dump 次数**而非压缩）。

---

## 五、常用命令速查
- 截图+OCR+dump：`py -3.13 scripts_test\snap.py <tag>`（输出 logs\snap_<tag>.txt + screenshots\<tag>.png）
- 解析 Node：`py -3.13 scripts_test\shownodes.py logs\snap_<tag>.txt`
- 自动抓机器人列表：`py -3.13 main.py --list`
- 手动指定机器人跑全流程（例 2 台、每台看 1 次广告）：`py -3.13 main.py --robots "A,B" --ad-times 1`
- 只看广告：`py -3.13 main.py --ad-only --robots "A" --ad-times 1`
- 手动进某机看一次广告并退出：`py -3.13 e2e_ad_once.py <昵称>`
- 手动跑某一台签到+反馈完整闭环：`py -3.13 e2e_robot_once.py <昵称>`
- 全白名单手动跑广告：`ad_test_all.bat [COUNT]`（默认每台 1 次；顶格补配额用 `watch_ad.bat`）
- 拉回 QQ：`adb -s emulator-5554 shell am start -n com.tencent.mobileqq/com.tencent.mobileqq.activity.SplashActivity`

## 六、踩坑时的恢复流程
1. 先 `py -3.13 scripts_test\snap.py dbg` + shownodes 看当前在哪个页面。
2. 若 Badcase 问卷（OCR 见 Badcase/反馈问卷/开始填写/感谢大家一直）：直接 `input keyevent 4` 连退 2-3 次。
3. 若推广广告页（无"关闭广告"文字的 H5，如悦己官/种植牙）：BACK 连退，退到任务中心特征再走退出。
4. 若广告页（有关闭按钮）：用 OCR 正向定位（`_ad_close_pos`）点它自己的坐标；
   **不要**再用固定 (160,152)——该坐标/该区在版式一变就可能命中 banner（见坑 13/14）。
   任务中心页已被 F11 禁止任何坐标盲点（`_tap` 需 `trusted=True` 才放行）。
5. 若 FAQ/资料/聊天/profile：多点几次 `input keyevent 4` 退回列表。
6. 若桌面：用 am start 拉回 QQ 再导航。
7. 确认回到机器人列表后再跑下一步流程。
