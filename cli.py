"""
CLI entry point for the Remote Website Real-Time Automation System.
Provides unified commands to launch the Relay Server, Worker, or GUI Controller.
"""

from __future__ import annotations
import argparse
import asyncio
import os
import signal
import sys


def run_server(args: argparse.Namespace) -> None:
    """Start the FastAPI / Uvicorn WebSocket relay server."""
    import uvicorn
    from server.main import app

    if args.port:
        os.environ["PORT"] = str(args.port)
    if args.session_timeout is not None:
        os.environ["SESSION_TIMEOUT_SECONDS"] = str(args.session_timeout)

    port = args.port or int(os.environ.get("PORT", 8000))
    host = args.host

    print(f"[*] Starting Remote Website Server on {host}:{port}...")
    uvicorn.run(app, host=host, port=port, log_level="info")


def run_worker(args: argparse.Namespace) -> None:
    """Start a Worker process with persistent Chrome and WebSocket client."""
    from worker.worker import Worker

    worker_kwargs = {}
    if args.id:
        os.environ["WORKER_ID"] = args.id
        worker_kwargs["worker_id"] = args.id
    if args.server_url:
        os.environ["SERVER_WS_URL"] = args.server_url
        worker_kwargs["server_url"] = args.server_url
    if args.token:
        os.environ["WORKER_TOKEN"] = args.token
        worker_kwargs["token"] = args.token
    if args.target_domain:
        os.environ["TARGET_DOMAIN"] = args.target_domain
        worker_kwargs["target_domain"] = args.target_domain
    if args.headed:
        os.environ["HEADLESS"] = "false"

    worker = Worker(**worker_kwargs)

    async def _async_main() -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def _sig_handler():
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _sig_handler)
            except NotImplementedError:
                pass  # Windows signal handling

        print(f"[*] Starting Worker '{worker.worker_id}' connecting to {worker.server_url}...")
        await worker.start()
        try:
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            print(f"[*] Shutting down Worker '{worker.worker_id}'...")
            await worker.stop()

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


def run_controller(args: argparse.Namespace) -> None:
    """Start the PySide6 Controller GUI."""
    from PySide6.QtWidgets import QApplication
    from controller.main_window import MainWindow

    server_url = args.server_url or os.environ.get("CONTROLLER_WS_URL", "ws://127.0.0.1:8000/ws/controller")
    token = args.token or os.environ.get("CONTROLLER_TOKEN", "default-controller-token-secret")

    app = QApplication(sys.argv)
    app.setApplicationName("RemoteWebsiteController")

    print(f"[*] Starting Controller GUI connecting to {server_url}...")
    window = MainWindow(
        server_url=server_url,
        token=token,
    )
    window.show()
    sys.exit(app.exec())


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="remote-website",
        description="Remote Website Real-Time Automation & DOM Synchronization Relay",
    )
    subparsers = parser.add_subparsers(dest="command", help="Component to launch", required=True)

    # Server subcommand
    server_parser = subparsers.add_parser("server", help="Launch the FastAPI / WebSocket Relay Server")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host address to bind (default: 0.0.0.0)")
    server_parser.add_argument("--port", type=int, default=None, help="Port to bind (default: from PORT or 8000)")
    server_parser.add_argument("--session-timeout", type=int, default=None, help="Session timeout in seconds")

    # Worker subcommand
    worker_parser = subparsers.add_parser("worker", help="Launch a Worker automation process")
    worker_parser.add_argument("--id", help="Explicit worker ID (default: saved .worker_id or random)")
    worker_parser.add_argument("--server-url", help="WebSocket server endpoint (default: ws://127.0.0.1:8000/ws/worker)")
    worker_parser.add_argument("--token", help="Worker authentication token")
    worker_parser.add_argument("--target-domain", help="Target domain URL")
    worker_parser.add_argument("--headed", action="store_true", help="Launch Chrome in visible (headed) mode")

    # Controller subcommand
    controller_parser = subparsers.add_parser("controller", help="Launch the PySide6 Controller GUI")
    controller_parser.add_argument("--server-url", help="WebSocket server endpoint (default: ws://127.0.0.1:8000/ws/controller)")
    controller_parser.add_argument("--token", help="Controller authentication token")

    args = parser.parse_args()

    if args.command == "server":
        run_server(args)
    elif args.command == "worker":
        run_worker(args)
    elif args.command == "controller":
        run_controller(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
