"""Arrow schemas for every table (DECISIONS.md D3). Every table carries market_uid.

Times are UTC epoch milliseconds (int64). Prices are cents (float64, 0-100).
"""
import pyarrow as pa

S, I, F, B = pa.string(), pa.int64(), pa.float64(), pa.bool_()


def _schema(cols):
    return pa.schema([pa.field(n, t) for n, t in cols])


# T0 - agent-facing view of T1: open markets only, same columns for both platforms.
MARKET_PROFILES = _schema([
    ("market_uid", S), ("platform", S), ("event_uid", S),
    ("category", S), ("subcategory", S), ("tags", S),
    ("title", S),                    # event title
    ("market_title", S),             # this outcome / market within the event
    ("yes_means", S),                # what a YES contract pays out on
    ("summary", S),                  # one line built from the titles (neither API has an "about" field)
    ("rules", S),                    # full resolution rules text, both platforms in one field
    ("open_time_ms", I), ("close_time_ms", I),
    ("time_until_close_hours", F),   # close_time - as_of, hours
    ("status", S), ("market_url", S), ("rules_hash", S),
    ("as_of_ms", I),                 # when time_until_close was computed
])

# T1 - one row per market, event fields denormalised onto each market row.
MARKET_CATALOG = _schema([
    ("market_uid", S), ("event_uid", S), ("platform", S),
    ("native_market_id", S), ("native_event_id", S), ("market_slug", S), ("event_slug", S),
    ("series_id", S), ("series_title", S),
    ("event_title", S), ("event_subtitle", S), ("market_title", S), ("market_question", S),
    ("yes_title", S), ("no_title", S), ("yes_side_label", S), ("no_side_label", S),
    ("market_type", S), ("category", S), ("subcategory", S), ("tags", S),
    # Structured bet description, used by candidate matching (added 2026-11-17)
    ("competition", S), ("competition_scope", S),   # Kalshi event product_metadata, e.g. "Pro Baseball" / "Spread"
    ("sports_market_type", S), ("line", F),         # Polymarket US, e.g. "baseball_team_full_game_spread" / -1.5
    ("yes_team_code", S), ("no_team_code", S),      # team on each side (Kalshi: from the ticker; Polymarket US: marketSides)
    ("event_teams", S),                             # Polymarket US event teams JSON [{code, display, name}]
    ("game_id", S),                                 # Kalshi: sports milestone id grouping a game's events (game_start_ms = its start)
    ("mutually_exclusive", B), ("collateral_return_type", S),
    ("strike_type", S), ("floor_strike", F), ("cap_strike", F), ("custom_strike", S),
    ("strike_date_ms", I), ("strike_period", S),
    ("open_time_ms", I), ("close_time_ms", I), ("expected_expiration_ms", I), ("latest_expiration_ms", I),
    ("occurrence_ms", I), ("game_start_ms", I), ("settlement_ts_ms", I),
    ("created_time_ms", I), ("updated_time_ms", I),
    ("event_start_ms", I), ("event_end_ms", I), ("event_closed_time_ms", I),
    ("can_close_early", B), ("early_close_condition", S),
    # Rules / resolution text (Kalshi)
    ("rules_primary", S), ("rules_secondary", S), ("settlement_sources", S),
    ("contract_terms_url", S), ("contract_url", S),
    # Rules / resolution text (Polymarket US)
    ("description", S), ("rules_disclaimer", S), ("event_description", S), ("resolution_source", S),
    # Status (all raw fields, DECISIONS.md D5)
    ("status_raw", S), ("status_canonical", S), ("result", S), ("settlement_value_c", F), ("expiration_value", S),
    ("ep3_status", S), ("is_active", B), ("is_closed", B), ("is_archived", B), ("is_hidden", B),
    ("event_active", B), ("event_closed", B), ("event_archived", B), ("event_live", B), ("event_ended", B),
    ("event_state", S),
    ("market_url", S),
    ("rules_hash", S), ("content_hash", S),
    ("first_seen_ms", I), ("last_seen_ms", I), ("catalog_pulled_ms", I),
])

# T2 - output of matching / verification. Active rows drive order-book collection.
MATCH_FLAGS = _schema([
    ("pair_id", S), ("kalshi_market_uid", S), ("polyus_market_uid", S),
    ("market_identical", B), ("relationship", S),
    ("proposer_explanation", S), ("judge_verdict", S), ("numeric_checks_passed", B),
    ("rules_review_status", S), ("human_review_status", S),
    ("kalshi_rules_hash", S), ("polyus_rules_hash", S),
    ("flagged_ts", I), ("updated_ts", I), ("is_active", B), ("source", S), ("note", S),
])

# T3 - top of book + per-market volume + status + all fee parameters, every tick (change-only).
PRICING_SNAPSHOTS = _schema([
    ("tick_ts", I), ("platform", S), ("market_uid", S),
    ("request_sent_ts", I), ("response_received_ts", I),
    ("yes_bid_c", F), ("yes_ask_c", F), ("no_bid_c", F), ("no_ask_c", F), ("last_price_c", F),
    ("yes_bid_size", F), ("yes_ask_size", F), ("price_derived", B),
    ("volume", F), ("volume_24h", F), ("open_interest", F),
    ("status_raw", S), ("status_canonical", S),
    ("fee_type", S), ("fee_multiplier", F), ("fee_type_override", S), ("fee_multiplier_override", F),
    ("fee_coefficient", F), ("tick_size_c", F), ("min_order_qty", F), ("price_level_structure", S),
    ("write_reason", S),
])

# T4 - full depth for flagged markets, one row per price level.
ORDERBOOK_SNAPSHOTS = _schema([
    ("tick_ts", I), ("platform", S), ("market_uid", S),
    ("request_sent_ts", I), ("response_received_ts", I), ("exchange_ts", I),
    ("outcome", S), ("side", S), ("level", I), ("price_c", F), ("qty", F), ("derived", B),
    ("book_hash", S), ("book_state", S),
])

# T5 - platform-wide counts per tick.
PLATFORM_COUNTS = _schema([
    ("tick_ts", I), ("platform", S),
    ("open_events", I), ("open_markets", I), ("markets_seen", I),
    ("flagged_pairs_active", I), ("flagged_pairs_open_both", I),
    ("sweep_requests", I), ("sweep_throttles", I), ("sweep_seconds", F), ("sweep_mb", F), ("rows_written", I),
])

# T6 - per-market trade stats Polymarket US only exposes per market (/book, /bbo):
# volume (shares traded), notional, open interest, last trade, daily OHLC, depth.
MARKET_STATS = _schema([
    ("tick_ts", I), ("platform", S), ("market_uid", S), ("source", S),
    ("request_sent_ts", I), ("response_received_ts", I), ("exchange_ts", I), ("state", S),
    ("best_bid_c", F), ("best_ask_c", F), ("current_px_c", F),
    ("last_trade_c", F), ("last_trade_qty", F), ("settlement_px_c", F),
    ("open_px_c", F), ("high_px_c", F), ("low_px_c", F), ("close_px_c", F),
    ("shares_traded", F), ("notional_traded", F), ("open_interest", F),
    ("bid_depth", I), ("ask_depth", I), ("bid_shares", F), ("ask_shares", F),
])
