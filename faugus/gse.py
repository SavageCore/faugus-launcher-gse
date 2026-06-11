import configparser
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from faugus.path_manager import PathManager

_GSE_BINARY_DIR = Path(PathManager.user_data("faugus-launcher/gse_fork"))
_GSE_CONFIG_BASE = Path(PathManager.user_config("faugus-launcher/gse_fork"))
_GSE_VERSION_FILE = _GSE_BINARY_DIR / "version.txt"
_GSE_API_URL = "https://api.github.com/repos/alex47exe/gse_fork/releases/latest"

_GBE_BINARY_DIR = Path(PathManager.user_data("faugus-launcher/gbe_fork"))
_GBE_VERSION_FILE = _GBE_BINARY_DIR / "version.txt"
_GBE_API_URL = "https://api.github.com/repos/Detanup01/gbe_fork/releases/latest"

_DLL_NAMES = ["steam_api64.dll", "steam_api.dll"]
_ALL_BINARY_DIRS = (_GSE_BINARY_DIR, _GBE_BINARY_DIR)

_TOOLS_BINARY_DIR = Path(PathManager.user_data("faugus-launcher/gse_fork_tools"))
_TOOLS_VERSION_FILE = _TOOLS_BINARY_DIR / "version.txt"
_TOOLS_API_URL = "https://api.github.com/repos/alex47exe/gse_fork_tools/releases/latest"
# PyInstaller bundle lives in a subdirectory; _OUTPUT is written relative to the exe dir
_TOOLS_EXE_DIR = _TOOLS_BINARY_DIR / "generate_emu_config"
_TOOLS_EXE = _TOOLS_EXE_DIR / "generate_emu_config"

LANGUAGES = [
    "english",
    "german",
    "french",
    "spanish",
    "portuguese",
    "russian",
    "japanese",
    "korean",
    "schinese",
    "tchinese",
    "italian",
    "dutch",
    "polish",
    "turkish",
]


def _linux_username() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER", "Player")


_DEFAULTS = {
    "appid": "",
    "username": _linux_username(),
    "persona_name": _linux_username(),
    "language": "english",
    "steamid": "",
    "offline": False,
    "disable_networking": False,
    "unlock_all_dlc": True,
    "allow_unknown_stats": False,
    "allow_unknown_achievements": False,
    "auto_create_leaderboards": True,
    "overlay_experimental": False,
    "overlay_achievements": True,
    "overlay_achievement_progress": False,
    "overlay_friends": True,
    "fetch_achievements": True,
    "dll_path": "",
    "fork_variant": "regular",
    "gse_fork_variant": "gse_fork",
}

_WANTED_FILES = {"steam_api64.dll", "steam_api.dll"}
_LINUX_TOOL_FILES = {
    "generate_interfaces_x64": "generate_interfaces_x64",
    "generate_interfaces_x32": "generate_interfaces_x32",
    "generate_interfaces_x86": "generate_interfaces_x32",  # gbe_fork uses x86 naming
}
_GENERATE_INTERFACES_X64 = _GSE_BINARY_DIR / "generate_interfaces_x64"
_GENERATE_INTERFACES_X32 = _GSE_BINARY_DIR / "generate_interfaces_x32"


def _fork_binary_dir(fork: str) -> Path:
    return _GBE_BINARY_DIR if fork == "gbe_fork" else _GSE_BINARY_DIR


def _fork_api_url(fork: str) -> str:
    return _GBE_API_URL if fork == "gbe_fork" else _GSE_API_URL


def _fork_version_file(fork: str) -> Path:
    return _GBE_VERSION_FILE if fork == "gbe_fork" else _GSE_VERSION_FILE


def _update_overlay_ini(
    path: Path,
    experimental: bool,
    achievements: bool,
    achievement_progress: bool,
    friends: bool,
) -> None:
    """Read-modify-write configs.overlay.ini, preserving all existing keys."""
    ini = configparser.RawConfigParser()
    ini.optionxform = str  # preserve key case (Font_Size etc.)
    if path.exists():
        ini.read(str(path), encoding="utf-8")

    if ini.has_section("overlay::general"):
        section = "overlay::general"
    elif ini.has_section("overlay"):
        section = "overlay"
    else:
        section = "overlay::general"
        ini.add_section(section)

    ini.set(section, "enable_experimental_overlay", "1" if experimental else "0")
    ini.set(section, "disable_achievement_notification", "0" if achievements else "1")
    ini.set(
        section,
        "disable_achievement_progress_notification",
        "0" if achievement_progress else "1",
    )
    ini.set(section, "disable_friend_notification", "0" if friends else "1")
    for stale in ("show_achievement_notifications", "show_friend_notifications"):
        if ini.has_option(section, stale):
            ini.remove_option(section, stale)

    with open(path, "w", encoding="utf-8") as f:
        ini.write(f)


# ---------------------------------------------------------------------------
# Auto-update
# ---------------------------------------------------------------------------


def _get_latest_version(fork: str = "gse_fork") -> str | None:
    try:
        import requests

        r = requests.get(_fork_api_url(fork), timeout=10)
        if r.status_code == 200:
            return r.json().get("tag_name")
    except Exception:
        pass
    return None


def _get_installed_version(fork: str = "gse_fork") -> str | None:
    vfile = _fork_version_file(fork)
    if vfile.exists():
        return vfile.read_text(encoding="utf-8").strip()
    return None


