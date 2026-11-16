import { describe, it, expect, vi } from 'vitest';
import { getDocumentDpe, NUMERO_DPE_RE, projeterDocument } from './documents-dpe';
import { TITRE_DEMANDE_REFUSEE, TITRE_LIMITE } from '$lib/data/panne';

// The real `api()` client runs; only the fetch it is handed is a double, so the URL the server
// loader would request is observed exactly as sent (D-102: the ADEME number, nothing else).

const NUMERO = '2385E0123456A';
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', reference: null, ...partiel };
}

const DOCUMENT = {
	numero: NUMERO,
	source_identity: { reference: NUMERO, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
	disponibilite: 'disponible',
	methode: 'actuelle',
	dates: { etablissement: '2024-03-01', fin_validite: '2034-02-28' },
	reperes: {
		etiquette_energie: {
			classe: valeur({ field_id: 'dpe.document.etiquette_dpe', valeur: 'D', unite: 'none' }),
			mesure: valeur({ field_id: 'dpe.document.conso_ep', valeur: 17830.4, unite: 'kwh_ep_per_year' })
		}
	},
	association: 'source_ban_equality',
	address_check: 'unverified'
};

function reponse(status: number, body: unknown, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

function fetchRepondant(status: number, body: unknown, headers: Record<string, string> = {}) {
	return vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => reponse(status, body, headers));
}

function demande(fetchImpl: ReturnType<typeof fetchRepondant>): URL {
	expect(fetchImpl).toHaveBeenCalledTimes(1);
	return new URL(String(fetchImpl.mock.calls[0][0]), 'http://origin.invalid');
}

describe('NUMERO_DPE_RE — forme ADEME par défaut (bloc E), même motif que la route', () => {
	it.each([NUMERO, '2244E0000009Z', '0000000000000'])('accepte %s', (numero) => {
		expect(NUMERO_DPE_RE.test(numero)).toBe(true);
	});
	it.each(['2385e0123456a', '2385E0123456', '2385E0123456AB', '2385E01234-6A', '', ' 2385E0123456A', '2385E0123456A/'])(
		'refuse « %s »',
		(saisie) => {
			expect(NUMERO_DPE_RE.test(saisie)).toBe(false);
		}
	);
});

describe('getDocumentDpe — une issue typée par réponse servie (SEM-01)', () => {
	it('lit /v1/dpe/{numero} et projette le document servi', async () => {
		const fetchImpl = fetchRepondant(200, { statut: 'ok', donnees: DOCUMENT });

		const issue = await getDocumentDpe(fetchImpl, NUMERO);

		expect(demande(fetchImpl).pathname).toBe(`/v1/dpe/${NUMERO}`);
		expect(demande(fetchImpl).search).toBe('');
		expect(issue.kind).toBe('ok');
		if (issue.kind !== 'ok') return;
		expect(issue.document.numero).toBe(NUMERO);
		expect(issue.document.source_identity.sources).toEqual([{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }]);
		expect(issue.document.reperes?.etiquette_energie.mesure.valeur).toBe(17830.4);
	});

	it('404 dpe_inconnu : la couverture servie, jamais la saisie', async () => {
		const fetchImpl = fetchRepondant(404, { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['44', '85'] } });

		expect(await getDocumentDpe(fetchImpl, NUMERO)).toEqual({ kind: 'inconnu', departements: ['44', '85'] });
	});

	it('404 sans code dpe_inconnu : refus typé, jamais « DPE inconnu »', async () => {
		const issue = await getDocumentDpe(fetchRepondant(404, { title: 'Not Found', status: 404 }), NUMERO);

		expect(issue).toMatchObject({ kind: 'panne', panne: { statut: 404, titre: TITRE_DEMANDE_REFUSEE } });
	});

	it.each([500, 503])('%s (publication refusée comprise) : panne, sans titre typé', async (statut) => {
		const issue = await getDocumentDpe(fetchRepondant(statut, { title: 'x', status: statut }), NUMERO);

		expect(issue).toEqual({ kind: 'panne', panne: { statut, titre: null, message: null } });
	});

	it('429 : titré par panne.ts, jamais « momentanément indisponible »', async () => {
		const issue = await getDocumentDpe(fetchRepondant(429, { title: 'Trop de demandes', status: 429 }, { 'Retry-After': '30' }), NUMERO);

		expect(issue).toMatchObject({ kind: 'panne', panne: { statut: 429, titre: TITRE_LIMITE } });
	});

	it('réseau en échec : panne', async () => {
		const fetchImpl = vi.fn(async () => {
			throw new TypeError('network');
		});

		expect(await getDocumentDpe(fetchImpl, NUMERO)).toEqual({ kind: 'panne', panne: { statut: null, titre: null, message: null } });
	});
});

