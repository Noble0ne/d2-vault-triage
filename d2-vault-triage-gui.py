#!/usr/bin/env python3
"""
Tkinter GUI companion for d2-vault-triage.py.

The weapon-analysis and recommendation logic remains in d2-vault-triage.py.
This file only handles:
- selecting the DIM vault CSV
- downloading/selecting the Aegis Endgame Analysis workbook
- choosing the output path
- displaying status/output in a Tkinter window
"""

import csv
import importlib.util
import os
from pathlib import Path
import sys
import threading
import tkinter as tk
from tkinter import filedialog


CORE_FILENAME = "d2-vault-triage.py"


def core_script_path():
    """Find the unchanged CLI script in source runs or inside a PyInstaller bundle."""
    candidates = []

    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(bundle_dir / CORE_FILENAME)
        candidates.append(Path(sys.executable).resolve().parent / CORE_FILENAME)
    else:
        candidates.append(Path(__file__).resolve().parent / CORE_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = "\n".join(str(p) for p in candidates)
    raise FileNotFoundError(
        f"Could not find {CORE_FILENAME}. Searched:\n{searched}"
    )


def load_core():
    """
    Load d2-vault-triage.py without renaming it.

    A normal Python import cannot use a module name containing hyphens, so this
    loads the exact existing file with importlib instead.
    """
    path = core_script_path()
    spec = importlib.util.spec_from_file_location("d2_vault_triage_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    required = (
        "build_weapon_index",
        "resolve_name",
        "pick_entry",
        "recommend",
        "dim_tag",
        "build_note",
        "apply_niche_ranking",
    )
    missing = [name for name in required if not callable(getattr(module, name, None))]
    if missing:
        raise ImportError(
            f"{CORE_FILENAME} is missing required function(s): {', '.join(missing)}"
        )

    return module


CORE = load_core()


def run_triage(xlsx_path, csv_path, out_path):
    """
    Orchestrate the same CSV pipeline as the CLI while delegating every weapon
    decision to the existing functions in d2-vault-triage.py.
    """
    index = CORE.build_weapon_index(xlsx_path)
    all_entries = [entry for entries in index.values() for entry in entries]

    with open(csv_path, newline="", encoding="utf-8") as f:
        vault_rows = list(csv.DictReader(f))

    results = []

    for row in vault_rows:
        name = row["Name"]
        resolved = CORE.resolve_name(name, index)
        category_hint = "Exotic Weapons" if row.get("Rarity") == "Exotic" else None

        if resolved:
            entry = CORE.pick_entry(index[resolved], row)
            rec = CORE.recommend(entry["tier"], entry["category"])
        else:
            entry = {
                "category": category_hint,
                "energy": None,
                "frame": None,
                "tier": None,
                "rank": None,
                "notes": None,
            }
            rec = CORE.recommend(None, category_hint)

        results.append(
            {
                "Name": name,
                "Hash": row.get("Hash"),
                "Id": row.get("Id"),
                "Tag": CORE.dim_tag(rec),
                "Notes": CORE.build_note(
                    rec,
                    entry["tier"],
                    entry["rank"],
                    entry["category"],
                    entry["notes"],
                ),
                "Type": row.get("Type"),
                "Element": row.get("Element"),
                "Frame": entry["frame"],
                "Category": entry["category"],
                "Tier": entry["tier"],
                "Rank": entry["rank"],
                "Recommendation": rec,
                "Crafted": row.get("Crafted"),
                "MasterworkTier": row.get("Masterwork Tier"),
                "Equipped": row.get("Equipped"),
            }
        )

    CORE.apply_niche_ranking(results, all_entries)

    if not results:
        raise ValueError("The DIM CSV contains no weapon rows.")

    out_path = str(out_path)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    dim_path = out_path.rsplit(".", 1)[0] + "-dim-import.csv"
    with open(dim_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Id", "Hash", "Tag", "Notes"])
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "Id": row["Id"],
                    "Hash": row["Hash"],
                    "Tag": row["Tag"],
                    "Notes": row["Notes"],
                }
            )

    summary = {
        "total": len(results),
        "lock": sum(1 for r in results if r["Recommendation"] == "LOCK"),
        "lock_exotic": sum(
            1 for r in results if r["Recommendation"] == "LOCK (exotic)"
        ),
        "keep_niche": sum(
            1 for r in results if r["Recommendation"] == "KEEP (best available)"
        ),
        "unlock": sum(1 for r in results if r["Recommendation"] == "UNLOCK"),
        "ungraded": sum(
            1 for r in results if r["Recommendation"] == "UNLOCK (ungraded)"
        ),
        "untiered": sum(
            1 for r in results if r["Recommendation"] == "UNLOCK (untiered)"
        ),
    }

    return dim_path, summary


class VaultTriageGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("D2 Vault Triage")
        self.root.geometry("760x520")
        self.root.minsize(650, 430)

        self.csv_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.tier_path = None

        self._build_ui()

        self.log("GHOST: Online. Running diagnostics... vault's heavier than last time, Guardian.")
        self.log("GHOST: I'll pull the latest Endgame Analysis before we start.")
        self.refresh_tier_list()

    def _build_ui(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(4, weight=1)

        tk.Label(self.root, text="DIM vault CSV:").grid(
            row=0, column=0, sticky="w", padx=(10, 6), pady=(10, 5)
        )
        tk.Entry(self.root, textvariable=self.csv_var).grid(
            row=0, column=1, sticky="ew", pady=(10, 5)
        )
        self.csv_button = tk.Button(
            self.root, text="Browse...", command=self.choose_csv
        )
        self.csv_button.grid(
            row=0, column=2, sticky="ew", padx=(6, 10), pady=(10, 5)
        )

        tk.Label(self.root, text="Output path:").grid(
            row=1, column=0, sticky="w", padx=(10, 6), pady=5
        )
        tk.Entry(self.root, textvariable=self.output_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=5
        )

        tier_controls = tk.Frame(self.root)
        tier_controls.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=5
        )
        tier_controls.columnconfigure(2, weight=1)

        self.refresh_button = tk.Button(
            tier_controls,
            text="Refresh tier list",
            command=self.refresh_tier_list,
        )
        self.refresh_button.grid(row=0, column=0, sticky="w")

        self.local_button = tk.Button(
            tier_controls,
            text="Load from file instead",
            command=self.choose_local_tier_list,
        )
        self.local_button.grid(row=0, column=1, sticky="w", padx=(6, 10))

        self.tier_status = tk.Label(
            tier_controls,
            text="Tier list: not loaded",
            anchor="w",
        )
        self.tier_status.grid(row=0, column=2, sticky="ew")

        self.run_button = tk.Button(
            self.root,
            text="Run",
            command=self.start_run,
            width=14,
        )
        self.run_button.grid(
            row=3, column=0, columnspan=3, pady=(5, 8)
        )

        log_frame = tk.Frame(self.root)
        log_frame.grid(
            row=4, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 10)
        )
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        scrollbar = tk.Scrollbar(log_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            yscrollcommand=scrollbar.set,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.config(command=self.log_text.yview)

    def log(self, message=""):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _ui(self, func, *args):
        self.root.after(0, func, *args)

    def choose_csv(self):
        path = filedialog.askopenfilename(
            title="Select DIM vault CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        self.csv_var.set(path)
        self.output_var.set(
            str(Path(path).resolve().with_name("vault-recommendations.csv"))
        )
        self.log("GHOST: Vault export acquired. That's the pile we're sorting.")

    def choose_local_tier_list(self):
        path = filedialog.askopenfilename(
            title="Select Aegis Endgame Analysis spreadsheet",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return

        self.tier_path = path
        self.tier_status.config(
            text=f"Tier list: local file — {Path(path).name}"
        )
        self.log("GHOST: Using your local analysis copy. Old-fashioned, but reliable.")

    def refresh_tier_list(self):
        self.refresh_button.config(state="disabled")
        self.log("GHOST: Checking the Vanguard uplink... pulling the latest tier data.")
        threading.Thread(target=self._download_tier_list, daemon=True).start()

    def _download_tier_list(self):
        try:
            target = CORE.download_tier_list()

            self.tier_path = target
            timestamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._ui(
                self.tier_status.config,
                {"text": f"Last refresh succeeded: {timestamp}"},
            )
            self._ui(
                self.log,
                "GHOST: Tier list refreshed. Current sandbox data is in hand.",
            )
        except Exception as exc:
            self._ui(
                self.tier_status.config,
                {"text": "Tier list refresh failed — load from file instead"},
            )
            self._ui(
                self.log,
                f"GHOST: Uplink dropped out. Couldn't refresh the tier list: {exc}",
            )
            self._ui(
                self.log,
                "GHOST: Use 'Load from file instead' and point me at an .xlsx copy.",
            )
        finally:
            self._ui(self.refresh_button.config, {"state": "normal"})

    def start_run(self):
        csv_path = self.csv_var.get().strip()
        out_path = self.output_var.get().strip()

        if not csv_path:
            self.log("GHOST: I need the DIM vault export first, Guardian.")
            return

        if not os.path.isfile(csv_path):
            self.log("GHOST: Nothing at that vault path that I can see.")
            return

        if not self.tier_path or not os.path.isfile(self.tier_path):
            self.log("GHOST: No usable tier list yet. Refresh it or load an .xlsx from file.")
            return

        if not out_path:
            out_path = str(
                Path(csv_path).resolve().with_name("vault-recommendations.csv")
            )
            self.output_var.set(out_path)

        out_parent = Path(out_path).expanduser().resolve().parent
        if not out_parent.is_dir():
            self.log("GHOST: That output folder doesn't exist. Check the path and try again.")
            return

        self.run_button.config(state="disabled")
        self.log("")
        self.log("GHOST: Give me a second... scanning your arsenal.")

        threading.Thread(
            target=self._run_worker,
            args=(self.tier_path, csv_path, out_path),
            daemon=True,
        ).start()

    def _run_worker(self, xlsx_path, csv_path, out_path):
        try:
            dim_path, summary = run_triage(xlsx_path, csv_path, out_path)
        except Exception as exc:
            self._ui(
                self.log,
                f"GHOST: Scan hit some interference: {exc}",
            )
            self._ui(self.run_button.config, {"state": "normal"})
            return

        lines = [
            "",
            "GHOST: Scan complete, Guardian. Here's the breakdown:",
            f"  Total weapons scanned:              {summary['total']}",
            f"  LOCK (A/S tier):                     {summary['lock']}",
            f"  LOCK (exotic -- always kept):        {summary['lock_exotic']}",
            f"  KEEP (best available in niche):      {summary['keep_niche']}",
            f"  UNLOCK (B tier or below):            {summary['unlock']}",
            f"  UNLOCK (ungraded, no niche gap):     {summary['ungraded']}",
            f"  UNLOCK (not in the analysis at all): {summary['untiered']}",
            "",
            f"GHOST: Full report's in {out_path}.",
            f"GHOST: When you're ready, drag {dim_path} into DIM's Import CSV",
            "       to apply the tags. I'll leave the actual dismantling to you.",
        ]

        for line in lines:
            self._ui(self.log, line)

        self._ui(self.run_button.config, {"state": "normal"})


def main():
    root = tk.Tk()
    VaultTriageGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
