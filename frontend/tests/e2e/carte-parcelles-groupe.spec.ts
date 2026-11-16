// Parcels sold together, drawn on the map of the parcels (04.10-09, LIEU-01, LIEU-02, SEM-01, UI-01). The
// parcels and the sales come from the routes of the map on the origin of the site (the contract examples
// of 04.10-02); the large group is an answer of the sales route replaced by the test.
//
// What this spec locks:
//   - the toggle of a sale of several parcels draws the served parcels of its group (dashed and filled,
//     under the address and the selection), frames the map on them between the zooms 16 and 18, adds
//     « Vendues ensemble » to the legend and a badge to their lines; turning it off, or choosing another
//     parcel, takes all of it away;
//   - what the map leaves out of the view (and what the service left out of the bounds) is counted and
//     said, and the count follows a move; a tile of parcels in outage leaves no hole in the group;
//   - without the module of Leaflet, the list and the panel work over the frame of the opening, read from
//     the same tiles Leaflet would have asked (task 2);
//   - the back button of the browser closes the map without leaving the fiche, and a closing from the
//     dialog consumes the entry it added (C8, task 3).
// Synthetic SSR source, headless runner, isolated context (AGENTS.md).
import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import {
	EXEMPLE_CONTEXTE,
	EXEMPLE_TUILE,
	EXEMPLE_VENTES,
	attendreAucuneCarteNonReconnue,
	installerRoutesCarte,
	type RoutesCarte
} from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, rapportAvecThemes, REQUETE_RAPPORT } from './fixtures/rapport';

const NB = String.fromCharCode(0xa0);
const LECTURES = 40;

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

const BOUTON_CARTE = 'Ouvrir la carte des parcelles';
const PARCELLES = EXEMPLE_TUILE.donnees.collection.features;

function parties(dialogue: Locator) {
	const panneau = dialogue.locator('section.parcelle');
	const ventes = panneau.locator('section.parcelle-ventes');
	const liste = dialogue.locator('section.parcelles');
	return {
		dialogue,
		etat: dialogue.locator('[aria-live="polite"]'),
		vue: dialogue.locator('.carte-vue'),
		panneau,
		ventes,
		cartes: ventes.locator('.ventes-cartes > li'),
		legende: dialogue.locator('ul.carte-legende > li'),
		liste,
		lignes: liste.locator('ul.parcelles-lignes > li > button'),
		groupe: dialogue.locator('path.parcel-group')
	};
}

