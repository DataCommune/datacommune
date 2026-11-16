// Acronyms spelled out at their first occurrence (plan 04.8-08, D-17, D-31; UI-SPEC v4 § 7.5). Headless
// runner, isolated context, every off-local request refused (AGENTS.md).
//
// What this spec locks, on the rendered `main`:
//   - the first occurrence of an acronym of the list, in reading order, is followed by its full form,
//     « BRGM (Bureau de recherches géologiques et minières) », and the full form is said once on the page;
//   - an acronym whose full form the page already says is never spelled out (« Diagnostics de performance
//     énergétique (DPE) »);
//   - values and exports are untouched: the CSV of the address fiche keeps the served sentences as served.
import { readFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { DEPARTEMENT_44, DEPARTEMENTS_LISTE } from '../../src/lib/data/__fixtures__/departement';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { COMMUNE_FLAT_85047_WITH_DVF_WINDOW, COMMUNE_INDICATORS_SYNTHETIC } from './fixtures/commune-indicators';
import { DOCUMENT_REEL, REEL } from './fixtures/dpe-documents';
import { attendreAucuneCarteNonReconnue, repondreCarteStatique } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { mockAuthAnonyme } from './fixtures/mocks-matrice';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, REQUETE_RAPPORT } from './fixtures/rapport';

interface Theme {
	id: string;
	titre: string;
	verdict: string;
	[cle: string]: unknown;
}
interface Entree {
	banId: string;
	rapport: { donnees: { themes: Theme[] } };
	documents: unknown;
	ventes: unknown;
	ventesAdresse: unknown;
}

const BASE = JSON.parse(readFileSync(new URL('./fixtures/rapport-43-themes.json', import.meta.url), 'utf8')) as Entree;
const LECTURES = 12;
const BRGM = 'Bureau de recherches géologiques et minières';
const POUR_MILLE = 'pour mille';
const ICPE = 'installations classées pour la protection de l’environnement';

const echapper = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** The visible text of the main, as a reader gets it (closed details are not read). */
const texteMain = (page: Page) => page.locator('main').innerText();

/**
 * The first occurrence of `sigle` in the main is followed by its full form, « DPE (diagnostic…) », or
 * « DGFiP, Direction… » when it closes a parenthesis; the full form is added once: the next occurrence of
 * the acronym stays bare.
 */
async function premiereOccurrenceDeveloppee(page: Page, sigle: string, plein: string, occurrencesMin = 2) {
	const texte = await texteMain(page);
	const mot = new RegExp(`(?<![\\p{L}\\p{N}_])${echapper(sigle)}(?![\\p{L}\\p{N}_])`, 'gu');
	const positions = [...texte.matchAll(mot)].map((m) => m.index);
	expect(positions.length, `« ${sigle} » au moins ${occurrencesMin} fois dans le main`).toBeGreaterThanOrEqual(occurrencesMin);
	// A sigle the screen no longer shows has nothing to spell out (occurrencesMin = 0).
	if (positions.length === 0) return;
	const formes = [`${sigle} (${plein})`, `${sigle}, ${plein})`];
	const suite = texte.slice(positions[0], positions[0] + sigle.length + plein.length + 3);
	expect(formes.some((forme) => texte.startsWith(forme, positions[0])), `première occurrence : « ${suite} »`).toBe(true);
	const ajouts = texte.split(`${sigle} (${plein})`).length - 1 + (texte.split(`${sigle}, ${plein})`).length - 1);
	expect(ajouts, `forme pleine ajoutée une seule fois après « ${sigle} »`).toBe(1);
}

async function configurerFiche(request: APIRequestContext, a: Entree) {
	const attente = (pathname: string, query: Record<string, string>, body: unknown) => ({
		method: 'GET',
		pathname,
		query,
		minCalls: 0,
		maxCalls: LECTURES,
		responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
	});
	const res = await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, {
		data: {
			reset: true,
			expectations: [
				attente(cheminRapport(a.banId), REQUETE_RAPPORT, a.rapport),
				attente(`/v1/adresse/${a.banId}/dpe`, { page: '1' }, a.documents),
				attente(`/v1/adresse/${a.banId}/ventes`, { page: '1' }, a.ventes),
				attente(`/v1/adresse/${a.banId}/ventes-declarees`, { page: '1' }, a.ventesAdresse)
			]
		}
	});
	expect(res.ok()).toBe(true);
}

