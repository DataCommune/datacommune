import AxeBuilder from '@axe-core/playwright';
import { mkdir } from 'node:fs/promises';
import { test, expect, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import {
	ADRESSE_44109_zz04_00004_DPE_OBSERVED,
	ADRESSE_44109_zz04_00004_OBSERVED
} from '../../src/lib/data/__fixtures__/adresse-44109-zz04-00004-20261013';
import { avecVentesDeLaParcelle } from './fixtures/adresse-ventes';
import { sansGroupe, variante, type NomVariante, type ReponseDocuments } from './fixtures/dpe-adresse-brute';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import {
	expectNoUnexpectedOffLocalRequest,
	installLocalNetworkGuard
} from './fixtures/local-network';

// Plan 04-18 (DPE-01, LIEU-01..03, CMP-01, SEM-01, SEM-02, UI-01; D-109, D-111, D-113) — the
// landmarks of the address page and the section of the declared DPE documents.
//
// What this spec locks, on the synthetic source of this instance (exact contract, SSR and client
// navigation alike):
//   - tracer: landmark ① « DPE déclarés à cette adresse » = the served count, the head sentence of
//     the section, the cards and their « DPE n° … » links, the meaning of ① in its source panel.
//   - the four landmarks: order ① ② ③ ④ at 375, 768 and 1 280 px, 2 × 2 under 1 000 px, the band
//     ≤ 400px at 375 × 800 on the densest fixture with ① in the first screen, each served state in
//     its own landmark (DVF outage in ② and ④ only), never « 0 » in ③;
//   - the section: every form of the D-109 sentence (0, 1, many, tie, undetermined, expired), the
//     A→G distribution on one line at 375 px, the served signal, the pagination bound to the
//     generation with and without JavaScript, 303 back to page 1, the section outage, a card
//     ≤ PLAFOND_CARTE at 375 px (160 px before the 14 px floor), and axe WCAG 2.1 AA without violation.

type Reply = { status: number; body: unknown; gate?: string };
type Expectation = {
	method: 'GET';
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number;
	responses: Reply[];
};

const NB = ' ';
// Stable synthetic address used for exhaustive state coverage; the public snapshot is tested below.
const BAN_ID = '85191_0420_00012';

const SOURCE_BAN = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr', licence: 'Licence Ouverte 2.0', producteur: 'IGN' };
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };
const SOURCE_DVF = { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees' };
const SOURCE_GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM / MTE', url: 'https://www.georisques.gouv.fr' };

const IDENTITE = {
	etat: 'etablie',
	ban_id: BAN_ID,
	libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '85000',
	commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	departement: { code: '85', nom: 'Vendée' },
	parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
};

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

const UNIQUE = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

const REFERENCE = {
	valeur: 2500,
	unite: 'euro_per_m2',
	territoire: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
	source: SOURCE_DVF,
	millesime: '2021-2025',
	url: SOURCE_DVF.url
};

function reperes(partiel: Record<string, unknown> = {}) {
	return {
		dpe: {
			...valeur({ field_id: 'adresse.dpe.nombre_declares', valeur: 3, unite: 'none', source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
			selection: UNIQUE
		},
		ventes: {
			nombre: valeur({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 2, unite: 'count', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' }),
			derniere: valeur({ field_id: 'adresse.dvf.ventes_parcelle.derniere', valeur: '2025-11-20', unite: 'none', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' })
		},
		argiles: valeur({ field_id: 'adresse.risques.argiles_au_point', valeur: 'Exposition moyenne', unite: 'none', periode: '2026-10-02', source: SOURCE_GEORISQUES, rattachement: 'au_point' }),
		reference_commune: {
			maisons: valeur({ field_id: 'adresse.reference_commune.prix_median_maison', valeur: 2500, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: REFERENCE }),
			appartements: valeur({ field_id: 'adresse.reference_commune.prix_median_appartement', valeur: 1980, unite: 'euro_per_m2', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'commune_reference', reference: { ...REFERENCE, valeur: 1980 } })
		},
		...partiel
	};
}

function carte(numero: string, partiel: Record<string, unknown> = {}) {
	return {
		numero,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		type_batiment: 'maison',
		surface_habitable: 92,
		date_etablissement: '2022-06-01',
		date_fin_validite: '2032-05-31',
		badges: [],
		...partiel
	};
}

const TROIS = [
	carte('2485E0000003C', { etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12', date_fin_validite: '2034-03-11', badges: ['plus_recent'] }),
	carte('2285E0000002D'),
	carte('2185E0000001E', { etiquette_dpe: 'E', date_etablissement: '2021-09-15', date_fin_validite: '2031-09-14' })
];

function documents(partiel: Record<string, unknown> = {}) {
	return {
		etat: 'value',
		motif: null,
		source: SOURCE_DPE,
		total: 3,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: '0123456789abcdef',
		distribution: { A: 0, B: 0, C: 1, D: 1, E: 1, F: 0, G: 0, unknown: 0 },
		date_import: '2026-09-30',
		documents: TROIS,
		selection: UNIQUE,
		signal_plusieurs_logements: null,
		rattachement: 'adresse_saisie_diagnostiqueur',
		...partiel
	};
}

const okFiche = (rep: unknown = reperes(), identite: unknown = IDENTITE): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: { identite, couverture: { etat: 'heberge', motif: null }, reperes: rep }, meta: { source: [SOURCE_BAN] } }
});
const okDocuments = (partiel: Record<string, unknown> = {}, gate?: string): Reply => ({
	status: 200,
	body: { statut: 'ok', donnees: documents(partiel) },
	...(gate ? { gate } : {})
});

function attente(pathname: string, query: Record<string, string>, replies: Reply[], minCalls = replies.length): Expectation {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}

const fiche = (replies: Reply[], banId = BAN_ID) => attente(cheminRapport(banId), REQUETE_RAPPORT, replies.map(enRapport));
const liste = (query: Record<string, string>, replies: Reply[], banId = BAN_ID) =>
	attente(`/v1/adresse/${banId}/dpe`, query, replies);

async function configure(request: APIRequestContext, expectations: Expectation[]) {
	// 04-05: each opened address also reads the first page of the sales of its parcel.
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: { reset: true, expectations: avecVentesDeLaParcelle(expectations) }
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

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
}

/** Link-preview metadata (Open Graph, Twitter): each balise once, with the values of the charter. */
async function verifierApercu(page: Page, { titre, url }: { titre: string; url: string | null }) {
	const meta = (attribut: 'property' | 'name', cle: string) => page.locator(`head meta[${attribut}="${cle}"]`);
	const contenu = async (attribut: 'property' | 'name', cle: string) => {
		expect(await meta(attribut, cle).count(), `${cle} présent une seule fois`).toBe(1);
		return meta(attribut, cle).getAttribute('content');
	};
	const image = await contenu('property', 'og:image');
	expect(image, 'og:image').toMatch(/^https:\/\/(dev\.)?datacommune\.fr\/og-image\.png$/);
	expect(await contenu('name', 'twitter:image'), 'twitter:image = og:image').toBe(image);
	expect(await contenu('name', 'twitter:card'), 'twitter:card').toBe('summary_large_image');
	expect(await contenu('property', 'og:title'), 'og:title').toBe(titre);
	expect(await contenu('name', 'twitter:title'), 'twitter:title').toBe(titre);
	const description = await contenu('name', 'description');
	const descriptionOg = await contenu('property', 'og:description');
	expect(descriptionOg, 'og:description non vide').toBeTruthy();
	expect(descriptionOg, 'og:description = description').toBe(description);
	expect(await contenu('name', 'twitter:description'), 'twitter:description').toBe(descriptionOg);
	expect(await contenu('property', 'og:type'), 'og:type').toBe('website');
	expect(await contenu('property', 'og:site_name'), 'og:site_name').toBe('DataCommune');
	expect(await contenu('property', 'og:locale'), 'og:locale').toBe('fr_FR');
	if (url !== null) {
		expect(await contenu('property', 'og:url'), 'og:url').toBe(url);
	} else {
		const nombre = await meta('property', 'og:url').count();
		expect(nombre, 'og:url absent ou une seule fois').toBeLessThanOrEqual(1);
		if (nombre === 1) expect(await contenu('property', 'og:url')).toMatch(/^https:\/\/(dev\.)?datacommune\.fr\/adresse\//);
	}
}

const cartes = (page: Page) => page.locator('ol.doclist-cards > li');

// Height ceilings of a document card at 375 px, recalculated for the v4 type contract (04.8-01,
// 14 px floor: card text 13 → 14 px, number link 15 → 16 px; 4 px between the number link and its box).
// - Without line 4: 160 px was set on the densest card (three badges), measured 154.6 px at the
//   old sizes (margin 160 / 154.6 = 3.5 %). At the new sizes the same card measures 184.4 px (its
//   dates and its badges each take two rows), so 160 × 184.4 / 154.6 = 190.8 → 191 px.
// - With the declared-address line (04-DECISIONS-TEXTES, row 21): 185 px was set on the densest
//   card with line 4, 180.8 px at the old sizes (margin 2.3 %); it measures 212 px, so
//   185 × 212 / 180.8 = 216.9 → 217 px.
// Both still catch the defect they guard: the box of line 3 falling under the number link (+20 px,
// its 44 px hit zone over the link's) takes the densest cards to 204.4 and 232 px.
const PLAFOND_CARTE = 191;
const PLAFOND_CARTE_LIGNE4 = 217;

function addressEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_ADDRESS_EVIDENCE_DIR;
	return directory ? `${directory.replace(/[\\/]$/, '')}/${name}` : testInfo.outputPath(name);
}

async function captureAddressBlock(
	page: Page,
	block: Locator,
	width: 1440 | 375,
	testInfo: TestInfo,
	name = `reperes-85191-zz05-00011-${width}.png`
) {
	const baseHeight = width === 1440 ? 900 : 800;
	await page.setViewportSize({ width, height: baseHeight });
	const header = page.locator('header.site-header');
	const [headerBox, blockBox] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(headerBox && blockBox, `repères et en-tête mesurables à ${width} px`).toBeTruthy();
	const height = Math.max(baseHeight, Math.ceil(headerBox!.height + blockBox!.height + 32));
	await page.setViewportSize({ width, height });
	await block.evaluate((element, headerHeight) => {
		const documentTop = element.getBoundingClientRect().top + window.scrollY;
		window.scrollTo({ top: Math.max(0, documentTop - Number(headerHeight) - 16), behavior: 'instant' });
	}, headerBox!.height);
	const [positionedHeader, positionedBlock] = await Promise.all([header.boundingBox(), block.boundingBox()]);
	expect(positionedHeader && positionedBlock, `repères positionnés à ${width} px`).toBeTruthy();
	expect(positionedBlock!.y).toBeGreaterThanOrEqual(positionedHeader!.y + positionedHeader!.height);
	expect(positionedBlock!.y + positionedBlock!.height).toBeLessThanOrEqual(height);
	await block.screenshot({
		path: addressEvidencePath(testInfo, name),
		animations: 'disabled'
	});
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await expectSourceContractMet(request);
	expectNoUnexpectedOffLocalRequest(page);
});

test.describe('Tracer — liste des documents et lien du document', () => {
	test('3 DPE déclarés : phrase D-109, trois cartes, lien « DPE n° … »', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments()])]);
		await open(page, `/adresse/${BAN_ID}`);


		const section = page.locator('section.doclist');
		await expect(section.getByRole('heading', { level: 3 })).toHaveText('Documents DPE déclarés à cette adresse');
		await expect(section.locator('.doclist-phrase')).toHaveText(`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024`);
		await expect(cartes(page)).toHaveCount(3);
		const lien = cartes(page).first().locator('a[href="/dpe/2485E0000003C"]');
		await expect(lien).toHaveText(`DPE n°${NB}2485E0000003C`);

		// The target page `/dpe/[numero]` is 04-04's: the link is locked here, not followed.
	});

	test('fiche adresse : aperçu de lien complet (og:*, twitter:*)', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments()])]);
		await open(page, `/adresse/${BAN_ID}`);
		await verifierApercu(page, { titre: await page.title(), url: null });
	});

	// Buttons (2026-11-10): the secondary buttons of the address fiche are written at 400, like the shared
	// `.bouton-secondaire` and the commune fiche (they were 600).
	test('fiche adresse : les boutons secondaires sont en graisse 400, comme ceux du socle', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments()])]);
		await open(page, `/adresse/${BAN_ID}`);
		const graisses = await page.locator('.adresse-secondary').evaluateAll((els) => els.map((el) => getComputedStyle(el).fontWeight));
		expect(graisses.length, 'la fiche porte au moins un bouton secondaire').toBeGreaterThan(0);
		expect(new Set(graisses)).toEqual(new Set(['400']));
	});
});

