// Cross-cutting proofs of the parcel map (04.10-10, SEM-01, UI-01): what the UI contract asks in its § 5 and
// that no feature plan carries alone. The map, its parcels and its sales come from the routes of the map
// on the origin of the site (the contract examples of 04.10-02), replaced per test.
//
// What this spec locks:
//   - at 1440, 375 and 320 px, at 200 % zoom (720 × 450) and on a phone held sideways: no horizontal scroll
//     of the document nor of the dialog, « Fermer » in the window, the map never taller than half the
//     window; full screen without a radius up to 720 px, a centred box with its edge above; only the body
//     of the dialog scrolls, never the page behind it;
//   - axe-core without violation with the dialog open and a group shown, then with the panel of sources
//     open over it; no visible text under 14 px, Inter 400 and 600 only; no accent background added by
//     the dialog, one radius for every control;
//   - printing hides the dialog and the action row and keeps the static picture of the fiche;
//   - at 320 px, the longest texts (T-34, T-23 of a group of twelve parcels, T-26) overflow nothing and
//     nothing is cut;
//   - in forced colours, the strokes, the dashed outline of the group and the sold marks are still drawn;
//   - the states of the UI contract § 3 that no other spec of the map plays: parcels being read, sales
//     being read, the cadastre out of coverage, a point outside every parcel and an undetermined parcel;
//   - the specs of the fiche answer the two pictures of its head by a strict pattern, which refuses every
//     route of the interactive map (`repondreCarteStatique`, task 2).
// The other states of § 3 are played by `carte-parcelles`, `-panneau`, `-liste` and `-groupe` (table in
// 04.10-10-SUMMARY.md). Plain Playwright test (outages answer 503). Synthetic SSR source, headless
// runner, isolated context (AGENTS.md).
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import {
	EXEMPLE_CONTEXTE,
	EXEMPLE_TUILE,
	EXEMPLE_VENTES,
	IMAGE_DE_TETE,
	attendreAucuneCarteNonReconnue,
	installerRoutesCarte,
	type RoutesCarte
} from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { verifierGraisses, verifierPlancherTypo } from './fixtures/quality';
import { cheminRapport, rapportAvecThemes, REQUETE_RAPPORT } from './fixtures/rapport';

const NB = String.fromCharCode(0xa0);
const LECTURES = 40;

// A synthetic address (no such street): the fiche of the map, with the parcel of the contract example.
const BAN_ID = '85191_0802_00003';
const PARCELLE_ADRESSE = EXEMPLE_CONTEXTE.donnees.parcelle_adresse.id_parcelle;
const PARCELLES = EXEMPLE_TUILE.donnees.collection.features;

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

/** Where a capture goes: the directory of `DC_CARTE_EVIDENCE_DIR` when set, the output of the test otherwise. */
function preuve(testInfo: TestInfo, nom: string): string {
	const dossier = process.env.DC_CARTE_EVIDENCE_DIR;
	return dossier ? `${dossier.replace(/[\\/]$/, '')}/${nom}` : testInfo.outputPath(nom);
}

const BOUTON_CARTE = 'Ouvrir la carte des parcelles';
const TITRE = 'Parcelles autour de l’adresse';
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const T = {
	chargementParcelles: 'Chargement des parcelles…',
	chargementVentes: 'Chargement des ventes de la parcelle…',
	choisir: 'Choisissez une parcelle sur la carte ou dans la liste.',
	bascule: 'Montrer ces parcelles',
	parcellesEnEchec: 'Les parcelles d’une partie de la carte n’ont pas pu être lues. Ce n’est pas une absence de parcelles ni de ventes.',
	rechargerParcelles: 'Recharger les parcelles'
};

function parties(page: Page) {
	const dialogue = page.getByRole('dialog', { name: TITRE });
	const panneau = dialogue.locator('section.parcelle');
	const ventes = panneau.locator('section.parcelle-ventes');
	return {
		dialogue,
		etat: dialogue.locator('[aria-live="polite"]'),
		vue: dialogue.locator('.carte-vue'),
		cadre: dialogue.locator('.carte-cadre'),
		corps: dialogue.locator('.carte-corps'),
		fermer: dialogue.getByRole('button', { name: 'Fermer' }),
		panneau,
		ventes,
		cartes: ventes.locator('.ventes-cartes > li'),
		liste: dialogue.locator('section.parcelles'),
		groupe: dialogue.locator('path.parcel-group')
	};
}

