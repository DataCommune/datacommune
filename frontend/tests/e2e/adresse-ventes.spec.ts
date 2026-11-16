import { mkdir, readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { ADRESSE_44109_zz04_00004_DPE_OBSERVED, ADRESSE_44109_zz04_00004_OBSERVED } from '../../src/lib/data/__fixtures__/adresse-44109-zz04-00004-20261013';
import {
	ADRESSES_SUFFIXES,
	BAN_ID_44109,
	BAN_ID_SUFFIXE_2,
	BAN_ID_SUFFIXE_2_BIS,
	BAN_ID_SUFFIXE_QUINQUIES,
	DECLAREES_SUFFIXE_NON_IMPORTE,
	PREMIERE_PAGE_DECLAREES,
	PREMIERE_PAGE_REGLE_NUMERO,
	PREMIERE_PAGE_REGLE_SUFFIXE,
	type CleSuffixe,
	SOURCE_DVF,
	VENTES_DECLAREES_44109_AVEC_VEFA,
	VENTES_DECLAREES_44109_INCLUSES,
	VENTES_DECLAREES_HORS_PARCELLE_INCLUSES,
	VENTES_DECLAREES_VEFA_HORS_PAGE,
	VENTES_DECLAREES_VEFA_SEULES,
	VENTES_HORS_PARCELLE,
	VENTES_PARCELLE_44109,
	VENTES_PARCELLE_44109_AVEC_VEFA,
	avecCommuneDeLAdresse,
	ficheHorsParcelle
} from './fixtures/adresse-ventes';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Plan 04-05 (LIEU-01, LIEU-02, SEM-01, SEM-02, UI-01; D-114, D-115) — the sales of the parcel of
// the point and the similar sales on the address page.
//
// What this spec locks, on the synthetic source of this instance (exact contract, SSR and client
// navigation alike):
//   - tracer: « Ventes publiées sur la parcelle » under the documents, ONE common line « DVF (DGFiP)
//     · {période} · ventes de la parcelle, rattachées à aucun logement », the cards in the served
//     order; a withheld price is the neutral badge « Prix non affiché » and no amount of it reaches
//     the HTML; the reason is read in the panel of the section;
//   - the states: a served zero with its period, a point outside every parcel and several parcels
//     said as such, the outage of the section alone (identity, landmarks and documents displayed);
//   - the pagination bound to the DVF generation, with and without JavaScript; a card ≤ 160 px at
//     375 px; axe WCAG 2.1 AA without violation.

type Reply = { status: number; body: unknown; gate?: string };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const NB = ' ';
const BAN_ID = '85191_0801_00001';
const GENERATION = 'fedcba9876543210';
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
// Keys and amounts the API never serves for a withheld price: sent here to prove the page neither
// renders nor serializes them.
const SENTINELLE = 'SENTINELLE-0405';
const MONTANT_RETENU = 312345;

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };
const SOURCE_GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM / MTE', url: 'https://www.georisques.gouv.fr' };

const IDENTITE = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: '1 Rue des Ventes, 85000 La Roche-sur-Yon',
	numero: '1',
	rep: null,
	nom_voie: 'Rue des Ventes',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000BA0001', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

const REPERES = {
	dpe: {
		...valeur({ field_id: 'adresse.dpe.nombre_declares', valeur: 0, unite: 'none', source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
		selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] }
	},
	ventes: {
		nombre: valeur({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 3, unite: 'count', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' }),
		derniere: valeur({ field_id: 'adresse.dvf.ventes_parcelle.derniere', valeur: '2025-11-20', unite: 'none', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' })
	},
	argiles: valeur({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'au_point' }),
	reference_commune: {
		maisons: valeur({ field_id: 'adresse.reference_commune.prix_median_maison', etat: 'insufficient_sample', unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference' }),
		appartements: valeur({ field_id: 'adresse.reference_commune.prix_median_appartement', etat: 'insufficient_sample', unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference' })
	}
};

const okFiche = (identite: unknown = IDENTITE): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: { identite, couverture: { etat: 'heberge', motif: null }, reperes: REPERES }, meta: { source: [SOURCE_BAN] } }
});

const SANS_DOCUMENT: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value', motif: null, total: 0, page: 1, pages: 1, par_page: 10, generation: '0123456789abcdef',
			distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 }, date_import: '2026-09-30', documents: [],
			selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] },
			signal_plusieurs_logements: null, constat_d110: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur'
		}
	}
};

function vente(partiel: Record<string, unknown> = {}) {
	return {
		date: '2025-11-20',
		nature: 'Vente',
		composition: { maisons: 1, appartements: 0, dependances: 1, locaux_activite: 0 },
		surface_batie: 90,
		prix: 238000,
		prix_m2: 2644,
		prix_affiche: true,
		minimisation: null,
		distance_m: null,
		...partiel
	};
}

const TROIS = [
	vente(),
	vente({ date: '2021-06-30', composition: { maisons: 0, appartements: 2, dependances: 1, locaux_activite: 0 }, surface_batie: null, prix: 225000, prix_m2: null }),
	vente({ date: '2019-03-15', composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 }, surface_batie: 85, prix: 210000, prix_m2: 2471 })
];

/** The withheld sale of B as the API serves it, plus keys the API never serves. */
const RETENUE = {
	...vente({ date: '2025-06-01', surface_batie: 95, prix: null, prix_m2: null, prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' } }),
	id_mutation: SENTINELLE,
	valeur_fonciere: MONTANT_RETENU
};

function ventes(partiel: Record<string, unknown> = {}) {
	return {
		etat: 'value',
		motif: null,
		nombre_parcelles: null,
		rattachement: 'parcelle_du_point',
		source: SOURCE_DVF,
		periode: '2021-2025',
		total: 3,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: GENERATION,
		ventes: TROIS,
		...partiel
	};
}

const okVentes = (partiel: Record<string, unknown> = {}, gate?: string): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: ventes(partiel) },
	...(gate ? { gate } : {})
});

/** Twenty-three sales on three pages: the same date, the surface tells them apart. */
const VINGT_TROIS = { total: 23, pages: 3 };
const pageDe = (n: number) =>
	Array.from({ length: n === 3 ? 3 : 10 }, (_, rang) => vente({ date: '2024-05-01', surface_batie: 41 + (n - 1) * 10 + rang, prix: 150001 + (n - 1) * 10 + rang, prix_m2: null }));

function attente(pathname: string, query: Record<string, string>, replies: Reply[], minCalls = replies.length): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}

const fiche = (replies: Reply[]) => attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, replies.map(enRapport));
const documents = (n = 1) => attente(`/v1/adresse/${BAN_ID}/dpe`, { page: '1' }, Array.from({ length: n }, () => SANS_DOCUMENT));
const liste = (query: Record<string, string>, replies: Reply[]) => attente(`/v1/adresse/${BAN_ID}/ventes`, query, replies);

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	// 04-06: the companion read of the commune of the address (reference theme), none required.
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: avecCommuneDeLAdresse(expectations) }
	});
	expect(res.ok()).toBe(true);
}

async function expectSourceContractMet(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const audit = (await res.json()) as { violations: unknown[]; unmetExpectations: unknown[] };
	expect(audit.violations, JSON.stringify(audit.violations)).toEqual([]);
	expect(audit.unmetExpectations, JSON.stringify(audit.unmetExpectations)).toEqual([]);
}

/** The URL of a page of this spec (it opened the theme « Ventes » until 04.7-03). */
function dansLesVentes(url: string): string {
	// Since 04.7-03 the lists are read in the page itself: no `theme` to carry any more.
	return url;
}

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(dansLesVentes(url));
	await hydrated;
}

const section = (page: Page) => page.locator('section.ventes');
const cartesParcelle = (page: Page) => page.locator('section.ventes > ol.ventes-cartes > li');

