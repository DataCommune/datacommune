// Panel of the parcel map: the sales of the chosen parcel (04.10-07, LIEU-02, SEM-01, SEM-02, UI-01), at
// 375 px. The sales come from the route of the map on the origin of the site (the contract example
// `ventes-parcelle.json` of 04.10-02), read by the browser once a parcel is chosen.
//
// What this spec locks:
//   - the served HTML of the fiche holds no date and no price of the sales of the map (restrictions on
//     DVF: the sales are never indexed); they are read after the choice only;
//   - the cards are those of the fiche: the badge « Prix non affiché » for a withheld price, never an
//     amount; five per page, the next page by a button, the range announced, the focus on the title;
//   - zero (with its period and its reserve), out of coverage, outage and rate limit are four displays,
//     none of them a zero;
//   - a sale of several parcels says it, with its reserves, and its toggle tells the map.
// Synthetic SSR source, headless runner, isolated context (AGENTS.md).
import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import { EXEMPLE_CONTEXTE, EXEMPLE_VENTES, attendreAucuneCarteNonReconnue, installerRoutesCarte, type RoutesCarte } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, rapportAvecThemes, REQUETE_RAPPORT } from './fixtures/rapport';

const NB = String.fromCharCode(0xa0);
const LECTURES = 12;

// A synthetic address (no such street): the fiche of the map, with the parcel of the contract example.
const BAN_ID = '85191_0802_00003';
const PARCELLE_ADRESSE = EXEMPLE_CONTEXTE.donnees.parcelle_adresse.id_parcelle;

const IDENTITE = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: '3 Rue des Parcelles, 85000 La Roche-sur-Yon',
	numero: '3',
	rep: null,
	nom_voie: 'Rue des Parcelles',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: PARCELLE_ADRESSE, nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};
const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const CARTE = {
	images: { plan: `/carte/${BAN_ID}/plan.png`, parcelles: `/carte/${BAN_ID}/parcelles.png` },
	emprise_m: 300,
	attribution: '© IGN (Plan IGN, Parcellaire Express)',
	licence: 'Licence Ouverte 2.0',
	geoportail: null
};
const SANS_DOCUMENT = {
	statut: 'ok',
	donnees: {
		etat: 'value', motif: null, total: 0, page: 1, pages: 1, par_page: 10, generation: '0123456789abcdef',
		distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 }, date_import: '2026-09-30', documents: [],
		selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] },
		signal_plusieurs_logements: null, constat_d110: null,
		source: { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' },
		rattachement: 'adresse_saisie_diagnostiqueur'
	}
};

function rapport() {
	const reponse = rapportAvecThemes(
		{ status: 200, body: { statut: 'ok', donnees: { identite: IDENTITE, couverture: { etat: 'heberge', motif: null } }, meta: { source: [SOURCE_BAN] } } },
		[]
	);
	const corps = reponse.body as { donnees: Record<string, unknown> };
	return { ...corps, donnees: { ...corps.donnees, carte: CARTE } };
}

async function configurer(request: APIRequestContext) {
	const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
		method: 'GET' as const,
		pathname,
		query,
		minCalls: 0,
		maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
	});
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: avecVentesDeLaParcelle([
				attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, rapport()),
				attente(`/v1/adresse/${BAN_ID}/dpe`, { page: '1' }, SANS_DOCUMENT)
			])
		}
	});
	expect(res.ok()).toBe(true);
}