def _download_release(version: str, fork: str = "gse_fork") -> None:
    import requests

    tag = f"[gse:{fork}]"
    binary_dir = _fork_binary_dir(fork)

    r = requests.get(_fork_api_url(fork), timeout=10)
    if r.status_code != 200:
        return

    assets = r.json().get("assets", [])
    asset_url = linux_url = None
    for asset in assets:
        name = asset.get("name", "").lower()
        if "win" in name and "release" in name and "debug" not in name:
            asset_url = asset["browser_download_url"]
        elif "linux" in name and "release" in name and "debug" not in name:
            linux_url = asset["browser_download_url"]
    if not asset_url:
        print(f"{tag} No Windows release asset found.", flush=True)
        return

    print(f"{tag} Downloading {fork} {version}…", flush=True)
    r = requests.get(asset_url, stream=True, timeout=60)
    if r.status_code != 200:
        return

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = os.path.join(tmp, "gse_release")
        with open(archive_path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)

        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir)

        try:
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(extract_dir)
            elif tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path) as tf:
                    tf.extractall(extract_dir)
            else:
                # Use 7z CLI - py7zr doesn't support the compression codec used here
                seven_z = (
                    subprocess.run(["which", "7z"], capture_output=True)
                    .stdout.strip()
                    .decode()
                )
                if not seven_z:
                    seven_z = (
                        subprocess.run(["which", "7za"], capture_output=True)
                        .stdout.strip()
                        .decode()
                    )
                if not seven_z:
                    print(
                        f"{tag} 7z not found - install p7zip to use Goldberg Emulator.",
                        flush=True,
                    )
                    return
                result = subprocess.run(
                    [seven_z, "x", archive_path, f"-o{extract_dir}", "-y"],
                    capture_output=True,
                )
                if result.returncode != 0:
                    print(
                        f"{tag} 7z extraction failed: {result.stderr.decode()}",
                        flush=True,
                    )
                    return
        except Exception as e:
            print(f"{tag} Failed to extract archive: {e}", flush=True)
            return

        binary_dir.mkdir(parents=True, exist_ok=True)
        found = set()
        for root, _, files in os.walk(extract_dir):
            parts = Path(root).parts
            variant = next((p for p in parts if p in ("regular", "experimental")), None)
            if variant is None:
                continue
            for fname in files:
                if fname in _WANTED_FILES:
                    src = Path(root) / fname
                    dst_dir = binary_dir / variant
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst_dir / fname))
                    found.add(f"{variant}/{fname}")

        if not found:
            print(f"{tag} No DLLs found in release archive.", flush=True)
            return

        _fork_version_file(fork).write_text(version + "\n", encoding="utf-8")
        print(f"{tag} {fork} {version} installed to {binary_dir}", flush=True)

    if linux_url:
        print(f"{tag} Downloading Linux tools from {fork} {version}…", flush=True)
        r2 = requests.get(linux_url, stream=True, timeout=60)
        if r2.status_code == 200:
            with tempfile.TemporaryDirectory() as tmp2:
                arc2 = os.path.join(tmp2, "gse_linux_release")
                with open(arc2, "wb") as f:
                    for chunk in r2.iter_content(65536):
                        f.write(chunk)
                ext2 = os.path.join(tmp2, "extracted")
                os.makedirs(ext2)
                try:
                    with tarfile.open(arc2) as tf:
                        tf.extractall(ext2)
                    binary_dir.mkdir(parents=True, exist_ok=True)
                    for root, _, files in os.walk(ext2):
                        for fname in files:
                            if fname in _LINUX_TOOL_FILES:
                                dst = binary_dir / _LINUX_TOOL_FILES[fname]
                                shutil.copy2(str(Path(root) / fname), str(dst))
                                dst.chmod(dst.stat().st_mode | 0o111)
                    print(f"{tag} Linux tools installed.", flush=True)
                except Exception as e:
                    print(f"{tag} Failed to extract Linux tools: {e}", flush=True)


def update_gse(fork: str = "gse_fork") -> None:
    latest = _get_latest_version(fork)
    if latest is None:
        return

    current = _get_installed_version(fork)
    binary_dir = _fork_binary_dir(fork)
    tools_missing = (
        not (binary_dir / "generate_interfaces_x64").exists()
        and not (binary_dir / "generate_interfaces_x32").exists()
    )
    variant_dirs_missing = (
        not (binary_dir / "regular").exists()
        and not (binary_dir / "experimental").exists()
    )
    if (
        latest != current
        or not is_installed(fork)
        or tools_missing
        or variant_dirs_missing
    ):
        _download_release(latest, fork)
    else:
        print(f"[gse:{fork}] {fork} is up to date ({current}).", flush=True)


# ---------------------------------------------------------------------------
# gse_fork_tools: auto-update + fetch achievements
# ---------------------------------------------------------------------------


def _get_tools_latest_version() -> str | None:
    try:
        import requests

        r = requests.get(_TOOLS_API_URL, timeout=10)
        if r.status_code == 200:
            return r.json().get("tag_name")
    except Exception:
        pass
    return None


def _get_tools_installed_version() -> str | None:
    if _TOOLS_VERSION_FILE.exists():
        return _TOOLS_VERSION_FILE.read_text(encoding="utf-8").strip()
    return None


def _download_tools_release(version: str) -> None:
    import requests

    r = requests.get(_TOOLS_API_URL, timeout=10)
    if r.status_code != 200:
        return

    assets = r.json().get("assets", [])
    asset_url = None
    for asset in assets:
        name = asset.get("name", "").lower()
        if "linux" in name and name.endswith(".tar.bz2"):
            asset_url = asset["browser_download_url"]
            break
    if not asset_url:
        print("[gse_tools] No Linux release asset found.", flush=True)
        return

    print(f"[gse_tools] Downloading gen_emu_cfg {version}…", flush=True)
    r = requests.get(asset_url, stream=True, timeout=120)
    if r.status_code != 200:
        return

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = os.path.join(tmp, "tools_release.tar.bz2")
        with open(archive_path, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)

        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir)
        try:
            with tarfile.open(archive_path, "r:bz2") as tf:
                tf.extractall(extract_dir)
        except Exception as e:
            print(f"[gse_tools] Failed to extract archive: {e}", flush=True)
            return

        # The archive extracts as generate_emu_config/<binary + _internal/ + data files>
        src_dir = Path(extract_dir) / "generate_emu_config"
        if not src_dir.is_dir():
            # Fallback: search one level deep
            for item in Path(extract_dir).iterdir():
                if item.is_dir() and (item / "generate_emu_config").exists():
                    src_dir = item
                    break
            else:
                print(
                    "[gse_tools] generate_emu_config directory not found in archive.",
                    flush=True,
                )
                return

        _TOOLS_BINARY_DIR.mkdir(parents=True, exist_ok=True)
        dst_dir = _TOOLS_BINARY_DIR / "generate_emu_config"
        if dst_dir.exists():
            shutil.rmtree(str(dst_dir))
        shutil.copytree(str(src_dir), str(dst_dir))
        exe = dst_dir / "generate_emu_config"
        if not exe.exists():
            print(
                "[gse_tools] generate_emu_config binary not found after extract.",
                flush=True,
            )
            return
        exe.chmod(exe.stat().st_mode | 0o111)

        _TOOLS_VERSION_FILE.write_text(version + "\n", encoding="utf-8")
        print(
            f"[gse_tools] gen_emu_cfg {version} installed to {_TOOLS_BINARY_DIR}",
            flush=True,
        )