async function ouvrirFiche(page: Page, request: APIRequestContext, a: Entree) {
	await configurerFiche(request, a);
	await page.setViewportSize({ width: 1440, height: 900 });
	await repondreCarteStatique(page, { statut: 503 });
	const hydratee = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(`/adresse/${a.banId}`);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(page.locator('li[id^="critere-"]')).toHaveCount(a.rapport.donnees.themes.length);
}

/**
 * The fixture of 43 themes, with « ICPE » said in two served verdicts: a row (argiles, at the address),
 * then a row of the commune (radon), in the same category « Risques ».
 */
function ficheAvecIcpe(): Entree {
	const a = structuredClone(BASE);
	const theme = (id: string) => a.rapport.donnees.themes.find((t) => t.id === id)!;
	theme('argiles').verdict = 'Peu de risque de fissures dues aux argiles au point de l’adresse, hors de toute ICPE.';
	theme('radon').verdict = 'Le potentiel le plus élevé de l’échelle, valable pour toute la commune et ses ICPE.';
	// The title the API serves (`themes/dpe.py`): « DPE », not the full form the fixture carries, so that no
	// full form is read before the first DPE of the page (04.8-08 Opus review, 2).
	theme('dpe').titre = 'DPE';
	return a;
}

/** The directory of two communes, as recherche-accueil-404.spec.ts serves it. */
function sourceAnnuaire(): SourceExpectation[] {
	const ligne = (code: string, nom: string, population: number) => ({
		code_insee: code,
		nom,
		departement: '35',
		population,
		population_annee: 2022,
		population_source: 'demographie',
		hebergement: { etat: 'heberge', motif: null, departements: ['35'] },
		prix: {
			maison: { mediane_m2: 3200, effectif: 120, etat: null },
			appartement: { mediane_m2: 3900, effectif: 300, etat: null }
		}
	});
	return [
		{
			method: 'GET',
			pathname: '/v1/communes/annuaire',
			query: { couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' },
			minCalls: 1,
			maxCalls: 1,
			responses: [
				{
					status: 200,
					body: {
						statut: 'ok',
						donnees: {
							results: [ligne('35238', 'Rennes', 227830), ligne('35047', 'Bruz', 18950)],
							total_count: 2,
							page: 1,
							per_page: 50,
							has_next: false,
							couverture: 'servies',
							departements_servis: ['35'],
							prix: { etat: 'servi', motif: null, fenetre_mediane_dvf: '2024-12-31 → 2025-12-31' }
						}
					}
				}
			]
		}
	];
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page }) => {
	await expectNoUnexpectedOffLocalRequest(page);
});