test.describe('États servis (SEM-01)', () => {
	test('adresse hors couverture : notice globale sans repère ni zéro', async ({ page, request }, testInfo) => {
		const horsCouverture = {
			statut: 'ok',
			donnees: {
				identite: IDENTITE,
				couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' }
			},
			meta: { source: [] }
		};
		await configure(request, [
			fiche([{ status: 200, body: horsCouverture }]),
			liste(
				{ page: '1' },
				[
					okDocuments({
						etat: 'out_of_coverage',
						motif: 'dataset_non_heberge',
						total: null,
						distribution: null,
						documents: [],
						selection: null
					})
				]
			)
		]);
		await open(page, `/adresse/${BAN_ID}`);

		const notice = page.locator('.notice.notice-warn');
		await expect(notice.getByRole('heading', { level: 2 })).toHaveText('Adresse hors couverture');
		await expect(notice).toContainText('Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.');
		await expect(notice.getByRole('link', { name: 'Voir la couverture des données' })).toHaveAttribute('href', '/datasets');
		await expect(notice).not.toContainText(/\binstance\b/i);
		await expect(page.locator('[data-kpi]')).toHaveCount(0);
		await expect(page.getByText(/Aucun DPE|momentanément indisponible/)).toHaveCount(0);
		for (const width of [1440, 375] as const) {
			await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
			const { scrollWidth, innerWidth } = await page.evaluate(() => ({
				scrollWidth: document.documentElement.scrollWidth,
				innerWidth: window.innerWidth
			}));
			expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations).toEqual([]);
			await captureAddressBlock(page, notice, width, testInfo, `notice-hors-couverture-${width}.png`);
		}
	});
});

