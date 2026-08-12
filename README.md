# d2-vault-triage

Cross-references a [DIM](https://app.destinyitemmanager.com/) weapon vault CSV export against a Destiny 2 tier-list spreadsheet and recommends **LOCK** (keep) vs **UNLOCK** (safe to dismantle) for every weapon you own.

Built against and tested with Aegis's [Endgame Analysis spreadsheet](https://docs.google.com/spreadsheets/d/1JM-0SlxVDAi-C6rGVlLxa-J1WGewEeL8Qvq4htWZHhY/edit?usp=drive_link) (view-only link) — the sheet names and columns this script expects (see Usage below) come from that spreadsheet's layout.

> **⚠️ This script does not delete or dismantle anything.** It only writes CSV notes/tags (`keep`/`junk`) that you import into DIM as suggestions. Nothing happens to your weapons until **you** review the recommendations and manually lock/dismantle them yourself, in-game or in DIM. Always review before acting on it — treat every recommendation as a suggestion, not an instruction.

## Requirements

- **Python 3.7 or later.**
  - **macOS/Linux**: check with `python3 --version` in Terminal. If missing, get it from [python.org](https://www.python.org/downloads/) or `brew install python3` on macOS.
  - **Windows**: check with `python --version` in Command Prompt or PowerShell. If missing, download the installer from [python.org](https://www.python.org/downloads/windows/) and, on the first install screen, **check the "Add python.exe to PATH" box** before clicking Install — if you skip this, `python` won't be recognized as a command afterward.
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

**macOS/Linux** (Terminal):
```bash
python3 d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]
```

**Windows** (Command Prompt or PowerShell):
```
python d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]
```

- `output.csv` is optional — defaults to `vault-recommendations.csv` if you don't pass one.
- Run from inside the folder where you downloaded both files and the script, or use full paths. Examples:
  ```bash
  # macOS/Linux
  python3 d2-vault-triage.py ~/Downloads/Endgame_Analysis.xlsx ~/Downloads/destiny_weapon.csv
  ```
  ```
  :: Windows
  python d2-vault-triage.py C:\Users\YourName\Downloads\Endgame_Analysis.xlsx C:\Users\YourName\Downloads\destiny_weapon.csv
  ```
- **Windows only**: if you see `'python' is not recognized as an internal or external command`, Python either isn't installed or wasn't added to PATH during setup — reinstall from python.org and make sure "Add python.exe to PATH" is checked, or run the installer again and choose "Modify" → check that box.
- To get to the right folder in a terminal: on Windows, open the folder in File Explorer, click the address bar, type `cmd`, and press Enter — it opens Command Prompt already in that folder.

Two files are written:

1. The full analysis (`output.csv`, or your chosen name) — every weapon with its resolved tier, rank, category, recommendation, and reasoning note.
2. `<output>-dim-import.csv` — a matching `Id`/`Hash`/`Tag`/`Notes` subset for reimporting tags directly into DIM (DIM's importer only reads those four columns; recommendations map to DIM's `keep`/`junk` tags since DIM has no native lock/unlock tag).

## Reusing with your own data

The script is generic — point it at your own DIM CSV export and keep whatever tier-list spreadsheet you're using, as long as the sheet names and columns above line up. No Destiny account credentials or API access involved; it only reads local files.

## License

MIT
