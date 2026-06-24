import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

BASE = "http://127.0.0.1:3000"


def get(path, timeout=120):
    with urlopen(BASE + path, timeout=timeout) as response:
        return response.status, json.load(response)


def main():
    tests = [
        ("Health", "/api/health"),
        ("AAPL analysis", "/api/analysis/AAPL"),
        ("Radar (3 assets)", "/api/radar?universe=core_us&limit=3"),
        ("Comparison", "/api/compare?left=MSFT&right=AAPL"),
    ]

    failures = 0

    for label, path in tests:
        print(f"\n=== {label} ===")
        try:
            status, data = get(path)
            print("HTTP:", status)

            if path == "/api/health":
                print("Providers:", ", ".join(data.get("providers", [])))
            elif path.startswith("/api/analysis"):
                print("Asset:", data.get("asset", {}).get("name"))
                print("Framework:", data.get("framework_engine", {}).get("version"))
                print("Technical:", (data.get("technical") or {}).get("score"))
            elif path.startswith("/api/radar"):
                print("Analysed:", data.get("analysed_count"))
                print("Errors:", data.get("error_count"))
                for item in data.get("results", [])[:3]:
                    print("-", item.get("symbol"), item.get("radar_score"), item.get("status", {}).get("label"))
            else:
                print("Same type:", data.get("same_asset_type"))
                print("Left:", data.get("left", {}).get("asset", {}).get("symbol"))
                print("Right:", data.get("right", {}).get("asset", {}).get("symbol"))

        except HTTPError as error:
            failures += 1
            body = error.read().decode("utf-8", errors="replace")
            print("FAILED HTTP", error.code, body[:500])
        except URLError as error:
            failures += 1
            print("FAILED CONNECTION", error.reason)
        except Exception as error:
            failures += 1
            print("FAILED", type(error).__name__, error)

    print("\nResult:", "PASS" if failures == 0 else f"{failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
