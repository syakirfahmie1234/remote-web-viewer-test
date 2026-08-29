"""
Worker Pool Manager module.
Manages a pool of concurrent, isolated Worker processes (3-5+ workers).
Each Worker instance operates an independent Chrome session, persistent WebDriver connection,
isolated DOM mutation observer, and discrete WebSocket channel to the server.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Dict, List, Optional, Union

from worker.config import SERVER_WS_URL, WORKER_TOKEN, TARGET_DOMAIN
from worker.worker import Worker

logger = logging.getLogger("worker.pool")


class WorkerPool:
    """
    Orchestrates a concurrent fleet of Worker instances.
    Guarantees strict isolation across all workers in the pool.
    """
    def __init__(
        self,
        worker_ids: Union[int, List[str]] = 3,
        server_url: str = SERVER_WS_URL,
        token: str = WORKER_TOKEN,
        target_domain: str = TARGET_DOMAIN,
    ) -> None:
        self.server_url = server_url
        self.token = token
        self.target_domain = target_domain
        self.workers: Dict[str, Worker] = {}

        if isinstance(worker_ids, int):
            ids = [f"worker-pool-{i+1:02d}" for i in range(worker_ids)]
        else:
            ids = list(worker_ids)

        for wid in ids:
            self.workers[wid] = Worker(
                worker_id=wid,
                server_url=self.server_url,
                token=self.token,
                target_domain=self.target_domain,
            )

    @property
    def count(self) -> int:
        """Total number of workers in pool."""
        return len(self.workers)

    def get_worker(self, worker_id: str) -> Optional[Worker]:
        """Get specific Worker instance by worker_id."""
        return self.workers.get(worker_id)

    def get_all_worker_ids(self) -> List[str]:
        """Return list of all worker IDs in the pool."""
        return list(self.workers.keys())

    async def start(self) -> None:
        """
        Start all workers in parallel.
        Launches isolated Chrome browser sessions and WebSocket connections.
        """
        logger.info(f"Starting WorkerPool with {len(self.workers)} workers: {list(self.workers.keys())}")
        tasks = [w.start() for w in self.workers.values()]
        await asyncio.gather(*tasks)
        logger.info("All pool workers started successfully")

    async def stop(self) -> None:
        """
        Cleanly stop all workers in parallel.
        Terminates WebSockets and closes isolated Chrome browsers.
        """
        logger.info(f"Stopping WorkerPool ({len(self.workers)} workers)...")
        tasks = [w.stop() for w in self.workers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All pool workers stopped")

    def is_healthy(self) -> bool:
        """Check if all Chrome browser sessions in the pool are alive."""
        return all(w.browser.is_alive() for w in self.workers.values())
