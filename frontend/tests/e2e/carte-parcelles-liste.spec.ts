// List of the parcels shown in the dialog of the parcel map (04.10-08, LIEU-01, SEM-01, UI-01): the path
// without the map. The parcels come from the routes of the map on the origin of the site (the contract
// examples of 04.10-02: four parcels around the address, every tile serving them).
//
// What this spec locks:
//   - the body of the dialog reads map, status line, legend and limits, panel, list (UI contract § 2.2);
//   - the list says the parcels of the view, the parcel of the address first by a word, and follows a
//     move; the count (T-29) is said in the status line after a move, never at the opening;
//   - a line chooses its parcel on the map, centres the map, brings the body back to the panel and puts
//     the focus on its title; a touch on the map checks its line, the focus and the scroll stay;
//   - a part of the view not read never says « Aucune parcelle » (T-37);
//   - the whole path by keyboard alone, the Tab loop through the box and the lines;
//   - lines of 44 px at least, no horizontal scroll, one radius for the controls.
// Synthetic SSR source, headless runner, isolated context (AGENTS.md).
import AxeBuilder from '@axe-core/playwright';
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

const BOUTON_CARTE = 'Ouvrir la carte des parcelles';

async function ouvrirCarte(page: Page) {
	await ouvrirFiche(page);
	await page.getByRole('button', { name: BOUTON_CARTE }).click();
	return dialogueOuvert(page);
}

async function dialogueOuvert(page: Page) {
	const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
	await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
	await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
	const liste = dialogue.locator('section.parcelles');
	return {
		dialogue,
		etat: dialogue.locator('[aria-live="polite"]'),
		vue: dialogue.locator('.carte-vue'),
		corps: dialogue.locator('.carte-corps'),
		panneau: dialogue.locator('section.parcelle'),
		liste,
		lignes: liste.locator('ul.parcelles-lignes > li > button')
	};
}

const PARCELLES = EXEMPLE_TUILE.donnees.collection.features;

const T = {
	titre: 'Parcelles affichées',
	compte: '4 parcelles sur la carte, dont 3 avec une vente (2021-2025).',
	vide: 'Aucune parcelle cadastrale dans cette vue.',
	filtre: 'Seulement les parcelles avec une vente',
	parcellesEnEchec: 'Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.',
	rechargerParcelles: 'Recharger les parcelles',
	retour: 'Revenir à l’adresse'
};
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;

/** The parcels of the example from the parcel of the address outwards: ZA0002 and ZA0004 are at the same distance. */
const ORDRE = ['ZA 0001', 'ZA 0003', 'ZA 0002', 'ZA 0004'];

const centre = async (cible: Locator) => {
	const boite = (await cible.boundingBox())!;
	return { x: boite.x + boite.width / 2, y: boite.y + boite.height / 2 };
};

/** The drawn parcel whose centre is the nearest to *point*, among the clickable parcels. */
async function parcelleVers(page: Page, point: { x: number; y: number }): Promise<{ x: number; y: number }> {
	const centres = await Promise.all((await page.locator('path.parcel').all()).map(centre));
	return centres.reduce((a, b) => (Math.hypot(a.x - point.x, a.y - point.y) <= Math.hypot(b.x - point.x, b.y - point.y) ? a : b));
}

async function glisser(page: Page, depuis: { x: number; y: number }, dx: number, dy: number) {
	await page.mouse.move(depuis.x, depuis.y);
	await page.mouse.down();
	await page.mouse.move(depuis.x + dx / 2, depuis.y + dy / 2, { steps: 5 });
	await page.mouse.move(depuis.x + dx, depuis.y + dy, { steps: 5 });
	await page.mouse.up();
}

/** Three drags of almost the width of the map: the parcels of the example leave the view. */
async function emmenerLoin(page: Page, vue: Locator) {
	const cadre = (await vue.boundingBox())!;
	for (let i = 0; i < 3; i++) {
		await glisser(page, { x: cadre.x + cadre.width - 30, y: cadre.y + cadre.height / 2 }, -(cadre.width - 60), 0);
	}
}

