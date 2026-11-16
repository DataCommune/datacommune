import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { CODE_INSEE, mockAuthAnonyme, mockCommune } from './fixtures/mocks-matrice';
import { readFileSync } from 'node:fs';
import { configureSsrSource, type SourceExpectation, type SsrSourceProbe } from './fixtures/bien-ssr-source';
import { BAN_ID, NUMERO, attenteDocument, sourceAdresse, sourceDocument } from './fixtures/surfaces-phase4';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from '../../src/lib/data/__fixtures__/departement';

// Phase 50 « qualité interface zéro défaut » — QUALITY MATRIX (plan 50-08, UI-01/UI-02/UI-04).
//
// THE FILE NAME IS LOCKED. `playwright.config.ts` (plan 50-01) scopes its `firefox` and `webkit`
// projects with `testMatch: /matrix-qualite\.spec\.ts/`, and the cross-browser CI step names this
// path. Any other name matches ZERO test in those two projects — silently, with a green exit code.
//
// WHAT IT PROVES, on the FIVE public surfaces of D-05 (landing, Fiche Commune, Fiche Bien nominal,
// documentary DPE inventory, /roadmap), extended by plan 56-01 (BUG-02) to the FIVE secondary
// public surfaces (/datasets, /guides, /reference, /playground, /communes). Phase 03-02 (D-39)
// removed the /roadmap, /guides, /reference and /playground routes, and their surfaces with them:
// SIX surfaces remain, all iterated by the loops below. Phase 03-05 (D-38) turned the landing into
// the direct commune search: the `accueil` surface (h1 + search field), with its own print,
// reduced-motion and « keyboard to the field » tests. Plan 03-10 added the comparator (`compare`,
// /comparer): SEVEN surfaces, plus its own scroll-region, print and reduced-motion tests. Plan
// 04-08 (D-101): `/bien` is a 308; the Fiche Bien nominal and the documentary inventory surfaces
// are replaced by the address page (`adresse`) and the DPE document page (`document`) that took
// their content. Their former own tests — inventory action, inventory print, Fiche Bien print and
// its reduced-motion target (the PDF button, a text now forbidden) — left with the route; each is
// mapped in 04-08-SUMMARY.md to the spec that keeps its invariant. The keyboard one is ported: the
// « DPE n° … » link of the address page opens the document by keyboard.
//   - D-04 : at 320 / 375 / 768 / 1440 px, no horizontal overflow, the content landmark is visible,
//     one primary interactive element is visible with a non-zero box, and no page error is raised.
//   - D-02 : axe-core WCAG 2.1 AA scan, zero `serious`/`critical` violation.
//   - D-08 : under `prefers-reduced-motion: reduce`, computed durations fall to effectively zero.
//   - D-15 : under `media: print`, both fiches render the dated header, the qualification, the
//     ERRIAL/Géorisques renvoi and the sources table, AND every piece of site chrome or export
//     affordance disappears (site header, marketing footer, CSV / PDF / clipboard buttons, ℹ️).
//     The inventory state has its own print test: its summary and reservations remain visible while
//     consultation controls disappear.
//   - the first inventory action keeps a usable target inside its own table cell at all four widths.
//   - SC 1, keyboard clause — THREE named points only: the skip link is the first tab stop on the
//     five surfaces, Escape closes the ℹ️ panel AND returns focus to its trigger (asserted AFTER
//     tabbing INTO the panel — see the block), and the first documentary action opens by keyboard.
//     This block alone runs on `chromium` + `firefox`, NOT on `webkit`
//     (`grepInvert` on the webkit project, motive measured and written in both places).
//
// EXPLICITLY NOT DONE HERE:
//   - The COMPLETE tab order of a surface is NOT asserted, nor is any name/role announcement:
//     that is block A of `50-NVDA-CHECKLIST.md`, a manual pass, and D-03 reserves that half to a
//     human on purpose. The three assertions above cover three named points of the clause, not the
//     clause as a whole — read them as such.
//   - Nothing is proven about the keyboard on SAFARI. The webkit build does not carry Safari's
//     default tab-order policy, so this block is not run there and no claim is made from it.
//   - No `toHaveScreenshot()` / visual baseline — refused by D-04 (maintenance cost not accepted).
//   - No `waitForTimeout` (forbidden in a `critical` spec by `scripts/check-e2e-manifest.mjs`):
//     waits are `waitForLoadState('networkidle')` plus state `expect`s.
//   - No data-contract assertion: micro-states, prices, denominators and dataset degradation stay
//     covered Chromium-only by `v35-critical.spec.ts`, which is NOT re-run ×3 (D-01 cost bound).
//
// `test` comes from `@playwright/test`, not from `./fixtures/quality` — same choice as
// `honnetete-publique.spec.ts`. The fail-closed fixture aborts on any unmocked API request, which
// is the right policy for a contract gate; here axe-core injects its own script into the page and
// the matrix is deliberately mock-minimal, so the surface-level guards below (page errors, explicit
// landmarks) carry the hermeticity instead.

// --- Largeurs (D-04) ---------------------------------------------------------------------------
// 320 = smallest viewport still in scope, 375 = iPhone SE/13 mini class, 768 = tablet portrait /
// Tailwind `md` boundary, 1440 = desktop reference of the 50-UI-SPEC.
const LARGEURS = [320, 375, 768, 1440] as const;
const HAUTEUR = 900;

// Widths used by the axe scan (D-02): the desktop reference and the mobile reference. Scanning all
// four would triple the axe cost for two layouts that resolve to the same rules.
const LARGEURS_AXE = [1440, 375] as const;

// --- Surfaces (D-05) ---------------------------------------------------------------------------

interface Surface {
	/** Identifier used in test titles — keep short, it is repeated 4 to 6 times. */
	id: string;
	chemin: string;
	/** Route mocks to install BEFORE the first navigation. */
	poserMocks: (page: Page) => Promise<void | SsrSourceProbe>;
	/**
	 * Content landmark: the element whose visibility means « this surface really rendered ».
	 * A missing landmark must FAIL the test, never let it pass on an empty page.
	 */
	repere: (page: Page) => Locator;
	/** Primary interactive element of the surface — must be visible with a non-zero box. */
	interactif: (page: Page) => Locator;
}

