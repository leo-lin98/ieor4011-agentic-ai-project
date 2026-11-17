"""Create a self-contained HTML report for reviewing retrieval results.

The report formats each query and candidate with contract details, similarity
scores, and review controls, escaping profile text before placing it in HTML. It
outputs an HTML page that supports inspecting and recording review decisions.
"""
import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def escape(value):
    return html.escape(str(value if value is not None else ""), quote=True)


# Format each contract for inspection, escaping profile text before inserting HTML.
def render_market(m):
    title = m.get("title") or "Untitled event"
    market = m.get("market_title") or "Market title unavailable"
    url = m.get("market_url") or ""
    link = f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">Open market ↗</a>' if url.startswith(("https://", "http://")) else ""
    close = m.get("close_time_ms")
    try:
        date = datetime.fromtimestamp(close / 1000, timezone.utc).strftime("%b %d, %Y %H:%M UTC") if isinstance(close, (float, int)) else "Not available"
    except (ValueError, OverflowError, OSError):
        date = "Not available"
    rules = m.get("rules") or "No rules available."
    return (f'<div class="event">{escape(title)}</div><h3>{escape(market)}</h3>'
            f'<div class="meaning"><span class="label">YES means</span><p>{escape(m.get("yes_means") or "Not available")}</p></div>'
            f'<dl><div><dt>Closes</dt><dd>{escape(date)}</dd></div><div><dt>Market ID</dt><dd class="id">{escape(m.get("market_uid") or "Not available")}</dd></div></dl>'
            f'<details><summary>Full rules</summary><pre>{escape(rules)}</pre></details><div class="link">{link}</div>')


def render_event(event):
    options = "".join(f'<li>{render_market(c)}</li>' for c in event.get("contracts", []))
    volume = event.get('volume_summary')
    volume_line = (f'<p>Kalshi total contracts traded: {volume["total"]:,.0f} ({volume["observed"]}/{volume["contracts"]} contracts have data)</p>' if volume and volume['total'] is not None else ('<p>Kalshi total contracts traded: unavailable</p>' if volume else ''))
    return (f'<h3>{escape(event.get("title") or "Untitled event")}</h3>'
            f'<p>Event ID: <span class="id">{escape(event.get("event_uid"))}</span> · '
            f'{event.get("option_count", 0)} options</p>{volume_line}'
            f'<details><summary>Inspect contract options</summary><ol>{options}</ol></details>')


# Provide separate review outcomes for an event and for each candidate pair.
def review_controls(query_id, candidate_id=None):
    query = escape(query_id)
    candidate = escape(candidate_id or "")
    if candidate_id is None:
        options = '<option value="">Not reviewed</option><option value="no_match_found">No match found among displayed suggestions</option>'
        label = 'Review outcome for this Kalshi event'
    else:
        options = '<option value="">Not reviewed</option><option value="matching">Matching events</option><option value="nonmatching">Not matching</option><option value="uncertain">Uncertain</option>'
        label = 'Review this event pair'
    return (f'<div class="review" data-query="{query}" data-candidate="{candidate}">'
            f'<label>{label}<select class="review-relation">{options}</select></label>'
            '<label>Notes <textarea class="review-notes" rows="2" placeholder="What did you check?"></textarea></label>'
            '<span class="review-status" role="status"></span></div>')