/** What has the focus, in words: the reference of a line, the name of a button, or a tag. */
const focusDecrit = (page: Page) =>
	page.evaluate(() => {
		const actif = document.activeElement as HTMLElement | null;
		if (!actif) return 'aucun';
		const ref = actif.querySelector('.ligne-ref');
		if (ref) return `ligne ${ref.textContent!.trim()}`;
		if (actif instanceof HTMLInputElement) return `case ${actif.closest('label')?.textContent?.trim() ?? ''}`;
		return `${actif.tagName.toLowerCase()} ${(actif.getAttribute('aria-label') ?? actif.textContent ?? '').trim()}`;
	});

/** Tab (or Shift+Tab) until *voulu* has the focus; fails if it never comes within *max* presses. */
async function tabulerJusqua(page: Page, voulu: string, max = 80, touche = 'Tab') {
	const vus: string[] = [];
	for (let i = 0; i < max; i++) {
		await page.keyboard.press(touche);
		const decrit = await focusDecrit(page);
		vus.push(decrit);
		if (decrit === voulu) return;
	}
	throw new Error(`« ${voulu} » jamais atteint au clavier ; parcours : ${vus.join(' | ')}`);
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

function installer(largeur: number) {
	const etat: { routes: RoutesCarte; erreurs: string[] } = { routes: null as unknown as RoutesCarte, erreurs: [] };
	test.beforeEach(async ({ page, request }) => {
		etat.erreurs = [];
		page.on('pageerror', (erreur) => etat.erreurs.push(erreur.message));
		await page.setViewportSize({ width: largeur, height: largeur > 720 ? 900 : 800 });
		await installLocalNetworkGuard(page);
		etat.routes = await installerRoutesCarte(page);
		await configurer(request);
	});
	test.afterEach(async ({ page }) => {
		expect(etat.erreurs, 'aucune erreur de script dans la page').toEqual([]);
		expectNoUnexpectedOffLocalRequest(page);
		attendreAucuneCarteNonReconnue(page);
	});
	return etat;
}

for (const largeur of [320, 375, 1440]) {
	test.describe(`Carte des parcelles — liste à ${largeur} px`, () => {
		installer(largeur);

		test('le corps se lit carte, état, légende, panneau, liste ; la liste dit la vue et la suit', async ({ page }) => {
			const { dialogue, etat, vue, liste, lignes } = await ouvrirCarte(page);

			// The order of the body is the order of reading, at every width.
			const ordre = await dialogue.evaluate((d) =>
				['.carte-cadre', '[aria-live="polite"]', '.carte-legende-bloc', 'section.parcelle', 'section.parcelles'].map((selecteur) => {
					const el = d.querySelector(selecteur);
					return el ? [...d.querySelectorAll('*')].indexOf(el) : -1;
				})
			);
			expect(ordre.every((rang) => rang >= 0), JSON.stringify(ordre)).toBe(true);
			expect([...ordre].sort((a, b) => a - b)).toEqual(ordre);

			// The list: its title, the parcels of the view from the address outwards, the address told by a word.
			await expect(liste.getByRole('heading', { level: 3 })).toHaveText(T.titre);
			await expect(lignes.locator('.ligne-ref')).toHaveText(ORDRE);
			await expect(lignes.nth(0)).toContainText('Adresse');
			await expect(lignes.nth(0)).toHaveAttribute('aria-current', 'true');
			await expect(lignes.nth(0).locator('svg')).toHaveCount(1);
			for (const i of [1, 2, 3]) await expect(lignes.nth(i)).not.toHaveAttribute('aria-current', /./);
			await expect(lignes.nth(0)).toContainText(`450${NB}m²`);
			await expect(lignes.nth(0)).toContainText(`4${NB}ventes`);
			await expect(lignes.nth(1)).toContainText(`Non renseigné`);
			await expect(lignes.nth(1)).not.toContainText('m²');
			await expect(lignes.nth(2)).not.toContainText('vente');
			await expect(liste.locator('.parcelles-compte')).toHaveText(T.compte);
			// Opening announces nothing: the count is said after a move.
			await expect(etat).toHaveText('');

			// Lines of 44 px at least, one radius for the controls (forme-publique.spec.ts), no horizontal scroll.
			const rayon = await dialogue.evaluate((d) => getComputedStyle(d.closest('.public-site')!).getPropertyValue('--radius-control').trim());
			for (const ligne of await lignes.all()) {
				expect((await ligne.boundingBox())!.height).toBeGreaterThanOrEqual(44);
				expect(await ligne.evaluate((b) => getComputedStyle(b).borderTopLeftRadius)).toBe(rayon);
			}
			expect(Math.round((await liste.locator('.parcelles-filtre').boundingBox())!.height)).toBeGreaterThanOrEqual(44);
			const debordements = await dialogue.evaluate((d) => {
				const corps = d.querySelector('.carte-corps')!;
				return [document.documentElement.scrollWidth - document.documentElement.clientWidth, corps.scrollWidth - corps.clientWidth];
			});
			expect(debordements).toEqual([0, 0]);
			if (largeur === 375) {
				const axe = await new AxeBuilder({ page }).include('dialog.carte-dialogue').withTags([...TAGS_WCAG]).analyze();
				expect(axe.violations, JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
			}

			// Far from the address: no parcel in the view, said in the list and in the status line.
			await emmenerLoin(page, vue);
			await expect(liste.locator('.parcelles-vide')).toHaveText(T.vide);
			await expect(lignes).toHaveCount(0);
			await expect(liste.locator('.parcelles-compte')).toHaveCount(0);
			await expect(etat).toHaveText(T.vide);
			// The selection holds, even out of the view.
			await expect(dialogue.locator('section.parcelle h3')).toHaveText('Parcelle ZA 0001');

			// Back to the address: the list follows, the count is said in the status line.
			await dialogue.getByRole('button', { name: T.retour }).click();
			await expect(lignes.locator('.ligne-ref')).toHaveText(ORDRE);
			await expect(etat).toHaveText(T.compte);
			await expect(liste.locator('.parcelles-vide')).toHaveCount(0);
		});
	});
}

test.describe('Carte des parcelles — liste et sélection à 375 px', () => {
	const etat = installer(375);

	test('une ligne choisit sa parcelle : carte centrée, corps remonté au panneau, focus sur son titre ; un toucher sur la carte coche sa ligne', async ({ page }) => {
		const { dialogue, etat: ligneEtat, vue, corps, panneau, lignes } = await ouvrirCarte(page);
		const titre = panneau.getByRole('heading', { level: 3 });
		await expect(titre).toHaveText('Parcelle ZA 0001');
		await expect(dialogue.locator('section.parcelle .ventes-cartes > li').first()).toBeVisible();

		const ligne = lignes.filter({ hasText: 'ZA 0002' });
		await ligne.scrollIntoViewIfNeeded();
		await ligne.click();
		await expect(titre).toHaveText('Parcelle ZA 0002');
		await expect(titre).toBeFocused();
		await expect(ligneEtat).toHaveText('Parcelle ZA 0002 sélectionnée.');
		await expect(ligne).toHaveAttribute('aria-current', 'true');
		await expect(lignes.nth(0)).not.toHaveAttribute('aria-current', /./);
		await expect(dialogue.locator('path.parcel-selected')).toHaveCount(1);
		// The map is centred on the chosen parcel.
		await expect
			.poll(async () => {
				const [choisie, milieu] = [await centre(dialogue.locator('path.parcel-selected')), await centre(vue)];
				return Math.round(Math.hypot(choisie.x - milieu.x, choisie.y - milieu.y));
			})
			.toBeLessThan(3);
		// The body came back up to the panel: once its sales are read, its top is the top of the scrolling body.
		await expect(panneau.locator('.ventes-cartes > li').first()).toBeVisible();
		await expect
			.poll(async () => Math.round(Math.abs((await panneau.boundingBox())!.y - (await corps.boundingBox())!.y)))
			.toBeLessThan(2);
		expect(await corps.evaluate((c) => c.scrollTop)).toBeGreaterThan(0);
		await expect(titre).toBeFocused();

		// A touch on the map: its line is checked, the focus stays on the map, nothing scrolls.
		await corps.evaluate((c) => (c.scrollTop = 0));
		const pointAdresse = await centre(dialogue.locator('.point-adresse'));
		const nord = await parcelleVers(page, { x: pointAdresse.x, y: pointAdresse.y - 76 });
		await page.mouse.click(nord.x, nord.y);
		await expect(titre).toHaveText('Parcelle ZA 0003');
		await expect(lignes.filter({ hasText: 'ZA 0003' })).toHaveAttribute('aria-current', 'true');
		await expect(ligne).not.toHaveAttribute('aria-current', /./);
		await expect(vue).toBeFocused();
		expect(await corps.evaluate((c) => c.scrollTop)).toBe(0);
	});

	test('une partie de la vue non lue n’est jamais « aucune parcelle » ; relue, la vue vide se dit', async ({ page }) => {
		const { dialogue, etat: ligneEtat, vue, liste } = await ouvrirCarte(page);
		etat.routes.repondre('parcelles', { status: 503, json: { title: 'Parcelles momentanement indisponibles' } });
		await emmenerLoin(page, vue);
		await expect(ligneEtat).toHaveText(T.parcellesEnEchec);
		await expect(dialogue.locator('.couche-parcelles .tile-failed').first()).toBeAttached();
		await expect(dialogue.getByText(/Aucune parcelle/)).toHaveCount(0);
		await expect(liste.locator('.parcelles-compte')).toHaveCount(0);

		etat.routes.repondre('parcelles', { status: 200, json: EXEMPLE_TUILE });
		await dialogue.getByRole('button', { name: T.rechargerParcelles }).click();
		await expect(dialogue.locator('.couche-parcelles .tile-failed')).toHaveCount(0);
		// Every tile of the view read, none of its parcels in it: now, and only now, the view is empty.
		await expect(liste.locator('.parcelles-vide')).toHaveText(T.vide);
		await expect(ligneEtat).toHaveText(T.vide);
	});

	test('parcours au clavier : ouvrir, choisir une parcelle dans la liste, lire ses ventes, changer de page, fermer', async ({ page }) => {
		etat.routes.repondre('ventes', (url) => ({ status: 200, json: pageDe(Number(url.searchParams.get('page'))) }));
		await ouvrirFiche(page);
		const bouton = page.getByRole('button', { name: BOUTON_CARTE });
		await bouton.focus();
		await page.keyboard.press('Enter');
		const { dialogue, panneau, liste, lignes } = await dialogueOuvert(page);
		await expect(dialogue.getByRole('heading', { level: 2 })).toBeFocused();
		await expect(panneau.locator('.ventes-cartes > li')).toHaveCount(5);

		// The box: reached by Tab, toggled by Space; the count does not change.
		await tabulerJusqua(page, `case ${T.filtre}`);
		await page.keyboard.press('Space');
		await expect(liste.getByRole('checkbox', { name: T.filtre })).toBeChecked();
		await expect(lignes.locator('.ligne-ref')).toHaveText(['ZA 0001', 'ZA 0003', 'ZA 0004']);
		await expect(liste.locator('.parcelles-compte')).toHaveText(T.compte);
		await page.keyboard.press('Space');
		await expect(lignes.locator('.ligne-ref')).toHaveText(ORDRE);

		// A line of the list, by Tab, chosen by Enter: the focus goes to the title of its panel.
		await tabulerJusqua(page, 'ligne ZA 0002');
		await page.keyboard.press('Enter');
		const titre = panneau.getByRole('heading', { level: 3 });
		await expect(titre).toHaveText('Parcelle ZA 0002');
		await expect(titre).toBeFocused();
		await expect(lignes.filter({ hasText: 'ZA 0002' })).toHaveAttribute('aria-current', 'true');

		// Its sales, and their next page, by keyboard: the focus comes back to the title.
		const ventes = panneau.locator('section.parcelle-ventes');
		await expect(ventes.locator('.ventes-cartes > li')).toHaveCount(5);
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 1 à 5 sur 12');
		await tabulerJusqua(page, 'button Page suivante');
		await page.keyboard.press('Enter');
		await expect(ventes.locator('.pagination-range')).toHaveText('Ventes 6 à 10 sur 12');
		await expect(titre).toBeFocused();

		// The Tab loop goes through the lines: from the last one back to « Fermer », and the other way.
		await tabulerJusqua(page, 'ligne ZA 0004');
		await page.keyboard.press('Tab');
		await expect(dialogue.getByRole('button', { name: 'Fermer' })).toBeFocused();
		await page.keyboard.press('Shift+Tab');
		await expect(lignes.filter({ hasText: 'ZA 0004' })).toBeFocused();

		// Escape closes; the focus goes back to the button of the fiche.
		await page.keyboard.press('Escape');
		await expect(page.locator('dialog.carte-dialogue')).toHaveCount(0);
		await expect(bouton).toBeFocused();
	});
});
