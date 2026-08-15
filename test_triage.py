#!/usr/bin/env python3
"""
Regression tests for d2-vault-triage.py, covering the specific failure
modes raised in GitHub issue #6 (roll-unawareness, fail-open defaults,
silent ambiguous matches, destructive DIM re-import, "redundant" claims
stronger than the evidence). Run with:

    python3 test_triage.py

Stdlib unittest only -- no pytest, no extra pip installs, matching the
CLI script's own zero-dependency stance. importlib is used to load the
hyphenated core script, the same trick d2-vault-triage-gui.py already
uses for the same reason (a module name can't contain a hyphen).
"""
import csv
import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

CORE_PATH = Path(__file__).resolve().parent / 'd2-vault-triage.py'
spec = importlib.util.spec_from_file_location('d2_vault_triage_core', CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def build_xlsx(path, rows_by_sheet):
    """Hand-builds a minimal valid .xlsx (OOXML zip) for a single-sheet or
    multi-sheet workbook, auto-indexing shared strings so callers never have
    to compute cell indices by hand -- exactly the kind of off-by-one that's
    easy to get wrong (see: the first draft of this fixture-builder itself,
    caught by actually running the pipeline against it).

    rows_by_sheet: {sheet_name: [[cell, cell, ...], ...]} -- row 0 is treated
    as a title row (matching Aegis's actual layout, one banner row above the
    real header), row 1 is the header, rows 2+ are data. Empty-string cells
    are written as real empty shared strings, not omitted, so column
    alignment matches a real spreadsheet."""
    strings = []

    def sidx(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    sheet_names = list(rows_by_sheet.keys())
    sheets_xml_entries = []
    rels_entries = []
    with zipfile.ZipFile(path, 'w') as z:
        for i, name in enumerate(sheet_names, start=1):
            rows = rows_by_sheet[name]
            row_tags = []
            for r_idx, row in enumerate(rows, start=1):
                cells = []
                for c_idx, value in enumerate(row):
                    col = chr(ord('A') + c_idx)
                    cells.append(f'<c r="{col}{r_idx}" t="s"><v>{sidx(value)}</v></c>')
                row_tags.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
            sheet_xml = (
                '<?xml version="1.0"?><worksheet '
                'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(row_tags)}</sheetData></worksheet>'
            )
            z.writestr(f'xl/worksheets/sheet{i}.xml', sheet_xml)
            sheets_xml_entries.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels_entries.append(
                f'<Relationship Id="rId{i}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{i}.xml"/>'
            )

        shared_xml = (
            '<?xml version="1.0"?><sst '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            f'count="0" uniqueCount="0">{"".join(f"<si><t>{s}</t></si>" for s in strings)}</sst>'
        )
        workbook_xml = (
            '<?xml version="1.0"?><workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(sheets_xml_entries)}</sheets></workbook>'
        )
        rels_xml = (
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rels_entries)}</Relationships>'
        )
        z.writestr('xl/sharedStrings.xml', shared_xml)
        z.writestr('xl/workbook.xml', workbook_xml)
        z.writestr('xl/_rels/workbook.xml.rels', rels_xml)


def build_vault_csv(path, rows):
    fieldnames = ['Name', 'Hash', 'Id', 'Rarity', 'Type', 'Element', 'Archetype', 'Tag', 'Notes']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            full = {k: '' for k in fieldnames}
            full.update(row)
            w.writerow(full)


HEADER = ['Name', 'Frame', 'Energy', 'Notes', 'Tier', 'Rank']


class PickEntryTests(unittest.TestCase):
    """Fixture: multiple source rows with the same display name where
    frame/element don't resolve it -- should come back flagged ambiguous,
    not silently resolved to the best-ranked row."""

    def test_single_entry_is_never_ambiguous(self):
        entries = [{'name': 'Solo', 'frame': 'X', 'energy': 'Solar', 'rank': '1', 'tier': 'S'}]
        entry, ambiguous = core.pick_entry(entries, {})
        self.assertFalse(ambiguous)
        self.assertEqual(entry['rank'], '1')

    def test_disambiguated_by_frame_is_not_ambiguous(self):
        entries = [
            {'name': 'Foo', 'frame': 'Adaptive', 'energy': None, 'rank': '5', 'tier': 'B'},
            {'name': 'Foo', 'frame': 'Aggressive', 'energy': None, 'rank': '10', 'tier': 'C'},
        ]
        entry, ambiguous = core.pick_entry(entries, {'Archetype': 'Adaptive Frame', 'Element': 'Solar'})
        self.assertFalse(ambiguous)
        self.assertEqual(entry['rank'], '5')

    def test_unresolvable_multi_row_match_is_ambiguous(self):
        entries = [
            {'name': 'Foo', 'frame': 'Adaptive', 'energy': None, 'rank': '5', 'tier': 'B'},
            {'name': 'Foo', 'frame': 'Aggressive', 'energy': None, 'rank': '10', 'tier': 'C'},
        ]
        entry, ambiguous = core.pick_entry(entries, {'Archetype': 'Precision Frame', 'Element': 'Void'})
        self.assertTrue(ambiguous)


