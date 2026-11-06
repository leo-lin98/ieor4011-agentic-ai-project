"""Async HTTP client with an adaptive, priority-aware rate limiter (DECISIONS.md D7, D9).

Priorities: 0 = order books for flagged markets, 1 = pricing sweep, 2 = catalog / background.
A waiter only takes a token when no higher-priority request is waiting.
"""
import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import USER_AGENT
from .util import now_ms

log = logging.getLogger("collector.http")


class AdaptiveLimiter:
    """Token bucket whose rate halves on 429 and creeps back up on success (AIMD)."""

    def __init__(self, name, start, min_rate, max_rate, burst, increase=0.05):
        self.name = name
        self.rate = start
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.burst = burst
        self.increase = increase
        self.tokens = float(burst)
        self.last = time.monotonic()
        self.waiting = [0, 0, 0]

    async def acquire(self, priority=1):
        self.waiting[priority] += 1
        try:
            while True:
                if any(self.waiting[p] for p in range(priority)):
                    await asyncio.sleep(0.02)
                    continue
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)
        finally:
            self.waiting[priority] -= 1

    def on_success(self):
        self.rate = min(self.max_rate, self.rate + self.increase)

    def on_throttle(self):
        self.rate = max(self.min_rate, self.rate * 0.5)
        self.tokens = 0.0


@dataclass
class Resp:
    data: Any
    status: int
    sent_ms: int        # UTC wall clock when the request was sent
    recv_ms: int        # UTC wall clock when the response body was received
    nbytes: int


class Api:
    def __init__(self, name, base_url, limiter: AdaptiveLimiter, timeout=60.0, max_retries=8):
        self.name = name
        self.limiter = limiter
        self.max_retries = max_retries
        self.requests = 0
        self.throttles = 0
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
        )

    async def get(self, path, params=None, priority=1) -> Resp:
        delay = 0.5
        last_err = None
        for _ in range(self.max_retries):
            await self.limiter.acquire(priority)
            sent = now_ms()
            try:
                r = await self.client.get(path, params=params)
            except httpx.TransportError as e:
                last_err = e
                await asyncio.sleep(delay + random.random() * delay)
                delay = min(delay * 2, 16)
                continue
            recv = now_ms()
            self.requests += 1
            if r.status_code == 429 or r.status_code >= 500:
                if r.status_code == 429:
                    self.throttles += 1
                    self.limiter.on_throttle()
                last_err = f"HTTP {r.status_code}"
                await asyncio.sleep(delay + random.random() * delay)
                delay = min(delay * 2, 16)
                continue
            if r.status_code == 404:
                return Resp(None, 404, sent, recv, len(r.content))
            if r.status_code >= 400:
                raise RuntimeError(f"{self.name} GET {path} {params} -> {r.status_code}: {r.text[:300]}")
            self.limiter.on_success()
            return Resp(r.json(), r.status_code, sent, recv, len(r.content))
        raise RuntimeError(f"{self.name} GET {path} failed after {self.max_retries} attempts: {last_err}")

    async def aclose(self):
        await self.client.aclose()
