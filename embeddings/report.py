"""Render retrieval results as a self-contained, read-only HTML report."""
import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

def render_market(m):
    def esc(x): return html.escape(str(x or ""))
    url = m.get("market_url") or ""
    link = f'<a href="{esc(url)}">Market link</a>' if url.startswith(("https://", "http://")) else ""
    close = m.get("close_time_ms")
    date = datetime.fromtimestamp(close / 1000, timezone.utc).isoformat() if isinstance(close, (float, int)) else ""
    return f'<h3>{esc(m.get("title"))}</h3><p>{esc(m.get("market_title"))}</p><p><b>ID:</b> {esc(m.get("market_uid"))} · <b>Close:</b> {esc(date)} · {link}</p><p><b>YES:</b> {esc(m.get("yes_means"))}</p><details><summary>Full rules</summary><pre>{esc(m.get("rules"))}</pre></details>'

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("input"); p.add_argument("output"); a = p.parse_args()
    data = json.loads(Path(a.input).read_text())
    sections = []
    for item in data["results"]:
        candidates = "".join(f'<article><b>Similarity: {c["score"]:.4f}</b>{render_market(c["market"])}</article>' for c in item["candidates"])
        sections.append(f'<section>{render_market(item["query"])}<h4>Candidates</h4>{candidates}</section>')
    output = Path(a.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Market candidates</title><style>body{font:16px system-ui;max-width:1000px;margin:auto;padding:2rem}section{border-top:3px solid #444;padding:1rem 0}article{border:1px solid #ccc;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Market candidates</h1><p>Similarity is not a probability or verification of equivalence.</p>' + ''.join(sections) + '</html>')
    print(f"Wrote {output}")
if __name__ == "__main__": main()
