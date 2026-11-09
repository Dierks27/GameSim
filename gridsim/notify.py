"""Notifications: ntfy.sh push (free, no account; topic name in .env), optional Discord webhook and Windows toast.
Secrets live in a local .env (KEY=VALUE lines) or the environment, never in the repo."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

from .config import REPO_ROOT, data_dir


def load_env() -> dict:
    env = {}
    p = None
    for root in (data_dir(), REPO_ROOT):          # your data folder first; the code folder for older installs
        for name in (".env", ".env.txt"):          # Notepad / Explorer often add ".txt"
            if p is None and (root / name).is_file():
                p = root / name
    if p is not None:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return {**env, **{k: v for k, v in os.environ.items() if k in env or k.isupper()}}


def send(cfg, title: str, message: str, url: str | None = None, priority: str = "default") -> list[str]:
    nc = cfg.notify
    env = load_env()
    sent = []
    topic = env.get(nc["ntfy_topic_env"])
    if topic:
        headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Tags": "football"}
        if url:
            headers["Click"] = url
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            sent.append("ntfy")
        except Exception as e:  # pragma: no cover - network
            print(f"  ntfy failed: {e}")
    hook = env.get(nc["discord_webhook_env"])
    if hook:
        try:
            body = json.dumps({"content": f"**{title}**\n{message}" + (f"\n{url}" if url else "")}).encode()
            req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            sent.append("discord")
        except Exception as e:  # pragma: no cover - network
            print(f"  discord failed: {e}")
    if nc.get("windows_toast") and sys.platform == "win32":
        ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
              "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
              f"$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode({json.dumps(title)})) > $null;"
              f"$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode({json.dumps(message[:200])})) > $null;"
              "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('GridSim').Show("
              "[Windows.UI.Notifications.ToastNotification]::new($t))")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=10, capture_output=True)
            if r.returncode == 0:
                sent.append("toast")
        except Exception:
            pass
    return sent
