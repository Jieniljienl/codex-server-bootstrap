"""Explicit proxy transport and bounded, public-only subscription downloads."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urljoin, urlsplit


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def response_bytes(response, limit):
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ServiceError("返回内容超过大小限制。")
    return data


def local_json(port, path, token=None, method="GET", payload=None, timeout=15):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response_bytes(response, 4 * 1024 * 1024)
        if response.status >= 400:
            messages = {
                400: "模型拒绝了当前请求（HTTP 400）。请检查模型、推理强度和快速模式是否受支持。",
                401: "本地模型接口认证失败，请检查本项目的配置。",
                403: "模型请求被拒绝，请检查账号权限和当前节点。",
                422: "模型无法使用当前参数，请检查推理强度和快速模式是否受支持。",
                429: "模型服务额度或请求频率受限，请稍后重试。",
            }
            raise ServiceError(messages.get(response.status, f"本地服务返回错误（HTTP {response.status}），请检查登录、节点和模型。"), 502)
        return json.loads(raw) if raw.strip() else {}
    except ServiceError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        raise ServiceError("无法连接本地服务，请检查本项目的服务状态。", 503) from None
    finally:
        connection.close()


def proxy_egress(proxy_port):
    # HTTPSConnection performs CONNECT to the destination, then verifies its TLS.
    # No environment proxy lookup, bypass list or direct fallback is involved.
    connection = http.client.HTTPSConnection("127.0.0.1", proxy_port, timeout=20, context=ssl.create_default_context())
    connection.set_tunnel("api.ipify.org", 443)
    try:
        connection.request("GET", "/?format=json", headers={"Accept": "application/json"})
        response = connection.getresponse()
        raw = response_bytes(response, 4096)
        if response.status != 200:
            raise ValueError()
        result = json.loads(raw)
        ipaddress.ip_address(result["ip"])
        return result["ip"]
    except (OSError, http.client.HTTPException, ValueError, KeyError):
        raise ServiceError("代理出口检测失败，请先选择可用节点。不会改用直连。", 502) from None
    finally:
        connection.close()


def public_addresses(hostname, port):
    try:
        addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)))
    except OSError:
        raise ServiceError("订阅域名解析失败。") from None
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ServiceError("订阅地址必须指向公网，不能访问本机或内网。")
    return addresses


def download_subscription(url, max_bytes=2 * 1024 * 1024):
    if not isinstance(url, str) or len(url) > 8192:
        raise ServiceError("订阅地址格式不正确。")
    current = url.strip()
    for _ in range(4):
        try:
            parts = urlsplit(current)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ValueError()
            port = parts.port or 443
            if not 1 <= port <= 65535:
                raise ValueError()
            host = parts.hostname.encode("idna").decode()
        except (ValueError, UnicodeError):
            raise ServiceError("请输入有效的 HTTPS 订阅地址；也可以直接粘贴订阅内容。") from None
        addresses = public_addresses(host, port)
        connection = http.client.HTTPSConnection(host, port, timeout=25, context=ssl.create_default_context())
        # Pin the validated address for this connection, retaining TLS hostname checks.
        connection._create_connection = lambda address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None: socket.create_connection((addresses[0], port), timeout, source_address)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            connection.request("GET", path, headers={"User-Agent": "clash.meta/ai-proxy-hub", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ServiceError("订阅跳转地址无效。")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise ServiceError(f"订阅下载失败（HTTP {response.status}），请检查地址有效期。")
            return response_bytes(response, max_bytes).decode("utf-8-sig")
        except ServiceError:
            raise
        except (OSError, UnicodeError, http.client.HTTPException):
            raise ServiceError("订阅下载失败；可在电脑下载后粘贴内容导入。") from None
        finally:
            connection.close()
    raise ServiceError("订阅跳转次数过多。")
