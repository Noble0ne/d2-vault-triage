#!/usr/bin/env python3
"""
Destiny 2 vault triage — cross-references a DIM weapon CSV export against
the "Endgame Analysis" spreadsheet tier lists and recommends LOCK (keep)
vs UNLOCK (safe to dismantle).

Usage:
    python3 d2-vault-triage.py <analysis.xlsx> <vault-export.csv> [output.csv]

Or run it with no arguments and it will ask for each path interactively --
drag a file from Finder/Explorer straight into the terminal window to fill
in its path instead of typing it out.

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

Designed by github.com/Noble0ne, with Claude.
"""
import sys, csv, re, zipfile, os
from xml.etree import ElementTree as ET

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

WEAPON_SHEET_NAMES = {
    'Autos', 'Bows', 'HCs', 'Pulses', 'Scouts', 'Sidearms', 'SMGs', 'BGLs',
    'Fusions', 'Glaives', 'Shotguns', 'Snipers', 'Rocket Sidearms', 'Traces',
    'HGLs', 'LFRs', 'LMGs', 'Rockets', 'Swords', 'Other', 'Exotic Weapons',
}


def load_shared_strings(z):
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    shared = []
    for si in root.findall('m:si', NS):
        texts = si.findall('.//m:t', NS)
        shared.append(''.join(t.text or '' for t in texts))
    return shared


def sheet_name_to_file(z):
    wb = z.read('xl/workbook.xml').decode('utf-8')
    rels = z.read('xl/_rels/workbook.xml.rels').decode('utf-8')
    relmap = dict(re.findall(
        r'Id="(rId\d+)" Type="[^"]*worksheet" Target="worksheets/(sheet\d+\.xml)"', rels))
    sheets = re.findall(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="(rId\d+)"', wb)
    return {name: relmap[rid] for name, rid in sheets if rid in relmap}


def read_sheet(z, sheetfile, shared):
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
    """name -> list of {name, category, energy, frame, notes, rank, tier}"""
    z = zipfile.ZipFile(xlsx_path)
    shared = load_shared_strings(z)
    name_to_file = sheet_name_to_file(z)
    index = {}
    for catname, sheetfile in name_to_file.items():
        if catname not in WEAPON_SHEET_NAMES:
            continue
        rows = read_sheet(z, sheetfile, shared)
        if len(rows) < 2:
            continue
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
    weapon that exists as two different intrinsic-frame versions)."""
    if len(entries) == 1:
        return entries[0]
    archetype = (vault_row.get('Archetype') or '').lower()
    element = (vault_row.get('Element') or '').lower()
    for e in entries:
        frame = (e.get('frame') or '').lower()
        if frame and frame in archetype:
            return e
    for e in entries:
        if (e.get('energy') or '').lower() == element:
            return e
    # fall back to the best (lowest rank number, or best-lettered tier)
    return sorted(entries, key=lambda e: _rank_val(e['rank']))[0]


def _rank_val(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 999


def recommend(tier, category):
    """S/A tier -> LOCK. B/C/D/E/F tier -> UNLOCK. Exotics are exempt from
    tier filtering entirely -- keep all of them regardless of grade.
    Two different kinds of "no tier" exist and are NOT treated the same:
    a weapon that resolved against the sheet but whose category (LMGs,
    Other) has no Tier/Rank column at all is UNLOCK (ungraded) -- present,
    just not scored, so niche-uniqueness can still save it. A weapon that
    isn't in the sheet by name at all is UNLOCK (untiered) -- no signal
    whatsoever, no value assumed."""
    if category == 'Exotic Weapons':
        return 'LOCK (exotic)'
    if tier in ('S', 'A'):
        return 'LOCK'
    if tier in ('B', 'C', 'D', 'E', 'F'):
        return 'UNLOCK'
    if category:
        return 'UNLOCK (ungraded)'
    return 'UNLOCK (untiered)'


def dim_tag(recommendation):
    """DIM's importable tags are: favorite, keep, infuse, junk, archive --
    there's no native lock/unlock tag, so map onto the closest equivalent."""
    if recommendation.startswith('LOCK') or recommendation.startswith('KEEP'):
        return 'keep'
    return 'junk'


