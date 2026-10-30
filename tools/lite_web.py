"""Dependency-free, loopback-only browser chat; one shared, nonpersistent conversation.

The browser receives only public chat text, never the system prompt or host settings.
Opening the startup fragment URL authorizes that tab until it is reloaded. All tabs
opened with that link share this server's bounded history; Reset clears it for all.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import secrets
import threading
import webbrowser

# Reuse the portable CLI's sole model path in both package and direct-script modes.
if __package__:
    from .lite import _reply
else:
    from lite import _reply

# Keep incoming bodies and prompts small independently (UTF-8 can use four bytes).
_MAX_BODY = 128 * 1024
_MAX_PROMPT = 32000
_BAD_REQUEST = "Invalid request. / 请求格式无效。"
_MODEL_ERROR = "Reply unavailable. Please try again. / 暂时无法回复，请重试。"
_CSP = ("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'")

# Single-file assets intentionally have no network fonts, libraries, images, or URLs.
_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>轻聊 Lite Chat</title>
<style nonce="__NONCE__">
:root{color-scheme:light dark;font-family:system-ui,"Segoe UI",sans-serif;background:#101723;color:#e9eef8}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:flex;justify-content:center}
main{width:min(900px,100%);height:100dvh;display:flex;flex-direction:column;padding:20px;gap:14px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px}h1{margin:0;font-size:1.4rem}
.subtitle{color:#aebdd5;font-size:.85rem;margin-top:5px}.banner{background:#1b2940;border:1px solid #314460;
 border-radius:12px;padding:12px;font-size:.85rem;line-height:1.6;color:#d2def2}
#messages{flex:1;overflow-y:auto;min-height:0;padding:6px;display:flex;flex-direction:column;gap:14px}
.bubble{padding:13px 16px;border-radius:16px;max-width:88%;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}
.user{align-self:flex-end;background:#294f9a}.assistant{align-self:flex-start;background:#223044}
.empty{margin:auto;color:#aebdd5;text-align:center;line-height:1.8}button,textarea{font:inherit}
button{border:1px solid #435773;border-radius:10px;padding:10px 15px;background:#223044;color:#edf3ff;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}#send{background:#385fc7;border-color:#385fc7;font-weight:600}
.composer{display:flex;align-items:flex-end;gap:10px}textarea{width:100%;resize:vertical;min-height:78px;max-height:240px;
 background:#172235;border:1px solid #435773;border-radius:12px;padding:12px;color:#edf3ff;line-height:1.5}
textarea:focus,button:focus-visible{outline:2px solid #90b5ff;outline-offset:2px}
.status{min-height:24px;color:#aebdd5;font-size:.85rem;display:flex;gap:8px;align-items:center}
.status.error{color:#ffc3bf}.spinner{display:none;width:14px;height:14px;border:2px solid #526680;
 border-top-color:#d2e0ff;border-radius:50%;animation:spin .8s linear infinite}
.busy .spinner{display:inline-block}@keyframes spin{to{transform:rotate(360deg)}}
.hint{font-size:.75rem;color:#91a4c2} @media(max-width:600px){main{padding:12px;gap:10px}.bubble{max-width:95%}
 header{align-items:flex-start}h1{font-size:1.2rem}.banner{font-size:.78rem}button{padding:10px}}
@media(prefers-reduced-motion:reduce){.spinner{animation:none}}
</style></head><body><main>
<header><div><h1>轻聊 <span lang="en">Lite Chat</span></h1>
<div class="subtitle">Windows 原生 · Standard library only · Local browser</div></div>
<button id="reset" type="button">清空全部 / Reset all</button></header>
<div class="banner">仅文本对话 / Text only · 无工具、无 QQ / No tools or QQ · 不保存到磁盘 / No persistent history<br>
此服务的所有标签页共享同一段有限记忆；清空会影响全部标签页。<br>
All tabs share one bounded conversation. Reset clears memory for everyone using this server.</div>
<section id="messages" aria-label="对话 / Conversation" aria-live="polite"></section>
<div id="status" class="status" role="status"><span class="spinner" aria-hidden="true"></span><span id="statusText"></span></div>
<div class="composer"><textarea id="prompt" maxlength="32000" aria-label="消息 / Message"
 placeholder="输入消息… / Type a message…"></textarea><button id="send" type="button">发送 / Send</button></div>
<div class="hint">Enter 发送 / send · Shift+Enter 换行 / new line · 关闭终端即停止 / Close terminal to stop</div>
</main><script nonce="__NONCE__">
'use strict';
// The fragment never reaches HTTP; erase it immediately and retain it only in memory.
const fragment = location.hash.slice(1);
history.replaceState(null, '', location.pathname);
const token = /^[a-f0-9]{64}$/.test(fragment) ? fragment : '';
const messages = document.getElementById('messages');
const promptBox = document.getElementById('prompt');
const sendButton = document.getElementById('send');
const resetButton = document.getElementById('reset');
const statusBox = document.getElementById('status');
const statusText = document.getElementById('statusText');
let busy = false;
function status(text, error=false) {
  statusText.textContent = text;
  statusBox.classList.toggle('error', error);
}
function setBusy(value) {
  busy = value;
  sendButton.disabled = value || !token;
  resetButton.disabled = value || !token;
  statusBox.classList.toggle('busy', value);
}
function bubble(role, text) {
  const node = document.createElement('div');
  node.className = 'bubble ' + (role === 'user' ? 'user' : 'assistant');
  // Treat model and user text as text, never HTML or Markdown execution.
  node.textContent = text;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
}
function render(items) {
  messages.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '开始一段轻量对话\nStart a lightweight conversation';
    messages.appendChild(empty);
  }
  for (const item of items) bubble(item.role, item.content);
}
async function api(path, body) {
  const options = {method: body === undefined ? 'GET' : 'POST',
    headers: {Authorization: 'Bearer ' + token}, credentials: 'omit', cache: 'no-store', redirect: 'error'};
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) throw new Error('Request failed');
  return response.json();
}
async function send() {
  const text = promptBox.value;
  if (busy || !token || !text.trim()) return;
  setBusy(true);
  status('正在回复… / Thinking…');
  try {
    const result = await api('/api/chat', {prompt: text});
    render(result.history);
    promptBox.value = '';
    status('已回复 / Ready');
  } catch (_) {
    status('暂时无法回复。请重试；若已刷新页面，请重新打开终端链接。 / Unable to reply. Retry or reopen the terminal link.', true);
  } finally {
    setBusy(false);
    promptBox.focus();
  }
}
sendButton.addEventListener('click', send);
promptBox.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); send();
  }
});
resetButton.addEventListener('click', async () => {
  if (busy || !token) return;
  setBusy(true);
  status('清空中… / Resetting…');
  try {
    const result = await api('/api/reset', {});
    render(result.history);
    status('全部记忆已清空 / Shared memory cleared');
  } catch (_) {
    status('清空失败，请重试。 / Reset failed. Please retry.', true);
  } finally { setBusy(false); }
});
async function start() {
  render([]);
  setBusy(true);
  if (!token) {
    status('请打开终端中包含授权片段的完整链接。 / Open the complete authorized link from the terminal.', true);
    setBusy(false); return;
  }
  try {
    const result = await api('/api/history');
    render(result.history);
    status('已连接本地服务 / Connected locally');
  } catch (_) { status('连接失败，请重新打开终端链接。 / Connection failed. Reopen the terminal link.', true); }
  finally { setBusy(false); }
}
start();
</script></body></html>'''


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate fields so JSON cannot mean different things to validators."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _json_constant(value: str) -> None:
    """Refuse NaN and infinity extensions, which are not valid JSON values."""
    raise ValueError("invalid JSON constant")


class _ChatServer(ThreadingHTTPServer):
    """Bind IPv4 loopback only and serialize one shared, bounded conversation."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, host, system: str, history_turns: int, port: int):
        self.host = host
        self.system = system
        self.history_turns = history_turns
        self.history: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.token = secrets.token_hex(32)
        self.nonce = secrets.token_hex(16)
        super().__init__(("127.0.0.1", port), _Handler)
        self.authority = f"127.0.0.1:{self.server_address[1]}"
        self.origin = f"http://{self.authority}"
        self.url = f"{self.origin}/#{self.token}"

    def handle_error(self, request, client_address) -> None:
        """Suppress tracebacks that could include prompts, URLs, or provider details."""
        # Broken local sockets are expected when a tab closes during generation.

    def messages(self) -> list[dict[str, str]]:
        """Export public text only; callers hold the shared conversation lock."""
        return [{"role": role, "content": text}
                for prompt, answer in self.history
                for role, text in (("user", prompt), ("assistant", answer))]


