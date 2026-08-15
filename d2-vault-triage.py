#!/usr/bin/env python3
"""
Destiny 2 vault triage — cross-references a DIM weapon CSV export against
the "Endgame Analysis" spreadsheet tier lists and recommends LOCK (keep)
vs UNLOCK (safe to dismantle).

Usage:
    python3 d2-vault-triage.py <vault-export.csv> [output.csv]

The Endgame Analysis spreadsheet is downloaded automatically (it's a public
Google Sheet) -- you only ever give it your DIM export and, optionally,
where to write the results. Run it with no arguments and it will ask for
each path interactively -- drag a file from Finder/Explorer straight into
the terminal window to fill in its path instead of typing it out.

To use a different or local copy of the analysis spreadsheet instead of the
auto-downloaded one, pass all three paths explicitly:
    python3 d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]

Threshold: S/A tier -> LOCK. B/C/D/E/F tier -> UNLOCK. Untiered/not in the
sheet -> UNLOCK (holds no value in the current sandbox). Exotics are exempt
from all of the above -- every owned exotic locks, regardless of grade.

Niche ranking: within each (Category, Frame, Element) niche -- e.g.
Rocket-frame Kinetic Pulse Rifle, or Strand Bow -- the best-ranked owned
weapon is kept even if its raw tier is mediocre, since it's the only thing
covering that niche until something better is acquired. Any other owned
weapon in the same niche is redundant and stays UNLOCK, with a note pointing
at what's already covering the niche and what the actual best option is.
A niche already covered by an owned exotic skips this entirely.

Reuse for anyone else: point it at their DIM CSV export, keep the same xlsx.

Data/network scope, for anyone checking before they run this: the script
downloads the current analysis spreadsheet from its public Google Sheets
export link (view-only, no sign-in, no Destiny account credentials or API
access of any kind), reads the local DIM .csv you give it, and writes the
two output .csv files next to it. Nothing else is read, written, or sent
anywhere. Pass an explicit analysis.xlsx path (see Usage above) to skip the
download entirely and use a local file instead.

Designed by github.com/Noble0ne, with Claude.
"""
import sys, csv, re, zipfile, os, tempfile, urllib.request, hashlib
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# An .xlsx file is just a zip archive of XML files (the OOXML format) --
# zipfile + ElementTree (both stdlib) are enough to read it, no openpyxl
# or other pip install required. NS is the XML namespace every spreadsheet
# tag in those files lives under; ElementTree needs it to match tags.
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

WEAPON_SHEET_NAMES = {
    'Autos', 'Bows', 'HCs', 'Pulses', 'Scouts', 'Sidearms', 'SMGs', 'BGLs',
    'Fusions', 'Glaives', 'Shotguns', 'Snipers', 'Rocket Sidearms', 'Traces',
    'HGLs', 'LFRs', 'LMGs', 'Rockets', 'Swords', 'Other', 'Exotic Weapons',
}

TIER_LIST_URL = (
    'https://docs.google.com/spreadsheets/d/'
    '1JM-0SlxVDAi-C6rGVlLxa-J1WGewEeL8Qvq4htWZHhY/export?format=xlsx'
)


