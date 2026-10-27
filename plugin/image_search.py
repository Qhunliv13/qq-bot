"""Native image metadata discovery; never downloads or sends candidate images.

Search/page content is untrusted, and declared URLs are not proof of matching
content or valid image bytes. All actual HTTP requests share the public guard.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import ipaddress
import json
import socket
from urllib.parse import urlencode, urljoin, urlsplit
import urllib.request

# Transport bounds apply to HTTP only, not model reasoning or tool execution.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT = 15


def schema() -> list[dict]:
    """Describe generic discovery tools without promising semantic verification."""
    definitions = (
        ("image_search", "Search native image indexes. Returns untrusted candidate URLs, not verified image bytes; does not download or send images.", "query", 6),
        ("page_images", "Extract declared image URLs from a public HTML page without downloading images or executing JavaScript.", "url", 12),
    )
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": {"type": "object", "properties": {
                 argument: {"type": "string", "minLength": 1},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": default}},
                 "required": [argument], "additionalProperties": False}}}
            for name, description, argument, default in definitions]


def _http_url(value: str, base: str = "") -> str:
    """Check declared URL syntax without DNS or fetching CDN image candidates."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("public_http_url_required")
    target = urljoin(base, html.unescape(value.strip()))
    if any(ord(char) < 32 for char in target) or "\\" in target:
        raise ValueError("public_http_url_required")
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("public_http_url_required")
    if parsed.port not in (None, 80, 443):
        raise ValueError("public_web_port_required")
    # Literal private addresses and localhost are rejectable without DNS traffic.
    if parsed.hostname.lower().rstrip(".") == "localhost" or parsed.hostname.lower().rstrip(".").endswith(".localhost"):
        raise ValueError("non_public_address")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("non_public_address")
    return target


def validate_public(url: str) -> str:
    """Reject credentials, private literal/DNS targets and mixed DNS answers."""
    target = _http_url(url)
    parsed = urlsplit(target)
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("non_public_address")
    return target


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    """Apply the same public guard before every redirect hop is followed."""

    def redirect_request(self, request, response, code, message, headers, target):
        """Validate redirected destinations before urllib constructs the next request."""
        validate_public(target)
        return super().redirect_request(request, response, code, message, headers, target)


