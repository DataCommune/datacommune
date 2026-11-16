// Phase 50 « qualité interface zéro défaut » — minimal route mocks of the FIVE public surfaces
// (plan 50-08, D-05). Helper module, deliberately NOT a `*.spec.ts`: `scripts/check-e2e-manifest.mjs`
// only classifies `tests/e2e/**/*.spec.ts`, so a helper here needs no manifest entry — and must
// never gain one.
//
// SCOPE, and why it is narrow. `matrix-qualite.spec.ts` is a LAYOUT + ACCESSIBILITY matrix: it
// measures overflow, landmarks, axe violations, reduced motion and print rendering. It is NOT a
// data-contract check — that lives in `v35-critical.spec.ts` (958 lines, which exports nothing and
// must not be imported, refactored or copied wholesale). The bodies below are therefore reduced to
// what each surface needs to RENDER without a console error or an unmocked request.
//
// FIDELITY WHERE FIDELITY IS LOAD-BEARING. Two fields cannot be reduced without silently
// short-circuiting the very DOM the matrix must measure:
//   - `meta.source` of /v1/commune carries THIRTEEN entries, one per key of
//     COMMUNE_META_SOURCE_ORDER and in that rank order: `sourcesCommuneParDataset` is fail-closed
//     on any other length (T-49-12), so a 3-entry mock would render `{}` — no ℹ️ source line, and
//     no printed sources table (D-15 would then assert on an absent block).
//
// 04-08 (D-101): the two `/bien` surfaces and their Resolve / logements / inventory mocks left with
// the route (a 308 now); the address and document surfaces read fixtures/surfaces-phase4.ts.

import type { Page } from '@playwright/test';
import {
	COMMUNE_META_SOURCE_NOMS,
	COMMUNE_META_SOURCE_ORDER
} from '../../../src/lib/data/explications';
import { configureSsrSource } from './bien-ssr-source';
import { COMMUNE_FLAT_85047 } from './commune-indicators';

// --- Identifiants partagés ----------------------------------------------------------------------

export const CODE_INSEE = '85047';

// --- /auth/me ----------------------------------------------------------------------------------

/** Anonymous visitor — the state every public surface is designed for. */
export async function mockAuthAnonyme(page: Page) {
	await page.route('**/auth/me', (route) =>
		route.fulfill({
			status: 401,
			contentType: 'application/json',
			body: JSON.stringify({ detail: 'Non authentifié' })
		})
	);
}

// --- /v1/commune -------------------------------------------------------------------------------

// One producer name per key, in the rank order of COMMUNE_META_SOURCE_ORDER (a `SourceAttribution`
// carries no dataset key, so its RANK is the whole contract). Names are the publication names the
// API really serves, read from the shared map instead of hand-copied.
const META_SOURCES_13 = COMMUNE_META_SOURCE_ORDER.map((dataset) => ({
	nom: COMMUNE_META_SOURCE_NOMS[dataset],
	licence: 'Licence ouverte',
	url: 'https://www.data.gouv.fr/',
	producteur: 'Producteur public'
}));

const COMMUNE_85047 = {
	code_insee: CODE_INSEE,
	nom_commune: 'Challans',
	code_departement: '85',
	code_region: '52',
	population: 21516,
	nb_menages: 9800,
	nb_logements: 11200,
	nb_logements_vacants_recensement: 820,
	densite: 287.4,
	superficie: 74.9,
	annee_demographie: 2021,
	revenu_median: 21340,
	taux_pauvrete: 12.8,
	indice_gini: 0.27,
	part_imposee: 58.2,
	annee_revenus: 2021,
	zone_abc: 'B2',
	zone_abc_reclassement: false,
	taux_vacance: 9.4,
	nb_vacants_plus_2ans: 410,
	annee_vacance: 2022,
	taux_tfpb: 41.3,
	taux_th_rs: 18.7,
	taux_teom: 11.2,
	nom_epci: 'CC Challans Gois Vie Océan',
	annee_fiscalite: 2023,
	loyer_m2_appartement: 9.1,
	loyer_m2_maison: 8.4,
	annee_loyers: 2023
};

/** Surface 2 — `/commune/[insee]`, nominal state. Since 03-07 the route serves every flat column,
 *  each null one with its declared state (D-47): the synthesis reads them, and an undeclared
 *  absence would be reported on the console, which the quality fixture refuses. */
export async function mockCommune(page: Page) {
	return configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/commune',
			query: { code_insee: CODE_INSEE },
			minCalls: 1,
			maxCalls: 1,
			responses: [
				{
					status: 200,
					body: {
						statut: 'ok',
						donnees: [COMMUNE_FLAT_85047],
						meta: { source: META_SOURCES_13 }
					}
				}
			]
		}
	]);
}

// --- /v1/communes, /v1/departements -------------------------------------------------------------
// Plan 56-01 (BUG-02) — annuaire public `/communes`. Shapes mirror `frontend/src/lib/api/communes.ts`
// (`CommunesListResponse`, `DepartementRow`), wrapped in the same `ReponseApi` envelope as every
// other data router.

const COMMUNES_LISTE = {
	statut: 'ok',
	donnees: {
		results: [{ code_insee: CODE_INSEE, nom: 'Challans', departement: '85', population: 21516 }],
		total_count: 1,
		page: 1,
		per_page: 50,
		has_next: false
	}
};

const DEPARTEMENTS_LISTE = {
	statut: 'ok',
	donnees: [{ code: '85', nom: 'Vendée', region: 'Pays de la Loire' }]
};

/** Surface 10 — `/communes`, annuaire (plan 56-01). */
export async function mockCommunesListe(page: Page) {
	await page.route('**/v1/communes**', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(COMMUNES_LISTE)
		})
	);
	await page.route('**/v1/departements**', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(DEPARTEMENTS_LISTE)
		})
	);
}
