"""Local browser for reviewing candidate contracts and curating benchmarks."""

import logging
import random
import re
import secrets
from bisect import bisect_left, bisect_right
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from embeddings.catalog import RULE_DETAILS, rule_fingerprint

from . import reviews

log = logging.getLogger(__name__)
STATUS = {"match": "Match", "no_match": "No match", "stale": "Rules changed · review again",
          "unreviewed": "Unreviewed"}
SEARCH_PAGE_SIZE = 20
SEARCH_LIMIT = 160
SEARCH_WORD = re.compile(r"[^\W_]+")
STYLE = """
:root {color-scheme: light; font: 16px/1.5 system-ui, sans-serif; color: #20292b; background: #f4f5f2}
body {margin: 0 auto; max-width: 1360px; padding: 1.5rem}
header, nav, .controls {display: flex; flex-wrap: wrap; align-items: center; gap: .65rem 1rem}
header {justify-content: space-between; border-bottom: 1px solid #c8d1cd}
h1 {margin: .35rem 0 .85rem; font-size: 1.7rem}
h2, h3 {line-height: 1.25} h3 {margin-top: 0}
nav {margin: 1rem 0}
form {margin: .5rem 0} input, button, select {font: inherit; padding: .3rem .5rem; max-width: 100%}
button {background: white; color: inherit; border: 1px solid #5a7068; border-radius: .3rem; cursor: pointer}
.primary {background: #155946; color: white} a {color: #155946} :focus-visible {outline: 3px solid #236bb3}
.contracts {display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.25rem; align-items: start}
article, .panel {border: 1px solid #c8d1cd; border-radius: .5rem; padding: 1rem; background: white}
article {margin-bottom: 1rem} .query {position: sticky; top: 1rem; max-height: calc(100vh - 3.5rem); overflow: auto}
p, dd, code {overflow-wrap: anywhere} dt {font-weight: 650} dd {margin: 0 0 .6rem}
.rules {white-space: pre-wrap} .notice {padding: .65rem; background: #e4eee8}
.market-details > summary {min-height: 1.5rem; cursor: pointer; color: #155946}
.decision, .contract, #catalog-search {scroll-margin-top: 1rem}
#catalog-search {margin-bottom: 1rem}
#catalog-search li {margin-bottom: .5rem; overflow-wrap: anywhere}
@media (max-width: 800px) {.contracts {grid-template-columns: 1fr} .query {position: static; max-height: none}}
"""


def esc(value):
    return escape(str(value), quote=True)


def hidden(name, value):
    return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'


def url(**params):
    return "/?" + urlencode({key: value for key, value in params.items() if value})


def link(value, label):
    parsed = urlsplit(value)
    if parsed.scheme in ("https", "http") and parsed.netloc:
        return f'<a href="{esc(value)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'
    return ""


def search_words(value):
    return SEARCH_WORD.findall(value.casefold())


def catalog_matches(markets, keywords, ranked):
    query_words = search_words(keywords)
    terms = set(query_words)
    if not terms:
        return []
    phrase = " ".join(query_words)
    matches = []
    for uid, market in markets.items():
        if uid in ranked:
            continue
        fields = (uid, market.get("market_title"), market.get("yes_title"), market.get("event_title"))
        words = [search_words(value or "") for value in fields]
        if terms.issubset(word for field in words for word in field):
            phrase_match = any(f" {phrase} " in f" {' '.join(field)} " for field in words)
            matches.append((not phrase_match, uid))
    return [uid for _, uid in sorted(matches)]


def ranking_status(catalog, kalshi, polyus):
    excluded = catalog.exclusion(kalshi, polyus)
    labels = {
        "sports_mismatch": "Filtered by sports category",
        "polyus_not_open": "Polymarket US contract was inactive at snapshot time",
        "kalshi_not_open": "Kalshi contract was inactive at snapshot time",
        "polyus_category_conflict": "Polymarket US event has conflicting categories",
        "kalshi_category_conflict": "Kalshi event has conflicting categories",
    }
    return labels.get(excluded, excluded.replace("_", " ") if excluded else "outside top 10")