const SURFACES: readonly Surface[] = [
	// Plan 03-05 — `/` is the direct commune search (D-38): prerendered, nothing fetched at load
	// time. The primary interactive element is the anchor of the screen, the search field.
	{
		id: 'accueil',
		chemin: '/',
		poserMocks: mockAuthAnonyme,
		// 04.8-07 (UI-SPEC § 6.6, § 3.13): the v4 h1 and the large field.
		repere: (page) =>
			page.getByRole('heading', { level: 1, name: 'Une adresse, en clair' }),
		interactif: (page) => page.getByRole('combobox', { name: 'Adresse, commune ou code postal' })
	},
	{
		id: 'commune',
		chemin: `/commune/${CODE_INSEE}`,
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return mockCommune(page);
		},
		repere: (page) => page.getByRole('heading', { name: 'Challans', exact: true }),
		// 04.8-04 (UI-SPEC v4 § 6.2): the five themes are open panels, nothing to unfold; the primary
		// control is the one filled button of the fiche. A control collapsed to a zero-size box at 320 px
		// is exactly the D-04 defect to catch.
		interactif: (page) => page.getByRole('link', { name: 'Comparer avec une autre commune' })
	},
	// 04-08 — the address page (D-112) and the DPE document page (LIEU-03), on the synthetic answers
	// shared with their own specs. Landmark: the h1; primary control: the comparison link.
	{
		id: 'adresse',
		chemin: `/adresse/${BAN_ID}`,
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return configureSsrSource(page.request, sourceAdresse());
		},
		repere: (page) => page.getByRole('heading', { level: 1, name: '12 rue des Lilas', exact: true }),
		interactif: (page) => page.getByRole('link', { name: 'Comparer avec une autre adresse' })
	},
	{
		id: 'document',
		chemin: `/dpe/${NUMERO}`,
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return configureSsrSource(page.request, sourceDocument());
		},
		// 04.8-04 (UI-SPEC v4 § 6.7): the served number moved from the h1 to the line under it.
		repere: (page) => page.locator('.document-sous .document-numero').filter({ hasText: NUMERO }),
		interactif: (page) => page.getByRole('link', { name: 'Comparer avec un autre DPE' })
	},
	// --- Plan 56-01 (BUG-02) — surfaces publiques secondaires (deux restent après 03-02) ---------
	// Plan 03-11 — `/datasets` is « Données & méthode » (D-40), server-rendered from
	// `/v1/datasets` (the fixture generated by the backend, served to the SSR load by the synthetic
	// source). The landmark is the anchor of the screen, the section « Ce que couvre cette
	// site »; the primary control, the first chip of the catalogue filter.
	{
		id: 'datasets',
		chemin: '/datasets',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return configureSsrSource(page.request, [
				{
					method: 'GET',
					pathname: '/v1/datasets',
					query: {},
					minCalls: 1,
					maxCalls: 1,
					responses: [{ status: 200, body: DATASETS_API }]
				}
			]);
		},
		repere: (page) => page.getByRole('region', { name: 'Couverture', exact: true }),
		interactif: (page) =>
			page.getByRole('group', { name: 'Filtrer les sources par thème' }).getByRole('button').first()
	},
	// Plan 03-04 — `/communes` is the commune search results page (D-43, D-44): two homonyms,
	// one out of coverage, served to the SSR load by the synthetic source (a browser route cannot
	// observe an SSR fetch). The landmark is the anchor of the screen, the h2 of the first card.
	{
		id: 'communes',
		chemin: '/communes?q=le+pin',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return mockRechercheCommunes(page);
		},
		// 04.8-07 (UI-SPEC § 6.5): the first h2 is now the panel « Communes »; the large field.
		repere: (page) => page.getByRole('heading', { level: 2 }).first(),
		interactif: (page) => page.getByRole('combobox', { name: 'Adresse, commune ou code postal' })
	},
	// Plan 03-10 — `/comparer` is the comparator (D-48, D-49): two communes read in one call to
	// `/v1/communes/comparaison`, served to the SSR load by the synthetic source. The landmark is
	// the anchor of the screen, the table's scroll region; its primary control, « Ajouter une
	// commune ». At 320 px the two columns scroll inside the region, never the page. Since 04.5-09
	// (B5) the add control lives in the folded « Modifier la sélection »: the control in view on
	// arrival is its summary.
	{
		id: 'compare',
		chemin: '/comparer?communes=44991,44992',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return mockComparaison(page, ['44991', '44992'], COMPARAISONS.deux_communes_hebergees);
		},
		repere: (page) => page.getByRole('region', { name: 'Tableau de comparaison' }),
		interactif: (page) => page.locator('details.comparer-modifier > summary')
	},
	// 04.8-06 (D-35) — the information pages in their v4 simple template, prerendered, nothing fetched. The
	// landmark is the h1; the primary control, the « Accueil » step of the breadcrumb.
	...([
		['mentions-legales', 'Mentions légales'],
		['cgu', "Conditions Générales d'Utilisation"],
		['confidentialite', 'Politique de Confidentialité'],
		['couverture', 'Couverture et limites'],
		['developpeurs', 'Développeurs'],
		['ce-que-nous-ne-montrons-pas', 'Ce que nous ne montrons pas, et pourquoi']
	] as const).map(
		([slug, titre]): Surface => ({
			id: slug,
			chemin: `/${slug}`,
			poserMocks: mockAuthAnonyme,
			repere: (page) => page.getByRole('heading', { level: 1, name: titre, exact: true }),
			interactif: (page) => page.getByRole('navigation', { name: "Fil d'Ariane" }).getByRole('link', { name: 'Accueil' })
		})
	),
	// --- Plan 04.8-05 — the departement page and the list of departements (UI-SPEC v4 § 6.3, § 8),
	// read by the SSR load from `/v1/departements/44` and `/v1/departements` (the shared fixtures).
	// Landmark: the h1; primary control: the first page link of « Parcourir » / of the first region.
	{
		id: 'departement',
		chemin: '/departement/44',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return configureSsrSource(page.request, [
				attenteDepartements('/v1/departements/44', { statut: 'ok', donnees: DEPARTEMENT_44, meta: { source: [] } }),
				attenteDepartements('/v1/departements', { statut: 'ok', donnees: DEPARTEMENTS_LISTE })
			]);
		},
		repere: (page) => page.getByRole('heading', { level: 1, name: 'Loire-Atlantique' }),
		interactif: (page) => page.locator('#parcourir ul.liste-liens a').first()
	},
	{
		id: 'departements',
		chemin: '/departements',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return configureSsrSource(page.request, [
				attenteDepartements('/v1/departements', { statut: 'ok', donnees: DEPARTEMENTS_LISTE })
			]);
		},
		repere: (page) => page.getByRole('heading', { level: 1, name: 'Départements' }),
		interactif: (page) => page.locator('section.panneau ul.liste-liens a').first()
	},
	// 04.8-07 (UI-SPEC § 6.10) — the root 404, ported in the v4 form: nothing fetched, the h1 « Page
	// introuvable » as landmark, the large field as its primary control. Appended last: `SURFACES[1]`
	// keeps naming the commune fiche.
	{
		id: 'introuvable',
		chemin: '/route-inconnue-matrice-04-8-07',
		poserMocks: mockAuthAnonyme,
		repere: (page) => page.getByRole('heading', { level: 1, name: 'Page introuvable' }),
		interactif: (page) => page.getByRole('combobox', { name: 'Chercher une adresse ou une commune' })
	}
];

/** 04.8-05: an SSR answer of the departement API, read at most once per load of the matrix. */
function attenteDepartements(pathname: string, body: unknown): SourceExpectation {
	return { method: 'GET', pathname, query: {}, minCalls: 1, maxCalls: 4, responses: Array.from({ length: 4 }, () => ({ status: 200, body })) };
}

const SURFACE_ACCUEIL = SURFACES.find((surface) => surface.id === 'accueil')!;

/** Answer of `/v1/datasets` for the `datasets` surface, generated by the backend (03-11). */
const DATASETS_API = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')
);

/**
 * Frozen harness responses of `/v1/communes/comparaison` (03-09), guarded by the backend schema
 * (`api/backend/tests/test_comparison_fixture_contract.py`).
 */
const COMPARAISONS = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')
) as Record<string, { donnees: { codes_inconnus: string[] } }>;

/** SSR answer of `/v1/communes/comparaison` for the `compare` surface: one call per load. */
function mockComparaison(page: Page, codes: string[], body: unknown) {
	return configureSsrSource(page.request, [
		{
			method: 'GET',
			pathname: '/v1/communes/comparaison',
			query: { codes: codes.join(',') },
			minCalls: 1,
			maxCalls: 1,
			responses: [{ status: 200, body }]
		}
	]);
}

/** SSR answer of `/v1/communes/search` for the `communes` surface: homonyms, one out of coverage. */
function mockRechercheCommunes(page: Page) {
	const commune = (code: string, departement: string, extra: Record<string, unknown>) => ({
		code_insee: code,
		nom: 'Le Pin',
		code_postal: null,
		departement,
		population: 1000,
		population_annee: null,
		population_source: 'geo_communes',
		hebergement: { etat: 'heberge', motif: null, departements: [departement] },
		...extra
	});
	return configureSsrSource(page.request, [
		// The results page reads the addresses of the same query too (04-17): nothing in a hosted BAN.
		{
			method: 'GET',
			pathname: '/v1/adresses/recherche',
			query: { q: 'le pin', limite: '8' },
			minCalls: 1,
			maxCalls: 1,
			responses: [{ status: 200, body: { statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['35'] } } } } }]
		},
		{
			method: 'GET',
			pathname: '/v1/communes/search',
			query: { q: 'le pin', limit: '20', inclure_arrondissements: 'true' },
			minCalls: 1,
			maxCalls: 1,
			responses: [
				{
					status: 200,
					body: {
						statut: 'ok',
						donnees: [
							commune('79211', '79', {
								hebergement: {
									etat: 'hors_couverture',
									motif: 'territoire_hors_perimetre',
									departements: []
								}
							}),
							commune('35999', '35', {
								code_postal: '35170',
								population: 750,
								population_annee: 2022,
								population_source: 'demographie'
							})
						]
					}
				}
			]
		}
	]);
}

