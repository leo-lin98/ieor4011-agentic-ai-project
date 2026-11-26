"""Serve the dashboard on 127.0.0.1 (see package docstring)."""
import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .data import Store, records

HERE = Path(__file__).parent
MARKET_COLS = ["market_uid", "platform", "category", "sport", "league", "bet_type", "event_title", "market", "yes",
               "line", "close_ms", "game_start_ms", "in_window", "market_url"]
PAIR_COLS = ["pair_id", "source", "league", "map_key", "direction", "status", "event_time_ms", "pair_close_ms",
             "polyus_market_uid", "polyus_title", "polyus_yes", "kalshi_market_uid", "kalshi_title", "kalshi_yes",
             "match_key", "template_pair_id"]


def make_handler(store: Store):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/":
                body = (HERE / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/api/summary":
                m = store.markets
                return self._json({
                    "as_of": store.as_of, "loaded_ms": store.loaded_ms,
                    "markets": {p: int((m.platform == p).sum()) for p in ("KALSHI", "POLYUS")},
                    "in_window": {p: int(((m.platform == p) & m.in_window).sum()) for p in ("KALSHI", "POLYUS")},
                    "pairs": int(len(store.pairs)),
                    "pair_sources": sorted(store.pairs.source.unique().tolist()) if len(store.pairs) else [],
                })
            if u.path == "/api/markets":
                m = store.filter_markets(platform=qs.get("platform") or None, category=qs.get("category") or None,
                                         sport=qs.get("sport") or None, league=qs.get("league") or None,
                                         bet=qs.get("bet") or None, q=qs.get("q") or None,
                                         window=qs.get("window") == "1")
                page, size = int(qs.get("page", 1)), min(int(qs.get("size", 100)), 500)
                m = m.sort_values(["close_ms", "platform", "event_title"], na_position="last")
                rows = records(m.iloc[(page - 1) * size: page * size], MARKET_COLS)
                return self._json({"total": int(len(m)), "page": page, "size": size, "rows": rows,
                                   "by_platform": {p: int((m.platform == p).sum()) for p in ("KALSHI", "POLYUS")},
                                   "options": store.market_options(m)})
            if u.path == "/api/pairs":
                p = store.filter_pairs(source=qs.get("source") or None, league=qs.get("league") or None,
                                       direction=qs.get("direction") or None, status=qs.get("status") or None,
                                       map_key=qs.get("map_key") or None, q=qs.get("q") or None)
                page, size = int(qs.get("page", 1)), min(int(qs.get("size", 100)), 500)
                if len(p):
                    p = p.sort_values(["event_time_ms", "league", "match_key"], na_position="last")
                    opts = {c: [{"v": str(k), "n": int(n)} for k, n in p[c].value_counts().items()]
                            for c in ("source", "league", "direction", "status", "map_key")}
                    rows = records(p.iloc[(page - 1) * size: page * size], PAIR_COLS)
                else:
                    opts, rows = {}, []
                return self._json({"total": int(len(p)), "page": page, "size": size, "rows": rows, "options": opts})
            if u.path == "/api/templates":
                t = store.filter_templates(source=qs.get("source") or None, verdict=qs.get("verdict") or None,
                                           league=qs.get("league") or None, q=qs.get("q") or None)
                cols = ["template_pair_id", "source", "league", "map_key", "shape", "n_pairs", "verdict", "status",
                        "opposite_side_ok", "confidence", "reviewed_by", "prompt_version", "date", "human_review_question"]
                if len(t):
                    t = t.sort_values(["source", "n_pairs"], ascending=[True, False])
                    opts = {c: [{"v": str(k), "n": int(n)} for k, n in t[c].value_counts().items()]
                            for c in ("source", "verdict", "league")}
                    rows = records(t, cols)
                else:
                    opts, rows = {}, []
                return self._json({"total": int(len(t)), "rows": rows, "options": opts})
            if u.path == "/api/template":
                d = store.template_detail(qs.get("id", ""), qs.get("source", ""))
                return self._json(d if d else {"error": "unknown template pair"}, 200 if d else 404)
            if u.path == "/api/pair":
                d = store.pair_detail(qs.get("id", ""))
                return self._json(d if d else {"error": "unknown pair"}, 200 if d else 404)
            if u.path == "/api/reload":
                store.reload()
                return self._json({"ok": True, "as_of": store.as_of})
            self._json({"error": "not found"}, 404)

    return H


def main():
    ap = argparse.ArgumentParser(prog="dashboard")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    print("loading catalogs and candidate pairs ...", flush=True)
    store = Store()
    m = store.markets
    print(f"  markets: Kalshi {int((m.platform == 'KALSHI').sum()):,}  Polymarket US {int((m.platform == 'POLYUS').sum()):,}"
          f"   candidate pairs: {len(store.pairs):,}")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(store))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"dashboard at {url}  (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
