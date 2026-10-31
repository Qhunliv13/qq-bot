"""Real-browser, offline synthetic-runtime acceptance for the full setup UI.

This is Chromium rendering/HTTP coverage, not Docker/Compose, model-provider or
QQ acceptance. Commands and model text are deterministic local substitutes.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

import pytest

playwright = pytest.importorskip("playwright.sync_api")

# The independent controller suite owns the shared safe real-bootstrap fixture.
pytest_plugins = ("test_setup_wizard_controller",)


@pytest.fixture(params=["container", "windows-native"])
def browser_session(core, tmp_path, monkeypatch, request):
    """Serve real setup over loopback and open an isolated, unrecorded browser tab."""
    from tools.setup_wizard import Controller, Server

    # Only deployment-specific presentation varies; lifecycle/model remain
    # deterministic substitutes, not native full-host or package-install proof.
    monkeypatch.setattr(core, "DEPLOYMENT_MODE", request.param, raising=False)
    controller = Controller(core, tmp_path / "browser private state", "browser-wizard")
    calls = []

    def check(data, *, quiet=False):
        """Represent a successful environment probe without a Docker process."""
        calls.append(("check", str(data), quiet))

    def command(action, **kwargs):
        """Record same-controller lifecycle operations without starting services."""
        calls.append((action,))

    def local(prompt):
        """Return deterministic visible model text with no external request."""
        calls.append(("chat", prompt))
        return "离线浏览器测试回复：你好！"

    monkeypatch.setattr(core, "check_engine", check)
    monkeypatch.setattr(controller, "_command", command)
    monkeypatch.setattr(controller, "_local", local)
    server = Server(controller, port=0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    errors = []
    external_requests = []
    try:
        with playwright.sync_playwright() as manager:
            try:
                browser = manager.chromium.launch(headless=True)
            except playwright.Error as exc:
                # Optional dependency is not proof that browser binaries exist.
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Playwright Chromium executable is not installed")
                raise
            context = browser.new_context(viewport={"width": 1365, "height": 1000},
                                          service_workers="block", accept_downloads=False)

            def route(request_route):
                """Block every nonlocal HTTP request and suppress the unused favicon."""
                request = request_route.request
                if not request.url.startswith(server.origin + "/"):
                    external_requests.append(request.url)
                    request_route.abort()
                elif urlsplit(request.url).path == "/favicon.ico":
                    request_route.fulfill(status=204, body="")
                else:
                    request_route.continue_()

            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page._setup_expected_console_errors = []

            def console(message):
                """Permit only explicit negative-case HTTP diagnostics, not script errors."""
                if message.type == "error" and not any(
                        expected in message.text for expected in page._setup_expected_console_errors):
                    errors.append(message.text)

            page.on("console", console)
            try:
                yield page, controller, server, calls
                assert errors == [], "Browser console/page error: " + repr(errors)
                assert external_requests == [], "Unexpected external network request"
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _capture(page, name):
    """Optionally save safe UI evidence before any credentials are disclosed."""
    value = os.environ.get("BOT_SETUP_SCREENSHOT_DIR")
    if not value:
        return
    folder = Path(value)
    # Evidence is opt-in and must stay in the designated inbox, not the source tree.
    if "_inbox" not in folder.resolve().parts:
        raise ValueError("BOT_SETUP_SCREENSHOT_DIR must be inside an _inbox directory")
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / (name + ".png")), full_page=True)


def _open(page, server):
    """Load the local capability once; JavaScript must immediately erase its fragment."""
    # Reopening is a new document, not a same-document fragment navigation.
    page.goto("about:blank")
    page.goto(server.url)
    playwright.expect(page.locator("#statusText")).not_to_have_text("正在连接本机向导…")
    assert page.url == server.origin + "/"


def _configure(page):
    """Fill actual noncoder forms and submit one fresh configuration request."""
    page.locator('[data-panel="0"] [data-next="1"]').click()
    page.locator("#base").fill("https://provider.invalid/v1")
    page.locator("#modelId").fill("offline-browser-model")
    page.locator("#key").fill("fictitious-browser-only-secret")
    page.locator('#modelForm button[type="submit"]').click()
    page.locator("#persona").fill("你叫小星，清晰友好。")
    page.locator('#botForm button[type="submit"]').click()
    playwright.expect(page.locator('[data-panel="3"]')).to_be_visible()
    playwright.expect(page.locator("#startBot")).to_be_enabled()
    assert page.locator("#key").input_value() == ""


def test_real_browser_full_wizard_consent_credentials_and_mobile(browser_session):
    """Exercise real DOM, JS, auth and state while keeping all runtime calls synthetic."""
    page, controller, server, calls = browser_session
    _open(page, server)
    playwright.expect(page.get_by_role("heading", name="让你的第一个 Bot 上线")).to_be_visible()
    if controller.core.DEPLOYMENT_MODE == "windows-native":
        playwright.expect(page.locator("#runtimeRequirements")).to_contain_text("原生 Windows 完整版")
        playwright.expect(page.locator("#runtimeRequirements")).to_contain_text("不需要 WSL")
        playwright.expect(page.locator("#environmentHelp")).to_contain_text("不修改全局 Python")
    else:
        playwright.expect(page.locator("#runtimeRequirements")).to_contain_text("容器部署")
    _capture(page, "bot-setup-welcome")
    page.locator('[data-panel="0"] [data-action="check"]').click()
    playwright.expect(page.locator("#environment")).to_have_text("已验证", timeout=10000)
    assert calls == [("check", str(controller.data), True)]
    assert not controller.data.exists()
    _configure(page)
    assert controller.snapshot()["initialized"] is True
    settings = controller.core.load_install(controller.data, controller.name)
    assert settings["model"]["model"] == "offline-browser-model"
    assert settings["persona"] == "你叫小星，清晰友好。"
    assert settings["owner_qqs"] == []
    page.locator("#startBot").click()
    playwright.expect(page.locator("#runtime")).to_have_text("已验证", timeout=10000)
    playwright.expect(page.locator("#chatArea")).to_be_visible()
    assert [value[0] for value in calls] == ["check", "up", "health"]
    assert page.locator("#model").inner_text() == "尚未验证"
    assert page.locator("#qq").inner_text() == "尚未验证"

    # Without the explicit per-turn checkbox, no request is admitted or charged.
    page.locator('#chatForm button[type="submit"]').click()
    playwright.expect(page.locator("#statusText")).to_contain_text("请先勾选")
    assert not [value for value in calls if value[0] == "chat"]
    page.locator("#prompt").fill("你好，请勿执行工具。")
    page.locator("#charge").check()
    page.locator('#chatForm button[type="submit"]').click()
    playwright.expect(page.locator("#answer")).to_have_text("离线浏览器测试回复：你好！", timeout=10000)
    assert [value for value in calls if value[0] == "chat"] == [("chat", "你好，请勿执行工具。")]
    assert not page.locator("#charge").is_checked()
    assert page.locator("#qq").inner_text() == "尚未验证"
    _capture(page, "bot-setup-running")
    page.set_viewport_size({"width": 390, "height": 844})
    _capture(page, "bot-setup-mobile")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") is True
    page.set_viewport_size({"width": 1365, "height": 1000})

    page.locator('[data-next="4"]').click()
    playwright.expect(page.locator('[data-panel="4"]')).to_be_visible()
    playwright.expect(page.locator('a[data-route="/chat"]')).to_have_attribute(
        "href", controller.snapshot()["dashboard_url"] + "/#/chat")
    assert settings["dashboard_password"] not in page.locator("body").inner_text()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#showCredentials").click()
    playwright.expect(page.locator("#credentials")).to_be_hidden()
    page.clock.install()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#showCredentials").click()
    playwright.expect(page.locator("#credentials")).to_contain_text(settings["dashboard_password"])
    assert "fictitious-browser-only-secret" not in page.locator("body").inner_text()
    page.clock.fast_forward(30001)
    playwright.expect(page.locator("#credentials")).to_be_hidden()
    assert page.locator("#credentials").inner_text() == ""
    assert page.evaluate("localStorage.length") == 0
    assert page.evaluate("sessionStorage.length") == 0
    assert page.context.cookies() == []


def test_real_browser_reopen_preserves_install_and_no_automatic_runtime_actions(browser_session):
    """Reopening the same owned installation enters management without new setup."""
    page, controller, server, calls = browser_session
    _open(page, server)
    _configure(page)
    before = (controller.data / "settings.json").read_bytes()
    _open(page, server)
    playwright.expect(page.locator('[data-panel="3"]')).to_be_visible()
    playwright.expect(page.locator('[data-step="1"]')).to_be_disabled()
    playwright.expect(page.locator('[data-step="2"]')).to_be_disabled()
    assert page.locator("#key").input_value() == ""
    assert before == (controller.data / "settings.json").read_bytes()
    assert calls == []
    assert controller.snapshot()["model"] == "unverified"
    assert controller.snapshot()["qq"] == "unverified"


def test_real_browser_invalid_base_preserves_fields_and_creates_no_state(browser_session):
    """A mistaken full request URL gives field-specific advice without losing inputs."""
    page, controller, server, calls = browser_session
    _open(page, server)
    page.locator('[data-panel="0"] [data-next="1"]').click()
    wrong_base = "https://provider.invalid/v1/chat/completions"
    page.locator("#base").fill(wrong_base)
    page.locator("#modelId").fill("preserve-this-model")
    page.locator("#key").fill("fictitious-retained-private-key")
    page.locator('#modelForm button[type="submit"]').click()
    page.locator("#persona").fill("保留这一段角色说明。")
    page._setup_expected_console_errors.append("400 (Bad Request)")
    page.locator('#botForm button[type="submit"]').click()
    playwright.expect(page.locator("#statusText")).to_contain_text("服务地址无效")
    assert not controller.data.exists()
    assert controller.snapshot()["initialized"] is False
    assert calls == []
    assert page.locator("#base").input_value() == wrong_base
    assert page.locator("#modelId").input_value() == "preserve-this-model"
    assert page.locator("#key").input_value() == "fictitious-retained-private-key"
    assert page.locator("#persona").input_value() == "保留这一段角色说明。"
    assert "fictitious-retained-private-key" not in page.locator("#statusText").inner_text()
    page.locator('#botForm [data-next="1"]').click()
    page.locator("#base").fill("https://provider.invalid/v1")
    page.locator('#modelForm button[type="submit"]').click()
    page.locator('#botForm button[type="submit"]').click()
    playwright.expect(page.locator('[data-panel="3"]')).to_be_visible()
    settings = controller.core.load_install(controller.data, controller.name)
    assert settings["model"]["model"] == "preserve-this-model"
    assert settings["model"]["api_key"] == "fictitious-retained-private-key"
    assert settings["persona"] == "保留这一段角色说明。"


def test_real_browser_success_waiting_failure_never_displays_previous_answer(browser_session, monkeypatch):
    """Real render clears a prior success while the next charged turn waits then fails."""
    page, controller, server, calls = browser_session
    _open(page, server)
    _configure(page)
    page.locator("#startBot").click()
    playwright.expect(page.locator("#runtime")).to_have_text("已验证", timeout=10000)
    page.locator("#charge").check()
    page.locator('#chatForm button[type="submit"]').click()
    playwright.expect(page.locator("#answer")).to_be_visible()
    playwright.expect(page.locator("#answer")).to_have_text("离线浏览器测试回复：你好！", timeout=10000)
    entered, release = threading.Event(), threading.Event()

    def delayed_failure(prompt):
        """Keep the pending DOM observable, then fail locally without contacting a model."""
        calls.append(("chat", prompt))
        entered.set()
        assert release.wait(timeout=15)
        raise RuntimeError("fictitious-private-error")

    monkeypatch.setattr(controller, "_local", delayed_failure)
    page.locator("#prompt").fill("第二次试聊只发送一次。")
    page.locator("#charge").check()
    try:
        page.locator('#chatForm button[type="submit"]').click()
        assert entered.wait(timeout=3)
        playwright.expect(page.locator("#model")).to_have_text("正在检查…", timeout=10000)
        playwright.expect(page.locator("#answer")).to_be_hidden()
        playwright.expect(page.locator("#answer")).to_have_text("")
        assert controller.snapshot()["answer"] == "" and controller.snapshot()["busy"]
    finally:
        release.set()
        controller.thread.join(timeout=3)
        assert not controller.thread.is_alive()
    playwright.expect(page.locator("#model")).to_have_text("未通过，请检查", timeout=10000)
    playwright.expect(page.locator("#answer")).to_be_hidden()
    playwright.expect(page.locator("#answer")).to_have_text("")
    playwright.expect(page.locator("#statusText")).to_contain_text("没有确认收到回复")
    assert "fictitious-private-error" not in page.locator("body").inner_text()
    assert len([value for value in calls if value[0] == "chat"]) == 2


def test_real_browser_oversized_chat_is_local_input_error_without_busy(browser_session):
    """The authoritative server rejects 6000 Chinese characters without a worker/model call."""
    page, controller, server, calls = browser_session
    _open(page, server)
    _configure(page)
    page.locator("#startBot").click()
    playwright.expect(page.locator("#runtime")).to_have_text("已验证", timeout=10000)
    page.locator("#prompt").fill("汉" * 6000)
    page.locator("#charge").check()
    page._setup_expected_console_errors.append("400 (Bad Request)")
    page.locator('#chatForm button[type="submit"]').click()
    playwright.expect(page.locator("#statusText")).to_contain_text("编码后的大小超过本地请求上限")
    playwright.expect(page.locator("#statusText")).to_contain_text("本次未发送，不会计费")
    assert controller.snapshot()["busy"] is False
    assert controller.snapshot()["model"] == "unverified"
    assert not [value for value in calls if value[0] == "chat"]
    assert page.locator("#prompt").input_value() == "汉" * 6000
    assert not page.locator("#charge").is_checked()


def test_real_browser_synchronous_double_chat_submit_consumes_one_consent(browser_session):
    """Two immediate form events with one confirmation admit exactly one charged turn."""
    page, controller, server, calls = browser_session
    _open(page, server)
    _configure(page)
    page.locator("#startBot").click()
    playwright.expect(page.locator("#runtime")).to_have_text("已验证", timeout=10000)
    page.locator("#prompt").fill("只发送这一次测试。")
    page.locator("#charge").check()
    page.evaluate("""() => {
      const form = document.getElementById('chatForm');
      form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
      form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
    }""")
    playwright.expect(page.locator("#answer")).to_have_text("离线浏览器测试回复：你好！", timeout=10000)
    assert [value for value in calls if value[0] == "chat"] == [("chat", "只发送这一次测试。")]
    assert not page.locator("#charge").is_checked()
    # A subsequent submission after the very fast worker completes still needs
    # a new deliberate confirmation rather than replaying the original consent.
    page.locator('#chatForm button[type="submit"]').click()
    playwright.expect(page.locator("#statusText")).to_contain_text("请先勾选")
    assert len([value for value in calls if value[0] == "chat"]) == 1
