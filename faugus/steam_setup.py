import os
import shutil
import struct
import subprocess
import threading

import vdf
from pathlib import Path
from faugus.path_manager import PathManager, IS_FLATPAK
from gi.repository import GdkPixbuf, GLib

def _check_command(cmd):
    try:
        if IS_FLATPAK:
            cmd = ["flatpak-spawn", "--host"] + cmd
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except FileNotFoundError:
        return False

def has_steam_flatpak():
    return _check_command(["flatpak", "info", "com.valvesoftware.Steam"])

def has_steam_native():
    return _check_command(["which", "steam"])

def detect_steam_version():
    if has_steam_native():
        return "native"
    elif has_steam_flatpak():
        return "flatpak"
    else:
        return None

def detect_steam_folder():
    steam_version = detect_steam_version()
    if steam_version == "flatpak":
        return (Path(PathManager.user_home(".var/app/com.valvesoftware.Steam/.steam/steam")), True)
    if steam_version == "native":
        return (Path(PathManager.user_home(".steam/steam")), False)
    return (None, False)

steam_folder, IS_STEAM_FLATPAK = detect_steam_folder()
userdata = steam_folder / "userdata" if steam_folder else None
library = steam_folder / "config/libraryfolders.vdf" if steam_folder else None
librarycache = steam_folder / "appcache/librarycache" if steam_folder else None

lossless_dll = (
    (steam_folder / "steamapps/common/Lossless Scaling/Lossless.dll")
    if steam_folder and (steam_folder / "steamapps/common/Lossless Scaling/Lossless.dll").is_file()
    else ""
)

def detect_steam_id():
    if userdata:
        try:
            steam_ids = [f for f in os.listdir(userdata)
                         if os.path.isdir(os.path.join(userdata, f)) and f.isdigit()]
            return steam_ids[0] if steam_ids else None
        except (FileNotFoundError, PermissionError):
            return None
    return None

steam_id = detect_steam_id()
steam_shortcuts_path = userdata / steam_id / "config/shortcuts.vdf" if userdata and steam_id else ""

def read_library_folders():
    libraries = []

    if not library.exists():
        return libraries

    with open(library, "r", errors="ignore") as f:
        for line in f:
            if '"path"' in line:
                path = line.split('"')[-2]
                libraries.append(Path(path))

    return libraries

def read_installed_games():
    if not steam_folder:
        return []

    games = []
    libraries = read_library_folders()

    for lib in libraries:
        steamapps_dir = lib / "steamapps"

        if not steamapps_dir.exists():
            continue

        for manifest in steamapps_dir.glob("appmanifest_*.acf"):
            appid = manifest.stem.split("_")[-1]
            name = None

            with open(manifest, "r", errors="ignore") as f:
                for line in f:
                    if '"name"' in line:
                        name = line.split('"')[-2]

            if name:
                games.append((appid, name))

    return sorted(games, key=lambda x: x[1].lower())

def get_steam_icon_path(appid):
    if not librarycache.exists():
        return None

    cache = librarycache / str(appid)
    if not cache.exists():
        return None

    images = []

    for img in cache.rglob("*.jpg"):
        if img.name in (
            "header.jpg",
            "library_600x900.jpg",
            "library_capsule.jpg",
        ):
            continue

        try:
            pix = GdkPixbuf.Pixbuf.new_from_file(str(img))
            area = pix.get_width() * pix.get_height()
            images.append((area, img))
        except Exception:
            pass

    if not images:
        return None

    images.sort(key=lambda x: x[0])
    return str(images[0][1])