def build_note(rec, tier, rank, category, sheet_notes):
    """Baseline note explaining the call before any niche-ranking context
    gets appended. For junk calls this spells out the actual grade and the
    sheet's own critique, rather than just a bare tier letter, so the reason
    the weapon isn't worth a slot is visible without cross-referencing the
    spreadsheet by hand."""
    sheet_notes = (sheet_notes or '').strip()
    if rec == 'LOCK (exotic)':
        base = 'Exotic -- always kept regardless of tier.'
    elif rec == 'LOCK':
        base = f"{tier} tier (Rank {rank} in {category})."
    elif rec == 'UNLOCK' and tier:
        base = (f"{tier} tier (Rank {rank} in {category}) -- graded below A, "
                f"not worth a vault slot at this grade.")
    elif rec == 'UNLOCK (ungraded)':
        base = (f"Present in the analysis under {category}, but that category "
                 "has no Tier/Rank data to grade it against.")
    else:
        base = ('Not present in the analysis at all -- no evidence it holds '
                'value in the current sandbox, safe to dismantle.')
    if sheet_notes:
        base = f"{base} {sheet_notes}"
    return base


def apply_niche_ranking(results, all_entries):
    """Within each (Category, Frame, Element) niche, the best-ranked owned
    weapon is kept even at a mediocre raw tier, since it's the only thing
    covering that niche right now. Every other owned weapon sharing the
    niche is genuinely redundant -- it stays UNLOCK, but the note names
    what's already covering the niche and what the sheet's actual best
    option is, so the choice is legible rather than a bare tier cutoff.
    Owning an exotic in the same Type+Element does NOT suppress this --
    the Exotic Weapons sheet has no Frame column, so an exotic's own
    archetype is unknown and can't be assumed to overlap a legendary's
    specific frame (e.g. an exotic Kinetic Strand Pulse with an Atomizing
    Rounds intrinsic doesn't replace a legendary Kinetic Strand Pulse
    running Micro-Missile -- different archetypes, different niches).
    Resolved-but-ungraded categories (LMGs, Other) still compete on niche
    uniqueness even with no Tier/Rank -- being the only owned example of
    that archetype is its own reason to keep it. Weapons that never
    resolved against the sheet at all never enter the ranking -- there's
    no data to reason from."""
    niches = {}
    for r in results:
        if not r['Category'] or r['Category'] == 'Exotic Weapons':
            continue
        niches.setdefault((r['Category'], r['Frame'], r['Element']), []).append(r)

    for (category, frame, element), members in niches.items():
        ranked = sorted(members, key=lambda m: _rank_val(m['Rank']))
        owned_best = ranked[0]
        descriptor = ' '.join(p for p in (frame, element, owned_best['Type']) if p)

        global_candidates = [
            e for e in all_entries
            if e['category'] == category
            and (e.get('frame') or '').lower() == (frame or '').lower()
            and (e.get('energy') or '').lower() == (element or '').lower()
        ]
        ranked_globals = [e for e in global_candidates if _rank_val(e['rank']) < 999]
        global_best = min(ranked_globals, key=lambda e: _rank_val(e['rank'])) if ranked_globals else None

        # If the best-owned item already qualifies as LOCK on its own merit
        # (S/A tier), leave its label alone -- it's not a placeholder, it's
        # genuinely good. Only promote when it wouldn't otherwise be kept.
        if not owned_best['Recommendation'].startswith('LOCK'):
            owned_best['Recommendation'] = 'KEEP (best available)'
            owned_best['Tag'] = 'keep'
            if not owned_best['Tier']:
                note = (f"Analysis doesn't score this category for {descriptor} -- kept anyway "
                         "because it's your only one in this niche.")
            elif global_best is None or global_best['name'] == owned_best['Name']:
                note = f"Best available {descriptor} you own -- top-ranked option in this niche."
            else:
                note = (f"Best {descriptor} you currently own (Rank {owned_best['Rank']}) -- "
                        f"still worth chasing {global_best['name']}, the top-ranked option in this niche.")
            owned_best['Notes'] = f"{owned_best['Notes']} | {note}" if owned_best['Notes'] else note

        owned_is_global_best = global_best is not None and global_best['name'] == owned_best['Name']
        for dup in ranked[1:]:
            if not owned_best['Tier']:
                dup_note = (f"Redundant -- {owned_best['Name']} already covers this ungraded "
                            f"{descriptor} niche.")
            elif global_best is None or owned_is_global_best:
                dup_note = (f"Redundant -- you already own the top-ranked {descriptor} in this "
                            f"niche: {owned_best['Name']} (Rank {owned_best['Rank']}). No need to "
                            f"chase anything further here.")
            else:
                dup_note = (f"Redundant -- you already own a better {descriptor}: "
                            f"{owned_best['Name']} (Rank {owned_best['Rank']}). Still missing the "
                            f"top-ranked option in this niche: {global_best['name']}.")
            dup['Notes'] = f"{dup['Notes']} | {dup_note}" if dup['Notes'] else dup_note


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