# Assemble the candidate cards and embed the review interface in one HTML page.
def render(data):
    meta = data.get("metadata", {})
    sections = []
    event_lookup = {e["event_uid"]: e for e in meta.get("candidate_events", [])}
    for number, item in enumerate(data["results"], 1):
        cards = "".join(f'<article class="candidate"><div class="card-top"><span>#{i}</span><span class="score">Event similarity {c["score"]:.3f}</span></div>{render_event(c.get("event") or event_lookup[c["event_uid"]])}{review_controls(item["query"]["event_uid"], (c.get("event") or event_lookup[c["event_uid"]])["event_uid"])}</article>' for i, c in enumerate(item["candidates"], 1))
        rail = f'rail-{number}'
        sections.append(f'<section class="query-section" id="query-{number}"><div class="section-heading"><span class="eyebrow">KALSHI EVENT {number:02}</span><span>{len(item["candidates"])} candidates</span></div><div class="query-card">{render_event(item["query"])}{review_controls(item["query"]["event_uid"])}</div><div class="rail-heading"><div><h2>Polymarket events</h2><p>Event similarity ranks candidates; inspect contracts separately.</p></div><div class="controls"><button type="button" data-rail="{rail}" data-direction="-1" aria-label="Scroll candidates left">←</button><button type="button" data-rail="{rail}" data-direction="1" aria-label="Scroll candidates right">→</button></div></div><div class="rail" id="{rail}" tabindex="0" role="region" aria-label="Candidates for Kalshi event {number}">{cards or "<p>No eligible candidates.</p>"}</div></section>')
    pool = len(meta.get("candidate_ids", []))
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Event candidate review</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color:#19243a;background:#f3f5f9;line-height:1.5}*{box-sizing:border-box}body{margin:0}main{max-width:1440px;margin:auto;padding:40px 32px 80px}header{margin-bottom:32px}h1{font-size:clamp(2rem,4vw,3rem);letter-spacing:-.04em;margin:4px 0}header p,.rail-heading p{color:#59677c;margin:4px 0}.eyebrow,.label{font-size:.72rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:#526580}.notice{background:#e8efff;border-left:4px solid #4269c8;padding:14px 18px;border-radius:6px;margin-top:20px;color:#263f70}.query-section{background:white;border:1px solid #dfe5ee;border-radius:18px;padding:26px;margin:24px 0;box-shadow:0 5px 22px #263c5910}.section-heading,.card-top,.rail-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.section-heading{font-size:.8rem;color:#63738a;margin-bottom:14px}.query-card{background:#f1f5fb;border:1px solid #e0e8f3;border-radius:12px;padding:20px}.event{font-size:.85rem;color:#586d8a;font-weight:650}h3{font-size:1.12rem;line-height:1.3;margin:6px 0 15px;overflow-wrap:anywhere}.meaning{border-left:3px solid #6685d5;padding-left:12px}.meaning p{margin:4px 0 0;overflow-wrap:anywhere}dl{display:flex;flex-wrap:wrap;gap:12px 28px;margin:18px 0 12px}dt{font-size:.72rem;text-transform:uppercase;color:#728096;font-weight:700}dd{margin:2px 0 0;font-size:.85rem;overflow-wrap:anywhere}.id{font-family:monospace}details{font-size:.88rem}summary{cursor:pointer;color:#315ba9;font-weight:650}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;color:#394a61;max-height:300px;overflow:auto}.link{margin-top:12px;font-size:.85rem}a{color:#315ba9}.rail-heading{margin:26px 0 12px}.rail-heading h2{font-size:1.15rem;margin:0}.rail-heading p{font-size:.85rem}.controls{display:flex;gap:8px}button{border:1px solid #ccd6e4;background:white;border-radius:9px;width:40px;height:40px;font-size:1.2rem;cursor:pointer}button:hover{background:#edf2fa}button:focus-visible,.rail:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #4776dc;outline-offset:3px}.rail{display:flex;gap:14px;overflow-x:auto;scroll-snap-type:x proximity;padding:4px 3px 18px}.candidate{flex:0 0 min(370px,85vw);scroll-snap-align:start;border:1px solid #dce3ed;border-radius:12px;padding:18px;background:#fff;min-width:0}.card-top{font-size:.8rem;font-weight:700;color:#68778d;margin-bottom:18px}.score{background:#eaf0ff;color:#3457a2;padding:5px 9px;border-radius:20px}.candidate dl{display:block}.candidate dl div+div{margin-top:8px}@media(max-width:600px){main{padding:24px 14px}.query-section{padding:18px}.controls{display:none}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
.review{margin-top:18px;padding-top:14px;border-top:1px solid #ccd6e4;display:grid;gap:10px}.review label,.review-settings label{display:grid;gap:4px;font-weight:650;font-size:.85rem}.review select,.review textarea,.review-settings input,.review-settings select{font:inherit;padding:8px;border:1px solid #9baabe;border-radius:6px;width:100%;background:white}.review-status{font-size:.8rem;color:#526580}.review-settings{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:18px 0}.review-settings button,.review-settings .import-button{width:auto;height:auto;font:inherit;padding:8px 12px;border:1px solid #9baabe;border-radius:6px;background:white;cursor:pointer}.review-settings label{min-width:160px}.review-settings input[type=file]{max-width:220px}#storage-status{color:#913521;font-weight:650}
</style></head><body><main><header><span class="eyebrow">Retrieval review</span><h1>Event candidates</h1><p>Compare each Kalshi event with its nearest Polymarket events. Expand options to review contracts.</p><div class="notice">Similarity is a ranking score, not a probability or proof of equivalence. The run searched ''' + str(pool) + ''' candidate events; relevant events outside that pool cannot appear.</div><div class="review-settings"><label>Reviewer <input id="reviewer" type="text" placeholder="Your name"></label><label>Split <select id="split"><option value="">Unspecified</option><option value="development">Development</option><option value="test">Test</option></select></label><button type="button" id="export-reviews">Download reviews CSV</button><label>Import reviews CSV <input id="import-reviews" type="file" accept=".csv,text/csv"></label></div><p>Reviews are saved in this browser on this report page. Download a CSV backup; import a CSV to continue reviewing or combine pages. Event groups default to the Kalshi event ID; edit related groups in the CSV before evaluation.</p><p id="storage-status" role="status" aria-live="polite"></p></header>''' + ''.join(sections) + '''</main><script>''' + (Path(__file__).parent / 'review.js').read_text(encoding='utf-8') + '''</script></body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input")
    p.add_argument("output")
    a = p.parse_args()
    data = json.loads(Path(a.input).read_text())
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
