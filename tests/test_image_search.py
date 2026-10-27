"""Offline native-image contracts, provider fixtures and public-HTTP boundaries."""
import html
import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from plugin import image_search as module


def response(body, url="https://example.com/article", content_type="text/html"):
    """Build an injected metadata response without making actual HTTP calls."""
    return {"body": body, "final_url": url, "content_type": content_type}


def bing(records, quote="'"):
    """Encode real Bing m attributes including JSON escapes and HTML entities."""
    return "".join("<a class='iusc' m=" + quote + html.escape(json.dumps(record), quote=True) + quote + "></a>" for record in records)


def test_schema_defaults_and_limits():
    """Both native tools expose the exact documented bounded input contract."""
    tools = {item["function"]["name"]: item["function"] for item in module.schema()}
    assert set(tools) == {"image_search", "page_images"}
    for name, default in (("image_search", 6), ("page_images", 12)):
        assert tools[name]["parameters"]["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 20, "default": default}
        assert tools[name]["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("quote", ["'", '"'])
def test_bing_metadata_quotes_escaped_urls_and_stable_dedupe(quote):
    """HTMLParser handles both quoting forms and does not synthesize URLs."""
    first = {"murl": "https://cdn.example.com/a.jpg?a=1&b=2", "turl": "https://cdn.example.com/t.jpg", "purl": "https://example.com/post", "t": "酒狐 & picture", "desc": "caption", "w": 720, "h": "1080"}
    second = {"murl": "https://cdn.example.com/b.jpg", "t": "second"}
    fetch = Mock(return_value=response(bing([first, first, second], quote)))
    result = module.search("酒狐", 2, fetch)
    assert result["ok"] and result["status"] == "candidates"
    assert [item["image_url"] for item in result["images"]] == [first["murl"], second["murl"]]
    assert result["images"][0]["title"] == "酒狐 & picture"
    assert result["images"][0]["width"] == 720
    assert result["images"][0]["height"] == 1080
    assert result["images"][0]["source_page"] == first["purl"]
    assert result["images"][0]["thumbnail_url"] == first["turl"]
    assert result["images"][0]["verified_image_bytes"] is False
    assert fetch.call_count == 1
    assert result["attempts"][0]["candidate_count"] == 3


def test_baidu_fallback_query_unchanged_and_literal_middle_url():
    """Provider failover keeps the supplied query and uses literal Baidu metadata."""
    calls = []

    def fetch(url):
        """Return empty Bing HTML before the native Baidu fixture."""
        calls.append(url)
        if "baidu" not in url:
            return response("<html></html>")
        return response(json.dumps({"data": [{"middleURL": "https://img.example.com/native.jpg", "thumbURL": "https://img.example.com/thumb.jpg", "objURL": "ippr_z2C$qAzdH3FAzdH3Fopaque", "replaceUrl": [{"FromURL": "https://example.com/origin"}], "width": "600", "height": 800, "fromPageTitleEnc": "native title"}, {}]}), content_type="application/json")

    result = module.search(" 酒狐 screenshot ", 6, fetch)
    assert result["ok"] and result["images"][0]["image_url"] == "https://img.example.com/native.jpg"
    assert result["images"][0]["source_page"] == "https://example.com/origin"
    assert result["images"][0]["width"] == 600
    assert len(calls) == 3
    for url in calls:
        args = parse_qs(urlsplit(url).query)
        assert (args.get("q") or args.get("word")) == [" 酒狐 screenshot "]
    assert all(item["status"] == "parsed" for item in result["attempts"])


def test_timeouts_differ_from_successful_zero_results():
    """All transport failures must not masquerade as a successful empty search."""
    failed = module.search("anything", fetch=Mock(side_effect=TimeoutError()))
    assert failed["ok"] is False
    assert failed["error"] == "search_sources_unavailable"
    assert all(item["status"] == "transport_error" for item in failed["attempts"])
    empty = module.search("anything", fetch=Mock(return_value=response("<html>no candidates</html>")))
    assert empty["ok"] is True and empty["status"] == "no_usable_candidates"
    assert empty["images"] == []


def test_parse_failure_and_opaque_urls_are_not_candidates():
    """Malformed JSON and obfuscated object URLs cannot create image results."""
    assert module._baidu('{"data":[{"objURL":"opaque-id"}]}') == []
    with pytest.raises(ValueError):
        module._baidu('{"errno":500,"data":[]}')
    with pytest.raises(ValueError):
        module._baidu('{"rcode":403,"data":[]}')
    fetch = Mock(side_effect=[response("<a m='{bad'></a>"), response("<html></html>"), response("not json", content_type="application/json")])
    result = module.search("q", fetch=fetch)
    assert result["status"] == "no_usable_candidates"
    assert result["attempts"][-1]["status"] == "parse_error"


def test_page_declarations_resolve_final_url_without_image_fetch():
    """Page discovery reads metadata, lazy images and srcset but never images."""
    body = '''<meta property="og:image" content="/hero.jpg">
<link rel="image_src" href="//cdn.example.com/card.jpg">
<img src="/hero.jpg" alt="duplicate"><img data-src="lazy.jpg" alt="fox" width="128" height="256">
<img srcset="/small.jpg 1x, https://cdn.example.com/large.jpg 2x">
<img src="javascript:alert(1)"><img src="file:///etc/passwd"><img src="http://localhost/private">
<script>fetch('https://cdn.example.com/not-declared.jpg')</script><img src="/truncated.jpg"'''
    fetch = Mock(return_value=response(body, "https://example.com/posts/final"))
    result = module.extract_page("https://example.com/start", fetch=fetch)
    assert result["ok"]
    assert [item["image_url"] for item in result["images"]] == ["https://example.com/hero.jpg", "https://cdn.example.com/card.jpg", "https://example.com/posts/lazy.jpg", "https://example.com/small.jpg", "https://cdn.example.com/large.jpg"]
    assert result["images"][2]["title"] == "fox"
    assert result["images"][2]["height"] == 256
    assert fetch.call_count == 1
    assert result["verified_image_bytes"] is False
    assert result["untrusted_content"] is True


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost/", "http://localhost./", "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "https://u:p@example.com/", "https://@example.com/", "https://example.com:8080/", "https://example.com\\@127.0.0.1/"])
def test_public_guard_rejects_unsafe_syntax_and_literals(url):
    """Unsafe destinations are rejected before any opener can issue a request."""
    with pytest.raises(ValueError):
        module.validate_public(url)


def test_dns_private_and_mixed_answers_rejected(monkeypatch):
    """A public hostname is unsafe if any resolved address is not global."""
    monkeypatch.setattr(module.socket, "getaddrinfo", Mock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.0.0.1", 443))]))
    with pytest.raises(ValueError, match="non_public_address"):
        module.validate_public("https://example.com/")


def test_redirect_public_validation_before_following(monkeypatch):
    """An attacker-controlled redirect cannot reach a private DNS destination."""
    monkeypatch.setattr(module.socket, "getaddrinfo", Mock(return_value=[(2, 1, 6, "", ("192.168.1.1", 80))]))
    request = module.urllib.request.Request("https://example.com/")
    with pytest.raises(ValueError, match="non_public_address"):
        module.PublicRedirect().redirect_request(request, None, 302, "Found", {}, "http://private.example/")


def test_transport_timeout_bound_and_response_limit(monkeypatch):
    """The shared transport enforces its byte bound and validates final URLs."""
    monkeypatch.setattr(module.socket, "getaddrinfo", Mock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]))
    raw = Mock()
    raw.geturl.return_value = "https://example.com/final"
    raw.headers.get_content_type.return_value = "text/html"
    raw.headers.get_content_charset.return_value = "utf-8"
    raw.read.return_value = b"<html></html>"
    opened = Mock()
    opened.__enter__ = Mock(return_value=raw)
    opened.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = opened
    build = Mock(return_value=opener)
    monkeypatch.setattr(module.urllib.request, "build_opener", build)
    result = module.get_public("https://example.com/")
    assert result["final_url"] == "https://example.com/final"
    assert opener.open.call_args.kwargs["timeout"] == 15
    assert raw.read.call_args.args == (2 * 1024 * 1024 + 1,)
    raw.read.return_value = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="response_too_large"):
        module.get_public("https://example.com/")


