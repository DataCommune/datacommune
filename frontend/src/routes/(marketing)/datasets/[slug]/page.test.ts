// Former detail pages (H4, D-40): each historical slug answers a permanent redirect to its source in
// « Données & méthode » (detail opened, row targeted since 04.5-11), an unknown slug a 404, and the request slug is never reflected
// (T-03-11-01). The targets must be catalogue keys that `GET /v1/datasets` serves.
import { isHttpError, isRedirect } from '@sveltejs/kit';
import { describe, expect, it } from 'vitest';
import { load } from './+page';
import { DATASET_SLUG_KEYS } from '$lib/data/dataset-slugs';
import datasetsApi from '$lib/data/__fixtures__/datasets-api.json';

// The 27 slugs of the removed frontend dataset catalogue (03-11), in its order.
const SLUGS_HISTORIQUES = [
	'ban', 'cadastre', 'dvf', 'dpe', 'loyers', 'risques', 'pollutions', 'urbanisme', 'batiments',
	'monuments', 'connectivite', 'entreprises', 'scolaire', 'transports', 'bdnb', 'errial', 'rnb',
	'zonage-abc', 'fiscalite', 'encadrement-loyers', 'revenus', 'demographie', 'copropriete',
	'permis', 'antennes', 'equipements', 'vacance'
];
const CLES_SERVIES = new Set(datasetsApi.donnees.map((jeu) => jeu.nom));

function charger(slug: string): unknown {
	try {
		(load as unknown as (event: { params: { slug: string } }) => unknown)({ params: { slug } });
	} catch (thrown) {
		return thrown;
	}
	throw new Error(`load(${slug}) neither redirected nor failed`);
}

describe('/datasets/[slug] — permanent redirect to the card (H4)', () => {
	it('keeps every historical slug, and only those', () => {
		expect(Object.keys(DATASET_SLUG_KEYS)).toEqual(SLUGS_HISTORIQUES);
	});

	it.each(SLUGS_HISTORIQUES)('%s answers 308 towards the source of a served catalogue key', (slug) => {
		const thrown = charger(slug);
		expect(isRedirect(thrown)).toBe(true);
		const { status, location } = thrown as { status: number; location: string };
		expect(status).toBe(308);
		expect(location).toBe(`/datasets?jeu=${DATASET_SLUG_KEYS[slug]}#${DATASET_SLUG_KEYS[slug]}`);
		expect(CLES_SERVIES.has(DATASET_SLUG_KEYS[slug])).toBe(true);
	});

	it('answers 404 to an unknown slug without reflecting it', () => {
		for (const slug of ['not-a-real-dataset', '<script>alert(1)</script>', 'constructor', '__proto__']) {
			const thrown = charger(slug);
			expect(isHttpError(thrown)).toBe(true);
			expect((thrown as { status: number }).status).toBe(404);
			expect(JSON.stringify(thrown)).not.toContain(slug);
		}
	});
});
