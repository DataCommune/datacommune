// Interactive parcel map of the address fiche (04.10-03, LIEU-01, SEM-01, UI-01): the tracer path at
// 375 px. From the button under the picture of the fiche to the chosen parcel: the dialog opens, Leaflet
// is asked at that moment only, the parcels come from the routes of the map on the origin of the site
// (the contract examples of 04.10-02), one parcel is chosen and the panel under the map says its
// reference and its surface.
//
// What this spec locks:
//   - nothing of the map before the click: no module that contains Leaflet, no map route, and no
//     neighbouring parcel in the served HTML of the fiche (rule (c) of the phase, Q7);
//   - the parcel of the address stands apart and is selected at the opening; the point of the address
//     stays a point (LIEU-01);
//   - a parcel without a surface says the label of the glossary, never « 0 m² » (SEM-01);
//   - Escape closes and gives the focus back; no request leaves the local origin, none goes to a map
//     path that no route knows.
// Synthetic SSR source, headless runner, isolated context (AGENTS.md).
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import {
	EXEMPLE_CONTEXTE,
	EXEMPLE_TUILE,
	attendreAucuneCarteNonReconnue,
	installerRoutesCarte,
	pngGris,
	type RoutesCarte
} from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_APP_ORIGIN, E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, rapportAvecThemes, REQUETE_RAPPORT } from './fixtures/rapport';

const NB = String.fromCharCode(0xa0);
const LECTURES = 12;

// A synthetic address (no such street): the fiche of the map, with the parcel of the contract example.
const BAN_ID = '85191_0802_00003';
const PARCELLES = EXEMPLE_TUILE.donnees.collection.features;
const PARCELLE_ADRESSE = EXEMPLE_CONTEXTE.donnees.parcelle_adresse.id_parcelle;
const VOISINES = PARCELLES.map((p) => p.id).filter((id) => id !== PARCELLE_ADRESSE);

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

function rapport(carte: typeof CARTE | null) {
	const reponse = rapportAvecThemes(
		{ status: 200, body: { statut: 'ok', donnees: { identite: IDENTITE, couverture: { etat: 'heberge', motif: null } }, meta: { source: [SOURCE_BAN] } } },
		[]
	);
	const corps = reponse.body as { donnees: Record<string, unknown> };
	return { ...corps, donnees: { ...corps.donnees, ...(carte ? { carte } : {}) } };
}

const repete = (body: unknown) => Array.from({ length: LECTURES }, () => ({ status: 200, body }));

async function configurer(request: APIRequestContext, carte: typeof CARTE | null = CARTE) {
	const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
		method: 'GET' as const,
		pathname,
		query,
		minCalls: 0,
		maxCalls: LECTURES,
		responses: repete(body)
	});
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: avecVentesDeLaParcelle([
				attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, rapport(carte)),
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

/** Where a capture goes: the directory of `DC_CARTE_EVIDENCE_DIR` when set, the output of the test otherwise. */
function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_CARTE_EVIDENCE_DIR;
	return dossier ? `${dossier.replace(/[\\/]$/, '')}/${nom}` : testInfo.outputPath(nom);
}

const centre = async (cible: Locator) => {
	const boite = (await cible.boundingBox())!;
	return { x: boite.x + boite.width / 2, y: boite.y + boite.height / 2 };
};

/** The drawn parcel whose centre is the nearest to *point*, among the clickable parcels. */
async function parcelleVers(page: Page, point: { x: number; y: number }): Promise<{ x: number; y: number }> {
	const centres = await Promise.all((await page.locator('path.parcel').all()).map(centre));
	return centres.reduce((a, b) => (Math.hypot(a.x - point.x, a.y - point.y) <= Math.hypot(b.x - point.x, b.y - point.y) ? a : b));
}

