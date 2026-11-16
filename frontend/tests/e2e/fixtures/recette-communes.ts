import { readFileSync } from 'node:fs';
import { expect, type APIRequestContext } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './ports';
import { COMMUNE_FLAT_85047, COMMUNE_INDICATORS_SYNTHETIC } from './commune-indicators';

// Synthetic answers of the acceptance replay (03-12, D-53), reused from the specs of the phase:
//   - the homonyms of commune-search.spec.ts (03-04): two « Le Pin », one out of coverage;
//   - the fiche of fiche-commune.spec.ts (03-07/08): flat columns and dated DVF/DPE blocks, moved
//     under the hosted homonym so that task 1 lands on a fiche with a served median price;
//   - the frozen comparison answers of 03-09 (`__fixtures__/commune-comparaison.json`, guarded by
//     api/backend/tests/test_comparison_fixture_contract.py): three communes, one out of coverage;
//   - the `/v1/datasets` answer of 03-11 (`__fixtures__/datasets-api.json`).
// Nothing here is production truth: every figure is a fixture value the page must recopy.

type Hosting = { etat: 'heberge' | 'hors_couverture'; motif: string | null; departements: string[] };

const HOSTED: Hosting = { etat: 'heberge', motif: null, departements: ['35'] };
const OUTSIDE: Hosting = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] };

/** Task 1: the homonym out of coverage (listed, with its badge and reason). */
export const LE_PIN_79 = {
	code_insee: '79211',
	nom: 'Le Pin',
	code_postal: null,
	departement: '79',
	population: 1000,
	population_annee: null,
	population_source: 'geo_communes',
	hebergement: OUTSIDE
};

/** Task 1: the homonym to open, told apart by its departement and postal code. */
export const LE_PIN_35 = {
	code_insee: '35999',
	nom: 'Le Pin',
	code_postal: '35170',
	departement: '35',
	population: 750,
	population_annee: 2022,
	population_source: 'demographie',
	hebergement: HOSTED
};

/** `/v1/commune?code_insee=35999`: the fiche of fiche-commune.spec.ts under the hosted homonym. */
export const FICHE_LE_PIN_35 = {
	statut: 'ok',
	donnees: [
		{
			...COMMUNE_FLAT_85047,
			...COMMUNE_INDICATORS_SYNTHETIC,
			code_insee: '35999',
			nom_commune: 'Le Pin',
			code_departement: '35',
			code_region: '53',
			population: 750,
			annee_demographie: 2022
		}
	],
	meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
};

// --- Comparison (task 3) --------------------------------------------------------------------

interface Commune {
	code_insee: string;
	nom: string;
	departement: string;
	hebergement: Hosting;
	synthese: Record<string, unknown> | null;
}
interface Critere {
	cle: string;
	cellules: { code_insee: string; ecart: { valeur: number; unite: string } | null; motif_non_calcul: string | null }[];
}
export interface ReponseComparaison {
	statut: string;
	donnees: { communes: Commune[]; criteres: Critere[]; codes_inconnus: string[] };
	meta: { source: unknown[] } | null;
}

const COMPARAISONS = JSON.parse(
	readFileSync(new URL('../../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')
) as Record<string, ReponseComparaison>;

/** Two hosted communes, 44991 « Ancienne » then 44992 « Courante ». */
export const DEUX = COMPARAISONS.deux_communes_hebergees;

/** The three communes of the frozen answer, 85991 out of coverage; the unknown code dropped. */
export const TROIS: ReponseComparaison = {
	...COMPARAISONS.trois_communes_dont_hors_couverture,
	donnees: { ...COMPARAISONS.trois_communes_dont_hors_couverture.donnees, codes_inconnus: [] }
};

export const [ANCIENNE, COURANTE, HORS_COUVERTURE] = TROIS.donnees.communes;

/** `/v1/commune` of a hosted commune of the comparison, from the same frozen answer. */
export function ficheComparee(code: string) {
	const commune = DEUX.donnees.communes.find((item) => item.code_insee === code)!;
	return { statut: 'ok', donnees: [commune.synthese], meta: DEUX.meta };
}

/** A search result of `/v1/communes/search` for a commune of the comparison. */
export function resultat(commune: Commune) {
	return {
		code_insee: commune.code_insee,
		nom: commune.nom,
		code_postal: null,
		departement: commune.departement,
		population: 100,
		population_annee: null,
		population_source: 'geo_communes',
		hebergement: commune.hebergement
	};
}

/** The house median line, served gap for column 2 and reason for the column out of coverage. */
export const CLE_PRIX_MAISONS = 'indicateurs_dvf.residentiel_maison.courante.mediane';

// --- Data & method (task 4) -----------------------------------------------------------------

export const DATASETS = JSON.parse(
	readFileSync(new URL('../../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
) as unknown;

// --- Synthetic source contract ----------------------------------------------------------------

type Reply = { status: number; body: unknown };
export interface Attente {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
}

/** One exact read, answered `count` times at most (at least once). */
export function lecture(pathname: string, query: Record<string, string>, body: unknown, count = 1): Attente {
	return {
		method: 'GET',
		pathname,
		query,
		minCalls: 1,
		maxCalls: count,
		responses: Array.from({ length: count }, () => ({ status: 200, body }))
	};
}

export const recherche = (q: string, donnees: unknown[], limit = '8') =>
	lecture('/v1/communes/search', { q, limit, inclure_arrondissements: 'true' }, { statut: 'ok', donnees });
export const commune = (code: string, body: unknown, count = 1) =>
	lecture('/v1/commune', { code_insee: code }, body, count);
export const referentiel = (code: string, nom: string) =>
	lecture('/v1/communes', { code_insee: code }, {
		statut: 'ok',
		donnees: { results: [{ code_insee: code, nom, departement: code.slice(0, 2), code_postal: null }] }
	});
export const comparaison = (codes: string[], body: unknown, count = 1) =>
	lecture('/v1/communes/comparaison', { codes: codes.join(',') }, body, count);
export const datasets = (count = 1) => lecture('/v1/datasets', {}, DATASETS, count);

/** Replaces the whole contract of the synthetic source (fail-closed: anything else is a 599). */
export async function configurer(request: APIRequestContext, attentes: Attente[]): Promise<void> {
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations: attentes } });
	expect(res.ok()).toBe(true);
}

/** No violation and no unmet expectation in the journal of the synthetic source. */
export async function contratTenu(request: APIRequestContext): Promise<void> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}
