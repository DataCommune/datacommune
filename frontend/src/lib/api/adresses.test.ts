import { describe, it, expect, vi } from 'vitest';
import { ApiError } from './client';
import {
	getDocumentsDpe,
	getFicheAdresse,
	getVentesAdresse,
	getVentesParcelle,
	getVentesRessemblantes,
	projeterCarteVente,
	searchAdresses,
	type DocumentsDpeAdresse,
	type RechercheAdresses
} from './adresses';
import { TITRE_ACCES_REFUSE, TITRE_DEMANDE_REFUSEE, TITRE_LIMITE } from '$lib/data/panne';
import { BAN_ID_ADRESSE_BRUTE, NOMS_VARIANTES, sansGroupe, variante } from '../../../tests/e2e/fixtures/dpe-adresse-brute';
import {
	BAN_ID_44109,
	VENTES_DECLAREES_44109,
	VENTES_DECLAREES_44109_AVEC_VEFA,
	VENTES_PARCELLE_44109_AVEC_VEFA
} from '../../../tests/e2e/fixtures/adresse-ventes';

// The real `api()` client runs; only the fetch it is handed is a double, so the URL the browser
// or the server loader would request is observed exactly as sent (D-105: this instance's API only).

function reponse(status: number, body: unknown, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json', ...headers }
	});
}

function fetchRepondant(status: number, body: unknown, headers: Record<string, string> = {}) {
	return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => reponse(status, body, headers));
}

function demande(fetchImpl: ReturnType<typeof fetchRepondant>): URL {
	expect(fetchImpl).toHaveBeenCalledTimes(1);
	return new URL(String(fetchImpl.mock.calls[0][0]), 'http://origin.invalid');
}

const RECHERCHE: RechercheAdresses = {
	ordre_types: ['adresse', 'commune'],
	resultats: [
		{
			type_resultat: 'adresse',
			ban_id: '85191_0420_00012',
			numero: '12',
			rep: null,
			nom_voie: 'Rue des Lilas',
			code_postal: '85000',
			code_commune: '85191',
			nom_commune: 'La Roche-sur-Yon',
			departement: { code: '85', nom: 'Vendée' }
		}
	],
	hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'] } }
};

const IDENTITE = {
	etat: 'etablie',
	ban_id: '85191_0420_00012',
	libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

const META = { source: [{ nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' }] };

function fiche(identite: unknown, couverture: unknown) {
	return { statut: 'ok', donnees: { identite, couverture }, meta: META };
}

describe('searchAdresses()', () => {
	it('sends q as typed, the departement filter and the limit to /v1/adresses/recherche', async () => {
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: RECHERCHE });

		const res = await searchAdresses('12 rue des Lilas', { departement: '2A', limite: 8 }, fetchImpl);

		const url = demande(fetchImpl);
		expect(url.pathname).toBe('/v1/adresses/recherche');
		expect(url.searchParams.get('q')).toBe('12 rue des Lilas');
		expect(url.searchParams.get('departement')).toBe('2A');
		expect(url.searchParams.get('limite')).toBe('8');
		// The served answer, untouched: the order of the types is read, never recomputed (D-106).
		expect(res).toEqual(RECHERCHE);
	});

	it('omits an empty departement and forwards the abort signal', async () => {
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: RECHERCHE });
		const controller = new AbortController();

		await searchAdresses('nantes', { signal: controller.signal }, fetchImpl);

		const url = demande(fetchImpl);
		expect(url.searchParams.has('departement')).toBe(false);
		expect(url.searchParams.has('limite')).toBe(false);
		expect(fetchImpl.mock.calls[0][1]?.signal).toBe(controller.signal);
	});

	it('a typed 503 is thrown, never an empty list (SEM-01)', async () => {
		const fetchImpl = fetchRepondant(503, {
			title: 'Recherche d’adresses momentanement indisponible',
			status: 503,
			code: 'geocodage_indisponible'
		});

		await expect(searchAdresses('12 rue des Lilas', {}, fetchImpl)).rejects.toBeInstanceOf(ApiError);
	});
});

