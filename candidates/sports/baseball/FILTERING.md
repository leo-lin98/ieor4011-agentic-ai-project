# Baseball: how we filter down to candidate pairs

**Goal:** find every Kalshi market and Polymarket US market that is the same baseball bet, without comparing everything to everything, and without losing anything. The LLM only reviews what code cannot confirm.

**Nothing about individual games or markets is hard-coded.** Code knows how each platform names leagues, bet types and futures series, and reads seasons, dates, teams, players and lines from the data. Anything in baseball it does not recognise is printed in the report by name and count.

```bash
python -m candidates sports baseball             # match the current catalog, write data/candidate_pairs/sports/baseball/
python -m candidates sports baseball --no-write  # report only
python -m candidates.sports.baseball.selftest    # check known answers on real saved markets (run before each season)
```

| File | What it holds |
|---|---|
| [config.py](config.py) | Leagues, every baseball bet type on each platform, futures series. **The only file to edit when a platform adds something.** |
| [games.py](games.py) | Game matching |
| [futures.py](futures.py) | Futures matching (awards, championships) |
| [match.py](match.py) | Runs both, assigns status, writes outputs and the report |
| [team_aliases.json](team_aliases.json) | Team code pairs that nothing else can resolve (empty so far) |
| [template_approvals.json](template_approvals.json) | Reviewed rules-template pairs |
| [selftest.py](selftest.py), `fixtures/` | Real markets with known answers |

---

## Step 1. Open baseball markets

From T1 `market_catalog/current/` ([common.py](../../common.py) `load_open`): markets in the **latest catalog pull**, status `open`, close time after that pull, sport label = baseball ([sport_map.py](../sport_map.py)).

**Close window:** only pairs whose **earlier close is within 7 days of the run** (`CLOSES_WITHIN_DAYS` in [config.py](config.py)). Liquidity is concentrated near expiration. Close used ([common.py](../../common.py) `market_close_ms`): Kalshi `close_time` (game markets close 48 hours after the start); Polymarket US the event's end (end of the game day). The market's own `endDate` is padded about two weeks after a game. Markets are pre-filtered with a 3-day margin, then whole pairs are kept or dropped, so a game is in or out on both platforms together. The report shows what was kept and dropped.

## Step 2. Split into futures and games

| | Kalshi | Polymarket US |
|---|---|---|
| **Game** | Event ticker `SERIES-YYMONDDHHMMTEAMS[-N]`, e.g. `KXMLBSPREAD-26OCT282000MILTB`, `KXMLBINNINGWIN-26OCT282000MILTB-9` | Event slug `[aec-]LEAGUE-TEAM-TEAM-YYYY-MM-DD[-N]`, e.g. `mlb-mil-tb-2026-10-28` |
| **Futures** | Series in the `FUTURES` table | Event slug matches a `FUTURES` pattern |
| **Neither** | Listed in the report under "Not covered" | Listed in the report under "Not covered" |

## Step 3a. Futures

[config.py](config.py) `FUTURES` lists every futures bet that exists on both platforms: 10 awards (AL/NL × MVP, Cy Young, Rookie of the Year, Hank Aaron, Manager of the Year), the World Series and the KBO champion. **A row covers every season.** Kalshi reuses the series ticker (`KXMLBNLMVP-26`, `-27`, …) and Polymarket US reuses the slug pattern (`mlb-nl-<date>-mvp`).

| Step | Rule |
|---|---|
| Event pair | Same season: Kalshi ticker suffix (`-26` → 2026), year in the Polymarket US slug |
| Market pair | Same player (normalised name), team code (MLB: `KXMLB-27-SEA` ↔ `…-sea`, identical on both platforms), or team name (KBO) |
| Direction | Always same side |

Match on tickers and slugs, never titles. Kalshi's `KXMLBNLHAARON` (National League) is titled "…*American* League Hank Aaron".

## Step 3b. Games

### Which league
| League | Kalshi `competition` | Polymarket US slug prefix |
|---|---|---|
| MLB | `Pro Baseball` | `mlb` |
| KBO | `Korea KBO` | `kbo` |

A baseball game in any other league is reported under "Unknown leagues". Add one line to `LEAGUES`.

### Which game
Kalshi lists each bet type as its own event (`KXMLBGAME-…`, `KXMLBSPREAD-…`, `KXMLBTOTAL-…`). They are grouped into one game by league + date/time + teams. Polymarket US has one event per game.

A Kalshi game and a Polymarket US game are the same game when:
1. **Same league.**
2. **Scheduled starts within 90 minutes.** Kalshi: the time in the ticker, US Eastern (`26OCT282000` = Oct 28 8:00 PM ET = Oct 29 00:00 UTC). Polymarket US: `gameStartTime`. Observed difference on every checked game: 0 minutes.
   - Never use Kalshi `occurrence_datetime` (not the start time), the Polymarket US `endDate` (padded about two weeks), or the slug date (US date, not UTC).
3. **Both teams resolved**, by the first of:
   - the same code (MLB codes match: `MIL`/`mil`),
   - Polymarket US display code,
   - the same name (KBO: "KT Wiz"),
   - [team_aliases.json](team_aliases.json),
   - elimination: one team resolved and exactly one left on each side.

   Teams come from codes and the Polymarket US team list, never titles ("Game 5: MIL Brewers vs. TB Rays").

If two Kalshi games fit equally well (e.g. a doubleheader at the same time), the game is not paired and is listed with the reason.

### Which market
Every market becomes a proposition **(period, bet, team, line)**, using the bet-type tables in [config.py](config.py):

