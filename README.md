# d2-vault-triage

Cross-references a [DIM](https://app.destinyitemmanager.com/) weapon vault CSV export against a Destiny 2 tier-list spreadsheet and recommends **LOCK** (keep) vs **UNLOCK** (safe to dismantle) for every weapon you own.

Built against and tested with Aegis's [Endgame Analysis spreadsheet](https://docs.google.com/spreadsheets/d/1JM-0SlxVDAi-C6rGVlLxa-J1WGewEeL8Qvq4htWZHhY/edit?usp=drive_link) (view-only link) — the sheet names and columns this script expects (see Usage below) come from that spreadsheet's layout.

## Requirements

- **Python 3.7 or later** — check with `python3 --version`. Get it from [python.org](https://www.python.org/downloads/) if you don't have it, or via `brew install python3` on macOS.
- **No pip installs needed.** The script only uses Python's standard library (`csv`, `zipfile`, `xml.etree`) — nothing to `pip install`.

## What it does

- **S/A tier → LOCK.** B tier and below → UNLOCK.
- **Exotics are exempt** from tier filtering entirely — every owned exotic locks, regardless of grade.
- **Niche ranking**: within each (Category, Frame, Element) niche — e.g. Rocket-frame Kinetic Pulse Rifle, or Strand Bow — the best-ranked weapon you own is kept even if its raw tier is mediocre, since it's the only thing covering that niche until something better drops. Any other owned weapon in the same niche is flagged as redundant, with a note pointing at what's already covering it and what the sheet's actual top-ranked option is.
- Weapons not present in the tier-list sheet at all are UNLOCK by default — no signal, no assumed value.

## Usage

### 1. Get the two input files

- **`analysis.xlsx`** — download Aegis's [Endgame Analysis spreadsheet](https://docs.google.com/spreadsheets/d/1JM-0SlxVDAi-C6rGVlLxa-J1WGewEeL8Qvq4htWZHhY/edit?usp=drive_link) as an Excel file: File → Download → Microsoft Excel (.xlsx). A different tier-list spreadsheet works too, as long as it has one sheet per weapon category (Autos, Bows, HCs, Pulses, Scouts, Sidearms, SMGs, BGLs, Fusions, Glaives, Shotguns, Snipers, Rocket Sidearms, Traces, HGLs, LFRs, LMGs, Rockets, Swords, Other, Exotic Weapons). Each sheet needs at minimum a `Name` column; `Energy`, `Frame`, `Notes`, `Tier`, and `Rank` (or `#`) columns are used when present.
- **`vault-export.csv`** — your DIM weapon export: open [DIM](https://app.destinyitemmanager.com/), go to Settings, and use the CSV export option for weapons.

### 2. Run it

```bash
python3 d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]
```

- `output.csv` is optional — defaults to `vault-recommendations.csv` if you don't pass one.
- Run from inside the folder where you downloaded both files, or use full paths, e.g.:
  ```bash
  python3 d2-vault-triage.py ~/Downloads/Endgame_Analysis.xlsx ~/Downloads/destiny_weapon.csv
  ```

Two files are written:

1. The full analysis (`output.csv`, or your chosen name) — every weapon with its resolved tier, rank, category, recommendation, and reasoning note.
2. `<output>-dim-import.csv` — a matching `Id`/`Hash`/`Tag`/`Notes` subset for reimporting tags directly into DIM (DIM's importer only reads those four columns; recommendations map to DIM's `keep`/`junk` tags since DIM has no native lock/unlock tag).

## Reusing with your own data

The script is generic — point it at your own DIM CSV export and keep whatever tier-list spreadsheet you're using, as long as the sheet names and columns above line up. No Destiny account credentials or API access involved; it only reads local files.

## License

MIT
