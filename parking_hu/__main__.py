from __future__ import annotations

import argparse
import os
from pathlib import Path
import threading
import webbrowser


def main():
    parser = argparse.ArgumentParser(description="胡轩铭智慧停车仿真模块")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--planner", default=os.environ.get("PARKING_PLANNER", "parking_hu.reference_planner:Planner"))
    parser.add_argument("--weights", default=str(Path(__file__).resolve().parents[1] / "models" / "parking_yolov8n.pt"))
    parser.add_argument("--runtime-dir", default=str(Path(__file__).resolve().parents[1] / "runtime"))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口应在 1024—65535 之间")
    import uvicorn
    from .service import ParkingService
    from .api import create_app
    service = ParkingService(args.runtime_dir, weights_path=args.weights, planner_name=args.planner)
    if not args.no_browser:
        timer = threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        timer.daemon = True
        timer.start()
    print(f"本地地址 http://127.0.0.1:{args.port}  规划插件 {args.planner}", flush=True)
    uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
