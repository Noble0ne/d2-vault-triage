# d2-vault-triage

Cross-references a [DIM](https://app.destinyitemmanager.com/) weapon vault CSV export against a Destiny 2 tier-list spreadsheet and recommends **LOCK** (keep) vs **UNLOCK** (safe to dismantle) for every weapon you own.

Run it with no arguments and Ghost walks you through it:

```
------------------------------------------------------------
GHOST: Online. Running diagnostics... vault's heavier than
       last time, Guardian.
GHOST: Let's sort out what's worth carrying and what's dead
       weight. I'll need a couple things from you first.
------------------------------------------------------------

Tip: drag a file straight from Finder/Explorer into this window
instead of typing the path out -- I'm not picky about how the
intel reaches me.

GHOST: First, let me pull the current Endgame Analysis spreadsheet.
GHOST: Got it -- current sandbox data in hand.

GHOST: Good. Now your vault export -- the DIM CSV. Same deal.
> 
GHOST: Last thing -- where do you want the recommendations written?
Press Enter and I'll call it 'vault-recommendations.csv', or point me
somewhere else -- a folder, or a folder plus a filename.
> 

GHOST: Give me a second... scanning your arsenal.

GHOST: Scan complete, Guardian. Here's the breakdown:
  Total weapons scanned:              426
  LOCK (A/S tier):                     201
  LOCK (exotic -- always kept):        113
  KEEP (best available in niche):      65
  UNLOCK (B tier or below):            42
  UNLOCK (ungraded, no niche gap):     0
  UNLOCK (not in the analysis at all): 5

GHOST: Full report's in vault-recommendations.csv.
GHOST: When you're ready, drag vault-recommendations-dim-import.csv into DIM's Import CSV
       to apply the tags. I'll leave the actual dismantling to you.
```

Built against and tested with Aegis's [Endgame Analysis spreadsheet](https://docs.google.com/spreadsheets/d/1JM-0SlxVDAi-C6rGVlLxa-J1WGewEeL8Qvq4htWZHhY/edit?usp=drive_link) (view-only link) — the sheet names and columns this script expects (see Usage below) come from that spreadsheet's layout. The script downloads its current contents automatically; you never have to open the sheet yourself unless you want to look at it.

> **⚠️ This script does not delete or dismantle anything.** It only writes CSV notes/tags (`keep`/`junk`) that you import into DIM as suggestions. Nothing happens to your weapons until **you** review the recommendations and manually lock/dismantle them yourself, in-game or in DIM. Always review before acting on it — treat every recommendation as a suggestion, not an instruction.

> **🔍 This is open source — read it before you run it.** Every line of both scripts is in this repo, and both are commented in more detail than a typical project specifically so someone with no context can audit what they're about to run, not just what a function is called. If you'd rather not run a prebuilt `.app`/`.exe`, the safest path is to run `d2-vault-triage.py`/`d2-vault-triage-gui.py` directly from source with `python3` — that way there's no build step between what you read and what actually executes. The only network access either script makes is downloading Aegis's tier-list spreadsheet (see the link above) — nothing else is contacted, and no vault/account data ever leaves your machine.

## Requirements

- **Python 3.7 or later.**
  - **macOS/Linux**: check with `python3 --version` in Terminal. If missing, get it from [python.org](https://www.python.org/downloads/) or `brew install python3` on macOS.
  - **Windows**: check with `python --version` in Command Prompt or PowerShell. If missing, download the installer from [python.org](https://www.python.org/downloads/windows/) and, on the first install screen, **check the "Add python.exe to PATH" box** before clicking Install — if you skip this, `python` won't be recognized as a command afterward.
- **No pip installs needed for the CLI.** `d2-vault-triage.py` only uses Python's standard library (`csv`, `zipfile`, `xml.etree`, `urllib.request`, `pathlib`, `tempfile`) — nothing to `pip install`.
- **The GUI (`d2-vault-triage-gui.py`) needs Pillow** (`pip install Pillow`) for the console's live-scaling background image. Tkinter itself ships with Python already — no separate install for that.

## What it does

- **S/A tier → LOCK.** B tier and below → UNLOCK.
- **Exotics are exempt** from tier filtering entirely — every owned exotic locks, regardless of grade. In practice this tool doesn't spend any real judgment on exotics at all: most can be reclaimed from Collections almost any time, so there's no actual risk in leaving that decision alone. The one common exception is exotics with random perk rolls Collections can't reproduce (e.g. Hawkmoon) — if you're chasing a specific roll, track that yourself; this tool won't flag it. **This makes it, in practice, a Legendary-weapon triage tool** — it's making sure you're holding onto genuinely good/meta Legendaries, not making any real call on your exotics.
- **Niche ranking**: within each (Category, Frame, Element) niche — e.g. Rocket-frame Kinetic Pulse Rifle, or Strand Bow — the best-ranked weapon you own is kept even if its raw tier is mediocre, since it's the only thing covering that niche until something better drops. Any other owned weapon in the same niche is flagged as redundant, with a note pointing at what's already covering it and what the sheet's actual top-ranked option is.
- Weapons not present in the tier-list sheet at all are UNLOCK by default — no signal, no assumed value.

## About the tier-list data

The lock/unlock calls this script makes are only as good as the tier list behind them — right now, that's Aegis's Endgame Analysis spreadsheet. Aegis is a well-regarded Destiny 2 data scientist, but his tier list is still one analyst's testing and opinion, not an objective source of truth. Treat every recommendation this script produces as a starting point, not a verdict — cross-check against your own experience or another in-depth analysis if something looks off to you.

This script is currently built and tested specifically against Aegis's spreadsheet layout (see the sheet names/columns listed under Usage below). It'll technically run against a differently-structured tier list too (see "Reusing with your own data"), but Aegis's sheet is the one it's aimed at right now.

## Usage

### 1. Get your DIM export

- **`vault-export.csv`** — your DIM weapon export: open [DIM](https://app.destinyitemmanager.com/) → **Settings** → **Spreadsheets** section → click **Weapons** to download the CSV.

That's the only file you need to get yourself — the script downloads the current Endgame Analysis spreadsheet automatically. If you'd rather use a specific/local copy instead (e.g. a different tier-list spreadsheet, or the download isn't reachable), see "Reusing with your own data" below — it needs one sheet per weapon category (Autos, Bows, HCs, Pulses, Scouts, Sidearms, SMGs, BGLs, Fusions, Glaives, Shotguns, Snipers, Rocket Sidearms, Traces, HGLs, LFRs, LMGs, Rockets, Swords, Other, Exotic Weapons), each with at minimum a `Name` column; `Energy`, `Frame`, `Notes`, `Tier`, and `Rank` (or `#`) columns are used when present.

### 2. Run it

The easiest way — just run the script with no extra typing, and it'll ask you for what it needs. You can **drag the file straight from Finder/Explorer into the terminal window** when it asks, instead of typing the path out — the window fills in the full path for you.

**macOS/Linux** (Terminal):
```bash
python3 d2-vault-triage.py
```

**Windows** (Command Prompt or PowerShell):
```
python d2-vault-triage.py
```

It'll:
1. Download the current Endgame Analysis spreadsheet automatically — no prompt, no action needed. If the download fails (no internet, the sheet moved, etc.), it'll ask you to point it at a local `.xlsx` copy instead.
2. Ask for the path to your DIM vault export (.csv) — drag the file in, or type/paste the path.
3. Ask where to save the recommendations CSV — press Enter to just use `vault-recommendations.csv` in the current folder, or drag a folder in and type a filename after it.

If you'd rather skip the prompts (e.g. scripting it, or running it the same way repeatedly), pass the paths directly on the command line instead:

```bash
# macOS/Linux
python3 d2-vault-triage.py <vault-export.csv> [output.csv]
```
```
:: Windows
python d2-vault-triage.py <vault-export.csv> [output.csv]
```

`output.csv` is optional in both modes — defaults to `vault-recommendations.csv` if left blank/omitted. This form always downloads the current Endgame Analysis spreadsheet, same as running with no arguments.

**Keep threshold**: by default, the script locks A-tier and S-tier (exotics are always kept regardless of tier). If you only want to keep S-tier legendaries, add `--s-only` to any of the command-line forms above:

```bash
python3 d2-vault-triage.py <vault-export.csv> [output.csv] --s-only
```

Running with no arguments at all asks interactively instead — press Enter for A-tier and up, or type `S`. The GUI has the same choice as a pair of radio buttons ("A-tier and up (default)" / "S-tier only").

- **Windows only**: if you see `'python' is not recognized as an internal or external command`, Python either isn't installed or wasn't added to PATH during setup — reinstall from python.org and make sure "Add python.exe to PATH" is checked, or run the installer again and choose "Modify" → check that box.
- To get to the right folder in a terminal: on Windows, open the folder in File Explorer, click the address bar, type `cmd`, and press Enter — it opens Command Prompt already in that folder.

Two files are written:

1. The full analysis (`output.csv`, or your chosen name) — every weapon with its resolved tier, rank, category, recommendation, and reasoning note.
2. `<output>-dim-import.csv` — a matching `Id`/`Hash`/`Tag`/`Notes` subset for reimporting tags directly into DIM (DIM's importer only reads those four columns; recommendations map to DIM's `keep`/`junk` tags since DIM has no native lock/unlock tag).

### 3. Bring the recommendations back into DIM

In DIM, go back to **Settings** → **Spreadsheets** → **Weapons**, then drag the `<output>-dim-import.csv` file into the **Import CSV** drop zone. This applies the `keep`/`junk` tags and notes from the analysis to each weapon in your account — same place you got the export from in step 1.

Remember: this only sets tags. Nothing gets locked, unlocked, or dismantled automatically — that's still on you, in DIM or in-game.

## Reusing with your own data

The script is generic — point it at your own DIM CSV export and keep whatever tier-list spreadsheet you're using, as long as the sheet names and columns above line up. No Destiny account credentials or API access involved; it only reads local files (plus the one automatic download of the Endgame Analysis spreadsheet, unless you override it).

To use a specific or local analysis spreadsheet instead of the auto-downloaded one, pass all three paths explicitly:

```bash
# macOS/Linux
python3 d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]
```
```
:: Windows
python d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]
```

This skips the download entirely and uses exactly the file you point it at.

## License

MIT

---

Designed by [github.com/Noble0ne](https://github.com/Noble0ne), with Claude.