// --- Outillage ---------------------------------------------------------------------------------

/** Collects page errors for the whole test — a Svelte effect loop must never pass unnoticed. */
function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(err.message));
	return erreurs;
}

/**
 * Sets the viewport, navigates, waits for the network to settle and for the landmark to be visible.
 * The landmark wait is what makes every measurement below non-vacuous: a page that never rendered
 * fails here, it does not silently report `scrollWidth === 0`.
 *
 * WR-09 — this helper used to take an option aborting the two self-hosted woff2 families. NO test
 * ever passed it, and its doc referenced a « repli de police » block that no longer exists in that
 * form (the font-metrics guard below settled on `document.fonts.ready` + `letter-spacing`). Dead
 * tooling in a GATE file is the worst place to leave any: it tells the next reader that a coverage
 * exists which does not. The rationale for NOT blocking the webfonts is kept where it belongs, in
 * the header of that block. The removed option name is deliberately not written here — the
 * acceptance check of this plan greps the directory for it.
 */
async function ouvrir(page: Page, surface: Surface, largeur: number) {
	await page.setViewportSize({ width: largeur, height: HAUTEUR });
	const source = await surface.poserMocks(page);
	await page.goto(surface.chemin);
	await page.waitForLoadState('networkidle');
	await source?.assert();
	await expect(surface.repere(page)).toBeVisible({ timeout: 15_000 });
	if (surface.id === 'adresse') await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
}

/**
 * Measures the document width and, when it overflows, names the elements responsible.
 *
 * WHY the diagnostic is part of the assertion and not left to an artifact: the CI workflow uploads
 * no Playwright report, so a red `expect(344).toBeLessThanOrEqual(321)` in a runner log carries the
 * SYMPTOM and nothing else — reproducing it cost a full investigation once (fix 50-08). The offender
 * list travels with the failure message instead. It changes nothing when the test is green.
 */
async function mesurerDebordement(page: Page, largeur: number) {
	return page.evaluate((vw) => {
		const document_ = document.documentElement;
		const coupables: string[] = [];
		document.querySelectorAll('*').forEach((el) => {
			const e = el as HTMLElement;
			const b = e.getBoundingClientRect();
			if (b.right > vw + 1) {
				const style = getComputedStyle(e);
				coupables.push(
					`${e.tagName.toLowerCase()}.${(e.className || '').toString().slice(0, 60)} ` +
						`w=${Math.round(b.width)} right=${Math.round(b.right)} ` +
						`flex-shrink=${style.flexShrink} white-space=${style.whiteSpace} ` +
						`| ${(e.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)}`
				);
			}
		});
		return { largeurDocument: document_.scrollWidth, coupables: coupables.slice(0, 8) };
	}, largeur);
}

// ===============================================================================================
// cards-integrales (BUG-02, D-13) — plan 56-01 : aucune card coupée à 375px, sur toutes les surfaces
// ===============================================================================================
//
// The historical card selector below comes from `## Méthode` in
// `56-AUDIT-MOBILE.md` (56-01 Task 2), extended to semantic summary rows in Phase 59.
// It targets whole cards or summary rows (union, not intersection):
//   - toute classe contenant « card » ou « carte » (composants nommés en anglais ou en français) ;
//   - tout `<article>` ;
//   - tout `<li>` contenant un `<h2>`/`<h3>` ;
//   - tout enfant DIRECT d'un conteneur `display: grid` ou `display: flex` portant une bordure
//     calculée non nulle (le filet du registre document tient lieu de « card » ici) ;
//   - tout élément portant la classe EXACTE `kpi` (le bloc KPI de la Fiche Commune / Fiche Bien —
//     PAS `kpi-value`/`kpi-label`/`kpi-grid`, qui ne sont que ses enfants) ;
//   - semantic summary table rows, which replaced those KPI cards in Phase 59.
//
// POURQUOI LE CONTENEUR, ET NON CHAQUE DESCENDANT DIRECTEMENT. Une troncature de texte vit
// presque toujours sur un DESCENDANT de la card (ex. `<p class="line-clamp-1">` dans un `<a>`
// bordée), jamais sur la card elle-même — c'est le constat de l'audit 56-01 (Task 2, AM-01/AM-02).
// Chaque card candidate est donc vérifiée EXTENSIVEMENT : sa propre boîte (scroll vs client) ET
// tout `-webkit-line-clamp` porté par n'importe lequel de ses descendants.
//
// POURQUOI PAS UNE VÉRIFICATION « SCAN COMPLET DU DOM ». Un premier passage de l'audit 56-01 a
// balayé CHAQUE élément du document (comme `mesurerDebordement` le fait pour le débordement
// horizontal) : il remonte trois faux positifs mesurés et compris, tous des non-défauts qu'une
// assertion permanente ne doit PAS bloquer :
//   - le dépassement de 2 à 4px de `scrollHeight` sur un `<h1>` ou un `span.kpi-value` : un artefact
//     de métriques de police (ascent/descent du line-box) SANS AUCUN `overflow: hidden` — rien n'est
//     visuellement coupé, mesuré avec `getComputedStyle(el).overflowY === 'visible'` sur les DEUX ;
//   - les motifs « visuellement masqué » (`sr-only`, WCAG) : un clip 1×1px INTENTIONNEL, pas une
//     troncature — `figcaption.sr-only`, `span.sr-only`, `h2.titre.sr-only` du dépôt ;
//   - les tables à défilement horizontal ASSUMÉES (`table.documents` de l'inventaire,
//     `div.overflow-x-auto` de `/communes`) : `overflow-x: auto` avec barre de défilement visible,
//     tout le contenu reste atteignable — ce n'est pas une troncature au sens de D-13.
// D'où la restriction aux candidats « card » ci-dessus : elle exclut mécaniquement les trois
// familles de faux positifs sans exemption ad hoc, en ne mesurant que ce que la Task 2 qualifie de
// card.
test.describe('cards-integrales (BUG-02, D-13)', () => {
	for (const surface of SURFACES) {
		test(`${surface.id} @ 375px — aucune card tronquée`, async ({ page }) => {
			await ouvrir(page, surface, 375);

			const defauts = await page.evaluate(() => {
				function estVisuellementMasque(el: Element): boolean {
					const e = el as HTMLElement;
					const style = getComputedStyle(e);
					const rect = e.getBoundingClientRect();
					// The responsive table header remains in the accessibility tree through the standard
					// 1x1 clipped pattern. Its intrinsic scroll size is expected and is not visible clipping.
					return style.position === 'absolute'
						&& style.overflow === 'hidden'
						&& (style.clipPath === 'inset(50%)' || style.clip !== 'auto')
						&& rect.width <= 1
						&& rect.height <= 1;
				}

				function estCandidatCard(el: Element): boolean {
					const cls = (el.className || '').toString();
					if (/\b(card|carte)/i.test(cls)) return true;
					if (el.tagName === 'ARTICLE') return true;
					if (el.tagName === 'LI' && el.querySelector('h2, h3')) return true;
					if (el.classList.contains('kpi') || el.matches('.summary-grid .summary-item')) return true;
					const parent = el.parentElement;
					if (parent) {
						const parentDisplay = getComputedStyle(parent).display;
						if (parentDisplay === 'grid' || parentDisplay === 'flex') {
							const s = getComputedStyle(el);
							if (s.borderStyle !== 'none' && s.borderWidth !== '0px') return true;
						}
					}
					return false;
				}

				const problemes: string[] = [];
				document.querySelectorAll('body *').forEach((el) => {
					if (!estCandidatCard(el) || estVisuellementMasque(el)) return;
					const e = el as HTMLElement;
					const nom = `${e.tagName.toLowerCase()}.${(e.className || '').toString().slice(0, 80)}`;
					if (e.scrollHeight > e.clientHeight + 1) {
						problemes.push(
							`${nom} : troncature verticale (scrollHeight=${e.scrollHeight} > clientHeight=${e.clientHeight})`
						);
					}
					if (e.scrollWidth > e.clientWidth + 1) {
						problemes.push(
							`${nom} : troncature horizontale (scrollWidth=${e.scrollWidth} > clientWidth=${e.clientWidth})`
						);
					}
					const clampsPortes = [e, ...Array.from(e.querySelectorAll('*'))]
						.filter((d) => (getComputedStyle(d) as CSSStyleDeclaration).webkitLineClamp !== 'none')
						.map((d) => `${d.tagName.toLowerCase()}.${(d.className || '').toString().slice(0, 60)}`);
					if (clampsPortes.length > 0) {
						problemes.push(`${nom} : line-clamp actif sur ${clampsPortes.join(', ')}`);
					}
				});
				return problemes;
			});

			expect(
				defauts,
				`card(s) tronquée(s) sur ${surface.id} à 375px :\n  ${defauts.join('\n  ')}`
			).toEqual([]);
		});
	}
});