/** Text of a locator with every no-break space as an ordinary one. */
async function texte(locator: ReturnType<Page['locator']>): Promise<string> {
	return ((await locator.textContent()) ?? '').replace(/[  ]/g, ' ').replace(/\s+/g, ' ').trim();
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

// --- Task 1 — sales of the parcel of the point (D-115, LIEU-02) -------------------------------

test.describe('Tracer — ventes publiées sur la parcelle', () => {
	test('trois ventes dans l’ordre servi, une ligne commune, prix et prix au m², sous les documents', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes()])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(section(page).getByRole('heading', { level: 3 })).toHaveText('Ventes publiées sur la parcelle');
		// Under the documents (UI-SPEC point 5): DOM order = visual order.
		const apres = await page.evaluate(() => {
			const documents = document.querySelector('section.doclist');
			const ventes = document.querySelector('section.ventes');
			return Boolean(documents && ventes && documents.compareDocumentPosition(ventes) & Node.DOCUMENT_POSITION_FOLLOWING);
		});
		expect(apres).toBe(true);
		await expect(section(page).locator('.ventes-commune span').first()).toHaveText(
			'DVF (DGFiP) · 2021-2025 · ventes de la parcelle, rattachées à aucun logement'
		);
		await expect(cartesParcelle(page)).toHaveCount(3);
		expect(await texte(cartesParcelle(page).nth(0).locator('.ventecard-l1'))).toBe('novembre 2025 · Vente');
		expect(await texte(cartesParcelle(page).nth(0).locator('.ventecard-l2'))).toBe('1 maison, 1 dépendance · surface bâtie déclarée 90 m²');
		expect(await texte(cartesParcelle(page).nth(0).locator('.ventecard-l3'))).toBe('238 000 € · 2 644 €/m²');
		expect(await texte(cartesParcelle(page).nth(1).locator('.ventecard-l3'))).toBe('225 000 €');
		expect(await texte(cartesParcelle(page).nth(2).locator('.ventecard-l1'))).toBe('mars 2019 · Vente');
		await expect(page.getByText('Ventes récentes dans l’immeuble')).toHaveCount(0);
	});

	test('vente isolée récente : badge « Prix non affiché », motif au volet, aucun montant ni identifiant dans le HTML', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes({ total: 1, ventes: [RETENUE] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(cartesParcelle(page)).toHaveCount(1);
		await expect(cartesParcelle(page).first().locator('.ventecard-badge')).toHaveText('Prix non affiché');
		const html = await page.content();
		expect(html).not.toContain(SENTINELLE);
		expect(html).not.toMatch(/312[\s  ]?345/);

		await section(page).getByRole('button', { name: 'Source et définition : ventes publiées sur la parcelle' }).click();
		const volet = page.getByRole('dialog', { name: 'Ventes publiées sur la parcelle' });
		await expect(volet).toBeVisible();
		// 04-DECISIONS-TEXTES row 32 (the former sentence announced a price band that is never served).
		await expect(volet).toContainText(`Vente isolée récente${NB}: le prix n'est pas publié, pour ne pas désigner un bien et son acheteur.`);
		await expect(volet).not.toContainText('tranche');
		const rattachement = volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: 'Rattachement' }) });
		await expect(rattachement.locator('dd')).toHaveText("Parcelle qui contient le point de l'adresse");
	});
});

test.describe('Ventes de la parcelle — états (SEM-01, N-01, N-02)', () => {
	test('zéro observé : « Aucune vente publiée sur la parcelle dans DVF (2021-2025). »', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes({ total: 0, ventes: [] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(section(page).locator('.ventes-phrase')).toHaveText('Aucune vente publiée sur la parcelle dans DVF (2021-2025).');
		await expect(cartesParcelle(page)).toHaveCount(0);
	});

	for (const [motif, nombre, badge, phrase] of [
		['point_hors_parcelle', null, 'Point hors parcelle', `Le point de l'adresse ne tombe dans aucune parcelle du cadastre importé${NB}: aucune vente n'y est rattachée.`],
		['parcelles_multiples', 2, 'Plusieurs parcelles', "Le point de l'adresse touche 2 parcelles"]
	] as const) {
		test(`${motif} : dit comme tel, jamais un zéro`, async ({ page, request }) => {
			const absence = { etat: 'not_available', motif, nombre_parcelles: nombre, total: null, periode: null, generation: null, ventes: [] };
			await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes(absence)])]);
			await open(page, `/adresse/${BAN_ID}`);

			await expect(section(page).locator('.state-badge')).toHaveText(badge);
			await expect(section(page).locator('.state-help')).toContainText(phrase);
			await expect(section(page)).not.toContainText('Aucune vente');
		});
	}

	test('panne 503 : encart de section, identité, repères et documents affichés ; « Recharger les ventes » relit la liste seule', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(2),
			liste({ page: '1' }, [{ status: 503, body: { title: 'Ventes indisponibles', status: 503, code: 'ventes_indisponibles' } }, okVentes()])
		]);
		// Every theme shown (04.5-02, `vue=tout`): the documents stay displayed next to the outage.
		await open(page, `/adresse/${BAN_ID}?vue=tout`);

		await expect(section(page).getByRole('alert')).toContainText("Les ventes n'ont pas pu être lues. Le reste de la page reste affiché.");
		// D-33 (04.8-03): number and readable street in the h1, postal code and commune on the identity line.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('1 rue des Ventes');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		await expect(page.locator('section.doclist .doclist-phrase')).toBeVisible();

		await page.getByRole('button', { name: 'Recharger les ventes' }).click();
		await expect(cartesParcelle(page)).toHaveCount(3);
		await expect(section(page).getByRole('alert')).toHaveCount(0);
	});
});

test.describe('Ventes de la parcelle — pagination liée à la génération DVF', () => {
	test('avec JavaScript : annonce « Ventes 11 à 20 sur 23 », focus sur le h2, URL ventes_page & vg', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(2),
			liste({ page: '1' }, [okVentes({ ...VINGT_TROIS, ventes: pageDe(1) })]),
			liste({ page: '2', vg: GENERATION }, [okVentes({ ...VINGT_TROIS, page: 2, ventes: pageDe(2) }, 'ventes-2')])
		]);
		await open(page, `/adresse/${BAN_ID}`);
		await expect(section(page).locator('.ventes-range')).toHaveText('Ventes 1 à 10 sur 23');

		await section(page).getByRole('link', { name: 'Page suivante' }).click();
		await expect(section(page)).toHaveAttribute('aria-busy', 'true');
		await expect(cartesParcelle(page)).toHaveCount(10);
		const relache = await request.post(`${E2E_SOURCE_ORIGIN}/__release`, { data: { gate: 'ventes-2' } });
		expect(relache.ok()).toBe(true);
		await expect(section(page)).toHaveAttribute('aria-busy', 'false');
		await expect(section(page).locator('.ventes-annonce')).toHaveText('Ventes 11 à 20 sur 23');
		await expect(section(page).getByRole('heading', { level: 3 })).toBeFocused();
		expect(await texte(cartesParcelle(page).first().locator('.ventecard-l2'))).toBe('1 maison, 1 dépendance · surface bâtie déclarée 51 m²');
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?ventes_page=2&vg=${GENERATION}#ventes$`));
	});

	test('génération périmée (409) : 303 vers la première page des ventes, les autres paramètres gardés', async ({ page, request }) => {
		await configure(request, [
			attente(cheminRapport(BAN_ID), REQUETE_RAPPORT, [okFiche()].map(enRapport), 0),
			attente(`/v1/adresse/${BAN_ID}/dpe`, { page: '2' }, [SANS_DOCUMENT], 0),
			liste({ page: '2', vg: 'ffffffffffffffff' }, [{ status: 409, body: { title: 'x', status: 409, code: 'generation_changee' } }])
		]);

		const perimee = await page.request.get(`/adresse/${BAN_ID}?page=2&ventes_page=2&vg=ffffffffffffffff`, { maxRedirects: 0 });
		expect(perimee.status()).toBe(303);
		expect(perimee.headers()['location']).toBe(`/adresse/${BAN_ID}?page=2&ventes_page=1`);
	});
});

