"""Verify the captured audit, not the product's business acceptance."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
load = lambda path: json.loads(path.read_text(encoding='utf-8'))
manifest = load(root / 'manifest.json')
for relative, expected_hash in manifest.items():
    path = (root / relative).resolve()
    assert path.is_relative_to(root.resolve())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash, relative
assert set(manifest) == {str(path.relative_to(root)).replace('\\', '/')
                         for path in root.rglob('*.json') if path.name != 'manifest.json'}
summary = load(root / 'live/summary.json')
assert len(summary) == 16
for row in summary:
    assert row['http'] == 200
    assert set(row['source_http'].values()) == {200}
    assert hashlib.sha256((root / 'live' / (row['case_id'] + '.json')).read_bytes()).hexdigest() == row['evidence_sha256']
equal_ban = [row for row in summary if row['ban_id'] == row['resolved_ban']]
assert len(equal_ban) == 14
assert all(sum(row['source_counts'].values()) == row['inventory_total'] for row in equal_ban)
assert sum(row['inventory_total'] > 0 for row in summary) == 13
assert all(row['attribution']['status'] == 'unassigned' for row in summary if row['inventory_total'] > 0)
fields = [('type_batiment', 'type_batiment'), ('surface_habitable_logement', 'surface_habitable'),
          ('etiquette_dpe', 'etiquette_dpe'), ('date_etablissement_dpe', 'date_etablissement_dpe'),
          ('date_fin_validite_dpe', 'date_fin_validite_dpe')]
projection_count = 0
for row in equal_ban:
    data = load(root / 'live' / (row['case_id'] + '.json'))
    official = [record for source in data['sources'].values() for record in source['body']['results']]
    actual = data['datacommune']['body']['donnees'][0]['dpe_inventory']['items']
    if row['case_id'] == 'R14':
        extra = load(root / 'details/R14-details.json')
        actual = [record for page in extra['pages'] for record in page['items']]
        official = extra['source_existant']['body']['results']
        assert extra['items_read'] == extra['unique_refs'] == 114
        assert not extra['last_has_more']
        assert len(extra['pages']) == 6
    expected = Counter(tuple(record.get(source) for source, _ in fields) for record in official)
    observed = Counter(tuple(record.get(api) for _, api in fields) for record in actual)
    assert expected == observed, row['case_id'] + ' field multiset differs'
    projection_count += len(actual)
assert projection_count == 157
source_reads = sum(len(row['source_http']) for row in summary)
assert source_reads == 32
for case_id in ['R04', 'R06', 'R12', 'R14']:
    details = load(root / 'details' / (case_id + '-details.json'))
    assert details['document']['http'] == 200
    doc = details['document']['body']
    assert 'numero_dpe' not in doc
    first = details['pages'][0]['items'][0]
    assert all(doc.get(api) == first.get(api) for _, api in fields), case_id
    assert details['last_has_more'] is False
    assert details['items_read'] == details['unique_refs'] == sum(len(page['items']) for page in details['pages'])
for stage in ['live', 'details']:
    before, after = [load(root / stage / (name + '.json')) for name in ['version-before', 'version-after']]
    assert before['http'] == after['http'] == 200
    assert before['body'] == after['body']
    assert before['body']['deployment_sha'] == '41f140a686de23ada10bcc5d5e0ce8d5e8510fc4'
    assert before['body']['environment'] == 'preproduction'
print(f'PASS: {len(manifest)} evidence hashes; {len(summary)} resolves; {source_reads} ADEME reads; same-BAN totals {len(equal_ban)}/14; {projection_count} field projections match across {len(fields)} fields; 4 document consultations checked; R14 114 unique references in 6 pages; runtime stable.')
print('PRODUCT GAP: 13/13 positive inventories remain unassigned. R09/R11 raw source addresses are outside their returned exact-BAN inventories.')
print('LIMIT: field projection parity is not independent proof of dwelling identity or diagnostic-number parity, since the API hides the number.')