// ===============================================================================================
// D-04 — débordement horizontal, repère de contenu, élément interactif, absence d'erreur de page
// ===============================================================================================

for (const surface of SURFACES) {
	test.describe(`Matrice qualité — ${surface.id} (D-04/D-05)`, () => {
		for (const largeur of LARGEURS) {
			test(`${surface.id} @ ${largeur}px — aucun débordement, repère et interactif visibles`, async ({
				page
			}) => {
				const erreurs = collecterErreurs(page);
				await ouvrir(page, surface, largeur);

				// (1) Débordement horizontal — le signal nommé par D-22. `documentElement` plutôt que
				// `body` : un enfant qui dépasse hors du flux du body n'élargit pas toujours `body`,
				// mais élargit toujours le document.
				const { largeurDocument, coupables } = await mesurerDebordement(page, largeur);
				expect(
					largeurDocument,
					`débordement horizontal sur ${surface.id} à ${largeur}px : le document mesure ${largeurDocument}px\n` +
						`Éléments au-delà de la fenêtre :\n  ${coupables.join('\n  ')}`
				).toBeLessThanOrEqual(largeur + 1); // +1 : tolérance d'arrondi, comme le patron existant

				// (2) L'élément interactif primaire est visible ET a une boîte englobante non nulle
				// (« visible » ne suffit pas : un élément de 0×0 peut passer certaines heuristiques).
				const interactif = surface.interactif(page);
				await expect(interactif).toBeVisible();
				const boite = await interactif.boundingBox();
				expect(boite, `élément interactif sans boîte sur ${surface.id}`).not.toBeNull();
				expect(boite!.width, `largeur nulle de l'élément interactif sur ${surface.id}`).toBeGreaterThan(0);
				expect(boite!.height, `hauteur nulle de l'élément interactif sur ${surface.id}`).toBeGreaterThan(0);

				// (3) Aucune erreur de page — en particulier aucune boucle d'effet Svelte.
				expect(erreurs, `erreurs de page sur ${surface.id} à ${largeur}px`).toEqual([]);
			});
		}
	});
}

// ===============================================================================================
// D-04 (garde de métriques de police) — la mise en page ne doit pas dépendre de la largeur du texte
// ===============================================================================================
//
// POURQUOI CE BLOC EXISTE. Le premier run CI de cette spec a été ROUGE là où Windows était vert :
// document mesuré à 344px dans une fenêtre de 320px, sur les DEUX fiches. Cause racine : le bouton
// « Exporter en PDF (dossier de mandat) » portait `shrink-0`, donc sa largeur valait son max-content
// INDÉPENDAMMENT de son conteneur (288px à 320px de fenêtre) et son libellé de 35 caractères ne
// pouvait jamais passer à la ligne. 267px avec Inter — mais le runner ne rend pas Inter au moment de
// la mesure (`font-display: swap` + cache froid ; `playwright install --with-deps` installe DejaVu
// Sans, ~26 % plus large qu'Inter SemiBold), ce qui donne 328px, donc un document à 344px. Le défaut
// était RÉEL et visible par un visiteur : avant que le webfont ne s'applique, la fiche défilait
// horizontalement.
//
// CE QUI N'A PAS MARCHÉ COMME GARDE, et pourquoi c'est écrit ici : simplement bloquer les woff2 pour
// forcer le repli ne reproduit RIEN sous Windows — le repli local (Arial / Segoe UI) est nettement
// moins large que DejaVu Sans, le document reste à 320px même avec le défaut en place (vérifié).
// Une garde qui ne rougit pas chez le développeur ne sert qu'en CI, trop tard.
//
// CE QUI MARCHE : élargir le texte lui-même, en em, ce qui est indépendant de la police installée.
// `letter-spacing` ajoute une fraction de la taille de police par caractère. Un texte plus large est
// un état utilisateur banal (police de repli, préférence de police du navigateur, taille minimale
// imposée, traduction plus longue), et l'assertion en devient PLUS stricte, jamais plus permissive.
//
// LE PIÈGE DE CALIBRATION, ET POURQUOI LA POLICE DE BASE EST ÉPINGLÉE ICI. Une 2e itération de ce
// bloc a rougi en CI seule (parcelle cadastrale insécable, 330px). Raison : la CI part d'une police
// de BASE plus large (DejaVu) et y ajoute le même `letter-spacing`. En notant W la largeur du texte
// sous Inter et r ≈ 1,26 le rapport DejaVu/Inter :
//     local(k) = W + N·k·fs        CI(k) = r·W + N·k·fs        CI − local = (r−1)·W
// L'écart NE DÉPEND PAS de k : aucun choix de facteur ne le referme, et l'augmenter rend simplement
// la CI plus stricte à son tour. Une garde qui ne peut pas être reproduite localement ne sert à
// rien. La seule issue est donc d'épingler la police de base : on attend `document.fonts.ready`, si
// bien que les DEUX environnements mesurent à partir d'Inter et que seul le facteur fait la
// différence. « N % plus large que la police de conception » redevient alors une grandeur définie.
//
// CE QUE CET ÉPINGLAGE NE MASQUE PAS : l'état « police de repli réellement rendue » reste couvert
// par le bloc D-04 précédent, qui n'attend AUCUNE police — c'est lui, et non cette garde, qui a
// attrapé les 344px du premier run CI. Les deux blocs ont deux rôles distincts : l'un mesure la page
// telle qu'elle est rendue, l'autre mesure sa robustesse au-delà de la police de conception.
//
// D'OÙ VIENT LE FACTEUR — une mesure, pas « le plus grand nombre qui passe ». Sur le libellé du
// bouton d'export, le texte mesure 233px sous Inter et 294px sous DejaVu (la police de repli du
// runner), pour N = 35 caractères à fs = 14px. En résolvant 233 + N·k·fs = 294, la pire
// substitution de police RÉELLEMENT observée vaut k ≈ 0,125em. Le facteur retenu en est le DOUBLE.
// Il ne s'appuie donc pas sur ce que la page supporte : caler un gate sur la falaise le ferait
// rougir au premier ajout de contenu, pour un non-défaut. Marge vérifiée au-dessus du seuil retenu —
// les 5 surfaces passent aussi à 0,3em.
//
// (L'ancienne valeur 0,13em de la 1re itération n'avait aucune base métrique : elle reproduisait la
// largeur observée en CI sans savoir qu'elle s'AJOUTAIT à une base déjà plus large — l'écart de
// police était compté deux fois. C'est ce double comptage, et non un défaut, qui a fait rougir la
// CI seule à la 2e itération.)
//
// Délibérément la seule largeur 320px : c'est là que la marge est la plus mince, et le coût reste
// borné (5 tests, ×3 moteurs).

/** Le double de la pire substitution de police mesurée (DejaVu ≈ Inter + 0,125em). */
const ELARGISSEMENT_TEXTE = '0.25em';