test.describe('Carte des parcelles — tracer à 375 px', () => {
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

	test('du bouton de la fiche à la parcelle choisie : ouvrir, voir, choisir, lire, fermer', async ({ page }, testInfo) => {
		const demandees: string[] = [];
		page.on('request', (requete) => demandees.push(requete.url()));
		// The dev server also serves the `?url` modules of the loader (one string each, the address of a file):
		// they hold no code of Leaflet and do not exist in the build.
		const avecLeaflet = () => demandees.filter((url) => /leaflet/i.test(url) && new URL(url).search !== '?url');

		await ouvrirFiche(page);
		const bouton = page.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
		await expect(bouton).toBeVisible();
		await expect(bouton).toHaveAttribute('aria-haspopup', 'dialog');
		await expect(bouton).toHaveClass(/bouton-secondaire/);
		// The row of the button is 44 px high, the picture and its caption around it.
		const rangee = page.locator('.adresse-carte .carte-action');
		await expect(rangee).toHaveCount(1);
		expect(Math.round((await rangee.boundingBox())!.height)).toBe(44);
		await expect(page.locator('.adresse-carte figure.carte img')).toHaveCount(2);

		// Before the click: no module that contains Leaflet, no route of the interactive map.
		await bouton.scrollIntoViewIfNeeded();
		await bouton.hover();
		expect(avecLeaflet(), 'aucune requête Leaflet avant le clic').toEqual([]);
		expect(routes.demandes.contexte).toEqual([]);
		expect(routes.demandes.parcelles).toEqual([]);
		expect(routes.demandes.images.length).toBeGreaterThan(0);
		await page.screenshot({ path: preuve(testInfo, 'carte-375-bouton.png'), animations: 'disabled' });

		await bouton.click();
		const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
		await expect(dialogue).toBeVisible();
		const defilementFiche = await page.evaluate(() => window.scrollY);
		expect(await dialogue.evaluate((d: HTMLDialogElement) => d.matches(':modal'))).toBe(true);
		const titre = dialogue.getByRole('heading', { level: 2, name: 'Parcelles autour de l’adresse' });
		await expect(titre).toBeFocused();
		// Full screen under 720 px.
		expect(await dialogue.boundingBox()).toEqual({ x: 0, y: 0, width: 375, height: 800 });

		// The parcels of the fixture are drawn once each, whatever the number of tiles that carry them.
		await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
		expect(routes.demandes.contexte).toEqual([`/carte/${BAN_ID}/contexte`]);
		expect(routes.demandes.parcelles.length, 'plusieurs tuiles portent les mêmes parcelles').toBeGreaterThan(1);
		for (const chemin of routes.demandes.parcelles) expect(chemin).toMatch(/^\/carte\/parcelles\/18\/\d+\/\d+$/);
		expect(avecLeaflet().length, 'Leaflet est demandé après le clic').toBeGreaterThan(0);
		for (const url of avecLeaflet()) expect(new URL(url).origin).toBe(E2E_APP_ORIGIN);
		// The loading notice of the frame left with the map.
		await expect(dialogue.getByText('Chargement de la carte…')).toHaveCount(0);

		// The parcel of the address stands apart, and the address stays a point (LIEU-01).
		await expect(dialogue.locator('path.parcel-address').first()).toBeVisible();
		const point = dialogue.locator('.point-adresse');
		await expect(point).toHaveCount(1);
		const traits = await dialogue.locator('path.parcel-address').first().evaluate((trait) => {
			const style = getComputedStyle(trait);
			const jeton = document.createElement('i');
			jeton.style.color = 'var(--color-accent)';
			trait.closest('dialog')!.append(jeton);
			const accent = getComputedStyle(jeton).color;
			jeton.remove();
			return { stroke: style.stroke, largeur: style.strokeWidth, accent };
		});
		expect(traits.stroke, 'trait de la parcelle de l’adresse : le jeton accent, pas la couleur de Leaflet').toBe(traits.accent);
		expect(traits.largeur).toBe('4px');

		// C3: the parcel of the address is selected at the opening, the panel answers without a gesture.
		const selection = dialogue.locator('path.parcel-selected');
		await expect(selection).toHaveCount(1);
		const panneau = dialogue.locator('section.parcelle');
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
		await expect(panneau.getByText('Parcelle de l’adresse', { exact: true })).toBeVisible();
		await expect(panneau.locator('dt')).toHaveText(['Surface cadastrale', 'Référence cadastrale']);
		await expect(panneau.locator('dd')).toHaveText([`450${NB}m²`, '00000 000 ZA 0001']);
		const centreAdresse = await centre(point);
		const choisie = await centre(selection);
		expect(Math.abs(choisie.x - centreAdresse.x)).toBeLessThan(2);
		expect(Math.abs(choisie.y - centreAdresse.y)).toBeLessThan(2);
		// Opening announces nothing: the status line speaks when the visitor chooses.
		const etat = dialogue.locator('[aria-live="polite"]');
		await expect(etat).toHaveText('');
		await page.screenshot({ path: preuve(testInfo, 'carte-375-ouverture.png'), animations: 'disabled' });

		// Touching the parcel north of the address: the selection moves, the panel follows, the line says it.
		const nord = await parcelleVers(page, { x: centreAdresse.x, y: centreAdresse.y - 76 });
		expect(nord.y).toBeLessThan(centreAdresse.y - 40);
		await page.mouse.click(nord.x, nord.y);
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0003');
		await expect(selection).toHaveCount(1);
		const deplacee = await centre(selection);
		expect(Math.abs(deplacee.x - nord.x)).toBeLessThan(2);
		expect(Math.abs(deplacee.y - nord.y)).toBeLessThan(2);
		await expect(etat).toHaveText('Parcelle ZA 0003 sélectionnée.');
		// It is not the parcel of the address, and its surface is not served: the glossary says so.
		await expect(panneau.getByText('Parcelle de l’adresse', { exact: true })).toHaveCount(0);
		const surface = panneau.locator('dd').first();
		await expect(surface.locator('[data-state="not_reported"]')).toHaveText('Non renseigné');
		await expect(surface).not.toContainText('m²');
		await expect(panneau.locator('dd').nth(1)).toHaveText('00000 000 ZA 0003');
		// The choice takes the focus nowhere: it stays on the map that was touched, never sent to the panel;
		// neither the body of the dialog nor the fiche behind it scrolled.
		await expect(dialogue.locator('.carte-vue')).toBeFocused();
		await expect(panneau.getByRole('heading', { level: 3 })).not.toBeFocused();
		expect(await dialogue.locator('.carte-corps').evaluate((corps) => corps.scrollTop)).toBe(0);
		expect(await page.evaluate(() => window.scrollY)).toBe(defilementFiche);
		await page.screenshot({ path: preuve(testInfo, 'carte-375-parcelle-choisie.png'), animations: 'disabled' });

		// A parcel with a served surface, east of the address.
		const est = await parcelleVers(page, { x: centreAdresse.x + 83, y: centreAdresse.y });
		await page.mouse.click(est.x, est.y);
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0002');
		await expect(panneau.locator('dd').first()).toHaveText(`612${NB}m²`);
		await expect(etat).toHaveText('Parcelle ZA 0002 sélectionnée.');

		// Zooming in asks the tiles of the next zoom and lets those of the former one go: each parcel stays
		// drawn once, carried by the tiles that remain, and the selection holds.
		// The tiles of the parcels layer only: the ground has its own tiles since 04.10-06.
		const tuiles = dialogue.locator('.carte-vue .couche-parcelles .leaflet-tile');
		const auZoom = (z: number) => new Set(routes.demandes.parcelles.filter((chemin) => chemin.startsWith(`/carte/parcelles/${z}/`)));
		await expect(tuiles).toHaveCount(auZoom(18).size);
		await page.keyboard.press('+');
		await expect.poll(() => auZoom(19).size).toBeGreaterThan(0);
		await expect.poll(async () => (await tuiles.count()) === auZoom(19).size, { message: 'les tuiles du zoom 18 sont parties' }).toBe(true);
		await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
		await expect(selection).toHaveCount(1);
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0002');

		// Escape closes, the focus goes back to the button.
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		await expect(bouton).toBeFocused();

		// Reopened, the dialog starts again from the address.
		await bouton.click();
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
		await dialogue.getByRole('button', { name: 'Fermer' }).click();
		await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		await expect(bouton).toBeFocused();
	});

	test('une réponse refusée ou en panne se dit : jamais un chargement sans fin, jamais une carte vide muette', async ({ page }) => {
		await ouvrirFiche(page);
		const bouton = page.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
		const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
		const cadre = dialogue.locator('.carte-cadre');
		const etat = dialogue.locator('[aria-live="polite"]');
		const fermer = async () => {
			await dialogue.getByRole('button', { name: 'Fermer' }).click();
			await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		};
		const demandees: string[] = [];
		page.on('request', (requete) => demandees.push(requete.url()));

		// The context does not answer: the frame says the outage of the glossary, and Leaflet is not asked.
		routes.repondre('contexte', { status: 503, json: { title: 'Adresse momentanement indisponible', code: 'adresse_indisponible' } });
		await bouton.click();
		await expect(cadre.locator('[role="status"] [data-state="temporary_error"]')).toContainText('Indisponible');
		await expect(dialogue.getByText('Chargement de la carte…')).toHaveCount(0);
		await expect(dialogue.locator('section.parcelle')).toHaveCount(0);
		// Since 04.10-12 the dialog comes on the click, with the `?url` modules of its loader in dev (one
		// string each, the address of a file): Leaflet itself is still never asked.
		expect(demandees.filter((url) => /leaflet/i.test(url) && new URL(url).search !== '?url')).toEqual([]);
		await fermer();

		// The context is rate limited: a limit with its served delay, never an outage nor a quota.
		routes.repondre('contexte', { status: 429, json: { title: 'Trop de demandes' }, headers: { 'Retry-After': '42' } });
		await bouton.click();
		await expect(etat).toHaveText(/^La limite de requêtes est atteinte\. Réessayez dans 42\ssecondes\.$/);
		await expect(dialogue.getByText('Chargement de la carte…')).toHaveCount(0);
		await expect(dialogue.locator('[data-state="temporary_error"]')).toHaveCount(0);
		await fermer();

		// The context answers, no tile of parcels does: the map is there, the outage is said under it.
		routes.repondre('contexte', { status: 200, json: EXEMPLE_CONTEXTE });
		routes.repondre('parcelles', { status: 503, json: { title: 'Parcelles momentanement indisponibles' } });
		await bouton.click();
		await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
		// 04.10-06: the outage of the tiles of parcels is said by T-34 in the status line, with its relaunch.
		await expect(etat).toHaveText('Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.');
		await expect(dialogue.getByRole('button', { name: 'Recharger les parcelles' })).toBeVisible();
		await expect(dialogue.locator('path.parcel')).toHaveCount(0);
		await expect(dialogue.locator('.point-adresse')).toHaveCount(1);
		await fermer();

		// The tiles are rate limited: the limit and its delay, in the status line.
		routes.repondre('parcelles', { status: 429, json: { title: 'Trop de demandes' }, headers: { 'Retry-After': '7' } });
		await bouton.click();
		await expect(etat).toHaveText(/^La limite de requêtes est atteinte\. Réessayez dans 7\ssecondes\.$/);
		await expect(dialogue.locator('[data-state="temporary_error"]')).toHaveCount(0);
		await fermer();
	});

	test('le HTML servi de la fiche ne porte aucune parcelle voisine', async ({ page }) => {
		const reponse = await page.request.get(`/adresse/${BAN_ID}`);
		expect(reponse.ok()).toBe(true);
		const html = await reponse.text();
		// Non-vacuity: this is the fiche of the address, and the neighbours are known to the fixture.
		expect(html).toContain(BAN_ID);
		expect(VOISINES).toHaveLength(3);
		for (const id of VOISINES) expect(html, `parcelle voisine ${id} dans le HTML servi`).not.toContain(id);
		// The routes installed for this page were not asked by a mere read of the HTML.
		expect(routes.demandes.contexte).toEqual([]);
		expect(routes.demandes.parcelles).toEqual([]);
	});

	test('sans carte servie : ni image ni bouton', async ({ page, request }) => {
		await configurer(request, null);
		await ouvrirFiche(page);
		await expect(page.locator('.adresse-carte')).toHaveCount(0);
		await expect(page.locator('.carte-action')).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Ouvrir la carte des parcelles' })).toHaveCount(0);
	});
});