describe('getFicheAdresse() — one typed outcome per served answer', () => {
	it('a hosted address: `ok`, its identity, coverage and BAN source, projected', async () => {
		const fetchImpl = fetchRepondant(200, fiche(IDENTITE, { etat: 'heberge', motif: null }));

		const issue = await getFicheAdresse(fetchImpl, '85191_0420_00012');

		expect(demande(fetchImpl).pathname).toBe('/v1/adresse/85191_0420_00012');
		expect(issue).toEqual({
			kind: 'ok',
			fiche: {
				identite: {
					etat: 'etablie',
					ban_id: '85191_0420_00012',
					libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
					numero: '12',
					rep: null,
					nom_voie: 'Rue des Lilas',
					code_postal: '85000',
					commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
					departement: { code: '85', nom: 'Vendée' },
					parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1 }
				},
				couverture: { etat: 'heberge', motif: null },
				// 04-18: the landmarks are projected too; this identity-only answer serves none.
				reperes: null,
				// 04-06: no theme served, none projected.
				themes: []
			},
			source: { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr' }
		});
	});

	it('the parcel reason is kept for an undetermined parcel only (2026-10-05, sentence of an undetermined identity)', async () => {
		const indeterminee = { ...IDENTITE, etat: 'indeterminee', libelle: null, parcelle: { etat: 'indeterminee', id_parcelle: null, motif: 'positions_contradictoires' } };
		const issue = await getFicheAdresse(fetchRepondant(200, fiche(indeterminee, { etat: 'heberge', motif: null })), '85191_0420_00012');
		expect(issue.kind === 'ok' && issue.fiche.identite.parcelle).toEqual({ etat: 'indeterminee', id_parcelle: null, nombre_candidats: null, motif: 'positions_contradictoires' });

		const unique = { ...IDENTITE, parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, motif: 'ignore' } };
		const servie = await getFicheAdresse(fetchRepondant(200, fiche(unique, { etat: 'heberge', motif: null })), '85191_0420_00012');
		expect(servie.kind === 'ok' && servie.fiche.identite.parcelle).toEqual({ etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1 });
	});

	it('the projection drops every field it does not name: a served coordinate never reaches the page model', async () => {
		const avecPosition = {
			...IDENTITE,
			position: { lon: -1.426123, lat: 46.670345, type_position: 'entrance' },
			lon: -1.426123,
			lat: 46.670345
		};
		const fetchImpl = fetchRepondant(200, fiche(avecPosition, { etat: 'heberge', motif: null }));

		const issue = await getFicheAdresse(fetchImpl, '85191_0420_00012');

		expect(JSON.stringify(issue)).not.toMatch(/-?\d{1,3}\.\d{3,}/);
		expect(JSON.stringify(issue)).not.toContain('position');
	});

	it('an address out of coverage is a 200 outcome of its own, never `inconnue` (N-07)', async () => {
		const identite = {
			etat: 'etablie',
			ban_id: '35238_1234_00001',
			libelle: null,
			numero: null,
			rep: null,
			nom_voie: null,
			code_postal: null,
			commune: { code_insee: '35238', nom: 'Rennes' },
			departement: { code: '35', nom: 'Ille-et-Vilaine' },
			parcelle: null
		};
		const fetchImpl = fetchRepondant(
			200,
			fiche(identite, { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' })
		);

		const issue = await getFicheAdresse(fetchImpl, '35238_1234_00001');

		expect(issue.kind).toBe('hors_couverture');
		if (issue.kind !== 'hors_couverture') return;
		expect(issue.fiche.couverture).toEqual({ etat: 'hors_couverture', motif: 'territoire_hors_perimetre' });
		expect(issue.fiche.identite.commune.nom).toBe('Rennes');
	});

	it('404 `adresse_inconnue` and 404 `voie_sans_numero` are two outcomes, read from the served code', async () => {
		const inconnue = fetchRepondant(404, { title: 'Adresse inconnue', status: 404, code: 'adresse_inconnue' });
		const voie = fetchRepondant(404, { title: 'Voie sans numero', status: 404, code: 'voie_sans_numero' });

		expect(await getFicheAdresse(inconnue, '85191_9999_00001')).toEqual({ kind: 'inconnue' });
		expect(await getFicheAdresse(voie, '85191_0420')).toEqual({ kind: 'voie' });
	});

	it('503 (search or identity outage) and a network failure are `panne`, without a typed title', async () => {
		const adresse = fetchRepondant(503, { title: 'Adresse momentanement indisponible', status: 503, code: 'adresse_indisponible' });
		const reseau = vi.fn(async () => {
			throw new TypeError('fetch failed');
		});

		expect(await getFicheAdresse(adresse, '85191_0420_00012')).toEqual({
			kind: 'panne',
			panne: { statut: 503, titre: null, message: null }
		});
		expect(await getFicheAdresse(reseau, '85191_0420_00012')).toEqual({
			kind: 'panne',
			panne: { statut: null, titre: null, message: null }
		});
	});

	it('409, 429, 401 and 403 are typed by panne.ts, never the generic outage (SEM-01)', async () => {
		const conflit = await getFicheAdresse(fetchRepondant(409, { detail: 'texte serveur' }), '85191_0420_00012');
		const limite = await getFicheAdresse(
			fetchRepondant(429, { detail: 'texte serveur' }, { 'Retry-After': '30' }),
			'85191_0420_00012'
		);
		const refus = await getFicheAdresse(fetchRepondant(403, { detail: 'texte serveur' }), '85191_0420_00012');
		const nonAuth = await getFicheAdresse(fetchRepondant(401, { detail: 'texte serveur' }), '85191_0420_00012');

		expect(conflit).toMatchObject({ kind: 'panne', panne: { statut: 409, titre: TITRE_DEMANDE_REFUSEE } });
		expect(limite).toMatchObject({ kind: 'panne', panne: { statut: 429, titre: TITRE_LIMITE } });
		expect(refus).toMatchObject({ kind: 'panne', panne: { statut: 403, titre: TITRE_ACCES_REFUSE } });
		expect(nonAuth).toMatchObject({ kind: 'panne', panne: { statut: 401, titre: TITRE_ACCES_REFUSE } });
		for (const issue of [conflit, limite, refus, nonAuth]) {
			expect(JSON.stringify(issue)).not.toContain('texte serveur');
		}
	});

	it('a 404 without a served code is a refused request, never invented as `inconnue`', async () => {
		const issue = await getFicheAdresse(fetchRepondant(404, { detail: 'Not Found' }), '85191_0420_00012');

		expect(issue).toMatchObject({ kind: 'panne', panne: { statut: 404, titre: TITRE_DEMANDE_REFUSEE } });
	});

	it('encodes the key in the path: a key is never read as a path', async () => {
		const fetchImpl = fetchRepondant(404, { title: 'Adresse inconnue', status: 404, code: 'adresse_inconnue' });

		await getFicheAdresse(fetchImpl, '85191_0420_00012_a/b');

		expect(String(fetchImpl.mock.calls[0][0])).toContain('/v1/adresse/85191_0420_00012_a%2Fb');
	});
});

// --- 04-18 — landmarks of the place and declared documents (04-03 contract) -------------------

const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeurLieu(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

const REPERES = {
	dpe: {
		...valeurLieu({ field_id: 'adresse.dpe.nombre_declares', valeur: 3, unite: 'none', source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
		selection: {
			etat: 'unique',
			motif: 'document_valide',
			date: '2024-03-12',
			document: { numero: '2485E0123456X', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
			documents_ex_aequo: []
		}
	},
	ventes: {
		nombre: valeurLieu({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 2, unite: 'count', periode: '2021-2025', rattachement: 'parcelle_du_point' }),
		derniere: valeurLieu({ field_id: 'adresse.dvf.ventes_parcelle.derniere', valeur: '2025-11-20', unite: 'none', periode: '2021-2025', rattachement: 'parcelle_du_point' })
	},
	argiles: valeurLieu({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', periode: '2026-10-02', rattachement: 'au_point' }),
	reference_commune: {
		maisons: valeurLieu({ field_id: 'adresse.reference_commune.prix_median_maison', valeur: 2500, unite: 'euro_per_m2', rattachement: 'commune_reference' }),
		appartements: valeurLieu({ field_id: 'adresse.reference_commune.prix_median_appartement', etat: 'insufficient_sample', unite: 'euro_per_m2', rattachement: 'commune_reference' })
	}
};

describe('getFicheAdresse() — landmarks (04-03 `reperes`)', () => {
	it('projects the four landmarks as served, the selection of ① included', async () => {
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: { identite: IDENTITE, couverture: { etat: 'heberge', motif: null }, reperes: REPERES }, meta: META });

		const issue = await getFicheAdresse(fetchImpl, '85191_0420_00012');

		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.fiche.reperes?.dpe.valeur).toBe(3);
		expect(issue.fiche.reperes?.dpe.selection).toEqual(REPERES.dpe.selection);
		expect(issue.fiche.reperes?.dpe.rattachement).toBe('adresse_saisie_diagnostiqueur');
		expect(issue.fiche.reperes?.ventes.derniere.valeur).toBe('2025-11-20');
		expect(issue.fiche.reperes?.reference_commune.appartements.etat).toBe('insufficient_sample');
	});

	it('a landmark served without a state is never read as a value: it becomes an outage of that landmark', async () => {
		const sansEtat = { ...REPERES, argiles: { field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition forte' } };
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: { identite: IDENTITE, couverture: { etat: 'heberge', motif: null }, reperes: sansEtat }, meta: META });

		const issue = await getFicheAdresse(fetchImpl, '85191_0420_00012');

		expect(issue.kind === 'ok' && issue.fiche.reperes?.argiles.etat).toBe('temporary_error');
	});
});

const CARTE = {
	numero: '2485E0123456X',
	etiquette_dpe: 'C',
	etiquette_ges: 'B',
	type_batiment: 'maison',
	surface_habitable: 92,
	date_etablissement: '2024-03-12',
	date_fin_validite: '2034-03-11',
	badges: ['plus_recent'],
	adresse_declaree: null
};

const DOCUMENTS = {
	etat: 'value',
	motif: null,
	source: SOURCE_DPE,
	total: 3,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: '0123456789abcdef',
	distribution: { A: 0, B: 0, C: 2, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
	date_import: '2026-09-30',
	documents: [CARTE],
	selection: REPERES.dpe.selection,
	signal_plusieurs_logements: null,
	rattachement: 'adresse_saisie_diagnostiqueur'
};

describe('getDocumentsDpe() — one typed outcome per served answer (04-03)', () => {
	it('requests /v1/adresse/{ban_id}/dpe with the page, and the generation only when given', async () => {
		const premiere = fetchRepondant(200, { statut: 'ok', donnees: DOCUMENTS });
		const seconde = fetchRepondant(200, { statut: 'ok', donnees: DOCUMENTS });

		await getDocumentsDpe(premiere, '85191_0420_00012', '1', null);
		await getDocumentsDpe(seconde, '85191_0420_00012', '2', '0123456789abcdef');

		const url1 = demande(premiere);
		expect(url1.pathname).toBe('/v1/adresse/85191_0420_00012/dpe');
		expect(Object.fromEntries(url1.searchParams)).toEqual({ page: '1' });
		expect(Object.fromEntries(demande(seconde).searchParams)).toEqual({ page: '2', g: '0123456789abcdef' });
	});

	it('`ok`: the served list projected field by field, nothing recomputed', async () => {
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: { ...DOCUMENTS, interne: 'jamais' } });

		const issue = await getDocumentsDpe(fetchImpl, '85191_0420_00012', '1', null);

		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.documents.total).toBe(3);
		expect(issue.documents.generation).toBe('0123456789abcdef');
		expect(issue.documents.documents).toEqual([CARTE]);
		expect(issue.documents.selection).toEqual(REPERES.dpe.selection);
		expect(issue.documents.distribution).toEqual(DOCUMENTS.distribution);
		expect(issue.documents.date_import).toBe('2026-09-30');
		expect(JSON.stringify(issue)).not.toContain('jamais');
	});

	it('declared address (04-19, P-ADR-01): three served fields kept, a text only when published, raw keys dropped', async () => {
		const brut = { adresse_brut: '12 rue SENTINELLE', complement_adresse_logement: 'porte SENTINELLE' };
		const cartes = [
			{ ...CARTE, ...brut, adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null, interne: 'SENTINELLE' } },
			{ ...CARTE, ...brut, adresse_declaree: { etat: 'masquee', texte: 'SENTINELLE', motif: 'complement_present' } },
			{ ...CARTE, ...brut, adresse_declaree: { etat: 'inconnu', texte: 'SENTINELLE', motif: null } }
		];
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: { ...DOCUMENTS, documents: cartes } });

		const issue = await getDocumentsDpe(fetchImpl, '85191_0420_00012', '1', null);

		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.documents.documents.map((carte) => carte.adresse_declaree)).toEqual([
			{ etat: 'publiee', texte: '14 rue des Lilas', motif: null },
			{ etat: 'masquee', texte: null, motif: 'complement_present' },
			{ etat: 'masquee', texte: null, motif: null }
		]);
		expect(JSON.stringify(issue)).not.toContain('SENTINELLE');
	});

	it('404 `page_hors_limites` and 409 `generation_changee` are `page_invalide` (the loader goes back to page 1)', async () => {
		const horsLimites = fetchRepondant(404, { title: 'Page hors limites', status: 404, code: 'page_hors_limites' });
		const generation = fetchRepondant(409, { title: 'Generation changee', status: 409, code: 'generation_changee' });

		expect(await getDocumentsDpe(horsLimites, '85191_0420_00012', '9', null)).toEqual({ kind: 'page_invalide' });
		expect(await getDocumentsDpe(generation, '85191_0420_00012', '2', 'ffffffffffffffff')).toEqual({ kind: 'page_invalide' });
	});

	it('503 `documents_dpe_indisponibles` and a network failure are a section outage, never an empty list', async () => {
		const panne = fetchRepondant(503, { title: 'Documents DPE indisponibles', status: 503, code: 'documents_dpe_indisponibles' });
		const reseau = vi.fn(async () => {
			throw new TypeError('fetch failed');
		});

		expect(await getDocumentsDpe(panne, '85191_0420_00012', '1', null)).toEqual({
			kind: 'panne',
			panne: { statut: 503, titre: null, message: null }
		});
		expect(await getDocumentsDpe(reseau, '85191_0420_00012', '1', null)).toMatchObject({ kind: 'panne', panne: { statut: null } });
	});

	it('a 409 without the `generation_changee` code stays a typed refusal, not a page reset', async () => {
		const issue = await getDocumentsDpe(fetchRepondant(409, { detail: 'conflit' }), '85191_0420_00012', '1', null);

		expect(issue).toMatchObject({ kind: 'panne', panne: { statut: 409, titre: TITRE_DEMANDE_REFUSEE } });
	});
});

