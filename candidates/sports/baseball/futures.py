"""Baseball futures (awards, championships): config.FUTURES rows, every season.

For each row: same season (Kalshi event ticker suffix, Polymarket US slug year), then the same
player (normalised name), team code (ticker suffix vs slug suffix) or team name.
"""
import re

from candidates import common as C

K_FUTURE_EVENT = re.compile(r"^[A-Z0-9]+-(\d{2})$")   # KXMLBNLMVP-26 -> 2026


def _entity_key(r, entity, platform):
    if entity == "team_code":
        ident = r.native_market_id if platform == "KALSHI" else r.market_slug
        code = ident.rsplit("-", 1)[-1] if isinstance(ident, str) else None
        return code.lower() if code else None
    return C.norm_name(r.yes_title if platform == "KALSHI" else r.market_title)


def match(rows, k, p, notes):
    """Returns (pair dicts, per-row report, Kalshi row indices covered, Polymarket US row indices covered)."""
    pairs, report, k_used, p_used = [], [], set(), set()
    for m in rows:
        kk = k[k.series_id == m["kalshi_series"]]
        pp = p[p.event_slug.str.match(m["polyus_event"], na=False)]
        k_used |= set(kk.index)
        p_used |= set(pp.index)

        k_by = {}
        for r in kk.itertuples():
            g = K_FUTURE_EVENT.match(r.native_event_id or "")
            if g:
                k_by.setdefault((2000 + int(g.group(1)), _entity_key(r, m["entity"], "KALSHI")), []).append(r)
            else:
                notes.append(f"{m['key']}: Kalshi {r.market_uid} event ticker has no season suffix; not paired")
        p_by = {}
        for r in pp.itertuples():
            if isinstance(r.yes_side_label, str) and r.yes_side_label.lower() != "yes":
                notes.append(f"{m['key']}: {r.market_uid} skipped, YES side is '{r.yes_side_label}' not 'Yes'")
                continue
            season = int(re.match(m["polyus_event"], r.event_slug).group(1))
            p_by.setdefault((season, _entity_key(r, m["entity"], "POLYUS")), []).append(r)

        event_pairs, paired_p = set(), set()
        for key, prs in p_by.items():
            krs = k_by.get(key, [])
            if key[1] is None or not krs:
                continue
            if len(prs) > 1 or len(krs) > 1:
                notes.append(f"{m['key']}: several markets for season={key[0]} {m['entity']}={key[1]}; not paired")
                continue
            kr, pr = krs[0], prs[0]
            pairs.append(C.pair_row(kr, pr, map_key=m["key"], league=m["league"], shape=m["shape"],
                                    match_key=f"season={key[0]}|{m['entity']}={key[1]}", direction="same",
                                    kalshi_yes=kr.yes_title, polyus_yes=pr.market_title))
            event_pairs.add((kr.event_uid, pr.event_uid))
            paired_p.add(pr.market_uid)

        report.append(dict(
            key=m["key"], kalshi_markets=len(kk), polyus_markets=len(pp), event_pairs=len(event_pairs),
            market_pairs=len(paired_p),
            polyus_unpaired=sorted(pp[~pp.market_uid.isin(paired_p)].market_title.fillna(pp.market_uid).tolist()),
            kalshi_templates=kk.template.nunique(), polyus_templates=pp.template.nunique()))
    return pairs, report, k_used, p_used