/** Opens the map and waits for the opening state: parcels drawn, the parcel of the address and its sales. */
async function ouvrirCarte(page: Page) {
	await ouvrirFiche(page);
	await page.getByRole('button', { name: BOUTON_CARTE }).click();
	const p = parties(page);
	await expect(p.dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
	await expect(p.dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
	await expect(p.panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
	await expect(p.cartes).toHaveCount(4);
	return p;
}

/** Opens the map without waiting for anything but the dialog (states read while something is held). */
async function ouvrirSansAttendre(page: Page) {
	await ouvrirFiche(page);
	await page.getByRole('button', { name: BOUTON_CARTE }).click();
	const p = parties(page);
	await expect(p.dialogue).toBeVisible();
	return p;
}

/** Holds every request matching *motif* until the returned function is called; the map route answers then. */
async function retenir(page: Page, motif: RegExp): Promise<() => void> {
	let lacher!: () => void;
	const retenue = new Promise<void>((r) => (lacher = r));
	await page.route(motif, async (route) => {
		await retenue;
		await route.fallback();
	});
	return lacher;
}

async function glisser(page: Page, depuis: { x: number; y: number }, dx: number, dy: number) {
	await page.mouse.move(depuis.x, depuis.y);
	await page.mouse.down();
	await page.mouse.move(depuis.x + dx / 2, depuis.y + dy / 2, { steps: 5 });
	await page.mouse.move(depuis.x + dx, depuis.y + dy, { steps: 5 });
	await page.mouse.up();
}

/** Horizontal scroll of the document, of the dialog and of its body, with the deepest culprits named. */
async function largeurs(page: Page) {
	return page.evaluate(() => {
		const vue = window.innerWidth;
		const dialogue = document.querySelector('dialog.carte-dialogue') as HTMLDialogElement;
		const corps = dialogue.querySelector('.carte-corps') as HTMLElement;
		const fautifs = [...dialogue.querySelectorAll('*')]
			.filter((el) => !el.closest('.carte-vue'))
			.filter((el) => el.getBoundingClientRect().right > vue + 1 && ![...el.children].some((c) => c.getBoundingClientRect().right > vue + 1))
			.slice(0, 5)
			.map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')} → ${Math.round(el.getBoundingClientRect().right)}`);
		return {
			document: document.documentElement.scrollWidth - vue,
			dialogue: dialogue.scrollWidth - dialogue.clientWidth,
			corps: corps.scrollWidth - corps.clientWidth,
			fautifs
		};
	});
}

async function sansDebordement(page: Page, contexte: string) {
	const l = await largeurs(page);
	const detail = `${contexte} : ${l.fautifs.join(' ; ')}`;
	expect(l.document, `document, ${detail}`).toBeLessThanOrEqual(0);
	expect(l.dialogue, `dialogue, ${detail}`).toBeLessThanOrEqual(0);
	expect(l.corps, `corps, ${detail}`).toBeLessThanOrEqual(0);
}

/**
 * Every visible element of the dialog (outside the map itself, whose tiles overflow by design) that cuts
 * its content: past an edge of the dialog, or clipped by its own overflow, or ended by an ellipsis.
 */
async function textesCoupes(page: Page): Promise<string[]> {
	return page.locator('dialog.carte-dialogue').evaluate((dialogue) => {
		const bord = dialogue.getBoundingClientRect();
		const decrire = (el: Element) => `${el.tagName.toLowerCase()}.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')} « ${(el.textContent ?? '').trim().slice(0, 40)} »`;
		const fautifs: string[] = [];
		for (const el of dialogue.querySelectorAll('*')) {
			if (el.closest('.carte-vue') || el.closest('.sr-only') || el.closest('svg')) continue;
			if (!el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) continue;
			const boite = el.getBoundingClientRect();
			if (boite.width === 0 || boite.height === 0) continue;
			const style = getComputedStyle(el);
			if (boite.left < bord.left - 0.5 || boite.right > bord.right + 0.5) fautifs.push(`${decrire(el)} : hors du dialogue`);
			const coupeX = ['hidden', 'clip'].includes(style.overflowX) && el.scrollWidth > el.clientWidth + 1;
			const coupeY = ['hidden', 'clip'].includes(style.overflowY) && el.scrollHeight > el.clientHeight + 1;
			if (coupeX || coupeY) fautifs.push(`${decrire(el)} : coupé`);
			if (style.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1) fautifs.push(`${decrire(el)} : points de suspension`);
		}
		return fautifs;
	});
}

async function sansViolationAxe(page: Page, contexte: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.violations, `${contexte} : ${JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`).toEqual([]);
}

/** Visible elements painted with the accent background (rule of `accent-unique.spec.ts`), under *portee*. */
async function fondsAccent(page: Page, portee: string): Promise<{ accent: string; elements: string[] }> {
	return page.evaluate((portee) => {
		const sonde = document.createElement('div');
		sonde.style.background = 'var(--color-accent)';
		document.querySelector('.public-site')!.append(sonde);
		const accent = getComputedStyle(sonde).backgroundColor;
		sonde.remove();
		const elements = [...document.querySelectorAll(`${portee} *`)].filter((el) => {
			if (getComputedStyle(el).backgroundColor !== accent) return false;
			const boite = el.getBoundingClientRect();
			if (boite.width === 0 || boite.height === 0) return false;
			if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
			if (el.closest('.site-brand-mark') || el.closest('.site-nav-count') || el.closest('.site-menu-count') || el.closest('.recherche-en-tete-bouton')) return false;
			return !/^(Retirer|Placer en premier)/.test((el.textContent ?? '').trim());
		});
		return { accent, elements: elements.map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)}`) };
	}, portee);
}

/** The rendered radius of every control of the dialog (rule of `forme-publique.spec.ts`). */
async function rayonsDuDialogue(page: Page) {
	return page.evaluate(() => {
		const decrire = (el: Element) => `${el.tagName.toLowerCase()}.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')}`;
		const attendu = getComputedStyle(document.querySelector('.public-site')!).getPropertyValue('--radius-control').trim();
		const dessineUneBoite = (style: CSSStyleDeclaration) =>
			(parseFloat(style.borderTopWidth) > 0 && parseFloat(style.borderLeftWidth) > 0 && style.borderTopStyle !== 'none') ||
			!['rgba(0, 0, 0, 0)', 'transparent'].includes(style.backgroundColor);
		const fautifs = new Set<string>();
		let mesures = 0;
		const controles = document.querySelectorAll('dialog.carte-dialogue :is(button, input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea, summary, a)');
		for (const el of controles) {
			if (el.getBoundingClientRect().height === 0) continue;
			const style = getComputedStyle(el);
			if (el.tagName === 'A' && !dessineUneBoite(style)) continue;
			mesures++;
			const rayons = [style.borderTopLeftRadius, style.borderTopRightRadius, style.borderBottomRightRadius, style.borderBottomLeftRadius];
			const nonNuls = rayons.filter((rayon) => parseFloat(rayon) > 0);
			if (nonNuls.length === 0 || nonNuls.some((rayon) => rayon !== attendu)) fautifs.add(`${decrire(el)}: ${rayons.join(' ')}`);
		}
		return { attendu, mesures, fautifs: [...fautifs] };
	});
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const copie = (valeur: unknown): any => structuredClone(valeur);

/** A square parcel of a group, on the row of the address, *decalage* degrees east (or west) of it. */
function parcelleDuGroupe(decalage: number, rang: number) {
	const lon = -3.5 + decalage;
	const id = `00000000ZC${String(rang + 1).padStart(4, '0')}`;
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
 * The second sale of the example, sold as a group of twelve parcels, all at the cadastre and in the bounds,
 * with a served total surface: the longest form of T-23 (with its surface), T-21 and T-26.
 */
function venteDeDouze() {
	const corps = copie(EXEMPLE_VENTES);
	const adresse = corps.donnees.groupes[1].parcelles[0];
	corps.donnees.groupes[1] = {
		total: 12,
		autres: 11,
		absentes_du_cadastre: 0,
		hors_emprise: 0,
		surface_totale_m2: 4812,
		parcelles: [adresse, ...[-0.012, -0.009, -0.006, -0.003, 0.003, 0.005, 0.0055, 0.006, 0.0065, 0.009, 0.012].map(parcelleDuGroupe)]
	};
	return corps;
}

function controlerFin(page: Page, erreurs: string[]) {
	expect(erreurs, 'aucune erreur de script dans la page').toEqual([]);
	expectNoUnexpectedOffLocalRequest(page);
	attendreAucuneCarteNonReconnue(page);
}

// ---------------------------------------------------------------------------------------------------
// Widths, 200 % zoom and a phone held sideways (UI contract § 2.2, § 4; UI Considerations, overflow).
// ---------------------------------------------------------------------------------------------------

const FENETRES = [
	{ nom: '1440 px', largeur: 1440, hauteur: 900 },
	{ nom: '375 px', largeur: 375, hauteur: 800 },
	{ nom: '320 px', largeur: 320, hauteur: 640 },
	{ nom: 'zoom 200 % (720 × 450)', largeur: 720, hauteur: 450 },
	{ nom: 'téléphone en paysage (667 × 375)', largeur: 667, hauteur: 375 },
	{ nom: 'grand téléphone en paysage (844 × 390)', largeur: 844, hauteur: 390 }
] as const;

for (const fenetre of FENETRES) {
	test.describe(`Carte des parcelles — ${fenetre.nom}`, () => {
		let routes: RoutesCarte;
		let erreurs: string[];

		test.beforeEach(async ({ page, request }) => {
			erreurs = [];
			page.on('pageerror', (erreur) => erreurs.push(erreur.message));
			await page.setViewportSize({ width: fenetre.largeur, height: fenetre.hauteur });
			await installLocalNetworkGuard(page);
			routes = await installerRoutesCarte(page);
			await configurer(request);
		});

		test.afterEach(async ({ page }) => controlerFin(page, erreurs));

		test('rien ne déborde, « Fermer » reste dans la fenêtre, la carte tient dans la moitié de la hauteur ; seul le corps défile', async ({ page }, testInfo) => {
			const { dialogue, cadre, corps, fermer, panneau } = await ouvrirCarte(page);
			expect(routes.demandes.contexte).toEqual([`/carte/${BAN_ID}/contexte`]);
			await sansDebordement(page, fenetre.nom);
			await expect(fermer).toBeInViewport({ ratio: 1 });
			const hauteurCarte = (await cadre.boundingBox())!.height;
			// One hundredth of a pixel: Firefox lays out 50 % of 640 px as 320.00001 (matrix of 04.10-10).
			expect(hauteurCarte, 'la carte, contour compris, au plus la moitié de la hauteur de la fenêtre').toBeLessThanOrEqual(fenetre.hauteur / 2 + 0.01);
			expect(hauteurCarte).toBeGreaterThan(0);

			// Full screen without an edge up to 720 px; above, a box centred by the browser, with its edge.
			const boite = (await dialogue.boundingBox())!;
			const forme = await dialogue.evaluate((d) => {
				const style = getComputedStyle(d);
				return {
					rayon: style.borderTopLeftRadius,
					contour: style.borderTopWidth,
					attendu: getComputedStyle(document.querySelector('.public-site')!).getPropertyValue('--radius-panneau').trim()
				};
			});
			if (fenetre.largeur <= 720) {
				expect(boite).toEqual({ x: 0, y: 0, width: fenetre.largeur, height: fenetre.hauteur });
				expect(forme.rayon).toBe('0px');
				expect(forme.contour).toBe('0px');
			} else {
				expect(boite.width).toBeLessThan(fenetre.largeur);
				expect(boite.height).toBeLessThan(fenetre.hauteur);
				expect(Math.abs(boite.x - (fenetre.largeur - boite.width) / 2)).toBeLessThanOrEqual(1);
				expect(Math.abs(boite.y - (fenetre.hauteur - boite.height) / 2)).toBeLessThanOrEqual(1);
				expect(forme.rayon).toBe(forme.attendu);
				expect(forme.contour).toBe('1px');
			}
			await page.screenshot({ path: preuve(testInfo, `etats-${fenetre.largeur}x${fenetre.hauteur}-ouverture.png`), animations: 'disabled' });

			// Only the body scrolls. A wheel under the map moves the body, never the fiche behind; at the end
			// of the body, nothing chains to the page; a wheel over the head scrolls nothing.
			const defilementFiche = await page.evaluate(() => window.scrollY);
			const tete = (await dialogue.locator('.carte-tete').boundingBox())!;
			const basCarte = (await cadre.boundingBox())!;
			const sousLaCarte = { x: boite.x + boite.width / 2, y: Math.min(basCarte.y + basCarte.height + 12, boite.y + boite.height - 4) };
			await page.mouse.move(sousLaCarte.x, sousLaCarte.y);
			await page.mouse.wheel(0, 200);
			await expect.poll(() => corps.evaluate((c) => c.scrollTop)).toBeGreaterThan(0);
			// Wheel until the end: Firefox bounds the scroll of one wheel event (matrix of 04.10-10).
			await expect(async () => {
				await page.mouse.wheel(0, 4000);
				expect(await corps.evaluate((c) => Math.round(c.scrollHeight - c.clientHeight - c.scrollTop)), 'le corps est au bout').toBeLessThanOrEqual(1);
			}).toPass();
			await page.mouse.wheel(0, 2000);
			await page.mouse.move(tete.x + 8, tete.y + tete.height / 2);
			await page.mouse.wheel(0, 2000);
			// The wheel events are handled: a last scroll of the body proves the ones before were.
			await page.mouse.move(sousLaCarte.x, sousLaCarte.y);
			await page.mouse.wheel(0, -50);
			await expect.poll(() => corps.evaluate((c) => Math.round(c.scrollHeight - c.clientHeight - c.scrollTop))).toBeGreaterThan(1);
			expect(await page.evaluate(() => window.scrollY), 'la fiche derrière le dialogue n’a pas défilé').toBe(defilementFiche);
			expect(await dialogue.evaluate((d) => d.scrollTop), 'le dialogue lui-même ne défile pas').toBe(0);
			expect((await dialogue.locator('.carte-tete').boundingBox())!.y, 'la tête reste en place').toBeCloseTo(tete.y, 0);
			await expect(fermer).toBeInViewport({ ratio: 1 });
			await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
			await sansDebordement(page, `${fenetre.nom}, corps au bout`);
		});
	});
}

// ---------------------------------------------------------------------------------------------------
// Accessibility, type, accent, radius, printing, long texts and forced colours.
// ---------------------------------------------------------------------------------------------------

for (const largeur of [375, 1440]) {
	test.describe(`Carte des parcelles — gardes du site, dialogue ouvert, à ${largeur} px`, () => {
		let erreurs: string[];

		test.beforeEach(async ({ page, request }) => {
			erreurs = [];
			page.on('pageerror', (erreur) => erreurs.push(erreur.message));
			await page.setViewportSize({ width: largeur, height: 900 });
			await installLocalNetworkGuard(page);
			await installerRoutesCarte(page);
			await configurer(request);
		});

		test.afterEach(async ({ page }) => controlerFin(page, erreurs));

		test('axe sans violation avec un groupe montré puis le volet des sources par-dessus ; 14 px, graisses, aucun fond accent, un seul rayon', async ({ page }, testInfo) => {
			await ouvrirFiche(page);
			const avant = await fondsAccent(page, 'body');
			expect(avant.accent, 'jeton --color-accent non résolu : la mesure serait vacuous').not.toBe('rgba(0, 0, 0, 0)');
			await page.getByRole('button', { name: BOUTON_CARTE }).click();
			const { dialogue, cartes, groupe } = parties(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(cartes).toHaveCount(4);
			await cartes.nth(1).getByRole('button', { name: T.bascule }).click();
			await expect(groupe).toHaveCount(2);
			await expect(dialogue.locator('ul.carte-legende > li')).toHaveCount(3);

			await sansViolationAxe(page, `${largeur} px, groupe montré`);
			// Soft: a finding of one check must not hide the others.
			expect.soft(await verifierPlancherTypo(page, 'dialog.carte-dialogue'), `plancher de 14 px à ${largeur} px`).toEqual([]);
			expect.soft(await verifierGraisses(page, 'dialog.carte-dialogue'), `graisses à ${largeur} px`).toEqual([]);
			// No accent background in the dialog; the page keeps the count it had before the opening.
			expect.soft((await fondsAccent(page, 'dialog.carte-dialogue')).elements, 'aucun fond accent dans le dialogue').toEqual([]);
			expect.soft((await fondsAccent(page, 'body')).elements, 'fonds accent de la page, dialogue ouvert').toEqual(avant.elements);
			const rayons = await rayonsDuDialogue(page);
			expect(rayons.mesures, 'des contrôles du dialogue sont mesurés').toBeGreaterThan(5);
			expect.soft(rayons.fautifs, `un seul rayon (${rayons.attendu}) pour tout contrôle`).toEqual([]);
			await page.screenshot({ path: preuve(testInfo, `etats-${largeur}-groupe.png`), animations: 'disabled' });

			// The panel of sources over the dialog.
			const detail = dialogue.getByRole('button', { name: /^Détail\s:\ssources et limites de la carte$/ });
			await detail.click();
			const volet = page.getByRole('dialog', { name: 'Sources et limites de la carte' });
			await expect(volet).toBeVisible();
			// axe measures colours: the panel is read once its opening is over, never in the middle of a fade.
			await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
			await sansViolationAxe(page, `${largeur} px, volet des sources par-dessus`);
			await page.keyboard.press('Escape');
			await expect(volet).toBeHidden();
			await expect(detail).toBeFocused();
			await expect(dialogue).toBeVisible();
		});
	});
}

test.describe('Carte des parcelles — impression, textes longs, contraste forcé', () => {
	let routes: RoutesCarte;
	let erreurs: string[];

	test.beforeEach(async ({ page, request }) => {
		erreurs = [];
		page.on('pageerror', (erreur) => erreurs.push(erreur.message));
		await installLocalNetworkGuard(page);
		routes = await installerRoutesCarte(page);
		await configurer(request);
	});

	test.afterEach(async ({ page }) => controlerFin(page, erreurs));

	test('impression : le dialogue et la rangée d’action sont absents, l’image statique de la fiche est imprimée', async ({ page }) => {
		await page.setViewportSize({ width: 1024, height: 900 });
		const { dialogue } = await ouvrirCarte(page);
		const figure = page.locator('.adresse-carte figure.carte');
		await page.emulateMedia({ media: 'print' });
		await expect(dialogue).toBeHidden();
		await expect(page.locator('dialog.carte-dialogue')).toBeHidden();
		await expect(figure.locator('.carte-action')).toBeHidden();
		await expect(figure.locator('img')).toHaveCount(2);
		for (const image of await figure.locator('img').all()) await expect(image).toBeVisible();
		await expect.poll(() => figure.locator('img').evaluateAll((imgs) => imgs.map((i) => (i as HTMLImageElement).naturalWidth))).toEqual([448, 448]);
		await page.emulateMedia({ media: 'screen' });
		await expect(dialogue).toBeVisible();
	});

	test('320 px, textes les plus longs : T-34 et un groupe de douze parcelles (T-21, T-23 avec sa surface, T-26), rien ne déborde, rien n’est coupé', async ({ page }, testInfo) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await page.setViewportSize({ width: 320, height: 640 });
		routes.repondre('ventes', { status: 200, json: venteDeDouze() });
		const { dialogue, cartes, groupe, panneau, etat } = await ouvrirCarte(page);
		const pied = cartes.nth(1).locator('.vente-groupe');
		await expect(pied.locator('p')).toHaveText(['Vendue avec 11 autres parcelles']);
		// Every tile of parcels of the framed view fails: T-34 and T-41 stay on screen with the group.
		routes.repondre('parcelles', { status: 503, json: { title: 'Parcelles indisponibles' } });
		await pied.getByRole('button', { name: T.bascule }).click();
		await expect(groupe).toHaveCount(12);
		await expect(etat).toHaveText(T.parcellesEnEchec);
		await expect(dialogue.getByRole('button', { name: T.rechargerParcelles })).toBeVisible();
		const textes = pied.locator('p');
		await expect(textes.nth(1)).toHaveText(/^12 parcelles vendues ensemble · juin 2024 · 4\s812\sm² au total$/);
		await expect(textes.nth(2)).toHaveText('Ne dit pas qui les possède aujourd’hui.');
		await expect(panneau.locator('.vente-groupe p').filter({ hasText: /de ces parcelles (sont|est) hors de la vue\.$/ })).toHaveCount(1);
		await expect(dialogue.locator('.carte-limites')).toContainText(`Ventes connues${NB}: 2021-2025.`);

		await sansDebordement(page, '320 px, textes longs');
		expect(await textesCoupes(page), 'aucun texte coupé ni hors du dialogue').toEqual([]);
		await etat.scrollIntoViewIfNeeded();
		await page.screenshot({ path: preuve(testInfo, 'etats-320-t34.png'), animations: 'disabled' });
		await pied.scrollIntoViewIfNeeded();
		await page.screenshot({ path: preuve(testInfo, 'etats-320-t23-douze.png'), animations: 'disabled' });
	});

	test('contraste forcé : les traits, le tireté du groupe et les pastilles restent tracés', async ({ page }, testInfo) => {
		await page.setViewportSize({ width: 375, height: 800 });
		const { dialogue, cartes, groupe, vue } = await ouvrirCarte(page);
		await cartes.nth(1).getByRole('button', { name: T.bascule }).click();
		await expect(groupe).toHaveCount(2);
		await page.emulateMedia({ forcedColors: 'active' });
		expect(await page.evaluate(() => matchMedia('(forced-colors: active)').matches), 'contraste forcé émulé').toBe(true);
		await vue.scrollIntoViewIfNeeded();

		const traces = await dialogue.evaluate((d) => {
			const mesurer = (selecteur: string) =>
				[...d.querySelectorAll(selecteur)].map((el) => {
					const style = getComputedStyle(el);
					const boite = el.getBoundingClientRect();
					return {
						largeur: boite.width,
						hauteur: boite.height,
						visible: style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0,
						trait: style.stroke,
						epaisseur: parseFloat(style.strokeWidth),
						opaciteTrait: Number(style.strokeOpacity),
						tirets: style.strokeDasharray,
						remplissage: style.fill,
						opaciteRemplissage: Number(style.fillOpacity)
					};
				});
			return {
				parcelles: mesurer('path.parcel'),
				adresse: mesurer('path.parcel-address'),
				groupe: mesurer('path.parcel-group'),
				pastilles: mesurer('path.parcel-sold-mark'),
				echantillons: mesurer('ul.carte-legende svg [class^="echantillon-"]')
			};
		});
		const nommer = (nom: string, liste: unknown[]) => `${nom} : ${JSON.stringify(liste)}`;
		expect(traces.parcelles, 'parcelles').toHaveLength(PARCELLES.length);
		for (const t of traces.parcelles) {
			expect(t.visible && t.largeur > 0 && t.hauteur > 0, nommer('parcelle', [t])).toBe(true);
			expect(t.trait !== 'none' && t.epaisseur > 0 && t.opaciteTrait > 0, nommer('trait de parcelle', [t])).toBe(true);
		}
		expect(traces.adresse.length, 'contour de l’adresse').toBeGreaterThan(0);
		for (const t of traces.adresse) expect(t.visible && t.largeur > 0 && t.trait !== 'none' && t.epaisseur >= 4, nommer('adresse', [t])).toBe(true);
		expect(traces.groupe, 'groupe').toHaveLength(2);
		for (const t of traces.groupe) {
			expect(t.visible && t.largeur > 0 && t.trait !== 'none' && t.epaisseur > 0 && t.opaciteTrait > 0, nommer('groupe', [t])).toBe(true);
			expect(t.tirets.replace(/px/g, '').replace(/,\s*/g, ' '), nommer('tireté du groupe', [t])).toBe('8 6');
		}
		expect(traces.pastilles.length, 'pastilles').toBeGreaterThan(0);
		for (const t of traces.pastilles) {
			expect(t.visible && t.largeur > 0 && t.hauteur > 0, nommer('pastille', [t])).toBe(true);
			expect(t.remplissage !== 'none' && t.opaciteRemplissage > 0, nommer('remplissage de pastille', [t])).toBe(true);
		}
		expect(traces.echantillons, 'échantillons de la légende').toHaveLength(3);
		for (const t of traces.echantillons) expect(t.visible && t.largeur > 0 && t.hauteur > 0, nommer('échantillon', [t])).toBe(true);
		await page.screenshot({ path: preuve(testInfo, 'etats-375-contraste-force.png'), animations: 'disabled' });
		await page.emulateMedia({ forcedColors: 'none' });
	});
});

// ---------------------------------------------------------------------------------------------------
// States of § 3 that no other spec of the map plays.
// ---------------------------------------------------------------------------------------------------

test.describe('Carte des parcelles — états du § 3 à 375 px', () => {
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

	test.afterEach(async ({ page }) => controlerFin(page, erreurs));

	test('chargement des parcelles : T-05 dans la ligne d’état, liste occupée, jamais « Aucune parcelle »', async ({ page }) => {
		const lacher = await retenir(page, /\/carte\/parcelles\//);
		const { dialogue, etat, liste } = await ouvrirSansAttendre(page);
		await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
		await expect(etat).toHaveText(T.chargementParcelles);
		await expect(liste).toHaveAttribute('aria-busy', 'true');
		await expect(dialogue).not.toContainText(/Aucune parcelle/);
		await expect(liste.locator('.parcelles-compte')).toHaveCount(0);
		await expect(dialogue.getByText(T.choisir)).toHaveCount(0);
		await expect(dialogue.locator('path.parcel')).toHaveCount(0);
		lacher();
		await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
		await expect(liste).toHaveAttribute('aria-busy', 'false');
		await expect(etat).toHaveText('');
	});

	test('chargement des ventes : surface et référence tout de suite, T-06 à la place des ventes, jamais « Aucune vente »', async ({ page }) => {
		const lacher = await retenir(page, /\/carte\/[^/]+\/parcelle\/[^/]+\/ventes/);
		const { panneau, ventes, cartes } = await ouvrirSansAttendre(page);
		await expect(panneau.getByRole('heading', { level: 3 })).toHaveText('Parcelle ZA 0001');
		await expect(panneau.locator('dl dd')).toHaveText([`450${NB}m²`, '00000 000 ZA 0001']);
		await expect(ventes).toHaveAttribute('aria-busy', 'true');
		await expect(ventes).toContainText(T.chargementVentes);
		await expect(ventes).not.toContainText(/Aucune vente/);
		await expect(ventes.getByRole('alert')).toHaveCount(0);
		await expect(cartes).toHaveCount(0);
		lacher();
		await expect(cartes).toHaveCount(4);
		await expect(ventes).toHaveAttribute('aria-busy', 'false');
		await expect(ventes).not.toContainText(T.chargementVentes);
	});

	test('cadastre hors couverture : l’état hors couverture à la place de la liste, le fond et le point restent, jamais « Aucune parcelle »', async ({ page }) => {
		const contexte = copie(EXEMPLE_CONTEXTE);
		contexte.donnees.parcelles = { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' };
		contexte.donnees.parcelle_adresse = { etat: 'out_of_coverage', id_parcelle: null, motif: 'territoire_hors_perimetre', nombre_parcelles: null };
		const tuile = copie(EXEMPLE_TUILE);
		tuile.donnees.parcelles = { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' };
		tuile.donnees.collection.features = [];
		routes.repondre('contexte', { status: 200, json: contexte });
		routes.repondre('parcelles', { status: 200, json: tuile });
		const { dialogue, etat, liste, vue } = await ouvrirSansAttendre(page);
		await expect(dialogue.locator('.carte-vue.leaflet-container')).toBeVisible();
		await expect.poll(() => routes.demandes.parcelles.length).toBeGreaterThan(0);
		await expect(liste.locator('[data-state="out_of_coverage"]')).toContainText('département non hébergé');
		await expect(liste.locator('ul.parcelles-lignes')).toHaveCount(0);
		await expect(liste.getByRole('checkbox')).toHaveCount(0);
		// The ground and the point stay.
		await expect(dialogue.locator('.carte-fond .leaflet-tile').first()).toBeAttached();
		await expect(dialogue.locator('.point-adresse')).toHaveCount(1);
		await expect(dialogue).not.toContainText(/Aucune parcelle/);
		// A move: the status line never says an empty view.
		const cadre = (await vue.boundingBox())!;
		await glisser(page, { x: cadre.x + cadre.width / 2, y: cadre.y + cadre.height / 2 }, -120, 0);
		await expect.poll(() => routes.demandes.parcelles.length).toBeGreaterThan(1);
		await expect(etat).toHaveText('');
		await expect(dialogue).not.toContainText(/Aucune parcelle/);
	});

	for (const [motif, phrase] of [
		['point_hors_parcelle', /^Le point de l'adresse ne tombe dans aucune parcelle du cadastre importé\s: aucune vente n'y est rattachée\.$/],
		['parcelle_indeterminee', /^Le point de l'adresse ne désigne pas une seule parcelle\.$/]
	] as const) {
		test(`parcelle de l’adresse non établie (${motif}) : la phrase servie, aucun contour épais, aucune sélection, T-35`, async ({ page }) => {
			const contexte = copie(EXEMPLE_CONTEXTE);
			contexte.donnees.parcelle_adresse = { etat: 'not_available', id_parcelle: null, motif, nombre_parcelles: null };
			routes.repondre('contexte', { status: 200, json: contexte });
			const { dialogue } = await ouvrirSansAttendre(page);
			await expect(dialogue.locator('path.parcel')).toHaveCount(PARCELLES.length);
			await expect(dialogue.locator('.carte-motif-parcelle')).toHaveText(phrase);
			await expect(dialogue.locator('path.parcel-address')).toHaveCount(0);
			await expect(dialogue.locator('path.parcel-selected')).toHaveCount(0);
			await expect(dialogue.getByText(T.choisir)).toBeVisible();
			// Never the nearest parcel: no line of the list is chosen.
			await expect(dialogue.locator('section.parcelles [aria-current="true"]')).toHaveCount(0);
		});
	}
});

// ---------------------------------------------------------------------------------------------------
// The installer of the pictures of the head (`repondreCarteStatique`): its pattern, without a page.
// ---------------------------------------------------------------------------------------------------

test('le motif des images de tête reconnaît plan.png et parcelles.png d’une clé BAN, et refuse les routes de la carte', () => {
	// BAN keys of the shapes of `BAN_ID_PATTERN`: a street key, a numbered address, a suffix (percent-encoded).
	const cles = [BAN_ID, '44109_zz01_00002', '2A004_abcd', '97411_B12345_00001_bis', '75056_1234_00007_a%20b'];
	for (const cle of cles) {
		for (const image of ['plan', 'parcelles']) expect(IMAGE_DE_TETE.test(`/carte/${cle}/${image}.png`), `${cle} ${image}`).toBe(true);
	}
	// The four routes of the interactive map (`src/lib/api/carte-parcelles.ts`), and near misses.
	const refuses = [
		`/carte/${BAN_ID}/contexte`,
		'/carte/parcelles/18/129856/90112',
		'/carte/fond/18/129856/90112.png',
		`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/ventes`,
		'/carte/fond/plan.png',
		'/carte/parcelles/parcelles.png',
		`/carte/${BAN_ID}/fond.png`,
		`/carte/${BAN_ID}/plan.png/contexte`,
		`/carte/${BAN_ID}/parcelle/${PARCELLE_ADRESSE}/plan.png`,
		'/src/lib/components/adresse/carte/plan.png'
	];
	for (const chemin of refuses) expect(IMAGE_DE_TETE.test(chemin), chemin).toBe(false);
});
