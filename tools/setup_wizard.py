"""Local first-run UI over the existing full bootstrap and same-instance Bot.

No second model/agent engine or QQ login implementation lives here. Only the
selected owned install is controlled; raw command output is never sent to HTTP.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import webbrowser


# Every route/body is bounded independently of Docker and model response size.
_MAX_BODY = 96 * 1024
_CSP = ("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")
_ERROR = "操作未完成。请检查当前步骤后重试；已有配置和数据不会被重置。"


class InputError(ValueError):
    """Carry only a fixed user-facing field hint, never a rejected input value."""


class Controller:
    """Serialize deliberate actions, preserving external state and graded readiness."""

    def __init__(self, core, data, name):
        self.core, self.data, self.name = core, data, name
        self.lock = threading.RLock()
        self.busy = False
        self.thread = None
        self.state = {"environment": "unknown", "runtime": "unverified", "model": "unverified",
                      "qq": "unverified", "stage": "welcome", "message": "欢迎！按页面提示创建你的第一个 Bot。"}
        # Startup validates selected state, but does not probe models/QQ or containers.
        self.initialized = (data / core.MARKER).exists()
        if self.initialized:
            core.load_install(data, name)
            self.state.update(stage="manage", message="已找到这一个 Bot 的配置，不会覆盖。可以检查并启动。")
        elif data.exists() and any(data.iterdir()):
            raise ValueError("unmanaged_data_directory")

    def snapshot(self):
        """Expose readiness and local access only, never saved credentials or raw logs."""
        with self.lock:
            value = dict(self.state, initialized=self.initialized, busy=self.busy)
            if self.initialized:
                settings = self.core.load_install(self.data, self.name)
                env = self.core.docker_environment(self.data, settings)
                value["dashboard_url"] = "http://127.0.0.1:" + env["BOT_WEB_PORT"]
            return value

    def _set(self, **values):
        """Publish bounded progress atomically while one worker owns the operation."""
        with self.lock:
            self.state.update(values)

    def _command(self, action, *, timeout=60):
        """Reuse bootstrap CLI with fixed argv, discarded diagnostics and no input keys."""
        return subprocess.run(
            [sys.executable, str(self.core.REPO_ROOT / "tools" / "bootstrap.py"), action,
             "--data-dir", str(self.data), "--runtime-name", self.name, "--non-interactive"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=self.core.REPO_ROOT,
            env=self.core.docker_environment(self.data), shell=False, timeout=timeout, check=True)

    def _local(self, prompt):
        """Run one explicit charged test through the existing local full-runtime entry.

        Chat reaches bootstrap on stdin, not in this launcher's arguments. The
        existing container client currently receives the prompt on its own argv;
        use a nonsensitive test message. An ambiguous outcome is never retried.
        """
        result = subprocess.run(
            [sys.executable, str(self.core.REPO_ROOT / "tools" / "bootstrap.py"), "chat",
             "--data-dir", str(self.data), "--runtime-name", self.name, "--non-interactive"],
            input=prompt.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=self.core.REPO_ROOT, env=self.core.docker_environment(self.data), shell=False, check=True)
        # The native client emits exactly one JSON response. Only visible text is
        # returned, never tool output, configuration, component URLs or raw errors.
        if len(result.stdout) > 8 * 1024 * 1024:
            raise ValueError("local_response_too_large")
        response = json.loads(result.stdout.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ValueError("local_response_failed")
        texts = [part["text"] for output in response.get("outputs", [])
                 if isinstance(output, dict) for part in output.get("components", [])
                 if isinstance(part, dict) and isinstance(part.get("text"), str)]
        answer = "\n".join(texts).strip()
        if not answer:
            raise ValueError("no_visible_reply")
        # Avoid echoing known install secrets even if a model accidentally quotes one.
        settings = self.core.load_install(self.data, self.name)
        for secret in (settings["model"]["api_key"], settings["dashboard_password"]):
            if secret:
                answer = answer.replace(secret, "[已隐藏私密信息]")
        return answer[:32000]

    def action(self, action, payload):
        """Validate an allowlisted action before scheduling at most one owned worker."""
        if action not in {"check", "save", "start", "stop", "chat", "credentials"}:
            raise ValueError("unknown_action")
        allowed = {"save": {"model", "owner_qqs", "persona"}, "chat": {"prompt", "confirm"},
                   "stop": {"confirm"}, "credentials": {"confirm"}}.get(action, set())
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("invalid_payload")
        with self.lock:
            if self.busy:
                raise InputError("上一步仍在进行，请等待页面显示结果，不要重复发送。")
            if action == "save":
                if self.initialized:
                    raise ValueError("already_initialized")
                model, owners, persona = payload.get("model"), payload.get("owner_qqs", []), payload.get("persona", "")
                if not isinstance(model, dict) or set(model) != {"base_url", "model", "api_key"}:
                    raise ValueError("invalid_model")
                for field in ("base_url", "model", "api_key"):
                    try:
                        self.core.validate_model_field(field, model[field])
                        model[field].encode("utf-8")
                    except (ValueError, UnicodeError):
                        raise InputError({"base_url": "服务地址无效：请粘贴服务商的 HTTP(S) Base URL，不要包含密钥、空格或 /chat/completions。",
                                          "model": "模型名称无效：请填写服务商提供的完整模型 ID。",
                                          "api_key": "密钥无效：请重新粘贴 API Key，不要包含换行或控制字符。"}[field]) from None
                try:
                    self.core.validate_owner_ids(owners)
                except ValueError:
                    raise InputError("管理员 QQ 号无效：请填写 5–20 位数字，多个号码用英文逗号分隔，或留空跳过。") from None
                if not isinstance(persona, str) or len(persona) > 16000:
                    raise InputError("角色说明过长或格式无效，请缩短后重试。")
                try:
                    persona.encode("utf-8")
                except UnicodeError:
                    raise InputError("角色说明含有无法保存的字符，请重新输入。") from None
                self.core.initialize_settings(self.data, self.name, model, owners, persona)
                self.initialized = True
                self.state.update(stage="configured", message="配置已安全保存到本机。接下来点击启动 Bot。")
                return {"ok": True}
            if action != "check" and not self.initialized:
                raise ValueError("not_initialized")
            if action == "credentials":
                if payload.get("confirm") is not True:
                    raise ValueError("confirmation_required")
                settings = self.core.load_install(self.data, self.name)
                return {"ok": True, "username": "astrbot", "password": settings["dashboard_password"],
                        "message": "仅初始登录信息；若你在控制台修改过密码，请使用新密码。不要截图分享。"}
            if action == "chat":
                prompt = payload.get("prompt")
                if (payload.get("confirm") is not True or not isinstance(prompt, str)
                        or not prompt.strip() or len(prompt) > 8000 or self.state["runtime"] != "ready"):
                    raise ValueError("invalid_chat")
            if action == "stop" and payload.get("confirm") is not True:
                raise ValueError("confirmation_required")
            self.busy = True
            self.state.update(stage=action, message={"check": "正在检查运行环境，不会发送模型或 QQ 消息。",
                "start": "正在准备并启动完整 Bot；首次下载和构建可能较久，请保留此窗口。",
                "stop": "正在停止这一个 Bot，配置与聊天记录会保留。",
                "chat": "正在等待 Bot 回复。此操作可能计费，不会自动重复发送。"}[action])
            self.thread = threading.Thread(target=self._worker, args=(action, dict(payload)), daemon=True)
            self.thread.start()
            return {"ok": True}

    def _worker(self, action, payload):
        """Perform one bounded control action; retain unknown outcomes without replay."""
        try:
            if action == "check":
                self._set(environment="checking")
                self.core.check_engine(self.data, quiet=True)
                self._set(environment="ready", message="环境检查通过。可以填写模型资料或启动已有 Bot。")
                if self.initialized:
                    try:
                        self._command("health", timeout=30)
                        self._set(runtime="ready", message="这一个 Bot 正在运行。模型与 QQ 需分别验证。")
                    except (OSError, ValueError, subprocess.SubprocessError):
                        self._set(runtime="unverified", message="环境可用；Bot 尚未确认运行，请点击启动。")
            elif action == "start":
                self._set(runtime="checking", model="unverified", qq="unverified")
                self._command("up", timeout=None)
                self._command("health", timeout=30)
                self._set(environment="ready", runtime="ready", message="Bot 已启动！先在这里试聊，再按需连接 QQ。")
            elif action == "stop":
                self._command("stop", timeout=None)
                self._set(runtime="unverified", model="unverified", qq="unverified", message="这一个 Bot 已停止。数据已保留，随时可以重新启动。")
            elif action == "chat":
                self._set(model="checking", answer="")
                answer = self._local(payload["prompt"])
                self._set(model="ready", answer=answer, message="已收到 Bot 的实际回复；这不代表 QQ 已连接。")
        except Exception:
            # Native errors can contain secrets, provider URLs or command lines.
            field = "environment" if action == "check" else "model" if action == "chat" else "runtime"
            message = ("环境尚未就绪。Windows 请打开 Docker Desktop 并启用此 Linux 环境；Linux 请安装并启动 Docker 和 Compose。修好后点击重新检查。"
                       if action == "check" else
                       "没有确认收到回复。请在高级控制台检查模型地址、名称和余额；不要反复点击，以免重复计费。"
                       if action == "chat" else
                       "操作未完成。首次构建需要网络和磁盘空间；检查运行环境后重试。已有配置和数据不会重置。")
            self._set(**{field: "failed"}, message=message)
        finally:
            with self.lock:
                self.busy = False


def _object(pairs):
    """Reject ambiguous duplicate JSON keys before any state or operation changes."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _constant(value):
    """Disallow nonfinite JSON extensions rather than coercing untrusted input."""
    raise ValueError("invalid_json_constant")


