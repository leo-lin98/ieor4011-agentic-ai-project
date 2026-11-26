"""Local dashboard over the collector's catalog and the candidate pairs.

    python -m dashboard            # http://127.0.0.1:8787
    python -m dashboard --port 9000 --no-browser

Tab 1  Markets     every open market on both platforms (Kalshi green, Polymarket US blue), with filters
Tab 2  Candidates  candidate pairs side by side: Polymarket US left, Kalshi right; click a row for both rulebooks

Reads data/market_catalog/current/*.parquet and data/candidate_pairs/**/candidate_pairs.parquet. Nothing
leaves the machine; nothing is written.
"""