// --- Task 3 — the complete section of the declared documents (D-109, D-111, LIEU-02, LIEU-03) ----

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;
const GENERATION = '0123456789abcdef';
const AUCUN = { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] };
const EGALITE = {
	etat: 'egalite',
	motif: 'document_valide',
	date: '2024-03-12',
	document: null,
	documents_ex_aequo: [
		{ numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
		{ numero: '2485E0000004D', etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' }
	]
};

function avecSelection(selection: unknown, total = 3) {
	const base = reperes();
	return reperes({ dpe: { ...base.dpe, valeur: total, selection } });
}

/** 23 documents in pages of 10, as served by 04-03 (numbers unique, served order). */
function pageDe(page: number) {
	const debut = (page - 1) * 10;
	const n = Math.min(10, 23 - debut);
	return Array.from({ length: n }, (_, i) => {
		const rang = debut + i + 1;
		return carte(`2385E00000${String(rang).padStart(2, '0')}X`, rang === 1 ? { badges: ['plus_recent'] } : {});
	});
}

const VINGT_TROIS = { total: 23, pages: 3, distribution: { A: 1, B: 2, C: 5, D: 8, E: 4, F: 2, G: 1, unknown: 0 } };

test.describe('Section des documents — formes de la phrase D-109 et répartition', () => {
	test('N = 0 : phrase du contrat, repère « 0 » et « base ADEME · {mois année} », aucun bouton d’actualisation', async ({ page, request }) => {
		const zero = { total: 0, documents: [], selection: AUCUN, distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 } };
		await configure(request, [fiche([okFiche(avecSelection(AUCUN, 0))]), liste({ page: '1' }, [okDocuments(zero)])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-phrase')).toHaveText(
			"Aucun DPE déclaré à cette adresse dans la base ADEME importée le 30 septembre 2026. Cela ne prouve pas qu'aucun DPE n'a été réalisé."
		);
		await expect(page.locator('section.doclist').getByRole('button', { name: /Actualiser|Recharger/ })).toHaveCount(0);
		await expect(cartes(page)).toHaveCount(0);
		await expect(page.locator('.doclist-distribution')).toHaveCount(0);
	});

	test('N = 1 : « 1 DPE déclaré à cette adresse · classe {X}, {mois année} », pas de répartition', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche(avecSelection(UNIQUE, 1))]),
			liste({ page: '1' }, [okDocuments({ total: 1, documents: [TROIS[0]], distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 0 } })])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-phrase')).toHaveText('1 DPE déclaré à cette adresse · classe C, mars 2024');
		await expect(page.locator('.doclist-distribution')).toHaveCount(0);
	});

	test('égalité servie : aucun document désigné, ① sans étiquette, cartes « Même date la plus récente »', async ({ page, request }) => {
		const docs = [
			carte('2485E0000003C', { etiquette_dpe: 'C', date_etablissement: '2024-03-12', badges: ['meme_date_plus_recente'] }),
			carte('2485E0000004D', { date_etablissement: '2024-03-12', badges: ['meme_date_plus_recente'] }),
			carte('2185E0000001E')
		];
		await configure(request, [fiche([okFiche(avecSelection(EGALITE))]), liste({ page: '1' }, [okDocuments({ selection: EGALITE, documents: docs })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-phrase')).toHaveText(
			`3 DPE déclarés à cette adresse · 2 documents partagent la date la plus récente${NB}: 12 mars 2024 · classes C, D`
		);
		await expect(page.locator('.doccard-badge')).toHaveText(['Même date la plus récente', 'Même date la plus récente']);
		await expect(page.locator('body')).not.toContainText('Le plus récent');
	});

	test('sélection indéterminée : « Le document le plus récent ne peut pas être déterminé. »', async ({ page, request }, testInfo) => {
		const indeterminee = { etat: 'indeterminee', motif: 'aucun_document_date', date: null, document: null, documents_ex_aequo: [] };
		await page.setViewportSize({ width: 375, height: 800 });
		await configure(request, [fiche([okFiche(avecSelection(indeterminee))]), liste({ page: '1' }, [okDocuments({ selection: indeterminee, documents: [carte('2185E0000001E', { date_etablissement: null })] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-phrase')).toHaveText('3 DPE déclarés à cette adresse. Le document le plus récent ne peut pas être déterminé.');
	});

	test('sélection unique sans classe : date distincte, aucun badge inventé et bande ≤ 400 px à 375', async ({ page, request }) => {
		const sansClasse = {
			...UNIQUE,
			document: { ...UNIQUE.document, etiquette_dpe: null }
		};
		await page.setViewportSize({ width: 375, height: 800 });
		await configure(request, [
			fiche([okFiche(avecSelection(sansClasse))]),
			liste({ page: '1' }, [okDocuments({ selection: sansClasse, documents: [carte('2485E0000003C', { etiquette_dpe: null })] })])
		]);
		await open(page, `/adresse/${BAN_ID}`);

	});

	test('plus récent expiré : « (expiré) » en tête, « · expiré » dans ①, badge neutre « Expiré »', async ({ page, request }) => {
		const expire = { ...UNIQUE, motif: 'expire' };
		const docs = [carte('2485E0000003C', { etiquette_dpe: 'C', date_etablissement: '2024-03-12', badges: ['plus_recent', 'expire'] }), ...TROIS.slice(1)];
		await configure(request, [fiche([okFiche(avecSelection(expire))]), liste({ page: '1' }, [okDocuments({ selection: expire, documents: docs })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-phrase')).toHaveText(`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024 (expiré)`);
		const badge = page.locator('.doccard-badge', { hasText: 'Expiré' });
		await expect(badge).toBeVisible();
		const couleur = await badge.evaluate((el) => getComputedStyle(el).backgroundColor);
		const soft = await page.evaluate(() => {
			const sonde = document.createElement('span');
			sonde.style.background = 'var(--color-soft)';
			// Probe the public theme; account screens inherit the legacy root tokens.
			document.querySelector('.public-site')!.append(sonde);
			const valeur = getComputedStyle(sonde).backgroundColor;
			sonde.remove();
			return valeur;
		});
		expect(couleur).toBe(soft);
	});

	test('N ≥ 2 à 375 px : répartition A→G sur une ligne, zéros affichés ; signal servi de plusieurs logements', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		const signal = { etat: 'value', motif: 'copropriete_immatriculee', nombre_lots_habitation: 12, rattachement: 'parcelle_du_point', source: { cle: 'copropriete', nom: 'Registre national des copropriétés (RNIC)', producteur: 'ANAH', url: 'https://www.data.gouv.fr' } };
		await configure(request, [
			fiche([okFiche()]),
			liste({ page: '1' }, [okDocuments({ ...VINGT_TROIS, documents: pageDe(1), signal_plusieurs_logements: signal })])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		const items = page.locator('.doclist-distribution > li');
		await expect(items).toHaveCount(7);
		const tops = await items.evaluateAll((lis) => lis.map((li) => Math.round(li.getBoundingClientRect().top)));
		expect(new Set(tops).size).toBe(1);
		await expect(page.locator('.doclist-signal')).toHaveText(
			'Cette adresse peut compter plusieurs logements (copropriété immatriculée au Registre national des copropriétés).'
		);
		const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
		expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	});
});

test.describe('Section des documents — pagination liée à la génération', () => {
	test('avec JavaScript : cartes gardées et aria-busy pendant la requête, annonce « Documents 11 à 20 sur 23 », focus sur le h2', async ({ page, request }) => {
		// One identity read for the page and its page change (FRONT WR-03 of the D-15 review): the
		// list is read alone, the identity (and Géorisques behind landmark ③) is not read again.
		await configure(request, [
			fiche([okFiche()]),
			liste({ page: '1' }, [okDocuments({ ...VINGT_TROIS, documents: pageDe(1) })]),
			liste({ page: '2', g: GENERATION }, [okDocuments({ ...VINGT_TROIS, page: 2, documents: pageDe(2) }, 'page-2')])
		]);
		await open(page, `/adresse/${BAN_ID}`);
		await expect(page.locator('.doclist-range')).toHaveText('Documents 1 à 10 sur 23');
		await expect(page.getByRole('link', { name: 'Page précédente' })).toHaveCount(0);

		await page.getByRole('link', { name: 'Page suivante' }).click();
		// The pointer leaves the link. The pagination preloads on tap since FRONT WR-03 (a resting
		// pointer no longer reads page 3, the race seen in the 04-19 matrix); kept as a guard.
		await page.locator('.doclist-range').hover();
		const section = page.locator('section.doclist');
		await expect(section).toHaveAttribute('aria-busy', 'true');
		await expect(cartes(page)).toHaveCount(10);
		await expect(cartes(page).first()).toContainText('2385E0000001X');

		const relache = await request.post(`${E2E_SOURCE_ORIGIN}/__release`, { data: { gate: 'page-2' } });
		expect(relache.ok()).toBe(true);
		await expect(section).toHaveAttribute('aria-busy', 'false');
		await expect(cartes(page).first()).toContainText('2385E0000011X');
		await expect(page.locator('.doclist-annonce')).toHaveText('Documents 11 à 20 sur 23');
		await expect(section.getByRole('heading', { level: 3 })).toBeFocused();
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?page=2&g=${GENERATION}$`));
	});

	// Codex review of the corrections of 04.5-02 (D45-11): a panel opened by hand writes its theme with
	// `replaceState`, which leaves `$page.url` as it was; the next page of the documents keeps the
	// theme read, without any reload in between.
	test('génération périmée (409) ou page hors limites (404) : 303 vers ?page=1', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche(), okFiche(), okFiche(), okFiche()]),
			liste({ page: '2', g: 'ffffffffffffffff' }, [{ status: 409, body: { title: 'Generation changee', status: 409, code: 'generation_changee' } }]),
			liste({ page: '9' }, [{ status: 404, body: { title: 'Page hors limites', status: 404, code: 'page_hors_limites' } }]),
			liste({ page: '1' }, [okDocuments({ ...VINGT_TROIS, documents: pageDe(1) }), okDocuments({ ...VINGT_TROIS, documents: pageDe(1) })])
		]);

		const perimee = await page.request.get(`/adresse/${BAN_ID}?page=2&g=ffffffffffffffff`, { maxRedirects: 0 });
		expect(perimee.status()).toBe(303);
		expect(perimee.headers()['location']).toBe(`/adresse/${BAN_ID}?page=1`);
		await page.goto(`/adresse/${BAN_ID}?page=9`);
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?page=1$`));
		await expect(page.locator('.doclist-range')).toHaveText('Documents 1 à 10 sur 23');
		await page.goto(`/adresse/${BAN_ID}?page=1`);
		await expect(page.locator('.doclist-range')).toHaveText('Documents 1 à 10 sur 23');
	});
});

test.describe('Section des documents — sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('les liens de page marchent sans JavaScript (?page=&g=)', async ({ page, request }) => {
		await configure(request, [
			fiche([okFiche(), okFiche()]),
			liste({ page: '1' }, [okDocuments({ ...VINGT_TROIS, documents: pageDe(1) })]),
			liste({ page: '2', g: GENERATION }, [okDocuments({ ...VINGT_TROIS, page: 2, documents: pageDe(2) })])
		]);
		await page.goto(`/adresse/${BAN_ID}`);

		await page.getByRole('link', { name: 'Page suivante' }).click();
		await expect(page).toHaveURL(new RegExp(`/adresse/${BAN_ID}\\?page=2&g=${GENERATION}$`));
		await expect(page.locator('.doclist-range')).toHaveText('Documents 11 à 20 sur 23');
		await expect(cartes(page)).toHaveCount(10);
	});
});

test.describe('Section des documents — panne, hauteur de carte et accessibilité', () => {
	test('liste en 503 : encart de section, repères et identité affichés, « Recharger les documents DPE » relit la liste', async ({ page, request }) => {
		// « Recharger les documents DPE » reads the list only (FRONT WR-03): one identity read.
		await configure(request, [
			fiche([okFiche()]),
			liste({ page: '1' }, [
				{ status: 503, body: { title: 'Documents DPE indisponibles', status: 503, code: 'documents_dpe_indisponibles' } },
				okDocuments()
			])
		]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(page.locator('.doclist-panne')).toContainText("Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché.");
		// D-33 (04.8-03): the h1 is the served number and readable street; the postal code and the commune are on the identity line.
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
		await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');
		await expect(cartes(page)).toHaveCount(0);

		await page.getByRole('button', { name: 'Recharger les documents DPE' }).click();
		await expect(cartes(page)).toHaveCount(3);
		await expect(page.locator('.doclist-panne')).toHaveCount(0);
	});

	test('375 px : carte de document ≤ 191 px (carte la plus dense : trois badges)', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		const dense = carte('1985E0000009E', {
			etiquette_dpe: 'E',
			type_batiment: 'appartement',
			surface_habitable: 104.5,
			date_etablissement: '2019-12-12',
			date_fin_validite: '2024-12-31',
			badges: ['plus_recent', 'ancienne_methode', 'expire']
		});
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ documents: [dense, ...TROIS.slice(1)] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const hauteurs = await cartes(page).evaluateAll((lis) => lis.map((li) => Math.round(li.getBoundingClientRect().height * 10) / 10));
		console.log(`RELEVE 375 cartes=${hauteurs.join('/')}`);
		for (const hauteur of hauteurs) expect(hauteur).toBeLessThanOrEqual(PLAFOND_CARTE);
		// The number link keeps its 44px hit zone, inside the card.
		const lien = (await cartes(page).first().locator('a.doccard-lien').boundingBox())!;
		const boite = (await cartes(page).first().boundingBox())!;
		expect(lien.height).toBeGreaterThanOrEqual(44);
		expect(lien.y + lien.height).toBeLessThanOrEqual(boite.y + boite.height);
		const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
		expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	});

	test('axe WCAG 2.1 AA : aucune violation sur la page servie (23 documents, signal, panneau ouvert)', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ ...VINGT_TROIS, documents: pageDe(1) })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const page1 = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(page1.violations, JSON.stringify(page1.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		await page.getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }).click();
		await expect(page.getByRole('dialog', { name: 'Documents DPE déclarés à cette adresse' })).toBeVisible();
		// The panel opens with a 150ms fade (UI-SPEC motion): axe reads it once the animation is over.
		await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
		const volet = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(volet.violations, JSON.stringify(volet.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
		console.log(`RELEVE axe violations=${page1.violations.length}+${volet.violations.length}`);
	});
});

// --- 04-19 Task 1 — line 4: declared address published or masked (P-ADR-01, LIEU-03, T-04-19-01) ---

const NNB = ' ';
const SENTINELLE = 'SENTINELLE-0419';
/** Keys the API never serves: sent here to prove the page neither renders nor serializes them. */
const BRUT_NON_CONTRACTUEL = {
	adresse_brut: `12 rue des Lilas ${SENTINELLE}`,
	adresse_complete_brut: `12 rue des Lilas ${SENTINELLE}-COMPLETE`,
	complement_adresse_logement: `porte ${SENTINELLE}-COMPLEMENT`
};
const DECLAREES = [
	carte('2485G0000001P', { date_etablissement: '2024-07-01', adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null }, ...BRUT_NON_CONTRACTUEL }),
	carte('2485G0000004C', { date_etablissement: '2024-04-01', adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' }, ...BRUT_NON_CONTRACTUEL }),
	carte('2485G0000003S', { date_etablissement: '2024-05-01', adresse_declaree: { etat: 'masquee', texte: null, motif: 'non_verifiable_sans_complement' }, ...BRUT_NON_CONTRACTUEL }),
	carte('2485G0000005K', { date_etablissement: '2024-03-01', adresse_declaree: null })
];

test.describe('Section des documents — adresse déclarée (ligne 4, P-ADR-01)', () => {
	test('publiée telle que saisie, masquée avec chacun de ses motifs, aucune sentinelle dans le HTML', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ total: 4, documents: DECLAREES })])]);
		const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
		const reponse = await page.goto(`/adresse/${BAN_ID}`);
		await hydrated;
		const html = await reponse!.text();
		await expect(cartes(page)).toHaveCount(4);

		const lignes4 = await cartes(page).evaluateAll((lis) => lis.map((li) => li.querySelector('.doccard-l4')?.textContent ?? null));
		// 2026-10-04 (04-DECISIONS-TEXTES, « Choix de structure »): the short line on the card, the
		// reason in the panel of the section.
		expect(lignes4).toEqual([`Adresse déclarée${NB}: «${NNB}14 rue des Lilas${NNB}»`, 'Adresse déclarée non affichée', 'Adresse déclarée non affichée', null]);
		// Neither the server-rendered HTML (serialized data included) nor the hydrated page carries a
		// key the contract does not serve, nor the generic label of `suppressed`.
		for (const contenu of [html, await page.content()]) {
			expect(contenu).not.toContain(SENTINELLE);
			expect(contenu).not.toContain('Secret statistique');
		}

		await page.getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }).click();
		const volet = page.getByRole('dialog', { name: 'Documents DPE déclarés à cette adresse' });
		const conservation = volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: 'Adresse déclarée non affichée' }) });
		await expect(conservation.locator('dd')).toHaveText(
			'Le document y joint un complément (étage, porte, bâtiment ou nom). Elle ne se vérifie pas comme une adresse sans complément. ' +
				"Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié."
		);
	});

	// 2026-10-05 (04-DECISIONS-TEXTES, « Choix de structure », WINDOWS n° 47): a masked declared address
	// is the short line « Adresse déclarée non affichée » on the card, its reason in the panel; the cap
	// of 160 px at 375 px (PLAFOND_CARTE since the 14 px floor) holds for a masked card without badge, its dates on one or two rows. A
	// masked card with a badge (the badge opens a row of its own) is measured below, not capped
	// (261005-t4d, Contradictions).
	test('375 px : carte à adresse masquée sans badge ≤ 191 px (ligne courte, motif au volet)', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		// Dates on two rows (« 1 avril 2024 ») and on one row (« 1 mai 2024 »), one card per reason.
		const masquees = DECLAREES.slice(1, 3);
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ total: 2, documents: masquees })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const hauteurs = await cartes(page).evaluateAll((lis) => lis.map((li) => Math.round(li.getBoundingClientRect().height * 10) / 10));
		console.log(`RELEVE 375 cartes-masquees=${hauteurs.join('/')}`);
		expect(hauteurs).toHaveLength(2);
		for (const hauteur of hauteurs) expect(hauteur).toBeLessThanOrEqual(PLAFOND_CARTE);
	});

	// A card that carries the declared-address line (published or masked) is capped at 185 px at
	// 375 px (04-DECISIONS-TEXTES, row 21, 2026-10-05; PLAFOND_CARTE_LIGNE4 since the 14 px floor); PLAFOND_CARTE stays the cap of the other cards. Cases:
	// the densest card (three badges) with a line 4, a long published address on two to three lines,
	// masked cards with the badge of the most recent. Also locked: line 4 wraps inside its card, no
	// horizontal overflow.
	test('375 px : ligne 4 publiée longue ou masquée, carte ≤ 217 px (décision n° 21), retour à la ligne dans la carte', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 800 });
		const dense = carte('1985E0000009E', {
			etiquette_dpe: 'E',
			type_batiment: 'appartement',
			surface_habitable: 104.5,
			date_etablissement: '2019-12-12',
			date_fin_validite: '2024-12-31',
			badges: ['plus_recent', 'ancienne_methode', 'expire'],
			adresse_declaree: { etat: 'masquee', texte: null, motif: 'non_verifiable_sans_complement' }
		});
		const longue = carte('2485G0000009L', { adresse_declaree: { etat: 'publiee', texte: '1234 bis avenue du Général-de-Gaulle-et-de-la-Libération-Nationale 85000 La Roche-sur-Yon', motif: null } });
		// The badge of the most recent opens a row of its own after dates on one row, or on two rows.
		const masqueeBadge = (numero: string, date: string) =>
			carte(numero, { date_etablissement: date, badges: ['plus_recent'], adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } });
		const avecBadge = [masqueeBadge('2485G0000006R', '2024-05-01'), masqueeBadge('2485G0000007T', '2024-08-01')];
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ total: 6, documents: [...DECLAREES.slice(1, 3), dense, longue, ...avecBadge] })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const hauteurs = await cartes(page).evaluateAll((lis) => lis.map((li) => Math.round(li.getBoundingClientRect().height * 10) / 10));
		console.log(`RELEVE 375 cartes-ligne4=${hauteurs.join('/')}`);
		for (const hauteur of hauteurs) expect(hauteur).toBeLessThanOrEqual(PLAFOND_CARTE_LIGNE4);
		const debords = await cartes(page).evaluateAll((lis) =>
			lis.map((li) => {
				const ligne = li.querySelector('.doccard-l4')!.getBoundingClientRect();
				const boite = li.getBoundingClientRect();
				return ligne.right <= boite.right && ligne.left >= boite.left;
			})
		);
		expect(debords).toEqual([true, true, true, true, true, true]);
		const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
		expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	});
});