test.describe('Ventes de la parcelle — sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('les liens de page marchent sans JavaScript (ventes_page & vg)', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche(), okFiche()]),
			documents(2),
			liste({ page: '1' }, [okVentes({ ...VINGT_TROIS, ventes: pageDe(1) })]),
			liste({ page: '2', vg: GENERATION }, [okVentes({ ...VINGT_TROIS, page: 2, ventes: pageDe(2) })])
		]);
		await page.goto(dansLesVentes(`/adresse/${BAN_ID}`));

		await section(page).getByRole('link', { name: 'Page suivante' }).click();
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?ventes_page=2&vg=${GENERATION}#ventes$`));
		await expect(section(page).locator('.ventes-range')).toHaveText('Ventes 11 à 20 sur 23');
		await expect(cartesParcelle(page)).toHaveCount(10);
	});

	// A link of the sales written before 04.5-02 carries no `theme`, and the server never sees its
	// `#ventes`: the parameters of the list name the theme of the sales (D45-11).
	// An old URL with the parameters of both lists names two themes; only its fragment, which the
	// server never sees, chose between them: both stay shown and open, on a wide screen too.
});

test.describe('Ventes de la parcelle — hauteur de carte et accessibilité', () => {
	test('375 px : carte de vente ≤ 160 px, aucun débordement', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes({ total: 4, ventes: [...TROIS, RETENUE] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const hauteurs = await cartesParcelle(page).evaluateAll((lis) => lis.map((li) => Math.round(li.getBoundingClientRect().height * 10) / 10));
		console.log(`RELEVE 375 cartes-ventes=${hauteurs.join('/')}`);
		for (const hauteur of hauteurs) expect(hauteur).toBeLessThanOrEqual(160);
		const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
		expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	});

	test('axe WCAG 2.1 AA : aucune violation (ventes servies, prix retenu, volet ouvert)', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes({ ...VINGT_TROIS, ventes: [RETENUE, ...pageDe(1).slice(1)] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const page1 = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(page1.violations, JSON.stringify(page1.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		await section(page).getByRole('button', { name: 'Source et définition : ventes publiées sur la parcelle' }).click();
		await expect(page.getByRole('dialog', { name: 'Ventes publiées sur la parcelle' })).toBeVisible();
		await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
		const volet = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(volet.violations, JSON.stringify(volet.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		console.log(`RELEVE axe ventes violations=${page1.violations.length}+${volet.violations.length}`);
	});
});

// --- Task 2 — similar sales (D-114): criteria visible and editable, served count and order --------

const CRITERES_SERVIS = {
	type: 'maison',
	surface: 90,
	tolerance: 20,
	surface_min: 72,
	surface_max: 108,
	perimetre: 'rayon',
	rayon_m: 1000,
	periode: { annees: 5, debut: 2021, fin: 2025 }
};

/** The six similar sales of the recipe task 4 (synthetic source), served order: distance, date. */
const SIX = [
	vente({ date: '2025-02-01', surface_batie: 90, prix: 300000, prix_m2: 3333, distance_m: 120 }),
	vente({ date: '2024-01-01', surface_batie: 90, prix: 305000, prix_m2: 3389, distance_m: 120 }),
	vente({ date: '2024-06-01', surface_batie: 100, prix: 310000, prix_m2: 3100, distance_m: 400 }),
	{ ...RETENUE, date: '2025-07-01', distance_m: 700 },
	vente({ date: '2022-03-01', surface_batie: 88, prix: 270000, prix_m2: 3068, distance_m: 800 }),
	vente({ date: '2023-01-15', surface_batie: 80, prix: 250000, prix_m2: 3125, distance_m: 900 })
];

function comparables(partiel: Record<string, unknown> = {}) {
	return {
		etat: 'value',
		motif: null,
		rattachement: 'rayon_du_point',
		source: SOURCE_DVF,
		criteres_appliques: CRITERES_SERVIS,
		effectif: 6,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: GENERATION,
		ventes: SIX,
		...partiel
	};
}

const okComparables = (partiel: Record<string, unknown> = {}, gate?: string): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: comparables(partiel) },
	...(gate ? { gate } : {})
});

/** Query of the recipe task 4 as submitted by the GET form (every field of the form). */
const RECETTE = { type: 'maison', surface: '90', tolerance: '20', rayon: '1000', periode: '5' };
const URL_RECETTE = 'type=maison&surface=90&tolerance=20&rayon=1000&periode=5';
const ressemblantes = (query: Record<string, string>, replies: Reply[]) =>
	attente(`/v1/adresse/${BAN_ID}/ventes-ressemblantes`, query, replies);
const zeroVentes = (n: number) => liste({ page: '1' }, Array.from({ length: n }, () => okVentes({ total: 0, ventes: [] })));

const sousSection = (page: Page) => page.locator('section#ventes-ressemblantes');
const cartesRessemblantes = (page: Page) => sousSection(page).locator('ol.ventes-cartes > li');
const PHRASE_RECETTE = '6 ventes ressemblantes · maisons · 72 à 108 m² · 1 km · 2021 → 2025';

/** Opens the panel of the similar sales from its link when it is not open yet. */
async function ouvrirPanneau(page: Page) {
	const panneau = page.locator('dialog.panneau-comparables');
	if (!(await panneau.isVisible())) {
		await page.locator('.actions-plus summary').click();
		await page.getByRole('link', { name: 'Ventes ressemblantes' }).first().click();
	}
	await expect(panneau).toBeVisible();
}

/** Fills the criteria of the recipe task 4 and submits the GET form. */
async function remplirRecette(page: Page) {
	await ouvrirPanneau(page);
	const formulaire = sousSection(page).locator('form');
	await formulaire.getByRole('radio', { name: 'Maison' }).check();
	await formulaire.getByLabel('Surface (m²)').fill('90');
	await formulaire.getByLabel('Rayon').selectOption('1000');
	await formulaire.getByRole('button', { name: 'Afficher les ventes ressemblantes' }).click();
}

async function expectRecetteServie(page: Page) {
	await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?${URL_RECETTE}#ventes-ressemblantes$`));
	expect(await texte(sousSection(page).locator('.comparables-effectif > span').first())).toBe(PHRASE_RECETTE);
	await expect(cartesRessemblantes(page)).toHaveCount(6);
	expect(await texte(cartesRessemblantes(page).first().locator('.ventecard-l1'))).toBe("février 2025 · Vente · à 120 m (à vol d'oiseau)");
	await expect(cartesRessemblantes(page).nth(3).locator('.ventecard-badge')).toHaveText('Prix non affiché');
	// The form keeps the criteria, shareable as they are in the URL.
	await expect(sousSection(page).getByRole('radio', { name: 'Maison' })).toBeChecked();
	await expect(sousSection(page).getByLabel('Surface (m²)')).toHaveValue('90');
	await expect(sousSection(page).getByLabel('Rayon')).toHaveValue('1000');
	const html = await page.content();
	expect(html).not.toContain(SENTINELLE);
	const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
	expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

test.describe('Ventes ressemblantes — sans type, puis tâche 4 de la recette avec JavaScript', () => {
	test('sans type : invitation, aucun type coché, aucune lecture des ventes ressemblantes', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1)]);
		await open(page, `/adresse/${BAN_ID}`);

		// C38: its own panel, opened from its link (UI-SPEC v3 § 4.8).
		await ouvrirPanneau(page);
		await expect(page.locator('dialog.panneau-comparables').getByRole('heading', { level: 2 })).toHaveText('Ventes ressemblantes');
		await expect(sousSection(page).locator('.comparables-phrase')).toHaveText('Choisissez un type pour lister les ventes ressemblantes.');
		await expect(sousSection(page).getByRole('radio', { name: 'Maison' })).not.toBeChecked();
		await expect(sousSection(page).getByRole('radio', { name: 'Appartement' })).not.toBeChecked();
		// The exact contract of the source refuses any read of the similar sales here.
	});

	for (const largeur of [375, 768, 1280]) {
		test(`tâche 4 à ${largeur} px : maison, 90 m², 1 km — effectif et critères servis`, async ({ page, request }) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			await configure(request, [fiche([okFiche()]), documents(2), zeroVentes(2), ressemblantes({ ...RECETTE, page: '1' }, [okComparables()])]);
			await open(page, `/adresse/${BAN_ID}`);

			await remplirRecette(page);
			await expectRecetteServie(page);
		});
	}

	test('chargement en navigation client : « Chargement des ventes ressemblantes… », aria-busy, formulaire utilisable', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(2), zeroVentes(2), ressemblantes({ ...RECETTE, page: '1' }, [okComparables({}, 'comparables')])]);
		await open(page, `/adresse/${BAN_ID}`);

		await remplirRecette(page);
		await expect(sousSection(page)).toHaveAttribute('aria-busy', 'true');
		await expect(sousSection(page).locator('.comparables-phrase')).toHaveText('Chargement des ventes ressemblantes…');
		await expect(sousSection(page).getByRole('button', { name: 'Afficher les ventes ressemblantes' })).toBeEnabled();
		await expect(sousSection(page).getByLabel('Surface (m²)')).toBeEditable();
		const relache = await request.post(`${E2E_SOURCE_ORIGIN}/__release`, { data: { gate: 'comparables' } });
		expect(relache.ok()).toBe(true);
		await expectRecetteServie(page);
		await expect(sousSection(page)).toHaveAttribute('aria-busy', 'false');
	});
});

