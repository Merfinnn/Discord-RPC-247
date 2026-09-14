import json
import os
import socket
import sys
import threading
import time

import requests
import websocket


def load_env_file(path=".env"):
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


load_env_file()

from keep_alive import get_local_ips, keep_alive

status = os.getenv("STATUS", "online")
name = os.getenv("NAME", "Name")
details = os.getenv("DETAILS", "Details")
state = os.getenv("STATE", "State")
activity_type = int(os.getenv("ACTIVITY_TYPE") or 0)
application_id = str(os.getenv("APP_ID", "")).strip()
large_image = os.getenv("LARGE_IMAGE", "Large Image")
large_text = os.getenv("LARGE_TEXT", "Large Text")
small_image = os.getenv("SMALL_IMAGE", "Small Image")
small_text = os.getenv("SMALL_TEXT", "Small Text")
startfrom = int(os.getenv("START") or 0)
start = str(int((time.time() - (startfrom * 3600)) * 1000))
custom_status = os.getenv("CUSTOM_STATUS", "")
custom_status_emoji = os.getenv("CUSTOM_EMOJI", "")
token = os.getenv("TOKEN")

VALID_STATUSES = {"online", "idle", "dnd", "invisible"}
if status not in VALID_STATUSES:
    print(f"[ERROR] Invalid status '{status}'. Use one of: {', '.join(sorted(VALID_STATUSES))}.")
    sys.exit(1)

if not token:
    print("[ERROR] Please set the token environment variable.")
    sys.exit(1)

if not application_id:
    print("[WARN] APP_ID is empty. Image assets will not render until you set a valid Discord application ID.")


def get_user_info(token_value):
    headers = {"Authorization": token_value, "Content-Type": "application/json"}
    response = requests.get("https://discord.com/api/v10/users/@me", headers=headers, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"Token validation failed: HTTP {response.status_code} ({response.text[:200]})")
    userinfo = response.json()
    return userinfo["username"], userinfo["discriminator"], userinfo["id"]


def get_app_assets(app_id, token_value):
    if not app_id:
        return []
    headers = {"Authorization": token_value, "Content-Type": "application/json"}
    response = requests.get(f"https://discord.com/api/v10/oauth2/applications/{app_id}/assets", headers=headers, timeout=15)
    if response.status_code != 200:
        return []
    try:
        data = response.json()
    except ValueError:
        return []
    return [item.get("name", "") for item in data if isinstance(item, dict) and item.get("name")]


def is_external_asset_url(value):
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def is_discord_asset_key(value):
    return isinstance(value, str) and value.strip().startswith("mp:")


def register_external_asset_url(app_id, image_url, token_value):
    if not image_url:
        return image_url

    normalized = str(image_url).strip()
    if is_discord_asset_key(normalized):
        return normalized
    if not app_id or not is_external_asset_url(normalized):
        return normalized

    headers = {"Authorization": token_value, "Content-Type": "application/json"}
    payload = {"urls": [image_url]}
    try:
        response = requests.post(
            f"https://discord.com/api/v10/applications/{app_id}/external-assets",
            headers=headers,
            json=payload,
            timeout=20,
        )
        if response.status_code >= 400:
            print(f"[WARN] Failed to register external asset URL '{image_url}' for app {app_id}. HTTP {response.status_code}: {response.text[:200]}")
            return image_url

        data = response.json()
        if isinstance(data, list) and data and isinstance(data[0], dict):
            external_path = data[0].get("external_asset_path")
            if external_path:
                return f"mp:{external_path}"
    except Exception as exc:
        print(f"[WARN] Could not register external asset URL '{image_url}' for app {app_id}: {exc}")

    return image_url


def validate_asset_names(app_id, large_image, small_image, token_value):
    if is_external_asset_url(large_image) or is_external_asset_url(small_image):
        return
    if is_discord_asset_key(large_image) or is_discord_asset_key(small_image):
        return

    asset_names = set(get_app_assets(app_id, token_value))
    if not app_id:
        print("[WARN] APP_ID is empty. Discord will not show images until you set a valid Discord application ID.")
        return
    if not asset_names:
        print("[WARN] No assets were found for this application ID. Upload images in the Discord Developer Portal and then set LARGE_IMAGE / SMALL_IMAGE to the exact asset names.")
        return
    if large_image and large_image not in asset_names:
        print(f"[WARN] LARGE_IMAGE '{large_image}' was not found in app {app_id}. Available assets: {', '.join(sorted(asset_names)) or 'none'}")
    if small_image and small_image not in asset_names:
        print(f"[WARN] SMALL_IMAGE '{small_image}' was not found in app {app_id}. Available assets: {', '.join(sorted(asset_names)) or 'none'}")
    if not large_image and not small_image:
        print("[WARN] No image keys configured. Set LARGE_IMAGE and/or SMALL_IMAGE to a valid asset name from your Discord app.")


