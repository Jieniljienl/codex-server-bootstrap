from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import secrets

from werkzeug.security import generate_password_hash

from . import settings


def main():
    parser = argparse.ArgumentParser(description="Independent AI proxy hub")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "password"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", required=True, type=Path)
        if name == "init":
            command.add_argument("--bind", default="0.0.0.0")
            command.add_argument("--port", default=8090, type=int)
            command.add_argument("--proxy-port", default=17890, type=int)
            command.add_argument("--controller-port", default=19090, type=int)
            command.add_argument("--api-port", default=18317, type=int)
    args = parser.parse_args()
    if args.command == "init":
        if (args.data_dir / "runtime.json").exists():
            print("Existing project settings preserved.")
            return
        password = getpass.getpass("设置网页访问密码（至少12字符）: ")
        if password != getpass.getpass("再次输入密码: "):
            raise SystemExit("两次密码不一致，未初始化。")
        try:
            settings.initialize(args.data_dir, password, args.bind, args.port, args.proxy_port, args.controller_port, args.api_port)
        except ValueError as error:
            raise SystemExit(str(error))
        print("Initialized. Secrets are stored only in the private data directory.")
    elif args.command == "password":
        password = getpass.getpass("新的网页访问密码（至少12字符）: ")
        if not 12 <= len(password) <= 256 or password != getpass.getpass("再次输入密码: "):
            raise SystemExit("密码无效或两次输入不一致。")
        with settings.LOCK:
            config = settings.load(args.data_dir)
            if not config:
                raise SystemExit("Project has not been initialized.")
            config.update(password_hash=generate_password_hash(password), auth_generation=secrets.token_hex(16))
            settings.write_json(args.data_dir / "runtime.json", config)
        print("Password changed. Existing browser sessions are invalidated.")
    elif args.command == "serve":
        from waitress import serve
        from .app import create_app
        config = settings.load(args.data_dir)
        serve(create_app(args.data_dir), host=config["web_bind"], port=config["web_port"], threads=6,
              max_request_body_size=3 * 1024 * 1024, clear_untrusted_proxy_headers=True)


if __name__ == "__main__":
    main()
