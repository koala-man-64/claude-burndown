"""Installation helpers for Claude Code statusline, startup supervisor, and background service."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import TASK_NAME, WATCHDOG_TASK_NAME, claude_home, project_root
from .util import atomic_write_text, read_json

STATUSLINE_REFRESH_S = 60
SERVICE_TASK_NAME = "ClaudeBurndownService"


def launcher_path() -> Path:
    return project_root() / "claude-burndown.py"


def python_launcher() -> str:
    if os.name == "nt":
        return "py"
    return sys.executable


def statusline_command() -> str:
    return f'{python_launcher()} "{launcher_path()}" statusline'


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.name}.{stamp}.claude-burndown.bak"
    target.write_bytes(path.read_bytes())
    return target


def install_statusline(settings_path: Path | None = None, apply: bool = False, force: bool = False) -> str:
    settings_path = settings_path or claude_home() / "settings.json"
    data = read_json(settings_path, None)
    if data is None and settings_path.exists():
        return f"skip: {settings_path} is not valid JSON"
    data = data if isinstance(data, dict) else {}
    desired = {"type": "command", "command": statusline_command(), "refreshInterval": STATUSLINE_REFRESH_S}
    existing = data.get("statusLine")
    if existing == desired:
        return f"statusLine already installed in {settings_path}"
    if existing and "claude-burndown" not in json.dumps(existing) and not force:
        return f"skip: a different statusLine is configured in {settings_path} ({json.dumps(existing)}); use --force to replace it"
    if not apply:
        return f"would set statusLine in {settings_path}: {json.dumps(desired)}"
    backup = _backup(settings_path) if settings_path.exists() else None
    data["statusLine"] = desired
    atomic_write_text(settings_path, json.dumps(data, indent=2) + "\n")
    return f"statusLine set in {settings_path}" + (f" (backup: {backup})" if backup else "")


def uninstall_statusline(settings_path: Path | None = None, apply: bool = False) -> str:
    settings_path = settings_path or claude_home() / "settings.json"
    data = read_json(settings_path, None)
    if not isinstance(data, dict) or "statusLine" not in data:
        return "statusLine not installed"
    if "claude-burndown" not in json.dumps(data["statusLine"]):
        return "skip: statusLine belongs to something else"
    if not apply:
        return f"would remove statusLine from {settings_path}"
    backup = _backup(settings_path)
    del data["statusLine"]
    atomic_write_text(settings_path, json.dumps(data, indent=2) + "\n")
    return f"statusLine removed from {settings_path} (backup: {backup})"


def startup_shortcut() -> Path:
    roaming = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "ClaudeBurndownSupervisor.lnk"


def _install_startup(interpreter: Path, arguments: str) -> str:
    shortcut = startup_shortcut()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    supervisor_arguments = arguments + " --supervise"
    script = (
        "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; "
        f"$link=$shell.CreateShortcut({quote(shortcut)}); "
        f"$link.TargetPath={quote(interpreter)}; $link.Arguments={quote(supervisor_arguments)}; "
        f"$link.WorkingDirectory={quote(project_root())}; $link.WindowStyle=7; $link.Save(); "
        f"Start-Process -FilePath {quote(interpreter)} -ArgumentList {quote(supervisor_arguments)} -WindowStyle Hidden"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        return f"startup supervisor failed ({result.returncode}): {(result.stderr or result.stdout).strip()}"
    return f"installed and started self-healing per-user Startup supervisor: {shortcut} (enterprise non-admin mode)"


def install_service(apply: bool = False, home: str | None = None) -> str:
    if os.name != "nt":
        return "Start claude-burndown serve with your user service manager (systemd or launchd)."
    exe = Path(sys.executable)
    interpreter = exe.with_name("pythonw.exe")
    if not interpreter.exists():
        interpreter = exe
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    arguments = subprocess.list2cmdline(["-B", str(launcher_path()), *(["--home", home] if home else []), "serve"])
    script = (
        f"$a = New-ScheduledTaskAction -Execute {quote(interpreter)} -Argument {quote(arguments)}; "
        "$logon = New-ScheduledTaskTrigger -AtLogOn; "
        "$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 1); "
        "$triggers = @($logon, $watchdog); "
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
        f"Register-ScheduledTask -TaskName '{SERVICE_TASK_NAME}' -Action $a -Trigger $triggers -Settings $s -Force | Out-Null; "
        f"Start-ScheduledTask -TaskName '{SERVICE_TASK_NAME}'"
    )
    if not apply:
        return f"would install and start self-healing {SERVICE_TASK_NAME} at logon; command: {interpreter} {arguments}"
    command = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; " + script]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # Access denied in corporate enterprise environments -> fall back to per-user Startup supervisor
        if "0x80070005" in result.stderr or "Access is denied" in result.stderr or "Access is denied" in result.stdout:
            return _install_startup(interpreter, arguments)
        return f"service installation failed ({result.returncode}): {(result.stderr or result.stdout).strip()}"
    return f"installed and started self-healing {SERVICE_TASK_NAME}"


def uninstall(apply: bool = False) -> list[str]:
    out = []
    out.append(uninstall_statusline(apply=apply))
    if os.name == "nt":
        if apply:
            subprocess.run(["schtasks", "/Delete", "/TN", SERVICE_TASK_NAME, "/F"], capture_output=True)
            try:
                startup_shortcut().unlink()
                out.append("removed Startup supervisor shortcut")
            except (FileNotFoundError, OSError):
                pass
            out.append(f"removed {SERVICE_TASK_NAME}")
        else:
            out.append(f"would remove {SERVICE_TASK_NAME} and startup shortcut")
    return out
