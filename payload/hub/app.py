from __future__ import annotations

from collections import defaultdict, deque
import logging
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from . import settings
from .network import ServiceError
from .services import Services


def create_app(data_dir, services=None):
    data_dir = Path(data_dir).resolve()
    configuration = settings.load(data_dir)
    if not configuration:
        raise RuntimeError("请先初始化本项目。")
    app = Flask(__name__)
    app.config.update(SECRET_KEY=configuration["session_secret"], MAX_CONTENT_LENGTH=3 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=8 * 3600)
    app.logger.disabled = True
    logging.getLogger("werkzeug").disabled = True
    service = services or Services(data_dir)
    login_attempts = defaultdict(deque)
    rate_lock = threading.Lock()
    chat_slots = threading.BoundedSemaphore(2)

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def authenticated():
        return session.get("authenticated") is True and session.get("generation") == settings.load(data_dir)["auth_generation"]

    def valid_csrf(value):
        expected = session.get("csrf")
        return isinstance(value, str) and isinstance(expected, str) and secrets.compare_digest(value, expected)

    @app.before_request
    def security():
        if request.path.startswith("/static/") or request.path == "/healthz":
            return None
        if request.path != "/login" and not authenticated():
            if request.path.startswith("/api/"):
                return jsonify(error="请先登录。"), 401
            return redirect(url_for("login"))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.headers.get("X-CSRF-Token") if request.is_json else request.form.get("csrf_token")
            if not valid_csrf(token):
                return jsonify(error="页面验证已失效，请刷新页面后重试。"), 403

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.errorhandler(ServiceError)
    def service_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error="请求内容过大。" if error.code == 413 else "请求无法处理。"), error.code

    @app.errorhandler(Exception)
    def unknown_error(error):
        # Never return request content, upstream bodies, tokens or tracebacks.
        return jsonify(error="处理未完成，请检查服务状态后重试。"), 500

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ServiceError("请求格式不正确。")
        return data

    @app.get("/healthz")
    def health():
        return jsonify(ok=True, version=settings.VERSION)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        status = 200
        if request.method == "POST":
            identity = request.remote_addr or "unknown"
            now = time.monotonic()
            with rate_lock:
                if len(login_attempts) > 2048:
                    login_attempts.clear()
                attempts = login_attempts[identity]
                while attempts and now - attempts[0] > 600:
                    attempts.popleft()
                allowed = len(attempts) < 8
                if allowed:
                    attempts.append(now)
            if not allowed:
                error, status = "尝试次数较多，请10分钟后再试。", 429
            elif check_password_hash(settings.load(data_dir)["password_hash"], request.form.get("password", "")[:1024]):
                with rate_lock:
                    login_attempts.pop(identity, None)
                session.clear()
                session.update(authenticated=True, generation=settings.load(data_dir)["auth_generation"])
                session.permanent = True
                csrf()
                return redirect(url_for("index"))
            else:
                error = "访问密码不正确。"
                status = 401
        return render_template("login.html", csrf_token=csrf(), error=error, version=settings.VERSION), status

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=csrf(), version=settings.VERSION, api_port=settings.load(data_dir)["api_port"])

    @app.post("/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        return jsonify(service.status())

    @app.get("/api/nodes")
    def nodes():
        return jsonify(service.nodes())

    @app.post("/api/subscription")
    def subscription():
        data = body()
        return jsonify(service.import_subscription(data.get("url", ""), data.get("content", "")))

    @app.post("/api/nodes/select")
    def select():
        return jsonify(service.select(body().get("name")))

    @app.post("/api/proxy/check")
    def proxy_check():
        return jsonify(service.check_proxy())

    @app.get("/api/models")
    def models():
        return jsonify(service.models())

    @app.post("/api/chat")
    def chat():
        data = body()
        if not chat_slots.acquire(blocking=False):
            raise ServiceError("已有问答正在处理，请稍后再试。", 429)
        try:
            return jsonify(service.chat(data.get("model"), data.get("prompt"), data.get("reasoning_effort", ""), data.get("fast_mode", False)))
        finally:
            chat_slots.release()

    @app.get("/api/access")
    def access():
        config = settings.load(data_dir)
        return jsonify(base_url=f"http://127.0.0.1:{config['api_port']}/v1", api_key=config["api_key"])

    @app.post("/api/password")
    def password():
        data = body()
        current, new = data.get("current"), data.get("new")
        if not isinstance(current, str) or not isinstance(new, str) or not 12 <= len(new) <= 256:
            raise ServiceError("新密码需要12到256个字符。")
        with settings.LOCK:
            config = settings.load(data_dir)
            if not check_password_hash(config["password_hash"], current[:1024]):
                raise ServiceError("当前密码不正确。", 403)
            config.update(password_hash=generate_password_hash(new), auth_generation=secrets.token_hex(16))
            settings.write_json(data_dir / "runtime.json", config)
            session.clear()
        return jsonify(message="密码已更新，请重新登录。")

    return app