def update_tools() -> None:
    latest = _get_tools_latest_version()
    if latest is None:
        return
    current = _get_tools_installed_version()
    if latest != current or not is_tools_installed():
        _download_tools_release(latest)
    else:
        print(f"[gse_tools] gen_emu_cfg is up to date ({current}).", flush=True)


def is_tools_installed() -> bool:
    return _TOOLS_EXE.exists()


def _saved_token_username() -> str | None:
    """Return the first username from refresh_tokens.json, or None if the file is absent/empty."""
    import json

    token_file = _TOOLS_EXE_DIR / "refresh_tokens.json"
    if not token_file.exists():
        return None
    try:
        tokens = json.loads(token_file.read_text(encoding="utf-8"))
        if isinstance(tokens, dict) and tokens:
            return next(iter(tokens))
    except Exception:
        pass
    return None


def _clear_saved_token(username: str | None = None) -> None:
    """Clear saved refresh token(s) from refresh_tokens.json."""
    import json

    token_file = _TOOLS_EXE_DIR / "refresh_tokens.json"
    if not token_file.exists():
        return
    if username is None:
        token_file.unlink(missing_ok=True)
        return
    try:
        tokens = json.loads(token_file.read_text(encoding="utf-8"))
        if isinstance(tokens, dict) and username in tokens:
            del tokens[username]
            if tokens:
                token_file.write_text(
                    json.dumps(tokens, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            else:
                token_file.unlink(missing_ok=True)
    except Exception:
        token_file.unlink(missing_ok=True)


def _read_global_config(key: str, default: str = "") -> str:
    config_path = Path(PathManager.user_config("faugus-launcher/config.ini"))
    if not config_path.exists():
        return default
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"')
    except Exception:
        pass
    return default


def _read_global_bool(key: str, default: bool = False) -> bool:
    return _read_global_config(key, "True" if default else "False").lower() == "true"


def _fetch_via_webapi(appid: str, api_key: str, settings_dst: Path) -> tuple[bool, str]:
    import json
    import urllib.error
    import urllib.request

    url = (
        "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
        f"?key={api_key}&appid={appid}"
    )
    _log(f"fetch: GET {url.split('?')[0]}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"Steam API error {e.code}: {e.reason}"
    except Exception as e:
        return False, f"Failed to reach Steam API: {e}"

    stats_section = data.get("game", {}).get("availableGameStats", {})
    achievements = stats_section.get("achievements", [])
    stats = stats_section.get("stats", [])

    settings_dst.mkdir(parents=True, exist_ok=True)

    if achievements:
        with open(settings_dst / "achievements.json", "w", encoding="utf-8") as f:
            json.dump(achievements, f, indent=2, ensure_ascii=False)
        _log(f"fetch: wrote {len(achievements)} achievements")

    if stats:
        stats_dict = {
            s["name"]: {
                "default": s.get("defaultvalue", 0),
                "type": s.get("type", "INT"),
            }
            for s in stats
        }
        with open(settings_dst / "stats.json", "w", encoding="utf-8") as f:
            json.dump(stats_dict, f, indent=2, ensure_ascii=False)
        _log(f"fetch: wrote {len(stats)} stats")

    ach_count = len(achievements)
    if ach_count == 0:
        return True, "Done - this game has no Steam achievements"
    return (
        True,
        f"Done - {ach_count} achievement{'s' if ach_count != 1 else ''} fetched",
    )


def fetch_steam_config(
    gameid: str,
    twofa_cb=None,
    auth_cb=None,
    _token_retry: bool = True,
) -> tuple[bool, str]:
    """Fetch achievement/stats config from Steam for gameid.

    Uses the Steam Web API when a key is configured in global settings.
    Falls back to gse_tools (saved token or anonymous) when no key is set.
    """
    cfg = read_config(gameid)
    appid = cfg.get("appid", "").strip()
    if not appid:
        return False, "No AppID set - enter one first"

    api_key = _read_global_config("steam-api-key")
    if api_key:
        _log("fetch: using Steam Web API")
        settings_dst = get_steam_settings_dir(gameid)
        _pre_fetch_cfg = read_config(gameid)
        ok, msg = _fetch_via_webapi(appid, api_key, settings_dst)
        if ok:
            overlay_path = settings_dst / "configs.overlay.ini"
            if overlay_path.exists():
                _update_overlay_ini(
                    overlay_path,
                    _pre_fetch_cfg.get("overlay_experimental", False),
                    _pre_fetch_cfg.get("overlay_achievements", True),
                    _pre_fetch_cfg.get("overlay_achievement_progress", False),
                    _pre_fetch_cfg.get("overlay_friends", True),
                )
        _log(f"fetch: {msg}")
        return ok, msg

    # --- gse_tools fallback ---
    if not is_tools_installed():
        return (
            False,
            "No Steam API key set and gen_emu_cfg not installed - add an API key in Settings or restart faugus",
        )

    remember_login_pref = _read_global_bool("gse-remember-login", True)
    username = _saved_token_username() if remember_login_pref else None
    remembered_login = False
    login_skip_app_confirmation = _read_global_bool(
        "gse-login-skip-app-confirmation", False
    )
    login_password = ""

    if username:
        remembered_login = True
        cmd = [str(_TOOLS_EXE), "-clr", "-skip_con", "-skip_inv", "-tok", appid]
    else:
        login_data = auth_cb() if auth_cb else None
        if auth_cb and login_data is None:
            return False, "Steam login cancelled"
        if login_data is None:
            cmd = [str(_TOOLS_EXE), "-anon", "-clr", "-skip_con", "-skip_inv", appid]
        else:
            username = str(login_data.get("username", "")).strip()
            login_password = str(login_data.get("password", ""))
            remembered_login = bool(login_data.get("remember_login", True))
            login_skip_app_confirmation = bool(
                login_data.get(
                    "login_skip_app_confirmation",
                    login_skip_app_confirmation,
                )
            )
            if username and username.lower() != "anonymous":
                cmd = [str(_TOOLS_EXE), "-clr", "-skip_con", "-skip_inv"]
                if remembered_login:
                    cmd.append("-tok")
                cmd.append(appid)
            else:
                username = None
                cmd = [
                    str(_TOOLS_EXE),
                    "-anon",
                    "-clr",
                    "-skip_con",
                    "-skip_inv",
                    appid,
                ]

    env = os.environ.copy()
    if username:
        env["GSE_CFG_USERNAME"] = username
    if login_password:
        env["GSE_CFG_PASSWORD"] = login_password
    env["PYTHONUNBUFFERED"] = "1"

    _log(
        f"fetch: using gse_tools (token={'yes' if remembered_login else 'no'}, "
        f"auth={'yes' if username else 'anonymous'})"
    )

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_TOOLS_EXE_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
        )
    except Exception as e:
        return False, str(e)

    _TWOFA_KW = (
        "2fa",
        "steam guard",
        "enter the steam",
        "emailed to you",
        "enter code",
    )
    _APP_CONFIRM_KW = (
        "confirm your login",
        "confirm login",
        "steam mobile app",
        "mobile app",
        "device confirmation",
    )
    _INVALID_TOKEN_KW = (
        "invalid refresh token",
        "refresh token invalid",
        "refresh token expired",
        "refresh token revoked",
        "revoked refresh token",
        "expired refresh token",
    )
    invalid_token_detected = False

    def _handle_twofa(prompt: str) -> bool:
        if twofa_cb:
            code = twofa_cb(prompt)
            if code is None:
                proc.kill()
                return False
            proc.stdin.write((code + "\n").encode())
            proc.stdin.flush()
            return True
        proc.kill()
        return False

    def _handle_app_confirmation(prompt: str) -> None:
        if not login_skip_app_confirmation:
            return
        low = prompt.lower()
        if any(kw in low for kw in _APP_CONFIRM_KW) and proc.stdin:
            proc.stdin.write(b"n\n")
            proc.stdin.flush()

    import select

    _IDLE_TIMEOUT = 60

    try:
        buf = b""
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], _IDLE_TIMEOUT)
            if not ready:
                _log(f"fetch: no output for {_IDLE_TIMEOUT}s - killing process")
                proc.kill()
                break
            ch = proc.stdout.read(1)
            if not ch:
                break
            buf += ch
            if ch == b"\n":
                line = buf.decode(errors="replace").rstrip()
                _log(f"fetch: {line}")
                low = line.lower()
                buf = b""
                if any(kw in low for kw in _TWOFA_KW):
                    if not _handle_twofa(line):
                        return False, "Steam 2FA cancelled or not supported"
                _handle_app_confirmation(line)
                if remembered_login and any(kw in low for kw in _INVALID_TOKEN_KW):
                    invalid_token_detected = True
            else:
                text = buf.decode(errors="replace")
                if len(text) >= 8 and any(kw in text.lower() for kw in _TWOFA_KW):
                    _log(f"fetch: {text.rstrip()}")
                    buf = b""
                    if not _handle_twofa(text.strip()):
                        return False, "Steam 2FA cancelled or not supported"
                _handle_app_confirmation(text)
                if remembered_login and any(
                    kw in text.lower() for kw in _INVALID_TOKEN_KW
                ):
                    invalid_token_detected = True
    except Exception as e:
        proc.kill()
        return False, str(e)

    if proc.poll() is None:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    if remembered_login and invalid_token_detected:
        _clear_saved_token(username)
        if _token_retry and auth_cb:
            _log("fetch: saved token invalid/revoked; cleared token and retrying login")
            return fetch_steam_config(
                gameid,
                twofa_cb=twofa_cb,
                auth_cb=auth_cb,
                _token_retry=False,
            )
        return (
            False,
            "Saved Steam token is invalid or revoked - token cleared, log in again",
        )

    out_root = _TOOLS_EXE_DIR / "_OUTPUT"
    settings_src = None
    if out_root.exists():
        for candidate in out_root.iterdir():
            if appid in candidate.name:
                ss = candidate / "steam_settings"
                if ss.exists():
                    settings_src = ss
                    break

    if settings_src is None:
        return False, f"No output found for AppID {appid} - check the AppID is correct"

    settings_dst = get_steam_settings_dir(gameid)
    settings_dst.mkdir(parents=True, exist_ok=True)

    _pre_fetch_cfg = read_config(gameid)
    _SKIP = {
        "configs.user.ini",
        "steam_appid.txt",
        "offline.txt",
        "disable_networking.txt",
    }

    copied = []
    for item in settings_src.iterdir():
        if item.name in _SKIP:
            continue
        dst = settings_dst / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(str(dst))
            shutil.copytree(str(item), str(dst))
        else:
            shutil.copy2(str(item), str(dst))
        copied.append(item.name)

    if not copied:
        return False, "No data files found in output - check the AppID is correct"

    overlay_path = settings_dst / "configs.overlay.ini"
    if overlay_path.exists():
        _update_overlay_ini(
            overlay_path,
            _pre_fetch_cfg.get("overlay_experimental", False),
            _pre_fetch_cfg.get("overlay_achievements", True),
            _pre_fetch_cfg.get("overlay_achievement_progress", False),
            _pre_fetch_cfg.get("overlay_friends", True),
        )

    ach_count = 0
    ach_file = settings_dst / "achievements.json"
    if ach_file.exists():
        try:
            import json

            data = json.loads(ach_file.read_text(encoding="utf-8"))
            ach_count = len(data) if isinstance(data, list) else 0
        except Exception:
            pass

    if ach_count == 0:
        msg = "Done - this game has no Steam achievements"
    else:
        msg = f"Done - {ach_count} achievement{'s' if ach_count != 1 else ''} fetched"
    _log(f"fetch: {msg}")
    return True, msg