def get_public(url: str, timeout: float = HTTP_TIMEOUT) -> dict:
    """Fetch bounded public HTTP metadata with redirects checked and proxies disabled."""
    target = validate_public(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect())
    request = urllib.request.Request(target, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ImageMetadataDiscovery/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
        "Accept-Encoding": "identity", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with opener.open(request, timeout=timeout) as response:
        final_url = validate_public(response.geturl())
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
        content_type = response.headers.get_content_type()
        text = raw.decode(response.headers.get_content_charset() or "utf-8", "replace")
    return {"body": text, "final_url": final_url, "content_type": content_type}


def _limit(value: int) -> int:
    """Require the explicit public contract rather than silently coercing input."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        raise ValueError("limit_must_be_integer_1_to_20")
    return value


def _response(fetch, url: str) -> tuple[str, str, str]:
    """Normalize the synchronous injectable transport without fetching candidates."""
    response = (fetch or get_public)(url)
    if not isinstance(response, dict):
        raise ValueError("fetch_must_return_dict")
    body = response.get("body", response.get("text", ""))
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if not isinstance(body, str):
        raise ValueError("fetch_body_must_be_text")
    if len(body.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    return body, _http_url(response.get("final_url") or url), str(response.get("content_type", "")).split(";", 1)[0].lower()


def _candidate(image, thumbnail="", source="", title="", description="", width=None, height=None, base="") -> dict | None:
    """Keep literal, syntactically safe declared URLs; never invent image URLs."""
    try:
        image_url = _http_url(image, base)
    except ValueError:
        return None
    result = {"image_url": image_url, "thumbnail_url": "", "source_page": "",
              "title": html.unescape(str(title or "")), "description": html.unescape(str(description or "")),
              "verified_image_bytes": False}
    for key, value in (("thumbnail_url", thumbnail), ("source_page", source)):
        if value:
            try:
                result[key] = _http_url(value, base)
            except ValueError:
                pass  # Optional invalid metadata must not discard a usable original URL.
    for key, value in (("width", width), ("height", height)):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            result[key] = value
        elif isinstance(value, str) and value.isdigit() and int(value) > 0:
            result[key] = int(value)
    return result


class _ImageHTML(HTMLParser):
    """Read Bing metadata or standard page declarations without running scripts."""

    def __init__(self, base: str, bing: bool = False):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.bing = bing
        self.images = []

    def handle_starttag(self, tag, attrs):
        """Read attributes robustly regardless of quote style or HTML entities."""
        data = dict(attrs)
        if self.bing:
            if data.get("m"):
                try:
                    record = json.loads(data["m"])
                except (ValueError, TypeError):
                    return
                if isinstance(record, dict):
                    candidate = _candidate(record.get("murl"), record.get("turl"), record.get("purl"), record.get("t"), record.get("desc"), record.get("w"), record.get("h"))
                    if candidate:
                        self.images.append(candidate)
            return
        urls = []
        if tag == "meta" and (data.get("property") or data.get("name") or "").lower() in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"}:
            urls.append(data.get("content"))
        elif tag == "link" and "image_src" in (data.get("rel") or "").lower().split():
            urls.append(data.get("href"))
        elif tag in {"img", "source"}:
            urls.extend([data.get("src"), data.get("data-src")])
            # Each srcset item starts with the declared URL; descriptors are not URLs.
            for entry in (data.get("srcset") or "").split(","):
                if entry.strip():
                    urls.append(entry.strip().split()[0])
        for image in urls:
            candidate = _candidate(image, source=self.base, title=data.get("alt", ""), width=data.get("width"), height=data.get("height"), base=self.base)
            if candidate:
                self.images.append(candidate)


def _unique(images: list[dict], limit: int) -> list[dict]:
    """Deduplicate original URLs stably, preserving native provider/page ordering."""
    seen = set()
    result = []
    for image in images:
        if image["image_url"] not in seen:
            seen.add(image["image_url"])
            result.append(image)
            if len(result) == limit:
                break
    return result


def _baidu(body: str) -> list[dict]:
    """Read literal Baidu URLs, never decode opaque/obfuscated object identifiers."""
    parsed = json.loads(body)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
        raise ValueError("invalid_baidu_response")
    # Provider failure payloads are not evidence of an empty successful search.
    if any(parsed.get(key, 0) not in (0, "0", None) for key in ("errNo", "errno", "rcode")):
        raise ValueError("baidu_provider_error")
    images = []
    for record in parsed["data"]:
        if not isinstance(record, dict):
            continue
        source = record.get("fromURL") or record.get("fromUrl") or ""
        replacements = record.get("replaceUrl")
        if isinstance(replacements, list) and replacements and isinstance(replacements[0], dict):
            source = replacements[0].get("FromURL") or source
        candidate = _candidate(record.get("middleURL") or record.get("thumbURL"), record.get("thumbURL"), source,
                               record.get("fromPageTitleEnc") or record.get("fromPageTitle"), record.get("desc"), record.get("width"), record.get("height"))
        if candidate:
            images.append(candidate)
    return images


def search(query: str, limit: int = 6, fetch=None) -> dict:
    """Search native indexes with unchanged query and explicit per-source outcomes."""
    limit = _limit(limit)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query_must_be_nonempty_string")
    providers = [("bing", "https://www.bing.com/images/search?" + urlencode({"q": query, "form": "HDRSC2", "safesearch": "Strict"})),
                 ("bing_cn", "https://cn.bing.com/images/search?" + urlencode({"q": query, "form": "HDRSC2", "safesearch": "Strict"})),
                 ("baidu", "https://image.baidu.com/search/acjson?" + urlencode({"tn": "resultjson_com", "word": query, "rn": limit, "ipn": "rj"}))]
    attempts, images = [], []
    for provider, url in providers:
        try:
            body, final_url, content_type = _response(fetch, url)
        except Exception as exc:
            attempts.append({"provider": provider, "url": url, "status": "transport_error", "candidate_count": 0, "error": type(exc).__name__})
            continue
        try:
            if provider == "baidu":
                candidates = _baidu(body)
            else:
                if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ValueError("unsupported_content_type")
                parser = _ImageHTML(final_url, bing=True)
                parser.feed(body)
                candidates = parser.images
            attempts.append({"provider": provider, "url": url, "status": "parsed", "candidate_count": len(candidates)})
            images = _unique(images + candidates, limit)
            if len(images) >= limit:
                break
        except (ValueError, TypeError) as exc:
            attempts.append({"provider": provider, "url": url, "status": "parse_error", "candidate_count": 0, "error": type(exc).__name__})
    parsed = any(attempt["status"] == "parsed" for attempt in attempts)
    result = {"ok": parsed, "query": query, "status": "candidates" if images else ("no_usable_candidates" if parsed else "search_sources_unavailable"),
              "images": images, "attempts": attempts, "verified_image_bytes": False, "untrusted_content": True}
    if not parsed:
        result["error"] = "search_sources_unavailable"
    return result


def extract_page(url: str, limit: int = 12, fetch=None) -> dict:
    """Extract public-page image declarations without fetching any image bytes."""
    limit = _limit(limit)
    url = _http_url(url)
    try:
        body, final_url, content_type = _response(fetch, url)
        if content_type and content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("unsupported_content_type")
        parser = _ImageHTML(final_url)
        parser.feed(body)
        images = _unique(parser.images, limit)
        return {"ok": True, "url": final_url, "status": "candidates" if images else "no_usable_candidates", "images": images,
                "verified_image_bytes": False, "untrusted_content": True}
    except Exception as exc:
        return {"ok": False, "url": url, "status": "page_unavailable", "error": type(exc).__name__, "images": [],
                "verified_image_bytes": False, "untrusted_content": True}