// --- 04.1-13 (D-127), plan 04.5-05 C2: the group of the documents attached by their exact raw ------
// address. Served apart since 2026-10-19; kept apart by the projection, never merged into the list
// of the ADEME key (D45-05, D45-06).
describe('getDocumentsDpe() — groupe rapproché par l’adresse brute, à part (C2)', () => {
	async function projeter(reponse: unknown): Promise<DocumentsDpeAdresse> {
		const issue = await getDocumentsDpe(fetchRepondant(200, reponse), BAN_ID_ADRESSE_BRUTE, '1', null);
		if (issue.kind !== 'ok') throw new Error(`projection ${issue.kind}`);
		return issue.documents;
	}

	it.each(NOMS_VARIANTES)('%s: the fields of the ADEME key are equal with and without the three keys of the group', async (nom) => {
		const { adresseBrute: avecGroupe, ...ademeAvec } = await projeter(variante(nom));
		const { adresseBrute: sansLeGroupe, ...ademeSans } = await projeter(sansGroupe(variante(nom)));
		expect(ademeAvec).toEqual(ademeSans);
		expect(sansLeGroupe).toBeNull();
		if (nom === 'sans-groupe') expect(avecGroupe).toBeNull();
	});

	it('an answer without the keys (before 2026-10-19) has no group, never a group counted zero', async () => {
		expect((await projeter(variante('sans-groupe'))).adresseBrute).toBeNull();
	});

	it('served: its state, its total and its cards in the served order, each with its served attachment', async () => {
		const reponse = variante('douze');
		const groupe = (await projeter(reponse)).adresseBrute;
		const servies = reponse.donnees.documents_adresse_brute as { numero: string }[];
		expect(groupe?.etat).toBe('value');
		expect(groupe?.total).toBe(12);
		expect(groupe?.documents.map((carte) => carte.numero)).toEqual(servies.map((carte) => carte.numero));
		expect(groupe?.documents.every((carte) => carte.rattachement === 'adresse_brute_exacte')).toBe(true);
		// The cards are projected like those of the ADEME key: classes, dates, badges, declared address.
		expect(groupe?.documents[0]).toEqual({
			numero: '2644E9000010S',
			etiquette_dpe: 'C',
			etiquette_ges: 'A',
			type_batiment: 'appartement',
			surface_habitable: 61.5,
			date_etablissement: '2026-01-20',
			date_fin_validite: '2036-01-19',
			badges: [],
			adresse_declaree: { etat: 'publiee', texte: '4 R JEAN JAURES 44000 NANTES', motif: null },
			rattachement: 'adresse_brute_exacte'
		});
		expect(groupe?.documents[3].adresse_declaree).toEqual({ etat: 'masquee', texte: null, motif: 'complement_present' });
		expect(groupe?.documents[9].badges).toEqual(['ancienne_methode', 'expire']);
	});

	it('not computed for the served generation: `not_available`, its total null kept null, no card', async () => {
		expect((await projeter(variante('non-calcule'))).adresseBrute).toEqual({ etat: 'not_available', total: null, documents: [] });
	});

	it('computed and empty: `value`, total 0, no card', async () => {
		expect((await projeter(variante('vide'))).adresseBrute).toEqual({ etat: 'value', total: 0, documents: [] });
	});

	it('an unreadable state of the group is no group; a card of an unknown attachment keeps its served key', async () => {
		const illisible = variante('servi');
		illisible.donnees.etat_adresse_brute = 'peut_etre';
		expect((await projeter(illisible)).adresseBrute).toBeNull();

		const inconnu = variante('servi');
		(inconnu.donnees.documents_adresse_brute as { rattachement: string }[])[1].rattachement = 'cle_future';
		const groupe = (await projeter(inconnu)).adresseBrute;
		expect(groupe?.documents.map((carte) => carte.rattachement)).toEqual(['adresse_brute_exacte', 'cle_future', 'adresse_brute_exacte']);
	});

	it('nothing of the group reaches the list of the ADEME key: documents, total, distribution, selection, finding', async () => {
		const reponse = variante('constat-ademe');
		const projete = await projeter(reponse);
		const brutes = new Set((reponse.donnees.documents_adresse_brute as { numero: string }[]).map((carte) => carte.numero));
		expect(projete.documents.some((carte) => brutes.has(carte.numero))).toBe(false);
		expect(projete.total).toBe(1);
		expect(projete.constat_d110?.criteres).toHaveLength(5);
		expect(JSON.stringify({ ...projete, adresseBrute: null })).not.toContain('adresse_brute_exacte');
	});
});

