#!/usr/bin/env python3
import json
import sys
import urllib.request
from pathlib import Path


def read_bot_token(env_path: Path) -> str:
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"BOT_TOKEN not found in {env_path}")


def main():
    env_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/circle-bot/env")
    bot_token = read_bot_token(env_path)
    with urllib.request.urlopen(
        f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
    ) as resp:
        payload = json.load(resp)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
