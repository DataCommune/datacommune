// Client of the interactive parcel map (04.10-03). The readers run for real; only the fetch they are
// handed is a double. The envelopes are the contract examples of `contracts/carte/exemples/`, the very
// files the API model reloads identically (04.10-02): what the service freezes is what the client reads.
import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
	CHEMIN_CONTEXTE,
	CHEMIN_FOND,
	CHEMIN_TUILE_PARCELLES,
	CHEMIN_VENTES_PARCELLE,
	GABARIT_FOND,
	CHEMINS_CARTE,
	ZOOM_MAX,
	ZOOM_MIN,
	ZOOM_OUVERTURE,
	lireContexte,
	lireTuileParcelles,
	lireVentesParcelle
} from './carte-parcelles';

const RACINE = resolve(process.cwd(), '..');
const lire = (chemin: string) => JSON.parse(readFileSync(join(RACINE, chemin), 'utf8'));
const CONTEXTE = lire('contracts/carte/exemples/contexte.json');
const TUILE = lire('contracts/carte/exemples/tuile-parcelles.json');
const VENTES = lire('contracts/carte/exemples/ventes-parcelle.json');
const CONTRAT = lire('contracts/openapi.v1.json') as { paths: Record<string, unknown> };

const BAN_ID = '00000_0001_00001';

function reponse(status: number, body: unknown, headers: Record<string, string> = {}) {
	return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json', ...headers }
	});
}

function fetchRepondant(status: number, body: unknown, headers: Record<string, string> = {}) {
	return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => reponse(status, body, headers));
}

/** A deep copy of an example, changed in place by *retouche*. */
function retouchee<T>(exemple: T, retouche: (copie: any) => void): T {
	const copie = structuredClone(exemple);
	retouche(copie);
	return copie;
}

const contexte = (fetchImpl: typeof fetch) => lireContexte(BAN_ID, { fetchImpl });
const tuile = (fetchImpl: typeof fetch) => lireTuileParcelles(18, 128512, 91234, { fetchImpl });