# ---------------------------------------------------------------------------
# Install check
# ---------------------------------------------------------------------------


def is_installed(fork: str = "gse_fork") -> bool:
    d = _fork_binary_dir(fork)
    for variant in ("regular", "experimental"):
        if any((d / variant / dll).exists() for dll in _DLL_NAMES):
            return True
    return any((d / dll).exists() for dll in _DLL_NAMES)


def is_any_fork_installed() -> bool:
    return is_installed("gse_fork") or is_installed("gbe_fork")


# ---------------------------------------------------------------------------
# Per-game config dir helpers
# ---------------------------------------------------------------------------


def get_game_config_dir(gameid: str) -> Path:
    return _GSE_CONFIG_BASE / gameid


def get_steam_settings_dir(gameid: str) -> Path:
    return _GSE_CONFIG_BASE / gameid / "steam_settings"


# ---------------------------------------------------------------------------
# Config read / write
# ---------------------------------------------------------------------------


def read_config(gameid: str) -> dict:
    cfg = dict(_DEFAULTS)
    settings_dir = get_steam_settings_dir(gameid)

    appid_file = settings_dir / "steam_appid.txt"
    if appid_file.exists():
        cfg["appid"] = appid_file.read_text(encoding="utf-8").strip()

    user_ini = settings_dir / "configs.user.ini"
    if user_ini.exists():
        parser = configparser.ConfigParser()
        parser.read(str(user_ini), encoding="utf-8")
        if parser.has_section("user::general"):
            cfg["username"] = parser.get(
                "user::general", "account_name", fallback=cfg["username"]
            )
            cfg["persona_name"] = parser.get(
                "user::general", "persona_name", fallback=cfg["persona_name"]
            )
            cfg["language"] = parser.get(
                "user::general", "language", fallback=cfg["language"]
            )
            cfg["steamid"] = parser.get(
                "user::general", "account_steamid", fallback=cfg["steamid"]
            )
        elif parser.has_section("main"):  # backward compat with pre-fix saved configs
            cfg["username"] = parser.get("main", "username", fallback=cfg["username"])
            cfg["persona_name"] = parser.get(
                "main", "persona_name", fallback=cfg["persona_name"]
            )
            cfg["language"] = parser.get("main", "language", fallback=cfg["language"])
            cfg["steamid"] = parser.get("main", "steamid", fallback=cfg["steamid"])
        if parser.has_section("faugus"):
            cfg["fetch_achievements"] = parser.getboolean(
                "faugus", "fetch_achievements", fallback=cfg["fetch_achievements"]
            )
            cfg["dll_path"] = parser.get("faugus", "dll_path", fallback="")
            cfg["gse_fork_variant"] = parser.get(
                "faugus", "gse_fork_variant", fallback="gse_fork"
            )
            cfg["fork_variant"] = parser.get(
                "faugus", "fork_variant", fallback="regular"
            )

    app_ini = settings_dir / "configs.app.ini"
    if app_ini.exists():
        parser = configparser.ConfigParser()
        parser.read(str(app_ini), encoding="utf-8")
        if parser.has_section("app"):
            cfg["unlock_all_dlc"] = parser.getboolean(
                "app", "unlock_all_dlc", fallback=cfg["unlock_all_dlc"]
            )
            cfg["allow_unknown_stats"] = parser.getboolean(
                "app", "allow_unknown_stats", fallback=cfg["allow_unknown_stats"]
            )
            cfg["allow_unknown_achievements"] = parser.getboolean(
                "app",
                "allow_unknown_achievements",
                fallback=cfg["allow_unknown_achievements"],
            )
            cfg["auto_create_leaderboards"] = parser.getboolean(
                "app",
                "auto_create_leaderboards",
                fallback=cfg["auto_create_leaderboards"],
            )

    overlay_ini = settings_dir / "configs.overlay.ini"
    if overlay_ini.exists():
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        parser.read(str(overlay_ini), encoding="utf-8")
        section = (
            "overlay::general" if parser.has_section("overlay::general") else "overlay"
        )
        if parser.has_section(section):
            cfg["overlay_experimental"] = parser.getboolean(
                section,
                "enable_experimental_overlay",
                fallback=cfg["overlay_experimental"],
            )
            if parser.has_option(section, "disable_achievement_notification"):
                cfg["overlay_achievements"] = not parser.getboolean(
                    section,
                    "disable_achievement_notification",
                    fallback=not cfg["overlay_achievements"],
                )
            elif parser.has_option(section, "show_achievement_notifications"):
                cfg["overlay_achievements"] = parser.getboolean(
                    section,
                    "show_achievement_notifications",
                    fallback=cfg["overlay_achievements"],
                )
            if parser.has_option(section, "disable_achievement_progress_notification"):
                cfg["overlay_achievement_progress"] = not parser.getboolean(
                    section,
                    "disable_achievement_progress_notification",
                    fallback=not cfg["overlay_achievement_progress"],
                )
            if parser.has_option(section, "disable_friend_notification"):
                cfg["overlay_friends"] = not parser.getboolean(
                    section,
                    "disable_friend_notification",
                    fallback=not cfg["overlay_friends"],
                )
            elif parser.has_option(section, "show_friend_notifications"):
                cfg["overlay_friends"] = parser.getboolean(
                    section,
                    "show_friend_notifications",
                    fallback=cfg["overlay_friends"],
                )

    cfg["offline"] = (settings_dir / "offline.txt").exists()
    cfg["disable_networking"] = (settings_dir / "disable_networking.txt").exists()

    return cfg