test('sans JavaScript : pas de bouton, la rangée d’action garde ses 44 px, l’image reste', async ({ browser, request }) => {
	const contexte = await browser.newContext({ javaScriptEnabled: false, baseURL: E2E_APP_ORIGIN, viewport: { width: 375, height: 800 } });
	try {
		const page = await contexte.newPage();
		await installLocalNetworkGuard(page);
		const routes = await installerRoutesCarte(page);
		await configurer(request);
		await page.goto(`/adresse/${BAN_ID}`);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('3 rue des Parcelles');
		const carte = page.locator('.adresse-carte figure.carte');
		await expect(carte.locator('img')).toHaveCount(2);
		const rangee = carte.locator('.carte-action');
		await expect(rangee).toHaveCount(1);
		expect(Math.round((await rangee.boundingBox())!.height)).toBe(44);
		await expect(page.getByRole('button', { name: 'Ouvrir la carte des parcelles' })).toHaveCount(0);
		await expect(carte.locator('button')).toHaveCount(0);
		expect(routes.demandes.contexte).toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
		attendreAucuneCarteNonReconnue(page);
	} finally {
		await contexte.close();
	}
});

// ---------------------------------------------------------------------------------------------------
// 04.10-06: the whole map. Ground, controls, sold marks and point (task 1); status line, legend, limits
// and the panel of sources (task 2); outages of tiles, rate limit and the failed load of Leaflet (task 3).
// ---------------------------------------------------------------------------------------------------