class _Handler(BaseHTTPRequestHandler):
    """Serve fixed assets and authenticated JSON without default request logging."""

    server_version = "LiteChat"
    sys_version = ""

    def setup(self) -> None:
        """Bound idle body/header reads so abandoned requests release their threads."""
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args) -> None:
        """Never emit URLs, tokens, request text, or raw server/provider errors."""
        # Even rejected URLs might contain a pasted secret; logging stays disabled.

    def parse_request(self) -> bool:
        """Preserve the exact request target, including stdlib-normalized // paths."""
        if not super().parse_request():
            return False
        # BaseHTTPRequestHandler folds leading // to /; exact routes must not do so.
        self.path = self.requestline.split()[1]
        return True

    def send_error(self, code, message=None, explain=None) -> None:
        """Replace stdlib HTML errors and reflected request text with fixed JSON."""
        self._json(code, {"error": _BAD_REQUEST})

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        """Use explicit lengths and defensive headers; never allow CORS or caching."""
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", _CSP.format(nonce=self.server.nonce))
        self.end_headers()
        # HEAD errors must still respect HTTP's no-response-body rule.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: dict) -> None:
        """Encode only structured public data, safely including Unicode text."""
        self._send(status, json.dumps(value, ensure_ascii=True).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _allowed(self, authenticate: bool) -> bool:
        """Reject rebinding, duplicate security headers, and cross-origin requests."""
        # Exact authority deliberately excludes localhost aliases and forwarded hosts.
        if self.headers.get_all("Host", []) != [self.server.authority]:
            self._json(403, {"error": "Forbidden."})
            return False
        origins = self.headers.get_all("Origin", [])
        if origins and origins != [self.server.origin]:
            self._json(403, {"error": "Forbidden."})
            return False
        # Static HTML has no credentials; public data always needs the bearer token.
        if authenticate:
            auth = self.headers.get_all("Authorization", [])
            expected = ("Bearer " + self.server.token).encode("utf-8")
            if len(auth) != 1 or not secrets.compare_digest(auth[0].encode("utf-8"), expected):
                self._json(401, {"error": "Unauthorized."})
                return False
        return True

    def _body(self) -> dict | None:
        """Read one bounded UTF-8 JSON object, refusing ambiguous body framing."""
        lengths = self.headers.get_all("Content-Length", [])
        types = self.headers.get_all("Content-Type", [])
        # Chunked bodies and conflicting lengths must never bypass the byte limit.
        if (self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1
                or not re.fullmatch(r"[0-9]{1,9}", lengths[0], flags=re.ASCII)):
            self._json(400, {"error": _BAD_REQUEST})
            return None
        length = int(lengths[0])
        if length > _MAX_BODY:
            self._json(413, {"error": "Request too large."})
            return None
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"error": "JSON required."})
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request")
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object,
                               parse_constant=_json_constant)
            if not isinstance(value, dict):
                raise ValueError("object required")
        except (ValueError, UnicodeError, RecursionError, OSError):
            self._json(400, {"error": _BAD_REQUEST})
            return None
        return value

    def do_GET(self) -> None:
        """Serve only the exact root asset or authenticated shared history."""
        if not self._allowed(authenticate=self.path != "/"):
            return
        if self.path == "/":
            # Only a CSP nonce is substituted; model and bearer secrets stay server-side.
            body = _HTML.replace("__NONCE__", self.server.nonce).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
        elif self.path == "/api/history":
            with self.server.lock:
                self._json(200, {"history": self.server.messages()})
        else:
            self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        """Validate first, then atomically update or clear successful chat history."""
        if not self._allowed(authenticate=True):
            return
        if self.path not in ("/api/chat", "/api/reset", "/api/history"):
            self._json(404, {"error": "Not found."})
            return
        body = self._body()
        if body is None:
            return
        # Non-chat endpoints accept only an empty object, never hidden instructions.
        if self.path != "/api/chat":
            if body:
                self._json(400, {"error": _BAD_REQUEST})
                return
            with self.server.lock:
                if self.path == "/api/reset":
                    self.server.history.clear()
                self._json(200, {"history": self.server.messages()})
            return
        prompt = body.get("prompt")
        if (set(body) != {"prompt"} or not isinstance(prompt, str)
                or not prompt.strip() or len(prompt) > _MAX_PROMPT):
            self._json(400, {"error": _BAD_REQUEST})
            return
        try:
            # Lone JSON surrogates are not valid UTF-8 model input.
            prompt.encode("utf-8")
        except UnicodeError:
            self._json(400, {"error": _BAD_REQUEST})
            return
        with self.server.lock:
            try:
                answer = _reply(self.server.host, self.server.system, prompt,
                                self.server.history, self.server.history_turns)
            except Exception:
                # Never append failures or expose provider errors/configuration.
                self._json(502, {"error": _MODEL_ERROR})
                return
            self.server.history.append((prompt, answer))
            if len(self.server.history) > self.server.history_turns:
                del self.server.history[:-self.server.history_turns]
            self._json(200, {"reply": answer, "history": self.server.messages()})