for (const surface of SURFACES) {
	test(`Métriques de police — ${surface.id} @ 320px : aucun débordement avec un texte élargi (D-04)`, async ({
		page
	}) => {
		await ouvrir(page, surface, 320);

		// Épingle la police de base (voir l'en-tête de bloc) : sans cette attente, la CI mesure la
		// police de repli et la garde n'est plus reproductible localement.
		await page.evaluate(() => document.fonts.ready);

		await page.addStyleTag({
			content: `*, *::before, *::after { letter-spacing: ${ELARGISSEMENT_TEXTE} !important; }`
		});

		// Gardes de non-vacuité, deux fois plutôt qu'une :
		//   - la feuille injectée est bien appliquée (sinon on mesurerait le bloc précédent) ;
		//   - plus AUCUN chargement de police n'est en attente, donc aucun échange ne peut survenir
		//     après la mesure : c'est précisément la propriété qui rend la base identique en local
		//     et en CI. On n'exige PAS qu'une graisse précise d'Inter soit chargée : les faces se
		//     chargent à la demande, et la landing n'utilise pas les mêmes que les
		//     fiches — une telle assertion serait fausse pour de mauvaises raisons.
		const etat = await page.evaluate(() => ({
			espacement: getComputedStyle(document.body).letterSpacing,
			statutPolices: document.fonts.status,
			facesDeclarees: document.fonts.size
		}));
		expect(
			etat.espacement,
			"l'élargissement du texte n'est pas appliqué — la garde serait vacuous"
		).not.toBe('normal');
		expect(
			etat.statutPolices,
			'un chargement de police est encore en attente — la base de calibration n’est pas stable'
		).toBe('loaded');
		expect(
			etat.facesDeclarees,
			'aucune @font-face déclarée — la page ne rend pas la typographie de conception'
		).toBeGreaterThan(0);

		const { largeurDocument, coupables } = await mesurerDebordement(page, 320);
		expect(
			largeurDocument,
			`débordement avec un texte élargi (${ELARGISSEMENT_TEXTE}) sur ${surface.id} à 320px : le document mesure ${largeurDocument}px\n` +
				`Un élément non rétrécissable ou insécable ne tient plus. Éléments au-delà :\n  ${coupables.join('\n  ')}`
		).toBeLessThanOrEqual(321);
	});
}

// ===============================================================================================
// D-02 — scan d'accessibilité axe-core, WCAG 2.1 AA, zéro violation serious/critical
// ===============================================================================================

// The four WCAG tags and NOTHING else: axe-core also ships « best-practice » heuristics that are
// not WCAG success criteria, and D-02 is scoped to WCAG 2.1 AA. Widening the tag list would put
// non-normative findings in a blocking gate.
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

for (const surface of SURFACES) {
	test.describe(`Matrice qualité — axe WCAG 2.1 AA — ${surface.id} (D-02)`, () => {
		for (const largeur of LARGEURS_AXE) {
			test(`${surface.id} @ ${largeur}px — aucune violation serious/critical`, async ({ page }) => {
				await ouvrir(page, surface, largeur);

				const resultats = await new AxeBuilder({ page })
					// No per-rule exemption is ever chained here (the axe builder offers one; this spec
					// must not use it). An exemption turns a real defect into a green test. A finding
					// is either fixed at its source or classified in the D-22 register.
					// NB: the name of that opt-out API is deliberately NOT written in this file — the
					// acceptance gate of plan 50-08 greps for it and would go red on a comment.
					.withTags([...TAGS_WCAG])
					.analyze();

				// Filtering by impact BEFORE asserting is deliberate (D-02 blocks on serious/critical
				// only). Passing `results.violations` raw would go red on `minor`/`moderate` findings
				// this phase does not undertake to block — and the first reflex would then be to
				// weaken the scan, which D-22 forbids.
				const bloquantes = resultats.violations.filter(
					(v) => v.impact === 'serious' || v.impact === 'critical'
				);

				// Non-vacuity guard: an axe run that inspected nothing (injection failure, empty page)
				// reports zero violation AND zero passing rule. Without this, a broken scan is green.
				expect(
					resultats.passes.length,
					`aucune règle axe n'a été évaluée sur ${surface.id} — le scan serait vacuous`
				).toBeGreaterThan(0);

				// The violations are serialized in the failure message: a CI log that only says
				// « expected [] » is unactionable.
				expect(
					bloquantes.map((v) => ({
						id: v.id,
						impact: v.impact,
						aide: v.help,
						noeuds: v.nodes.map((n) => ({
							cible: n.target.join(' '),
							// `failureSummary` carries the MEASURED values (contrast ratio, computed
							// colours, …). Without it a CI log names the rule but not the value to fix.
							resume: (n.failureSummary ?? '').replace(/\s+/g, ' ').trim()
						}))
					})),
					`violations axe serious/critical sur ${surface.id} à ${largeur}px`
				).toEqual([]);
			});
		}
	});
}

// ===============================================================================================
// D-08 — réduction d'animations : les durées calculées tombent à zéro effectif
// ===============================================================================================

/**
 * Longest duration of a computed `animation-duration` / `transition-duration`, in ms.
 * The property is a COMMA-SEPARATED LIST (one entry per animated property), so reading only the
 * first entry would miss a slow tail — the maximum is the honest reading.
 */
function dureeMaxMs(valeur: string): number {
	return Math.max(
		...valeur.split(',').map((brut) => {
			const v = brut.trim();
			if (v.endsWith('ms')) return Number.parseFloat(v);
			if (v.endsWith('s')) return Number.parseFloat(v) * 1000;
			return Number.NaN;
		})
	);
}

async function durees(cible: Locator): Promise<{ animation: number; transition: number }> {
	const brut = await cible.evaluate((el) => {
		const s = getComputedStyle(el);
		return { animation: s.animationDuration, transition: s.transitionDuration };
	});
	return { animation: dureeMaxMs(brut.animation), transition: dureeMaxMs(brut.transition) };
}

// 04-08: the only target of this block was the « Exporter en PDF (dossier de mandat) » button of
// the Fiche Bien, gone with `/bien` (D-101) and now a forbidden text. The address and document pages
// carry no transitioning control; the global `prefers-reduced-motion` rule of app.css stays proven
// on real controls by the accueil, communes and compare tests at the end of this file.

// ===============================================================================================
// D-15 — rendu d'impression des DEUX fiches (le DOM asserté est celui livré par le plan 50-07)
// ===============================================================================================

// Verbatim copy of the Copywriting Contract (50-UI-SPEC) — asserted to the character, because the
// whole point of the mention is that it is the same sentence on every printed document.
const MENTION_PRINT = 'Synthèse informative — ne remplace pas les diagnostics officiels';
const URL_ERRIAL = 'https://errial.georisques.gouv.fr';
const URL_GEORISQUES = 'https://georisques.gouv.fr';

// The commune fiche (the home page and `/communes` have their own print tests at the end of this
// file; the address page prints under adresse-lieu.spec.ts « impression »).
const FICHES_IMPRIMABLES = [SURFACES[1]] as const;

/**
 * A control that must NOT reach the printed document (CR-01).
 *
 * WHY THIS LIST EXISTS. Until it did, the print contract asserted PRESENCES only (dated header,
 * qualification, renvoi, sources table) plus a single absence, the site banner. A contract that
 * never states its exclusions proves nothing about the document actually produced: the marketing
 * footer printed as ~350px of solid `bg-column` ink — the `print-color-adjust: exact` rule of
 * app.css guarantees the fill is preserved — followed by 4 columns of site navigation, and the 5
 * per-theme CSV buttons, the « Exporter tout (CSV) » bar, the INSEE clipboard pill and every ℹ️
 * trigger printed with it, all under a green gate.
 *
 * `selecteur` is a CSS selector rather than a role locator on purpose: Playwright's role engine
 * skips elements that are out of the accessibility tree, so `getByRole(...)` would report zero
 * match for a control that is `display: none` AND for a control whose markup no longer exists —
 * the two outcomes this block must tell apart.
 */
interface ControleNonImprimable {
	/** Named in the failure message: a bare « expected 0, got 1 » is unactionable. */
	nom: string;
	selecteur: string;
}

const CONTROLES_COMMUNS: readonly ControleNonImprimable[] = [
	{ nom: 'pied de page marketing', selecteur: 'footer' }
];