def _get_clienticon_from_appinfo(appid):
    if not steam_folder:
        return None
    appinfo_path = steam_folder / "appcache/appinfo.vdf"
    if not appinfo_path.exists():
        return None
    try:
        with open(appinfo_path, "rb") as f:
            magic = struct.unpack("<I", f.read(4))[0]
            f.read(4)  # universe
            if magic not in (0x07564428, 0x07564429):
                return None
            # v29 has an extra 20-byte sha1 field in each entry header
            skip = 60 if magic == 0x07564429 else 40
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cur_appid, size = struct.unpack("<II", hdr)
                if cur_appid == 0:
                    break
                entry = f.read(size)
                if cur_appid == int(appid):
                    parsed = vdf.binary_loads(entry[skip:])
                    return parsed.get("common", {}).get("clienticon")
    except Exception:
        return None


def fetch_steam_cdn_icon(appid_or_name, dest, callback=None):
    def _fetch():
        try:
            import requests
            identifier = str(appid_or_name).strip()
            print(f"[icon] fetch_steam_cdn_icon: starting for {identifier!r} → {dest}", flush=True)

            if not identifier.isdigit():
                print(f"[icon] name-based lookup for {identifier!r}", flush=True)
                r = requests.get(
                    "https://store.steampowered.com/api/storesearch/",
                    params={"term": identifier, "l": "english", "cc": "US"},
                    timeout=10,
                )
                items = [i for i in r.json().get("items", []) if i.get("type") == "app"]
                if not items:
                    print(f"[icon] no Steam results for {identifier!r}", flush=True)
                    return
                identifier = str(items[0]["id"])
                print(f"[icon] resolved to appid {identifier}", flush=True)

            # Priority 1: client icon hash from local appinfo.vdf
            icon_hash = _get_clienticon_from_appinfo(identifier)
            if icon_hash:
                print(f"[icon] got clienticon hash from appinfo.vdf: {icon_hash}", flush=True)
                icon_url = (
                    f"https://cdn.cloudflare.steamstatic.com/steamcommunity/public"
                    f"/images/apps/{identifier}/{icon_hash}.ico"
                )
                print(f"[icon] downloading {icon_url}", flush=True)
                resp = requests.get(icon_url, timeout=10)
                resp.raise_for_status()
                icon_data = resp.content
                if icon_data[:4] != b'\x00\x00\x01\x00':
                    print(f"[icon] CDN returned non-ICO data ({icon_data[:4]!r}), skipping", flush=True)
                    return
                with open(dest, "wb") as f:
                    f.write(icon_data)
                print(f"[icon] saved {len(icon_data)} bytes to {dest}", flush=True)
            else:
                # Priority 2: crop library_600x900.jpg from CDN into a square
                print(f"[icon] no appinfo.vdf entry, falling back to library_600x900.jpg crop", flush=True)
                magick = shutil.which("magick") or shutil.which("convert")
                if not magick:
                    print(f"[icon] ImageMagick not found, cannot crop cover art", flush=True)
                    return
                cover_url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{identifier}/library_600x900.jpg"
                print(f"[icon] downloading {cover_url}", flush=True)
                resp = requests.get(cover_url, timeout=10)
                resp.raise_for_status()
                cover_data = resp.content
                if cover_data[:2] != b'\xff\xd8':
                    print(f"[icon] CDN returned non-JPEG data ({cover_data[:4]!r}), skipping", flush=True)
                    return
                tmp = dest + ".cover_tmp.jpg"
                with open(tmp, "wb") as f:
                    f.write(cover_data)
                result = subprocess.run(
                    [magick, tmp,
                     "-gravity", "Center", "-crop", "600x600+0+0", "+repage",
                     "-resize", "256x256!", f"png:{dest}"],
                    capture_output=True,
                )
                os.remove(tmp)
                if result.returncode == 0:
                    print(f"[icon] cropped cover art saved to {dest}", flush=True)
                else:
                    print(f"[icon] ImageMagick crop failed: {result.stderr.decode()}", flush=True)
                    return

            if callback:
                GLib.idle_add(callback)
        except Exception as e:
            print(f"[icon] fetch_steam_cdn_icon error: {e}", flush=True)

    threading.Thread(target=_fetch, daemon=True).start()