def download_tier_list(dest_path=None):
    """Downloads Aegis's current Endgame Analysis workbook straight from its
    public Google Sheets export link -- no manual File -> Download step
    needed. Writes to a temp file first and only replaces the real
    destination once the download is confirmed to be an actual .xlsx (a
    real zip archive) -- Google serves an HTML sign-in/quota page instead of
    the file if the sheet ever becomes unreachable, and this catches that
    case rather than silently handing garbage to build_weapon_index().
    Returns the path to the downloaded file as a string. Raises on any
    failure (network error, bad response) -- callers decide how to surface
    that: the CLI falls back to asking for a local file, the GUI shows it
    in the tier-list status label."""
    if dest_path is None:
        folder = Path(tempfile.gettempdir()) / 'd2-vault-triage'
        folder.mkdir(parents=True, exist_ok=True)
        dest_path = folder / 'aegis-endgame-analysis.xlsx'
    dest_path = Path(dest_path)
    tmp_path = dest_path.with_suffix('.tmp')

    request = urllib.request.Request(TIER_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        tmp_path.write_bytes(response.read())

    if not zipfile.is_zipfile(tmp_path):
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise ValueError(
            "Download did not return a valid .xlsx workbook -- Google may "
            "have served an error or sign-in page instead of the file.")

    os.replace(tmp_path, dest_path)
    return str(dest_path)


def load_shared_strings(z):
    """OOXML doesn't store repeated text inline in each cell -- every unique
    string in the whole workbook lives once in xl/sharedStrings.xml, and
    individual cells just reference it by index. This reads that table into
    a plain list, so a cell's stored index (0, 1, 2, ...) becomes a lookup:
    shared[index] -> the actual text. Without this, string cells would come
    back as bare numbers instead of weapon names."""
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    shared = []
    for si in root.findall('m:si', NS):
        texts = si.findall('.//m:t', NS)
        shared.append(''.join(t.text or '' for t in texts))
    return shared


def sheet_name_to_file(z):
    """The tab name you see in Excel/Sheets (e.g. "Autos") isn't the name of
    the file that holds its data -- internally it's just "sheet7.xml" or
    similar. Getting from one to the other takes two files: workbook.xml
    lists each visible tab name next to a relationship id (rId3, rId7, ...),
    and workbook.xml.rels maps those same relationship ids to the actual
    worksheet filename. This stitches both together into one direct lookup:
    tab name -> worksheet file -- so the rest of the script can ask for
    "Autos" by name instead of guessing which sheetN.xml it landed in."""
    wb = z.read('xl/workbook.xml').decode('utf-8')
    rels = z.read('xl/_rels/workbook.xml.rels').decode('utf-8')
    relmap = dict(re.findall(
        r'Id="(rId\d+)" Type="[^"]*worksheet" Target="worksheets/(sheet\d+\.xml)"', rels))
    sheets = re.findall(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="(rId\d+)"', wb)
    return {name: relmap[rid] for name, rid in sheets if rid in relmap}


def read_sheet(z, sheetfile, shared):
    """Parses one worksheet's raw XML into a plain list of row dicts, one
    per spreadsheet row, keyed by column letter (A, B, C, ...) -- the same
    layout you'd see looking at the sheet in a spreadsheet app. Each cell
    tag (<c>) carries its own column letter in its "r" attribute (e.g.
    r="C4" -> column C); a cell tagged t="s" holds a shared-string index
    rather than literal text, so those get resolved through the `shared`
    lookup from load_shared_strings() before being stored. Cells with no
    value (empty in the spreadsheet) come back as None."""
    root = ET.fromstring(z.read(f'xl/worksheets/{sheetfile}'))
    sheetdata = root.find('m:sheetData', NS)
    rows = []
    for row in sheetdata.findall('m:row', NS):
        cells = {}
        for c in row.findall('m:c', NS):
            col = re.match(r'[A-Z]+', c.get('r')).group()
            t = c.get('t')
            v = c.find('m:v', NS)
            val = v.text if v is not None else None
            if t == 's' and val is not None:
                val = shared[int(val)]
            cells[col] = val
        rows.append(cells)
    return rows


def build_weapon_index(xlsx_path):
    """name -> list of {name, category, energy, frame, notes, rank, tier}

    Reads the whole analysis spreadsheet once and turns it into an in-memory
    lookup keyed by weapon name, so the main loop can look up each owned
    weapon by name instead of re-scanning the sheet per weapon. Only sheets
    in WEAPON_SHEET_NAMES are read -- any other tab in the workbook (notes,
    changelog, whatever else the analysis includes) is skipped entirely."""
    z = zipfile.ZipFile(xlsx_path)  # .xlsx is a zip; open it as one directly
    shared = load_shared_strings(z)
    name_to_file = sheet_name_to_file(z)
    index = {}
    for catname, sheetfile in name_to_file.items():
        if catname not in WEAPON_SHEET_NAMES:
            continue
        rows = read_sheet(z, sheetfile, shared)
        if len(rows) < 2:
            continue
        # Row 0 is a title row (e.g. "Auto Rifles"), row 1 is the real
        # column header. colmap flips {column letter: header text} around
        # to {header text: column letter}, so the rest of this function can
        # ask for colmap.get('Tier') instead of hardcoding a column letter --
        # spreadsheet columns get inserted/reordered; header names don't.
        header = rows[1]
        colmap = {v: k for k, v in header.items() if v}
        name_col = colmap.get('Name')
        if not name_col:
            continue
        energy_col, frame_col = colmap.get('Energy'), colmap.get('Frame')
        notes_col, tier_col = colmap.get('Notes'), colmap.get('Tier')
        rank_col = colmap.get('Rank')
        if rank_col is None and catname != 'Exotic Weapons':
            # Weapon-category sheets renamed Rank -> # as of the 08-11-26 analysis.
            # Exotic Weapons also has a '#' column, but it's a row index, not a rank --
            # excluded so it doesn't get misread as one.
            rank_col = colmap.get('#')
        for r in rows[2:]:
            name = r.get(name_col)
            if not name:
                continue
            entry = {
                'name': name,
                'category': catname,
                'energy': r.get(energy_col) if energy_col else None,
                'frame': r.get(frame_col) if frame_col else None,
                'notes': r.get(notes_col) if notes_col else None,
                'rank': r.get(rank_col) if rank_col else None,
                'tier': r.get(tier_col) if tier_col else None,
            }
            index.setdefault(name, []).append(entry)
    return index


def resolve_name(name, index):
    if name in index:
        return name
    base = re.sub(r'\s*\(Adept\)\s*$', '', name).strip()
    if base in index:
        return base
    return None


def pick_entry(entries, vault_row):
    """Disambiguate when a name maps to multiple analysis rows (e.g. a
    weapon that exists as two different intrinsic-frame versions).

    Returns (entry, ambiguous). ambiguous is True only when more than one
    entry exists AND neither frame nor element narrowed it down -- callers
    treat that as a signal to flag the item for review rather than trust
    the pick. It is NOT set when frame or element actually resolves the
    choice; that's a confident match, just not a bare 1:1 name lookup."""
    if len(entries) == 1:
        return entries[0], False
    archetype = (vault_row.get('Archetype') or '').lower()
    element = (vault_row.get('Element') or '').lower()
    for e in entries:
        frame = (e.get('frame') or '').lower()
        if frame and frame in archetype:
            return e, False
    for e in entries:
        if (e.get('energy') or '').lower() == element:
            return e, False
    # Neither frame nor element disambiguated it -- there is no principled
    # way to pick one of these over the others, so the caller gets told
    # this pick is a guess instead of silently receiving the best-ranked row.
    return sorted(entries, key=lambda e: _rank_val(e['rank']))[0], True


def _rank_val(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 999


TIER_ORDER = ('S', 'A', 'B', 'C', 'D', 'E', 'F')
# best-to-worst, matching Aegis's own lettering -- index() on this tuple is how
# recommend() below compares a weapon's tier against the chosen keep-threshold
# without hardcoding which specific letters count as "good enough to keep".


def recommend(tier, category, min_tier='A'):
    """Tiers at or above min_tier -> LOCK ('A' keeps S+A, the default;
    'S' keeps S only). Everything below min_tier -> UNLOCK -- this is a
    confident call either way, because a tier grade IS positive evidence,
    whichever direction it points. Exotics are exempt from tier filtering
    entirely -- keep all of them regardless of grade.

    Two different kinds of "no signal" exist and both fail closed to
    REVIEW/UNKNOWN rather than defaulting to UNLOCK -- absence of evidence
    is not evidence of absence, and a junk tag is a strong claim to make
    on no data:
      - REVIEW (ungraded): resolved against the sheet by name, but that
        category (LMGs, Other) has no Tier/Rank column at all. We know
        which weapon this is, just not whether it's good.
      - UNKNOWN (unresolved): isn't in the sheet by name at all. Could be
        new/reissued, a name-matching miss, or genuinely out of scope
        (PvP/utility/speedrun tech Aegis's PvE sheet doesn't cover) --
        no signal whatsoever, so no call is made either way.

    A third fail-closed case, REVIEW (ambiguous match), is NOT decided
    here -- it depends on pick_entry()'s disambiguation result, which the
    caller has and this function doesn't, so the caller overrides this
    function's return value when that flag is set."""
    if category == 'Exotic Weapons':
        return 'LOCK (exotic)'
    if tier in TIER_ORDER:
        if TIER_ORDER.index(tier) <= TIER_ORDER.index(min_tier):
            return 'LOCK'
        return 'UNLOCK'
    if category:
        return 'REVIEW (ungraded)'
    return 'UNKNOWN (unresolved)'


def dim_tag(recommendation):
    """DIM's importable tags are: favorite, keep, infuse, junk, archive --
    there's no native lock/unlock tag, so map onto the closest equivalent.

    This is deliberately an allow-list, not a deny-list: only LOCK*/KEEP*
    map to 'keep' and only the exact confident 'UNLOCK' maps to 'junk'.
    Everything else (REVIEW*, UNKNOWN*) returns '' -- no tag at all, which
    is what keeps those rows out of the DIM-import CSV entirely (see
    main()/run_triage()). A junk tag is an assertion the item is worthless;
    this function should never make that assertion without positive
    evidence backing it."""
    if recommendation.startswith('LOCK') or recommendation.startswith('KEEP'):
        return 'keep'
    if recommendation == 'UNLOCK':
        return 'junk'
    return ''


def build_note(rec, tier, rank, category, sheet_notes, min_tier='A'):
    """Baseline note explaining the call before any niche-ranking context
    gets appended. For junk calls this spells out the actual grade and the
    sheet's own critique, rather than just a bare tier letter, so the reason
    the weapon isn't worth a slot is visible without cross-referencing the
    spreadsheet by hand. For REVIEW/UNKNOWN calls this spells out exactly
    what evidence is missing and why manual review is being asked for,
    rather than a bare unsupported claim."""
    sheet_notes = (sheet_notes or '').strip()
    if rec == 'LOCK (exotic)':
        base = 'Exotic -- always kept regardless of tier.'
    elif rec == 'LOCK':
        base = f"{tier} tier (Rank {rank} in {category})."
    elif rec == 'UNLOCK' and tier:
        base = (f"{tier} tier (Rank {rank} in {category}) -- graded below "
                f"{min_tier}, not worth a vault slot at this grade.")
    elif rec == 'REVIEW (ungraded)':
        base = (f"Present in the analysis under {category}, but that category "
                 "has no Tier/Rank data to grade it against. No tag applied -- "
                 "review manually.")
    elif rec == 'REVIEW (ambiguous match)':
        base = (f"Matches more than one entry in the analysis under this name, "
                 "and neither frame nor element narrowed it down to a single "
                 "row. No tag applied -- confirm which entry actually applies "
                 "before tagging.")
    else:
        base = ('Not found in the analysis by name at all -- could be new, '
                'reissued, a name-matching miss, or genuinely outside the '
                "sheet's scope (PvP/utility/speedrun tech). No tag applied -- "
                'this is not evidence the item is bad, just that there is no '
                'signal either way. Review manually.')
    if sheet_notes:
        base = f"{base} {sheet_notes}"
    return base


def apply_niche_ranking(results, all_entries):
    """Within each (Category, Frame, Element) niche, the best-ranked owned
    weapon not already independently LOCK gets promoted to KEEP (best
    available), since it's the only thing covering that niche until
    something better is acquired. Every other member gets a note pointing
    at what already covers the niche and what the sheet's actual best
    option is -- "same broad niche" is flagged for the operator to review,
    not asserted as settled redundancy, since two weapons sharing
    Category/Frame/Element can still serve very different roles (perk
    package, damage loop, ammo economy, subclass synergy, origin trait,
    encounter geometry).

    Only confidently-graded members (LOCK/UNLOCK) enter this at all --
    REVIEW/UNKNOWN items carry no reliable Tier/Frame signal to reason
    about niche coverage from, so promoting one based on an uncertain
    grade would just move the same problem up a level.

    Duplicate owned copies of the exact same weapon always tie on
    Rank/Tier (same sheet entry) -- there is no principled way to say one
    copy is better than another without looking at rolled perks, which
    this pass doesn't have. Rather than let sort-order stability silently
    crown one copy the "best available" winner, every copy of a tied,
    not-already-locked duplicate group is flagged REVIEW (duplicate
    copies) instead, with no tag applied to any of them. If the niche
    isn't already covered by something independently LOCK, and the single
    best-ranked *candidate for promotion* is one of those ambiguous
    groups, nothing else gets promoted in its place either -- telling the
    operator a strictly worse weapon is their "best available" would
    misrepresent the actual situation (better coverage exists, it's just
    not identified at the copy level).

    Owning an exotic in the same Type+Element does NOT suppress this --
    the Exotic Weapons sheet has no Frame column, so an exotic's own
    archetype is unknown and can't be assumed to overlap a legendary's
    specific frame (e.g. an exotic Kinetic Strand Pulse with an Atomizing
    Rounds intrinsic doesn't replace a legendary Kinetic Strand Pulse
    running Micro-Missile -- different archetypes, different niches)."""
    niches = {}
    for r in results:
        if r['Category'] == 'Exotic Weapons':
            continue
        if not r['Recommendation'].startswith(('LOCK', 'UNLOCK')):
            continue
        niches.setdefault((r['Category'], r['Frame'], r['Element']), []).append(r)

    for (category, frame, element), members in niches.items():
        descriptor = ' '.join(p for p in (frame, element, members[0]['Type']) if p)

        by_name = {}
        for m in members:
            by_name.setdefault(m['Name'], []).append(m)

        global_candidates = [
            e for e in all_entries
            if e['category'] == category
            and (e.get('frame') or '').lower() == (frame or '').lower()
            and (e.get('energy') or '').lower() == (element or '').lower()
        ]
        ranked_globals = [e for e in global_candidates if _rank_val(e['rank']) < 999]
        global_best = min(ranked_globals, key=lambda e: _rank_val(e['rank'])) if ranked_globals else None

        # A niche already covered by something independently LOCK needs no
        # promotion decision at all -- that item is the reference point for
        # everyone else's note, full stop. Only weapons that AREN'T already
        # locked are candidates for the "best available" promotion below.
        locked_names = {name for name, copies in by_name.items()
                         if copies[0]['Recommendation'].startswith('LOCK')}

        # Among the not-already-locked weapons: duplicate copies of the same
        # weapon always tie on Rank/Tier, so there's no principled way to
        # pick one copy over another as the promotion candidate -- flag the
        # whole group as REVIEW instead of letting sort-order stability
        # silently decide. Single-copy weapons go into promotable normally.
        promotable = []
        excluded_names = set()
        for name, copies in by_name.items():
            if name in locked_names:
                continue
            if len(copies) > 1:
                excluded_names.add(name)
                dup_note = (
                    f"You own {len(copies)} copies of this weapon -- same tier/rank on "
                    "every copy, so this pass can't tell which one is actually worth "
                    "keeping without looking at rolled perks. No tag applied on any copy; "
                    "review them against each other manually."
                )
                for copy in copies:
                    copy['Recommendation'] = 'REVIEW (duplicate copies)'
                    copy['Tag'] = ''
                    copy['Notes'] = f"{copy['Notes']} | {dup_note}" if copy['Notes'] else dup_note
            else:
                promotable.append(copies[0])

        owned_best = None
        if locked_names:
            # Already covered -- reference the best-ranked locked weapon for
            # everyone else's note. (Normally there's just one; if several
            # independently-locked weapons happen to share a niche, the
            # best-ranked of them is the one referenced.)
            locked_members = [by_name[name][0] for name in locked_names]
            owned_best = min(locked_members, key=lambda m: _rank_val(m['Rank']))
        else:
            # Nothing already covers this niche -- the best-ranked NOT-yet-
            # locked, non-duplicate candidate gets promoted, UNLESS the true
            # best-ranked weapon among ALL members (including the excluded
            # duplicate groups) is better than anything actually promotable.
            # In that case the real best is stuck in an ambiguous group, and
            # promoting a worse one in its place would misrepresent things.
            best_rank_overall = min((_rank_val(m['Rank']) for m in members), default=999)
            best_rank_promotable = min((_rank_val(m['Rank']) for m in promotable), default=999)
            if promotable and best_rank_promotable <= best_rank_overall:
                ranked = sorted(promotable, key=lambda m: _rank_val(m['Rank']))
                owned_best = ranked[0]
                owned_best['Recommendation'] = 'KEEP (best available)'
                owned_best['Tag'] = 'keep'
                if global_best is None or global_best['name'] == owned_best['Name']:
                    note = f"Best available {descriptor} you own -- top-ranked option in this niche."
                else:
                    note = (f"Best {descriptor} you currently own (Rank {owned_best['Rank']}) -- "
                            f"still worth chasing {global_best['name']}, the top-ranked option in this niche.")
                owned_best['Notes'] = f"{owned_best['Notes']} | {note}" if owned_best['Notes'] else note

        if owned_best is None:
            # Every not-yet-locked candidate was an unresolvable duplicate
            # group, with nothing already-locked to fall back on either --
            # no safe baseline to compare the rest against, so skip the
            # note-only pass too rather than point at a "best available"
            # that isn't one.
            continue

        owned_is_global_best = global_best is not None and global_best['name'] == owned_best['Name']
        for name, copies in by_name.items():
            if name == owned_best['Name'] or name in excluded_names or name in locked_names:
                continue
            for member in copies:
                if owned_is_global_best or global_best is None:
                    dup_note = (
                        f"Same broad niche as {owned_best['Name']} (Rank {owned_best['Rank']}), "
                        "which you already own and which tops this niche in the analysis -- "
                        "review whether you actually need both."
                    )
                else:
                    dup_note = (
                        f"Same broad niche as {owned_best['Name']} (Rank {owned_best['Rank']}), "
                        "which you already own -- review whether you need both. Still missing "
                        f"the top-ranked option in this niche: {global_best['name']}."
                    )
                member['Notes'] = f"{member['Notes']} | {dup_note}" if member['Notes'] else dup_note


def triage_vault(xlsx_path, csv_path, min_tier='A'):
    """Shared pipeline behind both the CLI and the GUI: build the weapon
    index, resolve/grade every owned weapon, apply niche ranking, and decide
    which rows are safe to include in a DIM-import file. Kept as a single
    implementation so the recommendation logic can't drift between the two
    front ends -- this used to be duplicated inline in main() and in the
    GUI's run_triage(), which is exactly the kind of thing that quietly goes
    out of sync the next time only one copy gets fixed.

    Each returned dict includes ExistingTag/ExistingNotes (whatever the
    input DIM export already had for that item) and DimImportSkipReason
    (non-empty when the row is deliberately left out of the DIM-import
    file). See write_dim_import_csv() for why that matters: DIM's importer
    unconditionally overwrites the tag and note of every row present in an
    imported file, so preserving something the operator already set
    requires never including that row at all -- there's no "only touch
    this if it's currently blank" mode on DIM's side to lean on instead.

    Every result also carries SourceHash/CaptureTime, stamped from the
    actual xlsx_path used for this run. Aegis's spreadsheet is a live
    document that changes over time -- a recommendation is only as good as
    the specific version of the sheet it came from, so the output should
    say which version that was rather than leave it implicit. The hash is
    of the raw file's own bytes, not the sheet's own claimed version/date
    (which this script doesn't parse), so it's traceable even if two runs
    used a differently-dated download of the same underlying content."""
    source_hash = hashlib.sha256(Path(xlsx_path).read_bytes()).hexdigest()[:12]
    capture_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    index = build_weapon_index(xlsx_path)
    all_entries = [e for entries in index.values() for e in entries]

    with open(csv_path, newline='', encoding='utf-8') as f:
        vault_rows = list(csv.DictReader(f))

    if not vault_rows:
        raise ValueError("The DIM CSV contains no weapon rows.")

    results = []
    for row in vault_rows:
        name = row['Name']
        resolved = resolve_name(name, index)
        category_hint = 'Exotic Weapons' if row.get('Rarity') == 'Exotic' else None
        ambiguous = False
        if resolved:
            entry, ambiguous = pick_entry(index[resolved], row)
            rec = recommend(entry['tier'], entry['category'], min_tier)
        else:
            entry = {'category': category_hint, 'energy': None, 'frame': None,
                      'tier': None, 'rank': None, 'notes': None}
            rec = recommend(None, category_hint, min_tier)

        # pick_entry()'s ambiguity signal overrides whatever recommend() said --
        # an unresolved 1-of-several-rows guess isn't a confident LOCK/UNLOCK
        # regardless of which row happened to get picked.
        if ambiguous:
            rec = 'REVIEW (ambiguous match)'

        results.append({
            'Name': name,
            'Hash': row.get('Hash'),
            'Id': row.get('Id'),
            'Tag': dim_tag(rec),
            'Notes': build_note(rec, entry['tier'], entry['rank'], entry['category'], entry['notes'], min_tier),
            'Type': row.get('Type'),
            'Element': row.get('Element'),
            'Frame': entry['frame'],
            'Category': entry['category'],
            'Tier': entry['tier'],
            'Rank': entry['rank'],
            'Recommendation': rec,
            'Crafted': row.get('Crafted'),
            'MasterworkTier': row.get('Masterwork Tier'),
            'Equipped': row.get('Equipped'),
            'ExistingTag': (row.get('Tag') or '').strip(),
            'ExistingNotes': (row.get('Notes') or '').strip(),
            'SourceHash': source_hash,
            'CaptureTime': capture_time,
        })

    apply_niche_ranking(results, all_entries)

    # Decided only now that niche ranking has finished -- it can change Tag/
    # Recommendation after the per-item pass above, so checking any earlier
    # would risk acting on a Tag that's about to be overwritten anyway.
    for r in results:
        if r['Tag'] and (r['ExistingTag'] or r['ExistingNotes']):
            r['DimImportSkipReason'] = (
                'Already has an existing DIM tag/note -- not touched, to avoid '
                'overwriting it. Review manually.'
            )
        else:
            r['DimImportSkipReason'] = ''

    return results


def write_dim_import_csv(results, dim_path):
    """Writes the DIM-importable subset (Id/Hash/Tag/Notes). Only rows with
    a real tag AND no DimImportSkipReason get included -- DIM's importer
    (see importTagsNotesFromCsv() in DIM's own source) unconditionally
    calls setItemTagsBulk()/setItemNote() for every single row present in
    an imported file, with no partial-update or "leave blank fields alone"
    mode. A row's absence from this file is the only thing that reliably
    keeps DIM from touching that item at all -- an empty Tag/Notes value
    inside an included row is not the same as omitting the row, and isn't
    trusted here to mean "leave it alone."""
    with open(dim_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['Id', 'Hash', 'Tag', 'Notes'])
        w.writeheader()
        for r in results:
            if r['Tag'] and not r['DimImportSkipReason']:
                w.writerow({'Id': r['Id'], 'Hash': r['Hash'], 'Tag': r['Tag'], 'Notes': r['Notes']})


def summarize_results(results):
    """Per-outcome counts for the post-run breakdown, shared by the CLI
    print and the GUI log so the two can't report different numbers for
    the same run."""
    def count(target):
        return sum(1 for r in results if r['Recommendation'] == target)
    return {
        'total': len(results),
        'lock': count('LOCK'),
        'lock_exotic': count('LOCK (exotic)'),
        'keep_niche': count('KEEP (best available)'),
        'unlock': count('UNLOCK'),
        'review_ungraded': count('REVIEW (ungraded)'),
        'review_ambiguous': count('REVIEW (ambiguous match)'),
        'review_duplicate': count('REVIEW (duplicate copies)'),
        'unknown': count('UNKNOWN (unresolved)'),
        'dim_import_skipped': sum(1 for r in results if r['Tag'] and r['DimImportSkipReason']),
    }


def clean_dragged_path(raw):
    """Dragging a file into a terminal window types its path for you, but
    the exact text varies by OS/app: Terminal.app backslash-escapes spaces
    (My\\ File.xlsx), while Windows Command Prompt/PowerShell and some
    terminals wrap the whole thing in quotes instead ("My File.xlsx").
    Handle both so either drag-and-drop style, or manual typing, works."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    raw = raw.replace('\\ ', ' ')
    return os.path.expanduser(raw)


def prompt_input_path(question, retry_hint):
    while True:
        raw = input(f"{question}\n> ").strip()
        path = clean_dragged_path(raw)
        if not path:
            print(f"GHOST: I need something to work with, Guardian. {retry_hint}\n")
            continue
        if not os.path.isfile(path):
            print(f"GHOST: Nothing at that path that I can see. {retry_hint}\n")
            continue
        return path


def prompt_output_path():
    raw = input(
        "GHOST: Last thing -- where do you want the recommendations written?\n"
        "Press Enter and I'll call it 'vault-recommendations.csv', or point me\n"
        "somewhere else -- a folder, or a folder plus a filename.\n> "
    ).strip()
    path = clean_dragged_path(raw)
    if not path:
        return 'vault-recommendations.csv'
    if os.path.isdir(path):
        path = os.path.join(path, 'vault-recommendations.csv')
    return path


def prompt_min_tier():
    raw = input(
        "GHOST: Keep threshold -- lock A-tier and up (default), or S-tier\n"
        "only? Press Enter for A-tier and up, or type S.\n> "
    ).strip().upper()
    return 'S' if raw == 'S' else 'A'


def main():
    args = sys.argv[1:]
    # --s-only is stripped out of args before any of the length checks below run,
    # so it composes with all three existing invocation forms (0/2-3/4 positional
    # args) instead of needing its own separate argument-count branch.
    min_tier = 'S' if '--s-only' in args else 'A'
    args = [a for a in args if a != '--s-only']

    if len(args) == 3:
        # Explicit override: analysis.xlsx, vault-export.csv, output.csv --
        # skips the download entirely in favor of a specific local file.
        xlsx_path, csv_path, out_path = args[0], args[1], args[2]
    elif len(args) in (1, 2):
        csv_path = args[0]
        out_path = args[1] if len(args) > 1 else 'vault-recommendations.csv'
        print("GHOST: Pulling the current Endgame Analysis before we start...")
        try:
            xlsx_path = download_tier_list()
        except Exception as exc:
            sys.exit(
                f"GHOST: Couldn't reach the Endgame Analysis spreadsheet: {exc}\n"
                f"GHOST: Pass a local copy explicitly instead: python3 "
                f"d2-vault-triage.py <analysis.xlsx> {csv_path} {out_path}")
    else:
        print("-" * 60)
        print("GHOST: Online. Running diagnostics... vault's heavier than")
        print("       last time, Guardian.")
        print("GHOST: Let's sort out what's worth carrying and what's dead")
        print("       weight. I'll need a couple things from you first.")
        print("-" * 60)
        print()
        print("Tip: drag a file straight from Finder/Explorer into this window")
        print("instead of typing the path out -- I'm not picky about how the")
        print("intel reaches me.\n")
        print("GHOST: First, let me pull the current Endgame Analysis spreadsheet.")
        try:
            xlsx_path = download_tier_list()
            print("GHOST: Got it -- current sandbox data in hand.\n")
        except Exception as exc:
            print(f"GHOST: Uplink dropped out, couldn't download it: {exc}\n")
            xlsx_path = prompt_input_path(
                "GHOST: Point me at a local copy of the analysis spreadsheet instead.",
                "Drag it in, or check the path and try again.")
        csv_path = prompt_input_path(
            "GHOST: Good. Now your vault export -- the DIM CSV. Same deal.",
            "Drag it in, or check the path and try again.")
        out_path = prompt_output_path()
        min_tier = prompt_min_tier()
        print()
        print("GHOST: Give me a second... scanning your arsenal.\n")

    try:
        results = triage_vault(xlsx_path, csv_path, min_tier)
    except ValueError as exc:
        sys.exit(f"GHOST: {exc}")

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    dim_path = out_path.rsplit('.', 1)[0] + '-dim-import.csv'
    write_dim_import_csv(results, dim_path)

    s = summarize_results(results)
    print()
    print("GHOST: Scan complete, Guardian. Here's the breakdown:")
    print(f"  Total weapons scanned:                    {s['total']}")
    print(f"  LOCK (A/S tier):                           {s['lock']}")
    print(f"  LOCK (exotic -- always kept):              {s['lock_exotic']}")
    print(f"  KEEP (best available in niche):            {s['keep_niche']}")
    print(f"  UNLOCK (below threshold):                  {s['unlock']}")
    print(f"  REVIEW (ungraded category):                {s['review_ungraded']}")
    print(f"  REVIEW (ambiguous match):                  {s['review_ambiguous']}")
    print(f"  REVIEW (duplicate copies, can't tell apart): {s['review_duplicate']}")
    print(f"  UNKNOWN (not in the analysis at all):      {s['unknown']}")
    if s['dim_import_skipped']:
        print(f"  Skipped from dim-import (already tagged/noted in DIM): {s['dim_import_skipped']}")
    print()
    print("GHOST: REVIEW/UNKNOWN items get no tag at all -- not enough evidence")
    print("       either way, so nothing gets touched in DIM for them.")
    print(f"GHOST: Full report's in {out_path}.")
    print(f"GHOST: When you're ready, drag {dim_path} into DIM's Import CSV")
    print("       to apply the tags. I'll leave the actual dismantling to you.")


if __name__ == '__main__':
    main()