test.describe('Ventes ressemblantes — tâche 4 de la recette sans JavaScript', () => {
	// The link of the panel carries its anchor: no smooth scroll to it, the form is filled at rest.
	test.use({ javaScriptEnabled: false, contextOptions: { reducedMotion: 'reduce' } });

	for (const largeur of [375, 768, 1280]) {
		test(`sans JavaScript à ${largeur} px : formulaire GET soumis, page rendue par le serveur`, async ({ page, request }) => {
			await page.setViewportSize({ width: largeur, height: 900 });
			// Without JavaScript the link of the panel loads the page once more, rendered with it open.
			await configure(request, [fiche([okFiche(), okFiche(), okFiche()]), documents(3), zeroVentes(3), ressemblantes({ ...RECETTE, page: '1' }, [okComparables()])]);
			await page.goto(dansLesVentes(`/adresse/${BAN_ID}`));

			await remplirRecette(page);
			await expectRecetteServie(page);
		});
	}

	// The form written before 04.5-02 had no hidden `theme`: its criteria name the theme of the sales.
});

test.describe('Ventes ressemblantes — états', () => {
	test('zéro : la phrase d’effectif reprend les critères servis, puis la phrase du contrat', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1), ressemblantes({ ...RECETTE, page: '1' }, [okComparables({ effectif: 0, ventes: [] })])]);
		await open(page, `/adresse/${BAN_ID}?${URL_RECETTE}`);

		expect(await texte(sousSection(page).locator('.comparables-effectif > span').first())).toBe('0 vente ressemblante · maisons · 72 à 108 m² · 1 km · 2021 → 2025');
		await expect(sousSection(page).locator('.comparables-phrase')).toHaveText(
			'Aucune vente ne correspond à ces critères. Élargissez le rayon, la période ou la tolérance de surface.'
		);
	});

	test('critère hors liste (rayon=5000) : formulaire gardé, phrase décidée, aucune requête élargie', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1)]);
		await open(page, `/adresse/${BAN_ID}?type=maison&rayon=5000`);

		// 04-DECISIONS-TEXTES row 34 (was the provisional fallback marked data-provisoire).
		const phrase = sousSection(page).locator('[data-criteres="non-reconnus"]');
		await expect(phrase).toHaveText("Ce critère n'est pas reconnu ; choisissez une valeur dans la liste.");
		await expect(phrase).not.toHaveAttribute('data-provisoire', /.*/);
		await expect(sousSection(page).locator('form')).toBeVisible();
		await expect(cartesRessemblantes(page)).toHaveCount(0);
		// No read of the similar sales: the exact contract of the source would report it.
	});

	test('panne : encart de section, critères conservés dans l’URL et le formulaire', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(),
			zeroVentes(1),
			ressemblantes({ ...RECETTE, page: '1' }, [{ status: 503, body: { title: 'x', status: 503, code: 'ventes_indisponibles' } }])
		]);
		await open(page, `/adresse/${BAN_ID}?${URL_RECETTE}`);

		await expect(sousSection(page).getByRole('alert')).toContainText("Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes.");
		await expect(sousSection(page).getByRole('button', { name: 'Relancer la recherche des ventes', exact: true })).toBeVisible();
		await expect(page).toHaveURL(new RegExp(`\\?${URL_RECETTE}$`));
		await expect(sousSection(page).getByLabel('Surface (m²)')).toHaveValue('90');
		// D-33 (04.8-03): number and readable street in the h1, postal code and commune on the identity line.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('1 rue des Ventes');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	});

});

// C16 (API WR-02, relecture CRF WR-05): a radius that reaches a departement not hosted is read in
// part. The page says « Liste incomplète », never the zero sentence, never the glossary sentence of
// « ce département » (the address's, which is hosted).
const PHRASE_CE_DEPARTEMENT = "Cette instance n'héberge pas les données de ce département.";
const PHRASE_ZERO_COMPARABLES = 'Aucune vente ne correspond à ces critères.';

test.describe('Ventes ressemblantes — rayon lu en partie (C16)', () => {
	test('ventes servies côté hébergé : « Liste incomplète », ni phrase du zéro ni « ce département », réserve exportée', async ({ page, request }) => {
		const partielles = okComparables({ motif: 'territoire_hors_perimetre', liste_incomplete: true });
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1), ressemblantes({ ...RECETTE, page: '1' }, [partielles])]);
		await open(page, `/adresse/${BAN_ID}?${URL_RECETTE}`);

		await expect(sousSection(page).locator('[data-reserve="liste-incomplete"]')).toHaveText('Liste incomplète');
		await expect(cartesRessemblantes(page)).toHaveCount(6);
		await expect(sousSection(page)).not.toContainText(PHRASE_ZERO_COMPARABLES);
		await expect(sousSection(page)).not.toContainText(PHRASE_CE_DEPARTEMENT);

	});

	test('aucune vente côté hébergé : hors couverture avec « Liste incomplète », jamais un zéro ni « ce département »', async ({ page, request }) => {
		const horsCouverture = okComparables({
			etat: 'out_of_coverage',
			motif: 'territoire_hors_perimetre',
			liste_incomplete: true,
			effectif: null,
			pages: null,
			generation: null,
			ventes: []
		});
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1), ressemblantes({ ...RECETTE, page: '1' }, [horsCouverture])]);
		await open(page, `/adresse/${BAN_ID}?${URL_RECETTE}`);

		await expect(sousSection(page).locator('.state-badge')).toHaveText('Hors couverture · département non hébergé');
		await expect(sousSection(page).locator('[data-reserve="liste-incomplete"]')).toHaveText('Liste incomplète');
		await expect(sousSection(page).locator('.state-help')).toHaveCount(0);
		await expect(sousSection(page)).not.toContainText(PHRASE_CE_DEPARTEMENT);
		await expect(sousSection(page)).not.toContainText(PHRASE_ZERO_COMPARABLES);
		await expect(sousSection(page).locator('.comparables-effectif')).toHaveCount(0);

	});
});

test.describe('Ventes ressemblantes — formulaire et accessibilité', () => {
	test('375 px : une colonne, champs de 44 px ; 1 280 px : quatre champs sur une ligne', async ({ page, request }) => {
		await configure(request, [fiche([okFiche(), okFiche()]), documents(2), zeroVentes(2)]);
		await page.setViewportSize({ width: 375, height: 800 });
		await open(page, `/adresse/${BAN_ID}`);
		await ouvrirPanneau(page);

		const champs = sousSection(page).locator('.comparables-champ');
		await expect(champs).toHaveCount(4);
		const gauches = await champs.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().left)));
		expect(new Set(gauches).size).toBe(1);
		const hauteurs = await sousSection(page)
			.locator('select, input[type="number"], .comparables-radio, button[type="submit"]')
			.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
		console.log(`RELEVE 375 champs=${hauteurs.map((h) => Math.round(h)).join('/')}`);
		for (const hauteur of hauteurs) expect(hauteur).toBeGreaterThanOrEqual(44);

		await page.setViewportSize({ width: 1280, height: 800 });
		await page.reload();
		const hauts = await champs.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
		expect(new Set(hauts).size).toBe(1);
	});

	test('axe WCAG 2.1 AA : aucune violation (formulaire, ventes ressemblantes servies, volet ouvert)', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), zeroVentes(1), ressemblantes({ ...RECETTE, page: '1' }, [okComparables()])]);
		await open(page, `/adresse/${BAN_ID}?${URL_RECETTE}`);

		const vue = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(vue.violations, JSON.stringify(vue.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		await sousSection(page).getByRole('button', { name: 'Source et définition : ventes ressemblantes' }).click();
		const volet = page.locator('dialog.source-panel[open]');
		await expect(volet).toBeVisible();
		await expect(volet).toContainText("À moins de 1 000 m du point de l'adresse".replace('1 000', '1 000').replace(' m du', ' m du'));
		await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
		const ouvert = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(ouvert.violations, JSON.stringify(ouvert.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		console.log(`RELEVE axe comparables violations=${vue.violations.length}+${ouvert.violations.length}`);
	});
});