class Server(ThreadingHTTPServer):
    """Authorize a temporary browser tab against one loopback-only install session."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, controller, port=0):
        self.controller = controller
        self.token = secrets.token_urlsafe(32)
        self.nonce = secrets.token_hex(16)
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = "127.0.0.1:" + str(self.server_address[1])
        self.origin = "http://" + self.authority
        self.url = self.origin + "/#token=" + self.token

    def handle_error(self, request, client_address):
        """Never log request bodies, token-bearing targets or raw exception strings."""


class Handler(BaseHTTPRequestHandler):
    """Serve fixed local assets and allowlisted authenticated requests without logs."""

    server_version = "BotSetup"
    sys_version = ""

    def setup(self):
        """Bound stalled HTTP readers without imposing a model-generation timeout."""
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        """Disable request logging because URLs or bodies may contain pasted keys."""

    def parse_request(self):
        """Retain exact paths so stdlib normalization cannot widen allowed routes."""
        if not super().parse_request():
            return False
        self.path = self.requestline.split()[1]
        return True

    def send_error(self, code, message=None, explain=None):
        """Never reflect untrusted request text in stdlib-generated HTML errors."""
        self._json(code, {"ok": False, "message": _ERROR})

    def _send(self, status, body, content_type):
        """Disable caching, embedding, referrers, sniffing and cross-site access."""
        self.close_connection = True
        self.send_response(status)
        for key, value in {"Content-Type": content_type, "Content-Length": str(len(body)),
                           "Cache-Control": "no-store", "Connection": "close", "Referrer-Policy": "no-referrer",
                           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                           "Content-Security-Policy": _CSP.format(nonce=self.server.nonce)}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        """Serialize controlled UTF-8 JSON without untrusted HTTP header content."""
        self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    def _authorized(self, *, post=False):
        """Reject DNS rebinding, duplicated headers and browser cross-origin writes."""
        if self.headers.get_all("Host", []) != [self.server.authority]:
            return False
        if self.headers.get_all("Authorization", []) != ["Bearer " + self.server.token]:
            return False
        origins = self.headers.get_all("Origin", [])
        return origins == [self.server.origin] if post else origins in ([], [self.server.origin])

    def do_GET(self):
        """Only the fixed page is public; install metadata always needs bearer auth."""
        if self.headers.get_all("Host", []) != [self.server.authority]:
            return self._json(403, {"ok": False, "message": _ERROR})
        if self.path == "/":
            page = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
            return self._send(200, page.replace("__NONCE__", self.server.nonce).encode("utf-8"), "text/html; charset=utf-8")
        if self.path != "/api/state" or not self._authorized():
            return self._json(403, {"ok": False, "message": _ERROR})
        try:
            return self._json(200, self.server.controller.snapshot())
        except Exception:
            return self._json(400, {"ok": False, "message": _ERROR})

    def do_POST(self):
        """Validate framing, type, size, origin and JSON before dispatching actions."""
        if self.path != "/api/action" or not self._authorized(post=True):
            # Windows may reset a socket closed with unread request bytes before
            # the browser receives its 403. Discard only a small, unambiguous
            # body with a short deadline; never parse or act on rejected data.
            lengths = self.headers.get_all("Content-Length", [])
            if (len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit()
                    and 0 <= int(lengths[0]) <= _MAX_BODY
                    and not self.headers.get_all("Transfer-Encoding")):
                self.connection.settimeout(0.5)
                try:
                    self.rfile.read(int(lengths[0]))
                except OSError:
                    pass
            return self._json(403, {"ok": False, "message": _ERROR})
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or not 1 <= int(lengths[0]) <= _MAX_BODY or self.headers.get_all("Transfer-Encoding")
                    or self.headers.get_all("Content-Type", []) != ["application/json"]):
                raise ValueError("invalid_request_framing")
            body = self.rfile.read(int(lengths[0]))
            if len(body) != int(lengths[0]):
                raise ValueError("incomplete_body")
            value = json.loads(body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
            if not isinstance(value, dict) or not isinstance(value.get("action"), str):
                raise ValueError("invalid_action")
            action = value.pop("action")
            result = self.server.controller.action(action, value)
            return self._json(200, result)
        except InputError as error:
            return self._json(400, {"ok": False, "message": str(error)})
        except Exception:
            return self._json(400, {"ok": False, "message": _ERROR})


def serve(core, data, name, *, open_browser=True):
    """Keep setup visible until closed; closing it never stops an existing Bot."""
    with Server(Controller(core, data, name)) as server:
        # Windows consumes this exact prefix and opens the host browser in memory.
        print("BOT_SETUP_URL=" + server.url, flush=True)
        if open_browser:
            try:
                webbrowser.open(server.url)
            except Exception:
                print("浏览器未能自动打开，请在本机使用上方的完整链接。", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            # Do not stop containers or replay a model request when the tab exits.
            pass