const T = {
	// {adresse} of T-03 is the title of the fiche, as in the alternative text of the tracer image.
	nom: 'Carte des parcelles autour de l’adresse 3 rue des Parcelles',
	zoomAvant: 'Zoom avant',
	zoomArriere: 'Zoom arrière',
	retour: 'Revenir à l’adresse',
	legendeAdresse: 'Parcelle de l’adresse',
	legendeVentes: 'Au moins une vente (2021-2025)',
	limites: `Ventes connues${NB}: 2021-2025. Cadastre${NB}: 2026-07. Fond${NB}: © IGN (Plan IGN), Licence Ouverte 2.0.`,
	fondEnEchec: 'Une partie du fond de carte n’a pas pu être chargée.',
	parcellesEnEchec: 'Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.',
	carteEnEchec: 'La carte n’a pas pu être chargée. La liste des parcelles reste disponible.',
	rechargerCarte: 'Recharger la carte',
	rechargerParcelles: 'Recharger les parcelles',
	// The existing text of the outage of sales (`MESSAGE_PANNE_COMPARABLES`), reused as is.
	panneVentes: "Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes."
};
/** Parcels of the example with at least one sale over the period: each carries a mark. */
const VENDUES = PARCELLES.filter((p) => (p.properties.ventes ?? 0) >= 1).length;
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const copie = (valeur: unknown): any => structuredClone(valeur);

async function ouvrirCarte(page: Page) {
	await ouvrirFiche(page);
	const bouton = page.getByRole('button', { name: 'Ouvrir la carte des parcelles' });
	await bouton.click();
	const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
	await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
	return { bouton, dialogue, etat: dialogue.locator('[aria-live="polite"]'), vue: dialogue.locator('.carte-vue') };
}

/** Zoom of the tiles of parcels asked last: the zoom the map is at. */
const dernierZoom = (routes: RoutesCarte) => Number(routes.demandes.parcelles.at(-1)!.split('/')[3]);

async function glisser(page: Page, depuis: { x: number; y: number }, dx: number, dy: number) {
	await page.mouse.move(depuis.x, depuis.y);
	await page.mouse.down();
	await page.mouse.move(depuis.x + dx / 2, depuis.y + dy / 2, { steps: 5 });
	await page.mouse.move(depuis.x + dx, depuis.y + dy, { steps: 5 });
	await page.mouse.up();
}

const titrePanneau = (dialogue: Locator) => dialogue.locator('section.parcelle').getByRole('heading', { level: 3 });