def test_bing_json_escaped_slashes_and_no_id_synthesis():
    """JSON escaped URL slashes decode normally but IDs never become URLs."""
    body = '<a m=\'{"murl":"https:\\/\\/cdn.example.com\\/fox.jpg"}\'></a><a m=\'{"mid":"ABC123"}\'></a>'
    result = module.search("q", 1, Mock(return_value=response(body)))
    assert [item["image_url"] for item in result["images"]] == ["https://cdn.example.com/fox.jpg"]


def test_page_transport_failure_and_wrong_content_type():
    """A failed HTML read must not look like a successful empty extraction."""
    assert module.extract_page("https://example.com", fetch=Mock(side_effect=TimeoutError()))["ok"] is False
    assert module.extract_page("https://example.com", fetch=Mock(return_value=response("binary", content_type="image/jpeg")))["ok"] is False


def test_default_page_limit_and_candidate_urls_do_not_resolve(monkeypatch):
    """Extraction caps metadata only and does not resolve or fetch image hosts."""
    dns = Mock(side_effect=AssertionError("candidate DNS must not run"))
    monkeypatch.setattr(module.socket, "getaddrinfo", dns)
    body = "".join('<img src="https://cdn.example.com/%s.jpg">' % index for index in range(25))
    result = module.extract_page("https://example.com", fetch=Mock(return_value=response(body)))
    assert len(result["images"]) == 12
    dns.assert_not_called()


@pytest.mark.parametrize("limit", [0, 21, True, 1.5, "6"])
def test_invalid_limits_rejected_without_network(limit):
    """The callable API uses the same limits as its model schema."""
    fetch = Mock()
    with pytest.raises(ValueError):
        module.search("q", limit, fetch)
    with pytest.raises(ValueError):
        module.extract_page("https://example.com/", limit, fetch)
    fetch.assert_not_called()