def main():
    if len(sys.argv) >= 3:
        xlsx_path, csv_path = sys.argv[1], sys.argv[2]
        out_path = sys.argv[3] if len(sys.argv) > 3 else 'vault-recommendations.csv'
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
        xlsx_path = prompt_input_path(
            "GHOST: First -- the Endgame Analysis spreadsheet. Where's it stashed?",
            "Drag it in, or check the path and try again.")
        csv_path = prompt_input_path(
            "GHOST: Good. Now your vault export -- the DIM CSV. Same deal.",
            "Drag it in, or check the path and try again.")
        out_path = prompt_output_path()
        print()
        print("GHOST: Give me a second... scanning your arsenal.\n")

    index = build_weapon_index(xlsx_path)
    all_entries = [e for entries in index.values() for e in entries]

    with open(csv_path, newline='', encoding='utf-8') as f:
        vault_rows = list(csv.DictReader(f))

    results = []
    for row in vault_rows:
        name = row['Name']
        resolved = resolve_name(name, index)
        category_hint = 'Exotic Weapons' if row.get('Rarity') == 'Exotic' else None
        if resolved:
            entry = pick_entry(index[resolved], row)
            rec = recommend(entry['tier'], entry['category'])
        else:
            entry = {'category': category_hint, 'energy': None, 'frame': None,
                      'tier': None, 'rank': None, 'notes': None}
            rec = recommend(None, category_hint)
        results.append({
            'Name': name,
            'Hash': row.get('Hash'),
            'Id': row.get('Id'),
            'Tag': dim_tag(rec),
            'Notes': build_note(rec, entry['tier'], entry['rank'], entry['category'], entry['notes']),
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
        })

    apply_niche_ranking(results, all_entries)

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    # DIM's CSV importer only reads Id/Hash/Tag/Notes -- write a matching
    # subset alongside the full analysis file above.
    dim_path = out_path.rsplit('.', 1)[0] + '-dim-import.csv'
    with open(dim_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['Id', 'Hash', 'Tag', 'Notes'])
        w.writeheader()
        for r in results:
            w.writerow({'Id': r['Id'], 'Hash': r['Hash'], 'Tag': r['Tag'], 'Notes': r['Notes']})

    lock = sum(1 for r in results if r['Recommendation'] == 'LOCK')
    lock_exotic = sum(1 for r in results if r['Recommendation'] == 'LOCK (exotic)')
    keep_niche = sum(1 for r in results if r['Recommendation'] == 'KEEP (best available)')
    unlock = sum(1 for r in results if r['Recommendation'] == 'UNLOCK')
    ungraded = sum(1 for r in results if r['Recommendation'] == 'UNLOCK (ungraded)')
    untiered = sum(1 for r in results if r['Recommendation'] == 'UNLOCK (untiered)')
    print()
    print("GHOST: Scan complete, Guardian. Here's the breakdown:")
    print(f"  Total weapons scanned:              {len(results)}")
    print(f"  LOCK (A/S tier):                     {lock}")
    print(f"  LOCK (exotic -- always kept):        {lock_exotic}")
    print(f"  KEEP (best available in niche):      {keep_niche}")
    print(f"  UNLOCK (B tier or below):            {unlock}")
    print(f"  UNLOCK (ungraded, no niche gap):     {ungraded}")
    print(f"  UNLOCK (not in the analysis at all): {untiered}")
    print()
    print(f"GHOST: Full report's in {out_path}.")
    print(f"GHOST: When you're ready, drag {dim_path} into DIM's Import CSV")
    print("       to apply the tags. I'll leave the actual dismantling to you.")


if __name__ == '__main__':
    main()
