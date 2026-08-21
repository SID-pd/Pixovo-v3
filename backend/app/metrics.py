import time
import functools
import asyncio
from typing import Dict, List, Any
from loguru import logger

class MetricsCollector:
    """In-memory collector for recording stage latencies and throughput stats."""
    _stage_timings: Dict[str, List[float]] = {}
    _recent_logs: List[Dict[str, Any]] = []

    @classmethod
    def record(cls, step_name: str, elapsed_ms: float, details: Dict[str, Any] = None):
        if step_name not in cls._stage_timings:
            cls._stage_timings[step_name] = []
        cls._stage_timings[step_name].append(elapsed_ms)
        # Keep last 100 measurements per step
        if len(cls._stage_timings[step_name]) > 100:
            cls._stage_timings[step_name].pop(0)

        cls._recent_logs.append({
            "step": step_name,
            "elapsed_ms": round(elapsed_ms, 2),
            "timestamp": time.time(),
            "details": details or {}
        })
        if len(cls._recent_logs) > 50:
            cls._recent_logs.pop(0)

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        stages = {}
        for name, times in cls._stage_timings.items():
            if times:
                stages[name] = {
                    "count": len(times),
                    "avg_ms": round(sum(times) / len(times), 2),
                    "min_ms": round(min(times), 2),
                    "max_ms": round(max(times), 2),
                    "last_ms": round(times[-1], 2)
                }
        return {
            "stages": stages,
            "recent_events": list(reversed(cls._recent_logs))
        }

def measure_step(name: str = None):
    """
    Metrics decorator that logs and records execution time for synchronous or asynchronous functions.
    """
    def decorator(func):
        step_name = name or func.__name__

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return await func(*args, **kwargs)
                finally:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"⏱️  [Metric] '{step_name}' took {elapsed_ms:.2f}ms")
                    MetricsCollector.record(step_name, elapsed_ms)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                t0 = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"⏱️  [Metric] '{step_name}' took {elapsed_ms:.2f}ms")
                    MetricsCollector.record(step_name, elapsed_ms)
            return sync_wrapper

    return decorator
