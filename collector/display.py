"""Build data/display/*.ipynb: one static notebook per table.

Each notebook concatenates that table's parquet files into one DataFrame and displays
15 rows per platform. Notebooks are executed at build time so outputs are saved in the file.

  python -m collector display
"""
import nbformat
from nbclient import NotebookClient
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from .config import DATA

DISPLAY = DATA / "display"
ROWS = 15

TABLES = [
    ("T0_market_profiles", "market_profiles", "Market profiles (agent input)",
     "One row per OPEN market with the same columns on both platforms: title, summary, rules, category, "
     "time until close. Built from T1 after every catalog refresh. This is the first table the agentic "
     "matching workflow reads.",
     'files = sorted(DATA.glob("market_profiles/*.parquet"))'),
    ("T1_market_catalog", "market_catalog", "Market catalog",
     "One row per market (event fields copied onto each market). Latest version of every market ever seen.",
     'files = sorted(DATA.glob("market_catalog/current/*.parquet"))'),
    ("T2_match_flags", "match_flags", "Match flags",
     "One row per Kalshi/Polymarket US pair. Active pairs drive order-book collection.",
     'files = sorted(DATA.glob("match_flags/*.parquet"))'),
    ("T3_pricing_snapshots", "pricing_snapshots", "Pricing snapshots",
     "Top of book, per-market volume, status and fees. One file per platform per 60s tick; rows are change-only.",
     'files = sorted(DATA.glob("pricing_snapshots/*/*/*.parquet"))'),
    ("T4_orderbook_snapshots", "orderbook_snapshots", "Order book snapshots",
     "Full depth for flagged markets, one row per price level. One file per platform per book tick.",
     'files = sorted(DATA.glob("orderbook_snapshots/*/*/*.parquet"))'),
    ("T5_platform_counts", "platform_counts", "Platform counts",
     "Open events/markets per platform per tick, plus sweep cost.",
     'files = sorted(DATA.glob("platform_counts/*/*/*.parquet"))'),
    ("T6_market_stats", "market_stats", "Market stats (Polymarket US)",
     "Per-market volume, notional, open interest and last trade from /book and /bbo.",
     'files = sorted(DATA.glob("market_stats/*/*/*.parquet"))'),
]

SETUP = '''import sys
from pathlib import Path
import pandas as pd

DATA = Path.cwd().parent                 # this notebook lives in data/display/
sys.path.insert(0, str(DATA.parent))     # repo root, for collector.schemas
from collector import schemas

pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 60)
pd.set_option("display.width", 250)'''

CONCAT = '''{glob}
print(f"{{len(files)}} file(s)")

if files:
    # numpy_nullable keeps integer timestamps as integers even when some are missing
    df = pd.concat([pd.read_parquet(f, dtype_backend="numpy_nullable") for f in files], ignore_index=True)
else:  # table not populated yet: show its columns
    df = schemas.{schema}.empty_table().to_pandas()

print("shape:", df.shape)'''

COLUMNS = '''print(f"{len(df.columns)} columns:")
for i, (col, dtype) in enumerate(df.dtypes.items(), 1):
    print(f"{i:3d}. {col:28s} {dtype}")'''

SHOW = '''if df.empty:
    print("No rows yet.")
    display(df)
elif "platform" in df.columns:
    for platform, g in df.groupby("platform"):
        print(f"{{platform}}: {{len(g):,}} rows (showing {rows})")
        display(g.head({rows}))
else:
    display(df.head({rows}))'''


BOOK_EXAMPLE_MD = """## Example: one live order book per platform

T4 only fills once pairs are flagged in `match_flags`. To show what it will look like, this cell picks
one liquid open market on each platform, pulls its order book **at notebook build time**, and converts it
with the collector's own code (`kalshi.book_rows` / `polyus.book_rows`), so the rows have exactly the T4 format.
Nothing here is written to `data/`.

- Kalshi returns bids only; YES asks are derived as 100 - NO bid (`derived = True`).
- Polymarket US returns YES bids/offers; the NO side is derived as 100 - YES."""

