# Football (NFL, college): how we filter down to candidate pairs

Same approach as [baseball](../baseball/FILTERING.md): nothing about individual games or markets is hard-coded. What differs for football is below.

```bash
python -m candidates sports football/nfl     # writes data/candidate_pairs/sports/football/nfl/
python -m candidates sports football/cfb     # writes data/candidate_pairs/sports/football/cfb/
```

| File | What it holds |
|---|---|
| [bets.py](bets.py) | Every football bet type on each platform (NFL and college share them) |
| [games.py](games.py) | Game matching |
| [futures.py](futures.py), [run.py](run.py) | Futures matching; the run (window, status, outputs, report) |
| `nfl/`, `cfb/` | Each league's `config.py`, `team_aliases.json`, `template_approvals.json`, outputs |

## Step 1. One league at a time

NFL and college are separate folders, so an NFL market is never compared with a college one.

| League | Kalshi `competition` | Polymarket US series / slug prefix |
|---|---|---|
| NFL | `Pro Football` | `nfl` |
| College | `NCAA Football` | `cfb` |

## Step 2. Close window: 7 days

A pair is kept when the **earlier of its two closes** is within 7 days of the run (`CLOSES_WITHIN_DAYS` in each league's config).

| Platform | Close used | Note |
|---|---|---|
| Kalshi | `close_time` | Game markets close 48 hours after kickoff |
| Polymarket US | The event's end (end of the game day) | The market's own `endDate` is padded about two weeks |

Markets are first pre-filtered with a 3-day margin, then whole pairs are kept or dropped. So a game near the edge is in or out on both platforms together. Example: Kent State vs Bowling Green kicks off Nov 11; Polymarket US closes Nov 11 and Kalshi Nov 13.

## Step 3. Games

**Kickoff and grouping (Kalshi):** football tickers carry only the date (`KXNFLGAME-26NOV05JACBAL`). The collector pulls Kalshi's **sports milestones** with each catalog refresh. A milestone is one per game; its `start_date` is the kickoff Kalshi shows, and it lists every event of that game (winner, spread, total, player props…, 79 events for one NFL game). Stored as `game_start_ms` and `game_id` in T1. Kalshi events are grouped into games by `game_id`. Without milestones (an older catalog), grouping falls back to ticker date + teams.

**Game pairing:** same league, kickoffs within 90 minutes (observed: 0), and both teams resolved:

| Evidence | Strength | Example |
|---|---|---|
| `team_aliases.json` | strong | |
| Same code or display code | strong | `BAL` / `bal`, `LOU` / `LOU` |
| Same name | strong | "Louisville" / "Louisville" |
| Kalshi name is the start of the Polymarket US name | **weak** | "Jacksonville" / "Jacksonville Jaguars" |
| Elimination (the other team) | only from a strong match | Kalshi `ULL` = `loulaf` (Louisiana-Lafayette) |

Weak evidence can't anchor elimination: "Georgia" is the start of "Georgia Tech" and "North Carolina" of "North Carolina A&T". When several Kalshi games fit, the one with the strongest evidence wins. College names are normalised ("Ohio St." = "Ohio State").

Team codes come from the full-game winner markets only. Other Kalshi markets carry codes that aren't teams (`BALMANDREWS`, `TIE`, `NONE`), and `NO` is New Orleans, not "no team".

## Step 4. Markets

| Kind | Examples | Pairs when |
|---|---|---|
| Team pick | Game / half / quarter winner (incl. tie), first team to score a TD, race to N points (incl. neither) | Same team, tie or none. Polymarket US one-market moneyline: NO team pairs as **opposite** |
| Team line | Spread, team points, 1st/2nd half team points, team field goals, team yards | Same team and line. Spreads as "team wins by more than X": Polymarket US "Jaguars cover +20.5" = Kalshi NO "Ravens win by over 20.5" (**opposite**) |
| Game line | Total, half and quarter totals | Same line |
| Yes/no | Tie, overtime, safety, D/ST touchdown, both teams score in quarter N | Same bet (Polymarket US line must be 0.5) |
| Player line | Passing / rushing / receiving / scrimmage yards, receptions, TDs, passing TDs, attempts, completions, interceptions, longest reception / rush | Same player and same N. Kalshi "greater than 9.5" = 10+; Polymarket US "10+" |
| Player pick | First TD scorer, team's first TD scorer (incl. "no TD"), most passing yards | Same player |

Kalshi's "(Protected)" player markets are a separate product with their own rules. They pair separately with the same Polymarket US market, as a separate template pair.

**Recognised, not matched yet** (counted in the report with a reason): fantasy points (scoring differs), exact winning margin vs margin bands, halftime/full-time, Kalshi ranges, ladders and escalators, total TDs, team sacks, college team passing/rushing stats.

**Polymarket US `title` can describe the NO side.** On spreads, "Will the Jacksonville Jaguars cover 20.5" has title "Baltimore Ravens wins by over 20.5 points", while its rules say YES = Jaguars cover +20.5. Matching uses the sides and the rules, never the title, and `polyus_title` shows the question.

## Results on the 2026-11-05 catalog (window Nov 5 23:10 to Nov 12 23:10 UTC)

| | NFL | College |
|---|---|---|
| Open markets K / P | 24,600 / 19,029 | 22,012 / 22,393 |
| Naive pairs (window) | 150 million | 225 million |
| Games paired | 16 of 16 | 127 (every Polymarket US game in the window) |
| Market pairs | 7,510 (7,002 same, 508 opposite) | 5,604 (4,137 same, 1,467 opposite) |
| Template pairs to review | 82 | 41 |
| Unknown / unusable markets | 0 / 0 | 0 / 0 |

Futures: none of either league's futures close within 7 days on Nov 5, so `FUTURES` is empty. Futures that fall in the window show up under "Not covered" (e.g. Polymarket US `nfl-nextteam-*`).
