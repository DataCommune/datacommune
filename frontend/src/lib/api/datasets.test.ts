import { describe, expect, it, vi } from 'vitest';
import { etatDuJeu, listDatasets, type DatasetPublic } from './datasets';
import fixture from '$lib/data/__fixtures__/datasets-api.json';

// The real `api()` client runs; only the fetch it is handed is a double, so the URL the server
// would request is observed exactly as sent.

function fetchReturning(body: unknown, status = 200) {
	return vi.fn(
		async (_input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify(body), {
				status,
				headers: { 'Content-Type': 'application/json' }
			})
	);
}

const JEUX = fixture.donnees as DatasetPublic[];
const jeu = (nom: string) => JEUX.find((item) => item.nom === nom)!;

describe('listDatasets()', () => {
	it('reads GET /v1/datasets through the fetch it is handed, without query string', async () => {
		const fetchImpl = fetchReturning(fixture);

		const jeux = await listDatasets(fetchImpl);

		expect(fetchImpl).toHaveBeenCalledTimes(1);
		const url = new URL(String(fetchImpl.mock.calls[0][0]), 'http://origin.invalid');
		expect(url.pathname).toBe('/v1/datasets');
		expect(url.search).toBe('');
		expect(jeux).toEqual(fixture.donnees);
	});

	it('rejects on an outage instead of returning an empty catalogue', async () => {
		const fetchImpl = fetchReturning({ detail: 'indisponible' }, 503);
		await expect(listDatasets(fetchImpl)).rejects.toMatchObject({ status: 503 });
	});
});

describe('etatDuJeu()', () => {
	it('reads a catalogue quarantine before the hosting (permis is also out of coverage)', () => {
		expect(etatDuJeu(jeu('permis'))).toEqual({
			kind: 'non_qualifie',
			motif: jeu('permis').quarantaine
		});
		expect(etatDuJeu(jeu('encadrement_loyers')).kind).toBe('non_qualifie');
	});

	it('reads the out-of-coverage reason served by the API', () => {
		expect(etatDuJeu(jeu('antennes'))).toEqual({
			kind: 'hors_couverture',
			motif: 'dataset_non_heberge'
		});
	});

	it('reads a hosted dataset with its served departements and status', () => {
		expect(etatDuJeu(jeu('dvf'))).toEqual({
			kind: 'heberge',
			statut: 'disponible',
			departements: jeu('dvf').hebergement!.departements
		});
		expect(etatDuJeu(jeu('georisques'))).toMatchObject({ statut: 'proxy_amont' });
		expect(etatDuJeu(jeu('batiments'))).toMatchObject({ statut: 'non_importe' });
	});

	it('refuses a status or a hosting it cannot word', () => {
		expect(etatDuJeu({ ...jeu('dvf'), statut: 'available' })).toEqual({
			kind: 'inconnu',
			statut: 'available'
		});
		expect(etatDuJeu({ ...jeu('dvf'), hebergement: null })).toEqual({
			kind: 'inconnu',
			statut: 'disponible'
		});
	});
});
