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
from tkinter import filedialog, ttk

from PIL import Image, ImageTk


CORE_FILENAME = "d2-vault-triage.py"

# Palette pulled from the app icon: deep navy shell, cyan lens/accent, orange highlights.
# Applied in bulk via root.tk_setPalette() in VaultTriageGUI.__init__ -- that's a real
# Tk API that sets application-wide default colors, picked up by any widget created
# afterward that doesn't set its own bg/fg explicitly. It does NOT reliably recolor
# plain tk.Button on macOS: Aqua renders native button chrome and mostly ignores
# bg/fg/activeBackground on that one widget class specifically (a long-standing,
# well-documented Tk-on-macOS limitation -- Entry/Label/Radiobutton are unaffected).
# That's why every button below is a ttk.Button under the "clam" theme instead of a
# plain tk.Button -- clam is a non-native, fully-styleable ttk theme that actually
# honors custom colors, at the cost of losing the native Aqua button bevel.
PALETTE = {
    "background": "#0b0f1e",
    "foreground": "#d7e6ff",
    "activeBackground": "#1b2947",
    "activeForeground": "#8fe3ff",
    "highlightBackground": "#0b0f1e",
    "highlightColor": "#39c5f2",
    "insertBackground": "#39c5f2",
    "selectBackground": "#1b2947",
    "selectForeground": "#ffffff",
    "selectColor": "#12172a",
    "troughColor": "#12172a",
}
ACCENT_CYAN = "#39c5f2"
ACCENT_ORANGE = "#ff9640"
# tk_setPalette's "background" becomes the default bg for Entry/Canvas too, which
# would make them blend into the window with no visible box outline. Give input-like
# widgets (the two Entry fields, the log Canvas) their own slightly-lighter bg plus a
# highlightthickness border instead, so they still read as distinct boxes.
INPUT_BG = "#141a2e"
INPUT_BORDER = "#2a3350"
BUTTON_BG = "#1b2947"
BUTTON_ACTIVE_BG = "#26355c"
BUTTON_DISABLED_BG = "#141a2e"
BUTTON_DISABLED_FG = "#5c6b8a"
CONSOLE_BG_IMAGE = "icons/console-bg.png"
LOG_FONT = ("Menlo", 12)
LOG_LINE_PAD = 4


def asset_path(relative_path):
    """Find a bundled asset in source runs or inside a PyInstaller bundle --
    same resolution strategy as core_script_path() below, generalized to any
    file added via --add-data instead of just the CLI script."""
    candidates = []

    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(bundle_dir / relative_path)
        candidates.append(Path(sys.executable).resolve().parent / relative_path)
    else:
        candidates.append(Path(__file__).resolve().parent / relative_path)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = "\n".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Could not find {relative_path}. Searched:\n{searched}")


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
        "triage_vault",
        "write_dim_import_csv",
        "summarize_results",
    )
    missing = [name for name in required if not callable(getattr(module, name, None))]
    if missing:
        raise ImportError(
            f"{CORE_FILENAME} is missing required function(s): {', '.join(missing)}"
        )

    return module


CORE = load_core()


def run_triage(xlsx_path, csv_path, out_path, min_tier="A"):
    """
    Thin GUI wrapper around CORE.triage_vault() -- all the actual weapon-
    decision logic (resolving names, grading tiers, niche ranking, deciding
    what's safe to include in the DIM-import file) lives once in
    d2-vault-triage.py and is shared with the CLI's main(), rather than
    being duplicated here. This function's only job is turning that shared
    result into the two output files and a summary the GUI can display.
    """
    out_path = str(out_path)
    results = CORE.triage_vault(xlsx_path, csv_path, min_tier)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    dim_path = out_path.rsplit(".", 1)[0] + "-dim-import.csv"
    CORE.write_dim_import_csv(results, dim_path)

    summary = CORE.summarize_results(results)
    return dim_path, summary


class VaultTriageGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("D2 Vault Triage")
        self.root.geometry("760x520")
        self.root.minsize(650, 430)
        # See the PALETTE comment above for why this covers most widgets but not
        # tk.Button -- the buttons get their own ttk styling further down.
        self.root.tk_setPalette(**PALETTE)

        self.csv_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.min_tier_var = tk.StringVar(value="A")
        self.min_tier_var.trace_add("write", self._on_min_tier_change)
        self.tier_path = None

        self._build_ui()

        self.log("GHOST: Online, Guardian. Load your vault export whenever you're ready — we'll see what we're working with.")
        self.log("GHOST: I'll pull the latest Endgame Analysis before we start.")
        self.refresh_tier_list()

    def _on_min_tier_change(self, *_args):
        if self.min_tier_var.get() == "S":
            self.log("GHOST: Keep threshold set to S-tier only.")
        else:
            self.log("GHOST: Keep threshold set to A-tier and up.")

    def _build_ui(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(5, weight=1)

        # Switching the ttk theme to "clam" is global (affects every ttk widget in
        # this app), but the only ttk widgets in use are the 4 buttons below, so it's
        # safe -- plain tk widgets (Entry/Label/Radiobutton/Canvas) aren't ttk and
        # aren't affected by this at all. "clam" is used specifically because it's a
        # non-native theme that fully honors custom colors, unlike the default "aqua"
        # theme on macOS.
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Vault.TButton",
            background=BUTTON_BG,
            foreground=ACCENT_CYAN,
            bordercolor=INPUT_BORDER,
            lightcolor=BUTTON_BG,
            darkcolor=BUTTON_BG,
            focuscolor=ACCENT_CYAN,
            padding=6,
        )
        style.map(
            "Vault.TButton",
            background=[("active", BUTTON_ACTIVE_BG), ("disabled", BUTTON_DISABLED_BG)],
            foreground=[("disabled", BUTTON_DISABLED_FG)],
        )

        tk.Label(self.root, text="DIM vault CSV:").grid(
            row=0, column=0, sticky="w", padx=(10, 6), pady=(10, 5)
        )
        tk.Entry(
            self.root,
            textvariable=self.csv_var,
            bg=INPUT_BG,
            fg=PALETTE["foreground"],
            insertbackground=ACCENT_CYAN,
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=ACCENT_CYAN,
        ).grid(
            row=0, column=1, sticky="ew", pady=(10, 5)
        )
        self.csv_button = ttk.Button(
            self.root, text="Browse...", command=self.choose_csv, style="Vault.TButton"
        )
        self.csv_button.grid(
            row=0, column=2, sticky="ew", padx=(6, 10), pady=(10, 5)
        )

        tk.Label(self.root, text="Output path:").grid(
            row=1, column=0, sticky="w", padx=(10, 6), pady=5
        )
        tk.Entry(
            self.root,
            textvariable=self.output_var,
            bg=INPUT_BG,
            fg=PALETTE["foreground"],
            insertbackground=ACCENT_CYAN,
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=ACCENT_CYAN,
        ).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=5
        )

        keep_controls = tk.Frame(self.root)
        keep_controls.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=5
        )

        tk.Label(keep_controls, text="Keep threshold:").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        tk.Radiobutton(
            keep_controls,
            text="A-tier and up (default)",
            variable=self.min_tier_var,
            value="A",
        ).grid(row=0, column=1, sticky="w")
        tk.Radiobutton(
            keep_controls,
            text="S-tier only",
            variable=self.min_tier_var,
            value="S",
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        tier_controls = tk.Frame(self.root)
        tier_controls.grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=5
        )
        tier_controls.columnconfigure(2, weight=1)

        self.refresh_button = ttk.Button(
            tier_controls,
            text="Refresh tier list",
            command=self.refresh_tier_list,
            style="Vault.TButton",
        )
        self.refresh_button.grid(row=0, column=0, sticky="w")

        self.local_button = ttk.Button(
            tier_controls,
            text="Load from file instead",
            command=self.choose_local_tier_list,
            style="Vault.TButton",
        )
        self.local_button.grid(row=0, column=1, sticky="w", padx=(6, 10))

        self.tier_status = tk.Label(
            tier_controls,
            text="Tier list: not loaded",
            anchor="w",
        )
        self.tier_status.grid(row=0, column=2, sticky="ew")

        self.run_button = ttk.Button(
            self.root,
            text="Run",
            command=self.start_run,
            width=14,
            style="Vault.TButton",
        )
        self.run_button.grid(
            row=4, column=0, columnspan=3, pady=(5, 8)
        )

        log_frame = tk.Frame(self.root)
        log_frame.grid(
            row=5, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 10)
        )
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        scrollbar = tk.Scrollbar(log_frame)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # The log console is a Canvas, not a Text widget, specifically so the
        # background artwork can sit behind the log lines -- tk.Text has no
        # background-image support at all; Canvas is the only classic Tk widget
        # that can layer an image behind drawn content. Everything below (the
        # image item, the log lines, scrolling) is manually drawn/positioned on
        # this one canvas rather than relying on Text's built-in behavior.
        self.log_canvas = tk.Canvas(
            log_frame,
            bg=INPUT_BG,
            highlightthickness=1,
            highlightbackground=INPUT_BORDER,
            highlightcolor=INPUT_BORDER,
            yscrollcommand=scrollbar.set,
        )
        self.log_canvas.grid(row=0, column=0, sticky="nsew")
        # Wrapped instead of passing self.log_canvas.yview directly, so dragging the
        # scrollbar also keeps the background image pinned to the viewport -- see
        # _reposition_console_bg().
        scrollbar.config(command=self._on_log_scroll)

        # Loaded once as a PIL Image (not yet a Tk-displayable PhotoImage) and kept
        # around as the source to rescale from on every resize -- see
        # _update_console_bg(). create_image() here has no `image=` yet since the
        # actual scaled/cropped PhotoImage isn't computed until the first
        # <Configure> event tells us the real canvas size.
        self._console_bg_source = Image.open(str(asset_path(CONSOLE_BG_IMAGE))).convert("RGB")
        self._console_bg_photo = None
        self._console_bg_fit_size = None
        self._console_bg_item = self.log_canvas.create_image(0, 0, anchor="nw")
        self._log_lines = []
        self._log_text_items = []
        self.log_canvas.bind("<Configure>", self._redraw_log)

    def _on_log_scroll(self, *args):
        self.log_canvas.yview(*args)
        self._reposition_console_bg()

    def _reposition_console_bg(self):
        """The background image lives in the same scrollable coordinate space as
        the log text (Canvas has no separate 'fixed background' layer), so left
        alone it would scroll away as the log grows. canvasy(0) converts the
        viewport's current top edge (window/screen coordinates) into the matching
        point in that scrollable content space -- moving the image there every
        time content changes or the view scrolls keeps it visually pinned to the
        window instead of drifting with the text."""
        top_content_y = self.log_canvas.canvasy(0)
        self.log_canvas.coords(self._console_bg_item, 0, top_content_y)

    def _update_console_bg(self, width, height):
        """Scale+crop the background image to exactly cover the current
        viewport (uniform scale, centered crop -- no distortion, no blank
        bars on the sides) and cache it so a resize that doesn't actually
        change the target size skips the expensive PIL resize/crop.

        Classic Tk's own PhotoImage only supports integer zoom()/subsample()
        factors, not arbitrary continuous resizing to match whatever size the
        panel happens to be -- hence Pillow (PIL.Image.resize) doing the actual
        scaling here, converted to a Tk-displayable ImageTk.PhotoImage only at
        the end. "Cover" scaling (scale by whichever dimension needs it more,
        then crop the overflow) is what guarantees the image always fills the
        panel completely with no letterboxing, at the cost of cropping off
        whatever doesn't fit -- a "fit" scale (no cropping) would leave blank
        bars on whichever side the aspect ratio doesn't match."""
        if (width, height) == self._console_bg_fit_size:
            return
        self._console_bg_fit_size = (width, height)

        src = self._console_bg_source
        scale = max(width / src.width, height / src.height)
        scaled_w, scaled_h = max(1, round(src.width * scale)), max(1, round(src.height * scale))
        scaled = src.resize((scaled_w, scaled_h), Image.LANCZOS)

        left = (scaled_w - width) // 2
        top = (scaled_h - height) // 2
        cropped = scaled.crop((left, top, left + width, top + height))

        self._console_bg_photo = ImageTk.PhotoImage(cropped)
        self.log_canvas.itemconfig(self._console_bg_item, image=self._console_bg_photo)

    def log(self, message=""):
        # Appends and redraws the entire log from scratch every call (see
        # _redraw_log) rather than incrementally adding one line -- simpler to
        # reason about, and fine for how much text a single triage run's GHOST
        # dialogue actually produces. Not built to scale to a very long-running,
        # high-volume log.
        self._log_lines.append(message)
        self._redraw_log()

    def _redraw_log(self, event=None):
        canvas = self.log_canvas
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            # Fires once before the window is actually mapped/sized (winfo_width()
            # returns 1 for an unrealized widget) -- the real <Configure> event that
            # follows once the window has real dimensions calls this again.
            return

        self._update_console_bg(width, height)

        # No incremental update -- every redraw clears and recreates all text items
        # from scratch. Canvas text items don't reflow on their own if the canvas
        # width changes, so recreating them is the simplest way to keep word-wrap
        # correct after a resize as well as after a new line is appended.
        for item in self._log_text_items:
            canvas.delete(item)
        self._log_text_items = []

        y = LOG_LINE_PAD
        text_width = max(width - 2 * LOG_LINE_PAD, 10)
        for line in self._log_lines:
            item = canvas.create_text(
                LOG_LINE_PAD,
                y,
                text=line,
                anchor="nw",
                # Canvas text items wrap on their own when given a fixed pixel
                # width -- this is what replaces tk.Text's built-in wrap="word".
                width=text_width,
                fill=PALETTE["foreground"],
                font=LOG_FONT,
            )
            self._log_text_items.append(item)
            # bbox() measures the just-created item's actual rendered height
            # (which varies once word-wrap kicks in on a long line), so the next
            # line always starts right below it with no manual line-height math.
            bbox = canvas.bbox(item)
            line_height = (bbox[3] - bbox[1]) if bbox else 16
            y += line_height + LOG_LINE_PAD

        # scrollregion is Canvas's equivalent of Text's automatic scrollbar range --
        # it has to be told explicitly since nothing here uses Tk's automatic
        # layout. yview_moveto(1.0) is the Canvas equivalent of Text's .see("end"):
        # jump to the bottom so the newest line is always visible.
        total_height = max(y, height)
        canvas.configure(scrollregion=(0, 0, width, total_height))
        canvas.yview_moveto(1.0)
        self._reposition_console_bg()

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
                {"text": f"Last refresh succeeded: {timestamp}", "fg": ACCENT_CYAN},
            )
            self._ui(
                self.log,
                "GHOST: Tier list refreshed. Current sandbox data is in hand.",
            )
        except Exception as exc:
            self._ui(
                self.tier_status.config,
                {"text": "Tier list refresh failed — load from file instead", "fg": ACCENT_ORANGE},
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
            args=(self.tier_path, csv_path, out_path, self.min_tier_var.get()),
            daemon=True,
        ).start()

    def _run_worker(self, xlsx_path, csv_path, out_path, min_tier):
        try:
            dim_path, summary = run_triage(xlsx_path, csv_path, out_path, min_tier)
        except Exception as exc:
            self._ui(
                self.log,
                f"GHOST: Scan hit some interference: {exc}",
            )
            self._ui(self.run_button.config, {"state": "normal"})
            return

        lock_label = "S tier" if min_tier == "S" else "A/S tier"
        unlock_label = "below S" if min_tier == "S" else "below A"

        lines = [
            "",
            "GHOST: Scan complete, Guardian. Here's the breakdown:",
            f"  Total weapons scanned:                    {summary['total']}",
            f"  LOCK ({lock_label}):                           {summary['lock']}",
            f"  LOCK (exotic -- always kept):              {summary['lock_exotic']}",
            f"  KEEP (best available in niche):            {summary['keep_niche']}",
            f"  UNLOCK (graded {unlock_label}):                    {summary['unlock']}",
            f"  REVIEW (ungraded category):                {summary['review_ungraded']}",
            f"  REVIEW (ambiguous match):                  {summary['review_ambiguous']}",
            f"  REVIEW (duplicate copies, can't tell apart): {summary['review_duplicate']}",
            f"  UNKNOWN (not in the analysis at all):      {summary['unknown']}",
        ]
        if summary["dim_import_skipped"]:
            lines.append(
                f"  Skipped from dim-import (already tagged/noted in DIM): "
                f"{summary['dim_import_skipped']}"
            )
        lines += [
            "",
            "GHOST: REVIEW/UNKNOWN items get no tag at all -- not enough evidence",
            "       either way, so nothing gets touched in DIM for them.",
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
