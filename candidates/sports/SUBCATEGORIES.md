# Sports subcategories: Kalshi vs Polymarket US labels

The same sport is labeled differently on the two platforms. This file records those differences. The code version is [sport_map.py](sport_map.py).

## Where the label lives

| | Kalshi | Polymarket US |
|---|---|---|
| Field | Series tag, T1 `tags` (e.g. `["Hockey"]`) | Market `subcategory` (e.g. `HOCKEY`) |
| Style | Title case, one tag per series | Upper case, no spaces, sometimes misspelled or cut short |
| Consistency | One label per sport | Several labels for some sports, and one label shared by two sports |

## Included sports

| Folder | Kalshi label | Polymarket US label(s) | Discrepancy |
|---|---|---|---|
| `football/` | `Football` | `FOOTBALL` | None |
| `basketball/` | `Basketball` | `BASKETBALL` | None |
| `soccer/` | `Soccer` | `SOCCER` | None |
| `hockey/` | `Hockey` | `HOCKEY`, `ICEHOCKEY` | Two labels on Polymarket US for the same sport |
| `golf/` | `Golf` | `GOLF` | None |
| `baseball/` | `Baseball` | `BASEBALL` | None |
| `motorsport/` | `Motorsport` | `MOTORSPORT` | None |
| `esports/` | `Esports` | `ESPORTS`, `EFOOTBALL`, `EBASKETBAL` | Polymarket US splits out e-soccer and e-basketball. `EFOOTBALL` means e-soccer, not American football. `EBASKETBAL` is cut short |
| `tennis/` | `Tennis` | `TENNIS`, `ATP`, `WTA`, `ATPDB`, `WTADB` | Five labels on Polymarket US: by tour, by doubles, and a general one |
| `mma/` | `MMA` | `MMA`, `COMBAT` (UFC series) | `COMBAT` is shared with boxing. Split by series slug: `ufc-*` is MMA |
| `boxing/` | `Boxing` | `BOXING`, `COMBAT` (other series) | Same `COMBAT` split |
| `table_tennis/` | `Table Tennis` | `TABLETENIS` | Misspelled on Polymarket US |

## Excluded sports (decided 2026-11-17)

| Sport | Kalshi label | Polymarket US label | Reason |
|---|---|---|---|
| Chess | `Chess` | `CHESS` | Almost no overlap |
| Rugby | `Rugby` | `RUGBY` | Almost no overlap |
| Cricket | `Cricket` | `CRICKET` | Almost no overlap |
| Darts | `Darts` | `DARTS` | Too small |
| Other | `Other`, `Cities`, `International` | none | Kalshi only |
| Volleyball | `Volleyball` | none | Kalshi only |
| Lacrosse | `Lacrosse` | none | Kalshi only |
| Pickleball | none | `PICKLEBALL` | Polymarket US only |

## Watch for

- New labels can appear on either platform. A sports label that is not in `sport_map.py` is dropped, so check for unmapped labels after each catalog refresh.