// --- 04-19 Task 2 — D-110 notice, rendered only from the served field (DPE-01, P-D110-NULL) ------

const CONSTAT_D110 = {
	criteres: ['maison', 'document_unique_cle', 'concordance_stricte', 'batiment_unique_parcelle_du_point', 'annexes_exclues_usage_inconnu_compte'],
	sources: [SOURCE_DPE, { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' }]
};
const UN_SEUL = { total: 1, distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 0 }, documents: [TROIS[0]] };

test.describe('Section des documents — constat D-110', () => {
	test('constat servi : encart neutre, critères servis, bouton i « ne prouve pas l’identité d’un logement », axe sans violation', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ ...UN_SEUL, constat_d110: CONSTAT_D110 })])]);
		await open(page, `/adresse/${BAN_ID}`);

		const encart = page.locator('[data-constat-d110]');
		// 2026-10-05 (rows 8 and 9): h3 under the h2 of the section; no column name in the public text.
		await expect(encart.locator('.notice').getByRole('heading', { level: 3 })).toHaveText(`Une adresse, un bâtiment, un DPE${NB}: les éléments concordent`);
		await expect(encart.locator('.notice h2')).toHaveCount(0);
		await expect(encart.locator('.constat-criteres')).toContainText("maison · document unique à la clé BAN · concordance stricte de l'adresse brute");
		await expect(encart.locator('.constat-criteres')).toContainText("annexes déclarées exclues, bâtiment d'usage inconnu compté");
		await expect(encart.locator('.constat-criteres')).not.toContainText('cad_parcelles');
		// A neutral notice, never the warn one. 04.8-02 (UI-SPEC v4 § 3.13): the status panel is a `simple`
		// panel, its body on --color-panneau and its title on the --color-soft band (the whole notice was soft).
		await expect(encart.locator('.notice')).not.toHaveClass(/notice-warn/);
		const [fond, bande] = await encart.locator('.notice').evaluate((el) => [
			getComputedStyle(el).backgroundColor,
			getComputedStyle(el.querySelector('.notice-title')!).backgroundColor
		]);
		const [panneau, soft] = await page.evaluate(() =>
			['--color-panneau', '--color-soft'].map((jeton) => {
				const sonde = document.createElement('div');
				sonde.style.background = `var(${jeton})`;
				// Probe the public theme; account screens inherit the legacy root tokens.
				document.querySelector('.public-site')!.append(sonde);
				const couleur = getComputedStyle(sonde).backgroundColor;
				sonde.remove();
				return couleur;
			})
		);
		expect([fond, bande]).toEqual([panneau, soft]);

		await encart.getByRole('button', { name: 'Source et définition : constat de concordance' }).click();
		const volet = page.getByRole('dialog', { name: `Une adresse, un bâtiment, un DPE${NB}: les éléments concordent` });
		await expect(volet).toContainText("Ce constat ne prouve pas l'identité d'un logement.");
		await page.waitForFunction(() => document.getAnimations().every((animation) => animation.playState === 'finished'));
		const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
		expect(axe.violations, JSON.stringify(axe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
	});

	test('constat non servi (null) sur un document unique de maison : aucun encart ni phrase', async ({ page, request }) => {
		await configure(request, [fiche([okFiche()]), liste({ page: '1' }, [okDocuments({ ...UN_SEUL, constat_d110: null })])]);
		await open(page, `/adresse/${BAN_ID}`);

		await expect(cartes(page)).toHaveCount(1);
		await expect(page.locator('[data-constat-d110]')).toHaveCount(0);
		expect(await page.content()).not.toContain('les éléments concordent');
	});
});

