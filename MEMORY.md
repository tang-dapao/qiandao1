# 项目记忆 / 进度存档（用于下次接着做）

> 更新日期：2026-09-08
> 项目路径：D:\qiandao
> 运行方式：Windows 上 `py -3.13 xxx.py`，模拟器 MuMu（ADB 127.0.0.1:16384）
> 目标：QQ 机器人任务中心 每日签到 / 问题反馈 / 看广告（每天每台 10 次，60s CD 多机轮转）
> **分辨率已固定为 1080×1920 竖屏（SurfaceOrientation=0）**，所有坐标基于此。

---

## 一、当前进度概览（已完成 / 验证通过的）

### 坐标体系（1080×1920 竖屏，实测确认）
- 截图 / OCR / input tap / uiautomator 共用同一 1080×1920 空间。
- uiautomator 能读出任务中心 WebView 中文文本与真实坐标（滚动后仍可靠）。
- 任务中心行按钮固定 `X≈879`（各行右侧对齐），按钮中心在标签中心下方约 24px。

### 三层返回路径（左上角返回箭头，已实测确认 —— 用户要求的退出方式）
- 第 1 层：任务中心顶部返回箭头 `《` @(90,138) → **聊天页**
- 第 2 层：聊天页左上角返回箭头（机器人名字**左侧**，别点名字）@(59,139) → **机器人首页(profile)**
- 第 3 层：profile 左上角返回箭头（"账户及设置"节点区）@(73,133) → **机器人列表**
- **重要**：必须先下滑到任务中心最上方，左上角返回箭头才会出现。
  - "已到顶"锚点文字：「开通解锁以下专属权益」/「收支详情」
  - 用 swipe_down 反复下滑直到锚点出现。

### 已单独验证通过的功能（在各机器人上）
| 功能 | 验证机器人 | 证据 |
|------|-----------|------|
| 签到 0→1 | 古禹 | 按钮变「×签到1天」，计数 1/1 |
| 问题反馈 0→1 | 古禹 | 按钮变「去反馈」，计数 1/1 |
| 看广告 0→1 | 裴旖 | 1/10 + 电量 +12，CD 倒计时出现 |
| 三层返回箭头退出 | 古禹/裴旖 | 都能正确回机器人列表 |

### 签到/看广告相关浮层坐标（1080×1920，实测）
- 签到浮层「每日免费领」：签到按钮 @(301,1800)、右上角✕ @(996,1143)、看广告+ @(781,1800)
- 签到成功浮层「恭喜获得」：我知道了 @(540,1189)、✕ @(540,1498)
- 反馈问卷页返回：优先文本「返回」，兜底 @(81,139)
- 广告页「关闭广告」按钮：左上角，OCR 实测定位到 @(99,152) / @(120,152)，兜底固定坐标 AD_CLOSE=(160,152)

---

## 二、代码文件结构与当前状态

### flow.py（核心，正在改，已验证）
- `_nav_robot_list()`：联系人 tab(y>=1850) → 机器人分类(y 900~1250) → 展开「我添加的机器人」
- `_collect_robot_names(max_scroll=12)`：**自动抓取机器人列表**，已稳定抓到完整 12 个
  - 识别规则：昵称节点 `x1∈[180,195] && y1∈[350,1850] && 宽≤230`
  - 先无条件 swipe_down ×7 滚回列表顶部，再向上滚动收集，去重、跳过「内测中」
  - 带编号重复昵称（小麦/小麦1/小麦2）视为不同条目保留
  - 实测稳定：12 个 = 代柯、尔尔、古禹、李宥恩、黎小姐、裴旖、藤非、小麦、小麦1、小麦2、席恩、游迦
- `_enter_taskcenter(name)`：进列表 → 点机器人 → profile「发消息」(wait_for×6) → 聊天页「个人」(y>=600) → 任务中心
- `_exit_taskcenter()`：**已改为左上角返回箭头 3 层退出**（见上），内含 `_scroll_to_top_of_taskcenter()`
- `_signin()` / `_feedback()`：已验证
- `_watch_ad_once()`：**广告关闭有坑，见第三节"坑"**
  - 已改为 OCR 定位「关闭广告/关闭/跳过」→ 点击 → 确认按钮消失
  - 相关辅助：`_ocr_shot()` / `_ocr_find(texts, region)` / `_close_ad(max_tries)`
- `run_robot()` / `run_all()`：签到反馈阶段 + 看广告轮转（60s CD），已加失败重试上限（fail≥3 跳过该机）