const CONTROLES_PAR_FICHE: Record<string, readonly ControleNonImprimable[]> = {
	commune: [
		...CONTROLES_COMMUNS,
		{
			nom: 'pastille Copier du code INSEE',
			// 03-17 F9: an inline text button, named by its text (no aria-label any more).
			// 04.8-04: the identity line is the subtitle `.fiche-sous` (UI-SPEC v4 § 6.2).
			selecteur: '.fiche-sous button.fiche-copy'
		},
		{ nom: 'bouton Télécharger (CSV)', selecteur: 'button:has-text("Télécharger (CSV)")' },
		{ nom: 'bouton i du volet source', selecteur: 'button.source-button' },
		{ nom: 'actions de la fiche', selecteur: '.fiche-actions' },
		// 04.8-04 (UI-SPEC v4 § 6.2): the themes are open panels, their chevron is gone; the link to the
		// limits of the fiche is the screen-only control that takes its place in this list.
		{ nom: 'lien des limites de la fiche', selecteur: '.lien-limites' }
	]
};

for (const surface of FICHES_IMPRIMABLES) {
	test(`Impression — ${surface.id} : en-tête daté, mention, renvoi, sources, chrome et exports masqués (D-15)`, async ({
		page
	}) => {
		await ouvrir(page, surface, 1440);

		// The three categories of the commune fiche are open cards above 1080 px (4.9), and printing opens the
		// questions (beforeprint): no row is left behind a closed summary on paper. Emulation AFTER the
		// goto (same defensive order as D-08 above).
		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		if (surface.id === 'commune') {
			const themes = page.locator('details.cat:is(#q-prix, #q-logement, #q-quotidien)');
			await expect(themes).toHaveCount(3);
			for (const theme of await themes.all()) {
				await expect(theme.locator(':scope > summary > .cat-titre')).toBeVisible();
				await expect(theme.locator('dl.tab > .tab-l').first()).toBeVisible();
			}
			const questions = page.locator('[data-faq] details');
			for (const question of await questions.all()) await expect(question).toHaveAttribute('open', '');
		}

		const enTete = page.locator('.bloc-impression').first();
		await expect(enTete).toBeVisible();

		// (1) Date de consultation au format JJ/MM/AAAA — jamais un millésime, jamais « null ».
		await expect(enTete.locator('.ligne.date')).toBeVisible();
		await expect(enTete.locator('.ligne.date')).toHaveText(/^Consulté le \d{2}\/\d{2}\/\d{4}$/);

		// (2) Mention de qualification, au caractère près.
		await expect(enTete.locator('.ligne.mention')).toHaveText(MENTION_PRINT);

		// (3) Les deux URL de renvoi, en texte clair (aucun `href` : un tirage papier ne suit
		// pas un lien — le renvoi utilisable EST l'URL écrite).
		const renvoi = enTete.locator('.ligne.renvoi');
		await expect(renvoi).toBeVisible();
		await expect(renvoi).toContainText(URL_ERRIAL);
		await expect(renvoi).toContainText(URL_GEORISQUES);

		// (4) Tableau final Donnée / Source / Millésime, alimenté par la provenance live.
		const sources = page.locator('table.sources');
		await expect(sources).toBeVisible();
		await expect(sources.locator('tbody tr').first()).toBeVisible();

		// (5) L'en-tête de SITE disparaît. `<header>` racine du layout = landmark `banner` ; les
		// `<header>` des fiches vivent dans `<main>` et n'en portent pas le rôle, donc ce locator
		// ne désigne que le chrome du site. Assertion par VISIBILITÉ et non par texte : un bloc en
		// `display:none` garde son `textContent` (constat nº3 du plan 50-07).
		await expect(page.getByRole('banner')).toBeHidden();

		// Le titre de la fiche, lui, s'imprime : c'est LE défaut fermé au plan 50-07 (la règle
		// `@media print` masquait tout élément `header`, donc aussi celui qui porte le `<h1>`).
		// L'asserter ici est la non-régression la plus utile du contrat d'impression.
		await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

		// (6) AUCUN chrome de site ni affordance d'export ne figure sur le document (CR-01).
		// Chaque contrôle est asserté DEUX FOIS, et l'ordre des deux assertions est le fond du
		// sujet :
		//   (a) il EXISTE dans le DOM sous `media: print` — un sélecteur périmé (classe renommée,
		//       bouton déplacé) rendrait sinon l'assertion d'absence vacuous, c'est-à-dire verte
		//       pour la mauvaise raison, exactement le défaut que ce bloc ferme ;
		//   (b) AUCUNE de ses instances n'est visible — `:visible` lit la boîte réellement calculée
		//       sous l'émulation, donc `display: none` y compte comme absent.
		// `toBeHidden()` ne convient pas : ces sélecteurs désignent plusieurs éléments (5 boutons
		// CSV, une dizaine de déclencheurs) et le mode strict de Playwright refuserait le locator.
		for (const { nom, selecteur } of CONTROLES_PAR_FICHE[surface.id]) {
			await expect(
				page.locator(selecteur),
				`${nom} : introuvable dans le DOM de ${surface.id} — sélecteur périmé, la garde d'absence serait vacuous`
			).not.toHaveCount(0);
			await expect(
				page.locator(`${selecteur}:visible`),
				`${nom} s'imprime sur ${surface.id} : le document porte du chrome de site ou une affordance inutilisable sur papier (CR-01)`
			).toHaveCount(0);
		}
	});
}

// ===============================================================================================
// SC 1, clause « au clavier » — TROIS points nommés, ni plus (UI-01)
// ===============================================================================================
//
// POURQUOI SEULEMENT TROIS. La vérification de phase a relevé que cette clause n'avait AUCUNE
// assertion e2e : la couverture clavier n'existait qu'au niveau unitaire, sur deux composants. Ce
// bloc lui donne sa première preuve rejouable — il ne prétend pas automatiser l'accessibilité au
// clavier. L'ordre de tabulation COMPLET d'une surface, le nom et le rôle annoncés de chaque
// contrôle restent le bloc A de `50-NVDA-CHECKLIST.md`, exécuté par un humain (D-03).
//
// PORTÉE MOTEUR — CE BLOC NE TOURNE PAS SUR WEBKIT, et le motif compte autant que l'exclusion.
// Le build WebKit de Playwright ne reproduit pas la politique de tabulation par défaut de Safari
// (macOS ne met les LIENS dans l'ordre de tabulation qu'avec « Navigation au clavier » activée,
// réglage système que le build de test ne porte pas). MESURÉ, sans exclusion : 6 des 7 tests
// rougissent sur webkit — les 5 arrêts de tabulation et le lien de source du panneau ℹ️, tous des
// `<a>`. Un vert n'y prouverait rien sur Safari et ces rouges sont des artefacts de build, pas des
// défauts. Cette clause relève du SC 1 (UI-01) ; la clause cross-moteur, elle, est le SC 2 (UI-02)
// et reste couverte par tous les autres blocs de ce fichier, qui tournent bien ×3.
//
// OÙ L'EXCLUSION EST POSÉE, et pourquoi PAS ici. La forme naturelle serait un saut conditionnel sur
// `browserName` en tête de test ; l'API qui le fait est INTERDITE dans une spec `critical` par
// `scripts/check-e2e-manifest.mjs` (son scan est textuel et ne distingue pas un saut conditionnel
// motivé d'un test désactivé — et affaiblir un gate pour faire passer son propre code est
// exactement ce que D-22 refuse). L'exclusion est donc déclarée dans `playwright.config.ts`, sur le
// projet `webkit`, par un `grepInvert` sur le préfixe « SC 1 clavier » — déterministe, visible dans
// la configuration des projets, et motivée là-bas comme ici. Le préfixe des trois titres est de ce
// fait LOAD-BEARING : le renommer sans toucher la configuration remettrait le bloc sur webkit.

for (const surface of SURFACES) {
	test(`SC 1 clavier — ${surface.id} : le lien d'évitement est le premier arrêt de tabulation`, async ({
		page
	}) => {
		await ouvrir(page, surface, 1440);

		// Garde de non-vacuité : le lien doit EXISTER avant la frappe. Sans elle, sa disparition
		// rendrait l'assertion sans objet au lieu de la faire rougir.
		const evitement = page.locator('a.skip-link');
		await expect(
			evitement,
			`lien d'évitement absent de ${surface.id} — l'assertion de premier arrêt serait vacuous`
		).toHaveCount(1);
		await expect(evitement).toHaveAttribute('href', '#main-content');

		// Depuis la page fraîchement chargée, le focus est sur le document : la 1re tabulation doit
		// atteindre le lien d'évitement, avant tout autre contrôle.
		await page.keyboard.press('Tab');
		await expect(
			evitement,
			`le premier arrêt de tabulation de ${surface.id} n'est pas le lien d'évitement`
		).toBeFocused();
	});
}

