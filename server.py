from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = ROOT_DIR / ".cache"
SATELLITE_CACHE_PATH = CACHE_DIR / "active_satellites.tle"
SATELLITE_META_PATH = CACHE_DIR / "active_satellites.meta.json"
SATELLITE_SOURCE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
SATELLITE_CACHE_MAX_AGE_S = 2 * 60 * 60
SATELLITE_API_PATH = "/api/satellites/active.tle"

cache_lock = threading.Lock()


def ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def read_cache_text() -> str | None:
    if not SATELLITE_CACHE_PATH.exists():
        return None
    return SATELLITE_CACHE_PATH.read_text(encoding="utf-8", errors="ignore")


def read_cache_meta() -> dict:
    if not SATELLITE_META_PATH.exists():
        return {}
    try:
        return json.loads(SATELLITE_META_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_cache(raw_text: str) -> None:
    ensure_cache_dir()
    SATELLITE_CACHE_PATH.write_text(raw_text, encoding="utf-8")
    SATELLITE_META_PATH.write_text(
        json.dumps({"saved_at": time.time()}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def cache_age_seconds() -> float | None:
    meta = read_cache_meta()
    saved_at = meta.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        return None
    return max(0.0, time.time() - float(saved_at))


def fetch_satellite_source() -> str:
    request = urllib.request.Request(
        SATELLITE_SOURCE_URL,
        headers={
            "User-Agent": "CivilizationObserver/1.0 (+local cache proxy)",
            "Accept": "text/plain",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def get_satellite_payload() -> tuple[int, str, dict[str, str]]:
    with cache_lock:
        cached_text = read_cache_text()
        age_s = cache_age_seconds()
        cache_is_fresh = cached_text is not None and age_s is not None and age_s < SATELLITE_CACHE_MAX_AGE_S

        if cache_is_fresh:
            return HTTPStatus.OK, cached_text, {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Cache-Status": "fresh",
            }

        try:
            fresh_text = fetch_satellite_source()
            write_cache(fresh_text)
            return HTTPStatus.OK, fresh_text, {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Cache-Status": "refreshed",
            }
        except urllib.error.HTTPError as error:
            if cached_text is not None:
                return HTTPStatus.OK, cached_text, {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Cache-Control": "no-store",
                    "X-Cache-Status": f"stale-http-{error.code}",
                }
            message = (
                f"Satellitenquelle aktuell nicht verfuegbar (HTTP {error.code}). "
                f"Es liegt noch kein lokaler Cache vor."
            )
            return HTTPStatus.BAD_GATEWAY, message, {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
            }
        except Exception as error:  # noqa: BLE001
            if cached_text is not None:
                return HTTPStatus.OK, cached_text, {
                    "Content-Type": "text/plain; charset=utf-8",
                    "Cache-Control": "no-store",
                    "X-Cache-Status": "stale-error",
                }
            message = (
                "Satellitenquelle aktuell nicht verfuegbar "
                f"({type(error).__name__}). Es liegt noch kein lokaler Cache vor."
            )
            return HTTPStatus.BAD_GATEWAY, message, {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
            }


class CivilizationObserverHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == SATELLITE_API_PATH:
            self.handle_satellite_request()
            return
        super().do_GET()

    def handle_satellite_request(self) -> None:
        status, payload, headers = get_satellite_payload()
        body = payload.encode("utf-8")
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[server] {self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Civilization Observer local server")
    parser.add_argument("--port", type=int, default=8000, help="Port for the local web server")
    args = parser.parse_args()

    ensure_cache_dir()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), CivilizationObserverHandler)
    print(f"Civilization Observer running on http://localhost:{args.port}")
    print("Satelliten werden lokal ueber /api/satellites/active.tle mit Festplatten-Cache bedient.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer gestoppt.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