class RecommendAndTagTests(unittest.TestCase):
    """Fixture: an ungraded spreadsheet category, and a new weapon missing
    from the sheet entirely -- both must fail closed to no tag, not junk."""

    def test_exotic_always_locks(self):
        self.assertEqual(core.recommend(None, 'Exotic Weapons'), 'LOCK (exotic)')

    def test_graded_above_threshold_locks(self):
        self.assertEqual(core.recommend('S', 'HCs', 'A'), 'LOCK')
        self.assertEqual(core.recommend('A', 'HCs', 'A'), 'LOCK')

    def test_graded_below_threshold_unlocks_confidently(self):
        self.assertEqual(core.recommend('B', 'HCs', 'A'), 'UNLOCK')
        self.assertEqual(core.dim_tag('UNLOCK'), 'junk')

    def test_ungraded_category_reviews_not_junks(self):
        rec = core.recommend(None, 'LMGs')
        self.assertEqual(rec, 'REVIEW (ungraded)')
        self.assertEqual(core.dim_tag(rec), '', 'ungraded weapons must get NO tag, not junk')

    def test_unresolved_weapon_is_unknown_not_junk(self):
        rec = core.recommend(None, None)
        self.assertEqual(rec, 'UNKNOWN (unresolved)')
        self.assertEqual(core.dim_tag(rec), '', 'unresolved weapons must get NO tag, not junk')
        note = core.build_note(rec, None, None, None, None)
        self.assertNotIn('safe to dismantle', note.lower(),
                          'absence from the sheet must not be framed as evidence the item is bad')

    def test_s_only_threshold_demotes_a_tier(self):
        self.assertEqual(core.recommend('A', 'HCs', 'S'), 'UNLOCK')
        self.assertEqual(core.recommend('S', 'HCs', 'S'), 'LOCK')


class NicheRankingTests(unittest.TestCase):
    """Fixture: two copies of the same weapon competing for a niche
    promotion -- the tie must not be broken by CSV row order."""

    @staticmethod
    def _member(name, rec, rank, tier):
        return {
            'Name': name, 'Recommendation': rec, 'Rank': rank, 'Tier': tier,
            'Category': 'HCs', 'Frame': 'Adaptive', 'Element': 'Solar', 'Type': 'Hand Cannon',
            'Notes': '', 'Tag': core.dim_tag(rec), 'Hash': name, 'Id': name,
        }

    def test_duplicate_copies_that_are_the_niche_best_block_promotion(self):
        results = [
            self._member('Duped', 'UNLOCK', '3', 'B'),
            self._member('Duped', 'UNLOCK', '3', 'B'),
            self._member('Other', 'UNLOCK', '10', 'D'),
        ]
        entries = [
            {'name': 'Duped', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '3'},
            {'name': 'Other', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '10'},
        ]
        core.apply_niche_ranking(results, entries)
        self.assertEqual(results[0]['Recommendation'], 'REVIEW (duplicate copies)')
        self.assertEqual(results[1]['Recommendation'], 'REVIEW (duplicate copies)')
        self.assertEqual(results[2]['Recommendation'], 'UNLOCK',
                          'a strictly worse weapon must not be promoted just because the true '
                          'best is an unresolvable duplicate group')

    def test_duplicate_copies_that_are_not_the_niche_best_dont_block_others(self):
        results = [
            self._member('Duped', 'UNLOCK', '8', 'C'),
            self._member('Duped', 'UNLOCK', '8', 'C'),
            self._member('Better', 'UNLOCK', '2', 'B'),
        ]
        entries = [
            {'name': 'Duped', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '8'},
            {'name': 'Better', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '2'},
        ]
        core.apply_niche_ranking(results, entries)
        self.assertEqual(results[2]['Recommendation'], 'KEEP (best available)')
        self.assertNotIn('Redundant', results[0]['Notes'],
                          'a duplicate-copy group should only carry its own explanation, '
                          'not also a generic redundant-with-itself note')

    def test_already_locked_duplicates_are_left_alone(self):
        results = [
            self._member('Locked', 'LOCK', '1', 'S'),
            self._member('Locked', 'LOCK', '1', 'S'),
        ]
        entries = [{'name': 'Locked', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '1'}]
        core.apply_niche_ranking(results, entries)
        self.assertEqual(results[0]['Recommendation'], 'LOCK')
        self.assertEqual(results[0]['Notes'], '')

    def test_niche_already_covered_by_lock_still_annotates_the_rest(self):
        """Regression test for a bug caught while writing this fix: when the
        niche's true best is independently LOCK (not ambiguous, just already
        confidently kept), everything else in the niche must still get its
        informational note -- not be silently skipped."""
        results = [
            self._member('Locked', 'LOCK', '1', 'S'),
            self._member('Duped', 'UNLOCK', '5', 'B'),
            self._member('Duped', 'UNLOCK', '5', 'B'),
            self._member('Solo', 'UNLOCK', '8', 'C'),
        ]
        entries = [
            {'name': 'Locked', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '1'},
            {'name': 'Duped', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '5'},
            {'name': 'Solo', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '8'},
        ]
        core.apply_niche_ranking(results, entries)
        self.assertEqual(results[3]['Recommendation'], 'UNLOCK', 'Solo must not be falsely promoted')
        self.assertIn('Same broad niche as Locked', results[3]['Notes'],
                       'Solo must still get pointed at what already covers the niche')

    def test_softened_wording_not_settled_redundancy(self):
        results = [
            self._member('A', 'UNLOCK', '3', 'B'),
            self._member('B', 'UNLOCK', '9', 'D'),
        ]
        entries = [
            {'name': 'A', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '3'},
            {'name': 'B', 'category': 'HCs', 'frame': 'Adaptive', 'energy': 'Solar', 'rank': '9'},
        ]
        core.apply_niche_ranking(results, entries)
        self.assertIn('Same broad niche', results[1]['Notes'])
        self.assertNotIn('Redundant', results[1]['Notes'])