describe('bornes et chemins', () => {
	it('zoom 16 to 19, opening at 18 (UI-SPEC § 2.3)', () => {
		expect([ZOOM_MIN, ZOOM_MAX, ZOOM_OUVERTURE]).toEqual([16, 19, 18]);
	});

	it('every constant path of the client is a `/v1/carte/…` path of the contract', () => {
		expect(CHEMINS_CARTE).toEqual([CHEMIN_CONTEXTE, CHEMIN_TUILE_PARCELLES, CHEMIN_FOND, CHEMIN_VENTES_PARCELLE]);
		for (const chemin of CHEMINS_CARTE) {
			expect(chemin.startsWith('/carte/')).toBe(true);
			expect(Object.keys(CONTRAT.paths), chemin).toContain(`/v1${chemin}`);
		}
		// Non-vacuity: the same check refuses a path the contract does not have.
		expect(Object.keys(CONTRAT.paths)).not.toContain('/v1/carte/parcelles/{z}/{x}');
	});

	it('the ground tiles are asked on the origin of the site, by the template Leaflet fills', () => {
		expect(CHEMIN_FOND).toBe('/carte/fond/{z}/{x}/{y}.png');
		// Leaflet fills `{z}`, `{x}` and `{y}` of a template: the template is the path of the contract.
		expect(GABARIT_FOND).toBe(CHEMIN_FOND);
		expect(GABARIT_FOND).not.toMatch(/^https?:|^\/\//);
	});

	it('the readers ask the constant paths, on the origin of the site, without key nor cookie', async () => {
		const pourContexte = fetchRepondant(200, CONTEXTE);
		await lireContexte('00000_0001_00001_bis', { fetchImpl: pourContexte });
		expect(pourContexte).toHaveBeenCalledTimes(1);
		expect(pourContexte.mock.calls[0][0]).toBe(CHEMIN_CONTEXTE.replace('{ban_id}', '00000_0001_00001_bis'));

		const pourTuile = fetchRepondant(200, TUILE);
		await lireTuileParcelles(17, 3, 5, { fetchImpl: pourTuile });
		expect(pourTuile.mock.calls[0][0]).toBe('/carte/parcelles/17/3/5');
		expect(CHEMIN_TUILE_PARCELLES.replace('{z}', '17').replace('{x}', '3').replace('{y}', '5')).toBe('/carte/parcelles/17/3/5');

		for (const appel of [pourContexte.mock.calls[0], pourTuile.mock.calls[0]]) {
			const init = appel[1] as RequestInit;
			expect(init.credentials).toBe('omit');
			expect(Object.keys(init.headers as Record<string, string>).map((nom) => nom.toLowerCase())).toEqual(['accept']);
		}
	});

	it('a BAN key is escaped: it never adds a segment to the path', async () => {
		const fetchImpl = fetchRepondant(200, CONTEXTE);
		await lireContexte('a/b?c', { fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/carte/a%2Fb%3Fc/contexte');
	});
});

describe('enveloppes du contrat', () => {
	it('the context example goes through the projection without losing a field', async () => {
		const issue = await contexte(fetchRepondant(200, CONTEXTE));
		expect(issue).toEqual({ kind: 'ok', donnees: CONTEXTE.donnees });
	});

	it('the tile example goes through the projection without losing a field', async () => {
		const issue = await tuile(fetchRepondant(200, TUILE));
		expect(issue).toEqual({ kind: 'ok', donnees: TUILE.donnees });
		// Non-vacuity: a missing surface stays null, a count of zero stays zero.
		const proprietes = TUILE.donnees.collection.features.map((f: { properties: unknown }) => f.properties);
		expect(proprietes.some((p: { contenance: unknown }) => p.contenance === null)).toBe(true);
		expect(proprietes.some((p: { ventes: unknown }) => p.ventes === 0)).toBe(true);
	});

	it('`ventes` null on a parcel is kept null: not counted, never zero', async () => {
		const corps = retouchee(TUILE, (c) => {
			c.donnees.ventes = { etat: 'temporary_error', motif: 'lecture_indisponible', periode: null, annee_debut: null };
			for (const f of c.donnees.collection.features) f.properties.ventes = null;
		});
		const issue = await tuile(fetchRepondant(200, corps));
		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.donnees.collection.features.map((f) => f.properties.ventes)).toEqual([null, null, null, null]);
		expect(issue.donnees.collection.features).toHaveLength(4);
	});

	it('a tile without any parcel is a served zero, not an outage', async () => {
		const corps = retouchee(TUILE, (c) => (c.donnees.collection.features = []));
		const issue = await tuile(fetchRepondant(200, corps));
		expect(issue).toEqual({ kind: 'ok', donnees: corps.donnees });
	});
});

describe('réponse illisible = panne, jamais une liste plus courte (T-04.10-38)', () => {
	it.each([
		['parcelles.etat', (c: any) => (c.donnees.parcelles.etat = 'partiel')],
		['ventes.etat', (c: any) => (c.donnees.ventes.etat = 'inconnu')],
		['parcelles absent', (c: any) => delete c.donnees.parcelles],
		['collection absente', (c: any) => delete c.donnees.collection],
		['collection.type', (c: any) => (c.donnees.collection.type = 'Feature')],
		['features', (c: any) => (c.donnees.collection.features = null)],
		['a feature without geometry', (c: any) => (c.donnees.collection.features[1].geometry = null)],
		['a feature with a geometry of another type', (c: any) => (c.donnees.collection.features[1].geometry.type = 'Point')],
		['a feature with a broken ring', (c: any) => (c.donnees.collection.features[1].geometry.coordinates[0][0][2] = ['x', 46.5])],
		['a feature without label point', (c: any) => delete c.donnees.collection.features[2].properties.etiquette],
		['a label point that is not a pair of numbers', (c: any) => (c.donnees.collection.features[2].properties.etiquette = [-3.5])],
		['a feature without identifier', (c: any) => delete c.donnees.collection.features[0].properties.id_parcelle],
		['an identifier that is not a parcel reference', (c: any) => (c.donnees.collection.features[0].properties.id_parcelle = 'ZA0001')],
		['an identifier that differs from the feature id', (c: any) => (c.donnees.collection.features[0].id = '00000000ZA0009')],
		['a surface that is not a number', (c: any) => (c.donnees.collection.features[0].properties.contenance = '450')],
		['a count that is not a whole number', (c: any) => (c.donnees.collection.features[0].properties.ventes = 1.5)],
		['donnees absent', (c: any) => delete c.donnees]
	])('tile: %s', async (_nom, retouche) => {
		const issue = await tuile(fetchRepondant(200, retouchee(TUILE, retouche)));
		expect(issue).toEqual({ kind: 'panne', statut: 200 });
	});

	it.each([
		['parcelle_adresse.etat', (c: any) => (c.donnees.parcelle_adresse.etat = 'unique')],
		['parcelles.etat', (c: any) => (c.donnees.parcelles.etat = 'temporary_error')],
		['ventes.etat', (c: any) => (c.donnees.ventes.etat = 'not_available')],
		['centre', (c: any) => (c.donnees.centre.lat = null)],
		['emprise', (c: any) => delete c.donnees.emprise.nord],
		['an upside-down extent', (c: any) => (c.donnees.emprise.nord = c.donnees.emprise.sud)],
		['a parcel of the address said `value` without identifier', (c: any) => (c.donnees.parcelle_adresse.id_parcelle = null)],
		['fond', (c: any) => delete c.donnees.fond.attribution]
	])('context: %s', async (_nom, retouche) => {
		const issue = await contexte(fetchRepondant(200, retouchee(CONTEXTE, retouche)));
		expect(issue).toEqual({ kind: 'panne', statut: 200 });
	});

	it('a parcel of the address that is not established keeps its state and its reason', async () => {
		const corps = retouchee(CONTEXTE, (c) => {
			c.donnees.parcelle_adresse = { etat: 'not_available', id_parcelle: null, motif: 'parcelles_multiples', nombre_parcelles: 2 };
		});
		const issue = await contexte(fetchRepondant(200, corps));
		expect(issue).toEqual({ kind: 'ok', donnees: corps.donnees });
	});

	it('a body that is not JSON is an outage', async () => {
		expect(await tuile(fetchRepondant(200, '<html>'))).toEqual({ kind: 'panne', statut: 200 });
		expect(await contexte(fetchRepondant(200, '<html>'))).toEqual({ kind: 'panne', statut: 200 });
	});
});

describe('refus et pannes', () => {
	it('a 429 is a rate limit with the served delay of Retry-After, on the context and on a tile', async () => {
		expect(await contexte(fetchRepondant(429, { title: 'Trop de demandes' }, { 'Retry-After': '42' }))).toEqual({ kind: 'limite', retryAfter: 42 });
		expect(await tuile(fetchRepondant(429, { title: 'Trop de demandes' }, { 'Retry-After': '7' }))).toEqual({ kind: 'limite', retryAfter: 7 });
	});

	it('a 429 without a delay in seconds keeps no delay', async () => {
		expect(await tuile(fetchRepondant(429, {}))).toEqual({ kind: 'limite', retryAfter: null });
		expect(await tuile(fetchRepondant(429, {}, { 'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT' }))).toEqual({ kind: 'limite', retryAfter: null });
	});

	it('a 503 is an outage, never an empty tile', async () => {
		expect(await tuile(fetchRepondant(503, { title: 'Parcelles momentanement indisponibles' }))).toEqual({ kind: 'panne', statut: 503 });
		expect(await contexte(fetchRepondant(503, { code: 'adresse_indisponible' }))).toEqual({ kind: 'panne', statut: 503 });
	});

	it('an unknown address (404) is no map for this address, said as an outage of the map', async () => {
		expect(await contexte(fetchRepondant(404, { code: 'adresse_inconnue' }))).toEqual({ kind: 'panne', statut: 404 });
	});

	it('a network failure is an outage without status', async () => {
		const enEchec = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		});
		expect(await tuile(enEchec as unknown as typeof fetch)).toEqual({ kind: 'panne', statut: null });
		expect(await contexte(enEchec as unknown as typeof fetch)).toEqual({ kind: 'panne', statut: null });
	});

	it('the abort signal of the caller is handed to fetch', async () => {
		const fetchImpl = fetchRepondant(200, TUILE);
		const controleur = new AbortController();
		await lireTuileParcelles(16, 1, 2, { fetchImpl, signal: controleur.signal });
		expect((fetchImpl.mock.calls[0][1] as RequestInit).signal).toBe(controleur.signal);
	});
});

