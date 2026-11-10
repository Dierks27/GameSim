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
        # published as JSON (UTF-8): a header can only carry ASCII, which turned "down 7¢" into "down 7"
        body = {"topic": topic, "title": title, "message": message, "tags": ["football"],
                "priority": {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}.get(priority, 3)}
        if url:
            body["click"] = url
        try:
            req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
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
        r = _toast(title, message)
        if r is not None and r.returncode == 0:
            sent.append("toast")
    return sent


TOAST_PS = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
            "$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode($env:GRIDSIM_TOAST_TITLE)) > $null;"
            "$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode($env:GRIDSIM_TOAST_TEXT)) > $null;"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('GridSim').Show("
            "[Windows.UI.Notifications.ToastNotification]::new($t))")


def toast_text(message: str, limit: int = 240) -> str:
    """A toast shows a few lines: cut at a word, and say so."""
    if len(message) <= limit:
        return message
    return message[:limit].rsplit(" ", 1)[0] + " … (open GridSim for the rest)"


def _toast(title: str, message: str):
    """Windows toast. The text travels in environment variables, never inside the PowerShell script: written into
    the script, "¢" and "→" came out as \\uXXXX codes and "$2.25" lost its digits (PowerShell read $2 as a variable)."""
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_PS], timeout=15,
                              capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                              env={**os.environ, "GRIDSIM_TOAST_TITLE": title, "GRIDSIM_TOAST_TEXT": toast_text(message)})
    except Exception:
        return None