class TriageVaultIntegrationTests(unittest.TestCase):
    """End-to-end fixtures matching the exact scenarios from the issue:
    normal + Adept ambiguity, an item with an existing DIM tag/note, and
    the full non-destructive dim-import behavior."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.xlsx_path = os.path.join(self.tmpdir.name, 'analysis.xlsx')
        self.csv_path = os.path.join(self.tmpdir.name, 'vault.csv')

        build_xlsx(self.xlsx_path, {
            'HCs': [
                ['HCs'],
                HEADER,
                ['Great Gun', 'Adaptive', 'Solar', '', 'S', '1'],
                ['Meh Gun', 'Adaptive', 'Solar', '', 'D', '20'],
            ],
        })

    def test_existing_dim_tag_and_note_are_preserved(self):
        """Fixture: an item with an existing DIM tag and personal note --
        must never appear in the dim-import output, regardless of what this
        script's own recommendation would have been."""
        build_vault_csv(self.csv_path, [
            {'Name': 'Great Gun', 'Hash': 'h1', 'Id': '1', 'Rarity': 'Legendary',
             'Tag': 'favorite', 'Notes': 'my beloved'},
            {'Name': 'Meh Gun', 'Hash': 'h2', 'Id': '2', 'Rarity': 'Legendary'},
        ])
        results = core.triage_vault(self.xlsx_path, self.csv_path, min_tier='A')
        great = next(r for r in results if r['Name'] == 'Great Gun')
        self.assertEqual(great['Tag'], 'keep', "the tool's own opinion is still recorded in the report")
        self.assertTrue(great['DimImportSkipReason'], 'must be flagged for skip')

        dim_path = os.path.join(self.tmpdir.name, 'dim-import.csv')
        core.write_dim_import_csv(results, dim_path)
        with open(dim_path, newline='', encoding='utf-8') as f:
            imported_ids = {row['Id'] for row in csv.DictReader(f)}
        self.assertNotIn('1', imported_ids,
                          "an item with an existing tag/note must never be in the import file, "
                          "since DIM overwrites every row present in it unconditionally")
        self.assertIn('2', imported_ids, 'items with no existing tag/note should still be included')

    def test_adept_suffix_resolves_to_base_name(self):
        build_vault_csv(self.csv_path, [
            {'Name': 'Great Gun (Adept)', 'Hash': 'h1', 'Id': '1', 'Rarity': 'Legendary'},
        ])
        results = core.triage_vault(self.xlsx_path, self.csv_path, min_tier='A')
        self.assertEqual(results[0]['Recommendation'], 'LOCK',
                          '(Adept) suffix must still resolve against the base weapon name')

    def test_brand_new_weapon_is_unknown_not_junk(self):
        build_vault_csv(self.csv_path, [
            {'Name': 'Never Heard Of It', 'Hash': 'h9', 'Id': '9', 'Rarity': 'Legendary'},
        ])
        results = core.triage_vault(self.xlsx_path, self.csv_path, min_tier='A')
        self.assertEqual(results[0]['Recommendation'], 'UNKNOWN (unresolved)')
        self.assertEqual(results[0]['Tag'], '')

    def test_empty_vault_csv_raises_instead_of_crashing_on_index_zero(self):
        build_vault_csv(self.csv_path, [])
        with self.assertRaises(ValueError):
            core.triage_vault(self.xlsx_path, self.csv_path, min_tier='A')

    def test_every_row_is_stamped_with_source_hash_and_capture_time(self):
        build_vault_csv(self.csv_path, [
            {'Name': 'Great Gun', 'Hash': 'h1', 'Id': '1', 'Rarity': 'Legendary'},
        ])
        results = core.triage_vault(self.xlsx_path, self.csv_path, min_tier='A')
        self.assertTrue(results[0]['SourceHash'], 'every row must record which sheet version it came from')
        self.assertTrue(results[0]['CaptureTime'], 'every row must record when the run happened')


if __name__ == '__main__':
    unittest.main()
