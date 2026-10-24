"""Check the listening port before importing the app or downloading resources."""
import json
import socket
from urllib.request import ProxyHandler, build_opener


def _is_wuwa_service(port: int) -> bool:
    # Loopback checks must not go through system/environment HTTP proxies.
    try:
        with build_opener(ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/api/health", timeout=2,
        ) as response:
            data = json.loads(response.read(4096))
        return isinstance(data, dict) and data.get("service") == "wuwa-web" and data.get("status") == "ok"
    except Exception:
        return False


def check_startup_port(port: int) -> int | None:
    """None: start normally; 0: existing Wuwa service; 1: unavailable port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Windows permits overlapping wildcard binds without exclusive use.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("0.0.0.0", port))
    except OSError:
        if _is_wuwa_service(port):
            print(f"[WuwaWebTool] Already running on port {port}.", flush=True)
            print(f"[WuwaWebTool] Open http://localhost:{port} (reuse the existing instance).", flush=True)
            return 0
        print(f"[WuwaWebTool] Port {port} is unavailable or occupied by another program.", flush=True)
        print("[WuwaWebTool] Stop the program using this port, or set PORT to another port before starting.", flush=True)
        return 1
    return None