def market_card(market):
    platform = market["platform"]
    title = market.get("market_title") or market.get("market_question") or "Untitled contract"
    event = market.get("event_title") or "Unavailable"
    yes = market.get("yes_title") or market.get("yes_side_label") or "Unavailable"
    fields = [
        ("Contract ID", market.get("native_market_id") or market["market_uid"].split(":", 1)[1]),
        ("Market UID", market["market_uid"]), ("Event UID", market.get("event_uid")),
        ("Yes side label", market.get("yes_side_label")),
        ("No outcome", market.get("no_title")), ("No side label", market.get("no_side_label")),
    ]
    details = "".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in fields if value)
    rules = "".join(
        f'<dt>{esc(label)}</dt><dd class="rules">{esc(market[field])}</dd>'
        for label, field in RULE_DETAILS[platform] if market.get(field)
    )
    labels = {
        "market_url": "Kalshi series page" if platform == "KALSHI" else "Polymarket US event page",
        "contract_terms_url": "Kalshi series contract terms", "contract_url": "Kalshi series contract document",
    }
    links = " · ".join(filter(None, (link(market[field], label) for field, label in labels.items() if market.get(field))))
    rules_html = f"<dl>{rules}</dl>" if rules else "<p>No settlement text in this snapshot.</p>"
    links_html = f"<p>{links}</p>" if links else ""
    return (
        f"<h3>{esc(title)}</h3><dl><dt>Event title</dt><dd>{esc(event)}</dd>"
        f"<dt>Yes outcome</dt><dd>{esc(yes)}</dd></dl>"
        '<details class="market-details"><summary>More details</summary>'
        f"<dl>{details}</dl><h4>Settlement rules</h4>{rules_html}{links_html}</details>"
    )


