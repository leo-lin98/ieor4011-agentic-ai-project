"""Static configuration. Everything tunable lives here or on the CLI."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["IEOR_COLLECTOR_DATA_DIR"]).resolve() if os.environ.get("IEOR_COLLECTOR_DATA_DIR") else ROOT / "data"

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
POLYUS_BASE = "https://gateway.polymarket.us"      # public market data
POLYUS_API_BASE = "https://api.polymarket.us"      # authenticated: account, portfolio, orders, WebSocket
USER_AGENT = "pm-arb-research-collector/0.1"

# Loop cadences (seconds)
PRICE_INTERVAL_S = 60       # pricing_snapshots + platform_counts
BOOK_INTERVAL_S = 60        # orderbook_snapshots for flagged markets
CATALOG_INTERVAL_S = 3600   # market_catalog refresh
HEARTBEAT_S = 900           # write an unchanged pricing row at least this often

# Adaptive rate limits (requests/second). Unauthenticated Kalshi reads are
# throttled well below the documented 20 req/s Basic tier (measured ~4 req/s
# before 429s), so start low and let the limiter climb.
KALSHI_RATE = dict(start=5.0, min_rate=1.0, max_rate=15.0, burst=5)
POLYUS_RATE = dict(start=15.0, min_rate=2.0, max_rate=20.0, burst=10)  # doc limit 25 req/s

# Background Polymarket US per-market volume sweep via /bbo (lowest priority)
POLYUS_STATS_SWEEP_RPS = 6.0
STATS_FLUSH_S = 300

# Kalshi sports milestones (game start times) pulled with each catalog refresh: games starting
# from this many hours ago to this many days ahead get game_start_ms / game_id in market_catalog.
MILESTONE_LOOKBACK_H = 24
MILESTONE_LOOKAHEAD_D = 8

# Raw JSON archive of catalog pulls: first catalog refresh of each UTC day
RAW_ARCHIVE_DAILY = True