// --- C37 — sales declared at the address: a relation of its own, never merged with the parcel's -----

const declarees = (replies: Reply[]) => attente(`/v1/adresse/${BAN_ID}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, replies);
const okDeclarees = (partiel: Record<string, unknown> = {}): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: ventes({ rattachement: 'adresse_declaree_vente', nombre_parcelles: undefined, nombre_vefa: 0, ...partiel }) }
});
const sectionDeclarees = (page: Page) => page.locator('section#ventes-adresse');

test.describe('Ventes déclarées à cette adresse (C37)', () => {
	test('point hors parcelle : la parcelle dit son état, la section des ventes déclarées montre les siennes, montant entier', async ({ page, request }) => {
		const plusieurs = vente({ date: '2019-05-01', composition: { maisons: 0, appartements: 2, dependances: 1, locaux_activite: 0 }, surface_batie: null, prix: 456789, prix_m2: null });
		await configure(request, [
			fiche([okFiche()]),
			documents(),
			liste({ page: '1' }, [okVentes({ etat: 'not_available', motif: 'point_hors_parcelle', total: null, periode: null, generation: null, ventes: [] })]),
			declarees([okDeclarees({ total: 1, ventes: [plusieurs] })])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(section(page).locator('.state-badge')).toHaveText('Point hors parcelle');
		await expect(sectionDeclarees(page).getByRole('heading', { level: 3 })).toHaveText('Ventes déclarées à cette adresse');
		// C38: right after the sales of the parcel, before the similar sales; DOM order = visual order.
		const ordre = await page.evaluate(() =>
			[...document.querySelectorAll('section#ventes, section#ventes-adresse, section#ventes-ressemblantes')].map((section) => [
				section.id,
				Math.round(section.getBoundingClientRect().top)
			])
		);
		expect(ordre.map(([id]) => id)).toEqual(['ventes', 'ventes-adresse', 'ventes-ressemblantes']);
		expect(Number(ordre[0][1])).toBeLessThan(Number(ordre[1][1]));
		await expect(page.locator('dialog.panneau-comparables section#ventes-ressemblantes')).toHaveCount(1);
		expect(await texte(sectionDeclarees(page).locator('.ventes-commune span').first())).toBe(
			'DVF (DGFiP) · 2021-2025 · ventes déclarées à ce numéro, rattachées à aucun logement'
		);
		const cartes = sectionDeclarees(page).locator('ol.ventes-cartes > li');
		await expect(cartes).toHaveCount(1);
		expect(await texte(cartes.first().locator('.ventecard-l2'))).toBe('2 appartements, 1 dépendance · surface bâtie non renseignée');
		expect(await texte(cartes.first().locator('.ventecard-l3'))).toBe('456 789 €');

		// UI-005 (04.5-06): the declared list is folded; its summary opens it.
		await sectionDeclarees(page).getByRole('button', { name: 'Source et définition : ventes déclarées à cette adresse' }).click();
		const volet = page.getByRole('dialog', { name: 'Ventes déclarées à cette adresse' });
		await expect(volet).toBeVisible();
		await expect(volet).toContainText('pas par la parcelle');
		await expect(volet).toContainText('Aucune vente rattachée ne veut pas dire aucune vente');
		// C3 (04.5-08): the sale is attached by number and suffix; the withdrawn sentence is gone.
		await expect(volet).toContainText('La vente est rattachée par le numéro et le suffixe déclarés');
		await expect(volet).not.toContainText('Les données DVF importées ne gardent pas le suffixe');
		await expect(volet).not.toContainText('DVF ne distingue pas');
		const rattachement = volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: 'Rattachement' }) });
		await expect(rattachement.locator('dd')).toHaveText('Adresse déclarée dans la vente (DVF)');
	});

	test('aucune vente rattachée (C44) : « Aucune vente rattachée à cette adresse dans DVF (2021-2025). »', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes()]), declarees([okDeclarees({ total: 0, ventes: [] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(sectionDeclarees(page).locator('.ventes-phrase')).toHaveText('Aucune vente rattachée à cette adresse dans DVF (2021-2025).');
		await expect(sectionDeclarees(page).locator('ol.ventes-cartes')).toHaveCount(0);
		// The parcel list is untouched: its three sales stay its own.
		await expect(cartesParcelle(page)).toHaveCount(3);
	});

	test('adresse avec suffixe : « Suffixe non distingué », jamais un zéro', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(),
			liste({ page: '1' }, [okVentes()]),
			declarees([okDeclarees({ etat: 'not_available', motif: 'suffixe_non_distingue', total: null, periode: null, generation: null, ventes: [] })])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(sectionDeclarees(page).locator('.state-badge')).toHaveText('Suffixe non distingué');
		await expect(sectionDeclarees(page).locator('.state-help')).toContainText('Les données DVF importées ne gardent pas le suffixe');
		await expect(sectionDeclarees(page)).not.toContainText('Aucune vente');
	});

	test('panne de la section seule : identité, repères et ventes de la parcelle affichés', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(),
			liste({ page: '1' }, [okVentes()]),
			declarees([{ status: 503, body: { title: 'x', status: 503, code: 'ventes_indisponibles' } }])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(sectionDeclarees(page).getByRole('alert')).toContainText("Les ventes n'ont pas pu être lues.");
		await expect(cartesParcelle(page)).toHaveCount(3);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('1 rue des Ventes');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
	});

	// Review of 04.5-06 (IMPORTANT): an outage that comes after a folded positive list, on the same
	// address, opens the fold again so that its alert stays in view. The page of the parcel reads the
	// declared list again (the loader reads every list), whose second answer is a 503.
	test('axe WCAG 2.1 AA : aucune violation, section servie et volet ouvert', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), documents(), liste({ page: '1' }, [okVentes()]), declarees([okDeclarees()])]);
		await open(page, `/adresse/${BAN_ID}`);

		// The folded list first (UI-005, 04.5-06), then opened, so that its content is analysed too.
		const replie = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(replie.violations, JSON.stringify(replie.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		const vue = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(vue.violations, JSON.stringify(vue.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		await sectionDeclarees(page).getByRole('button', { name: 'Source et définition : ventes déclarées à cette adresse' }).click();
		await expect(page.getByRole('dialog', { name: 'Ventes déclarées à cette adresse' })).toBeVisible();
		await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
		const volet = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(volet.violations, JSON.stringify(volet.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		console.log(`RELEVE axe ventes-declarees violations=${vue.violations.length}+${volet.violations.length}`);
	});
});

// --- B4 (plan 04.5-06) — the two relations of the sales, replayed on 44109_zz04_00004 ------------
//
// Inputs of the review (B4): `/adresse/44109_zz04_00004`, whose parcel list and declared list carry
// the same three sales, and the synthetic « point hors parcelle avec vente déclarée au numéro »
// (fixtures/adresse-ventes.ts, both reconstructed or synthetic, named there). Frozen at the starting
// SHA of plan 06 (9149114e), before the theme of the sales changes:
//   - the cards of each list, in order, as content tuples (date and nature, composition, built
//     surface, displayed price or masking badge, served masking reason, distance): a sale carries no
//     identifier, so a permutation or a replaced sale at the same rank changes a tuple (no business
//     id is added for this test);
//   - the reads of the source while the page loads (`/__requests`), the same after the change;
//   - the captures of the theme of the sales and of the essential, 1440 × 900 and 375 × 812
//     (`DC_REFONTE_D_EVIDENCE_DIR`, the test output otherwise).

/** UI-005: the overlap sentence (new label, read in a block by Ianis, R10). */
const PHRASE_RECOUVREMENT =
	"Une même vente peut figurer dans les deux listes : l'une rattache les ventes à la parcelle du point de l'adresse, l'autre à l'adresse déclarée dans la vente.";

type TupleVente = [string | null, string | null, string | null, string | null, string | null, string | null];

/** The cards of a list, in the rendered order, as content tuples. */
async function tuplesDeVentes(liste: Locator): Promise<TupleVente[]> {
	return liste.locator('ol.ventes-cartes > li').evaluateAll((cartes) =>
		cartes.map((carte) => {
			const norm = (texte: string | null | undefined) => (texte == null ? null : texte.replace(/\s+/g, ' ').trim());
			const ligne2 = carte.querySelectorAll('.ventecard-l2 > span');
			return [
				norm(carte.querySelector('.ventecard-l1 > span')?.textContent),
				norm(ligne2[0]?.textContent),
				norm(ligne2[2]?.textContent),
				norm(carte.querySelector('.ventecard-l3')?.textContent),
				carte.querySelector('.ventecard-badge')?.getAttribute('data-minimisation') ?? null,
				norm(carte.querySelector('.ventecard-distance')?.textContent)
			] as TupleVente;
		})
	);
}

/** The three sales captured in both lists of 44109_zz04_00004 (m375/11-adresse--texte.txt l. 158-208). */
const TUPLES_44109: TupleVente[] = [
	['juin 2025 · Vente', "avec un local d'activité", 'surface bâtie déclarée 47 m²', '141 000 €', null, null],
	['décembre 2022 · Vente', '1 appartement, 2 dépendances', 'surface bâtie déclarée 99 m²', '532 000 € · 5 374 €/m²', null, null],
	['octobre 2021 · Vente', '3 appartements, 2 dépendances', 'surface bâtie déclarée 115 m²', '482 000 €', null, null]
];
/** The sale declared at the number of the synthetic address in no parcel (« 1 Place Royale »). */
const TUPLES_HORS_PARCELLE: TupleVente[] = [['février 2023 · Vente', "avec un local d'activité", 'surface bâtie déclarée 461 m²', '1 719 045 €', null, null]];

/**
 * Reads of a load of the page, as served by the source: path and query, sorted. Frozen at the starting
 * SHA of plan 06; plan 07 (C4, R8) changes one query on purpose, the declared sales asked with their
 * VEFA (`&vefa=incluses`), and plan 08 (C3) the same one, by number and suffix
 * (`&regle=numero_et_suffixe`): no read is added or removed.
 */
const LECTURES_44109 = [
	'/v1/adresse/44109_zz04_00004/dpe?page=1',
	// R-07 (2026-11-10): the same read, asked with the short wait.
	'/v1/adresse/44109_zz04_00004/rapport?attente=courte',
	'/v1/adresse/44109_zz04_00004/ventes-declarees?page=1&vefa=incluses&regle=numero_et_suffixe',
	'/v1/adresse/44109_zz04_00004/ventes?page=1'
];

const CAS_B4 = {
	// The declared replays of plan 06, as served under `vefa=incluses` (no VEFA: `nombre_vefa` 0).
	'44109-zz04-00004': { fiche: ADRESSE_44109_zz04_00004_OBSERVED, parcelle: VENTES_PARCELLE_44109, declarees: VENTES_DECLAREES_44109_INCLUSES },
	'44109-zz04-00004-hors-parcelle': {
		fiche: ficheHorsParcelle(ADRESSE_44109_zz04_00004_OBSERVED),
		parcelle: VENTES_HORS_PARCELLE,
		declarees: VENTES_DECLAREES_HORS_PARCELLE_INCLUSES
	}
} as const;
type CleB4 = keyof typeof CAS_B4;

/** One load of the address (fiche, documents, both sales lists), `n` loads at most. */
function attentesB4(cle: CleB4, n = 1): Expectation[] {
	const cas = CAS_B4[cle];
	const fois = (reply: Reply) => Array.from({ length: n }, () => reply);
	return [
		attente(cheminRapport(BAN_ID_44109), REQUETE_RAPPORT, fois({ status: 200, body: cas.fiche }).map(enRapport), 1),
		attente(`/v1/adresse/${BAN_ID_44109}/dpe`, { page: '1' }, fois({ status: 200, body: ADRESSE_44109_zz04_00004_DPE_OBSERVED }), 1),
		attente(`/v1/adresse/${BAN_ID_44109}/ventes`, { page: '1' }, fois(cas.parcelle), 1),
		attente(`/v1/adresse/${BAN_ID_44109}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, fois(cas.declarees), 1)
	];
}

