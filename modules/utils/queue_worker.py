import logging
import signal
from queue import Queue
from threading import Thread

from modules.models.collection_types import Collection
from modules.repositories.lootpool_repo import LootpoolRepository
from modules.repositories.market_repo import MarketRepository
from modules.repositories.raidpool_repo import RaidpoolRepository
from modules.repositories.usage_repo import UsageRepository

logger = logging.getLogger(__name__)

# ─── INTERNAL QUEUE & REPO MAPPING ─────────────────────────────────────────────
_request_queue = Queue()


# ─── WORKER LOOP ────────────────────────────────────────────────────────────────
def _worker_loop():
    while True:
        collection_type, item = _request_queue.get()
        if item is None:
            _request_queue.task_done()
            logger.info(f"Worker for {collection_type} shutting down")
            break

        repo = collection_type.repo
        if repo:
            try:
                repo.save(item)
                logger.debug(f"Saved item to {collection_type}")
            except Exception:
                logger.exception(f"Error saving to {collection_type}")
        else:
            logger.error(f"No repository configured for {collection_type!r}")
        _request_queue.task_done()


# ─── START UP WORKERS ──────────────────────────────────────────────────────────
_worker_thread = Thread(target=_worker_loop, daemon=True)
_worker_thread.start()
logger.info("Background queue worker started")


# ─── PUBLIC API ────────────────────────────────────────────────────────────────
def enqueue(collection_type: Collection, item: dict):
    _request_queue.put((collection_type, item))


def shutdown_workers():
    """
    1) Signal the worker loop to exit after it drains the queue.
    2) Wait for it to finish processing all enqueued items.
    3) Flush any buffered repos (e.g. UsageRepository).
    """
    logger.info("shutdown_workers() called, stopping worker thread…")

    # 1) tell the worker to stop once it's picked up everything
    _request_queue.put((None, None))

    # 2) wait for the worker to drain the queue and exit
    _worker_thread.join()
    logger.info("Worker thread has exited")

    # 3) now flush any in‑memory buffers
    for repo in _repo_map.values():
        flush = getattr(repo, "flush_all", None)
        if callable(flush):
            logger.info(f"Flushing buffer for repo {repo.__class__.__name__}")
            flush()

    logger.info("All queue workers have shut down and buffers flushed")