// Reading redesign, plan 04.5-02 (D45-07, D45-10): the two frozen addresses captured at 1440 × 900
// and 375 × 812, the same step at the starting SHA (« avant ») and after the change (« après »), on
// the same inputs; the directory is `DC_REFONTE_D_EVIDENCE_DIR`, the test output otherwise.
function refonteEvidencePath(testInfo: TestInfo, name: string): string {
	const directory = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	return directory ? `${directory.replace(/[\/]$/, '')}/${name}` : testInfo.outputPath(name);
}

// Plan 04.5-03 (N-2): the landmarks alone, at 1440 × 900 and 375 × 812, on the two frozen addresses.
// Theme navigation of the address page (plan 04.5-02, D45-07, UI-024): risks and urbanism are
// reached from the theme access without crossing the list of documents nor the sales, and changing
// theme reads nothing again (the journal of the e2e source, the requests of the browser).
async function requetesDeLaSource(request: APIRequestContext): Promise<number> {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	return ((await res.json()) as { requests: unknown[] }).requests.length;
}

const acces = (page: Page) => page.getByRole('navigation', { name: 'Thèmes de la fiche' });

// Plan 04.5-05 (B3, UI-008): the instruction and the counter of the checked boxes precede the first
// box; four documents checked open the comparator with exactly these four; the cards, their links,
// badges and the range are those of the frozen record (lecture-rendu.spec.ts).
test.describe('Section des documents — consigne de comparaison et compteur (04.5-05, UI-008)', () => {
	const BAN_NANTES = '44109_zz04_00004';
	const NUMEROS = ADRESSE_44109_zz04_00004_DPE_OBSERVED.donnees.documents.map((document) => document.numero);

	for (const [width, height] of [[1440, 900], [375, 812]] as const) {
		test(`${width} px : consigne et compteur avant la première case ; quatre cochés au clavier, le lien compare exactement ces quatre`, async ({ page, request }) => {
			await installLocalNetworkGuard(page);
			await configure(request, [
				fiche([{ status: 200, body: ADRESSE_44109_zz04_00004_OBSERVED }], BAN_NANTES),
				liste({ page: '1' }, [{ status: 200, body: ADRESSE_44109_zz04_00004_DPE_OBSERVED }], BAN_NANTES)
			]);
			await page.setViewportSize({ width, height });
			await open(page, `/adresse/${BAN_NANTES}?theme=documents`);

			const section = page.locator('section.doclist');
			const consigne = section.locator('.doclist-consigne');
			await expect(consigne).toBeVisible();
			await expect(consigne).toContainText('Sélectionnez 2 à 4 documents DPE pour les comparer');
			const compteur = consigne.locator('.doclist-compteur');
			await expect(compteur).toHaveText('0 sélectionné');
			const ordre = await section.evaluate((noeud) => {
				const premiereCase = noeud.querySelector('input[type="checkbox"]')!;
				const premiereCarte = noeud.querySelector('ol.doclist-cards > li')!;
				const texte = noeud.querySelector('.doclist-consigne')!;
				return [premiereCase, premiereCarte].map((cible) => Boolean(texte.compareDocumentPosition(cible) & Node.DOCUMENT_POSITION_FOLLOWING));
			});
			expect(ordre).toEqual([true, true]);

			// Checked with the keyboard (focus, Space) in an order other than the served one.
			const coches = [NUMEROS[3], NUMEROS[0], NUMEROS[4], NUMEROS[1]];
			for (const [rang, numero] of coches.entries()) {
				await page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${numero}` }).focus();
				await page.keyboard.press('Space');
				await expect(compteur).toHaveText(`${rang + 1} ${rang === 0 ? 'sélectionné' : 'sélectionnés'}`);
			}
			await expect(page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${NUMEROS[2]}` })).toBeDisabled();
			const lien = page.getByRole('link', { name: 'Comparer les documents (4)' });
			await expect(lien).toHaveAttribute('href', `/comparer?dpe=${coches.join(',')}`);
			await lien.focus();
			await expect(lien).toBeFocused();

			// Links, badges and the range of the list are still there (D45-11).
			await expect(section.locator('ol.doclist-cards > li a.doccard-lien')).toHaveCount(NUMEROS.length);
			await expect(section.locator('.doclist-range')).toHaveText(`Documents 1 à ${NUMEROS.length} sur ${NUMEROS.length}`);
			const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
			expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
			await expectSourceContractMet(request);
			await expectNoUnexpectedOffLocalRequest(page);
		});
	}
});