// C4 (plan 04.5-07, report 4.1-15; D45-05): the page asks the declared sales with the VEFA
// (`vefa=incluses`), keeps the served VEFA count of the list and the served mark of each card of the
// three lists, as served. Nothing is counted here.
describe('ventes — VEFA servies et marquées (04.5-07, C4)', () => {
	it('getVentesAdresse asks for the VEFA and the number and suffix rule (C3, 04.5-08), with the page and the token as read', async () => {
		const premiere = fetchRepondant(200, VENTES_DECLAREES_44109_AVEC_VEFA.body);
		await getVentesAdresse(premiere, BAN_ID_44109, '1', null);
		expect(demande(premiere).pathname).toBe(`/v1/adresse/${BAN_ID_44109}/ventes-declarees`);
		expect(Object.fromEntries(demande(premiere).searchParams)).toEqual({ page: '1', vefa: 'incluses', regle: 'numero_et_suffixe' });

		const seconde = fetchRepondant(200, VENTES_DECLAREES_44109_AVEC_VEFA.body);
		await getVentesAdresse(seconde, BAN_ID_44109, '2', 'fedcba9876543210');
		expect(Object.fromEntries(demande(seconde).searchParams)).toEqual({ page: '2', vefa: 'incluses', regle: 'numero_et_suffixe', vg: 'fedcba9876543210' });
	});

	it('C3 : a served suffix reason is kept as served, never turned into a zero', async () => {
		for (const motif of ['suffixe_sans_correspondance', 'suffixe_non_importe']) {
			const corps = { statut: 'ok', donnees: { etat: 'not_available', motif, rattachement: 'adresse_declaree_vente', source: null, periode: null, total: null, nombre_vefa: null, page: 1, pages: null, par_page: 10, generation: null, ventes: [] } };
			const issue = await getVentesAdresse(fetchRepondant(200, corps), BAN_ID_44109, '1', null);
			expect(issue.kind).toBe('ok');
			if (issue.kind !== 'ok') continue;
			expect([issue.ventes.etat, issue.ventes.motif, issue.ventes.total]).toEqual(['not_available', motif, null]);
		}
	});

	it('the served VEFA count of the declared list is kept as served; null when not served or not a count', async () => {
		const lire = async (nombreVefa: unknown) => {
			const corps = structuredClone(VENTES_DECLAREES_44109_AVEC_VEFA.body) as { donnees: Record<string, unknown> };
			if (nombreVefa === undefined) delete corps.donnees.nombre_vefa;
			else corps.donnees.nombre_vefa = nombreVefa;
			const issue = await getVentesAdresse(fetchRepondant(200, corps), BAN_ID_44109, '1', null);
			if (issue.kind !== 'ok') throw new Error('served list expected');
			return issue.ventes;
		};
		const servie = await lire(2);
		expect(servie.nombre_vefa).toBe(2);
		// The served total is the listed mutations, VEFA included, never recomputed.
		expect(servie.total).toBe(5);
		expect((await lire(0)).nombre_vefa).toBe(0);
		for (const illisible of [undefined, null, -1, 1.5, '2']) expect((await lire(illisible)).nombre_vefa, String(illisible)).toBeNull();
	});

	it('each card keeps its served mark: true for a VEFA, false for a sale or when not served as true', () => {
		const servies = (VENTES_DECLAREES_44109_AVEC_VEFA.body as { donnees: { ventes: Record<string, unknown>[] } }).donnees.ventes;
		expect(servies.map((carte) => projeterCarteVente(carte).hors_stats_vefa)).toEqual([false, true, true, false, false]);
		// A sale of a replay of plan 06, served without the key, is a sale.
		const sansCle = (VENTES_DECLAREES_44109.body as { donnees: { ventes: Record<string, unknown>[] } }).donnees.ventes[0];
		expect(projeterCarteVente(sansCle).hors_stats_vefa).toBe(false);
		for (const autre of [1, 'true', null]) expect(projeterCarteVente({ ...sansCle, hors_stats_vefa: autre }).hors_stats_vefa, String(autre)).toBe(false);
	});

	it('the cards of the parcel and of the similar sales keep the mark too', async () => {
		const parcelle = await getVentesParcelle(fetchRepondant(200, VENTES_PARCELLE_44109_AVEC_VEFA.body), BAN_ID_44109, '1', null);
		if (parcelle.kind !== 'ok') throw new Error('served list expected');
		expect(parcelle.ventes.ventes.map((carte) => carte.hors_stats_vefa)).toEqual([false, true, true, false, false]);

		const servies = (VENTES_PARCELLE_44109_AVEC_VEFA.body as { donnees: { ventes: Record<string, unknown>[] } }).donnees.ventes;
		const ressemblantes = {
			statut: 'ok',
			donnees: {
				etat: 'value', motif: null, rattachement: 'rayon_du_point', liste_incomplete: false, effectif: 2,
				criteres_appliques: { type: 'appartement', surface: null, tolerance: null, surface_min: null, surface_max: null, perimetre: 'rayon', rayon_m: 500, periode: { annees: 5, debut: '2021', fin: '2025' } },
				source: { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)' }, page: 1, pages: 1, par_page: 10, generation: 'fedcba9876543210',
				ventes: [servies[1], servies[0]].map((carte) => ({ ...carte, distance_m: 120 }))
			}
		};
		const criteres = { type: 'appartement', surface: null, tolerance: null, rayon: null, periode: null };
		const issue = await getVentesRessemblantes(fetchRepondant(200, ressemblantes), BAN_ID_44109, criteres, '1', null);
		if (issue.kind !== 'ok') throw new Error('served list expected');
		expect(issue.comparables.ventes.map((carte) => carte.hors_stats_vefa)).toEqual([true, false]);
	});
});