BOOK_EXAMPLE_PICK = '''import httpx
from collector import kalshi, polyus
from collector.config import KALSHI_BASE, POLYUS_BASE, USER_AGENT
from collector.http import Resp
from collector.util import now_ms

def latest_pricing(platform, cols):
    """Pricing rows are change-only: the latest state of a market is its last row."""
    fs = sorted(DATA.glob(f"pricing_snapshots/{platform}/*/*.parquet"))
    px = pd.concat([pd.read_parquet(f, columns=cols) for f in fs], ignore_index=True)
    return px.sort_values("tick_ts").groupby("market_uid").tail(1)

cols = ["tick_ts", "market_uid", "status_canonical", "yes_bid_c", "yes_ask_c"]

def mid_priced(df):
    """Open, two-sided quote, YES bid between 20c and 80c (a more interesting book than a 2c longshot)."""
    return df[(df.status_canonical == "open") & df.yes_bid_c.between(20, 80) & (df.yes_ask_c < 100)]

# Kalshi: mid-priced open market with the highest 24h volume
k = mid_priced(latest_pricing("KALSHI", cols + ["volume_24h"]))
k_uid = k.sort_values("volume_24h", ascending=False).market_uid.iloc[0]

# Polymarket US: mid-priced open market with the most shares traded (T6), else the tightest spread
p = mid_priced(latest_pricing("POLYUS", cols))
sfs = sorted(DATA.glob("market_stats/POLYUS/*/*.parquet"))
st = pd.concat([pd.read_parquet(f, columns=["market_uid", "shares_traded"]) for f in sfs]) if sfs else pd.DataFrame()
st = st[st.market_uid.isin(p.market_uid)] if len(st) else st
if len(st):
    p_uid = st.sort_values("shares_traded", ascending=False).market_uid.iloc[0]
else:
    p_uid = p.assign(spread=p.yes_ask_c - p.yes_bid_c).sort_values("spread").market_uid.iloc[0]

catalog = pd.concat([pd.read_parquet(DATA / f"market_catalog/current/{x}.parquet",
                                     columns=["market_uid", "event_title", "market_title", "market_question",
                                              "yes_title", "yes_side_label", "market_slug"])
                     for x in ("KALSHI", "POLYUS")])
display(catalog[catalog.market_uid.isin([k_uid, p_uid])])'''

BOOK_EXAMPLE_FETCH = '''def fetch(base, path):
    sent = now_ms()
    r = httpx.get(base + path, timeout=30, headers={"User-Agent": USER_AGENT})
    recv = now_ms()
    r.raise_for_status()
    return Resp(r.json(), r.status_code, sent, recv, len(r.content))

tick = (now_ms() // 1000) * 1000
kr = fetch(KALSHI_BASE, f"/markets/{k_uid.split(':', 1)[1]}/orderbook")
k_rows, _ = kalshi.book_rows(k_uid, kr.data, tick, kr)

slug = catalog.set_index("market_uid").market_slug[p_uid]
pr = fetch(POLYUS_BASE, f"/v1/markets/{slug}/book")
p_rows, _, _ = polyus.book_rows(p_uid, pr.data, tick, pr)

import pyarrow as pa
example = (pa.Table.from_pylist(k_rows + p_rows, schema=schemas.ORDERBOOK_SNAPSHOTS)
           .to_pandas(types_mapper={pa.int64(): pd.Int64Dtype()}.get))
order = {"yes": 0, "no": 1, "bid": 0, "ask": 1}
example = example.sort_values(["platform", "outcome", "side", "level"],
                              key=lambda s: s.map(order) if s.name in ("outcome", "side") else s,
                              ignore_index=True)
print("pulled at", pd.to_datetime(tick, unit="ms", utc=True))
for platform, g in example.groupby("platform"):
    print(f"{platform} {g.market_uid.iloc[0]}: {len(g)} level rows "
          f"(showing top 5 levels of each outcome/side)")
    display(g.groupby(["outcome", "side"], sort=False).head(5))'''

BOOK_EXAMPLE_LADDER = '''# Same data as a YES-side price ladder (best 10 levels): bids on the left, asks on the right
for platform, g in example.groupby("platform"):
    y = g[g.outcome == "yes"]
    bid = y[y.side == "bid"].set_index("level")[["qty", "price_c"]].add_prefix("bid_")
    ask = y[y.side == "ask"].set_index("level")[["price_c", "qty"]].add_prefix("ask_")
    print(platform, g.market_uid.iloc[0])
    display(bid.join(ask, how="outer").head(10))'''

EXTRA = {
    "T4_orderbook_snapshots": [
        new_markdown_cell(BOOK_EXAMPLE_MD),
        new_code_cell(BOOK_EXAMPLE_PICK),
        new_code_cell(BOOK_EXAMPLE_FETCH),
        new_code_cell(BOOK_EXAMPLE_LADDER),
    ],
}


def build_notebook(name, table, title, blurb, glob):
    nb = new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [
        new_markdown_cell(f"# {name.split('_')[0]} · `{table}`: {title}\n\n{blurb}\n\n"
                          f"See `DECISIONS.md` for the full column definitions. Re-generate with "
                          f"`python -m collector display`."),
        new_code_cell(SETUP),
        new_markdown_cell("## Build the table: concatenate its parquet files"),
        new_code_cell(CONCAT.format(glob=glob, schema=table.upper())),
        new_markdown_cell("## Columns"),
        new_code_cell(COLUMNS),
        new_markdown_cell(f"## View: {ROWS} rows per platform"),
        new_code_cell(SHOW.format(rows=ROWS)),
    ] + EXTRA.get(name, [])
    return nb


def build_all():
    DISPLAY.mkdir(parents=True, exist_ok=True)
    out = []
    for name, table, title, blurb, glob in TABLES:
        nb = build_notebook(name, table, title, blurb, glob)
        NotebookClient(nb, timeout=600, kernel_name="python3",
                       resources={"metadata": {"path": str(DISPLAY)}}).execute()
        path = DISPLAY / f"{name}.ipynb"
        nbformat.write(nb, path)
        out.append(path)
    return out