def write_config(gameid: str, cfg: dict) -> None:
    settings_dir = get_steam_settings_dir(gameid)
    settings_dir.mkdir(parents=True, exist_ok=True)

    appid = cfg.get("appid", "").strip()
    if appid:
        (settings_dir / "steam_appid.txt").write_text(appid + "\n", encoding="utf-8")
    else:
        (settings_dir / "steam_appid.txt").unlink(missing_ok=True)

    user_ini = configparser.ConfigParser()
    user_ini["user::general"] = {
        "account_name": cfg.get("username", _DEFAULTS["username"]),
        "language": cfg.get("language", _DEFAULTS["language"]),
    }
    steamid = cfg.get("steamid", "").strip()
    if steamid:
        user_ini["user::general"]["account_steamid"] = steamid
    persona = cfg.get("persona_name", "").strip()
    if persona:
        user_ini["user::general"]["persona_name"] = persona
    user_ini["faugus"] = {
        "fetch_achievements": "1" if cfg.get("fetch_achievements", True) else "0",
        "dll_path": cfg.get("dll_path", ""),
        "gse_fork_variant": cfg.get("gse_fork_variant", "gse_fork"),
        "fork_variant": cfg.get("fork_variant", "regular"),
    }
    with open(settings_dir / "configs.user.ini", "w", encoding="utf-8") as f:
        user_ini.write(f)

    app_ini = configparser.ConfigParser()
    app_ini["app"] = {
        "unlock_all_dlc": "1" if cfg.get("unlock_all_dlc") else "0",
        "allow_unknown_stats": "1" if cfg.get("allow_unknown_stats") else "0",
        "allow_unknown_achievements": "1"
        if cfg.get("allow_unknown_achievements")
        else "0",
        "auto_create_leaderboards": "1" if cfg.get("auto_create_leaderboards") else "0",
    }
    with open(settings_dir / "configs.app.ini", "w", encoding="utf-8") as f:
        app_ini.write(f)

    _update_overlay_ini(
        settings_dir / "configs.overlay.ini",
        bool(cfg.get("overlay_experimental")),
        bool(cfg.get("overlay_achievements", True)),
        bool(cfg.get("overlay_achievement_progress", False)),
        bool(cfg.get("overlay_friends", True)),
    )

    offline_file = settings_dir / "offline.txt"
    if cfg.get("offline"):
        offline_file.touch()
    else:
        offline_file.unlink(missing_ok=True)

    net_file = settings_dir / "disable_networking.txt"
    if cfg.get("disable_networking"):
        net_file.touch()
    else:
        net_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Launch / restore
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(f"[gse] {msg}", flush=True)


