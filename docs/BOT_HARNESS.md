# Simple independent bot harness

This is a thin local host, not a new platform framework. `bot_core.Harness` directly
calls the existing `plugin.agent_loop.run`; it does not copy the model/tool loop.
It can execute without importing AstrBot, connecting to QQ or having a QQ event.
There is no automatic scheduler, capability graph, login migration or background daemon.

## What stays unchanged

- Existing QQ/NapCat process, login session, account files and production adapter.
- Production task, memory, persona, owner checks and publication behavior.
- The existing loop's tool transcripts, observation semantics, finalization and errors.
- No production cutover; only a later explicit user instruction can authorize it.

The harness does not keep QQ logged in: it simply never touches the login process.
A later external QQ reauthorization can still be required by QQ itself; no software
change can honestly guarantee that a server-side login will never expire.

## Python embedding

```python
from bot_core import Harness

# Host-owned callbacks, not new implementations of the agent loop.
harness = Harness(system=persona, llm_messages=model_callback,
                  run_tool=authorized_tool_callback)
result = await harness.run("Work on this request")
print(result.text)
```

`model_callback(messages, allow_tools=True)` returns `(text, tool_calls)` in the
existing loop's format. `authorized_tool_callback(name, arguments)` executes actual
host-authorized tooling. An optional existing `asyncio.Lock` can be injected so QQ and
local requests eventually share the **same** whole-turn FIFO rather than competing
execution pipelines. This change does not yet attach the live production callbacks.

If tools are not injected, tool requests return `tool_unavailable`, never fake success.
The caller owns identity, permissions and authorized context. The harness creates a
fresh turn transcript; it does not automatically load production memory or chat history.
An empty caller-supplied `transcript=[]` can retain partial evidence if the original
completion exception propagates. Tools are not replayed on an exception.

## Local command

The built-in HTTP host uses a chat-completions-compatible endpoint. It does not read
production configuration or silently choose a model. Supply your endpoint/model and
an operator-selected system prompt; credentials come from an environment variable,
never a command-line plaintext key.

```powershell
$env:BOT_MODEL_BASE_URL = "https://your-provider.example/v1"
$env:BOT_MODEL = "your-configured-model"
$env:BOT_MODEL_API_KEY = "your-key"
python -m bot_core --system-file ./persona.txt --prompt "Hello"
```

Use your own UTF-8 persona file; no new coercive persona is shipped or installed.
The built-in HTTP host is intentionally small and does not advertise the production
106-tool set. To attach your existing model/tools, provide a trusted Python factory:

```powershell
python -m bot_core --host my_host:create_harness --prompt "Run a local task"
```

The explicit factory returns a `Harness`. Loading a factory runs trusted operator code;
it is not a sandbox or a permission boundary. The harness does not scan or import
arbitrary modules automatically. Input may also be read from stdin when `--prompt`
is omitted. CLI JSON reports the actual text/tool names with `published: false` and
`destination: local`: printing a result is not QQ delivery.

## Verification and limits

Tests run the real shared loop with synthetic model/tools, verifying whole-turn FIFO,
sibling completion, silence, actual observations and error propagation. HTTP-host tests
mock or use local protocol fixtures; they are not claims of provider/model quality.
No test changes the QQ login or production model route.

This delivers independent **invocation of the existing agent loop**, not automatic
migration of every feature into a new process. Existing memory/tasks and authorized QQ
tools remain in their current host until explicitly bound through a reviewed adapter.
No new durable queue, independent identity policy, always-on API or production service
is introduced. The larger `BOT_CORE_DECOUPLING.md` is a future design, not required
runtime complexity for using this harness.