test.describe('fiche adresse', () => {
	test.afterEach(async ({ page }) => {
		attendreAucuneCarteNonReconnue(page);
	});

	test('première occurrence développée, suivantes non ; forme pleine déjà dite → rien', async ({ page, request }) => {
		const a = ficheAvecIcpe();
		await ouvrirFiche(page, request, a);
		// Under the threshold of two columns a closed category hides its text, as this test needs.
		await page.setViewportSize({ width: 1000, height: 900 });

		// What is in sight on arrival comes first: DPE, as the API serves it, in the label of a key figure
		// (Opus review, 1), whatever « Sources de la fiche » says further down; then the sources that did not
		// answer (BRGM, BRGM / MTE…), behind the « i » under the closed categories.
		await premiereOccurrenceDeveloppee(page, 'DPE', 'diagnostic de performance énergétique', 1);
		await page.getByRole('button', { name: /sources qui n’ont pas répondu/ }).click();
		const panneauSources = page.getByRole('dialog', { name: 'Sources qui n’ont pas répondu' });
		await expect(panneauSources).toContainText(`BRGM (${BRGM}), BRGM / MTE`);
		// Socle (2026-11-09): a producer added to the list is spelled out in the same line of names.
		await expect(panneauSources).toContainText('ANFR (Agence nationale des fréquences)');
		await premiereOccurrenceDeveloppee(page, 'BRGM', BRGM, 1);
		await panneauSources.getByRole('button', { name: 'Fermer' }).click();

		// A closed category hides its text: each one is read on its own, what the head has said stays said.
		await page.locator('details#q-risques > summary').click();
		await expect(page.locator('#critere-argiles .ligne-phrase')).toHaveText(
			`Peu de risque de fissures dues aux argiles au point de l’adresse, hors de toute ICPE (${ICPE}).`
		);
		await expect(page.locator('#critere-radon .ligne-phrase')).toHaveText(
			'Le potentiel le plus élevé de l’échelle, valable pour toute la commune et ses ICPE.'
		);
		await premiereOccurrenceDeveloppee(page, 'ICPE', ICPE);
		await page.locator('details#q-risques > summary').click();

		// The served sentence of the fixture: « ‰ » spelled out after its first number only.
		await page.locator('details#q-quotidien > summary').click();
		await premiereOccurrenceDeveloppee(page, '‰', POUR_MILLE);
		await page.locator('details#q-quotidien > summary').click();

		// The sources of the fiche are prose of the main: an acronym first said there is spelled out there.
		await page.locator('#sources details > summary').click();
		await premiereOccurrenceDeveloppee(page, 'ADEME', 'Agence de la transition écologique', 1);
		await premiereOccurrenceDeveloppee(page, 'ANAH', 'Agence nationale de l’habitat', 1);
		// Left out of the list (socle): their full form would fall inside the name of a source, in its link.
		const sources = await page.locator('#sources').innerText();
		expect(sources).toContain('Ma Connexion Internet (Arcep)');
		expect(sources).toMatch(/API Carto GPU/);
		expect(sources).not.toMatch(/API \(|Arcep, Autorité|CSTB \(/);
	});

	test('l’export CSV garde les phrases servies telles quelles', async ({ page, request }) => {
		const a = ficheAvecIcpe();
		await ouvrirFiche(page, request, a);
		const telechargement = page.waitForEvent('download');
		await page.locator('.actions-plus summary').click();
		await page.getByRole('button', { name: 'Télécharger (CSV)' }).click();
		await page.getByRole('dialog', { name: 'Exporter les données' }).getByRole('button', { name: /^Télécharger \(\d+\)$/ }).click();
		const csv = (await readFile((await (await telechargement).path())!, 'utf8')).replace(/^﻿/, '');
		const delinquance = a.rapport.donnees.themes.find((t) => t.id === 'delinquance')!;
		expect(delinquance.verdict).toContain('‰');
		// The served sentences reach the CSV as served (`adresse-export.ts`: the verdict is the value or the
		// note of its row), never spelled out.
		for (const id of ['argiles', 'radon', 'delinquance']) {
			const t = a.rapport.donnees.themes.find((x) => x.id === id)!;
			expect(csv).toContain(t.verdict.split(',')[0]);
		}
		expect(csv).toContain('6,3 ‰ dans la commune, 6,3 ‰ dans le département');
		expect(csv).not.toContain(`(${ICPE})`);
		expect(csv).not.toContain(`(${BRGM})`);
		expect(csv).not.toContain(`(${POUR_MILLE})`);
		expect(csv).not.toContain('(diagnostic de performance énergétique)');
		expect(csv).not.toContain('(Agence nationale des fréquences)');
		expect(csv).not.toContain('(Agence nationale de l’habitat)');
	});
});

test.describe('annuaire /communes', () => {
	test('la légende du tableau garde « (DVF) » : une cellule ou une légende de tableau n’est jamais développée', async ({ page }) => {
		await mockAuthAnonyme(page);
		const sonde = await configureSsrSource(page.request, sourceAnnuaire());
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto('/communes');
		await sonde.assert();
		const legende = page.locator('table caption');
		await expect(legende).toContainText('Prix médians au m² des ventes retenues (DVF)');
		expect(await texteMain(page)).not.toContain('(Demandes de valeurs foncières)');
	});
});

test.describe('page introuvable (repli sans registre)', () => {
	// The default lead carries no acronym (the repli itself is proven by layout.test.ts, on a served message):
	// this checks that the page renders through it without error and adds nothing to a full form.
	test('le chapô par défaut, rendu par le repli, reste tel quel et sans erreur', async ({ page }) => {
		const erreurs: string[] = [];
		page.on('pageerror', (err) => erreurs.push(err.message));
		await mockAuthAnonyme(page);
		const reponse = await page.goto('/route-inconnue-04-8-08');
		expect(reponse?.status()).toBe(404);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page introuvable');
		// The lead says « Base Adresse Nationale » in full: BAN is not added (one segment, its own register).
		await expect(page.locator('main .erreur-chapo')).toHaveText(
			'Cette adresse ou cette page n’existe pas sur DataCommune. Le lien est peut-être incomplet, ou l’adresse ne figure pas encore dans la Base Adresse Nationale.'
		);
		expect(erreurs).toEqual([]);
	});
});

// ---- Other ported surfaces (task 2): fixtures as their own specs serve them ----------------------------

const DPE_PLEIN = 'diagnostic de performance énergétique';
const lire = (pathname: string, body: unknown, query: Record<string, string> = {}): SourceExpectation => ({
	method: 'GET',
	pathname,
	query,
	minCalls: 0,
	maxCalls: LECTURES,
	responses: Array.from({ length: LECTURES }, () => ({ status: 200, body }))
});

/** Opens `chemin` at 1440 px, anonymous, after serving `source`; waits for the h1. */
async function ouvrirPage(page: Page, chemin: string, source: SourceExpectation[] = []) {
	await mockAuthAnonyme(page);
	if (source.length) await configureSsrSource(page.request, source);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto(chemin);
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

test.describe('fiche commune', () => {
	test('DVF, DPE et LOVAC développés une seule fois, à leur première occurrence ; le JSON-LD reste brut', async ({ page }) => {
		// Challans, as portage-commune-dpe.spec.ts serves it.
		await ouvrirPage(page, '/commune/85047', [
			lire(
				'/v1/commune',
				{
					statut: 'ok',
					donnees: [{ ...COMMUNE_FLAT_85047_WITH_DVF_WINDOW, ...COMMUNE_INDICATORS_SYNTHETIC }],
					meta: { source: [{ nom: 'Démographie' }, { nom: 'Loyers' }, { nom: 'Zonage ABC' }] }
				},
				{ code_insee: '85047' }
			)
		]);
		// The tiles no longer name their source: DVF is met once on the screen (4.9, lot 3).
		await premiereOccurrenceDeveloppee(page, 'DVF', 'Demandes de valeurs foncières', 1);
		await premiereOccurrenceDeveloppee(page, 'DPE', DPE_PLEIN);
		// Since 09/11 the vacancy row reads « Taux de vacance du parc privé », its source in its panel: LOVAC
		// may be absent from the screen; when it shows, it is spelled out once.
		await premiereOccurrenceDeveloppee(page, 'LOVAC', 'fichier des logements vacants', 0);
		await expect(page.locator('[data-kpi="dpe-dominant"] .kpi-sub')).toHaveText(`Classe DPE (${DPE_PLEIN}) médiane`);
		// The FAQ equals its JSON-LD, which is never spelled out.
		const ld = await page.locator('script[type="application/ld+json"]').allTextContents();
		expect(ld.join('\n')).not.toMatch(/\((?:diagnostic de performance énergétique|Demandes de valeurs foncières)\)/);
	});
});

test.describe('document DPE', () => {
	test('BAN et ADEME développés une fois ; le h1 dit déjà la forme pleine, DPE reste nu', async ({ page }) => {
		await ouvrirPage(page, `/dpe/${REEL}`, [lire(`/v1/dpe/${REEL}`, DOCUMENT_REEL)]);
		// Since P2 of the document page (2026-11-10) the address and the provenance are read in two folds,
		// closed on a document in force: the reader opens them, and the acronyms are spelled out there.
		for (const id of ['rattachement', 'provenance']) {
			const repli = page.locator(`details#${id}`);
			if (!(await repli.evaluate((noeud) => (noeud as HTMLDetailsElement).open))) await repli.locator('summary').click();
			await expect(repli).toHaveAttribute('open');
		}
		await premiereOccurrenceDeveloppee(page, 'BAN', 'Base Adresse Nationale');
		// Since 09/11 the dataset line reads « DPE des logements existants »: ADEME is met once, in the source.
		await premiereOccurrenceDeveloppee(page, 'ADEME', 'Agence de la transition écologique', 1);
		expect(await texteMain(page)).not.toContain(`(${DPE_PLEIN})`);
		await expect(page.getByRole('heading', { level: 1 })).toContainText('Diagnostic de performance énergétique');
	});
});

test.describe('département et liste', () => {
	const SOURCES = [
		{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/' },
		{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'https://data.ademe.fr/datasets/dpe-v2-logements-existants' },
		{ nom: 'Recensement', producteur: 'INSEE', licence: 'Licence Ouverte 2.0' }
	];
	const LISTE = { statut: 'ok', donnees: DEPARTEMENTS_LISTE };

	test('département 44 : DPE, DVF, DGFiP et ADEME développés à leur première occurrence', async ({ page }) => {
		await ouvrirPage(page, '/departement/44', [
			lire('/v1/departements/44', { statut: 'ok', donnees: DEPARTEMENT_44, meta: { source: SOURCES } }),
			lire('/v1/departements', LISTE)
		]);
		// The label of a tile keeps its full form. 4.9: the definitions left the tiles for their panels, so
		// DVF, DGFiP and ADEME are first read in the sources, once each.
		await premiereOccurrenceDeveloppee(page, 'DPE', DPE_PLEIN);
		await premiereOccurrenceDeveloppee(page, 'DVF', 'Demandes de valeurs foncières', 1);
		await premiereOccurrenceDeveloppee(page, 'DGFiP', 'Direction générale des finances publiques', 1);
		await premiereOccurrenceDeveloppee(page, 'ADEME', 'Agence de la transition écologique', 1);
	});

	test('liste des départements : DPE développé dans le chapô', async ({ page }) => {
		await ouvrirPage(page, '/departements', [lire('/v1/departements', LISTE)]);
		await premiereOccurrenceDeveloppee(page, 'DPE', DPE_PLEIN, 1);
	});
});

test.describe('accueil', () => {
	test('PLU et DGFiP développés une fois ; le chapô dit « diagnostics énergétiques », DPE reste nu', async ({ page }) => {
		await ouvrirPage(page, '/');
		await premiereOccurrenceDeveloppee(page, 'PLU', 'plan local d’urbanisme', 1);
		await premiereOccurrenceDeveloppee(page, 'DGFiP', 'Direction générale des finances publiques', 1);
		const texte = await texteMain(page);
		expect(texte.match(/\bDPE\b/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
		expect(texte).not.toContain(`(${DPE_PLEIN})`);
	});
});

test.describe('/datasets', () => {
	const DATASETS_API = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/datasets-api.json', import.meta.url), 'utf8')) as unknown;

	test('CGU développé à sa première occurrence dans les conditions servies, pas à la seconde', async ({ page }) => {
		await ouvrirPage(page, '/datasets?jeu=urbanisme', [lire('/v1/datasets', DATASETS_API)]);
		await premiereOccurrenceDeveloppee(page, 'CGU', 'conditions générales d’utilisation');
	});

	test('sigles écartés du socle : les restrictions de DVF gardent « (RGPD, loi… », « API Carto » garde son nom', async ({ page }) => {
		// Left out of the list (socle): the served restriction is held word for word by donnees-methode.spec.ts,
		// and a full form there would open a parenthesis inside a parenthesis.
		await ouvrirPage(page, '/datasets?jeu=dvf', [lire('/v1/datasets', DATASETS_API)]);
		await expect(page.locator('main .dataset-restrictions')).toContainText('(RGPD, loi Informatique et libertés)');
		// « API Carto » is the name of a product, never « API (interface…) Carto ».
		await ouvrirPage(page, '/datasets?jeu=urbanisme', [lire('/v1/datasets', DATASETS_API)]);
		const conditions = await page.locator('main .dataset-conditions').innerText();
		expect(conditions).toContain('API Carto conserve la licence des sources');
	});

	test('la mention imposée entre guillemets reste telle que servie', async ({ page }) => {
		await ouvrirPage(page, '/datasets?jeu=loyers', [lire('/v1/datasets', DATASETS_API)]);
		const texte = await texteMain(page);
		expect(texte).toContain('« Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin »');
		expect(texte).not.toContain('Estimations ANIL (');
	});
});

test.describe('comparateur', () => {
	test('documents DPE : DPE développé dans le h1, nu ensuite et dans le tableau ; titre de l’onglet intact', async ({ page }) => {
		const DPE_COMPARES = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/dpe-comparaison.json', import.meta.url), 'utf8')).ancien_et_actuel as {
			donnees: { colonnes: { numero: string }[] };
		};
		const numeros = DPE_COMPARES.donnees.colonnes.map((c) => c.numero).join(',');
		await ouvrirPage(page, `/comparer?dpe=${numeros}`, [lire('/v1/dpe/comparaison', DPE_COMPARES, { numeros })]);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(`Comparer des documents DPE (${DPE_PLEIN})`);
		await premiereOccurrenceDeveloppee(page, 'DPE', DPE_PLEIN);
		expect(await page.locator('main table').first().innerText()).not.toContain(`(${DPE_PLEIN})`);
		await expect(page).not.toHaveTitle(/\(diagnostic/);
	});
});