def _find_dll(
    start_dir: Path, dll_name: str, max_parents: int = 0, override: str = ""
) -> "Path | None":
    if override:
        p = Path(override)
        if p.name == dll_name and p.exists():
            return p
    search_dir = start_dir
    for _ in range(max_parents + 1):
        matches = [
            m
            for m in search_dir.rglob(dll_name)
            if not any(m.is_relative_to(d) for d in _ALL_BINARY_DIRS)
        ]
        if matches:
            return matches[0]
        parent = search_dir.parent
        if parent == search_dir:
            break
        search_dir = parent
    return None


def prepare_goldberg(game: dict) -> None:
    game_path = game.get("path", "")
    gameid = game.get("gameid", "")

    _log(f"prepare: gameid={gameid!r} path={game_path!r}")

    if not game_path:
        _log("prepare: no game path, aborting")
        return

    game_dir = Path(os.path.dirname(game_path))
    settings_src = get_steam_settings_dir(gameid)

    _log(f"prepare: game_dir={game_dir}")
    _log(f"prepare: settings_src={settings_src} exists={settings_src.exists()}")

    if not settings_src.exists():
        _log(
            "prepare: staging steam_settings not found - open Goldberg Settings and save first"
        )
        return

    cfg = read_config(gameid)
    dll_path_override = cfg.get("dll_path", "").strip()
    if dll_path_override:
        _log(f"prepare: dll_path override set: {dll_path_override!r}")
    fork = cfg.get("gse_fork_variant", "gse_fork")
    binary_dir = _fork_binary_dir(fork)
    _log(f"prepare: using fork={fork!r} binary_dir={binary_dir}")

    for dll_name in _DLL_NAMES:
        stale = binary_dir / (dll_name + ".orig")
        if stale.exists():
            _log(f"prepare: removing stale backup {stale}")
            stale.unlink()

    dll_dir = game_dir
    orig_for_interfaces: "Path | None" = None
    for dll_name in _DLL_NAMES:
        game_dll = _find_dll(game_dir, dll_name, override=dll_path_override)
        if game_dll is None:
            _log(
                f"prepare: {dll_name} not found under {game_dir} (or parents), skipping"
            )
            continue
        if dll_path_override and game_dll == Path(dll_path_override):
            _log(f"prepare: {dll_name}: using override path {game_dll}")
        dll_dir = game_dll.parent
        orig_dll = game_dll.with_suffix(game_dll.suffix + ".orig")
        variant = cfg.get("fork_variant", "regular")
        src_dll = binary_dir / variant / dll_name
        if not src_dll.exists():
            src_dll = binary_dir / dll_name

        _log(
            f"prepare: {dll_name}: found at {game_dll}  src_dll={src_dll} exists={src_dll.exists()}"
        )

        if not src_dll.exists():
            _log(
                f"prepare: Goldberg {dll_name} not in binary dir {binary_dir}, skipping"
            )
            continue

        if not orig_dll.exists():
            _log(f"prepare: backing up {game_dll} -> {orig_dll}")
            shutil.copy2(str(game_dll), str(orig_dll))
        else:
            _log(f"prepare: backup {orig_dll} already exists, skipping backup")

        if orig_for_interfaces is None:
            orig_for_interfaces = orig_dll

        _log(f"prepare: injecting {src_dll} -> {game_dll}")
        shutil.copy2(str(src_dll), str(game_dll))

    settings_dst = dll_dir / "steam_settings"
    _log(f"prepare: copying steam_settings {settings_src} -> {settings_dst}")
    if settings_dst.exists():
        _log("prepare: removing existing steam_settings in dll dir")
        shutil.rmtree(str(settings_dst))
    shutil.copytree(str(settings_src), str(settings_dst))
    deployed = sorted(p.name for p in settings_dst.iterdir())
    _log(f"prepare: steam_settings files: {deployed}")

    if orig_for_interfaces and orig_for_interfaces.exists():
        gi_x64 = binary_dir / "generate_interfaces_x64"
        gi_x32 = binary_dir / "generate_interfaces_x32"
        tool = gi_x64 if gi_x64.exists() else gi_x32 if gi_x32.exists() else None
        if tool:
            try:
                result = subprocess.run(
                    [str(tool), str(orig_for_interfaces)],
                    cwd=str(settings_dst),
                    timeout=30,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    _log(
                        f"prepare: generate_interfaces stdout: {result.stdout.strip()}"
                    )
                if result.stderr.strip():
                    _log(
                        f"prepare: generate_interfaces stderr: {result.stderr.strip()}"
                    )
                if result.returncode != 0:
                    _log(f"prepare: generate_interfaces exited {result.returncode}")
                if (settings_dst / "steam_interfaces.txt").exists():
                    _log("prepare: generate_interfaces wrote steam_interfaces.txt")
                else:
                    _log("prepare: generate_interfaces ran but produced no output")
            except Exception as e:
                _log(f"prepare: generate_interfaces failed: {e}")
        else:
            _log(
                "prepare: generate_interfaces tool not found - restart faugus to download"
            )

    _log("prepare: done")


def restore_goldberg(game: dict) -> None:
    game_path = game.get("path", "")
    gameid = game.get("gameid", "")

    _log(f"restore: gameid={gameid!r} path={game_path!r}")

    if not game_path:
        _log("restore: no game path, aborting")
        return

    game_dir = Path(os.path.dirname(game_path))

    for dll_name in _DLL_NAMES:
        orig_dll = _find_dll(game_dir, dll_name + ".orig")
        if orig_dll is None:
            continue
        settings_dst = orig_dll.parent / "steam_settings"
        if settings_dst.exists():
            _log(f"restore: removing {settings_dst}")
            shutil.rmtree(str(settings_dst))
        game_dll = orig_dll.with_suffix("")
        _log(f"restore: {orig_dll} -> {game_dll}")
        game_dll.unlink(missing_ok=True)
        orig_dll.rename(game_dll)

    # legacy fallback: clean up steam_settings placed in the exe directory
    settings_dst = game_dir / "steam_settings"
    if settings_dst.exists():
        _log(f"restore: removing legacy {settings_dst}")
        shutil.rmtree(str(settings_dst))

    _log("restore: done")


def export_game(
    game_path: str, gameid: str, fork: str, destination: Path
) -> "tuple[bool, str]":
    _log(
        f"export: gameid={gameid!r} path={game_path!r} fork={fork!r} dest={destination}"
    )

    if not game_path:
        return False, "no game path configured"

    game_dir = Path(os.path.dirname(game_path))
    if not game_dir.is_dir():
        return False, f"game directory not found: {game_dir}"

    export_dir = destination / game_dir.name
    if export_dir.exists():
        return False, "exists"

    _log(f"export: copying {game_dir} -> {export_dir}")
    try:
        shutil.copytree(str(game_dir), str(export_dir))
    except Exception as e:
        return False, f"copy failed: {e}"

    cfg = read_config(gameid)
    variant = cfg.get("fork_variant", "regular")
    binary_dir = _fork_binary_dir(fork)
    _log(f"export: fork={fork!r} variant={variant!r} binary_dir={binary_dir}")

    dll_dir = export_dir
    dll_for_interfaces: "Path | None" = None
    dlls_to_inject: "list[tuple[Path, Path]]" = []

    for dll_name in _DLL_NAMES:
        game_dll = _find_dll(export_dir, dll_name)
        if game_dll is None:
            _log(f"export: {dll_name} not found under {export_dir}, skipping")
            continue
        dll_dir = game_dll.parent

        src_dll = binary_dir / variant / dll_name
        if not src_dll.exists():
            src_dll = binary_dir / dll_name
        if not src_dll.exists():
            _log(
                f"export: Goldberg {dll_name} not in binary dir {binary_dir}, skipping"
            )
            continue

        if dll_for_interfaces is None:
            dll_for_interfaces = game_dll
        dlls_to_inject.append((src_dll, game_dll))

    settings_dst = dll_dir / "steam_settings"
    settings_src = get_steam_settings_dir(gameid)
    has_settings = settings_src.exists()

    if has_settings:
        _log(f"export: copying steam_settings {settings_src} -> {settings_dst}")
        if settings_dst.exists():
            shutil.rmtree(str(settings_dst))
        shutil.copytree(str(settings_src), str(settings_dst))

    if dll_for_interfaces is not None and dll_for_interfaces.exists():
        gi_x64 = binary_dir / "generate_interfaces_x64"
        gi_x32 = binary_dir / "generate_interfaces_x32"
        tool = gi_x64 if gi_x64.exists() else gi_x32 if gi_x32.exists() else None
        if tool:
            settings_dst.mkdir(exist_ok=True)
            try:
                result = subprocess.run(
                    [str(tool), str(dll_for_interfaces)],
                    cwd=str(settings_dst),
                    timeout=30,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    _log(f"export: generate_interfaces stdout: {result.stdout.strip()}")
                if result.stderr.strip():
                    _log(f"export: generate_interfaces stderr: {result.stderr.strip()}")
                if (settings_dst / "steam_interfaces.txt").exists():
                    _log("export: generate_interfaces wrote steam_interfaces.txt")
                else:
                    _log("export: generate_interfaces ran but produced no output")
            except Exception as e:
                _log(f"export: generate_interfaces failed: {e}")
        else:
            _log("export: generate_interfaces tool not found")

    for src_dll, game_dll in dlls_to_inject:
        _log(f"export: injecting {src_dll} -> {game_dll}")
        shutil.copy2(str(src_dll), str(game_dll))

    _log("export: done")
    msg = str(export_dir)
    if not has_settings:
        msg += "\n\nNote: no GSE settings were found for this game. Open Goldberg Settings and save first for a fully configured export."
    return True, msg


# ---------------------------------------------------------------------------
# Save data location
# ---------------------------------------------------------------------------

_PCGW_API = "https://www.pcgamingwiki.com/w/api.php"

# Maps {{p|...}}/{{path|...}} macro keys (lower-cased, as they appear in
# PCGamingWiki's "Save game data location" wikitext) to the equivalent
# subpath inside a Wine prefix's "steamuser" profile.
_PCGW_PATH_MACROS = {
    "userprofile": "drive_c/users/steamuser",
    "userprofile\\documents": "drive_c/users/steamuser/Documents",
    "documents": "drive_c/users/steamuser/Documents",
    "userprofile\\appdata\\roaming": "drive_c/users/steamuser/AppData/Roaming",
    "appdata": "drive_c/users/steamuser/AppData/Roaming",
    "userprofile\\appdata\\local": "drive_c/users/steamuser/AppData/Local",
    "localappdata": "drive_c/users/steamuser/AppData/Local",
    "userprofile\\appdata\\locallow": "drive_c/users/steamuser/AppData/LocalLow",
    "userprofile\\saved games": "drive_c/users/steamuser/Saved Games",
    "public": "drive_c/users/Public",
    "programdata": "drive_c/ProgramData",
    "allusersprofile": "drive_c/ProgramData",
}

_PCGW_SAVE_TEMPLATE_RE = re.compile(
    r"\{\{Game data/saves\s*\|\s*Windows\b", re.IGNORECASE
)
_PCGW_MACRO_RE = re.compile(r"\{\{p(?:ath)?\|([^{}]+)\}\}", re.IGNORECASE)


def _pcgw_extract_balanced(text: str, start: int) -> "str | None":
    """Return the wikitext template starting at the '{{' at/after `start` up
    to its matching '}}', accounting for templates nested inside it."""
    open_at = text.find("{{", start)
    if open_at == -1:
        return None

    depth = 0
    i = open_at
    n = len(text)
    while i < n - 1:
        chunk = text[i : i + 2]
        if chunk == "{{":
            depth += 1
            i += 2
        elif chunk == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return text[open_at:i]
        else:
            i += 1
    return None


def _pcgw_translate_path(raw: str) -> "str | None":
    """Translate a save-path payload (which may embed {{p|...}} macros) into a
    path relative to a Wine prefix root, or None if a macro isn't recognised."""
    for key in _PCGW_MACRO_RE.findall(raw):
        if key.strip().lower() not in _PCGW_PATH_MACROS:
            return None

    translated = _PCGW_MACRO_RE.sub(
        lambda m: _PCGW_PATH_MACROS[m.group(1).strip().lower()], raw
    )
    translated = translated.replace("\\", "/").strip()
    while "//" in translated:
        translated = translated.replace("//", "/")
    translated = translated.rstrip("/")
    return translated or None


def _pcgw_page_for_appid(appid: str) -> "str | None":
    import requests

    r = requests.get(
        _PCGW_API,
        params={
            "action": "cargoquery",
            "tables": "Infobox_game",
            "fields": "Infobox_game._pageName=Page",
            "where": f'Infobox_game.Steam_AppID HOLDS "{appid}"',
            "format": "json",
        },
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json().get("cargoquery", [])
    if not rows:
        return None
    return rows[0].get("title", {}).get("Page")


def _pcgw_save_section_wikitext(page: str) -> "str | None":
    import requests

    r = requests.get(
        _PCGW_API,
        params={"action": "parse", "page": page, "prop": "sections", "format": "json"},
        timeout=10,
    )
    r.raise_for_status()
    index = None
    for section in r.json().get("parse", {}).get("sections", []):
        if section.get("line", "").strip().lower() == "save game data location":
            index = section.get("index")
            break
    if index is None:
        return None

    r = requests.get(
        _PCGW_API,
        params={
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "section": index,
            "format": "json",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("parse", {}).get("wikitext", {}).get("*")


def lookup_save_path(appid: str) -> "str | None":
    """Best-effort lookup of a game's Windows save location on PCGamingWiki,
    translated to a path relative to a Wine prefix root (e.g.
    "drive_c/users/steamuser/Documents/My Game"). Returns None if the page,
    the section, the template, or any of its path macros can't be resolved -
    PCGamingWiki doesn't expose this as a queryable field, only as wikitext,
    so lookups on stub pages or unusual templates are expected to fail."""
    appid = (appid or "").strip()
    if not appid:
        return None

    try:
        page = _pcgw_page_for_appid(appid)
        if not page:
            return None

        wikitext = _pcgw_save_section_wikitext(page)
        if not wikitext:
            return None

        match = _PCGW_SAVE_TEMPLATE_RE.search(wikitext)
        if not match:
            return None

        template = _pcgw_extract_balanced(wikitext, match.start())
        if not template:
            return None

        # template looks like "{{Game data/saves|Windows|<path>}}" (possibly
        # "Windows 3.x" etc.) - drop the outer braces and the leading parts.
        inner = template[2:-2]
        _, _, payload = inner.partition("|Windows")
        _, _, raw_path = payload.partition("|")
        raw_path = raw_path.strip()
        if not raw_path:
            return None

        return _pcgw_translate_path(raw_path)
    except Exception:
        return None
