# Basketball (NBA): how we filter down to candidate pairs

Same approach as [football](../football/FILTERING.md): nothing about individual games or markets is hard-coded. What differs for basketball is below.

```bash
python -m candidates sports basketball/nba     # writes data/candidate_pairs/sports/basketball/nba/
```

| File | What it holds |
|---|---|
| [bets.py](bets.py) | Every basketball bet type on each platform (leagues share them) |
| [games.py](games.py) | Game matching |
| [futures.py](futures.py), [run.py](run.py) | Futures matching; the run (window, status, outputs, report) |
| `nba/` | The league's `config.py`, `team_aliases.json`, `template_approvals.json`, outputs |

## Step 1. One league at a time

Only the NBA for now. College (men's and women's) has the same bet types and would be `cbb/` and `wcbb/` folders.

| League | Kalshi `competition` | Polymarket US series / slug prefix | Open markets 2026-11-25 K / P |
|---|---|---|---|
| NBA | `Pro Basketball (M)` | `nba` | 17,235 / 7,659 |
| College (M) | `College Basketball (M)` | `cbb` | 15,426 / 5,157 (not built) |
| College (W) | `College Basketball (W)` | `wcbb` | 5,153 / 1,116 (not built) |

Kalshi's NBA volume is mostly non-game: AP Poll-style rankings, awards, futures, Next Team, KenPom. All close past the 7-day window and never reach the matcher.

## Step 2. Close window: 7 days

As football. Kalshi lists NBA games about 4 days ahead, Polymarket US about 8, so the report always shows Polymarket US games "with no Kalshi game" for the later dates. They pair on the next run once Kalshi lists them.

## Step 3. Games

**Tip-off and grouping (Kalshi):** basketball tickers carry only the date (`KXNBAGAME-26NOV25SASPOR`). As in football, Kalshi's sports milestones give `game_start_ms` and `game_id`; events are grouped into games by `game_id`.

**Polymarket US:** one event per game, slug `[aec-]nba-TEAM-TEAM-YYYY-MM-DD`, tip-off = `gameStartTime`. Only events with two teams in `event_teams` count as games: the league-wide daily leader markets (`nba-lead-pts-2026-11-25`, `nba-lead-3pm-…`) have a game-shaped slug but no teams, and go to "Not covered".

**Team codes differ** for several NBA teams: Kalshi `NYK` / Polymarket US `ny`, `PHX` / `pho`, `SAS` / `sa`, `GSW` / `gs`, `NOP` / `no`. These resolve by name ("New York" is the start of "New York Knicks"; both teams resolved this way is accepted because each matches exactly one team). All 30 Kalshi games paired on Nov 25 without aliases. `team_aliases.json` is there for the day a name changes.

## Step 4. Markets

| Kind | Examples | Pairs when |
|---|---|---|
| Team pick | Game / half / quarter winner (incl. tie), race to 10 / 20 / 30 points | Same team or tie. Polymarket US one-market moneyline: NO team pairs as **opposite** |
| Team line | Spread, team points, half team points | Same team and line. Polymarket US "Heat cover +27.5" = Kalshi NO "76ers win by over 27.5" (**opposite**) |
| Game line | Total, half and quarter totals | Same line |
| Yes/no | Overtime | Same bet (Polymarket US line 0.5) |
| Player line | Points, rebounds, assists, threes, steals, blocks, points + rebounds + assists | Same player and same N. Kalshi "19.5 greater" = 20+; Polymarket US "at least 20" |
| Player yes/no | Double-double, triple-double | Same player |

Kalshi's "(Protected)" player markets pair separately with the same Polymarket US market, as in football, so each Polymarket US player market usually gets two pairs.

**Recognised, not matched yet** (counted in the report): per-game Points Leader (Polymarket US only has a league-wide daily leader), Winning Margin (bands 1–5 / 6–10 / 11–20 / 21+ look identical on both platforms and should be checked next), `KXNBATOTALPRECISE` 1-point total ladders (confirm one-sided vs range), Steals + Blocks, escalators. Polymarket US "neither team reaches N" race markets have no Kalshi counterpart.

## Results on the 2026-11-25 catalog (window Nov 25 22:33 to Dec 2 22:33 UTC)

| | NBA |
|---|---|
| Open markets K / P | 17,235 / 7,659 |
| In window K / P | 10,840 / 6,284 (naive pairs 68 million) |
| Games paired | 30 of 30 Kalshi games (Polymarket US lists 64; the rest are later dates) |
| Market pairs | 6,288 (5,892 same, 396 opposite) |
| Template pairs to review | 86 |
| Unknown / unusable markets | 0 / 0 |

Most pairs are player lines (threes 920, rebounds 847, points 819, assists 615, PRA 550). Spreads 245, totals 128, moneylines 60.

Futures: none close within 7 days (awards, conference and finals markets), so `FUTURES` is empty.