def send_custom_status(ws, current_status, details=None, state=None, name=None, application_id=None, activity_type=0, large_image=None, large_text=None, small_image=None, small_text=None, custom_status=None, custom_status_emoji=None, start=None):
    if start is None:
        start = int(time.time() * 1000)

    activity = {
        "type": int(activity_type),
    }

    if application_id:
        activity["application_id"] = str(application_id)
    if name is not None:
        activity["name"] = name

    if details is not None:
        activity["details"] = details
    if state is not None:
        activity["state"] = state

    assets = {}
    if large_image is not None:
        assets["large_image"] = str(large_image).strip()
        if large_text:
            assets["large_text"] = str(large_text).strip()
    if small_image is not None:
        assets["small_image"] = str(small_image).strip()
        if small_text:
            assets["small_text"] = str(small_text).strip()
    if assets:
        activity["assets"] = assets

    activity["timestamps"] = {"start": start}

    activities_list = [activity]
    if custom_status:
        custom_act = {
            "type": 4,
            "name": "Custom Status",
            "state": custom_status,
        }
        if custom_status_emoji:
            custom_act["emoji"] = {"name": custom_status_emoji}
        activities_list.append(custom_act)

    payload = {
        "op": 3,
        "d": {
            "since": 0,
            "activities": activities_list,
            "status": current_status,
            "afk": False,
        },
    }
    ws.send(json.dumps(payload))


def connect_gateway(token_value, current_status, details=None, state=None, name=None, application_id=None, activity_type=0, large_image=None, large_text=None, small_image=None, small_text=None, custom_status=None, custom_status_emoji=None, start=None):
    ws = websocket.WebSocket()
    ws.connect("wss://gateway.discord.gg/?v=10&encoding=json")

    hello = json.loads(ws.recv())
    heartbeat_interval = hello["d"]["heartbeat_interval"]

    identify = {
        "op": 2,
        "d": {
            "token": token_value,
            "properties": {
                "$os": "Windows",
                "$browser": "Chrome",
                "$device": "desktop",
            },
            "presence": {"status": current_status, "afk": False},
        },
    }
    ws.send(json.dumps(identify))

    while True:
        message = ws.recv()
        if not message:
            raise RuntimeError("Gateway connection closed before READY event.")
        data = json.loads(message)
        if data.get("op") == 0 and data.get("t") == "READY":
            break

    send_custom_status(
        ws,
        current_status,
        details=details,
        state=state,
        name=name,
        application_id=application_id,
        activity_type=activity_type,
        large_image=large_image,
        large_text=large_text,
        small_image=small_image,
        small_text=small_text,
        custom_status=custom_status,
        custom_status_emoji=custom_status_emoji,
        start=start,
    )
    return ws, heartbeat_interval


def heartbeat_loop(ws, heartbeat_interval):
    while True:
        time.sleep(heartbeat_interval / 1000)
        try:
            ws.send(json.dumps({"op": 1, "d": None}))
        except Exception:
            break


def run_onliner():
    os.system("cls" if os.name == "nt" else "clear")

    try:
        username, discriminator, user_id = get_user_info(token)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    ips = get_local_ips()
    print("[main] Local/server IPs:")
    if ips:
        for ip in ips:
            print(f"  - {ip}")
    else:
        print("  - Unable to detect local IP")

    print(f"Logged in as {username}#{discriminator} ({user_id}).")
    print(f"Status: {status} | Custom status: {custom_status or 'disabled'}")
    print(f"Details: {details or 'disabled'} | State: {state or 'disabled'}")
    print(f"Name: {name or 'disabled'}")    
    print(f"Activity type: {activity_type}")
    print(f"App ID: {application_id or 'disabled'}")

    processed_large_image = register_external_asset_url(application_id, large_image, token)
    processed_small_image = register_external_asset_url(application_id, small_image, token)
    print(f"Large image: {processed_large_image or 'disabled'}")
    print(f"Small image: {processed_small_image or 'disabled'}")
    print(f"Large text: {large_text or 'disabled'}")
    print(f"Small text: {small_text or 'disabled'}")
    print(f"Custom emoji: {custom_status_emoji or 'disabled'}")
    print(f"Start timestamp: {start}")
    validate_asset_names(application_id, processed_large_image, processed_small_image, token)

    while True:
        try:
            ws, heartbeat_interval = connect_gateway(
                token,
                status,
                details=details,
                state=state,
                name=name,
                application_id=application_id,
                activity_type=activity_type,
                large_image=processed_large_image,
                large_text=large_text,
                small_image=processed_small_image,
                small_text=small_text,
                custom_status=custom_status,
                custom_status_emoji=custom_status_emoji,
                start=start,
            )
            heartbeat_loop(ws, heartbeat_interval)
        except Exception as exc:
            print(f"[WARN] Gateway disconnected. Reconnecting in 5 seconds... ({exc})")
            time.sleep(5)


if __name__ == "__main__":
    print('[main] starting keep_alive helper in background thread')
    keepalive_thread = threading.Thread(target=keep_alive, daemon=True)
    keepalive_thread.start()
    print('[main] keep_alive helper started')
    run_onliner()