async function ouvrirCarte(page: Page) {
	await ouvrirFiche(page);
	await page.getByRole('button', { name: BOUTON_CARTE }).click();
	const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
	await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
	await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
	const p = parties(dialogue);
	await expect(p.panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
	await expect(p.cartes).toHaveCount(4);
	return p;
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

/** References of the lines that carry the badge « Vendue ensemble ». */
const lignesDuGroupe = (lignes: Locator) =>
	lignes.evaluateAll((boutons) =>
		boutons
			.filter((b) => [...b.querySelectorAll('.ligne-badge')].some((badge) => badge.textContent?.trim() === 'Vendue ensemble'))
			.map((b) => b.querySelector('.ligne-ref')!.textContent!.trim())
	);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const copie = (): any => structuredClone(EXEMPLE_VENTES);

/** A square parcel of the large group, on the row of the address, *decalage* degrees east (or west) of it. */
function parcelleDuGroupe(decalage: number, rang: number) {
	const lon = -3.5 + decalage;
	const id = `00000000ZB${String(rang + 1).padStart(4, '0')}`;
	const anneau = [
		[lon - 0.0002, 46.49986],
		[lon + 0.0002, 46.49986],
		[lon + 0.0002, 46.50014],
		[lon - 0.0002, 46.50014],
		[lon - 0.0002, 46.49986]
	];
	return { id_parcelle: id, geometry: { type: 'MultiPolygon', coordinates: [[anneau]] }, etiquette: [lon, 46.5] };
}

/**
 * The second sale of the example, sold with twelve other parcels: one is gone from the cadastre, three are
 * out of the bounds of the address, nine are served and drawn: the parcel of the address and eight others
 * spread over about 2 km east to west (more than a view at zoom 16 holds at 375 px), four of them close
 * together some 400 m east, so that a move east brings several into the view at once.
 */
function venteDeTreize() {
	const corps = copie();
	const adresse = corps.donnees.groupes[1].parcelles[0];
	corps.donnees.groupes[1] = {
		total: 13,
		autres: 12,
		absentes_du_cadastre: 1,
		hors_emprise: 3,
		surface_totale_m2: null,
		parcelles: [adresse, ...[-0.012, -0.009, -0.006, 0.005, 0.0055, 0.006, 0.0065, 0.012].map(parcelleDuGroupe)]
	};
	return corps;
}

const T = {
	bascule: 'Montrer ces parcelles',
	legende: 'Vendues ensemble',
	badge: 'Vendue ensemble',
	reserve: 'Ne dit pas qui les possède aujourd’hui.',
	parcellesEnEchec: 'Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.'
};
const HORS_VUE = /^(\d+) de ces parcelles sont hors de la vue\.$/;

/** The number T-26 says, or null when the panel does not say it. */
async function horsVueDit(panneau: Locator): Promise<number | null> {
	const textes = await panneau.locator('.vente-groupe p').allTextContents();
	for (const texte of textes) {
		const m = HORS_VUE.exec(texte.trim());
		if (m) return Number(m[1]);
		if (texte.trim() === '1 de ces parcelles est hors de la vue.') return 1;
	}
	return null;
}

function controlerFin(page: Page, erreurs: string[]) {
	expect(erreurs, 'aucune erreur de script dans la page').toEqual([]);
	expectNoUnexpectedOffLocalRequest(page);
	attendreAucuneCarteNonReconnue(page);
}

for (const largeur of [375, 1440]) {
	test.describe(`Carte des parcelles — vendues ensemble à ${largeur} px`, () => {
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

		test.afterEach(async ({ page }) => controlerFin(page, erreurs));

		test('la bascule trace le groupe sous l’adresse et la sélection, le légende et le marque dans la liste ; l’éteindre retire tout', async ({ page }) => {
			const { dialogue, cartes, legende, lignes, groupe, panneau } = await ouvrirCarte(page);
			await expect(groupe).toHaveCount(0);
			await expect(legende).toHaveCount(2);
			expect(await lignesDuGroupe(lignes)).toEqual([]);

			const bascule = cartes.nth(1).getByRole('button', { name: T.bascule });
			await bascule.click();
			await expect(bascule).toHaveAttribute('aria-pressed', 'true');

			// The two served parcels of the sale, dashed and filled, with the tokens of the Price category.
			await expect(groupe).toHaveCount(2);
			const trait = await groupe.first().evaluate((chemin) => {
				const style = getComputedStyle(chemin);
				const jeton = (nom: string) => {
					const i = document.createElement('i');
					i.style.color = `var(${nom})`;
					chemin.closest('dialog')!.append(i);
					const couleur = getComputedStyle(i).color;
					i.remove();
					return couleur;
				};
				const rang = (selecteur: string) => Number((document.querySelector(selecteur)?.closest('.leaflet-pane') as HTMLElement | null)?.style.zIndex ?? NaN);
				return {
					stroke: style.stroke,
					fill: style.fill,
					tirets: style.strokeDasharray,
					largeur: style.strokeWidth,
					touche: style.pointerEvents,
					encre: jeton('--color-prix-encre'),
					fond: jeton('--color-prix-fond'),
					rangs: {
						parcelles: rang('path.parcel'),
						groupe: rang('path.parcel-group'),
						pastilles: rang('path.parcel-sold-mark'),
						selection: rang('path.parcel-selected')
					}
				};
			});
			expect(trait.stroke).toBe(trait.encre);
			expect(trait.fill).toBe(trait.fond);
			expect(trait.tirets.replace(/px/g, '').replace(/,\s*/g, ' ')).toBe('8 6');
			expect(trait.largeur).toBe('3px');
			expect(trait.touche, 'le groupe laisse passer le toucher').toBe('none');
			// Stacking: parcels, group, sold marks, then the address and the selection.
			expect(trait.rangs.parcelles).toBeLessThan(trait.rangs.groupe);
			expect(trait.rangs.groupe).toBeLessThan(trait.rangs.pastilles);
			expect(trait.rangs.groupe).toBeLessThan(trait.rangs.selection);
			// The selected parcel keeps its style of selection.
			await expect(dialogue.locator('path.parcel-selected')).toHaveCount(1);

			// A small group is never enlarged past the zoom of the opening.
			await expect.poll(() => dernierZoom(routes)).toBe(18);

			// The legend says it by a word, with the same dashed sample.
			await expect(legende).toHaveCount(3);
			await expect(legende.nth(2)).toHaveText(T.legende);
			await expect(legende.nth(2).locator('svg[aria-hidden="true"] .echantillon-groupe')).toHaveCount(1);
			// Their lines carry the badge; the others do not.
			await expect.poll(() => lignesDuGroupe(lignes)).toEqual(['ZA 0001', 'ZA 0004']);
			// Both are in the view: nothing is said out of it.
			expect(await horsVueDit(panneau)).toBeNull();
			// Never an owner (UI contract, forbidden words), in the panel nor in the legend; the panel of sources
			// says it does not tell one.
			await expect(panneau).not.toContainText(/propriétaire|unité foncière/);
			await expect(dialogue.locator('ul.carte-legende')).not.toContainText(/propriétaire|unité foncière/);

			// Turned off: the drawing, the legend and the badges go.
			await bascule.click();
			await expect(bascule).toHaveAttribute('aria-pressed', 'false');
			await expect(groupe).toHaveCount(0);
			await expect(legende).toHaveCount(2);
			expect(await lignesDuGroupe(lignes)).toEqual([]);

			// Shown again, then another parcel is chosen: the group goes with the sale.
			await bascule.click();
			await expect(groupe).toHaveCount(2);
			await lignes.filter({ hasText: 'ZA 0002' }).click();
			await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0002');
			await expect(groupe).toHaveCount(0);
			await expect(legende).toHaveCount(2);
			expect(await lignesDuGroupe(lignes)).toEqual([]);
		});
	});
}

test.describe('Carte des parcelles — un grand groupe à 375 px', () => {
	let routes: RoutesCarte;
	let erreurs: string[];

	test.beforeEach(async ({ page, request }) => {
		erreurs = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.setViewportSize({ width: 375, height: 900 });
		await installLocalNetworkGuard(page);
		routes = await installerRoutesCarte(page);
		await configurer(request);
	});

	test.afterEach(async ({ page }) => controlerFin(page, erreurs));

	test('treize parcelles : neuf tracées, la surface non calculée, ce qui reste hors de la vue compté et suivi ; une tuile en panne n’y fait pas de trou', async ({ page }) => {
		routes.repondre('ventes', { status: 200, json: venteDeTreize() });
		const { dialogue, cartes, groupe, panneau, etat, vue } = await ouvrirCarte(page);
		const pied = cartes.nth(1).locator('.vente-groupe');
		await expect(pied.locator('p')).toHaveText(['Vendue avec 12 autres parcelles']);

		// The tiles of parcels of the framed view fail: the group is drawn from what the sale served.
		routes.repondre('parcelles', { status: 503, json: { title: 'Parcelles indisponibles' } });
		await pied.getByRole('button', { name: T.bascule }).click();
		await expect(groupe).toHaveCount(9);
		// Framed on the group, at the widest zoom allowed, without animation (reduced motion).
		await expect.poll(() => dernierZoom(routes)).toBe(16);
		await expect(dialogue.locator('.leaflet-proxy')).toHaveCount(0);
		await expect(etat).toHaveText(T.parcellesEnEchec);
		await expect(groupe).toHaveCount(9);

		// The served words of the group: no total surface, the reserve, the parcel gone, those out of the view.
		const textes = pied.locator('p');
		await expect(textes.nth(1)).toHaveText('13 parcelles vendues ensemble · juin 2024');
		await expect(textes.nth(2)).toHaveText(T.reserve);
		await expect(textes.nth(3)).toHaveText(`Surface totale non calculée${NB}: 1 de ces parcelles ne figure plus au cadastre.`);
		// Three are never on the map: said apart (T-46, S2 of 2026-11-15), never counted again in T-26.
		await expect(textes.nth(4)).toHaveText('3 de ces parcelles sont trop loin de l’adresse pour être montrées.');
		// At zoom 16 some of the nine drawn stay out of the view.
		await expect.poll(() => horsVueDit(panneau)).toBeGreaterThan(0);
		const avant = (await horsVueDit(panneau))!;

		// A move: the count follows the view. The body scrolled down to the toggle: the map comes back first.
		await vue.scrollIntoViewIfNeeded();
		const cadre = (await vue.boundingBox())!;
		await glisser(page, { x: cadre.x + cadre.width - 20, y: cadre.y + cadre.height / 2 }, -(cadre.width - 40), 0);
		await expect.poll(async () => (await horsVueDit(panneau)) ?? 0).not.toBe(avant);
		await expect(panneau).not.toContainText(/propriétaire|unité foncière/);
	});
});

// ---------------------------------------------------------------------------------------------------
// 04.10-09, task 2: the module of Leaflet does not load. The list and the panel work over the bounds of
// the opening (the frame centred on the address at zoom 18), read without Leaflet: the same tiles Leaflet
// would have asked, the parcel of the address selected and its sales read; the texts « sans carte ».
// ---------------------------------------------------------------------------------------------------

/** The script and the style sheet of Leaflet themselves, not the `?url` modules the fiche loads in dev. */
const estLeaflet = (url: URL) => url.search !== '?url' && /\/leaflet(-[\w-]+)?\.(js|css)$/.test(url.pathname);

/** Cuts the module of Leaflet until the returned function lets it through. */
async function couperLeaflet(page: Page): Promise<() => void> {
	let couper = true;
	await page.route(estLeaflet, (route) => (couper ? route.abort() : route.fallback()));
	return () => {
		couper = false;
	};
}

const S = {
	carteEnEchec: 'La carte n’a pas pu être chargée. La liste des parcelles reste disponible.',
	rechargerCarte: 'Recharger la carte',
	rechargerParcelles: 'Recharger les parcelles',
	compte: '4 parcelles autour de l’adresse, dont 3 avec une vente (2021-2025).',
	vide: 'Aucune parcelle cadastrale autour de l’adresse.',
	choisir: 'Choisissez une parcelle dans la liste.',
	limites: `Ventes connues${NB}: 2021-2025. Cadastre${NB}: 2026-07. Fond${NB}: © IGN (Plan IGN), Licence Ouverte 2.0.`
};
const ORDRE = ['ZA 0001', 'ZA 0003', 'ZA 0002', 'ZA 0004'];
const references = (lignes: Locator) => lignes.locator('.ligne-ref').allTextContents();

async function ouvrirSansCarte(page: Page) {
	await ouvrirFiche(page);
	await page.getByRole('button', { name: BOUTON_CARTE }).click();
	const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
	const cadre = dialogue.locator('.carte-cadre');
	await expect(cadre.locator('[role="status"]')).toHaveText(S.carteEnEchec);
	await expect(cadre.getByRole('button', { name: S.rechargerCarte })).toBeVisible();
	return { ...parties(dialogue), cadre };
}

test.describe('Carte des parcelles — sans la carte, à 375 px', () => {
	let routes: RoutesCarte;
	let erreurs: string[];

	test.beforeEach(async ({ page, request }) => {
		erreurs = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		await page.setViewportSize({ width: 375, height: 900 });
		await installLocalNetworkGuard(page);
		routes = await installerRoutesCarte(page);
		await configurer(request);
	});

	test.afterEach(async ({ page }) => controlerFin(page, erreurs));

	test('Leaflet en échec : les tuiles que Leaflet aurait lues, la liste, la parcelle de l’adresse choisie et ses ventes lues jusqu’au bout', async ({ page, context }) => {
		// The reference: the tiles of parcels Leaflet asks at the opening, in another page of the same size.
		const temoin = await context.newPage();
		await temoin.setViewportSize({ width: 375, height: 900 });
		await installLocalNetworkGuard(temoin);
		const routesTemoin = await installerRoutesCarte(temoin);
		await ouvrirCarte(temoin);
		const tuilesLeaflet = [...new Set(routesTemoin.demandes.parcelles)].sort();
		const tailleLeaflet = await temoin.locator('.carte-vue').evaluate((e) => ({ largeur: e.clientWidth, hauteur: e.clientHeight }));
		// The reference case of `parcel-view.test.ts` (`tuilesDuCadre`) was read here once: this frame, these tiles.
		expect(tailleLeaflet).toEqual({ largeur: 375, hauteur: 420 });
		expect(tuilesLeaflet).toHaveLength(9);
		attendreAucuneCarteNonReconnue(temoin);
		await temoin.close();

		await couperLeaflet(page);
		const { dialogue, cadre, panneau, ventes, cartes, liste, lignes, etat } = await ouvrirSansCarte(page);
		await expect(dialogue.locator('.leaflet-container')).toHaveCount(0);
		// The same tiles, asked once each, without Leaflet.
		await expect.poll(() => [...new Set(routes.demandes.parcelles)].sort()).toEqual(tuilesLeaflet);
		expect(routes.demandes.parcelles.length).toBe(tuilesLeaflet.length);

		// The list over the opening bounds, the parcel of the address first and selected.
		await expect(liste.getByRole('heading', { level: 3 })).toHaveText('Parcelles affichées');
		await expect.poll(() => references(lignes)).toEqual(ORDRE);
		await expect(lignes.first()).toHaveAttribute('aria-current', 'true');
		await expect(liste.locator('.parcelles-compte')).toHaveText(S.compte);
		// Its sales, read to the end.
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
		await expect(cartes).toHaveCount(4);
		await expect(ventes).toHaveAttribute('aria-busy', 'false');
		expect(routes.demandes.ventes).toEqual([`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/ventes`]);
		// No legend without the map; the line of limits stays.
		await expect(dialogue.locator('ul.carte-legende')).toHaveCount(0);
		await expect(dialogue.locator('.carte-limites')).toContainText(S.limites);
		// The frame still says the outage: the list is the way to the parcels.
		await expect(cadre.locator('[role="status"]')).toHaveText(S.carteEnEchec);

		// A line chooses its parcel; the panel reads it, the focus on its title.
		await lignes.filter({ hasText: 'ZA 0003' }).click();
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0003');
		await expect(panneau.getByRole('heading', { level: 3 })).toBeFocused();
		await expect(lignes.filter({ hasText: 'ZA 0003' })).toHaveAttribute('aria-current', 'true');
		await expect(etat).toHaveText('Parcelle ZA 0003 sélectionnée.');
	});

	test('sans carte et sans parcelle de l’adresse : T-35 et T-37 dans leur variante', async ({ page }) => {
		const contexte = structuredClone(EXEMPLE_CONTEXTE) as unknown as { donnees: Record<string, unknown> };
		contexte.donnees.parcelle_adresse = { etat: 'not_available', id_parcelle: null, motif: 'parcelles_multiples', nombre_parcelles: 3 };
		routes.repondre('contexte', { status: 200, json: contexte });
		const vide = structuredClone(EXEMPLE_TUILE) as { donnees: { collection: { features: unknown[] } } };
		vide.donnees.collection.features = [];
		routes.repondre('parcelles', { status: 200, json: vide });
		await couperLeaflet(page);
		const { panneau, liste } = await ouvrirSansCarte(page);
		await expect(liste.locator('.parcelles-vide')).toHaveText(S.vide);
		await expect(panneau.locator('.parcelle-vide')).toHaveText(S.choisir);
	});

	test('une tuile en échec sans carte : T-34 et T-41 au-dessus de la liste, jamais le zéro ; relue, la liste vient', async ({ page }) => {
		routes.repondre('parcelles', { status: 503, json: { title: 'Parcelles indisponibles' } });
		await couperLeaflet(page);
		const { dialogue, liste, lignes, etat } = await ouvrirSansCarte(page);
		await expect(etat).toHaveText(T.parcellesEnEchec);
		const relance = dialogue.getByRole('button', { name: S.rechargerParcelles });
		await expect(relance).toBeVisible();
		// Both above the list, in the order of reading.
		const avantLaListe = (cible: Locator) =>
			cible.evaluate((e) => Boolean(e.compareDocumentPosition(document.querySelector('section.parcelles')!) & Node.DOCUMENT_POSITION_FOLLOWING));
		expect(await avantLaListe(etat)).toBe(true);
		expect(await avantLaListe(relance)).toBe(true);
		await expect(liste).not.toContainText(S.vide);
		await expect(liste.locator('.parcelles-compte')).toHaveCount(0);

		routes.repondre('parcelles', { status: 200, json: EXEMPLE_TUILE });
		await relance.click();
		await expect.poll(() => references(lignes)).toEqual(ORDRE);
		await expect(etat).not.toHaveText(T.parcellesEnEchec);
		await expect(relance).toHaveCount(0);
	});

	test('un groupe sans carte : T-23, T-24, T-25 et T-26 compté sur le cadre d’ouverture, T-31 dans la liste', async ({ page }) => {
		routes.repondre('ventes', { status: 200, json: venteDeTreize() });
		await couperLeaflet(page);
		const { panneau, cartes, lignes, groupe } = await ouvrirSansCarte(page);
		await expect(cartes).toHaveCount(4);
		const pied = cartes.nth(1).locator('.vente-groupe');
		await pied.getByRole('button', { name: T.bascule }).click();
		await expect(pied.locator('p')).toHaveText([
			'Vendue avec 12 autres parcelles',
			'13 parcelles vendues ensemble · juin 2024',
			T.reserve,
			`Surface totale non calculée${NB}: 1 de ces parcelles ne figure plus au cadastre.`,
			// Three out of the bounds of the address (T-46), and the eight served ones out of the frame of the
			// opening, said for the listed zone without the map (T-48, S6 of 2026-11-15).
			'3 de ces parcelles sont trop loin de l’adresse pour être montrées.',
			'8 de ces parcelles sont hors de la zone listée.'
		]);
		await expect.poll(() => lignesDuGroupe(lignes)).toEqual(['ZA 0001']);
		await expect(groupe).toHaveCount(0);
		await expect(panneau).not.toContainText(/propriétaire|unité foncière/);
	});

	test('« Recharger la carte » : la carte se monte et garde la parcelle choisie dans la liste', async ({ page }) => {
		const liberer = await couperLeaflet(page);
		const { dialogue, cadre, panneau, lignes } = await ouvrirSansCarte(page);
		await expect.poll(() => references(lignes)).toEqual(ORDRE);
		await lignes.filter({ hasText: 'ZA 0003' }).click();
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0003');

		liberer();
		await cadre.getByRole('button', { name: S.rechargerCarte }).click();
		await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
		await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
		await expect(cadre.locator('[role="status"]')).toHaveCount(0);
		// The choice is kept: the panel, the outline of the selection, the checked line.
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0003');
		await expect(dialogue.locator('path.parcel-selected')).toHaveCount(1);
		await expect(lignes.filter({ hasText: 'ZA 0003' })).toHaveAttribute('aria-current', 'true');
		// With the map, the legend comes back and the count speaks of the map.
		await expect(dialogue.locator('ul.carte-legende')).toHaveCount(1);
		await expect(dialogue.locator('section.parcelles .parcelles-compte')).toHaveText('4 parcelles sur la carte, dont 3 avec une vente (2021-2025).');
	});
});

// ---------------------------------------------------------------------------------------------------
// 04.10-09, task 3 (C8, detachable): the back button of the browser closes the map without leaving the
// fiche. The map adds one entry of history at its opening, without changing the URL (shallow routing):
// no address of a map is ever created, so none can be shared nor indexed.
// ---------------------------------------------------------------------------------------------------

/**
 * A page of departure that stays in the history on the three engines: Firefox replaces the initial
 * `about:blank` of a tab by the next navigation instead of keeping it (matrix of 04.10-10).
 */
const PAGE_DE_DEPART = 'data:text/html,depart';

/** The fiche, entered from a page of departure: a back from the fiche itself leaves for that page. */
async function ficheDepuisUnePageVide(page: Page) {
	await page.goto('about:blank');
	await page.goto(PAGE_DE_DEPART);
	await ouvrirFiche(page);
	const url = page.url();
	// Set in the document: a reload of the page would lose it.
	await page.evaluate(() => Object.assign(window, { __documentDeLaFiche: true }));
	return url;
}

const memeDocument = (page: Page) => page.evaluate(() => (window as unknown as { __documentDeLaFiche?: boolean }).__documentDeLaFiche === true);
const longueurHistorique = (page: Page) => page.evaluate(() => history.length);
/** The entry the map added is gone: the state of the current entry no longer carries it. */
const entreeDeLaCarteQuittee = (page: Page) => page.waitForFunction(() => !JSON.stringify(history.state ?? null).includes('carteParcelles'));

test.describe('Carte des parcelles — retour du navigateur (C8)', () => {
	let erreurs: string[];

	test.beforeEach(async ({ page, request }) => {
		erreurs = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		await installLocalNetworkGuard(page);
		await installerRoutesCarte(page);
		await configurer(request);
	});

	test.afterEach(async ({ page }) => controlerFin(page, erreurs));

	test('à 375 px : le retour ferme la carte sans quitter la fiche ni la recharger ; un second retour quitte la fiche', async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 900 });
		const urlFiche = await ficheDepuisUnePageVide(page);
		const avant = await longueurHistorique(page);
		const bouton = page.getByRole('button', { name: BOUTON_CARTE });
		await bouton.click();
		await expect(page.locator('dialog.carte-dialogue .carte-vue.leaflet-container')).toBeVisible();
		// The URL of the fiche does not change at the opening: one entry of history, no address of a map.
		expect(page.url()).toBe(urlFiche);
		expect(await longueurHistorique(page)).toBe(avant + 1);

		await page.goBack();
		await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		expect(page.url()).toBe(urlFiche);
		expect(await memeDocument(page), 'la fiche n’est pas rechargée').toBe(true);
		await expect(bouton).toBeFocused();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('3 rue des Parcelles');

		// « Suivant » (shallow routing): the entry of the map comes back, the map does not; the URL stays.
		await page.goForward();
		await expect.poll(() => page.evaluate(() => JSON.stringify(history.state ?? null).includes('carteParcelles'))).toBe(true);
		await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		expect(page.url()).toBe(urlFiche);
		await page.goBack();
		await entreeDeLaCarteQuittee(page);
		expect(page.url()).toBe(urlFiche);
		expect(await memeDocument(page)).toBe(true);

		// From the entry of the fiche, a back leaves the fiche, as before the map.
		await page.goBack();
		await expect.poll(() => page.url()).toBe(PAGE_DE_DEPART);
	});

	test('à 1440 px : « Fermer », Échap et le voile consomment l’entrée ; dix ouvertures ne laissent pas dix entrées', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const urlFiche = await ficheDepuisUnePageVide(page);
		const avant = await longueurHistorique(page);
		const bouton = page.getByRole('button', { name: BOUTON_CARTE });
		const dialogue = page.locator('dialog.carte-dialogue');
		const fermetures = [
			() => dialogue.getByRole('button', { name: 'Fermer' }).click(),
			() => page.keyboard.press('Escape'),
			// The backdrop: a corner of the window, outside the box centred above 720 px.
			() => page.mouse.click(5, 5)
		];
		for (let i = 0; i < 10; i++) {
			await bouton.click();
			await expect(dialogue.getByRole('heading', { level: 2 })).toBeFocused();
			expect(page.url()).toBe(urlFiche);
			// Open, the map holds its entry; the one a former closing left forward is replaced, not added to.
			expect(await longueurHistorique(page)).toBe(avant + 1);
			await fermetures[i % fermetures.length]();
			await expect(dialogue).toHaveCount(0);
			await entreeDeLaCarteQuittee(page);
			expect(page.url()).toBe(urlFiche);
			await expect(bouton).toBeFocused();
		}
		expect(await longueurHistorique(page)).toBeLessThanOrEqual(avant + 1);
		expect(await memeDocument(page)).toBe(true);
		// The first back leaves the fiche: no entry of the map is left behind.
		await page.goBack();
		await expect.poll(() => page.url()).toBe(PAGE_DE_DEPART);
	});
});
