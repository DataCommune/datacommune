import AxeBuilder from '@axe-core/playwright';
import { getExplication, type CoveredFieldId } from '../../src/lib/data/explications';
import { test, expect, type Locator, type Page } from '@playwright/test';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import {
	ANCIENNE,
	CLE_PRIX_MAISONS,
	COURANTE,
	DEUX,
	FICHE_LE_PIN_35,
	HORS_COUVERTURE,
	LE_PIN_35,
	lecture,
	LE_PIN_79,
	TROIS,
	commune,
	comparaison,
	configurer,
	contratTenu,
	datasets,
	ficheComparee,
	recherche,
	referentiel,
	resultat,
	type Attente
} from './fixtures/recette-communes';
import { reponseHorsCouverture } from './fixtures/commune-indicators';
import { lireTheme } from './fixtures/themes';

// Acceptance replay of phase 3 (D-53, UI-01, COM-01..03): the four written tasks of
// 03-RECETTE.md (internal tracking), played headless at 375, 768 and
// 1280 px on the synthetic source. Same tasks, same success criteria as the written ones; this is
// a local synthetic proof, never Ianis's acceptance (D-36) and never real data (03-13).
//
// Then the UI-01 invariants on every screen of the phase at each width: no horizontal overflow of
// the document, no truncated box, no `title` on an interactive element, no `position: fixed` but
// the skip link and `<dialog>` (the header is sticky), horizontal scrolling only inside the
// comparator region (and the curl line of /developpeurs, kept scrolling on purpose by 03-02), no
// CSS reordering, axe WCAG 2.1 AA without violation; on the fiche and the comparator the tab order
// is the DOM order.
//
// Every read goes to the synthetic source under an exact contract (fail-closed journal checked
// after each test) and no browser request leaves the loopback environment.

const LARGEURS = [375, 768, 1280] as const;
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const REGION = 'Tableau de comparaison';

// Public definition of a field, read from `EXPLICATIONS` as the source panel renders it (03-17 F66),
// never the technical description of the contract.
const definition = (fieldId: CoveredFieldId) => getExplication(fieldId).definition;

/** Height of the site header, as rendered. */
async function hauteurEnTete(page: Page): Promise<number> {
	const box = await page.locator('header.site-header').boundingBox();
	expect(box, 'en-tête mesuré').not.toBeNull();
	return box!.height;
}

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function ouvrir(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
	// C2: the categories of a comparison of communes are folded on arrival. The cases of this spec read
	// their rows: they are unfolded here, as a reader would open them; the fold itself is locked by
	// `comparateur-revue.spec.ts`. No fold exists for the addresses and the DPE documents.
	await page.locator('details.compare-groupe-pli').evaluateAll((plis) => plis.forEach((pli) => ((pli as HTMLDetailsElement).open = true)));
}

// The comparator of communes keeps its communes-only field; `/` has the single field (04-17).
const champ = (page: Page) => page.getByRole('combobox', { name: 'Rechercher une commune' });
const propositions = (page: Page) => page.getByRole('listbox', { name: 'Communes proposées' }).getByRole('option');
// 04.8-07 (UI-SPEC § 3.13, § 6.6): the home carries the large field.
const champUnique = (page: Page) => page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
const propositionsLieux = (page: Page) => page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
/** The address search of the single field, run with each communes search: nothing in a hosted BAN. */
const adressesSansResultat = (q: string, limite = '8') =>
	lecture(
		'/v1/adresses/recherche',
		{ q, limite },
		{
			statut: 'ok',
			donnees: {
				ordre_types: ['commune', 'adresse'],
				resultats: [],
				hebergement: { ban: { etat: 'heberge', motif: null, departements: ['35'] } }
			}
		}
	);
const region = (page: Page) => page.getByRole('region', { name: REGION });
const norm = (value: string | null | undefined) => value?.replace(/\s+/g, ' ').trim() ?? null;
const entetes = (page: Page) =>
	page.locator('thead th[data-code]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-code')!));
const ecart = (page: Page, cle: string, code: string) =>
	page.locator(`tr[data-cle="${cle}"] td[data-code="${code}"] .compare-ecart`);

/**
 * The comparator on a phone (03-18, R-2): the page scrolls, the region only horizontally, criteria
 * stick left, the column names follow in the duplicated band, aligned on the real `th`; the column
 * header row takes a fifth of the window at most at 375 px and its actions are 44 × 44 px targets.
 */
