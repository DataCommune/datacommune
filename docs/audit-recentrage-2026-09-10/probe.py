"""Bounded, read-only source/preproduction audit. Never prints credentials."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://data.ademe.fr/data-fair/api/v1/datasets/'
FIELDS = 'numero_dpe,identifiant_ban,adresse_ban,adresse_brut,code_insee_ban,type_batiment,surface_habitable_logement,etiquette_dpe,date_etablissement_dpe,date_fin_validite_dpe,numero_dpe_remplace,id_rnb,score_ban,numero_etage_appartement'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {'User-Agent': 'DataCommune-audit/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
            return {'http': response.status, 'body': json.load(response)}
    except urllib.error.HTTPError as error:
        return {'http': error.code, 'error': 'http_error'}
    except Exception as error:
        return {'http': None, 'error': type(error).__name__}

def source(dataset, query, size=100):
    url = BASE + dataset + '/lines?' + urllib.parse.urlencode({'qs': query, 'size': size, 'select': FIELDS})
    result = fetch(url)
    result['url'] = url
    return result

def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()
                if key not in {'context_ref', 'document_ref', 'next_cursor', 'cursor', 'reference', 'contexte'}}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value

def headers():
    result = {'User-Agent': 'DataCommune-Preproduction-Check/1.0'}
    result['X-API-Key'] = os.environ['AUDIT_DC_API_KEY']
    for env, header in [('AUDIT_CF_ID', 'CF-Access-Client-Id'), ('AUDIT_CF_SECRET', 'CF-Access-Client-Secret')]:
        if os.environ.get(env):
            result[header] = os.environ[env]
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['discover', 'run', 'details'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cases', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.mode == 'discover':
        for commune in ['44143', '44109', '85191']:
            for kind in ['maison', 'appartement']:
                response = source('dpe03existant', f'code_insee_ban:"{commune}" AND type_batiment:"{kind}"', 3)
                save(args.out / f'discovery-{commune}-{kind}.json', response)
                print(json.dumps({'commune': commune, 'type': kind, 'http': response['http'], 'body': response.get('body')}, ensure_ascii=False), flush=True)
        return
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    assert len(cases) <= 16, 'This audit intentionally limits API traffic.'
    auth = headers()
    base = 'https://dev-api.datacommune.fr'
    save(args.out / 'version-before.json', fetch(base + '/v1/health/version', auth))
    if args.mode == 'details':
        for case in cases:
            if case['case_id'] not in {'R04', 'R06', 'R12', 'R14'}:
                continue
            resolved = fetch(base + '/v1/resolve?' + urllib.parse.urlencode({'q': case['address'], 'datasets': 'dpe'}), auth)
            inventory = resolved['body']['donnees'][0]['dpe_inventory']
            context = inventory['context_ref']
            document = fetch(base + '/v1/bien/dpe/document?' + urllib.parse.urlencode({'contexte': context, 'reference': inventory['items'][0]['document_ref']}), auth)
            pages = [inventory]
            while pages[-1].get('has_more') and len(pages) < 6:
                page = fetch(base + '/v1/bien/dpe/inventaire?' + urllib.parse.urlencode({'contexte': context, 'curseur': pages[-1]['next_cursor'], 'limite': 20}), auth)
                assert page['http'] == 200, 'Pagination did not succeed'
                pages.append(page['body'])
                time.sleep(3)
            official = source('dpe03existant', f'identifiant_ban:"{case["ban_id"]}"', 200)
            all_items = [item for page in pages for item in page['items']]
            evidence = {'case_id': case['case_id'], 'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'document': sanitize(document), 'pages': sanitize(pages), 'source_existant': official,
                        'items_read': len(all_items), 'unique_refs': len({item['document_ref'] for item in all_items}),
                        'last_has_more': pages[-1].get('has_more')}
            save(args.out / (case['case_id'] + '-details.json'), evidence)
            print(json.dumps({'case': case['case_id'], 'document': sanitize(document), 'items_read': len(all_items), 'pages_read': len(pages), 'last_has_more': pages[-1].get('has_more')}, ensure_ascii=False), flush=True)
            time.sleep(3)
        save(args.out / 'version-after.json', fetch(base + '/v1/health/version', auth))
        return
    summary = []
    for case in cases:
        case_id = case['case_id']
        captured_at = dt.datetime.now(dt.timezone.utc).isoformat()
        sources = {name: source(name, f'identifiant_ban:"{case["ban_id"]}"')
                   for name in ['dpe03existant', 'dpe02neuf']}
        result = fetch(base + '/v1/resolve?' + urllib.parse.urlencode({'q': case['address'], 'datasets': 'dpe'}), auth)
        evidence = {'captured_at': captured_at, 'case': case, 'sources': sources, 'datacommune': sanitize(result)}
        save(args.out / f'{case_id}.json', evidence)
        row = result.get('body', {}).get('donnees', [{}])[0]
        inventory = row.get('dpe_inventory') or {}
        summary.append({**case, 'captured_at': captured_at, 'http': result['http'],
                        'resolved_ban': row.get('ban_id'), 'resolved_address': row.get('adresse'),
                        'source_counts': {name: value.get('body', {}).get('total') for name, value in sources.items()},
                        'source_http': {name: value['http'] for name, value in sources.items()},
                        'inventory_total': inventory.get('total'), 'inventory_state': inventory.get('state'),
                        'read_state': inventory.get('read_state'), 'has_more': inventory.get('has_more'),
                        'attribution': row.get('dpe_attribution'), 'parcels': row.get('parcelles_candidates'),
                        'evidence_sha256': hashlib.sha256((args.out / f'{case_id}.json').read_bytes()).hexdigest()})
        save(args.out / 'summary.json', summary)
        print(json.dumps(summary[-1], ensure_ascii=False), flush=True)
        time.sleep(3)
    save(args.out / 'version-after.json', fetch(base + '/v1/health/version', auth))

if __name__ == '__main__':
    main()