class ReviewApp:
    def __init__(self, run, db_path):
        self.run = run
        self.db = db_path
        self.run_key = (run.id, run.metadata["created_at"])
        self.polyus_snapshot = run.metadata["catalog"]["platforms"]["POLYUS"]["sha256"]
        reviews.init_db(db_path)
        self.csrf = secrets.token_urlsafe(32)
        self.queries = sorted(run.catalog.groups["KALSHI"])
        random.Random(":".join(self.run_key)).shuffle(self.queries)
        self.positions = {uid: index for index, uid in enumerate(self.queries)}

    def find_uid(self, platform, entered):
        contract_id = entered.strip()
        if not contract_id:
            return ""
        uid = contract_id if contract_id.startswith(platform + ":") else f"{platform}:{contract_id}"
        if uid not in self.run.catalog.markets[platform]:
            raise ValueError(f"Contract ID {entered!r} is absent from this snapshot.")
        return uid

    def form(self, action, body, **params):
        fields = {"action": action, "csrf": self.csrf, **params}
        return f'<form method="post">{"".join(hidden(k, v) for k, v in fields.items())}{body}</form>'

    def query_controls(self, kalshi, benchmark, manual, absent, completed, **search_state):
        params = {"kalshi": kalshi["market_uid"], "benchmark": benchmark, "manual": manual, **search_state}
        completion = ""
        if completed:
            completion = '<p>Review complete for this run.</p>' + self.form(
                "review_complete", '<button name="complete" value="0">Reopen review</button>', **params,
            )
        elif not absent:
            completion = self.form(
                "review_complete", '<button name="complete" value="1">Mark review complete</button>', **params,
            )
        if absent:
            text = "<p>No corresponding Polymarket US market recorded for this snapshot.</p>"
            button = '<button name="absent" value="0">Clear no-market finding</button>'
        else:
            text = ""
            button = '<button name="absent" value="1">No corresponding Polymarket US market</button>'
        return (
            f'<div class="decision" id="query-review">{completion}'
            f'{text}{self.form("market_absence", button, **params)}</div>'
        )

    def pending_reviews(self, completed, absences):
        absent_markets = self.run.catalog.full_rows("KALSHI", {uid for uid, _ in absences})
        confirmed = {
            uid for uid, market in absent_markets.items() if (uid, rule_fingerprint(market)) in absences
        }
        done = completed | confirmed
        return [index for index, uid in enumerate(self.queries) if uid not in done]

    def candidate(self, kalshi, polyus, label, benchmark, manual, status, selected, **search_state):
        params = {
            "kalshi": kalshi["market_uid"], "polyus": polyus["market_uid"],
            "benchmark": benchmark, "manual": manual, **search_state,
        }
        controls = (
            '<div class="controls"><button class="primary" name="label" value="match">Match</button>'
            '<button name="label" value="no_match">No match</button></div>'
        )
        form = self.form("decision", controls, **params)
        if selected and selected["frozen_at"] is None and status == "match":
            form += self.form(
                "add_pair", '<div class="controls"><label><input type="checkbox" name="independent" value="1"> '
                'Found independently of retrieval</label><button>Add to benchmark</button></div>', **params,
            )
        return (
            f'<article class="contract" id="contract-{esc(polyus["market_uid"])}">'
            f'<p><strong>{esc(label)}</strong></p>{market_card(polyus)}'
            f'<div class="decision" id="review-{esc(polyus["market_uid"])}">'
            f'<p>Decision: <strong>{esc(STATUS[status])}</strong></p>{form}</div></article>'
        )

    def catalog_search(self, kalshi, benchmark, manual, ranked, keywords="", search_page=""):
        filters = (hidden("kalshi", kalshi) + hidden("benchmark", benchmark) + hidden("polyus", manual))
        form = (
            f'<form action="/#catalog-search" method="get" class="controls">{filters}'
            f'<label>Keywords <input type="search" name="keywords" maxlength="{SEARCH_LIMIT}" '
            f'value="{esc(keywords)}" required></label>'
            '<button>Search catalog</button></form>'
        )
        results = ""
        if keywords:
            markets = self.run.catalog.markets["POLYUS"]
            matches = catalog_matches(markets, keywords, ranked)
            if matches:
                page = int(search_page) if search_page.isascii() and search_page.isdecimal() and len(search_page) < 8 else 1
                last_page = (len(matches) - 1) // SEARCH_PAGE_SIZE + 1
                page = min(max(page, 1), last_page)
                start = (page - 1) * SEARCH_PAGE_SIZE
                end = min(start + SEARCH_PAGE_SIZE, len(matches))
                items = []
                for uid in matches[start:end]:
                    market = markets[uid]
                    title = market.get("market_title") or market.get("yes_title") or uid
                    event = market.get("event_title")
                    status = ranking_status(self.run.catalog, kalshi, uid)
                    target = url(kalshi=kalshi, benchmark=benchmark, polyus=uid, keywords=keywords, search_page=page)
                    target += "#contract-" + quote(uid, safe="")
                    event_html = f" · {esc(event)}" if event and event != title else ""
                    items.append(f'<li><a href="{esc(target)}">{esc(title)}</a>{event_html} · {esc(status)}</li>')
                pages = []
                for number, label in ((page - 1, "Previous results"), (page + 1, "More results")):
                    if 1 <= number <= last_page:
                        target = url(kalshi=kalshi, benchmark=benchmark, polyus=manual, keywords=keywords,
                                     search_page=number) + "#catalog-search"
                        pages.append(f'<a href="{esc(target)}">{label}</a>')
                results = (
                    f"<p>Showing {start + 1:,}–{end:,} of {len(matches):,} contracts</p>"
                    f"<ul>{''.join(items)}</ul>"
                    f'<nav aria-label="Catalog search results">{"".join(pages)}</nav>'
                )
            else:
                results = "<p>No additional contracts matched these keywords.</p>"
        expanded = " open" if keywords else ""
        return (
            f'<details class="panel" id="catalog-search"{expanded}>'
            '<summary>Find more Polymarket US contracts</summary>'
            '<p>Search the frozen catalog by title, Yes outcome, or contract ID.</p>'
            f"{form}{results}</details>"
        )

    def benchmark_panel(self, all_benchmarks, selected, kalshi, manual):
        name = selected["name"] if selected else ""
        options = ['<option value="">Select a benchmark</option>']
        for row in all_benchmarks:
            state = "frozen" if row["frozen_at"] else "draft"
            active = " selected" if row["name"] == name else ""
            options.append(f'<option value="{esc(row["name"])}"{active}>{esc(row["name"])} · {state}</option>')
        chooser = (
            f'<form method="get" class="controls">{hidden("kalshi", kalshi)}{hidden("polyus", manual)}'
            f'<label>Benchmark <select name="benchmark">{"".join(options)}</select></label>'
            '<button>Open</button></form>'
        )
        create = self.form(
            "create", '<div class="controls"><label>New benchmark <input name="name" required maxlength="100"></label>'
            '<button>Create</button></div>', kalshi=kalshi, manual=manual,
        )
        extra = ""
        if selected:
            state = "Frozen" if selected["frozen_at"] else "Draft"
            extra = f'<p>{state} · {selected["pair_count"]} pairs</p>'
            if not selected["frozen_at"] and selected["pair_count"]:
                extra += self.form("freeze", "<button>Freeze benchmark</button>",
                                   benchmark=name, kalshi=kalshi, manual=manual)
            with reviews.connection(self.db) as db:
                pairs = reviews.benchmark_pairs(db, name)
            if pairs:
                extra += '<ul>'
                for pair in pairs:
                    k, p = pair["kalshi_market_uid"], pair["polyus_market_uid"]
                    extra += f'<li><a href="{esc(url(kalshi=k, polyus=p, benchmark=name))}">{esc(k)} / {esc(p)}</a>'
                    if not selected["frozen_at"]:
                        extra += self.form("remove_pair", "<button>Remove pair</button>",
                                           benchmark=name, kalshi=kalshi, polyus=manual, manual=manual,
                                           pair_kalshi=k, pair_polyus=p)
                    extra += "</li>"
                extra += "</ul>"
        expanded = " open" if selected else ""
        return f'<details class="panel"{expanded}><summary>Benchmarks</summary>{chooser}{create}{extra}</details>'

    def render(self, query, error=None):
        benchmark = query.get("benchmark", "")
        search_state = {
            "keywords": query.get("keywords", "")[:SEARCH_LIMIT].strip(),
            "search_page": query.get("search_page", "")[:7],
        }
        all_benchmarks = reviews.benchmarks(self.db)
        selected = next((row for row in all_benchmarks if row["name"] == benchmark), None)
        completed, absences = reviews.review_progress(self.db, self.run_key, self.polyus_snapshot)
        pending = self.pending_reviews(completed, absences)
        entered = query.get("kalshi", self.queries[pending[0]] if pending else "")
        uid = manual = ""
        try:
            uid = self.find_uid("KALSHI", entered)
            manual = self.find_uid("POLYUS", query.get("polyus", ""))
        except ValueError as exc:
            error = str(exc)
        index = self.positions.get(uid, -1)
        previous = bisect_left(pending, index) - 1
        following = bisect_right(pending, index)
        navigation = [f'<span>{len(pending):,} remaining</span>']
        if previous >= 0:
            navigation.insert(0, f'<a href="{esc(url(kalshi=self.queries[pending[previous]], benchmark=benchmark))}">Previous</a>')
        if following < len(pending):
            navigation.append(f'<a href="{esc(url(kalshi=self.queries[pending[following]], benchmark=benchmark))}">Next</a>')
        ranked_html = ""
        query_html = ("<p>Enter a Kalshi contract ID to begin.</p>" if pending else
                      "<p>No Kalshi contracts remain in the review queue. Enter a contract ID to revisit it.</p>")
        if uid:
            kalshi = self.run.catalog.full_rows("KALSHI", [uid])[uid]
            ranked = self.run.results({uid})
            ranked.sort(key=lambda row: (-row["similarity_score"], row["polyus_market_uid"]))
            polyus_ids = {row["polyus_market_uid"] for row in ranked}
            markets = self.run.catalog.full_rows("POLYUS", polyus_ids | {manual})
            decisions = reviews.decisions(self.db, uid)
            absent = (uid, rule_fingerprint(kalshi)) in absences
            controls = self.query_controls(kalshi, benchmark, manual, absent, uid in completed, **search_state)
            query_html = f'<article class="query"><h2>Kalshi</h2>{market_card(kalshi)}{controls}</article>'
            for rank, row in enumerate(ranked, 1):
                polyus_uid = row["polyus_market_uid"]
                polyus = markets[polyus_uid]
                status = reviews.review_state(decisions.get(polyus_uid), kalshi, polyus)
                label = f'#{rank} · cosine {row["similarity_score"]:.6f}'
                ranked_html += self.candidate(kalshi, polyus, label, benchmark, manual, status, selected, **search_state)
            if not ranked_html:
                ranked_html = "<p>No ranked candidates for this contract.</p>"
            if manual and manual not in polyus_ids:
                polyus = markets[manual]
                status = reviews.review_state(decisions.get(manual), kalshi, polyus)
                label = f"Manual lookup · {ranking_status(self.run.catalog, uid, manual)}"
                ranked_html = self.candidate(kalshi, polyus, label, benchmark, manual, status, selected,
                                             **search_state) + ranked_html
            lookup = (
                f'<form method="get" class="controls">{hidden("kalshi", uid)}{hidden("benchmark", benchmark)}'
                f'<label>Polymarket US contract ID <input name="polyus" value="{esc(query.get("polyus", ""))}"></label>'
                '<button>Look up</button></form>'
            )
            search = self.catalog_search(uid, benchmark, manual, polyus_ids, **search_state)
            ranked_html = (
                '<section aria-label="Polymarket US candidates"><h2>Polymarket US</h2>'
                f'{lookup}{search}{ranked_html}</section>'
            )
        top = (
            f'<header><h1>Market review</h1><strong>{esc(self.run.id)}</strong></header>'
            f'<nav aria-label="Kalshi contracts">{"".join(navigation)}</nav>'
            f'<form method="get" class="controls">{hidden("benchmark", benchmark)}'
            f'<label>Kalshi contract ID <input name="kalshi" value="{esc(entered)}"></label><button>Go to contract</button></form>'
        )
        notice = f'<p class="notice" role="alert">{esc(error)}</p>' if error else ""
        panel = self.benchmark_panel(all_benchmarks, selected, uid, manual)
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width, initial-scale=1"><title>Market review</title>'
            f'<style>{STYLE}</style><body>{top}{notice}{panel}<main class="contracts">'
            f'{query_html}{ranked_html}</main></body></html>'
        )

    def submit(self, data):
        if not secrets.compare_digest(data.get("csrf", "").encode(), self.csrf.encode()):
            raise ValueError("Reload the review page before submitting a form.")
        action = data.get("action")
        name = data.get("benchmark", "")
        anchor = ""
        if action == "create":
            name = data.get("name", "").strip()
            reviews.create_benchmark(self.db, name)
        elif action == "freeze":
            reviews.freeze_benchmark(self.db, name)
        elif action == "remove_pair":
            reviews.remove_benchmark_pair(self.db, name, data["pair_kalshi"], data["pair_polyus"])
        elif action == "review_complete":
            uid = self.find_uid("KALSHI", data.get("kalshi", ""))
            if not uid or data.get("complete") not in ("0", "1"):
                raise ValueError("Choose a Kalshi contract and a completion action.")
            reviews.set_review_complete(self.db, self.run_key, uid, data["complete"] == "1")
            anchor = "#query-review"
        elif action == "market_absence":
            uid = self.find_uid("KALSHI", data.get("kalshi", ""))
            if not uid or data.get("absent") not in ("0", "1"):
                raise ValueError("Choose a Kalshi contract and a no-market action.")
            kalshi = self.run.catalog.full_rows("KALSHI", [uid])[uid]
            reviews.set_market_absence(self.db, kalshi, self.run.catalog, self.polyus_snapshot, data["absent"] == "1")
            anchor = "#query-review"
        elif action in ("decision", "add_pair"):
            uid = self.find_uid("KALSHI", data.get("kalshi", ""))
            candidate = self.find_uid("POLYUS", data.get("polyus", ""))
            if not uid or not candidate:
                raise ValueError("Choose a pair before submitting a decision.")
            kalshi = self.run.catalog.full_rows("KALSHI", [uid])[uid]
            polyus = self.run.catalog.full_rows("POLYUS", [candidate])[candidate]
            if action == "decision":
                reviews.save_decision(self.db, kalshi, polyus, data.get("label"), self.polyus_snapshot)
            else:
                reviews.add_benchmark_pair(self.db, name, kalshi, polyus, data.get("independent") == "1")
            anchor = "#review-" + quote(candidate, safe="")
        else:
            raise ValueError("Unknown review action.")
        return url(kalshi=data.get("kalshi"), polyus=data.get("manual"), benchmark=name,
                   keywords=data.get("keywords"), search_page=data.get("search_page")) + anchor


