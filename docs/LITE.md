# Lite text chat (opt-in)

**新增 Windows 原生双击版：**[`lite.cmd`](../lite.cmd) 默认打开极小本机网页，`lite.ps1 chat` 用命令行；首次配置和加密保存 Key 更简单，不需要 WSL / Docker。见[原生极轻量版](WINDOWS_LITE.md)。以下保留原来的无配置文件文字入口，二者复用同一 HTTP 传输与轻量回复实现。

A small standalone text client, **not** AstrBot, QQ, a web server, or an account integration. It uses only Python 3.12+ standard library and the existing `bot_core.http_host.ChatCompletionHost` for the OpenAI-compatible HTTP request. Run from the repository root on Windows or Linux; no Docker, VM, QQ login, plugin config, or extra packages are needed. Nothing is deployed or activated by adding this file.

## Configure

Supply a trusted OpenAI-compatible `/v1` base URL, model ID, and a key **through environment variables**, never on the command line. `BOT_MODEL_BASE_URL` and `BOT_MODEL` are required unless overridden by `--base-url` and `--model`. `BOT_MODEL_API_KEY` is the default credential variable. HTTPS requires a nonempty key; HTTP is permitted only for explicit loopback (`localhost`, `127.0.0.1`, `::1`). URL validation, redirect refusal and the response-size limit come from the existing host. Don't include `/chat/completions` in the base URL.

```sh
# Linux/macOS shell (illustrative names; substitute your own trusted endpoint/model)
export BOT_MODEL_BASE_URL='https://example.invalid/v1'
export BOT_MODEL='your-model'
export BOT_MODEL_API_KEY='your-key'
python tools/lite.py --system 'You are a helpful assistant.' --prompt 'Hello'
printf 'Hello\n' | python tools/lite.py --system-file persona.txt
python tools/lite.py --system-file persona.txt --interactive --history-turns 12
```

```powershell
# Windows PowerShell
$env:BOT_MODEL_BASE_URL = 'https://example.invalid/v1'
$env:BOT_MODEL = 'your-model'
$env:BOT_MODEL_API_KEY = 'your-key'
python tools/lite.py --system 'You are a helpful assistant.' --prompt 'Hello'
python tools/lite.py --system-file persona.txt --interactive --history-turns 12
```

Use `--api-key-env OTHER_ENV_NAME` to select another credential **variable name**; it must be an ASCII environment identifier. There is intentionally no literal `--api-key` argument. Use exactly one of `--system` and `--system-file` (the latter explicitly reads UTF-8). With no `--prompt`, one-shot mode reads all stdin. In `--interactive` mode, each input line is a turn, `/exit` quits, and EOF quits; `--prompt` cannot be combined with it. `--history-turns` accepts positive integers and defaults to 12 successful user/assistant pairs; the system message and current prompt are included separately. This bounds **turn count, not token count**; API usage/cost can still grow with large messages. There is no automatic truncation of user text.

The CLI prints only assistant `content`, never a separate provider `reasoning_content`. It sends no tool definitions and refuses responses containing tool calls. It does **not** run code, browse, or invoke bot tools. It does not write history or credentials, and each process starts a fresh in-memory session. A failed request prints only `lite: configuration or model completion failed` on stderr (status 1), without exposing raw upstream errors. Interactive responses are printed as text, not JSON; one-shot output is also plain text. Windows standard streams are configured for UTF-8 when supported.

**Model trust boundary:** This transport cannot reliably distinguish private planning *embedded as unmarked ordinary `content`* by a provider; such content could be printed. Choose a trusted text-only endpoint/model that returns public final answers, and do not use this CLI for sensitive reasoning isolation. This is not a fix for upstream model reasoning leaks.

Offline tests: `python -m unittest discover -s tests -p test_lite.py -v` (no network). Running `python -m bot_core` remains the separate existing harness CLI and is not altered by this profile.
