from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from urllib.parse import quote

import yaml

from . import settings
from .network import ServiceError, download_subscription, local_json, proxy_egress


def normalize_subscription(content):
    if not isinstance(content, str) or not content.strip():
        raise ServiceError("订阅内容不能为空。")
    content = content.strip().lstrip("\ufeff")
    if len(content.encode()) > 2 * 1024 * 1024:
        raise ServiceError("订阅内容不能超过2MB。")
    if content.startswith(("<", "[Proxy]", "[General]")):
        raise ServiceError("该内容不是兼容订阅。请在服务商后台选择 Clash/Mihomo、URI 或 Base64 订阅。")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError:
        parsed = None
    if isinstance(parsed, dict):
        proxies = parsed.get("proxies")
        if not isinstance(proxies, list) or not proxies or len(proxies) > 2000:
            raise ServiceError("订阅没有有效的 proxies 节点列表；不支持仅引用其他订阅的配置。")
        names = set()
        for node in proxies:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not isinstance(node.get("type"), str):
                raise ServiceError("订阅包含格式不正确的节点。")
            name = node["name"]
            if not name or len(name) > 256 or name in settings.RESERVED or name in names:
                raise ServiceError("节点名称重复或使用了系统保留名称，请调整订阅。")
            if node["type"].lower() in ("direct", "reject", "reject-drop", "compatible", "dns"):
                raise ServiceError("订阅中包含直连或非代理节点，无法作为VPN出口导入。")
            names.add(name)
        # Never import listener, DNS, routing or controller fields from a subscription.
        return yaml.safe_dump({"proxies": proxies}, allow_unicode=True, sort_keys=False)
    if "://" not in content:
        try:
            compact = "".join(content.split())
            content = base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8-sig")
        except (ValueError, UnicodeError):
            raise ServiceError("无法识别订阅，请选择 Clash/Mihomo、节点链接或Base64格式。") from None
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    allowed = ("ss://", "ssr://", "vmess://", "vless://", "trojan://", "hysteria://", "hysteria2://", "hy2://", "tuic://", "socks://", "socks5://", "http://", "https://", "anytls://")
    if not lines or len(lines) > 2000 or any(not line.lower().startswith(allowed) for line in lines):
        raise ServiceError("订阅格式或节点协议不兼容，请使用服务商提供的 Clash/Mihomo 格式。")
    return "\n".join(lines) + "\n"