test(`SC 1 clavier — accueil : le lien d'évitement mène au champ de recherche en une tabulation`, async ({
	page
}) => {
	await ouvrir(page, SURFACE_ACCUEIL, 1440);
	const evitement = page.locator('a.skip-link');
	await expect(evitement, "lien d'évitement absent : l'assertion serait vacuous").toHaveCount(1);

	await page.keyboard.press('Tab');
	await expect(evitement).toBeFocused();
	// Activating the skip link moves the sequential focus start to <main>: the next stop is the
	// first control of the screen, its anchor — the search field, before the filter and the button.
	await page.keyboard.press('Enter');
	await page.keyboard.press('Tab');
	// 04.8-07 (UI-SPEC § 3.13, § 6.6): the large field of the v4 home.
	await expect(page.getByRole('combobox', { name: 'Adresse, commune ou code postal' })).toBeFocused();
});

test(`SC 1 clavier — commune : Échap referme le panneau ℹ️ et rend le focus au déclencheur`, async ({
	page
}) => {
	await ouvrir(page, SURFACES[1], 1440);

	// 04.8-04 (UI-SPEC v4 § 6.2): the rows sit in open categories (4.9) and the questions are details. The
	// keyboard alone opens a question (Enter on its summary) and closes it (Space), then the source
	// panel of a row (Enter on its Source button).
	const question = page.locator('[data-faq] details').first();
	await expect(question).not.toHaveAttribute('open');
	await question.locator(':scope > summary').focus();
	await page.keyboard.press('Enter');
	await expect(question).toHaveAttribute('open', '');
	await page.keyboard.press('Space');
	await expect(question).not.toHaveAttribute('open');

	const theme = page.locator('#critere-prix_commune');
	const declencheur = theme.locator('dl.tab .source-button').first();
	await expect(declencheur).toBeVisible();
	await declencheur.focus();
	await expect(declencheur).toBeFocused();
	await page.keyboard.press('Enter');

	const panneau = page.locator('dialog.source-panel[open]');
	await expect(panneau).toBeVisible();

	// LE FOCUS DOIT D'ABORD ENTRER DANS LE PANNEAU, sinon l'assertion finale ne prouve RIEN : le
	// volet donne le focus à son titre, puis la tabulation atteint son premier contrôle.
	const fermer = panneau.getByRole('button', { name: 'Fermer', exact: true });
	await page.keyboard.press('Tab');
	await expect(fermer, "la tabulation n'est pas entrée dans le panneau").toBeFocused();
	await expect(
		declencheur,
		'le focus n’a pas quitté le déclencheur — l’assertion de retour de focus serait vacuous'
	).not.toBeFocused();

	await page.keyboard.press('Escape');

	// Les deux moitiés : le panneau disparaît ET le focus revient sur le déclencheur (SC 1, aucun
	// piège de focus).
	await expect(panneau).toHaveCount(0);
	await expect(
		declencheur,
		'Échap a refermé le panneau sans rendre le focus au déclencheur — le focus est perdu'
	).toBeFocused();
});

test(`SC 1 clavier — adresse : le lien « DPE n° … » ouvre le document au clavier`, async ({ page }) => {
	const erreurs = collecterErreurs(page);
	await page.setViewportSize({ width: 1440, height: HAUTEUR });
	await mockAuthAnonyme(page);
	const source = await configureSsrSource(page.request, [...sourceAdresse(), attenteDocument(1)]);
	await page.goto(`/adresse/${BAN_ID}`);
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('heading', { level: 1, name: '12 rue des Lilas', exact: true })).toBeVisible();
	await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');

	const action = page.getByRole('link', { name: new RegExp(`^DPE n°.${NUMERO}$`) });
	await expect(action, "lien documentaire absent — l'assertion clavier serait vacuous").toHaveCount(1);
	await action.focus();
	await expect(action, "le lien documentaire n'est pas focalisable").toBeFocused();
	await page.keyboard.press('Enter');

	await expect(page).toHaveURL(new RegExp(`/dpe/${NUMERO}$`));
	// 04.8-04 (UI-SPEC v4 § 6.7): the served number is under the h1.
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(page.locator('.document-sous .document-numero')).toContainText(NUMERO);
	await source.assert();
	expect(erreurs, "erreurs de page pendant l'ouverture clavier du document").toEqual([]);
});

test('fiche adresse — les flèches des listes de vigilances sont rendues et décoratives dans chaque moteur', async ({ page }) => {
	const fixture = JSON.parse(readFileSync(new URL('./fixtures/rapport-43-themes.json', import.meta.url), 'utf8')) as {
		rapport: { donnees: { themes: unknown[]; resume: Record<string, unknown> } };
	};
	const attentes = sourceAdresse(null, 1, undefined, fixture.rapport.donnees.themes);
	const rapport = attentes.find((a) => a.pathname.endsWith('/rapport'))!;
	for (const reponse of rapport.responses) {
		(reponse.body as { donnees: { resume: unknown } }).donnees.resume = fixture.rapport.donnees.resume;
	}
	await mockAuthAnonyme(page);
	const source = await configureSsrSource(page.request, attentes);
	await page.goto(`/adresse/${BAN_ID}`);
	await expect(page.getByRole('heading', { level: 1, name: '12 rue des Lilas', exact: true })).toBeVisible();
	await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	await expect(page.locator('li[id^="critere-"]')).toHaveCount(43);
	// The list of the commune is in « Risques »: its arrows are held to the same rule once opened.
	await page.locator('details#q-risques > summary').click();
	const liens = page.locator('.bref-l a');
	const fleches = liens.locator('.fleche');
	expect(await liens.count()).toBeGreaterThan(0);
	await expect(fleches).toHaveCount(await liens.count());
	for (const fleche of await fleches.all()) {
		await expect(fleche).toBeVisible();
		await expect(fleche).toHaveText('↓');
		await expect(fleche).toHaveAttribute('aria-hidden', 'true');
	}
	await source.assert();
});

// ===============================================================================================
// Plan 03-05 — `/` : impression et réduction d'animations de l'accueil
// ===============================================================================================

test(`Impression — accueil : le titre et les deux phrases restent, le champ et le chrome disparaissent`, async ({
	page
}) => {
	await ouvrir(page, SURFACE_ACCUEIL, 1440);
	await page.emulateMedia({ media: 'print' });

	// 04.8-07 (UI-SPEC § 6.6): the v4 h1 and its chapô stand for the former title and sentences.
	await expect(
		page.getByRole('heading', { level: 1, name: 'Une adresse, en clair' })
	).toBeVisible();
	await expect(
		page.getByText(/^Chaque chiffre vient d’une source publique/)
	).toBeVisible();

	// Same rule as `/communes`: CSS selectors, each control must exist AND be hidden.
	for (const [nom, selecteur] of [
		['champ de recherche', 'form[role="search"]'],
		['en-tête du site', 'header.site-header'],
		['pied du site', 'footer.site-footer']
	] as const) {
		const controles = page.locator(selecteur);
		expect(await controles.count(), `${nom} absent : l'assertion serait vacuous`).toBeGreaterThan(0);
		for (const controle of await controles.all()) {
			await expect(controle, `${nom} imprimé`).toBeHidden();
		}
	}
});

test(`Réduction d'animations — accueil : la règle atteint les contrôles de la page (D-08)`, async ({
	page
}) => {
	// No transition is planned on this page: what is locked is that no control escapes the global
	// rule — the computed durations fall strictly between 0 and 0.01ms, a value only that rule
	// produces.
	await ouvrir(page, SURFACE_ACCUEIL, 1440);
	await page.emulateMedia({ reducedMotion: 'reduce' });
	// 04.8-07 (UI-SPEC § 3.13): the large field and its plain button « Rechercher ».
	for (const cible of [
		page.getByRole('combobox', { name: 'Adresse, commune ou code postal' }),
		page.getByRole('search', { name: 'Rechercher un lieu' }).getByRole('button', { name: 'Rechercher', exact: true }),
		page.getByRole('link', { name: 'Voir la couverture des données' })
	]) {
		await expect(cible).toBeVisible();
		const apres = await durees(cible);
		expect(apres.transition).toBeLessThanOrEqual(0.01);
		expect(apres.transition).toBeGreaterThan(0);
		expect(apres.animation).toBeLessThanOrEqual(0.01);
		expect(apres.animation).toBeGreaterThan(0);
	}
});