def review_handler(app):
    def params(raw):
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    class Handler(BaseHTTPRequestHandler):
        def page(self, status, html):
            body = html.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            hosts = {f"{host}:{self.server.server_port}" for host in ("127.0.0.1", "localhost")}
            if self.headers.get("Host") not in hosts:
                self.send_error(HTTPStatus.FORBIDDEN)
                return False
            if urlsplit(self.path).path != "/":
                self.send_error(HTTPStatus.NOT_FOUND)
                return False
            return True

        def do_GET(self):
            if self.allowed():
                self.page(HTTPStatus.OK, app.render(params(urlsplit(self.path).query)))

        def do_POST(self):
            if not self.allowed():
                return
            data = {}
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 8192 or self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    raise ValueError("Invalid form submission.")
                data = params(self.rfile.read(length).decode())
                destination = app.submit(data)
            except (KeyError, ValueError) as exc:
                query = {"kalshi": data.get("kalshi", ""), "polyus": data.get("manual", ""),
                         "benchmark": data.get("benchmark", ""), "keywords": data.get("keywords", ""),
                         "search_page": data.get("search_page", "")}
                self.page(HTTPStatus.BAD_REQUEST, app.render(query, str(exc)))
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", destination)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def server_for(run, db_path, port):
    return ThreadingHTTPServer(("127.0.0.1", port), review_handler(ReviewApp(run, db_path)))