class Services:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.root = Path(__file__).resolve().parent.parent

    @property
    def config(self):
        return settings.load(self.data_dir)

    def controller(self, path, method="GET", payload=None):
        config = self.config
        return local_json(config["controller_port"], path, config["mihomo_secret"], method, payload)

    def nodes(self):
        group = self.controller("/proxies/AI")
        provider = self.controller("/providers/proxies/subscription")
        nodes = []
        for node in provider.get("proxies", []):
            name = node.get("name")
            if not isinstance(name, str) or name in settings.RESERVED or str(node.get("type", "")).lower() in ("direct", "reject", "compatible"):
                continue
            history = node.get("history") or []
            nodes.append({"name": name, "type": node.get("type", ""), "delay": history[-1].get("delay") if history else None})
        return {"nodes": nodes, "selected": group.get("now", "REJECT")}

    def status(self):
        config = self.config
        state = settings.read_json(self.data_dir / "state.json")
        proxy = {"ok": False, "node": state.get("selected", "REJECT"), "subscription_set": (self.data_dir / "subscription.json").exists(), "updated_at": state.get("updated_at")}
        try:
            group = self.controller("/proxies/AI")
            proxy.update(ok=True, node=group.get("now", "REJECT"))
        except ServiceError:
            proxy["error"] = "代理服务未就绪"
        api = {"ok": False}
        try:
            self.models()
            api["ok"] = True
        except ServiceError:
            api["error"] = "模型接口未就绪，请检查服务或账号登录"
        return {"version": settings.VERSION, "proxy": proxy, "api": api, "web_port": config["web_port"], "api_port": config["api_port"], "codex_installed": (self.root / "bin/codex").is_file()}

    def select(self, name):
        with settings.LOCK:
            available = {node["name"] for node in self.nodes()["nodes"]}
            if not isinstance(name, str) or name not in available:
                raise ServiceError("请选择当前订阅中的真实节点。")
            self.controller("/proxies/AI", "PUT", {"name": name})
            # Close existing proxy tunnels so new requests cannot retain the old exit.
            self.controller("/connections", "DELETE")
            state = settings.read_json(self.data_dir / "state.json")
            state["selected"] = name
            settings.write_json(self.data_dir / "state.json", state)
        return {"selected": name, "message": "节点已切换；正在进行的模型请求可能需要重试。"}

    def import_subscription(self, url="", content=""):
        with settings.LOCK:
            old_meta = settings.read_json(self.data_dir / "subscription.json")
            if content and url:
                raise ServiceError("订阅地址和文本请选择一种方式导入。")
            if not content:
                url = url.strip() if isinstance(url, str) else ""
                url = url or old_meta.get("url", "")
                if not url:
                    raise ServiceError("请输入订阅地址或粘贴订阅内容。")
                content = download_subscription(url)
            normalized = normalize_subscription(content)
            path = self.data_dir / "mihomo/subscription.yaml"
            previous = path.read_text(encoding="utf-8")
            previous_selection = settings.read_json(self.data_dir / "state.json").get("selected", "REJECT")
            settings.atomic_write(path, normalized)
            try:
                binary = self.root / "bin/mihomo"
                if not binary.is_file():
                    raise ServiceError("Mihomo 尚未安装，请先完成部署。", 503)
                result = subprocess.run([str(binary), "-t", "-d", str(path.parent), "-f", str(path.parent / "config.yaml")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25, check=False)
                if result.returncode:
                    raise ServiceError("Mihomo 无法读取该订阅，原有订阅已保留。请改用 Clash/Mihomo 格式。")
                self.controller("/configs?force=true", "PUT", {"path": str(path.parent / "config.yaml")})
                nodes = self.nodes()["nodes"]
                if not nodes:
                    raise ServiceError("订阅中没有可选代理节点，原有订阅已保留。")
                names = {node["name"] for node in nodes}
                selected = previous_selection if previous_selection in names else "REJECT"
                self.controller("/proxies/AI", "PUT", {"name": selected})
                self.controller("/connections", "DELETE")
            except (ServiceError, OSError, subprocess.TimeoutExpired) as error:
                settings.atomic_write(path, previous)
                try:
                    self.controller("/configs?force=true", "PUT", {"path": str(path.parent / "config.yaml")})
                    self.controller("/proxies/AI", "PUT", {"name": previous_selection})
                except ServiceError:
                    pass
                if isinstance(error, ServiceError):
                    raise
                raise ServiceError("订阅验证未完成，原有订阅已恢复。") from None
            now = datetime.now(timezone.utc).isoformat()
            settings.write_json(self.data_dir / "subscription.json", {"url": url if url else "", "source": "url" if url else "text"})
            settings.write_json(self.data_dir / "state.json", {"selected": selected, "updated_at": now})
            return {"count": len(nodes), "message": f"已导入 {len(nodes)} 个节点。" + ("请选择节点后再测试。" if selected == "REJECT" else "已保留当前节点。")}

    def check_proxy(self):
        selected = self.nodes()["selected"]
        if selected in settings.RESERVED:
            raise ServiceError("请先从订阅中选择一个代理节点。")
        return {"ip": proxy_egress(self.config["proxy_port"]), "node": selected}

    def models(self):
        config = self.config
        result = local_json(config["api_port"], "/v1/models", config["api_key"], timeout=20)
        models = [{"id": item["id"]} for item in result.get("data", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
        return {"models": models}

    def chat(self, model, prompt, reasoning_effort="", fast_mode=False):
        if not isinstance(model, str) or not model or len(model) > 256:
            raise ServiceError("请选择模型。")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
            raise ServiceError("问题不能为空，且不能超过12000个字符。")
        if not isinstance(reasoning_effort, str) or reasoning_effort not in ("", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            raise ServiceError("推理强度选项无效。")
        if not isinstance(fast_mode, bool):
            raise ServiceError("快速模式选项无效。")
        if self.nodes()["selected"] in settings.RESERVED:
            raise ServiceError("请先选择代理节点。")
        if model not in {item["id"] for item in self.models()["models"]}:
            raise ServiceError("模型不在当前可用列表中，请刷新模型列表。")
        config = self.config
        payload = {"model": model, "input": prompt.strip(), "stream": False, "store": False}
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}
        if fast_mode:
            payload["service_tier"] = "priority"
        result = local_json(config["api_port"], "/v1/responses", config["api_key"], "POST", payload, timeout=180)
        texts = []
        if isinstance(result.get("output_text"), str):
            texts.append(result["output_text"])
        else:
            for item in result.get("output", []):
                if not isinstance(item, dict):
                    continue
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") in ("output_text", "text") and isinstance(block.get("text"), str):
                        texts.append(block["text"])
        if not texts:
            if result.get("status") == "failed" or result.get("error"):
                raise ServiceError("模型调用失败，请检查节点、授权和所选模型。", 502)
            raise ServiceError("模型没有返回可显示的文字，请更换模型后重试。", 502)
        # Nothing from this request is persisted, cached or added to a later request.
        return {"answer": "\n".join(texts), "model": model, "effective_tier": result.get("service_tier"),
                "requested_reasoning": reasoning_effort or "default", "requested_fast": fast_mode}