async function lecturesDeLaSource(request: APIRequestContext): Promise<string[]> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	const { requests } = (await res.json()) as { requests: { pathname: string; search: string }[] };
	return requests.map((lecture) => `${lecture.pathname}${lecture.search}`).sort();
}


const listeParcelle44109 = (page: Page) => page.locator('section#ventes');
const listeDeclaree44109 = (page: Page) => page.locator('section#ventes-adresse');

test.describe('B4 — deux rattachements des ventes, rejoués sur 44109_zz04_00004 (04.5-06)', () => {
	test('44109 : les deux listes gardent leurs cartes, dans l’ordre, aucune vente dédoublonnée', async ({ page, request }) => {
		await configure(request, attentesB4('44109-zz04-00004'));
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		await expect(listeParcelle44109(page).getByRole('heading', { level: 3 })).toHaveText('Ventes publiées sur la parcelle');
		expect(await tuplesDeVentes(listeParcelle44109(page))).toEqual(TUPLES_44109);
		expect(await tuplesDeVentes(listeDeclaree44109(page))).toEqual(TUPLES_44109);
		// Control: a permutation of two sales, or a sale replaced at the same rank, fails the comparison.
		expect(() => expect([TUPLES_44109[1], TUPLES_44109[0], TUPLES_44109[2]]).toEqual(TUPLES_44109)).toThrow();
		expect(() => expect([TUPLES_44109[0], TUPLES_44109[1], TUPLES_HORS_PARCELLE[0]]).toEqual(TUPLES_44109)).toThrow();
		expect(await lecturesDeLaSource(request)).toEqual(LECTURES_44109);
	});

	test('point hors parcelle avec vente déclarée (synthétique) : l’état de la parcelle et la vente déclarée, mêmes lectures', async ({ page, request }) => {
		await configure(request, attentesB4('44109-zz04-00004-hors-parcelle'));
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		await expect(listeParcelle44109(page).locator('.state-badge')).toHaveText('Point hors parcelle');
		await expect(listeParcelle44109(page).locator('ol.ventes-cartes')).toHaveCount(0);
		expect(await tuplesDeVentes(listeDeclaree44109(page))).toEqual(TUPLES_HORS_PARCELLE);
		expect(await lecturesDeLaSource(request)).toEqual(LECTURES_44109);
	});

	// --- Task 4 (UI-006, R7): the essential leads to the sales declared at the address -------------

	/** Opens a URL as it is, without the theme `open` adds: the essential is read before any theme. */
	async function ouvrirTelle(page: Page, url: string) {
		const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
		await page.goto(url);
		await hydrated;
	}

	// --- Task 2 (UI-007): the document the prefilled surface comes from ------------------------------

	test('44109 : la surface préremplie nomme le DPE sélectionné servi, avec un lien vers sa page', async ({ page, request }) => {
		await configure(request, attentesB4('44109-zz04-00004'));
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		const formulaire = page.locator('section#ventes-ressemblantes form');
		await expect(formulaire.getByLabel('Surface (m²)')).toHaveValue('119');
		expect(await texte(formulaire.locator('.comparables-origine'))).toBe('Surface issue du DPE n° 2644E8000029X, le plus récent de cette adresse.');
		await expect(formulaire.locator('.comparables-origine a')).toHaveAttribute('href', '/dpe/2644E8000029X');
	});

	// --- Task 1 (UI-005): the overlap said once, the declared list folded ----------------------------

});

// --- C4 (plan 04.5-07) — VEFA in the sales declared at the address --------------------------------
//
// Synthetic `vefa=incluses` answers of fixtures/adresse-ventes.ts (named there): 44109 with its three
// sales and two VEFA, and an address in no parcel whose declared sales are VEFA only. The captures of
// the essential and of the theme of the sales (declared list opened by its anchor) are taken at the
// starting SHA of plan 07 (d71cf851), before the page asks for the VEFA, and again after, same inputs.