async function ouvrirFiche(page: Page) {
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto(`/adresse/${BAN_ID}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('3 rue des Parcelles');
}

async function ouvrirCarte(page: Page) {
	const bouton = page.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
	await bouton.click();
	const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
	await expect(dialogue).toBeVisible();
	const panneau = dialogue.locator('section.parcelle');
	await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
	return { dialogue, panneau, ventes: panneau.locator('section.parcelle-ventes') };
}

async function fermer(page: Page, dialogue: Locator) {
	await dialogue.getByRole('button', { name: 'Fermer' }).click();
	await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const copie = (): any => structuredClone(EXEMPLE_VENTES);

/** Page *numero* of 12 sales, five per page, under the generation of the example. */
function pageDe(numero: number) {
	const corps = copie();
	const n = numero < 3 ? 5 : 2;
	const modele = corps.donnees.ventes[0];
	corps.donnees.ventes = Array.from({ length: n }, (_, i) => ({ ...modele, date: `202${5 - numero}-0${i + 1}-15` }));
	corps.donnees.groupes = Array.from({ length: n }, () => null);
	Object.assign(corps.donnees, { total: 12, page: numero, pages: 3 });
	return corps;
}

// Every date and price of the example of the map, as a card writes them and as the source serves them.
const TRACES_DES_VENTES = [/2025-03-14|2024-06-01|2022-09-20|2021-02-10/, /mars 2025|juin 2024|septembre 2022|février 2021/i, /238\s?000|1\s?250\s?000|48\s?000|2\s?587/];

test.describe('Carte des parcelles — panneau des ventes à 375 px', () => {
	let routes: RoutesCarte;
	let erreurs: string[];

	test.beforeEach(async ({ page, request }) => {
		erreurs = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		await page.setViewportSize({ width: 375, height: 800 });
		await installLocalNetworkGuard(page);
		routes = await installerRoutesCarte(page);
		await configurer(request);
	});

	test.afterEach(async ({ page }) => {
		expect(erreurs, 'aucune erreur de script dans la page').toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
		attendreAucuneCarteNonReconnue(page);
	});

	test('les ventes sont lues après la sélection, jamais dans le HTML servi ; cartes de la fiche, prix retenu', async ({ page }) => {
		// The served HTML of the fiche: no trace of the sales of the map.
		const servi = await (await page.request.get(`${E2E_APP_ORIGIN}/adresse/${BAN_ID}`)).text();
		expect(servi).toContain('3 rue des Parcelles');
		for (const trace of TRACES_DES_VENTES) expect(servi, String(trace)).not.toMatch(trace);

		await ouvrirFiche(page);
		expect(routes.demandes.ventes).toEqual([]);
		for (const trace of TRACES_DES_VENTES) expect(await page.content(), String(trace)).not.toMatch(trace);

		const { ventes } = await ouvrirCarte(page);
		// The parcel of the address is selected at the opening: its sales are read, for this address.
		await expect(ventes.locator('.ventes-cartes > li')).toHaveCount(4);
		expect(routes.demandes.ventes).toEqual([`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/ventes`]);
		await expect(ventes).toHaveAttribute('aria-busy', 'false');
		await expect(ventes.getByRole('heading', { level: 4 })).toHaveText('Ventes publiées sur la parcelle');
		await expect(ventes.locator('.parcelle-ventes-source')).toHaveText('DVF (DGFiP) · 2021-2025 · ventes de la parcelle, rattachées à aucun logement');
		// Non-vacuity of the check on the served HTML: the panel now writes these very traces.
		const texte = (await ventes.textContent()) ?? '';
		for (const trace of TRACES_DES_VENTES.slice(1)) expect(texte).toMatch(trace);
		// The withheld price: the badge of the fiche, never an amount.
		const retenue = ventes.locator('.ventes-cartes > li').nth(1);
		await expect(retenue.locator('.ventecard-badge')).toHaveText('Prix non affiché');
		await expect(retenue).not.toContainText('€');
		// One page: no navigation.
		await expect(ventes.locator('nav')).toHaveCount(0);
	});

	test('cinq ventes par page, la suivante par un bouton : plage annoncée, focus sur le titre, jeton porté', async ({ page }) => {
		const demandees: string[] = [];
		page.on('request', (requete) => {
			const url = new URL(requete.url());
			if (url.pathname.endsWith('/ventes') && url.pathname.startsWith('/carte/')) demandees.push(`${url.pathname}${url.search}`);
		});
		routes.repondre('ventes', (url) => ({ status: 200, json: pageDe(Number(url.searchParams.get('page'))) }));
		await ouvrirFiche(page);
		const { panneau, ventes } = await ouvrirCarte(page);
		await expect(ventes.locator('.ventes-cartes > li')).toHaveCount(5);
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 1 à 5 sur 12');
		await expect(ventes.getByRole('button', { name: 'Page précédente' })).toHaveCount(0);

		await ventes.getByRole('button', { name: 'Page suivante' }).click();
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 6 à 10 sur 12');
		await expect(ventes.locator('.parcelle-ventes-annonce')).toHaveText('Ventes 6 à 10 sur 12');
		await expect(panneau.getByRole('heading', { level: 3 })).toBeFocused();
		expect(demandees).toEqual([
			`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/ventes?page=1`,
			`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/ventes?page=2&vg=0123456789abcdef`
		]);

		await ventes.getByRole('button', { name: 'Page suivante' }).click();
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 11 à 12 sur 12');
		await expect(ventes.locator('.ventes-cartes > li')).toHaveCount(2);
		await expect(ventes.getByRole('button', { name: 'Page suivante' })).toHaveCount(0);
		// The buttons keep the 44 px of the pagination of the fiche.
		const precedente = ventes.getByRole('button', { name: 'Page précédente' });
		expect(Math.round((await precedente.boundingBox())!.height)).toBeGreaterThanOrEqual(44);
		await precedente.click();
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 6 à 10 sur 12');
	});

	test('zéro, hors couverture, panne et limite de requêtes : quatre affichages, aucun n’est un zéro sauf le zéro', async ({ page }) => {
		await ouvrirFiche(page);

		// Zero over the period: the sentence of the fiche with the served period, then the reserve.
		const zero = copie();
		Object.assign(zero.donnees, { total: 0, pages: 1, ventes: [], groupes: [] });
		routes.repondre('ventes', { status: 200, json: zero });
		let { dialogue, ventes } = await ouvrirCarte(page);
		await expect(ventes.locator('.parcelle-ventes-zero')).toHaveText([
			'Aucune vente publiée sur la parcelle dans DVF (2021-2025).',
			'Avant 2021, les ventes ne sont pas connues ici.'
		]);
		await expect(ventes.locator('[role="alert"]')).toHaveCount(0);
		await fermer(page, dialogue);

		// Out of coverage: the state of the glossary, never a zero.
		routes.repondre('ventes', { status: 200, json: { statut: 'ok', donnees: { etat: 'out_of_coverage', motif: 'dataset_non_heberge', page: 1, par_page: 5 } } });
		({ dialogue, ventes } = await ouvrirCarte(page));
		await expect(ventes.locator('[data-state="out_of_coverage"]')).toBeVisible();
		await expect(ventes).not.toContainText('Aucune vente');
		await fermer(page, dialogue);

		// Outage: the text of the drawer of sales, in an alert, and its reload.
		routes.repondre('ventes', { status: 503, json: { title: 'Ventes indisponibles' } });
		({ dialogue, ventes } = await ouvrirCarte(page));
		const alerte = ventes.getByRole('alert');
		await expect(alerte).toContainText("Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes.");
		await expect(ventes).not.toContainText('Aucune vente');
		routes.repondre('ventes', { status: 200, json: EXEMPLE_VENTES });
		await alerte.getByRole('button', { name: 'Recharger les ventes' }).click();
		await expect(ventes.locator('.ventes-cartes > li')).toHaveCount(4);
		await expect(ventes.getByRole('alert')).toHaveCount(0);
		await fermer(page, dialogue);

		// Rate limit: the message of the limit with its served delay, never an outage.
		routes.repondre('ventes', { status: 429, json: { title: 'Trop de demandes' }, headers: { 'Retry-After': '30' } });
		({ dialogue, ventes } = await ouvrirCarte(page));
		await expect(ventes.getByRole('alert')).toContainText(`La limite de requêtes est atteinte. Réessayez dans 30${NB}secondes.`);
		await expect(ventes).not.toContainText('Source indisponible');
		await fermer(page, dialogue);
	});

	test('une vente à plusieurs parcelles le dit, avec ses réserves ; la bascule montre un groupe à la fois', async ({ page }) => {
		await ouvrirFiche(page);
		const { panneau, ventes } = await ouvrirCarte(page);
		const cartes = ventes.locator('.ventes-cartes > li');
		await expect(cartes).toHaveCount(4);
		const pied = (rang: number) => cartes.nth(rang).locator('.vente-groupe');

		// A sale of this parcel alone says nothing of a group.
		await expect(pied(0)).toHaveCount(0);
		// Withheld price: no price reserve; the other parcel, agreed in the singular; the toggle.
		await expect(pied(1).locator('p')).toHaveText(['Vendue avec 1 autre parcelle']);
		const bascule = pied(1).getByRole('button', { name: 'Montrer ces parcelles' });
		await expect(bascule).toHaveAttribute('aria-pressed', 'false');
		await expect(bascule).toHaveClass(/bouton-lien/);
		expect(Math.round((await bascule.boundingBox())!.height)).toBeGreaterThanOrEqual(44);
		// Displayed price: the price is that of the whole sale.
		await expect(pied(2).locator('p')).toHaveText(['Prix de toute la vente (3 parcelles).', 'Vendue avec 2 autres parcelles']);
		// Beyond the cap of the route: said, never drawn, no toggle, and T-45 says why (R4 of Ianis, 2026-11-15).
		await expect(pied(3).locator('p')).toHaveText([
			'Prix de toute la vente (60 parcelles).',
			'Vendue avec 59 autres parcelles',
			'Groupe trop grand pour être montré sur la carte (60 parcelles).'
		]);
		await expect(pied(3).getByRole('button')).toHaveCount(0);

		await bascule.click();
		await expect(bascule).toHaveAttribute('aria-pressed', 'true');
		await expect(pied(1).locator('p')).toHaveText([
			'Vendue avec 1 autre parcelle',
			`2 parcelles vendues ensemble · juin 2024 · 830${NB}m² au total`,
			'Ne dit pas qui les possède aujourd’hui.'
		]);

		// One group at a time; an incomplete surface is said, never summed.
		await pied(2).getByRole('button', { name: 'Montrer ces parcelles' }).click();
		await expect(bascule).toHaveAttribute('aria-pressed', 'false');
		await expect(ventes.locator('[aria-pressed="true"]')).toHaveCount(1);
		await expect(pied(2).locator('p')).toHaveText([
			'Prix de toute la vente (3 parcelles).',
			'Vendue avec 2 autres parcelles',
			'3 parcelles vendues ensemble · septembre 2022',
			'Ne dit pas qui les possède aujourd’hui.',
			`Surface totale non calculée${NB}: 1 de ces parcelles ne figure plus au cadastre.`
		]);
		// Never an owner (UI contract, forbidden words).
		await expect(panneau).not.toContainText(/propriétaire|unité foncière/);
	});
});