describe('projeterDocument — seuls les membres rendus franchissent le modèle SSR', () => {
	it('une clé inattendue de la réponse n’atteint pas le modèle', () => {
		const projete = projeterDocument({ ...DOCUMENT, adresse_brut: 'SENTINELLE', id: 42 });

		expect(JSON.stringify(projete)).not.toContain('SENTINELLE');
		expect(Object.keys(projete).sort()).toEqual([
			'adresse_ban',
			'adresse_declaree',
			'adresse_divergente',
			'caracteristiques',
			'dates',
			'disponibilite',
			'methode',
			'numero',
			'provenance',
			'reperes',
			'source_identity',
			'statut'
		]);
	});

	it('une source hors de l’énumération fermée est écartée', () => {
		const projete = projeterDocument({
			...DOCUMENT,
			source_identity: { reference: NUMERO, sources: [{ dataset: 'https://evil.example', url: 'https://evil.example' }] }
		});

		expect(projete.source_identity.sources).toEqual([]);
	});

	it('un numéro de statut hors de la forme ADEME ne devient jamais un lien', () => {
		const projete = projeterDocument({
			...DOCUMENT,
			statut: { code: 'remplace', validite: 'expired', date: null, remplace_par: 'javascript:alert(1)', remplace: '../admin' }
		});

		expect(projete.statut).toEqual({ code: 'remplace', validite: 'expired', date: null, remplace_par: null, remplace: null });
	});

	it('adresse déclarée masquée : aucun texte ne franchit le modèle, même servi', () => {
		const projete = projeterDocument({
			...DOCUMENT,
			adresse_declaree: { etat: 'masquee', texte: '12 rue SENTINELLE', motif: 'complement_present' }
		});

		expect(projete.adresse_declaree).toEqual({ etat: 'masquee', texte: null, motif: 'complement_present' });
	});

	it('document retiré : aucun descriptif, même si la réponse en porte', () => {
		const projete = projeterDocument({ ...DOCUMENT, disponibilite: 'retire' });

		expect(projete.dates).toBeNull();
		expect(projete.reperes).toBeNull();
	});
});

// Plan 04.5-12 (C1, 04.1-12): the API serves, next to each yearly total, the per-square-metre value
// published by the ADEME (`mesure_m2`). It is projected as served, never derived from the total.
describe('projeterDocument — valeur par m² servie à côté du total (C1, D45-05)', () => {
	const ETIQUETTES = {
		etiquette_energie: {
			classe: valeur({ field_id: 'dpe.document.etiquette_dpe', valeur: 'D', unite: 'none' }),
			mesure: valeur({ field_id: 'dpe.document.conso_ep', valeur: 10258, unite: 'kwh_ep_per_year' }),
			mesure_m2: valeur({ field_id: 'dpe.document.conso_ep_m2', valeur: 63, unite: 'kwh_ep_per_m2_per_year' })
		},
		etiquette_climat: {
			classe: valeur({ field_id: 'dpe.document.etiquette_ges', valeur: 'C', unite: 'none' }),
			mesure: valeur({ field_id: 'dpe.document.emissions_ges', valeur: 10258, unite: 'kg_co2_eq_per_year' }),
			mesure_m2: valeur({ field_id: 'dpe.document.emissions_ges_m2', etat: 'not_reported' })
		}
	};

	it('mesure_m2 est gardée à côté de mesure, telle que servie (valeur, état, unité, source)', () => {
		const projete = projeterDocument({ ...DOCUMENT, reperes: ETIQUETTES });

		expect(projete.reperes?.etiquette_energie.mesure.valeur).toBe(10258);
		expect(projete.reperes?.etiquette_energie.mesure_m2).toMatchObject({
			field_id: 'dpe.document.conso_ep_m2',
			etat: 'value',
			valeur: 63,
			unite: 'kwh_ep_per_m2_per_year',
			source: SOURCE_DPE
		});
		expect(projete.reperes?.etiquette_climat.mesure_m2).toMatchObject({ field_id: 'dpe.document.emissions_ges_m2', etat: 'not_reported', valeur: null });
	});

	it('mesure_m2 non servie : panne de cette ligne sous son identifiant, jamais un manque ni un quotient', () => {
		const projete = projeterDocument(DOCUMENT);

		expect(projete.reperes?.etiquette_energie.mesure_m2).toMatchObject({ field_id: 'dpe.document.conso_ep_m2', etat: 'temporary_error', valeur: null });
		expect(projete.reperes?.etiquette_climat.mesure_m2).toMatchObject({ field_id: 'dpe.document.emissions_ges_m2', etat: 'temporary_error', valeur: null });
	});

	it('document retiré : aucune valeur par m² ni aucun total, même si la réponse en porte', () => {
		const projete = projeterDocument({ ...DOCUMENT, disponibilite: 'retire', reperes: ETIQUETTES, caracteristiques: [ETIQUETTES.etiquette_energie.mesure] });

		expect(projete.reperes).toBeNull();
		expect(projete.caracteristiques).toEqual([]);
		expect(JSON.stringify(projete)).not.toMatch(/10258|conso_ep|emissions_ges/);
	});
});