test.describe('Section des documents — consigne sans JavaScript (04.5-05)', () => {
	test.use({ javaScriptEnabled: false });

	test('sans JavaScript, ni consigne ni case : la sélection n’agit qu’avec JavaScript', async ({ page, request }) => {
		const BAN_NANTES = '44109_zz04_00004';
		await configure(request, [
			fiche([{ status: 200, body: ADRESSE_44109_zz04_00004_OBSERVED }], BAN_NANTES),
			liste({ page: '1' }, [{ status: 200, body: ADRESSE_44109_zz04_00004_DPE_OBSERVED }], BAN_NANTES)
		]);
		await page.goto(`/adresse/${BAN_NANTES}?theme=documents`);
		await expect(page.locator('section.doclist ol.doclist-cards > li')).toHaveCount(5);
		await expect(page.locator('.doclist-consigne')).toHaveCount(0);
		await expect(page.locator('section.doclist input[type="checkbox"]')).toHaveCount(0);
		await expectSourceContractMet(request);
	});
});

// Plan 04.5-05 (B3 + C2): the theme « Documents DPE » of 44109_zz04_00004 at 1440 × 900 and
// 375 × 812, on the observed answer and on the synthetic variant whose raw-address group counts 12
// (its ten most recent served). The same step at the starting SHA (« avant ») and after the change
// (« après »), on the same inputs.
test.describe('Fiche adresse — captures du thème Documents DPE (04.5-05)', () => {
	const BAN_NANTES = '44109_zz04_00004';
	for (const nom of ['sans-groupe', 'douze'] as const satisfies readonly NomVariante[]) {
		test(`captures 1440 et 375 du thème Documents DPE, variante ${nom}`, async ({ page, request }, testInfo) => {
			test.setTimeout(60_000);
			await installLocalNetworkGuard(page);
			if (process.env.DC_REFONTE_D_EVIDENCE_DIR) await mkdir(process.env.DC_REFONTE_D_EVIDENCE_DIR, { recursive: true });
			for (const [width, height] of [[1440, 900], [375, 812]] as const) {
				await configure(request, [
					fiche([{ status: 200, body: ADRESSE_44109_zz04_00004_OBSERVED }], BAN_NANTES),
					liste({ page: '1' }, [{ status: 200, body: variante(nom) }], BAN_NANTES)
				]);
				await page.setViewportSize({ width, height });
				await open(page, `/adresse/${BAN_NANTES}?theme=documents`);
				await expect(page.locator('section.doclist')).toBeVisible();
				const fichier = `documents-${BAN_NANTES.replaceAll('_', '-')}-${nom}-${width}`;
				// The sticky site header would cover the section in a stitched element capture.
				await page.locator('section.doclist').screenshot({ path: refonteEvidencePath(testInfo, `${fichier}.png`), animations: 'disabled', style: '.site-header { position: static !important; }' });
				await page.locator('section.doclist').scrollIntoViewIfNeeded();
				await page.screenshot({ path: refonteEvidencePath(testInfo, `${fichier}-ecran.png`), animations: 'disabled' });
				await expectSourceContractMet(request);
			}
			await expectNoUnexpectedOffLocalRequest(page);
		});
	}
});