// ===============================================================================================
// Plan 03-04 — `/communes` : impression et réduction d'animations de la page de résultats
// ===============================================================================================

const SURFACE_COMMUNES = SURFACES.find((surface) => surface.id === 'communes')!;

test(`Impression — communes : les cartes restent, le champ, les actions et le chrome disparaissent`, async ({
	page
}) => {
	await ouvrir(page, SURFACE_COMMUNES, 1440);
	await page.emulateMedia({ media: 'print' });

	// Kept on paper: the facts of each card, badge and reason of the commune out of coverage.
	// 04.8-07 (UI-SPEC § 6.5): one h2 for the panel, each commune a row whose link carries its facts
	// (name and served line), so the row link is kept on paper; « Ouvrir la fiche » is that same link.
	await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Communes']);
	await expect(page.locator('li.resultat a.resultat-lien .lien-t')).toHaveText(['Le Pin', 'Le Pin']);
	// Each row carries its served line: as many as the rows, so the loop below cannot run empty
	// (04.8-07 Codex pass 2, P3).
	const lignesServies = page.locator('li.resultat a.resultat-lien .lien-n');
	await expect(lignesServies).toHaveCount(2);
	for (const ligne of await lignesServies.all()) await expect(ligne).toBeVisible();
	await expect(page.getByText('Hors couverture · département non hébergé')).toBeVisible();
	// The comparison control is mounted once hydrated: wait for it before counting it below.
	await expect(page.locator('li.resultat .ajout button').first()).toBeAttached();

	// Screen controls: the selector is CSS on purpose (a role locator skips hidden nodes and would
	// not tell a hidden control from a missing one); each one must exist AND be hidden.
	for (const [nom, selecteur] of [
		['champ de recherche', 'form[role="search"]'],
		['action « Ajouter à la comparaison »', 'li.resultat .ajout button'],
		['en-tête du site', 'header.site-header'],
		['pied du site', 'footer.site-footer']
	] as const) {
		const controles = page.locator(selecteur);
		expect(await controles.count(), `${nom} absent : l'assertion serait vacuous`).toBeGreaterThan(0);
		for (const controle of await controles.all()) {
			await expect(controle, `${nom} imprimé`).toBeHidden();
		}
	}
});

test(`Réduction d'animations — communes : la règle atteint les contrôles de la page (D-08)`, async ({
	page
}) => {
	// The UI-SPEC plans no transition on this page (the only one is the source panel): there is no
	// baseline duration to reduce. What is locked is that no control escapes the global rule: the
	// computed durations fall strictly between 0 and 0.01ms, a value only that rule produces.
	await ouvrir(page, SURFACE_COMMUNES, 1440);
	await page.emulateMedia({ reducedMotion: 'reduce' });
	// 04.8-07 (UI-SPEC § 6.5): the large field; the row link replaces « Ouvrir la fiche ».
	for (const cible of [
		page.getByRole('combobox', { name: 'Adresse, commune ou code postal' }),
		page.getByRole('button', { name: /^Ajouter à la comparaison/ }).first(),
		page.locator('li.resultat a.resultat-lien').first()
	]) {
		await expect(cible).toBeVisible();
		const apres = await durees(cible);
		expect(apres.transition).toBeLessThanOrEqual(0.01);
		expect(apres.transition).toBeGreaterThan(0);
		expect(apres.animation).toBeLessThanOrEqual(0.01);
		expect(apres.animation).toBeGreaterThan(0);
	}
});

// ===============================================================================================
// Plan 03-10 — `/comparer` : région de défilement au clavier, impression, réduction d'animations
// ===============================================================================================

const SURFACE_COMPARE = SURFACES.find((surface) => surface.id === 'compare')!;

/** Three communes, one out of coverage: at 375 px they need the scroll region. */
function surfaceTroisColonnes(): Surface {
	const reponse = structuredClone(COMPARAISONS.trois_communes_dont_hors_couverture);
	// The frozen response also reports an unknown code; this surface asks for three known ones.
	reponse.donnees.codes_inconnus = [];
	return {
		...SURFACE_COMPARE,
		chemin: '/comparer?communes=44991,44992,85991',
		poserMocks: async (page) => {
			await mockAuthAnonyme(page);
			return mockComparaison(page, ['44991', '44992', '85991'], reponse);
		}
	};
}

// Not an « SC 1 clavier » test: no tab order is asserted, only that the focused region scrolls
// with the arrow keys, so it runs on the three engines.
test(`Région de défilement — compare @ 375px : focalisable, défile aux flèches, seule à défiler`, async ({
	page
}) => {
	const erreurs = collecterErreurs(page);
	await ouvrir(page, surfaceTroisColonnes(), 375);
	const region = SURFACE_COMPARE.repere(page);
	expect(
		await region.evaluate((element) => element.scrollWidth > element.clientWidth),
		'trois colonnes doivent déborder de la région à 375px — le test serait vacuous'
	).toBe(true);
	const { largeurDocument, coupables } = await mesurerDebordement(page, 375);
	expect(largeurDocument, `la page défile à 375px : ${coupables.join(' | ')}`).toBeLessThanOrEqual(376);

	await region.focus();
	await expect(region).toBeFocused();
	await page.keyboard.press('ArrowRight');
	await page.keyboard.press('ArrowRight');
	await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
	expect(erreurs).toEqual([]);
});

test(`Impression — compare : le tableau entier reste, sans défilement ni en-têtes collants, contrôles masqués`, async ({
	page
}) => {
	await ouvrir(page, surfaceTroisColonnes(), 1440);
	await page.emulateMedia({ media: 'print' });

	const region = SURFACE_COMPARE.repere(page);
	await expect(region).toBeVisible();
	expect(await region.evaluate((element) => getComputedStyle(element).overflowX)).toBe('visible');
	expect(await page.locator('thead th').first().evaluate((element) => getComputedStyle(element).position)).toBe('static');
	await expect(page.locator('thead th[data-code="85991"]')).toContainText('Hors couverture · département non hébergé');

	for (const [nom, selecteur] of [
		['contrôles du comparateur', '.comparer-controles'],
		['actions de colonne', '.compare-actions'],
		['boutons de source', '.source-button'],
		['en-tête du site', 'header.site-header'],
		['pied du site', 'footer.site-footer']
	] as const) {
		const controles = page.locator(selecteur);
		expect(await controles.count(), `${nom} absent : l'assertion serait vacuous`).toBeGreaterThan(0);
		for (const controle of await controles.all()) {
			await expect(controle, `${nom} imprimé`).toBeHidden();
		}
	}
});

test(`Réduction d'animations — compare : la règle atteint les contrôles de la page (D-08)`, async ({
	page
}) => {
	// No transition is planned on this page (the only one is the source panel): what is locked is
	// that no control escapes the global rule.
	await ouvrir(page, SURFACE_COMPARE, 1440);
	await page.emulateMedia({ reducedMotion: 'reduce' });
	// 04.5-09 (B5): « Ajouter une commune » lives in the folded « Modifier la sélection ».
	await page.locator('details.comparer-modifier > summary').click();
	for (const cible of [
		page.getByRole('button', { name: 'Ajouter une commune' }),
		page.getByRole('button', { name: 'Copier le lien' }),
		page.getByRole('button', { name: 'Retirer Courante' })
	]) {
		await expect(cible).toBeVisible();
		const apres = await durees(cible);
		expect(apres.transition).toBeLessThanOrEqual(0.01);
		expect(apres.transition).toBeGreaterThan(0);
		expect(apres.animation).toBeLessThanOrEqual(0.01);
		expect(apres.animation).toBeGreaterThan(0);
	}
});