def create_server(host, system: str, history_turns: int = 12, port: int = 0) -> ThreadingHTTPServer:
    """Return a bound, not-yet-serving loopback server; callers own close/shutdown.

    ``token``, ``origin``, and ``url`` expose the startup authorization contract.
    Port zero asks the OS for a free port; explicit ports must be integers 1..65535.
    No model connection is attempted until an authenticated, validated chat request.
    """
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid local port")
    if type(history_turns) is not int or history_turns < 1:
        raise ValueError("invalid history limit")
    # Native setup deliberately defaults to no persona; an empty system is valid.
    if not isinstance(system, str):
        raise ValueError("system message must be text")
    return _ChatServer(host, system, history_turns, port)


def run_web(host, system: str, history_turns: int = 12, port: int = 0,
            open_browser: bool = True) -> None:
    """Block serving the local UI until Ctrl+C, closing the listener on every exit."""
    server = create_server(host, system, history_turns, port)
    try:
        # A fragment is intentionally printable for manual opening, never an HTTP query.
        print("Lite Chat: " + server.url, flush=True)
        print("Shared text-only memory; Ctrl+C to stop. / 共享纯文本记忆；Ctrl+C 停止。", flush=True)
        if open_browser:
            try:
                webbrowser.open(server.url, new=1)
            except Exception:
                # Browser launch is optional; a fixed message leaves manual opening usable.
                print("Open the local link above. / 请手动打开上方本地链接。", flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        # Shutdown in this serving thread must not call shutdown(), which would deadlock.
        pass
    finally:
        server.server_close()