// Plan 04.5-05 (C2; 04.1-13, D-127): the documents attached by their exact raw address, a group of
// their own after the list of the ADEME key, on the synthetic variants of fixtures/dpe-adresse-brute.ts.
// The ADEME head sentence, distribution and D-110 notice are those of the same answer without the
// group; only the cards of the group carry the badge; the limit « dix sur N » is said; a group not
// computed says its cause and never a zero; its Source panel opens with the keyboard.
test.describe('Section des documents — groupe rapproché par l’adresse saisie (04.5-05, C2)', () => {
	const BAN_NANTES = '44109_zz04_00004';
	const BADGE = "Rattaché par l'adresse saisie";
	const groupe = (page: Page) => page.locator('section.doclist [data-groupe-dpe="adresse_brute"]');

	async function ouvrirListe(page: Page, request: APIRequestContext, reponse: ReponseDocuments, width = 1440) {
		await configure(request, [
			fiche([{ status: 200, body: ADRESSE_44109_zz04_00004_OBSERVED }], BAN_NANTES),
			liste({ page: '1' }, [{ status: 200, body: reponse }], BAN_NANTES)
		]);
		await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
		await open(page, `/adresse/${BAN_NANTES}?theme=documents`);
		await expect(page.locator('section.doclist .doclist-phrase')).toBeVisible();
	}

	/** What the ADEME key says at the head of the list, as rendered. */
	async function teteAdeme(page: Page) {
		const texte = async (selecteur: string) => ((await page.locator(selecteur).count()) ? (await page.locator(selecteur).textContent())?.replace(/\s+/g, ' ').trim() : null);
		return {
			phrase: await texte('section.doclist .doclist-phrase'),
			repartition: await texte('section.doclist .doclist-distribution'),
			constat: await texte('section.doclist [data-constat-d110] .notice'),
			cartes: await page.locator('section.doclist ol.doclist-cards > li .doccard-lien').evaluateAll((liens) => liens.map((lien) => lien.getAttribute('href')))
		};
	}

	const servies = (nom: NomVariante) => (variante(nom).donnees.documents_adresse_brute ?? []) as { numero: string }[];

	for (const nom of ['servi', 'ademe-zero', 'constat-ademe', 'douze'] as const satisfies readonly NomVariante[]) {
		test(`${nom} : tête, répartition et constat de la clé ADEME identiques sans le groupe ; seules les cartes du groupe portent le badge, dans l’ordre servi`, async ({ page, request }) => {
			await installLocalNetworkGuard(page);
			await ouvrirListe(page, request, sansGroupe(variante(nom)));
			const sans = await teteAdeme(page);
			await expect(groupe(page)).toHaveCount(0);
			await expectSourceContractMet(request);

			await ouvrirListe(page, request, variante(nom));
			expect(await teteAdeme(page)).toEqual(sans);
			const cartes = groupe(page).locator('ol.doclist-brute-cards > li');
			await expect(cartes).toHaveCount(servies(nom).length);
			expect(await cartes.locator('.doccard-lien').evaluateAll((liens) => liens.map((lien) => lien.getAttribute('href')))).toEqual(
				servies(nom).map((carte) => `/dpe/${carte.numero}`)
			);
			await expect(cartes.locator('.doccard-rattachement')).toHaveText(servies(nom).map(() => BADGE));
			await expect(page.locator('section.doclist ol.doclist-cards .doccard-rattachement')).toHaveCount(0);
			await expect(page.locator('section.doclist .doccard-rattachement')).toHaveCount(servies(nom).length);
			await expect(groupe(page).locator('input[type="checkbox"]')).toHaveCount(0);
			await expect(groupe(page).getByRole('heading', { level: 4 })).toHaveText("Documents DPE rapprochés par l'adresse saisie");
			await expectSourceContractMet(request);
			await expectNoUnexpectedOffLocalRequest(page);
		});
	}

	test('douze : les dix plus récents servis, la limite dite sur le total servi de 12', async ({ page, request }) => {
		await installLocalNetworkGuard(page);
		await ouvrirListe(page, request, variante('douze'));
		await expect(groupe(page).locator('.doclist-brute-phrase')).toHaveText(
			'12 DPE rapprochés de cette adresse par leur adresse saisie. Ils ne sont pas comptés parmi les DPE déclarés à cette adresse.'
		);
		await expect(groupe(page).locator('.doclist-brute-limite')).toHaveText('Les 10 plus récents sur 12 sont affichés.');
		await expect(groupe(page).locator('ol.doclist-brute-cards > li')).toHaveCount(10);
		// The ADEME list keeps its own range and pagination; the group has none.
		await expect(page.locator('section.doclist .doclist-range')).toHaveText('Documents 1 à 5 sur 5');
		await expect(groupe(page).locator('.pagination')).toHaveCount(0);
		await expectSourceContractMet(request);
	});

	test('non-calcule : le rapprochement non calculé dit sa cause, jamais « 0 document » ni aucun zéro', async ({ page, request }) => {
		await installLocalNetworkGuard(page);
		await ouvrirListe(page, request, variante('non-calcule'));
		await expect(groupe(page).locator('.doclist-brute-phrase')).toHaveText(
			"Le rapprochement par l'adresse saisie n'est pas calculé pour cette importation des données DPE."
		);
		await expect(groupe(page)).not.toContainText('0 document');
		expect((await groupe(page).locator(':scope > :not(dialog)').allTextContents()).join(' ')).not.toMatch(/\b0\b/);
		await expect(groupe(page).locator('li')).toHaveCount(0);
		await expectSourceContractMet(request);
	});

	test('vide : la phrase de zéro du groupe, aucune carte ; sans-groupe : aucun bloc', async ({ page, request }) => {
		await installLocalNetworkGuard(page);
		await ouvrirListe(page, request, variante('vide'));
		await expect(groupe(page).locator('.doclist-brute-phrase')).toHaveText('Aucun DPE rapproché de cette adresse par son adresse saisie.');
		await expect(groupe(page).locator('li')).toHaveCount(0);
		await expectSourceContractMet(request);
		await ouvrirListe(page, request, variante('sans-groupe'));
		await expect(groupe(page)).toHaveCount(0);
		await expectSourceContractMet(request);
	});

	test('Source du groupe ouvert au clavier : définition, rattachement et badge expliqués ; Échap rend le focus', async ({ page, request }) => {
		await installLocalNetworkGuard(page);
		await ouvrirListe(page, request, variante('servi'));
		const bouton = groupe(page).getByRole('button', { name: "Source et définition : documents DPE rapprochés par l'adresse saisie" });
		await bouton.focus();
		await page.keyboard.press('Enter');
		const volet = page.getByRole('dialog', { name: "Documents DPE rapprochés par l'adresse saisie" });
		await expect(volet).toBeVisible();
		await expect(volet).toContainText('Adresse saisie, rapprochée exactement de la BAN');
		await expect(volet).toContainText("ne correspond à aucune adresse de la Base Adresse Nationale importée");
		await expect(volet).toContainText('il ne désigne aucun logement précis.');
		await page.keyboard.press('Escape');
		await expect(volet).toBeHidden();
		await expect(bouton).toBeFocused();
		await expectSourceContractMet(request);
	});

	test('375 px : le groupe sans défilement horizontal, ses cartes mesurées', async ({ page, request }) => {
		await installLocalNetworkGuard(page);
		await ouvrirListe(page, request, variante('douze'), 375);
		const hauteurs = await groupe(page).locator('ol.doclist-brute-cards > li').evaluateAll((lis) =>
			lis.map((li) => ({ hauteur: Math.round(li.getBoundingClientRect().height * 10) / 10, ligne4: li.querySelector('.doccard-l4') !== null }))
		);
		console.log(`RELEVE C2 cartes 375=${JSON.stringify(hauteurs)}`);
		expect(hauteurs).toHaveLength(10);
		const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
		expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
		await expectSourceContractMet(request);
	});
});