// Rule (c) of the phase, Q7 (T-04.10-04): the map data is read by the browser after the click. Nothing
// of it goes through a loader or an endpoint of SvelteKit, where it would land in the served HTML and
// behind the quota of the fiche.
describe('frontière avec les chargeurs serveur', () => {
	const ROUTES = resolve(process.cwd(), 'src/routes');
	const fichiersServeur = (dossier: string): string[] =>
		readdirSync(dossier, { withFileTypes: true }).flatMap((entree) => {
			const chemin = join(dossier, entree.name);
			if (entree.isDirectory()) return fichiersServeur(chemin);
			return /(^\+server|\.server)\.(ts|js)$/.test(entree.name) ? [chemin] : [];
		});

	it('no server file of src/routes names the map client nor a path of the map', () => {
		const fichiers = fichiersServeur(ROUTES);
		// Non-vacuity: the loaders of the address page are among the files read.
		expect(fichiers.some((f) => f.replaceAll('\\', '/').endsWith('adresse/[id]/+page.server.ts'))).toBe(true);
		const fautifs = fichiers.filter((fichier) => /carte-parcelles|\/carte\//.test(readFileSync(fichier, 'utf8')));
		expect(fautifs.map((f) => f.replaceAll('\\', '/').slice(ROUTES.length + 1))).toEqual([]);
	});
});

// Sales of one parcel of the map (04.10-07, route of 04.10-05): the cards of the fiche, with the same
// price rule (`projeterCarteVente`), and one group per card.
describe('ventes d’une parcelle', () => {
	const ID = '00000000ZA0001';
	const ventes = (fetchImpl: typeof fetch, page = 1, vg: string | null = null) => lireVentesParcelle(BAN_ID, ID, { page, vg }, { fetchImpl });

	it('the path of the sales is the one of the contract, filled with the BAN key of the fiche and the parcel', async () => {
		expect(CHEMIN_VENTES_PARCELLE).toBe('/carte/{ban_id}/parcelle/{id_parcelle}/ventes');
		const fetchImpl = fetchRepondant(200, VENTES);
		await lireVentesParcelle('a/b', ID, { page: 1, vg: null }, { fetchImpl });
		expect(fetchImpl.mock.calls[0][0]).toBe('/carte/a%2Fb/parcelle/00000000ZA0001/ventes?page=1');
		const init = fetchImpl.mock.calls[0][1] as RequestInit;
		expect(init.credentials).toBe('omit');
		expect(Object.keys(init.headers as Record<string, string>).map((nom) => nom.toLowerCase())).toEqual(['accept']);
	});

	it('the generation token is carried from one page to the next', async () => {
		const fetchImpl = fetchRepondant(200, VENTES);
		await ventes(fetchImpl, 2, '0123456789abcdef');
		expect(fetchImpl.mock.calls[0][0]).toBe(`/carte/${BAN_ID}/parcelle/${ID}/ventes?page=2&vg=0123456789abcdef`);
	});

	it('the example of the contract, served whole, goes through the projection without losing a field', async () => {
		const issue = await ventes(fetchRepondant(200, VENTES));
		expect(issue).toEqual({ kind: 'ok', donnees: VENTES.donnees });
		// Non-vacuity: a withheld price, a sale alone, a group beyond the cap and an incomplete surface.
		expect(VENTES.donnees.ventes.some((v: { prix_affiche: boolean }) => !v.prix_affiche)).toBe(true);
		expect(VENTES.donnees.groupes).toContain(null);
		expect(VENTES.donnees.groupes.some((g: { parcelles: unknown } | null) => g !== null && g.parcelles === null)).toBe(true);
		expect(VENTES.donnees.groupes.some((g: { surface_totale_m2: unknown } | null) => g !== null && g.surface_totale_m2 === null)).toBe(true);
	});

	it('reuses the projection of the cards of the fiche: an amount served next to a minimisation is never kept', async () => {
		const corps = retouchee(VENTES, (c) => {
			c.donnees.ventes[1].prix = 199000;
			c.donnees.ventes[1].prix_m2 = 1809;
			c.donnees.ventes[1].prix_affiche = true;
		});
		const issue = await ventes(fetchRepondant(200, corps));
		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.donnees.ventes[1]).toMatchObject({ prix: null, prix_m2: null, prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' } });
		expect(JSON.stringify(issue.donnees)).not.toContain('199000');
	});

	it('a served zero keeps its period and the first year of the import', async () => {
		const corps = retouchee(VENTES, (c) => {
			Object.assign(c.donnees, { total: 0, pages: 1, ventes: [], groupes: [] });
		});
		const issue = await ventes(fetchRepondant(200, corps));
		expect(issue).toEqual({ kind: 'ok', donnees: corps.donnees });
	});

	it('out of coverage keeps its served reason, without any sale', async () => {
		const corps = { statut: 'ok', donnees: { etat: 'out_of_coverage', motif: 'dataset_non_heberge', page: 1, par_page: 5 } };
		const issue = await ventes(fetchRepondant(200, corps));
		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.donnees).toMatchObject({ etat: 'out_of_coverage', motif: 'dataset_non_heberge', ventes: [], groupes: [], total: null });
	});

	it('a page out of bounds (404 `page_hors_limites`) and a changed generation (409) are an invalid page', async () => {
		expect(await ventes(fetchRepondant(404, { title: 'Page hors limites', status: 404, code: 'page_hors_limites' }), 9)).toEqual({ kind: 'page_invalide' });
		expect(await ventes(fetchRepondant(409, { title: 'Generation changee', status: 409, code: 'generation_changee' }), 2, '0123456789abcdef')).toEqual({
			kind: 'page_invalide'
		});
	});

	it('a parcel refused by the service (404 without code) is an outage of the sales, never a zero', async () => {
		expect(await ventes(fetchRepondant(404, { title: 'Parcelle introuvable', status: 404 }))).toEqual({ kind: 'panne', statut: 404 });
		expect(await ventes(fetchRepondant(409, { title: 'Conflit', status: 409, code: 'autre' }))).toEqual({ kind: 'panne', statut: 409 });
	});

	it('a 429 is the limit with its served delay; a 503 or a network failure is an outage', async () => {
		expect(await ventes(fetchRepondant(429, { title: 'Trop de demandes' }, { 'Retry-After': '30' }))).toEqual({ kind: 'limite', retryAfter: 30 });
		expect(await ventes(fetchRepondant(503, { title: 'Ventes indisponibles' }))).toEqual({ kind: 'panne', statut: 503 });
		const enEchec = vi.fn(async () => {
			throw new TypeError('Failed to fetch');
		});
		expect(await ventes(enEchec as unknown as typeof fetch)).toEqual({ kind: 'panne', statut: null });
	});

	it.each([
		['groupes shorter than ventes', (c: any) => c.donnees.groupes.pop()],
		['groupes longer than ventes', (c: any) => c.donnees.groupes.push(null)],
		['groupes absent next to sales', (c: any) => delete c.donnees.groupes],
		['an unknown state', (c: any) => (c.donnees.etat = 'temporary_error')],
		['a sale that is not an object', (c: any) => (c.donnees.ventes[0] = 'vente')],
		['a group count that is not a whole number', (c: any) => (c.donnees.groupes[1].autres = '1')],
		['a group surface that is not a whole number', (c: any) => (c.donnees.groupes[1].surface_totale_m2 = -3)],
		['a served count without a surface that is not a whole number', (c: any) => (c.donnees.groupes[1].sans_surface = null)],
		['a parcel of a group without geometry', (c: any) => (c.donnees.groupes[1].parcelles[0].geometry = null)],
		['a parcel of a group with a broken reference', (c: any) => (c.donnees.groupes[1].parcelles[0].id_parcelle = 'ZA0001')],
		['a page that is not a page', (c: any) => (c.donnees.page = 0)],
		['donnees absent', (c: any) => delete c.donnees]
	])('unreadable answer, an outage: %s', async (_nom, retouche) => {
		expect(await ventes(fetchRepondant(200, retouchee(VENTES, retouche)))).toEqual({ kind: 'panne', statut: 200 });
	});

	it('the count without a surface (R3, 2026-11-15) is projected; absent from an older server it is unknown, never zero', async () => {
		const lu = await ventes(fetchRepondant(200, retouchee(VENTES, (c: any) => (c.donnees.groupes[1].sans_surface = 2))));
		expect(lu.kind === 'ok' && lu.donnees.groupes[1]?.sans_surface).toBe(2);
		const ancien = await ventes(fetchRepondant(200, retouchee(VENTES, (c: any) => c.donnees.groupes.forEach((g: any) => g && delete g.sans_surface))));
		expect(ancien.kind === 'ok' && ancien.donnees.groupes.map((g) => (g ? g.sans_surface : 'aucun groupe'))).toEqual(['aucun groupe', null, null, null]);
	});

	it('the abort signal of the caller is handed to fetch', async () => {
		const fetchImpl = fetchRepondant(200, VENTES);
		const controleur = new AbortController();
		await lireVentesParcelle(BAN_ID, ID, { page: 1, vg: null }, { fetchImpl, signal: controleur.signal });
		expect((fetchImpl.mock.calls[0][1] as RequestInit).signal).toBe(controleur.signal);
	});
});