### main.py（已重写为 adb+flow 版 CLI）
- `py -3.13 main.py`：全自动（自动抓列表 → 签到+反馈 → 看广告轮转）
- `--list`：只抓取并列出机器人
- `--robots "A,B"`：手动指定覆盖自动抓取
- `--no-signin` / `--no-feedback` / `--ad-only`：功能开关
- `--ad-times N`：每台看广告次数

### 其它文件
- `adb_ui.py`：adb 驱动层（swipe 已适配 1080×1920；nodes 跳过零尺寸节点；dump 看 "dumped to" 输出而非 rc）
- `config.yaml`：udid、app 包名、timing、workflow（ad_times_per_robot=10、ad_close_retries、skip_beta、skip_duplicate_name 等）
- `test_e2e.py` / `test_ad.py`：临时测试脚本
- `scripts_test/snap.py`：截图+OCR+uiautomator 三合一 dump 工具（`py -3.13 scripts_test\snap.py <tag>` → logs\snap_<tag>.txt）
- `scripts_test/shownodes.py`：解析 dump 里的 Node（Windows 控制台避免中文乱码时用）
- `legacy/`：**旧 Appium/OCR 框架已整体移入**（driver/locators/ocr_utils/robot/scheduler/signin/feedback/ad_watcher
  + robot_state.json/collected.json + appium_caps/），无任何活跃代码引用，仅作历史参考，随时可删。

---

## 三、踩过的坑（重要！下次别重蹈覆辙）

### 坑 1：广告关闭 —— 2026-09-08 已基于实机证据重写并双机验证通过 ✅
**新方案（flow.py `_close_ad`）**：uiautomator 优先 → OCR 顶部兜底 → 固定坐标兜底。
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

---

## 四、还没做的 / 待办清单（按优先级）

### P0（当前卡点，必须解决）
1. ~~彻底解决广告关闭~~ → **2026-09-08 已完成**：`_close_ad` 三层方案重写并双机 E2E 验证通过（见坑 1）。
   同时完成：`_nav_robot_list` 加残留任务中心检测与自动退出（坑 3 部分落地）。

### P1（全自动完善）
2. **端到端实测 `main.py` 完整流程**（多机 + 60s CD 看广告轮转），确认广告关闭不卡顿、退出正常、计数正确。
3. **验证 `run_all` 的 60s CD 轮转**在多机器人下正常消磨、单机失败跳过逻辑生效。
4. 设备不在 QQ 主界面时的完整启动兜底（已做任务中心残留检测；还需覆盖 桌面/其它 app 场景，如 am start 拉起 QQ）。

### P2（健壮性/打磨）
5. 广告关闭的 OCR 区域裁剪（只扫顶部 + 必要时全屏），减少 OCR 耗时。
6. 清理临时测试脚本（test_e2e.py / test_ad.py），或保留为可复用验证工具。
7. 是否删掉已废弃的旧框架文件（driver.py/locators.py/ocr_utils.py/robot.py/scheduler.py/signin.py/feedback.py/ad_watcher.py）——注意 robot.py 的 RobotManager 还有列表读取逻辑可参考，但新 main.py 已不用。

---

## 五、常用命令速查
- 截图+OCR+dump：`py -3.13 scripts_test\snap.py <tag>`（输出 logs\snap_<tag>.txt + screenshots\<tag>.png）
- 解析 Node：`py -3.13 scripts_test\shownodes.py logs\snap_<tag>.txt`
- 自动抓机器人列表：`py -3.13 main.py --list`
- 手动指定机器人跑全流程（例 2 台、每台看 1 次广告）：`py -3.13 main.py --robots "A,B" --ad-times 1`
- 只看广告：`py -3.13 main.py --ad-only --robots "A" --ad-times 1`
- 手动进某机看一次广告并退出：`py -3.13 test_ad.py <昵称>`
- 手动跑某一台签到+反馈完整闭环：`py -3.13 test_e2e.py <昵称>`
- 拉回 QQ：`adb -s 127.0.0.1:16384 shell am start -n com.tencent.mobileqq/com.tencent.mobileqq.activity.SplashActivity`

## 六、踩坑时的恢复流程
1. 先 `py -3.13 scripts_test\snap.py dbg` + shownodes 看当前在哪个页面。
2. 若广告页：点左上 (160,152)（或 OCR 找关闭）。
3. 若 FAQ/资料/聊天/profile：多点几次 `input keyevent 4` 退回列表。
4. 若桌面：用 am start 拉回 QQ 再导航。
5. 确认回到机器人列表后再跑下一步流程。