| Bet | Kalshi `competition_scope` | Polymarket US `sportsMarketType` |
|---|---|---|
| Full game winner | `Game` | `baseball_team_full_game_winner` |
| Run line | `Spread` | `baseball_team_full_game_spread` |
| Total runs | `Point Total` | `baseball_team_full_game_total` |
| Team total runs | `Team Total` | `baseball_team_total_runs` |
| First 5 innings winner / spread / total | `First 5 Innings Winner` / `Spread` / `Total` | `baseball_team_first_five_winner` / `_spread` / `_total` |
| Inning N winner / total (1–9) | `Nth Inning Winner` / `Nth Inning Total` | `baseball_team_inningN_winner` / `_total` |
| Run in the first inning | `YRFI/NRFI` | `baseball_team_first_inning_run` |
| Extra innings | `Extra Innings` | `baseball_game_extra_innings` |

| Bet | Kalshi | Polymarket US | Pairs when |
|---|---|---|---|
| Winner | One market per team (and `TIE` for 3-way bets) | One market per game (YES team, NO team), or one per outcome incl. "…end in a tie?" | Same team: **same**. Polymarket US NO team = Kalshi team: **opposite** |
| Run line | "Tampa Bay wins by over 1.5 runs" (`floor_strike` 1.5) | "Milwaukee covers -1.5" (YES `-1.50`) | Same team and line: **same**. Polymarket US "Milwaukee covers +1.5" = Kalshi NO "Tampa Bay wins by over 1.5": **opposite** |
| Totals, team totals | "Over 1.5 runs" (`floor_strike`) | YES Over, `line` 1.5 | Same line (and team): **same** |
| Yes/no props | YES = it happens | YES = it happens | **same** |

Lines must be identical. A Polymarket US line with no Kalshi market at that line is counted in the report, not forced.

Verified on the 2026 World Series Game 5: every bet type above paired, including ties in first-5-innings and inning winners.

### Recognised, not matched yet
- **Player props** (Polymarket US `baseball_player_*`: hits, home runs, strikeouts, …). Kalshi has player series too (`KXMLBHIT`, `KXMLBHR`, `KXMLBKS`, …), but matching them is not built. They are counted in the report under "Recognised, not matched yet".

## Step 4. Rules templates: what the LLM reviews

Each market's rules text becomes a template: names → `{X}`, dates → `{DATE}`, times → `{TIME}`, numbers → `{N}`. Each Kalshi × Polymarket US template pair is reviewed **once**. Approval covers every current and future market pair with the same two templates, for example every MLB run-line pair all season.

| Status | Meaning |
|---|---|
| `needs_template_review` | Template pair not reviewed yet. **Send to the LLM.** |
| `auto_confirmed` | Template pair approved (and `opposite_side_ok` for opposite-side pairs). **No LLM call.** |
| `template_rejected` | Template pair rejected, or opposite side not allowed |

Review input: `template_pairs.parquet`. Review output: an entry in [template_approvals.json](template_approvals.json):
```json
{"<template_pair_id>": {"status": "approved", "opposite_side_ok": true,
  "note": "Both settle a tie at 50/50; both use a two-day postponement window.", "reviewed_by": "llm+riddhi", "date": "2026-11-17"}}
```
If a platform changes its wording, the template changes and the pair goes back to review.

Things the reviewer must check (seen so far):
- **Opposite-side pairs** need the same outcome for a tie or a push on both platforms. KBO games: both settle a tie at 50/50. MLB full game: no ties.
- **Awards:** Polymarket US splits $1 among co-winners and names the BBWAA. Kalshi names "the Governing League".
- **World Series:** only Polymarket US covers cancellation. Kalshi's market closes in 2029, Polymarket US's in 2027.

## Outputs

`data/candidate_pairs/sports/baseball/`: `candidate_pairs.parquet` (T7, `pair_id` in the T2 format, both market titles in `kalshi_title` / `polyus_title`), `template_pairs.parquet` (for review), `report.json`.

`polyus_title` is the Polymarket US question, followed by the market's own title in brackets when the question does not already contain it. For futures the question is just the event ("National League MVP"); the bracket adds the player or team.

## The report: what to act on

| Section | Meaning | Action |
|---|---|---|
| Unknown leagues | A baseball game in a league not in `LEAGUES` | Add the league |
| Unknown bet types | A bet type not in `KALSHI_BETS` / `POLYUS_BETS` | Add it if both platforms offer it |
| Polymarket US games with no Kalshi game | Reason given per game | Teams not resolved: add to `team_aliases.json` |
| Not covered | Futures series or events not in `FUTURES` | Add a row if the other platform has it |
| Templates per futures row above 1 | Wording changed for some markets | Check the template text |
| Notes | Ambiguous names, games matched by elimination, a catalog without the bet-type fields | Read |

## Results

| Data | Open markets K / P | Pairs | Template pairs |
|---|---|---|---|
| Catalog 2026-11-05 (pulled before the bet-type fields existed) | 1,288 / 76 | 68 futures. The 3 KBO games are listed as unpaired until the catalog is refreshed | 12 |
| Self-test fixtures (World Series Game 5 + 2026-11-05 raw) | 1,362 / 580 | 127: 70 futures, 57 game | 28 |

**Before the next run:** refresh the catalog with `python -m collector catalog`. The catalog now stores the bet-type fields (`competition`, `competition_scope`, `sports_market_type`, `line`, team codes); the 2026-11-05 catalog predates them.
