"""异常告警推送（多通道 · 默认企业微信）。

设计原则（顺序即优先级）：
1. **绝不阻断主流程**：所有网络发送都在后台线程里做，任何异常就地吞掉，
   调用方 `alert()` 永远立即返回、绝不抛异常。
2. **落盘优先**：每条异常先写 `logs/anomaly.jsonl`（结构化），再尝试推送；
   推送失败也不丢证据。
3. **去重限频**：同一 dedup_key 在 `dedup_window` 秒内只推一次（跨进程，
   状态存 `logs/.notify_state.json`）；整点小时内最多 `max_per_hour` 条，
   溢出的只计数，下一条正常消息里附带「已抑制 N 条」。
4. **通道可切换/可降级**：primary 失败自动尝试 fallback 链上的下一个通道。

支持的通道（凭据放 `notify.local.yaml`，已 gitignore）：
    wecom_app:   corpid / agentid / secret   企业微信自建应用（需企业可信IP）
    wecom_bot:   webhook                    企业微信群机器人（不受 IP 白名单限制）
    pushplus:    token                      pushplus 微信推送（最省事，无需企业资质）
    serverchan:  sendkey                    Server酱（免费 5 条/天）

命令行自检：
    py -3.13 notify.py --check     # 校验配置/换取 token，不发消息
    py -3.13 notify.py --test      # 真发一条测试消息
    py -3.13 notify.py --ip        # 打印本机公网 IP（配可信IP 时用）
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import time

import requests

log = logging.getLogger("notify")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FILE = "notify.local.yaml"
STATE_FILE = os.path.join(BASE_DIR, "logs", ".notify_state.json")
TOKEN_FILE = os.path.join(BASE_DIR, "logs", ".wecom_token.json")
API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# access_token 有效期 7200s；提前 200s 刷新，避免边界失效
_TOKEN_TTL_SAFE = 200

# 需要强制刷新 token 的错误码
_ERR_TOKEN = (40014, 42001, 42007, 42009)
# 频率超限
_ERR_RATE = (45009, 45033)
# 公网 IP 不在「企业可信IP」白名单（家宽换 IP 后必然出现）
_ERR_IP = 60020

# 可重试的异常类型（2026-10-30）：只重试**网络层**抖动，
# 配置类错误（webhook 失效 / errcode / IP 未授信）重试多少次都不会变好，
# 只会拖长收尾时间，故一律不重试。
_TRANSIENT_EXC = (
    requests.exceptions.Timeout,      # ReadTimeout / ConnectTimeout
    requests.exceptions.ConnectionError,   # DNS / 连接被拒 / 断流
    TimeoutError,
    ConnectionResetError,
)


class IpNotTrusted(RuntimeError):
    """60020：本机公网 IP 未加入应用「企业可信IP」，需管理员在后台补加。"""


# ---------------------------------------------------------------- 配置读取
def load_notify_config(cfg: dict | None = None, base_dir: str = BASE_DIR) -> dict:
    """合并 config.yaml 的 notify.* 与 notify.local.yaml 的通道凭据。

    只做读取与扁平化，不做可用性判断（交给 build_channels）。
    """
    cfg = cfg or {}
    raw = cfg.get("notify") if isinstance(cfg.get("notify"), dict) else {}
    out = {
        "enabled": bool(raw.get("enabled", False)),
        "channel": str(raw.get("channel") or "wecom_app"),
        "fallback": list(raw.get("fallback") or []),
        "touser": str(raw.get("touser") or "@all"),
        "timeout": float(raw.get("timeout", 5.0)),
        "dedup_window": float(raw.get("dedup_window", 1800)),
        "max_per_hour": int(raw.get("max_per_hour", 40)),
        "record_file": raw.get("record_file", "logs/anomaly.jsonl"),
        "shot_dir": raw.get("shot_dir", "screenshots/anomaly"),
        # 2026-10-30 新增：推送失败不再「一次超时就丢」。
        # send_retries = 额外重试次数（0 = 只试一次，回到旧行为）
        # retry_backoff = 两次尝试之间的等待（秒）
        # retry_timeout = 重试时把单次 HTTP 超时抬到多少（企微偶发 5s 读超时）
        # pending_file  = 全部通道都失败时转存的待补发队列（flush 时再补一次）
        "send_retries": int(raw.get("send_retries", 2)),
        "retry_backoff": float(raw.get("retry_backoff", 2.0)),
        "retry_timeout": float(raw.get("retry_timeout", 10.0)),
        "pending_file": raw.get("pending_file", "logs/notify_pending.jsonl"),
        "resend_max": int(raw.get("resend_max", 5)),
        "wecom_app": {},
        "wecom_bot": {},
        "pushplus": {},
        "serverchan": {},
    }
    path = os.path.join(base_dir, LOCAL_FILE)
    if os.path.isfile(path):
        try:
            import yaml  # 延迟导入：缺 yaml 时也不影响主流程
            with open(path, "r", encoding="utf-8") as f:
                local = yaml.safe_load(f) or {}
            for k in ("wecom_app", "wecom_bot", "pushplus", "serverchan"):
                blk = local.get(k)
                if isinstance(blk, dict):
                    out[k] = {kk: ("" if vv is None else str(vv)) for kk, vv in blk.items()}
        except Exception as e:
            log.warning("读取 %s 失败：%s", LOCAL_FILE, e)
    return out


def build_channels(conf: dict) -> list:
    """按「primary → fallback」顺序构造可用通道列表（凭据不全的静默跳过）。"""
    order = [conf.get("channel") or "wecom_app"] + list(conf.get("fallback") or [])
    chans = []
    for name in order:
        try:
            if name == "wecom_app":
                c = conf.get("wecom_app") or {}
                if c.get("corpid") and c.get("agentid") and c.get("secret"):
                    chans.append(WeComApp(c["corpid"], c["agentid"], c["secret"],
                                          touser=conf.get("touser", "@all"),
                                          timeout=conf.get("timeout", 5.0)))
            elif name == "wecom_bot":
                c = conf.get("wecom_bot") or {}
                if c.get("webhook"):
                    chans.append(WeComBot(c["webhook"], timeout=conf.get("timeout", 5.0)))
            elif name == "pushplus":
                c = conf.get("pushplus") or {}
                if c.get("token"):
                    chans.append(PushPlus(c["token"], timeout=conf.get("timeout", 5.0)))
            elif name == "serverchan":
                c = conf.get("serverchan") or {}
                if c.get("sendkey"):
                    chans.append(ServerChan(c["sendkey"], timeout=conf.get("timeout", 5.0)))
        except Exception as e:
            log.warning("通道 %s 构造失败（跳过）：%s", name, e)
    return chans


# ---------------------------------------------------------------- 通道实现
class WeComApp:
    """企业微信自建应用：gettoken + message/send + media/upload。"""

    name = "wecom_app"
    supports_image = True

    def __init__(self, corpid: str, agentid: str, secret: str,
                 touser: str = "@all", timeout: float = 5.0):
        self.corpid = corpid
        self.agentid = int(agentid) if str(agentid).isdigit() else agentid
        self.secret = secret
        self.touser = touser
        self.timeout = timeout
        self._token = ""
        self._token_exp = 0.0
        self._lock = threading.Lock()

    # -- token ------------------------------------------------------
    def _load_token_cache(self) -> tuple[str, float]:
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("corpid") == self.corpid:
                return d.get("token", ""), float(d.get("exp", 0))
        except Exception:
            pass
        return "", 0.0

    def _save_token_cache(self, token: str, exp: float) -> None:
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"corpid": self.corpid, "token": token, "exp": exp}, f)
        except Exception:
            pass

    def token(self, force: bool = False) -> str:
        with self._lock:
            now = time.time()
            if not force and self._token and now < self._token_exp:
                return self._token
            if not force and not self._token:
                tk, exp = self._load_token_cache()
                if tk and now < exp:
                    self._token, self._token_exp = tk, exp
                    return tk
            r = requests.get(
                API_BASE + "/gettoken",
                params={"corpid": self.corpid, "corpsecret": self.secret},
                timeout=self.timeout,
            )
            d = r.json()
            if d.get("errcode") != 0:
                raise RuntimeError("gettoken 失败 errcode=%s errmsg=%s"
                                   % (d.get("errcode"), d.get("errmsg")))
            self._token = d["access_token"]
            self._token_exp = now + max(60, int(d.get("expires_in", 7200)) - _TOKEN_TTL_SAFE)
            self._save_token_cache(self._token, self._token_exp)
            return self._token

    # -- 发送 ------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        """带一次 token 失效重试的 POST。"""
        for attempt in (0, 1):
            tk = self.token(force=bool(attempt))
            r = requests.post(API_BASE + path, params={"access_token": tk},
                              json=payload, timeout=self.timeout)
            d = r.json()
            code = d.get("errcode")
            if code == 0:
                return d
            if code in _ERR_TOKEN and attempt == 0:
                continue
            if code == _ERR_IP:
                raise IpNotTrusted(
                    "公网 IP 未列入「企业可信IP」（errcode=60020）。请在管理后台 → "
                    "应用管理 → 本应用 → 企业可信IP 添加当前公网 IP。原始返回：%s"
                    % d.get("errmsg"))
            raise RuntimeError("发送失败 errcode=%s errmsg=%s" % (code, d.get("errmsg")))
        raise RuntimeError("发送失败：token 重试后仍失败")

    def send(self, title: str, content: str) -> dict:
        return self._post("/message/send", {
            "touser": self.touser,
            "msgtype": "markdown",
            "agentid": self.agentid,
            "markdown": {"content": content[:4096]},
        })

    def upload_image(self, path: str) -> str:
        tk = self.token()
        with open(path, "rb") as f:
            r = requests.post(API_BASE + "/media/upload",
                              params={"access_token": tk, "type": "image"},
                              files={"media": (os.path.basename(path), f)},
                              timeout=max(self.timeout, 10.0))
        d = r.json()
        if d.get("errcode") != 0:
            raise RuntimeError("upload 失败 errcode=%s" % d.get("errcode"))
        return d["media_id"]

    def send_image(self, path: str) -> dict:
        media_id = self.upload_image(path)
        return self._post("/message/send", {
            "touser": self.touser,
            "msgtype": "image",
            "agentid": self.agentid,
            "image": {"media_id": media_id},
        })


class WeComBot:
    """企业微信群机器人 webhook（不受企业可信IP 限制，必须配普通群）。"""

    name = "wecom_bot"
    supports_image = False

    def __init__(self, webhook: str, timeout: float = 5.0):
        self.webhook = webhook
        self.timeout = timeout

    def send(self, title: str, content: str) -> dict:
        r = requests.post(self.webhook, json={
            "msgtype": "markdown",
            "markdown": {"content": ("### %s\n%s" % (title, content))[:4096]},
        }, timeout=self.timeout)
        d = r.json()
        if d.get("errcode") != 0:
            code = d.get("errcode")
            hint = {
                93000: "webhook key 无效（机器人已删除 / key 复制不全 / 不属于该企业）",
                45009: "该机器人触发频率限制（20 条/分钟）",
                40008: "消息格式错误（markdown 超长？）",
            }.get(code, "")
            raise RuntimeError("群机器人失败 errcode=%s %s errmsg=%s"
                               % (code, hint, d.get("errmsg")))
        return d


class PushPlus:
    """pushplus 微信推送（个人微信接收，无需企业资质）。"""

    name = "pushplus"
    supports_image = False

    def __init__(self, token: str, timeout: float = 5.0):
        self.token = token
        self.timeout = timeout

    def send(self, title: str, content: str) -> dict:
        r = requests.post("https://www.pushplus.plus/send", json={
            "token": self.token,
            "title": title[:100],
            "content": content[:4000],
            "template": "markdown",
        }, timeout=max(self.timeout, 10.0))
        d = r.json()
        if d.get("code") != 200:
            raise RuntimeError("pushplus 失败 code=%s msg=%s"
                               % (d.get("code"), d.get("msg")))
        return d


class ServerChan:
    """Server酱（免费版每天 5 条，仅作降级备份）。"""

    name = "serverchan"
    supports_image = False

    def __init__(self, sendkey: str, timeout: float = 5.0):
        self.sendkey = sendkey
        self.timeout = timeout

    def send(self, title: str, content: str) -> dict:
        url = "https://sctapi.ftqq.com/%s.send" % self.sendkey
        r = requests.post(url, data={"title": title[:64], "desp": content[:8000]},
                          timeout=max(self.timeout, 10.0))
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError("Server酱失败 code=%s msg=%s"
                               % (d.get("code"), d.get("message") or d.get("info")))
        return d


# ---------------------------------------------------------------- 告警器
class Notifier:
    """线程安全的告警入口。`alert()` 入队即返回，发送在守护线程里做。"""

    def __init__(self, conf: dict):
        self.conf = conf
        self.channels = build_channels(conf) if conf.get("enabled") else []
        self.enabled = bool(self.channels)
        self._q: queue.Queue = queue.Queue()
        self._worker = None
        self._suppressed = 0
        self._rate_hits: list = []
        self._dedup = {}          # key -> last_ts
        self._dedup_lock = threading.Lock()
        self._load_state()
        if self.enabled:
            self._worker = threading.Thread(target=self._run, name="notify-worker", daemon=True)
            self._worker.start()

    # -- 去重状态 --------------------------------------------------
    def _load_state(self) -> None:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d.get("dedup"), dict):
                now = time.time()
                win = self.conf.get("dedup_window", 1800)
                self._dedup = {k: v for k, v in d["dedup"].items()
                               if isinstance(v, (int, float)) and now - v < win}
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"dedup": self._dedup}, f)
        except Exception:
            pass

    def _is_dup(self, key: str) -> bool:
        if not key:
            return False
        now = time.time()
        win = self.conf.get("dedup_window", 1800)
        with self._dedup_lock:
            last = self._dedup.get(key)
            if last and now - last < win:
                return True
            self._dedup[key] = now
            self._save_state()
        return False

    # -- 记录 ------------------------------------------------------
    def record(self, event: dict) -> None:
        """结构化落盘（无论推送是否成功）。"""
        try:
            path = self.conf.get("record_file", "logs/anomaly.jsonl")
            if not os.path.isabs(path):
                path = os.path.join(BASE_DIR, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("异常记录落盘失败：%s", e)

    def save_shot(self, img_bytes: bytes, tag: str) -> str:
        """保存现场截图，返回绝对路径（失败返回空串）。"""
        try:
            d = self.conf.get("shot_dir", "screenshots/anomaly")
            if not os.path.isabs(d):
                d = os.path.join(BASE_DIR, d)
            os.makedirs(d, exist_ok=True)
            name = "%s_%s.png" % (time.strftime("%Y%m%d_%H%M%S"), tag)
            p = os.path.join(d, name)
            with open(p, "wb") as f:
                f.write(img_bytes)
            return p
        except Exception as e:
            log.warning("现场截图保存失败：%s", e)
            return ""

    # -- 对外入口 --------------------------------------------------
    def alert(self, title: str, detail: str = "", *, kind: str = "anomaly",
              level: str = "warn", dedup_key: str = "", extra: dict | None = None,
              shot_path: str = "", dedup: bool = True) -> None:
        """记录并推送一条告警。绝不抛异常、绝不阻塞。

        dedup=False：跳过同类去重（用于「每账号跑完」这类**必须每次都发**的
        统计消息；频率仍受 max_per_hour 约束）。
        """
        try:
            ev = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": round(time.time(), 3),
                "kind": kind,
                "level": level,
                "title": title,
                "detail": detail,
                "shot": shot_path,
            }
            if extra:
                ev.update({k: v for k, v in extra.items() if k not in ev})
            self.record(ev)
            log.warning("[告警][%s] %s | %s", kind, title, str(detail).replace("\n", " / "))
            if not self.enabled:
                return
            if dedup:
                key = dedup_key or ("%s|%s" % (kind, title))
                if self._is_dup(key):
                    self._suppressed += 1
                    log.info("告警去重命中（窗口 %ss），已抑制：%s",
                             int(self.conf.get("dedup_window", 1800)), key)
                    return
            self._q.put(ev)
        except Exception as e:  # 保命：告警系统自身不允许影响主流程
            log.warning("alert 内部异常（已忽略）：%s", e)

    def _rate_ok(self) -> bool:
        now = time.time()
        self._rate_hits = [t for t in self._rate_hits if now - t < 3600]
        if len(self._rate_hits) >= self.conf.get("max_per_hour", 40):
            self._suppressed += 1
            return False
        self._rate_hits.append(now)
        return True

    def _run(self) -> None:
        while True:
            ev = self._q.get()
            if ev is None:
                self._q.task_done()
                return
            try:
                if not self._rate_ok():
                    continue
                self._deliver(ev)
            except Exception as e:
                log.warning("告警推送失败（不影响主流程）：%s", e)
            finally:
                self._q.task_done()

    def _deliver(self, ev: dict, *, allow_pending: bool = True,
                 retries: int | None = None) -> bool:
        """推送一条告警；返回是否成功（至少一个通道发出去了）。

        2026-10-30 加固：**不再「一次超时就整条丢」**（当天实测丢了 2 条：
        13:18「连续 3 支广告没打开」、14:08「✅ 账号 觅夏 第 2/4」，
        都是 `qyapi.weixin.qq.com` 读超时，而 `fallback: []` 没有备用通道）。
          · 网络层异常（Timeout / ConnectionError）按 `notify.send_retries`
            重试，重试期间把该通道单次超时抬到 `retry_timeout`（默认 10s）。
          · 配置类异常（errcode / IP 未授信 / webhook 失效）**不重试** ——
            重试不会变好，只会拖长进程退出时间。
          · 全部通道都失败 → 转存 `pending_file`，由 `flush()` 或
            `notify.py --resend` 补发。**成功路径零开销**（不写文件、不多发请求）。
        """
        content = self._format(ev)
        if self._suppressed:
            content += "\n\n> 此前已抑制 %d 条重复/超频告警" % self._suppressed
            self._suppressed = 0
        title = ev.get("title", "qiandao 异常")
        tries = max(0, int(self.conf.get("send_retries", 2)
                           if retries is None else retries))
        backoff = float(self.conf.get("retry_backoff", 2.0) or 0)
        retry_to = float(self.conf.get("retry_timeout", 10.0) or 10.0)
        last_err = None
        for ch in self.channels:            # primary → fallback
            old_timeout = getattr(ch, "timeout", None)
            for attempt in range(1, tries + 2):
                try:
                    if attempt > 1 and old_timeout is not None:
                        ch.timeout = max(float(old_timeout), retry_to)
                    ch.send(title, content)
                    log.info("告警已推送（通道 %s，第 %d 次尝试）：%s",
                             ch.name, attempt, title)
                    if ev.get("shot") and getattr(ch, "supports_image", False) \
                            and os.path.isfile(ev["shot"]):
                        try:
                            ch.send_image(ev["shot"])
                        except Exception as e:
                            log.warning("告警截图推送失败（不影响文本）：%s", e)
                    return True
                except Exception as e:
                    last_err = e
                    if isinstance(e, IpNotTrusted):
                        self._note_ip_hint(str(e))
                    if not isinstance(e, _TRANSIENT_EXC) or attempt > tries:
                        log.warning("通道 %s 推送失败（第 %d/%d 次，不再重试）：%s",
                                    ch.name, attempt, tries + 1, e)
                        break
                    log.warning("通道 %s 推送失败（第 %d/%d 次，网络抖动，可重试）：%s",
                                ch.name, attempt, tries + 1, e)
                    if backoff > 0:
                        time.sleep(backoff)
                finally:
                    if attempt > 1 and old_timeout is not None:
                        ch.timeout = old_timeout
        if last_err is not None:
            if allow_pending:
                self._pending_add(ev, last_err)
            else:
                log.warning("补发仍失败（保留在待补发队列，下次再试）：%s", last_err)
        return False

    # -- 待补发队列（2026-10-30）----------------------------------------
    def _pending_path(self) -> str:
        p = self.conf.get("pending_file", "logs/notify_pending.jsonl")
        return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)

    def _pending_add(self, ev: dict, err) -> None:
        """把发送失败的告警追加到待补发队列（一行一条 JSON）。"""
        path = self._pending_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ev": ev, "err": str(err)[:300],
                                    "failed_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                   ensure_ascii=False) + "\n")
            log.warning("全部通道推送失败 → 已转入待补发队列 %s（异常同时落盘 anomaly.jsonl）：%s",
                        os.path.basename(path), err)
        except Exception as e:
            log.warning("待补发队列写入失败（告警内容仅存于 anomaly.jsonl）：%s", e)

    def resend_pending(self, max_items: int | None = None,
                       budget: float | None = None) -> tuple:
        """补发待发队列里的告警，返回 (成功数, 仍失败数)。

        约束（收尾阶段不能被补发卡住）：
          · 每条**只试一遍**（`retries=0`），不走重试链路；
          · 最多 `resend_max` 条；
          · `budget` 秒总时间预算，超了就把剩下的留到下次。
        补发失败的行原样保留，下次再试。
        """
        path = self._pending_path()
        if not os.path.isfile(path):
            return (0, 0)
        cap = max_items if max_items is not None else int(self.conf.get("resend_max", 5))
        t0 = time.time()
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
        except Exception as e:
            log.warning("读取待补发队列失败：%s", e)
            return (0, 0)
        if not lines:
            return (0, 0)
        log.warning("待补发队列有 %d 条（本次最多补发 %d 条）：%s", len(lines), cap,
                    self._pending_path())
        ok = fail = 0
        kept = []
        for idx, ln in enumerate(lines):
            if idx >= cap or (budget is not None and time.time() - t0 > budget):
                kept.append(ln)          # 超出配额/预算，留到下次
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                log.warning("待补发队列有一行无法解析，已丢弃：%s", ln[:120])
                continue
            ev = obj.get("ev") if isinstance(obj, dict) else None
            if not isinstance(ev, dict):
                continue
            if self._deliver(ev, allow_pending=False, retries=0):
                ok += 1
                log.info("补发成功：%s", ev.get("title"))
            else:
                fail += 1
                kept.append(ln)
        try:
            if kept:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(kept) + "\n")
            else:
                os.remove(path)
                log.info("待补发队列已清空")
        except Exception as e:
            log.warning("回写待补发队列失败：%s", e)
        if ok or fail:
            log.warning("补发结果：成功 %d 条，仍失败 %d 条", ok, fail)
        return (ok, fail)

    @staticmethod
    def _note_ip_hint(msg: str) -> None:
        """60020 时把提示写到固定文件，方便事后排查（不推送、不抛出）。"""
        try:
            p = os.path.join(BASE_DIR, "logs", "notify_ip_blocked.txt")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except Exception:
            pass

    @staticmethod
    def _format(ev: dict) -> str:
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}.get(ev.get("level", "warn"), "⚠️")
        lines = [
            "> 类型：**%s**  ·  时间：%s" % (ev.get("kind", "-"), ev.get("ts", "-")),
        ]
        if ev.get("robot"):
            lines.append("> 账号 / 机器人：%s" % ev["robot"])
        lines.append("")
        lines.append("## %s %s" % (icon, ev.get("title", "")))
        if ev.get("detail"):
            lines.append("")
            lines.append(str(ev["detail"])[:1500])
        extras = {k: v for k, v in ev.items()
                  if k not in ("ts", "epoch", "kind", "level", "title", "detail", "shot", "robot")}
        if extras:
            lines.append("")
            lines.append("> " + " · ".join("%s=%s" % (k, v) for k, v in extras.items()))
        if ev.get("shot"):
            lines.append("")
            lines.append("截图：`%s`" % os.path.basename(ev["shot"]))
        return "\n".join(lines)

    def flush(self, timeout: float = 5.0) -> None:
        """等待队列清空 + 补发待发队列（收尾时用，超时不报错）。

        2026-10-30：join 完 worker 后再同步补一次 `pending_file` ——
        当天 14:08「✅ 账号 觅夏」那条就是被企微 5s 读超时丢掉的，
        现在会落到待补发队列，收尾时补出去（总预算 `max(10s, timeout×2)`）。
        """
        if not self._worker:
            return
        try:
            self._q.put(None)
            self._worker.join(timeout)
        except Exception:
            pass
        if self.enabled:
            try:
                self.resend_pending(budget=max(10.0, float(timeout) * 2))
            except Exception as e:
                log.warning("补发待发队列异常（已忽略）：%s", e)


# ---------------------------------------------------------------- 单例
_NOTIFIER: "Notifier | None" = None
_INIT_LOCK = threading.Lock()


def get_notifier(cfg: dict | None = None) -> Notifier:
    """进程内单例。首次调用时传入 cfg（main.py 的 load_config 结果）。"""
    global _NOTIFIER
    with _INIT_LOCK:
        if _NOTIFIER is None:
            conf = load_notify_config(cfg)
            _NOTIFIER = Notifier(conf)
            if not conf.get("enabled"):
                log.info("告警通道未启用（notify.enabled=false）")
            elif not _NOTIFIER.enabled:
                log.warning("告警已开启但没有任何可用通道，请检查 %s", LOCAL_FILE)
            else:
                log.info("告警通道就绪：%s",
                         ", ".join(c.name for c in _NOTIFIER.channels))
        return _NOTIFIER


def alert(title: str, detail: str = "", **kw) -> None:
    """模块级快捷入口（未初始化时静默忽略，不报错）。"""
    if _NOTIFIER is not None:
        _NOTIFIER.alert(title, detail, **kw)


def flush(timeout: float = 5.0) -> None:
    """模块级收尾入口：等待告警队列真正发送完成（未初始化时静默忽略）。

    2026-10-27 修复「最后一个账号的完成告警丢失」：告警发送走后台 daemon
    线程，进程退出时 `atexit` 只有 3s 等待窗口；多账号模式**最后一个账号**
    （唐灵固定 4/4）的完成告警发出后主流程往往 ~1ms 就结束，推送常被退出
    竞态丢弃（10-26 12:35 / 10-27 13:56 实测：无「告警已推送」确认行）。
    在主流程收尾处显式调用本函数可提前等待、并给足时间。

    注意：flush 会向队列投递终止哨兵并等待 worker 退出，**调用后不应再
    推送新告警**（收尾场景适用）。
    """
    if _NOTIFIER is not None:
        _NOTIFIER.flush(timeout)


def resend_pending(max_items: int | None = None,
                   budget: float | None = None) -> tuple:
    """模块级补发入口（未初始化时返回 (0, 0)，不报错）。"""
    if _NOTIFIER is None:
        return (0, 0)
    return _NOTIFIER.resend_pending(max_items, budget)


def _shutdown() -> None:
    if _NOTIFIER is not None:
        _NOTIFIER.flush(3.0)


atexit.register(_shutdown)


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="qiandao 告警链路自检")
    ap.add_argument("--config", default=os.path.join(BASE_DIR, "config.yaml"))
    ap.add_argument("--check", action="store_true", help="校验配置，不发消息")
    ap.add_argument("--test", action="store_true", help="发送测试消息")
    ap.add_argument("--ip", action="store_true", help="打印本机公网 IP")
    ap.add_argument("--resend", action="store_true",
                    help="补发待补发队列（logs/notify_pending.jsonl）后退出")
    ap.add_argument("--limit", type=int, default=None,
                    help="配合 --resend：本次最多补发几条（默认取 notify.resend_max）")
    a = ap.parse_args()

    cfg = {}
    if os.path.isfile(a.config):
        import yaml
        with open(a.config, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    conf = load_notify_config(cfg)

    if a.ip:
        for url in ("https://myip.ipip.net", "https://api.ipify.org"):
            try:
                print(requests.get(url, timeout=6).text.strip())
                break
            except Exception:
                continue
        sys.exit(0)

    print("notify.enabled=%s channel=%s fallback=%s"
          % (conf["enabled"], conf["channel"], conf["fallback"] or "-"))
    chans = build_channels(conf)
    if not chans:
        print("✗ 没有可用通道，请检查 %s 中的凭据" % LOCAL_FILE)
        sys.exit(1)
    print("✓ 可用通道：%s" % ", ".join(c.name for c in chans))

    if a.resend:
        n = get_notifier(cfg)
        if not n.enabled:
            print("✗ 告警未启用或无可用通道，无法补发")
            sys.exit(1)
        ok, fail = n.resend_pending(max_items=a.limit, budget=None)
        print("补发完成：成功 %d 条，仍失败 %d 条（队列 %s）"
              % (ok, fail, n._pending_path()))
        sys.exit(0 if fail == 0 else 4)

    if a.test:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        title = "✅ 告警链路已打通"
        content = ("> 时间：%s\n> 通道：%s\n\n"
                   "后续出现官方版式变动 / 新活动弹窗 / 广告样式异常时，"
                   "会通过这条通道推送。\n\n测试消息（收到即表示链路正常）"
                   % (ts, ", ".join(c.name for c in chans)))
        ok = False
        for ch in chans:
            try:
                ch.send(title, content)
                print("✓ 测试消息已通过 %s 发送，请查看接收端" % ch.name)
                ok = True
                break
            except Exception as e:
                print("✗ %s 发送失败：%s" % (ch.name, e))
        sys.exit(0 if ok else 3)
