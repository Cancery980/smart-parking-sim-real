"""Standalone Windows demo entry, frozen with PyInstaller by the release build.

Does not change the source application's launch configuration or require CUDA.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Parking Windows CPU demo")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--runtime-dir", type=Path)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in 1024..65535")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    frozen = getattr(sys, "frozen", False)
    resources = Path(sys._MEIPASS) if frozen else Path(__file__).resolve().parents[1]
    application_dir = Path(sys.executable).resolve().parent if frozen else resources
    if not frozen:
        sys.path.insert(0, str(resources))
    runtime = (args.runtime_dir or application_dir / "runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ["YOLO_CONFIG_DIR"] = str(runtime / "ultralytics")
    os.environ["MPLCONFIGDIR"] = str(runtime / "matplotlib")
    os.environ["MPLBACKEND"] = "Agg"
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_check:
            port_check.bind(("127.0.0.1", args.port))
    except OSError:
        print(f"端口 {args.port} 已被占用。请关闭之前的演示窗口，再次启动。", flush=True)
        if not args.no_browser:
            input("按 Enter 关闭此窗口……")
        return 2
    try:
        print("智慧停车演示版 v1.2  |  真实照片与二维停车流程", flush=True)
        print("正在加载本地 CPU 模型，首次启动请稍候……", flush=True)
        import torch
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        import uvicorn
        from parking_hu.detector import YoloDetector
        from parking_hu.service import ParkingService
        import parking_hu.api as api
        # Both source and frozen layouts keep static/models/reports at this root.
        api.ROOT = resources
        detector = YoloDetector(resources / "models" / "parking_yolov8n.pt", device="cpu")
        service = ParkingService(runtime, detector=detector)
        if not service.snapshot["valid"]:
            raise RuntimeError(service.snapshot.get("error") or "首次真实检测失败")
        print(f"真实 YOLO 检测已完成，使用 CPU。地址：{url}", flush=True)
        print("浏览器将自动打开。使用期间保留此窗口；关闭窗口或按 Ctrl+C 停止服务。", flush=True)
        print(f"真实停车场照片：{url}/real  |  二维停车与寻车流程：{url}", flush=True)
        print("真实照片和模型已随包提供；不需要账号、联网下载或显卡驱动。", flush=True)
        stopping = threading.Event()

        def open_when_ready():
            for _ in range(100):
                if stopping.wait(0.1):
                    return
                try:
                    with urllib.request.urlopen(url + "/api/state", timeout=0.5) as response:
                        if response.status == 200:
                            webbrowser.open(url + "/real")
                            return
                except OSError:
                    pass

        if not args.no_browser:
            threading.Thread(target=open_when_ready, daemon=True).start()
        try:
            uvicorn.run(api.create_app(service), host="127.0.0.1", port=args.port,
                        log_level="warning", loop="asyncio", http="h11", ws="none")
        finally:
            stopping.set()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        traceback.print_exc()
        print("启动未完成。请保留此窗口中的错误信息，并确认 ZIP 已完整解压。", flush=True)
        if not args.no_browser:
            input("按 Enter 关闭此窗口……")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
