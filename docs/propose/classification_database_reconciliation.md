# Initial classification database reconciliation

The migration inventoried all nine JSON files in `data/propose` and the three matching classification review exports in Downloads. The five run files contain, respectively, 10, 100, 200, 200, and 200 pairs; all 710 stored results are successful. Every run's pair IDs, ranks, ordering, statuses, and full result records were checked against its source. The three standalone manifests contain 10, 100, and 200 pairs. Original run and review timestamps were unavailable and remain unknown.

| Review export | Source judgments | Already imported |
| --- | ---: | ---: |
| `data/propose/classification-reviews.json` | 310 | 0 |
| `Downloads/classification-reviews (1).json` | 210 | 199 |
| `Downloads/classification-reviews (2).json` | 310 | 310 |
| `Downloads/classification-reviews.json` | 10 | 10 |

The database has **310 distinct reviewed pairs** and 321 distinct judgment records. Eleven pairs have differing historical judgments across exports; neither export order nor file modification time identifies a safe winner. These are retained as conflicts and excluded from scoring until explicitly reviewed. The other overlapping export records are deduplicated. The detailed machine-readable report can be regenerated with the import command in the classification README. Importing the same sources again does not create additional runs, attempts, or judgments.