async function comparateurSansCadre(page: Page, largeur: number, colonne: string) {
	const zone = region(page);
	const mesures = await zone.evaluate(async (element) => {
		const horizontal = element.scrollWidth > element.clientWidth;
		const vertical = element.scrollHeight > element.clientHeight + 1;
		element.scrollLeft = element.scrollWidth;
		await new Promise((fin) => requestAnimationFrame(() => requestAnimationFrame(fin)));
		const zoneBox = element.getBoundingClientRect();
		const critere = element.querySelector('tbody th[scope="row"]')!.getBoundingClientRect();
		element.scrollLeft = 0;
		return { horizontal, vertical, critere: critere.left - zoneBox.left, maxHeight: getComputedStyle(element).maxHeight };
	});
	expect(mesures.vertical, JSON.stringify(mesures)).toBe(false);
	expect(mesures.maxHeight, JSON.stringify(mesures)).toBe('none');
	if (largeur === 375) expect(mesures.horizontal, JSON.stringify(mesures)).toBe(true);
	expect(Math.abs(mesures.critere), JSON.stringify(mesures)).toBeLessThan(2);
	const annonce = page.locator('.compare-defilement');
	if (mesures.horizontal) await expect(annonce).toBeVisible();
	else await expect(annonce).toBeHidden();

	const ligneEntete = (await page.locator('thead tr').boundingBox())!.height;
	const plafond = await page.evaluate(() => window.innerHeight / 5);
	const cibles: string[] = [];
	for (const bouton of await page.locator('thead .compare-action').all()) {
		const boite = (await bouton.boundingBox())!;
		cibles.push(`${Math.round(boite.width)}x${Math.round(boite.height)}`);
		const nom = (await bouton.getAttribute('aria-label')) ?? 'action de colonne';
		expect(boite.width, nom).toBeGreaterThanOrEqual(44);
		expect(boite.height, nom).toBeGreaterThanOrEqual(44);
	}

	// Past the real header, the band shows under the site header, on its `th`, without moving the table.
	const haut = () => page.evaluate(() => document.querySelector('table.compare-table')!.getBoundingClientRect().top + window.scrollY);
	const avant = await haut();
	await page.evaluate(() => window.scrollTo(0, window.scrollY + document.querySelector('thead')!.getBoundingClientRect().bottom + 120));
	const bande = page.locator('.compare-bande');
	await expect(bande).toHaveCSS('visibility', 'visible');
	await expect(bande).toHaveAttribute('aria-hidden', 'true');
	expect(Math.abs((await haut()) - avant), 'the band pushes nothing').toBeLessThanOrEqual(1);
	// What sticks while reading takes a fifth of the window at most (R-2; option (a) of 2026-09-29).
	const hauteurBande = (await bande.boundingBox())!.height;
	if (largeur === 375) expect(hauteurBande, `bande ${hauteurBande}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
	const aligne = await page.evaluate((code) => {
		const cellule = document.querySelector(`.compare-bande-cellule[data-code="${code}"]`)!.getBoundingClientRect();
		const th = document.querySelector(`thead th[data-code="${code}"]`)!.getBoundingClientRect();
		const haut = document.querySelector('.compare-bande')!.getBoundingClientRect().top;
		const bas = Math.max(0, document.querySelector('header.site-header')!.getBoundingClientRect().bottom); // 0 once the public header, not sticky since 04.8-02 (A-01), has scrolled away
		return { gauche: cellule.left - th.left, largeur: cellule.width - th.width, haut: haut - bas };
	}, colonne);
	for (const ecartPx of Object.values(aligne)) expect(Math.abs(ecartPx), JSON.stringify(aligne)).toBeLessThanOrEqual(2);
	console.log(
		`RELEVE synthetique ${largeur}px entete_colonnes=${ligneEntete} plafond=${plafond} bande=visible ` +
			`hauteur_bande=${hauteurBande} defilement_vertical=${mesures.vertical} cibles=${cibles.join(',')}`
	);
	await page.evaluate(() => window.scrollTo(0, 0));
	// The real header row scrolls with the page. Its only caller compares a column out of coverage:
	// measured and recorded above (Ianis, 2026-09-29, option (a)); the fifth-of-the-window cap with
	// two hosted communes is asserted in task 3 (D-15 IN-07).
	// Non-regression guard, not the R-2 criterion (D-15 WR-03): with a column out of coverage the real
	// header is not capped at a fifth, but it is not left unbounded either: two fifths of the window.
	if (largeur === 375) expect(ligneEntete, `garde de non-régression : thead ${ligneEntete}px > ${2 * plafond}px`).toBeLessThanOrEqual(2 * plafond);
}

/** One action on an `i` button opens its source panel, focus on its title; Escape gives it back. */
async function voletEnUneAction(page: Page, bouton: Locator) {
	const nom = (await bouton.getAttribute('aria-label'))!.replace(/^(?:Source et définition|Détail)\s:\s/, '');
	await expect(bouton).not.toHaveAttribute('title');
	await bouton.click();
	const volet = page.getByRole('dialog', { name: nom });
	await expect(volet).toBeVisible();
	await expect(volet.getByRole('heading', { name: nom })).toBeFocused();
	await expect(volet.locator('dt', { hasText: /^Définition$/ })).toBeVisible();
	await expect(volet.locator('dt', { hasText: /^Source$/ })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(volet).toBeHidden();
	await expect(bouton).toBeFocused();
}

/** Adds a commune from the comparator's in-page search, by its name among the proposals. */
async function ajouterDepuisLeComparateur(page: Page, saisie: string, nom: string) {
	await page.getByRole('button', { name: 'Ajouter une commune' }).click();
	await expect(champ(page)).toBeFocused();
	await champ(page).fill(saisie);
	await propositions(page).filter({ hasText: nom }).click();
	await expect(page.getByRole('status').filter({ hasText: 'ajoutée' })).toContainText(`${nom} ajoutée à la comparaison`);
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await contratTenu(request);
	expectNoUnexpectedOffLocalRequest(page);
});

for (const largeur of LARGEURS) {
	test.describe(`${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		test('Tâche 1 — homonymes : de la recherche à la fiche de la bonne commune', async ({ page, request }) => {
			await configurer(request, [
				recherche('le pin', [LE_PIN_79, LE_PIN_35]),
				adressesSansResultat('le pin'),
				commune('35999', FICHE_LE_PIN_35)
			]);
			await ouvrir(page, '/');

			await champUnique(page).fill('le pin');
			await expect(propositionsLieux(page)).toHaveCount(3);
			// Two homonyms, told apart by departement and postal code; the one out of coverage stays proposed.
			const horsCouverture = propositionsLieux(page).filter({ hasText: 'Deux-Sèvres (79)' });
			await expect(horsCouverture).toContainText('Le Pin');
			await expect(horsCouverture).toContainText('Hors couverture');
			const cible = propositionsLieux(page).filter({ hasText: 'Ille-et-Vilaine (35)' }).filter({ hasText: '35170' });
			await expect(cible).toHaveCount(1);
			await expect(cible).toContainText('Le Pin');
			await cible.click();

			await expect(page).toHaveURL(/\/commune\/35999$/);
			await expect(page.getByRole('heading', { level: 1, name: 'Le Pin' })).toBeVisible();
			await expect(page.locator('.fiche-sous')).toContainText('Ille-et-Vilaine (35) · INSEE 35999');
		});

		test('Tâche 2 — prix médian des maisons, son effectif, sa fenêtre et sa source', async ({ page, request }) => {
			await configurer(request, [commune('35999', FICHE_LE_PIN_35)]);
			await ouvrir(page, '/commune/35999');

			const kpi = page.locator('[data-kpi="prix-maisons"]');
			await expect(kpi.locator('dt.kpi-label')).toHaveText('Prix médian des maisons');
			await expect(kpi.locator('dt.kpi-label')).toBeVisible();
			await expect(kpi.locator('.state-value .value')).toHaveText('2\u202f000');
			await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
			// One short line under the value; the source is read in the panel (03-17 R-1).
			await expect(kpi.locator('.kpi-line .kpi-meta')).toHaveText('12 ventes · 2025');
			await expect(kpi.locator('.kpi-source')).toHaveCount(0);
			if (largeur === 375) {
				// Measured here; the asserted ceiling (half of a 375 × 800 window, option (b) of R-1 chosen
				// by Ianis) lives in fiche-commune.spec.ts, on the densest synthetic fixture.
				const strip = await page.locator('dl.kpi-strip').boundingBox();
				const moitie = await page.evaluate(() => window.innerHeight / 2);
				console.log(`RELEVE 375px reperes=${strip!.height} plafond=${moitie}`);
			}

			// The source panel of ①, in one action: definition of the contract, period, source.
			const bouton = kpi.getByRole('button', { name: 'Détail : Prix médian des maisons' });
			await bouton.click();
			const volet = page.getByRole('dialog', { name: 'Prix médian des maisons' });
			await expect(volet.getByRole('heading', { name: 'Prix médian des maisons' })).toBeFocused();
			const entree = (terme: string) =>
				volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: new RegExp(`^${terme}$`) }) }).locator('dd');
			await expect(entree('Définition')).toHaveText(definition('dvf.indicateurs.maison.mediane_courante'));
			await expect(entree('Période')).toHaveText('2025-01-01 → 2026-01-01 (fin exclue)');
			await expect(entree('Source')).toHaveText('DVF synthétique');
			await page.keyboard.press('Escape');
			await expect(volet).toBeHidden();
			await expect(bouton).toBeFocused();

			// Every value met by the task (the markers known for this commune) opens its panel in one action.
			const boutons = page.locator('[data-kpi] .source-button');
			await expect(boutons).toHaveCount(3);
			for (const un of await boutons.all()) await voletEnUneAction(page, un);
		});

		test('Tâche 3 — comparer trois communes dont une hors couverture, depuis la fiche', async ({ page, request }) => {
			await configurer(request, [
				commune(ANCIENNE.code_insee, ficheComparee(ANCIENNE.code_insee)),
				referentiel(ANCIENNE.code_insee, ANCIENNE.nom),
				recherche('Cour', [resultat(COURANTE)]),
				comparaison([ANCIENNE.code_insee, COURANTE.code_insee], DEUX),
				recherche('Couv', [resultat(HORS_COUVERTURE)]),
				comparaison([ANCIENNE.code_insee, COURANTE.code_insee, HORS_COUVERTURE.code_insee], TROIS)
			]);
			await ouvrir(page, `/commune/${ANCIENNE.code_insee}`);

			await page.getByRole('link', { name: 'Comparer avec une autre commune' }).click();
			await expect(page).toHaveURL(/\/comparer\?communes=44991$/);
			await expect(page.getByText('Ajoutez une deuxième commune pour lire les écarts.')).toBeVisible();

			await ajouterDepuisLeComparateur(page, 'Cour', COURANTE.nom);
			await expect(region(page)).toBeVisible();
			if (largeur === 375) {
				// Two hosted communes: the column header row is capped at a fifth of the window.
				const deux = (await page.locator('thead tr').boundingBox())!.height;
				const plafond = await page.evaluate(() => window.innerHeight / 5);
				console.log(`RELEVE synthetique 375px 2 communes hebergees entete_colonnes=${deux} plafond=${plafond}`);
				expect(deux, `thead ${deux}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
			}
			await ajouterDepuisLeComparateur(page, 'Couv', HORS_COUVERTURE.nom);
			await expect(page).toHaveURL(/\/comparer\?communes=44991,44992,85991$/);

			// Three columns in the order of addition; the one out of coverage keeps its column and its reason.
			await expect.poll(() => entetes(page)).toEqual(['44991', '44992', '85991']);
			await expect(page.locator('thead th[data-code="85991"] .state-badge')).toHaveText(
				'Hors couverture · département non hébergé'
			);
			// The served gap, recopied; the column out of coverage says its state once per cell,
			// without a repeated gap (03-18 C-03).
			const servi = TROIS.donnees.criteres.find((critere) => critere.cle === CLE_PRIX_MAISONS)!.cellules[1].ecart!;
			expect(norm(await ecart(page, CLE_PRIX_MAISONS, '44992').textContent())).toBe(
				`+${servi.valeur} €/m² par rapport à ${ANCIENNE.nom}`
			);
			const horsCouverture = page.locator(`tr[data-cle="${CLE_PRIX_MAISONS}"] td[data-code="85991"]`);
			await expect(horsCouverture).toHaveText('Hors couverture');
			await expect(horsCouverture).toHaveAttribute('data-state', 'out_of_coverage');
			await expect(ecart(page, CLE_PRIX_MAISONS, '85991')).toHaveCount(0);

			// On a phone the page scrolls; the region scrolls horizontally only; criteria and names kept.
			await comparateurSansCadre(page, largeur, '44992');
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		});

		test('Tâche 4 — la méthode en une action depuis la fiche et le comparateur, puis « Données & méthode »', async ({
			page,
			request
		}) => {
			await configurer(request, [
				commune(ANCIENNE.code_insee, ficheComparee(ANCIENNE.code_insee)),
				comparaison([ANCIENNE.code_insee, COURANTE.code_insee], DEUX),
				datasets()
			]);
			await ouvrir(page, `/commune/${ANCIENNE.code_insee}`);

			// A value of the fiche: a row of the first theme, read from its panel (04.8-04).
			const theme = await lireTheme(page, 'immobilier');
			await voletEnUneAction(page, theme.locator('.tab-l').first().getByRole('button', { name: /^Détail : / }));

			// A row of the comparator (lot 6 of phase 4.9: one control per row of identical provenance).
			await ouvrir(page, `/comparer?communes=${ANCIENNE.code_insee},${COURANTE.code_insee}`);
			await voletEnUneAction(page, page.getByRole('button', { name: 'Détail : Revenu déclaré médian', exact: true }));

			// The header link reaches « Données & méthode », whose first section is the coverage; up to
			// 1120 px the links are the panel of « Menu » (04.8 D-41, 4.9 harmonisation), opened first.
			if (largeur <= 1120) await page.getByRole('banner').locator('summary').click();
			await page
				.getByRole('navigation', { name: 'Navigation principale' })
				.getByRole('link', { name: 'Données & méthode' })
				.click();
			await expect(page).toHaveURL(/\/datasets$/);
			await expect(page.getByRole('heading', { level: 1, name: 'Données & méthode' })).toBeVisible();
			await expect(page.getByRole('region', { name: 'Couverture', exact: true })).toBeVisible();
		});
	});
}

// --- Phone invariants: vocabulary and distinct states (03-18, after 03-17) ----------------------

/** Vocabulary the screens must never show: the retired « Non servi », an absence titled as a
 * heading, the retired « Passoires ». A `Non renseigné` badge of a SERVED `not_reported` stays licit. */
async function vocabulaire(page: Page) {
	const releve = {
		non_servi: await page.getByText('Non servi', { exact: true }).count(),
		non_renseigne_titre: await page.getByRole('heading', { name: 'Non renseigné' }).count(),
		passoires: await page.getByText(/Passoires/).count()
	};
	expect(releve, JSON.stringify(releve)).toEqual({ non_servi: 0, non_renseigne_titre: 0, passoires: 0 });
	return releve;
}

const erreur = (pathname: string, query: Record<string, string>, status: number): Attente => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 1,
	maxCalls: 1,
	responses: [{ status, body: { detail: 'texte serveur' } }]
});

test.describe('Invariants téléphone (375 px)', () => {
	test.use({ viewport: { width: 375, height: 800 } });

	test('vocabulaire et états distincts : hors couverture, zéro résultat, panne, refus, zéro servi', async ({ page, request }) => {
		const horsBan = { statut: 'ok', donnees: [], meta: { hebergement: { ban: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] } } } };
		await configurer(request, [
			commune('35999', FICHE_LE_PIN_35),
			comparaison(['44991', '44992', '85991'], TROIS),
			lecture('/v1/communes/search', { q: '33000', limit: '20', inclure_arrondissements: 'true' }, horsBan),
			adressesSansResultat('33000'),
			recherche('zzzzqqq', [], '20'),
			adressesSansResultat('zzzzqqq'),
			// 04.7-14: a search without result reads its shortened prefixes for « communes au nom proche »
			// (prefixesProches: 7 - 2 and 7 / 2 characters); neither answers here.
			recherche('zzzzq', [], '5'),
			recherche('zzz', [], '5'),
			commune(LE_PIN_79.code_insee, reponseHorsCouverture('territoire_hors_perimetre')),
			referentiel(LE_PIN_79.code_insee, LE_PIN_79.nom),
			erreur('/v1/communes/search', { q: 'panne', limit: '20', inclure_arrondissements: 'true' }, 503),
			erreur('/v1/adresses/recherche', { q: 'panne', limite: '8' }, 503),
			erreur('/v1/communes/search', { q: 'refus', limit: '20', inclure_arrondissements: 'true' }, 409),
			erreur('/v1/adresses/recherche', { q: 'refus', limite: '8' }, 409)
		]);

		// The fiche: vocabulary, and a served zero stays a value, distinct from any absence badge.
		await ouvrir(page, '/commune/35999');
		await expect(page.locator('[data-kpi="prix-maisons"] .state-value')).toBeVisible();
		await vocabulaire(page);
		const zeros = await page.locator('[data-state="value"]').evaluateAll((nodes) =>
			nodes
				.filter((node) => node.querySelector('.value')?.textContent?.trim() === '0')
				.map((node) => ({ badge: node.querySelector('.state-badge') !== null, unite: node.querySelector('.unit')?.textContent?.trim() ?? null }))
		);
		expect(zeros.length, 'a served zero on the fiche').toBeGreaterThan(0);
		for (const zero of zeros) expect(zero, JSON.stringify(zeros)).toMatchObject({ badge: false });
		expect(zeros.some((zero) => zero.unite), JSON.stringify(zeros)).toBe(true);

		// The comparator: same vocabulary.
		await ouvrir(page, '/comparer?communes=44991,44992,85991');
		await expect(region(page)).toBeVisible();
		await vocabulaire(page);

		// A postal code out of coverage: the warn notice, titled; never « Aucune commune trouvée ».
		await ouvrir(page, '/communes?q=33000');
		const resultats = page.getByRole('region', { name: 'Résultats' });
		await expect(resultats.getByRole('heading', { level: 2 })).toHaveText('Code postal hors couverture');
		await expect(resultats.locator('.communes-notice')).toHaveClass(/communes-notice-warn/);

		// Zero result: a neutral notice, its own title.
		await ouvrir(page, '/communes?q=zzzzqqq');
		// D8 (2026-11-10, agreed by the orchestrator): the title of a zero is the h2 of the Notice of the fiche,
		// announced by its status role (was the state line `p.r-etat`, no h2).
		await expect(resultats.getByRole('heading', { level: 2 })).toHaveCount(1);
		await expect(resultats.getByRole('status').getByRole('heading', { level: 2 })).toHaveText('Aucune commune ni adresse trouvée pour « zzzzqqq »');
		await expect(resultats.locator('.communes-notice')).not.toHaveClass(/communes-notice-warn/);

		// A commune out of coverage: the warn notice of the contract, no marker.
		await ouvrir(page, `/commune/${LE_PIN_79.code_insee}`);
		await expect(page.getByRole('status').filter({ hasText: 'Commune hors couverture' })).toBeVisible();
		await expect(page.locator('[data-kpi]')).toHaveCount(0);

		// An outage keeps the generic wording; a refused request (409) is titled as such.
		await ouvrir(page, '/communes?q=panne');
		await expect(page.getByRole('alert')).toContainText('La recherche est momentanément indisponible.');
		await expect(page.getByRole('alert')).not.toContainText('Demande refusée');
		await ouvrir(page, '/communes?q=refus');
		await expect(page.getByRole('alert')).toContainText('Demande refusée');
		await expect(page.getByRole('alert')).not.toContainText('momentanément indisponible');
		await expect(page.getByText('texte serveur')).toHaveCount(0);
		console.log('RELEVE synthetique invariants 375px non_servi=0 non_renseigne_titre=0 passoires=0 etats_distincts=true');
	});
});

// --- UI-01 invariants on every screen of the phase ---------------------------------------------

interface Ecran {
	nom: string;
	url: string;
	attentes: Attente[];
	pret: (page: Page) => Promise<void>;
	ordreTabulation?: boolean;
}

const ECRANS: Ecran[] = [
	{
		nom: 'accueil',
		url: '/',
		attentes: [],
		// 04.8-07 (UI-SPEC § 6.6): the v4 h1 of the home.
		pret: (page) => expect(page.getByRole('heading', { level: 1, name: 'Une adresse, en clair' })).toBeVisible()
	},
	{
		nom: 'résultats',
		url: '/communes?q=le+pin',
		attentes: [recherche('le pin', [LE_PIN_79, LE_PIN_35], '20'), adressesSansResultat('le pin')],
		// 04.8-07 (UI-SPEC § 6.5): one h2 for the group, the homonyms as rows of whole-row links.
		pret: async (page) => {
			await expect(page.getByRole('heading', { level: 2 })).toHaveText(['Communes']);
			await expect(page.locator('li.resultat .lien-t')).toHaveText(['Le Pin', 'Le Pin']);
		}
	},
	{
		nom: 'fiche',
		url: '/commune/35999',
		// Read twice at most: the header invariant reloads the fiche with four communes set aside.
		attentes: [commune('35999', FICHE_LE_PIN_35, 2)],
		pret: (page) => expect(page.locator('[data-kpi="prix-maisons"] .state-value')).toBeVisible(),
		ordreTabulation: true
	},
	{
		nom: 'comparateur',
		url: '/comparer?communes=44991,44992,85991',
		attentes: [comparaison(['44991', '44992', '85991'], TROIS)],
		pret: (page) => expect(region(page)).toBeVisible(),
		ordreTabulation: true
	},
	{
		nom: 'données et méthode',
		url: '/datasets',
		attentes: [datasets()],
		pret: (page) => expect(page.getByRole('region', { name: 'Couverture', exact: true })).toBeVisible()
	},
	{
		nom: 'développeurs',
		url: '/developpeurs',
		attentes: [],
		pret: (page) => expect(page.getByRole('heading', { level: 1, name: 'Développeurs' })).toBeVisible()
	}
];

/** Layout invariants of UI-01, measured in one pass inside the page. */
function mesurerInvariants(regionLabel: string) {
	const visible = (el: Element) => {
		const box = el.getBoundingClientRect();
		return box.width > 1 && box.height > 1 && el.checkVisibility({ visibilityProperty: true });
	};
	// Visually hidden text (clip, 1 px box) is not a truncated box.
	const masqueAccessible = (el: Element) => {
		const style = getComputedStyle(el);
		return style.clip !== 'auto' || style.clipPath !== 'none';
	};
	const nom = (el: Element) =>
		`${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${el.classList.length ? `.${[...el.classList].join('.')}` : ''}`;
	const defilantAutorise = (el: Element) =>
		el.getAttribute('aria-label') === regionLabel ||
		// 04.8-06 (UI-SPEC § 6.9): on /developpeurs the code scrolls inside its named, focusable wrapper.
		(el.getAttribute('role') === 'region' && el.getAttribute('aria-label') === 'Exemple de requête curl' && location.pathname === '/developpeurs');
	const dansDefilant = (el: Element) => {
		for (let parent = el.parentElement; parent; parent = parent.parentElement) if (defilantAutorise(parent)) return true;
		return false;
	};
	const tous = [...document.querySelectorAll('body *')];
	const formulaires = new Set(['INPUT', 'SELECT', 'TEXTAREA']);

	const titres = [...document.querySelectorAll('a[title], button[title], summary[title], [role="button"][title], input[title], select[title], [tabindex][title]')].map(nom);
	const fixes = tous
		.filter((el) => getComputedStyle(el).position === 'fixed')
		// The skip link predates the phase (app.css, 03-03): off-screen until focused.
		.filter((el) => el.tagName !== 'DIALOG' && !el.classList.contains('skip-link'))
		.map(nom);
	const defilants = tous
		.filter((el) => !formulaires.has(el.tagName) && visible(el))
		.filter((el) => ['auto', 'scroll'].includes(getComputedStyle(el).overflowX) && el.scrollWidth > el.clientWidth + 1)
		.filter((el) => !defilantAutorise(el))
		.map(nom);
	// The one cut by design: the rings of the home page, a decoration hidden from assistive technologies, stop at
	// the edges of the head, past the window on every width.
	const decorCoupe = (el: Element) => el.closest('.cadre-anneaux[aria-hidden="true"]') !== null;
	const tronques = tous
		.filter((el) => !formulaires.has(el.tagName) && visible(el) && !masqueAccessible(el) && !dansDefilant(el) && !decorCoupe(el))
		.filter((el) => {
			const style = getComputedStyle(el);
			const coupeX = ['hidden', 'clip'].includes(style.overflowX) && el.scrollWidth > el.clientWidth + 1;
			const coupeY = ['hidden', 'clip'].includes(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
			return coupeX || coupeY;
		})
		.map(nom);
	const horsCadre = tous
		.filter((el) => visible(el) && !dansDefilant(el) && !decorCoupe(el) && !el.closest('dialog') && !el.classList.contains('skip-link'))
		.filter((el) => {
			const box = el.getBoundingClientRect();
			return box.left < -1 || box.right > window.innerWidth + 1;
		})
		.map(nom);
	const reordonnes = [...document.querySelectorAll('main, main *')]
		.filter((el) => {
			const style = getComputedStyle(el);
			return style.order !== '0' || style.flexDirection.endsWith('reverse') || style.gridAutoFlow.includes('dense');
		})
		.map(nom);
	const tabindexPositifs = [...document.querySelectorAll('[tabindex]')].filter((el) => (el as HTMLElement).tabIndex > 0).map(nom);
	return {
		scrollWidth: document.documentElement.scrollWidth,
		innerWidth: window.innerWidth,
		titres,
		fixes,
		defilants,
		tronques,
		horsCadre,
		reordonnes,
		tabindexPositifs
	};
}

/** Tags the tabbable elements in DOM order and returns their count. */
function marquerTabulables(): number {
	const fermeAuDessus = (el: Element) => {
		for (let parent = el.parentElement; parent; parent = parent.parentElement) {
			if (parent.tagName === 'DETAILS' && !(parent as HTMLDetailsElement).open) {
				const resume = parent.querySelector(':scope > summary');
				if (!(resume && (resume === el || resume.contains(el)))) return true;
			}
		}
		return false;
	};
	const candidats = [...document.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, summary, [tabindex]')];
	const tabulables = candidats.filter((el) => {
		if (el.tabIndex < 0 || el.matches(':disabled') || el.closest('[inert]')) return false;
		if (el instanceof HTMLInputElement && el.type === 'hidden') return false;
		if (el.closest('dialog:not([open])') || fermeAuDessus(el)) return false;
		const box = el.getBoundingClientRect();
		return box.width > 0 && box.height > 0 && el.checkVisibility({ visibilityProperty: true });
	});
	tabulables.forEach((el, index) => (el.dataset.recetteTab = String(index)));
	return tabulables.length;
}

for (const largeur of LARGEURS) {
	test.describe(`Invariants UI-01 à ${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		for (const ecran of ECRANS) {
			test(`${ecran.nom} (${ecran.url})`, async ({ page, request }) => {
				await configurer(request, ecran.attentes);
				await ouvrir(page, ecran.url);
				await ecran.pret(page);

				const mesure = await page.evaluate(mesurerInvariants, REGION);
				expect(mesure.scrollWidth, `document ${mesure.scrollWidth}px > fenêtre ${mesure.innerWidth}px`).toBeLessThanOrEqual(
					mesure.innerWidth
				);
				expect(mesure.titres, 'élément interactif avec title').toEqual([]);
				expect(mesure.fixes, 'position: fixed hors <dialog> et lien d’évitement').toEqual([]);
				expect(mesure.defilants, 'défilement horizontal hors de la région du comparateur').toEqual([]);
				expect(mesure.tronques, 'boîte coupée par son conteneur').toEqual([]);
				expect(mesure.horsCadre, 'boîte hors de la fenêtre').toEqual([]);
				expect(mesure.reordonnes, 'ordre visuel réordonné par CSS').toEqual([]);
				expect(mesure.tabindexPositifs, 'tabindex positif').toEqual([]);

				const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
				expect(axe.passes.length, 'scan axe vide').toBeGreaterThan(0);
				expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);

				// On a phone the site header takes a fifth of the window at most (03-17 H-01).
				const enTeteSans = await hauteurEnTete(page);
				if (largeur === 375) {
					const plafond = await page.evaluate(() => window.innerHeight / 5);
					console.log(`RELEVE 375px ${ecran.nom} entete_site=${enTeteSans} plafond=${plafond}`);
					expect(enTeteSans, `en-tête ${enTeteSans}px > un cinquième de la fenêtre (${plafond}px)`).toBeLessThanOrEqual(plafond);
				}

				if (!ecran.ordreTabulation) return;
				// Tab order = DOM order, from the start of the document (60 stops at most on the comparator).
				const total = await page.evaluate(marquerTabulables);
				const arrets = Math.min(total, 60);
				expect(arrets, 'éléments tabulables').toBeGreaterThan(10);
				const suivis: string[] = [];
				for (let index = 0; index < arrets; index++) {
					await page.keyboard.press('Tab');
					suivis.push(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.recetteTab ?? `hors liste: ${document.activeElement?.tagName}`));
				}
				expect(suivis).toEqual(Array.from({ length: arrets }, (_, index) => String(index)));

				if (ecran.nom === 'fiche') {
					// The synthesis reads ① ② ③ ④ in rows, left to right, whatever the grid.
					const boites = await page.locator('[data-kpi]').evaluateAll((nodes) =>
						nodes.map((node) => ({ id: node.getAttribute('data-kpi'), ...node.getBoundingClientRect().toJSON() }))
					);
					expect(boites.map((boite) => boite.id)).toEqual(['prix-maisons', 'tendance', 'dpe-dominant']);
					for (let index = 1; index < boites.length; index++) {
						const [avant, apres] = [boites[index - 1], boites[index]];
						const memeLigne = Math.abs(apres.top - avant.top) <= 1;
						expect(memeLigne ? apres.left > avant.left : apres.top > avant.top, JSON.stringify([avant, apres])).toBe(true);
					}

					if (largeur === 375) {
						// Four communes set aside in the tab: the counter never changes the header height (H-01).
						await page.evaluate(() => sessionStorage.setItem('dc:comparaison', JSON.stringify(['44991', '44992', '85991', '44993'])));
						await ouvrir(page, ecran.url);
						await ecran.pret(page);
						await expect(page.locator('.site-nav-count')).toHaveText('4');
						const enTeteAvec = await hauteurEnTete(page);
						expect(Math.abs(enTeteAvec - enTeteSans), `en-tête ${enTeteSans}px sans sélection, ${enTeteAvec}px avec quatre communes`).toBeLessThanOrEqual(1);
					}
				} else {
					expect(await entetes(page)).toEqual(['44991', '44992', '85991']);
				}
			});
		}
	});
}
