import os
import socket
import time
import urllib.request
from threading import Thread

from flask import Flask, request

app = Flask('')


def keepalive_logs_enabled():
    return os.getenv('ENABLE_KEEPALIVE_LOGS', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}


def log_keepalive(message):
    if keepalive_logs_enabled():
        print(message)


def get_local_ips():
    candidates = []
    try:
        hostname = socket.gethostname()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            ip = sockaddr[0]
            if family in (socket.AF_INET, socket.AF_INET6) and ip not in candidates:
                candidates.append(ip)
    except Exception:
        pass

    try:
        with urllib.request.urlopen('https://api.ipify.org', timeout=10) as response:
            public_ip = response.read().decode('utf-8').strip()
            if public_ip and public_ip not in candidates:
                candidates.append(public_ip)
    except Exception:
        pass

    return candidates


@app.after_request
def log_incoming_visitors(response):
    if keepalive_logs_enabled():
        print(f"[keep_alive] Incoming visitor to port {os.getenv('PORT', '8080')}")
    return response


@app.route('/')
def main():
    return 'ok'


def run():
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8080'))
    ips = get_local_ips()
    log_keepalive(f"[keep_alive] Flask debug server starting on {host}:{port}")
    if ips:
        log_keepalive(f"[keep_alive] detected IPs: {', '.join(ips)}")
    app.run(host=host, port=port)


def ping_url(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'keepalive-pinger/1.0'})
        log_keepalive(f"[keep_alive] pinging {url}...")
        with urllib.request.urlopen(req, timeout=15) as response:
            status = response.status
            log_keepalive(f"[keep_alive] pinged {url} -> HTTP {status}")
            return status
    except Exception as exc:
        log_keepalive(f"[keep_alive] ping failed for {url}: {exc}")
        return None


def ping_loop():
    app_url = os.getenv('APP_URL')
    if not app_url:
        log_keepalive('[keep_alive] APP_URL not set; skipping external ping loop.')
        return

    interval_seconds = int(os.getenv('PING_INTERVAL', '780'))
    log_keepalive(f"[keep_alive] app ping loop enabled: URL={app_url}, interval={interval_seconds}s")
    while True:
        ping_url(app_url)
        time.sleep(interval_seconds)


def keep_alive():
    log_keepalive('[keep_alive] keep_alive() started')
    log_keepalive(f"[keep_alive] APP_URL={os.getenv('APP_URL', 'not set')}")
    log_keepalive(f"[keep_alive] PING_INTERVAL={os.getenv('PING_INTERVAL', '780')}s")

    server = Thread(target=run, daemon=True)
    server.start()

    app_url = os.getenv('APP_URL')
    if app_url:
        log_keepalive(f"[keep_alive] starting ping loop for {app_url}")
        pinger = Thread(target=ping_loop, daemon=True)
        pinger.start()
    else:
        log_keepalive('[keep_alive] no APP_URL configured; external ping loop disabled')


if __name__ == '__main__':
    log_keepalive('[keep_alive] running as standalone script')
    keep_alive()
    while True:
        time.sleep(60)