for (const largeur of [375, 1440]) {
	test.describe(`Carte des parcelles — carte complète à ${largeur} px`, () => {
		let routes: RoutesCarte;
		let erreurs: string[];

		test.beforeEach(async ({ page, request }) => {
			erreurs = [];
			page.on('pageerror', (erreur) => erreurs.push(erreur.message));
			await page.setViewportSize({ width: largeur, height: 900 });
			await installLocalNetworkGuard(page);
			routes = await installerRoutesCarte(page);
			await configurer(request);
		});

		test.afterEach(async ({ page }) => {
			expect(erreurs, 'aucune erreur de script dans la page').toEqual([]);
			expectNoUnexpectedOffLocalRequest(page);
			attendreAucuneCarteNonReconnue(page);
		});

		test('fond de plan, zoom par boutons, retour à l’adresse, pastilles et clavier', async ({ page }, testInfo) => {
			const leaflet: string[] = [];
			page.on('request', (requete) => {
				if (/leaflet/i.test(requete.url())) leaflet.push(requete.url());
			});
			const { bouton, dialogue, etat, vue } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);

			// Ground: asked on our origin, by the template of the contract, at the opening zoom; empty alt;
			// desaturated like the picture of the fiche (C10).
			await expect.poll(() => routes.demandes.fond.length).toBeGreaterThan(0);
			for (const chemin of routes.demandes.fond) expect(chemin).toMatch(/^\/carte\/fond\/18\/\d+\/\d+\.png$/);
			const fond = dialogue.locator('.carte-fond img.leaflet-tile');
			await expect(fond.first()).toBeVisible();
			for (const tuile of await fond.all()) await expect(tuile).toHaveAttribute('alt', '');
			expect(await fond.first().evaluate((img) => getComputedStyle(img).filter)).toContain('grayscale(1)');

			// The map is one tab stop, a named group with its description; no text node inside Leaflet.
			await expect(vue).toHaveAttribute('role', 'group');
			await expect(vue).toHaveAccessibleName(T.nom);
			await expect(vue).toHaveAccessibleDescription(/^Les flèches déplacent la carte/);
			const textes = await vue.evaluate((el) => {
				const marche = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
				const lus: string[] = [];
				while (marche.nextNode()) if (marche.currentNode.textContent!.trim()) lus.push(marche.currentNode.textContent!);
				return lus;
			});
			expect(textes, 'aucun texte dans le conteneur Leaflet').toEqual([]);
			for (const svg of await vue.locator('svg').all()) {
				expect(await svg.evaluate((s) => s.closest('[aria-hidden="true"]') !== null), 'SVG masqué aux lecteurs d’écran').toBe(true);
			}

			// Sold marks: one per parcel with a sale over the period, letting touches through, like the point.
			const pastilles = dialogue.locator('.parcel-sold-mark');
			await expect(pastilles).toHaveCount(VENDUES);
			expect(await pastilles.first().evaluate((m) => getComputedStyle(m).pointerEvents)).toBe('none');
			expect(await dialogue.locator('.point-adresse').evaluate((m) => getComputedStyle(m).pointerEvents)).toBe('none');

			// Zoom controls: 44 × 44, named, glyph hidden; tab order map, +, −; the return button is absent.
			const plus = dialogue.getByRole('button', { name: T.zoomAvant, exact: true });
			const moins = dialogue.getByRole('button', { name: T.zoomArriere, exact: true });
			const retour = dialogue.getByRole('button', { name: T.retour });
			for (const commande of [plus, moins]) {
				const boite = (await commande.boundingBox())!;
				expect([Math.round(boite.width), Math.round(boite.height)]).toEqual([44, 44]);
				await expect(commande.locator('[aria-hidden="true"]')).toHaveCount(1);
			}
			await expect(retour).toHaveCount(0);
			await vue.focus();
			await page.keyboard.press('Tab');
			await expect(plus).toBeFocused();
			await page.keyboard.press('Tab');
			await expect(moins).toBeFocused();
			// The focus ring of the map is drawn inside its frame.
			expect(await vue.evaluate((el) => getComputedStyle(el).outlineOffset)).toBe('-3px');

			// Bounds 16 and 19, opening at 18 (C11).
			await plus.click();
			await expect.poll(() => dernierZoom(routes)).toBe(19);
			await expect(plus).toBeDisabled();
			await moins.click();
			await moins.click();
			await moins.click();
			await expect.poll(() => dernierZoom(routes)).toBe(16);
			await expect(moins).toBeDisabled();
			await expect(plus).toBeEnabled();
			// The wheel zooms when the pointer is on the map (C13).
			const centreVue = await centre(vue);
			// Leaflet drops a wheel that comes during its zoom animation (the class is on its map pane): wait for
			// the last step of the buttons to end (matrix of 04.10-10).
			await expect(vue.locator('.leaflet-zoom-anim')).toHaveCount(0);
			await page.mouse.move(centreVue.x, centreVue.y);
			await page.mouse.wheel(0, -300);
			// Zoomed in: « Zoom arrière » is no longer at its bound. Read on the button, not on the tiles
			// asked: how far one wheel event zooms depends on the engine (Leaflet scales the wheel by
			// engine), and the tiles of a zoom already visited may not be asked again (matrix of 04.10-10).
			await expect(moins).toBeEnabled();
			// Back to the opening zoom, by the buttons, whatever the wheel did: down to the bound 16, then 18.
			while (await moins.isEnabled()) await moins.click();
			await expect.poll(() => dernierZoom(routes)).toBe(16);
			await plus.click();
			await plus.click();
			await expect.poll(() => dernierZoom(routes)).toBe(18);
			// Moving and zooming keep the selection.
			await expect(titrePanneau(dialogue)).toHaveText('Parcelle ZA 0001');

			// A drag never selects: started on another parcel, the selection holds.
			const point = dialogue.locator('.point-adresse');
			const centreAdresse = await centre(point);
			const est = await parcelleVers(page, { x: centreAdresse.x + 83, y: centreAdresse.y });
			await glisser(page, est, -60, 0);
			await expect(titrePanneau(dialogue)).toHaveText('Parcelle ZA 0001');

			// Moved until the point leaves the view: the return button comes, and brings the map back.
			const cadre = (await vue.boundingBox())!;
			// Three drags, and up to three more: in WebKit a drag sometimes does not start (matrix of 04.10-10).
			for (let i = 0; i < 6 && (i < 3 || !(await retour.isVisible())); i++) {
				await glisser(page, { x: cadre.x + cadre.width - 30, y: cadre.y + cadre.height / 2 }, -(cadre.width - 60), 0);
			}
			await expect(retour).toBeVisible();
			expect(Math.round((await retour.boundingBox())!.height)).toBe(44);
			// The selection holds, even out of the view.
			await expect(titrePanneau(dialogue)).toHaveText('Parcelle ZA 0001');
			await page.screenshot({ path: preuve(testInfo, `carte-${largeur}-hors-adresse.png`), animations: 'disabled' });
			await retour.click();
			await expect(retour).toHaveCount(0);
			const recentre = await centre(point);
			const milieu = await centre(vue);
			expect(Math.abs(recentre.x - milieu.x)).toBeLessThan(3);
			expect(Math.abs(recentre.y - milieu.y)).toBeLessThan(3);
			await page.screenshot({ path: preuve(testInfo, `carte-${largeur}-complete.png`), animations: 'disabled' });

			if (largeur > 720) {
				// Without reduced motion, Leaflet animates its zoom (non-vacuity of the reduced-motion scenario).
				expect(await dialogue.locator('.leaflet-zoom-animated').count()).toBeGreaterThan(0);
				// On a desktop, a click on the backdrop closes; reopened, the map starts again from the address
				// without asking the module of Leaflet again.
				const demandesLeaflet = leaflet.length;
				await page.mouse.click(5, 5);
				await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
				await bouton.click();
				await expect(titrePanneau(dialogue)).toHaveText('Parcelle ZA 0001');
				await expect(dialogue.locator('path.parcel-selected')).toHaveCount(1);
				expect(leaflet.length, 'Leaflet n’est pas redemandé').toBe(demandesLeaflet);
			}
			await expect(etat).not.toContainText(/quota|panne/i);
		});

		test('ligne d’état, légende, ligne de limites et volet des sources par-dessus la carte', async ({ page }) => {
			// No inertia after the drags below (the product honours reduced motion): in Firefox and WebKit it
			// carried the view off the parcels; this scenario is about the status line, not the animation.
			await page.emulateMedia({ reducedMotion: 'reduce' });
			const { dialogue, etat } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);

			// Legend: one word per form, with its sample hidden from screen readers.
			const legende = dialogue.locator('ul.carte-legende > li');
			await expect(legende).toHaveText([T.legendeAdresse, T.legendeVentes]);
			for (const item of await legende.all()) await expect(item.locator('svg[aria-hidden="true"]')).toHaveCount(1);

			// Limits line: served period, vintage, credit and licence; the detail opens the panel over the map.
			await expect(dialogue.locator('.carte-limites')).toContainText(T.limites);
			const detail = dialogue.getByRole('button', { name: /^Détail\s:\ssources et limites de la carte$/ });
			const axe1 = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe1.violations, JSON.stringify(axe1.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
			await detail.click();
			const volet = page.getByRole('dialog', { name: 'Sources et limites de la carte' });
			await expect(volet).toBeVisible();
			// axe measures colours: the panel is read once its opening is over, never in the middle of a fade.
			await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
			await expect(volet.locator('dt')).toHaveText(['Parcelles', 'Surface', 'Ventes', 'Vendues ensemble', 'Prix', 'Ce que la carte ne dit pas', 'Fond']);
			// R1 (2026-11-15): the base map names its source page, as the Licence Ouverte asks; a new tab, no referrer.
			const source = volet.getByRole('link', { name: 'Plan IGN' });
			await expect(source).toHaveAttribute('href', 'https://cartes.gouv.fr/rechercher-une-donnee/dataset/IGNF_PLAN-IGN');
			await expect(source).toHaveAttribute('rel', 'noopener noreferrer');
			await expect(volet.locator('dd').first()).toContainText('2026-07');
			await expect(volet.locator('dd').nth(2)).toContainText('2021-2025');
			await expect(volet.locator('dd').nth(4)).toContainText('pour ne pas désigner un bien et son acheteur');
			const axe2 = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe2.violations, JSON.stringify(axe2.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
			await page.keyboard.press('Escape');
			await expect(volet).toBeHidden();
			await expect(detail).toBeFocused();
			await expect(dialogue).toBeVisible();

			// The status line changes at most once a second, whatever the number of moves. A parcel is chosen
			// first, so that the moves have an announcement to drop.
			const centreAdresse = await centre(dialogue.locator('.point-adresse'));
			const est = await parcelleVers(page, { x: centreAdresse.x + 83, y: centreAdresse.y });
			await page.mouse.click(est.x, est.y);
			await expect(etat).toHaveText('Parcelle ZA 0002 sélectionnée.');
			await dialogue.locator('.carte-vue').evaluate((vue) => {
				const ligne = vue.closest('dialog')!.querySelector('[aria-live="polite"]')!;
				const w = window as unknown as { changements: number[] };
				w.changements = [];
				new MutationObserver(() => w.changements.push(performance.now())).observe(ligne, { childList: true, characterData: true, subtree: true });
			});
			const cadre = (await dialogue.locator('.carte-vue').boundingBox())!;
			for (let i = 0; i < 6; i++) await glisser(page, { x: cadre.x + cadre.width / 2, y: cadre.y + cadre.height / 2 }, i % 2 ? 120 : -120, 0);
			// The moves dropped the announcement of the choice; the line then says the count of the view (T-29, 04.10-08).
			await expect(etat).toHaveText('4 parcelles sur la carte, dont 3 avec une vente (2021-2025).');
			const changements = await page.evaluate(() => (window as unknown as { changements: number[] }).changements);
			expect(changements.length, 'la ligne a parlé pendant les déplacements').toBeGreaterThan(0);
			for (let i = 1; i < changements.length; i++) expect(changements[i] - changements[i - 1]).toBeGreaterThanOrEqual(950);
		});

		test('parcelle de l’adresse non unique, ventes hors couverture', async ({ page }) => {
			const contexte = copie(EXEMPLE_CONTEXTE);
			contexte.donnees.parcelle_adresse = { etat: 'not_available', id_parcelle: null, motif: 'parcelles_multiples', nombre_parcelles: 3 };
			contexte.donnees.ventes = { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre', periode: null, annee_debut: null };
			const tuile = copie(EXEMPLE_TUILE);
			tuile.donnees.ventes = { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre', periode: null, annee_debut: null };
			for (const p of tuile.donnees.collection.features) p.properties.ventes = null;
			routes.repondre('contexte', { status: 200, json: contexte });
			routes.repondre('parcelles', { status: 200, json: tuile });
			const { dialogue } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			// Several parcels: the served sentence, no thick outline, no opening selection, the panel asks to choose.
			await expect(dialogue.locator('.carte-motif-parcelle')).toHaveText(`Le point de l'adresse touche 3 parcelles${String.fromCharCode(0x202f)}; aucune n'est choisie.`);
			await expect(dialogue.locator('path.parcel-address')).toHaveCount(0);
			await expect(dialogue.locator('path.parcel-selected')).toHaveCount(0);
			await expect(dialogue.getByText('Choisissez une parcelle sur la carte ou dans la liste.')).toBeVisible();
			// Sales out of coverage: no mark, the item leaves the legend, the state says it with its reason.
			await expect(dialogue.locator('.parcel-sold-mark')).toHaveCount(0);
			await expect(dialogue.locator('ul.carte-legende > li')).toHaveText([T.legendeAdresse]);
			await expect(dialogue.locator('.carte-legende-bloc [data-state="out_of_coverage"]')).toContainText('département non hébergé');
			await expect(dialogue.locator('.carte-limites')).toContainText(`Ventes connues${NB}: Non renseigné.`);
		});

		test('une tuile de fond en échec se dit et se relance ; les parcelles restent', async ({ page }) => {
			routes.repondre('fond', { status: 503, body: '' });
			const { dialogue, etat } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(dialogue.locator('.carte-fond .tile-failed').first()).toBeAttached();
			await expect(etat).toHaveText(T.fondEnEchec);
			routes.repondre('fond', { status: 200, body: pngGris(256), contentType: 'image/png' });
			await dialogue.getByRole('button', { name: T.rechargerCarte }).click();
			await expect(dialogue.locator('.carte-fond .tile-failed')).toHaveCount(0);
			await expect(etat).not.toHaveText(T.fondEnEchec);
			await expect(dialogue.getByRole('button', { name: T.rechargerCarte })).toHaveCount(0);
		});

		test('une tuile de parcelles en échec n’est pas une zone vide ; seule elle est relancée', async ({ page }) => {
			let enEchec: string | null = null;
			routes.repondre('parcelles', (url) => {
				enEchec ??= url.pathname;
				return url.pathname === enEchec
					? { status: 503, json: { title: 'Parcelles momentanement indisponibles' } }
					: { status: 200, json: EXEMPLE_TUILE };
			});
			const { dialogue, etat } = await ouvrirCarte(page);
			await expect(dialogue.locator('.couche-parcelles .tile-failed')).toHaveCount(1);
			await expect(etat).toHaveText(T.parcellesEnEchec);
			// The other tiles keep their parcels; nothing says « no parcel ».
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(dialogue.getByText(/Aucune parcelle/)).toHaveCount(0);
			const avant = routes.demandes.parcelles.length;
			routes.repondre('parcelles', { status: 200, json: EXEMPLE_TUILE });
			await dialogue.getByRole('button', { name: T.rechargerParcelles }).click();
			await expect(dialogue.locator('.couche-parcelles .tile-failed')).toHaveCount(0);
			expect(routes.demandes.parcelles.slice(avant)).toEqual([enEchec]);
			await expect(etat).not.toHaveText(T.parcellesEnEchec);
		});

		test('ventes d’une tuile en panne : parcelles tracées sans pastille, relance, puis pastilles', async ({ page }) => {
			const tuile = copie(EXEMPLE_TUILE);
			tuile.donnees.ventes = { etat: 'temporary_error', motif: null, periode: '2021-2025', annee_debut: 2021 };
			for (const p of tuile.donnees.collection.features) p.properties.ventes = null;
			routes.repondre('parcelles', { status: 200, json: tuile, headers: { 'Cache-Control': 'no-store' } });
			const { dialogue, etat } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(dialogue.locator('.parcel-sold-mark')).toHaveCount(0);
			await expect(etat).toHaveText(T.panneVentes);
			const avant = routes.demandes.parcelles.length;
			routes.repondre('parcelles', { status: 200, json: EXEMPLE_TUILE });
			await dialogue.getByRole('button', { name: T.rechargerParcelles }).click();
			await expect(dialogue.locator('.parcel-sold-mark')).toHaveCount(VENDUES);
			expect(routes.demandes.parcelles.length, 'la relance redemande les tuiles au réseau').toBeGreaterThan(avant);
			await expect(etat).not.toHaveText(T.panneVentes);
		});

		test('limite de requêtes sur les tuiles : le délai servi, les parcelles lues restent', async ({ page }) => {
			// No inertia after the drag (the product honours reduced motion): in WebKit it carried the view
			// past the tiles read (matrix of 04.10-10); this scenario is about the limit, not the animation.
			await page.emulateMedia({ reducedMotion: 'reduce' });
			const { dialogue, etat } = await ouvrirCarte(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			routes.repondre('parcelles', { status: 429, json: { title: 'Trop de demandes' }, headers: { 'Retry-After': '7' } });
			const avant = routes.demandes.parcelles.length;
			const cadre = (await dialogue.locator('.carte-vue').boundingBox())!;
			// A move short enough that the tiles already read stay in view (Leaflet drops the tiles it leaves).
			const pas = Math.min(cadre.width - 40, 300);
			await glisser(page, { x: cadre.x + cadre.width - 20, y: cadre.y + cadre.height / 2 }, -pas, 0);
			await expect.poll(() => routes.demandes.parcelles.length).toBeGreaterThan(avant);
			await expect(etat).toHaveText(/^La limite de requêtes est atteinte\. Réessayez dans 7\ssecondes\.$/);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(dialogue).not.toContainText(/quota|panne/i);
		});

		test('Leaflet en échec : T-38 et sa relance ; pendant le chargement, T-04 puis T-05', async ({ page }) => {
			let couper = true;
			let liberer!: () => void;
			const retenue = new Promise<void>((r) => (liberer = r));
			// The script and the style sheet of Leaflet themselves, not the `?url` modules the fiche loads in dev.
			await page.route((url) => url.search !== '?url' && /\/leaflet(-[\w-]+)?\.(js|css)$/.test(url.pathname), async (route) => {
				if (couper) return route.abort();
				await retenue;
				return route.fallback();
			});
			await ouvrirFiche(page);
			await page.getByRole('button', { name: 'Ouvrir la carte des parcelles' }).click();
			const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
			const cadre = dialogue.locator('.carte-cadre');
			await expect(cadre.locator('[role="status"]')).toHaveText(T.carteEnEchec);
			// 04.10-09: without the map, the panel answers over the bounds of the opening (UI contract § 3).
			await expect(dialogue.locator('section.parcelle').getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
			await expect(dialogue.locator('.leaflet-container')).toHaveCount(0);
			couper = false;
			// The parcels are held while the map loads again: the frame says T-04, then the status line T-05.
			let lacher!: () => void;
			const parcellesRetenues = new Promise<void>((r) => (lacher = r));
			await page.route(/\/carte\/parcelles\//, async (route) => {
				await parcellesRetenues;
				return route.fallback();
			});
			await cadre.getByRole('button', { name: T.rechargerCarte }).click();
			await expect(cadre.locator('[role="status"]')).toHaveText('Chargement de la carte…');
			liberer();
			await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
			await expect(cadre.locator('[role="status"]')).toHaveCount(0);
			await expect(dialogue.locator('[aria-live="polite"]')).toHaveText('Chargement des parcelles…');
			lacher();
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
		});
	});
}

test('mouvement réduit : Leaflet n’anime jamais son zoom', async ({ page, request }) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.setViewportSize({ width: 1440, height: 900 });
	await installLocalNetworkGuard(page);
	const routes = await installerRoutesCarte(page);
	await configurer(request);
	const { dialogue } = await ouvrirCarte(page);
	await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
	await dialogue.getByRole('button', { name: T.zoomAvant, exact: true }).click();
	await expect.poll(() => dernierZoom(routes)).toBe(19);
	// `leaflet-zoom-animated` says nothing (Leaflet sets it on tile containers and SVG renderers whatever the
	// option); the proxy of the zoom animation exists only when Leaflet animates its zoom.
	await expect(dialogue.locator('.leaflet-proxy')).toHaveCount(0);
	await expect(dialogue.locator('.leaflet-zoom-anim')).toHaveCount(0);
	expectNoUnexpectedOffLocalRequest(page);
	attendreAucuneCarteNonReconnue(page);
});