const CAS_VEFA = {
	'44109-zz04-00004-vefa': { fiche: ADRESSE_44109_zz04_00004_OBSERVED, parcelle: VENTES_PARCELLE_44109_AVEC_VEFA, declarees: VENTES_DECLAREES_44109_AVEC_VEFA },
	'vefa-seules-hors-parcelle': { fiche: ficheHorsParcelle(ADRESSE_44109_zz04_00004_OBSERVED), parcelle: VENTES_HORS_PARCELLE, declarees: VENTES_DECLAREES_VEFA_SEULES }
} as const;
type CleVefa = keyof typeof CAS_VEFA;

/** One load of the address with its VEFA variant: fiche, documents, both sales lists. */
function attentesVefa(cle: CleVefa): Expectation[] {
	const cas = CAS_VEFA[cle];
	return [
		attente(cheminRapport(BAN_ID_44109), REQUETE_RAPPORT, [{ status: 200, body: cas.fiche }].map(enRapport), 1),
		attente(`/v1/adresse/${BAN_ID_44109}/dpe`, { page: '1' }, [{ status: 200, body: ADRESSE_44109_zz04_00004_DPE_OBSERVED }], 1),
		attente(`/v1/adresse/${BAN_ID_44109}/ventes`, { page: '1' }, [cas.parcelle], 1),
		attente(`/v1/adresse/${BAN_ID_44109}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [cas.declarees], 1)
	];
}

/** The mark of the report 4.1-15, written here by hand (read in a block by Ianis, R10). */
const MARQUE_VEFA = 'VEFA, hors statistiques';

/** The two synthetic VEFA of fixtures/adresse-ventes.ts, as their cards read (written by hand). */
const TUPLES_VEFA: TupleVente[] = [
	["mars 2024 · Vente en l'état futur d'achèvement", '1 appartement, 1 dépendance', 'surface bâtie déclarée 64 m²', `312 000 € · 4 875 €/m² ${MARQUE_VEFA}`, null, null],
	["septembre 2023 · Vente en l'état futur d'achèvement", '1 appartement', 'surface bâtie déclarée 42 m²', `219 000 € · 5 214 €/m² ${MARQUE_VEFA}`, null, null]
];

type Debordement = { classe: string; vertical: number; horizontal: number };

/**
 * Internal overflow of an element and of its ancestors up to its `summary`: how much of their content
 * exceeds their box (`scrollHeight - clientHeight`, `scrollWidth - clientWidth`), in px.
 */
async function debordementsInternes(locator: Locator): Promise<Debordement[]> {
	return locator.evaluate((element) => {
		const mesures: { classe: string; vertical: number; horizontal: number }[] = [];
		for (let noeud: Element | null = element; noeud; noeud = noeud.parentElement) {
			mesures.push({ classe: noeud.className || noeud.tagName, vertical: noeud.scrollHeight - noeud.clientHeight, horizontal: noeud.scrollWidth - noeud.clientWidth });
			if (noeud.tagName === 'SUMMARY') break;
		}
		return mesures;
	});
}

/** Nothing hidden by an internal overflow (1 px of rounding allowed). */
function expectSansDebordement(debordements: Debordement[]) {
	expect(debordements.length).toBeGreaterThan(1);
	for (const mesure of debordements) {
		expect(mesure.vertical, `débordement vertical de ${mesure.classe}`).toBeLessThanOrEqual(1);
		expect(mesure.horizontal, `débordement horizontal de ${mesure.classe}`).toBeLessThanOrEqual(1);
	}
}

/** Whether each card of a list carries the mark, in the rendered order. */
async function marquesDesCartes(liste: Locator): Promise<(string | null)[]> {
	return liste.locator('ol.ventes-cartes > li').evaluateAll((cartes) => cartes.map((carte) => carte.querySelector('[data-vefa]')?.textContent?.trim() ?? null));
}

test.describe('C4 — VEFA dans les ventes déclarées (04.5-07)', () => {
	test('VEFA seules (synthétique) : chaque carte de la liste déclarée porte la marque', async ({ page, request }) => {
		await configure(request, attentesVefa('vefa-seules-hors-parcelle'));
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		await expect(listeDeclaree44109(page).locator('ol.ventes-cartes > li')).toHaveCount(2);
		expect(await marquesDesCartes(listeDeclaree44109(page))).toEqual([MARQUE_VEFA, MARQUE_VEFA]);
		expect(await tuplesDeVentes(listeDeclaree44109(page))).toEqual(TUPLES_VEFA);
		await expectSourceContractMet(request);
	});

	test('44109 avec VEFA (synthétique) : les deux VEFA marquées dans les deux listes, les ventes jamais', async ({ page, request }) => {
		await configure(request, attentesVefa('44109-zz04-00004-vefa'));
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		const attendues = [null, MARQUE_VEFA, MARQUE_VEFA, null, null];
		expect(await marquesDesCartes(listeParcelle44109(page))).toEqual(attendues);
		expect(await marquesDesCartes(listeDeclaree44109(page))).toEqual(attendues);
		// The sales keep their frozen tuples (plan 06); the VEFA read as written above.
		const attendus = [TUPLES_44109[0], TUPLES_VEFA[0], TUPLES_VEFA[1], TUPLES_44109[1], TUPLES_44109[2]];
		expect(await tuplesDeVentes(listeDeclaree44109(page))).toEqual(attendus);
		expect(await tuplesDeVentes(listeParcelle44109(page))).toEqual(attendus);
		await expectSourceContractMet(request);
	});

	// Task 3: the line of the page and the line of the essential say the served total and the served
	// VEFA count (texts of the report and of the plan, written here by hand); the essential carries no
	// `adresse.dvf.ventes_adresse.nombre` when VEFA are served; nothing is subtracted (D45-05).

	test('4 VEFA servies, une seule sur la page (synthétique) : les nombres servis, jamais un compte des cartes ni une différence', async ({ page, request }) => {
		await configure(request, [
			attente(cheminRapport(BAN_ID_44109), REQUETE_RAPPORT, [{ status: 200, body: ADRESSE_44109_zz04_00004_OBSERVED }].map(enRapport), 1),
			attente(`/v1/adresse/${BAN_ID_44109}/dpe`, { page: '1' }, [{ status: 200, body: ADRESSE_44109_zz04_00004_DPE_OBSERVED }], 1),
			attente(`/v1/adresse/${BAN_ID_44109}/ventes`, { page: '1' }, [VENTES_PARCELLE_44109], 1),
			attente(`/v1/adresse/${BAN_ID_44109}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [VENTES_DECLAREES_VEFA_HORS_PAGE], 1)
		]);
		await open(page, `/adresse/${BAN_ID_44109}?theme=ventes`);

		expect((await marquesDesCartes(listeDeclaree44109(page))).filter(Boolean)).toHaveLength(1);
		expect(await texte(listeDeclaree44109(page).locator('.ventes-adresse-range'))).toBe('Ventes 1 à 10 sur 23, dont 4 VEFA hors statistiques');
		// Neither the VEFA cards of the page (1) nor a total minus the VEFA (19) is ever written.
		for (const zone of [listeDeclaree44109(page).locator('.ventes-adresse-range')]) {
			expect(await texte(zone)).not.toMatch(/dont 1 VEFA|(^|[^0-9])(19|22)([^0-9]|$)/);
		}
		await expectSourceContractMet(request);
	});

});

// --- C3 (plan 04.5-08) — sales declared by number and suffix ----------------------------------------
//
// Synthetic « 2 », « 2 bis » and « 4 quinquies » of fixtures/adresse-ventes.ts (shaped on the PostGIS
// fixture of the API, named there), each with the answer of both rules. The captures of the essential and
// of the theme of the sales are taken at the starting SHA of plan 08 (acc3eeff), when the page asked the
// default rule (`numero`), and again after, same inputs: the source offers both answers and the page
// reads exactly one.

function identiteSuffixe(cle: CleSuffixe) {
	const adresse = ADRESSES_SUFFIXES[cle];
	return {
		...IDENTITE,
		ban_id: adresse.banId,
		libelle: `${adresse.numero}${adresse.rep ? ` ${adresse.rep}` : ''} Rue des Suffixes, 85000 Commune suffixes`,
		numero: adresse.numero,
		rep: adresse.rep,
		commune: { code_insee: '85195', nom: 'Commune suffixes' }
	};
}

/** Fiche, documents and the sales of the parcel of one synthetic suffix address, one load. */
function attentesAdresseSuffixe(cle: CleSuffixe): Expectation[] {
	const banId = ADRESSES_SUFFIXES[cle].banId;
	return [
		attente(cheminRapport(banId), REQUETE_RAPPORT, [okFiche(identiteSuffixe(cle))].map(enRapport), 1),
		attente(`/v1/adresse/${banId}/dpe`, { page: '1' }, [SANS_DOCUMENT], 1),
		attente(`/v1/adresse/${banId}/ventes`, { page: '1' }, [okVentes()], 1)
	];
}

/** The answers of both rules offered, none required: the page reads one (captures before and after). */
function attentesDeuxRegles(cle: CleSuffixe): Expectation[] {
	const adresse = ADRESSES_SUFFIXES[cle];
	return [
		...attentesAdresseSuffixe(cle),
		attente(`/v1/adresse/${adresse.banId}/ventes-declarees`, { ...PREMIERE_PAGE_REGLE_NUMERO }, [adresse.numeroRegle], 0),
		attente(`/v1/adresse/${adresse.banId}/ventes-declarees`, { ...PREMIERE_PAGE_REGLE_SUFFIXE }, [adresse.suffixeRegle], 0)
	];
}

/** One load of a synthetic suffix address whose declared list is served *reponse*, by the query of the page. */
function attentesRegleSuffixe(cle: CleSuffixe, reponse: Reply = ADRESSES_SUFFIXES[cle].suffixeRegle): Expectation[] {
	return [...attentesAdresseSuffixe(cle), attente(`/v1/adresse/${ADRESSES_SUFFIXES[cle].banId}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [reponse], 1)];
}

/**
 * Codex review M2 (04.5-08): presses Tab from the focused element until *cible* has the focus, at most
 * *max* times; the number of presses, or null when Tab never reaches it (a control taken out of the tab
 * order, `tabindex="-1"`, is never reached). The target itself is never focused by the test.
 */
async function tabulerJusqua(page: Page, cible: Locator, max = 25): Promise<number | null> {
	for (let pression = 1; pression <= max; pression++) {
		await page.keyboard.press('Tab');
		if (await cible.evaluate((element) => element === document.activeElement)) return pression;
	}
	return null;
}

/** Line 1 of each card of the declared list (month, year and nature), in the rendered order. */
async function lignes1Declarees(page: Page): Promise<string[]> {
	return listeDeclaree44109(page)
		.locator('ol.ventes-cartes > li .ventecard-l1 > span')
		.evaluateAll((spans) => spans.map((span) => (span.textContent ?? '').replace(/\s+/g, ' ').trim()));
}

// Report 4.1-14 (C44): the three e2e cases, the query of the page strict (`regle=numero_et_suffixe` with
// `vefa=incluses`, never a loosened `sameQuery`). Labels and sentences written here by hand, as the
// report proposes them (read in a block by Ianis, R10).
test.describe('C3 — ventes déclarées par numéro et suffixe (04.5-08)', () => {
	test('« 2 » (synthétique) : sa vente déclarée sans suffixe, jamais celles du « 2 B », du « 2 C » ni du « 2 Z »', async ({ page, request }) => {
		await configure(request, attentesRegleSuffixe('2'));
		await open(page, `/adresse/${BAN_ID_SUFFIXE_2}?theme=ventes`);

		expect(await lignes1Declarees(page)).toEqual(['mars 2025 · Vente']);
		expect(await texte(listeDeclaree44109(page).locator('.ventes-adresse-range'))).toBe('Ventes 1 à 1 sur 1');
		for (const autre of ['mai 2024', 'juillet 2023', 'juin 2022', 'août 2021', 'février 2023']) {
			await expect(listeDeclaree44109(page)).not.toContainText(autre);
		}
		await expectSourceContractMet(request);
	});

	test('« 2 bis » (synthétique) : ses ventes déclarées « 2 B » et sa VEFA marquée, la plage servie', async ({ page, request }) => {
		await configure(request, attentesRegleSuffixe('2-bis'));
		await open(page, `/adresse/${BAN_ID_SUFFIXE_2_BIS}?theme=ventes`);

		expect(await lignes1Declarees(page)).toEqual(['mai 2024 · Vente', "février 2023 · Vente en l'état futur d'achèvement", 'juin 2022 · Vente']);
		expect(await marquesDesCartes(listeDeclaree44109(page))).toEqual([null, MARQUE_VEFA, null]);
		expect(await texte(listeDeclaree44109(page).locator('.ventes-adresse-range'))).toBe('Ventes 1 à 3 sur 3, dont 1 VEFA hors statistiques');
		await expect(listeDeclaree44109(page)).not.toContainText('Suffixe non distingué');
		await expectSourceContractMet(request);
	});

	test('« 4 quinquies » (synthétique) : « Suffixe sans correspondance » et sa phrase, jamais « Aucune vente rattachée »', async ({ page, request }) => {
		await configure(request, attentesRegleSuffixe('4-quinquies'));
		await open(page, `/adresse/${BAN_ID_SUFFIXE_QUINQUIES}?theme=ventes`);

		await expect(listeDeclaree44109(page).locator('.state-badge')).toHaveText('Suffixe sans correspondance');
		expect(await texte(listeDeclaree44109(page).locator('.state-help'))).toBe(
			"Le suffixe de cette adresse ne correspond pas de façon sûre à un suffixe déclaré dans DVF ; les ventes déclarées à ce numéro n'y sont pas montrées."
		);
		expect(await listeDeclaree44109(page).evaluate((element) => (element as HTMLElement).innerText)).not.toContain('Aucune vente');
		await expect(listeDeclaree44109(page).locator('ol.ventes-cartes')).toHaveCount(0);
		await expectSourceContractMet(request);
	});

	test('« 2 bis » sur une génération importée sans le suffixe : « Suffixe non disponible » et sa phrase, aucune vente prise pour « sans suffixe »', async ({
		page,
		request
	}) => {
		await configure(request, attentesRegleSuffixe('2-bis', DECLAREES_SUFFIXE_NON_IMPORTE));
		await open(page, `/adresse/${BAN_ID_SUFFIXE_2_BIS}?theme=ventes`);

		await expect(listeDeclaree44109(page).locator('.state-badge')).toHaveText('Suffixe non disponible');
		expect(await texte(listeDeclaree44109(page).locator('.state-help'))).toBe(
			"Les données DVF servies ont été importées sans le suffixe de l'adresse ; les ventes déclarées n'y sont pas encore distinguées par suffixe."
		);
		await expect(listeDeclaree44109(page).locator('ol.ventes-cartes')).toHaveCount(0);
		expect(await listeDeclaree44109(page).evaluate((element) => (element as HTMLElement).innerText)).not.toContain('Aucune vente');
		await expectSourceContractMet(request);
	});

});

test.describe('B4 — liste déclarée sans JavaScript (04.5-06)', () => {
	test.use({ javaScriptEnabled: false });

	test('vd_page=2 : la liste déclarée est rendue ouverte par le serveur, ses cartes visibles', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche()]),
			documents(),
			liste({ page: '1', vg: GENERATION }, [okVentes()]),
			attente(`/v1/adresse/${BAN_ID}/ventes-declarees`, { page: '2', vefa: 'incluses', regle: 'numero_et_suffixe', vg: GENERATION }, [okDeclarees({ ...VINGT_TROIS, page: 2, ventes: pageDe(2) })])
		]);
		await page.goto(`/adresse/${BAN_ID}?theme=ventes&vd_page=2&vg=${GENERATION}`);

		await expect(sectionDeclarees(page).locator('ol.ventes-cartes > li')).toHaveCount(10);
		await expect(sectionDeclarees(page).locator('ol.ventes-cartes')).toBeVisible();
		await expect(sectionDeclarees(page).locator('.ventes-adresse-range')).toHaveText('Ventes 11 à 20 sur 23');
	});
});
