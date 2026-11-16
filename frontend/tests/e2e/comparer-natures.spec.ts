import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { cheminRapport, enRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { COMMUNE_NON_SERVIE, PREMIERE_PAGE_DECLAREES, SANS_VENTE, SANS_VENTE_DECLAREE, SOURCE_DVF, VENTES_DECLAREES_VEFA_SEULES } from './fixtures/adresse-ventes';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';

// Plan 04-07 (CMP-01, D-102, D-116, SEM-01, SEM-02, UI-01): addresses compared with addresses.
// Plan 04-10 (CMP-02, D-116): DPE documents compared by their number, nature switch.
// Plan 04-11 (D-102, CMP-02, R06-09/7): a DPE designated on a compared address, in the link only
// (end of file): task 5 of the recette, « Lien altéré », parity with the document comparator, band.
//
// What this spec locks, on the synthetic source of this instance (exact contract):
//   - tracer: « Comparer avec une autre adresse » on `/adresse/A` opens `/comparer?adresses=A`; the
//     address field (addresses only) adds B; ONE call to /v1/adresses/comparaison serves two
//     columns and four `tbody` in the served order, the commune of the address last with its
//     sentence; the gap line is the served one (never computed) and carries no colour of judgement;
//   - the header band of addresses: a street on two lines at most, then the postal code, never cut
//     when the codes differ; long shared prefixes and homonymous streets stay distinct at 375, 768
//     and 1 280 px, the band taking a fifth of the window at most; axe WCAG 2.1 AA without violation.
//
// Responses: the frozen served responses of `__fixtures__/adresse-comparaison.json` (guarded by
// `api/backend/tests/test_adresse_comparaison.py`); the renamed variants below are SYNTHETIC and keep
// the served shape.

type Reply = { status: number; body: unknown };
type Contract = { method: 'GET'; pathname: string; query: Record<string, string>; minCalls: number; maxCalls: number; responses: Reply[] };
interface Colonne { ban_id: string; numero_voie: string | null; code_postal: string | null; libelle: string | null }
interface Reponse { statut: string; donnees: { colonnes: Colonne[]; groupes: { cle: string; lignes: { cle: string; cellules: ({ ban_id: string } & Record<string, unknown>)[] }[] }[]; ids_ecartes: string[] }; meta: null }

const REPONSES = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/adresse-comparaison.json', import.meta.url), 'utf8')
) as Record<string, Reponse>;
const DEUX = REPONSES.deux_adresses;
const [A, B] = DEUX.donnees.colonnes.map((colonne) => colonne.ban_id);
const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] as const;

const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeur(partiel: Record<string, unknown>) {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: null, rattachement: null, reference: null, ...partiel };
}

/** The page of A, as `/v1/adresse/{id}` serves it (no theme: the comparison is the subject here). */
const FICHE_A: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			identite: {
				etat: 'etablie', ban_id: A, libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon', numero: '12', rep: null,
				nom_voie: 'Rue des Lilas', code_postal: '85000', commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
				departement: { code: '85', nom: 'Vendée' },
				parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: 1, rattachement: 'parcelle_du_point', motif: null }
			},
			couverture: { etat: 'heberge', motif: null },
			reperes: {
				dpe: {
					...valeur({ field_id: 'adresse.dpe.nombre_declares', valeur: 0, unite: 'none', source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur' }),
					selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] }
				},
				ventes: {
					nombre: valeur({ field_id: 'adresse.dvf.ventes_parcelle.nombre', valeur: 0, unite: 'count', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' }),
					derniere: valeur({ field_id: 'adresse.dvf.ventes_parcelle.derniere', etat: 'not_available', motif: 'aucune_vente', unite: 'none', periode: '2021-2025', source: SOURCE_DVF, rattachement: 'parcelle_du_point' })
				},
				argiles: valeur({ field_id: 'adresse.risques.argiles_au_point', etat: 'not_reported', unite: 'none', rattachement: 'au_point' }),
				reference_commune: {
					maisons: valeur({ field_id: 'adresse.reference_commune.prix_median_maison', etat: 'insufficient_sample', unite: 'euro_per_m2', source: SOURCE_DVF, rattachement: 'commune_reference' }),
					appartements: valeur({ field_id: 'adresse.reference_commune.prix_median_appartement', etat: 'insufficient_sample', unite: 'euro_per_m2', source: SOURCE_DVF, rattachement: 'commune_reference' })
				}
			},
			themes: []
		},
		meta: { source: [] }
	}
};

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

const RECHERCHE_B: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			ordre_types: ['adresse', 'commune'],
			resultats: [
				{
					type_resultat: 'adresse', ban_id: B, numero: '1', rep: null, nom_voie: 'Rue du Lieu', code_postal: '85000',
					code_commune: '85191', nom_commune: 'La Roche-sur-Yon', departement: { code: '85', nom: 'Vendée' }
				}
			],
			hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'], territoire: null, homonymes: [] } }
		}
	}
};

function attente(pathname: string, query: Record<string, string>, replies: Reply[], minCalls = replies.length): Contract {
	return { method: 'GET', pathname, query, minCalls, maxCalls: replies.length, responses: replies };
}
const comparaison = (ids: string[], body: unknown, max = 1): Contract =>
	attente('/v1/adresses/comparaison', { ids: ids.join(',') }, Array.from({ length: max }, () => ({ status: 200, body })), 1);

async function configure(request: APIRequestContext, expectations: Contract[]) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } })).ok()).toBe(true);
}
async function audit(request: APIRequestContext) {
	const res = await request.get(`${E2E_SOURCE_ORIGIN}/__requests`);
	expect(res.ok()).toBe(true);
	return (await res.json()) as { requests: { pathname: string; query: Record<string, string> }[]; violations: unknown[]; unmetExpectations: unknown[] };
}
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
	// C2: the categories of a comparison of communes are folded on arrival. The cases of this spec read
	// their rows: they are unfolded here, as a reader would open them; the fold itself is locked by
	// `comparateur-revue.spec.ts`. No fold exists for the addresses and the DPE documents.
	await page.locator('details.compare-groupe-pli').evaluateAll((plis) => plis.forEach((pli) => ((pli as HTMLDetailsElement).open = true)));
}
const norm = (value: string | null | undefined) => value?.replace(/[\s  ]+/g, ' ').trim() ?? null;
const region = (page: Page) => page.getByRole('region', { name: 'Tableau de comparaison' });

/**
 * Opens the folded panel « Modifier la sélection » (04.5-09, B5): with two objects or more the add
 * control and the filter live in it, closed on arrival. Clicked on its summary, as a visitor does.
 */
async function ouvrirModification(page: Page) {
	const pli = page.locator('details.comparer-modifier');
	await expect(pli).not.toHaveAttribute('open', '');
	await pli.locator('summary').click();
	await expect(pli).toHaveAttribute('open', '');
}

/** Opens the folded reference and method (04.5-09, B5), after the table, by its summary. */
async function ouvrirLecture(page: Page) {
	const pli = page.locator('details.comparer-lecture');
	await expect(pli).not.toHaveAttribute('open', '');
	await pli.locator('summary').click();
	await expect(pli).toHaveAttribute('open', '');
}

function evidencePath(name: string): string {
	const directory = process.env.DC_COMPARE_TABLE_EVIDENCE_DIR;
	if (!directory) return test.info().outputPath(name);
	mkdirSync(directory, { recursive: true });
	return join(directory, name);
}

/** Scrolls the page until the real `thead` has left the window under the site header. */
async function depasserEntete(page: Page) {
	await page.evaluate(() => {
		const bas = document.querySelector('thead')!.getBoundingClientRect().bottom;
		window.scrollTo(0, window.scrollY + bas + 120);
	});
	await expect(page.locator('.compare-bande')).toHaveCSS('visibility', 'visible');
}

/**
 * Text actually painted in each cell of the band: a character counts only when its box lies inside
 * the cell and inside its clipping span on both axes (a third line cut by the clamp is not seen).
 */
function textesVisibles(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		[...document.querySelectorAll<HTMLElement>('.compare-bande-cellule')].map((cellule) => {
			const boite = cellule.getBoundingClientRect();
			let visible = '';
			const marcheur = document.createTreeWalker(cellule, NodeFilter.SHOW_TEXT);
			for (let noeud = marcheur.nextNode() as Text | null; noeud; noeud = marcheur.nextNode() as Text | null) {
				const cadre = noeud.parentElement!.getBoundingClientRect();
				const texte = noeud.textContent ?? '';
				for (let i = 0; i < texte.length; i++) {
					const range = document.createRange();
					range.setStart(noeud, i);
					range.setEnd(noeud, i + 1);
					const r = range.getBoundingClientRect();
					const dedans =
						r.left >= Math.max(boite.left, cadre.left) - 0.5 && r.right <= Math.min(boite.right, cadre.right) + 0.5 &&
						r.top >= Math.max(boite.top, cadre.top) - 0.5 && r.bottom <= Math.min(boite.bottom, cadre.bottom) + 0.5;
					if (dedans) visible += texte[i];
				}
				visible += ' ';
			}
			return visible.replace(/\s+/g, ' ').trim();
		})
	);
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});
test.afterEach(async ({ page, request }) => {
	const result = await audit(request);
	expect(result.violations, JSON.stringify(result.violations)).toEqual([]);
	expect(result.unmetExpectations, JSON.stringify(result.unmetExpectations)).toEqual([]);
	expectNoUnexpectedOffLocalRequest(page);
});

test('tracer — « Comparer avec une autre adresse », then B added: two columns, four groups, the commune last, served gaps', async ({ page, request }) => {
	await configure(request, [
		// The page of A (SSR) reads its report; the comparator with A alone reads its fiche (one read by
		// navigation; the link is preloaded on tap since row 48, which may read it once more).
		attente(cheminRapport(A), REQUETE_RAPPORT, [enRapport(FICHE_A)]),
		attente(`/v1/adresse/${A}`, {}, [FICHE_A, FICHE_A], 1),
		attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [SANS_DOCUMENT]),
		attente(`/v1/adresse/${A}/ventes`, { page: '1' }, [SANS_VENTE]),
		attente(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [SANS_VENTE_DECLAREE]),
		attente('/v1/commune', { code_insee: '85191' }, [COMMUNE_NON_SERVIE], 0),
		attente('/v1/adresses/recherche', { q: '1 Rue du Lieu', limite: '8' }, [RECHERCHE_B]),
		comparaison([A, B], DEUX, 2)
	]);
	await open(page, `/adresse/${A}`);

	const bouton = page.getByRole('link', { name: 'Comparer avec une autre adresse' });
	await expect(bouton).toHaveAttribute('href', `/comparer?adresses=${A}`);
	await bouton.click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A}$`));
	await expect(page.getByRole('heading', { level: 1, name: 'Comparer des adresses', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { level: 2, name: '12 Rue des Lilas' })).toBeVisible();
	await expect(page.getByText('Ajoutez une deuxième adresse pour lire les écarts.')).toBeVisible();
	// The address joined the address selection of the tab, never the communes one.
	expect(await page.evaluate(() => [sessionStorage.getItem('dc:comparaison:adresses'), sessionStorage.getItem('dc:comparaison')])).toEqual([
		JSON.stringify([A]),
		null
	]);

	await page.getByRole('button', { name: 'Ajouter une adresse' }).click();
	const champ = page.getByRole('combobox', { name: 'Adresse' });
	await champ.fill('1 Rue du Lieu');
	// The served address (the « see all results » option also quotes the query).
	await page.getByRole('option').filter({ hasText: 'La Roche-sur-Yon' }).first().click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}$`));
	await expect(region(page)).toBeVisible();
	const lues = (await audit(request)).requests.filter((row) => row.pathname === '/v1/adresses/comparaison').map((row) => row.query.ids);
	expect(new Set(lues)).toEqual(new Set([`${A},${B}`]));

	// Two columns, each named by its street, linked to its page, with « {CP} {Commune} ».
	await expect(page.locator('thead th[data-code]')).toHaveCount(2);
	await expect(page.locator(`thead th[data-code="${B}"] a.compare-nom`)).toHaveAttribute('href', `/adresse/${B}`);
	expect(norm(await page.locator(`thead th[data-code="${B}"] .compare-identite`).textContent())).toBe('85000 La Roche-sur-Yon');
	// Four groups, in the served order, each headed by a rowgroup header; the commune last.
	expect(await page.locator('tbody').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-groupe')))).toEqual([
		'documents',
		'ventes',
		'lieu',
		'commune_reference'
	]);
	expect(await page.locator('tbody tr.compare-groupe th').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('scope')))).toEqual(
		Array(4).fill('rowgroup')
	);
	const reference = page.locator('tbody[data-groupe="commune_reference"]');
	await expect(reference.locator('.compare-reference-phrase')).toHaveText("Ces valeurs décrivent la commune, pas l'adresse.");
	// 04.5-09 (B5): the reading note is folded after the table, its text unchanged, open in one action.
	await ouvrirLecture(page);
	await expect(page.getByText("Les valeurs de la commune sont rangées à part : elles ne décrivent pas l'adresse.")).toBeVisible();

	// The gap shown is the served one; a categorical value says only its verdict.
	const ecart = (cle: string) => page.locator(`tr[data-cle="${cle}"] td[data-code="${B}"] .compare-ecart`);
	expect(norm(await ecart('contenance').textContent())).toBe('−412 m² par rapport à 12 Rue des Lilas');
	expect(norm(await ecart('arret_proche').textContent())).toBe('+200 m par rapport à 12 Rue des Lilas');
	expect(norm(await ecart('argiles').textContent())).toBe('Différent');
	expect(norm(await ecart('ventes_parcelle').textContent())).toBe('Écart non calculé · donnée absente');
	// C40: the declared sales after « Dernière vente »; C41: two equal counts read « Identique ».
	expect(await page.locator('tbody[data-groupe="ventes"] tr[data-cle]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')))).toEqual([
		'ventes_parcelle',
		'derniere_vente',
		'ventes_declarees'
	]);
	expect(norm(await page.locator('tr[data-cle="ventes_declarees"] th.compare-critere').textContent())).toBe('Ventes déclarées à cette adresse');
	expect(norm(await ecart('ventes_declarees').textContent())).toBe('Identique');
	// No colour of judgement: a gap and a verdict are written in ink, a reason in warn, whatever the sign.
	const couleurs = await page.locator('.compare-ecart').evaluateAll((nodes) =>
		nodes.map((node) => [node.getAttribute('data-ecart'), getComputedStyle(node).color])
	);
	const encre = await page.evaluate(() => getComputedStyle(document.querySelector('.compare-critere')!).color);
	for (const [genre, couleur] of couleurs) {
		if (genre !== 'motif') expect(couleur, `${genre}`).toBe(encre);
	}

	const resultat = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(resultat.violations, JSON.stringify(resultat.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
});

test('adresses sans nature de période structurée : métadonnées communes ou divergentes gardées par cellule', async ({
	page,
	request
}) => {
	const divergente = structuredClone(DEUX);
	const ligneDivergente = divergente.donnees.groupes
		.find((groupe) => groupe.cle === 'ventes')!
		.lignes.find((ligne) => ligne.cle === 'ventes_declarees')!;
	ligneDivergente.cellules[1].periode = '2020-2024';
	await configure(request, [
		attente(
			'/v1/adresses/comparaison',
			{ ids: `${A},${B}` },
			[
				{ status: 200, body: DEUX },
				{ status: 200, body: divergente },
				{ status: 200, body: DEUX },
				{ status: 200, body: divergente }
			]
		)
	]);

	for (const width of [1440, 375] as const) {
		await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
		await open(page, `/comparer?adresses=${A},${B}`);
		let ligne = page.locator('tr[data-cle="ventes_declarees"]');
		await expect(ligne.locator('th.compare-critere .compare-meta')).toHaveCount(0);
		let metasCellules = ligne.locator('td .compare-meta');
		await expect(metasCellules).toHaveCount(2);
		await expect(metasCellules.nth(0)).toContainText('2021-2025');
		await expect(metasCellules.nth(1)).toContainText('2021-2025');
		await ligne.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: evidencePath(`adresse-meta-cellules-${width}.png`), animations: 'disabled' });

		await open(page, `/comparer?adresses=${A},${B}`);
		ligne = page.locator('tr[data-cle="ventes_declarees"]');
		await expect(ligne.locator('th.compare-critere .compare-meta')).toHaveCount(0);
		metasCellules = ligne.locator('td .compare-meta');
		await expect(metasCellules).toHaveCount(2);
		await expect(metasCellules.nth(0)).toContainText('2021-2025');
		await expect(metasCellules.nth(1)).toContainText('2020-2024');
		await expect(metasCellules.nth(0)).toContainText('Source : Demandes de valeurs foncières (DVF)');
		await expect(metasCellules.nth(1)).toContainText('Source : Demandes de valeurs foncières (DVF)');
		await ligne.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: evidencePath(`adresse-meta-divergente-${width}.png`), animations: 'disabled' });
	}
});

// 04-DECISIONS-TEXTES row 48 (WINDOWS n° 59): the link is preloaded on tap only, so a hover never
// reads the fiche of the address (identity, landmarks and upstream sources) for the comparator.
// Deterministic form (no rest of the pointer, forbidden in a critical spec): the option SvelteKit
// resolves for this link (the closest `data-sveltekit-preload-data`, the body saying `hover`) is
// `tap`, and a tap still preloads the comparator (one more read of the fiche, without navigation).
// With `tap`, SvelteKit's hover callback preloads neither data nor code (`preload_code` is off).
test('« Comparer avec une autre adresse » : préchargé au toucher seulement, jamais au survol', async ({ page, request }) => {
	await configure(request, [
		// The SSR of the page of A (its report), then the tap preload of the comparator with A alone.
		attente(cheminRapport(A), REQUETE_RAPPORT, [enRapport(FICHE_A)]),
		attente(`/v1/adresse/${A}`, {}, [FICHE_A], 1),
		attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [SANS_DOCUMENT]),
		attente(`/v1/adresse/${A}/ventes`, { page: '1' }, [SANS_VENTE]),
		attente(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [SANS_VENTE_DECLAREE]),
		attente('/v1/commune', { code_insee: '85191' }, [COMMUNE_NON_SERVIE], 0)
	]);
	await open(page, `/adresse/${A}`);

	const lien = page.getByRole('link', { name: 'Comparer avec une autre adresse' });
	const option = await lien.evaluate((a) => ({
		lien: a.closest('[data-sveltekit-preload-data]')?.getAttribute('data-sveltekit-preload-data') ?? null,
		page: document.body.getAttribute('data-sveltekit-preload-data')
	}));
	// 2026-11-10 (owner decision, review R-10): no data is preloaded on hover anywhere on the site, the body says `tap`.
	expect(option).toEqual({ lien: 'tap', page: 'tap' });

	const lecture = page.waitForRequest((requete) => new URL(requete.url()).pathname === `/v1/adresse/${A}`);
	await lien.dispatchEvent('mousedown');
	await lecture;
	await expect(page).toHaveURL(new RegExp(`/adresse/${A}$`));
	await expect
		.poll(async () => (await audit(request)).requests.filter((row) => row.pathname === `/v1/adresse/${A}`).length)
		.toBe(1);
});

test('the address band tells long shared prefixes and homonymous streets apart, a fifth of the window at most', async ({ page, request }) => {
	// SYNTHETIC names and postal codes on the served shape (UI-SPEC « Bande d'en-tête collante »).
	const renommer = (paires: [string, string][]) => {
		const copie = structuredClone(DEUX);
		copie.donnees.colonnes.forEach((colonne, index) => Object.assign(colonne, { numero_voie: paires[index][0], code_postal: paires[index][1] }));
		return copie;
	};
	const cas = [
		renommer([
			['12 avenue du Maréchal de Lattre de Tassigny', '85000'],
			['12 avenue du Maréchal Foch', '85000']
		]),
		renommer([
			['1 rue de la Gare', '85000'],
			['1 rue de la Gare', '85100']
		])
	];
	const ids = `${A},${B}`;
	await configure(request, [attente('/v1/adresses/comparaison', { ids }, [...cas, ...cas, ...cas].map((body) => ({ status: 200, body })))]);
	for (const largeur of [375, 768, 1280]) {
		for (const [index] of cas.entries()) {
			await page.setViewportSize({ width: largeur, height: 800 });
			await open(page, `/comparer?adresses=${ids}`);
			await expect(region(page)).toBeVisible();
			await depasserEntete(page);
			const textes = await textesVisibles(page);
			console.log(`RELEVE e2e ${largeur}px adresses cas=${index} bande_textes=${JSON.stringify(textes)}`);
			expect(textes.every((texte) => texte.length > 0), `${largeur}px ${JSON.stringify(textes)}`).toBe(true);
			expect(new Set(textes).size, `${largeur}px: band cells the reader cannot tell apart ${JSON.stringify(textes)}`).toBe(2);
			if (index === 1) {
				// Homonymous streets: the postal code is shown whole, never cut.
				expect(textes.map((texte) => texte.slice(-7))).toEqual(['(85000)', '(85100)']);
			}
			const hauteur = (await page.locator('.compare-bande').boundingBox())!.height;
			const plafond = await page.evaluate(() => window.innerHeight / 5);
			expect(hauteur, `${largeur}px bande ${hauteur}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		}
	}
});

// --- 04-10: DPE documents compared by their number (CMP-02, D-116, task 6 of the recette) --------
//
// Responses: the frozen served responses of `__fixtures__/dpe-comparaison.json` (guarded by
// `api/backend/tests/test_dpe_comparaison_public.py`); the document pages below are SYNTHETIC and
// keep the served shape of `GET /v1/dpe/{numero}`.

type ReponseDocuments = {
	statut: string;
	donnees: {
		colonnes: { numero: string; adresse_declaree: { etat: string; texte: string | null; motif: string | null } | null }[];
		groupes: { cle: string; lignes: { cle: string; identiques: boolean; cellules: { numero: string; valeur: unknown }[] }[] }[];
		numeros_ecartes: string[];
	};
	meta: null;
};
const DOCUMENTS_SERVIS = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/dpe-comparaison.json', import.meta.url), 'utf8')
) as Record<string, ReponseDocuments>;
const ANCIEN_ACTUEL = DOCUMENTS_SERVIS.ancien_et_actuel;
const TROIS = DOCUMENTS_SERVIS.meme_methode_trois;
const [ANCIEN, ACTUEL] = ANCIEN_ACTUEL.donnees.colonnes.map((colonne) => colonne.numero);

/** The rows of the document comparator, in the served order (R5, plan 04.5-13: per m² first, totals after). */
const LIGNES_DOCUMENT = [
	'classe_energie',
	'consommation_m2',
	'classe_climat',
	'emissions_m2',
	'consommation',
	'emissions',
	'type_batiment',
	'surface',
	'periode_construction',
	'energie_chauffage',
	'cout_annuel',
	'etabli_le',
	'valable_jusqu_au',
	'methode',
	'adresse_declaree',
	'adresse_ban'
];

const valeurDpe = (partiel: Record<string, unknown>) =>
	valeur({ source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', ...partiel });

/** The document of 2020 as `GET /v1/dpe/{numero}` serves it (former method, SYNTHETIC values). */
const DOCUMENT_ANCIEN: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			numero: ANCIEN,
			source_identity: { reference: ANCIEN, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
			disponibilite: 'disponible',
			methode: 'ancienne',
			dates: { etablissement: '2020-05-04', fin_validite: '2030-05-03' },
			reperes: {
				etiquette_energie: {
					classe: valeurDpe({ field_id: 'dpe.document.etiquette_dpe', valeur: 'D', unite: 'none' }),
					mesure: valeurDpe({ field_id: 'dpe.document.conso_ep', valeur: 17830.4, unite: 'kwh_ep_per_year' })
				},
				etiquette_climat: {
					classe: valeurDpe({ field_id: 'dpe.document.etiquette_ges', valeur: 'C', unite: 'none' }),
					mesure: valeurDpe({ field_id: 'dpe.document.emissions_ges', valeur: 3433.1, unite: 'kg_co2_eq_per_year' })
				},
				logement_decrit: {
					type_batiment: valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' }),
					surface: valeurDpe({ field_id: 'dpe.document.surface_habitable', valeur: 79, unite: 'm2' })
				},
				cout_annuel: valeurDpe({ field_id: 'dpe.document.cout_annuel', valeur: 1834.2, unite: 'euro_per_year' })
			},
			statut: { code: 'ancienne_methode', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
			caracteristiques: [valeurDpe({ field_id: 'dpe.document.type_batiment', valeur: 'maison', unite: 'none' })],
			adresse_declaree: { etat: 'publiee', texte: '12 rue des Lilas', motif: null },
			adresse_ban: { etat: 'sans_cle', ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null },
			adresse_divergente: null,
			provenance: { source: 'ADEME', date_import: '2026-09-30' },
			association: 'source_ban_equality',
			address_check: 'unverified'
		},
		meta: { source: [] }
	}
};

const comparaisonDpe = (numeros: string[], body: unknown, max = 1, status = 200): Contract =>
	attente('/v1/dpe/comparaison', { numeros: numeros.join(',') }, Array.from({ length: max }, () => ({ status, body })), 1);

async function couleursSansJugement(page: Page) {
	const couleurs = await page.locator('.compare-ecart').evaluateAll((nodes) =>
		nodes.map((node) => [node.getAttribute('data-ecart'), getComputedStyle(node).color])
	);
	const encre = await page.evaluate(() => getComputedStyle(document.querySelector('.compare-critere')!).color);
	expect(couleurs.length).toBeGreaterThan(0);
	for (const [genre, couleur] of couleurs) {
		if (genre !== 'motif') expect(couleur, `${genre}`).toBe(encre);
	}
}

test('task 6 — « Comparer avec un autre DPE », then a number added: two methods, no class gap, the reason said', async ({ page, request }) => {
	await configure(request, [
		// The document page (SSR), then the comparator with it alone (read again once or twice: the
		// link is preloaded on tap, like the address page).
		attente(`/v1/dpe/${ANCIEN}`, {}, [DOCUMENT_ANCIEN, DOCUMENT_ANCIEN, DOCUMENT_ANCIEN], 2),
		comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL, 2)
	]);
	await open(page, `/dpe/${ANCIEN}`);

	const bouton = page.getByRole('link', { name: 'Comparer avec un autre DPE' });
	await expect(bouton).toHaveAttribute('href', `/comparer?dpe=${ANCIEN}`);
	await expect(bouton).toHaveAttribute('data-sveltekit-preload-data', 'tap');
	await bouton.click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN}$`));
	// 04.8-08 (D-31): the h1 is the first prose of the main, its DPE is spelled out.
	await expect(page.getByRole('heading', { level: 1, name: 'Comparer des documents DPE (diagnostic de performance énergétique)', exact: true })).toBeVisible();
	await expect(page.getByText('Ajoutez un deuxième document DPE pour lire les écarts.')).toBeVisible();
	await expect(page.getByText('Le numéro figure sur le document et sur la page de chaque adresse.')).toBeVisible();

	await page.getByRole('textbox', { name: 'Numéro du DPE' }).fill(ACTUEL);
	await page.getByRole('button', { name: 'Ajouter un DPE' }).click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN},${ACTUEL}$`));
	await expect(region(page)).toBeVisible();
	const lues = (await audit(request)).requests.filter((row) => row.pathname === '/v1/dpe/comparaison').map((row) => row.query.numeros);
	expect(new Set(lues)).toEqual(new Set([`${ANCIEN},${ACTUEL}`]));

	expect(await page.locator('tbody').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-groupe')))).toEqual([
		'etiquettes',
		'logement',
		'cout_validite',
		'adresse'
	]);
	await expect(page.locator(`thead th[data-code="${ACTUEL}"] a.compare-nom`)).toHaveAttribute('href', `/dpe/${ACTUEL}`);
	expect(norm(await page.locator(`thead th[data-code="${ANCIEN}"] .compare-badge`).textContent())).toBe('Ancienne méthode');
	expect(norm(await page.locator(`thead th[data-code="${ACTUEL}"] .compare-identite`).textContent())).toBe('Adresse déclarée non affichée');
	const ecart = (cle: string) => page.locator(`tr[data-cle="${cle}"] td[data-code="${ACTUEL}"] .compare-ecart`);
	for (const cle of ['classe_energie', 'consommation', 'classe_climat', 'emissions']) {
		expect(norm(await ecart(cle).textContent()), cle).toBe('Écart non calculé · méthodes de calcul différentes');
	}
	expect(norm(await ecart('type_batiment').textContent())).toBe('Identique');
	expect(norm(await ecart('cout_annuel').textContent())).toBe(`+200 €/an par rapport à DPE n° ${ANCIEN}`);
	// 04.5-09 (B5): the reading note is folded after the table, its text unchanged, open in one action.
	await ouvrirLecture(page);
	await expect(
		page.getByText('Deux DPE établis avec des méthodes différentes (avant et après juillet 2021) ne se comparent pas : la ligne le dit.')
	).toBeVisible();
	await couleursSansJugement(page);

	// The documents joined their own selection of the tab, the nature modified last: the header
	// counts them, the switch names the three natures with their counts.
	expect(
		await page.evaluate(() => [
			sessionStorage.getItem('dc:comparaison:dpe'),
			sessionStorage.getItem('dc:comparaison'),
			sessionStorage.getItem('dc:comparaison:derniere-nature')
		])
	).toEqual([JSON.stringify([ANCIEN, ACTUEL]), null, 'dpe']);
	await expect(page.locator('.site-nav-count')).toHaveText('2');
	const nav = page.getByRole('navigation', { name: 'Nature des objets comparés' });
	await expect(nav.getByRole('link')).toHaveText(['Communes', 'Adresses', 'Documents DPE (2)']);
	await expect(nav.getByRole('link', { name: 'Documents DPE (2)' })).toHaveAttribute('aria-current', 'page');
	await expect(nav.getByRole('link', { name: 'Adresses' })).toHaveAttribute('href', '/comparer?adresses=');
	const consommation = page.locator('tr[data-cle="consommation"]');
	await expect(consommation.locator('th.compare-critere .compare-meta')).toHaveCount(0);
	// C3 (arbitrated by Ianis on 2026-11-10): the documents no longer write period and source under each value.
	await expect(consommation.locator('td .compare-meta')).toHaveCount(0);

	const resultat = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(resultat.violations, JSON.stringify(resultat.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);
	for (const width of [1440, 375] as const) {
		await page.setViewportSize({ width, height: width === 1440 ? 900 : 800 });
		await consommation.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: evidencePath(`dpe-meta-cellules-${width}.png`), animations: 'disabled' });
	}
});

test('DPE input: a malformed number is said and never sent; an unknown one and a fifth of the link are explained', async ({ page, request }) => {
	const quatre = [ACTUEL, '2485E0000003C', '2385E9999999X', '2485E0000004C'];
	await configure(request, [comparaisonDpe(quatre, TROIS)]);
	await open(page, `/comparer?dpe=${[...quatre, '2485E0000005K'].join(',')}`);

	await expect(region(page)).toBeVisible();
	const altere = page.getByRole('heading', { name: 'Lien altéré' }).locator('..');
	await expect(altere.getByText('Seuls les quatre premiers documents DPE du lien sont affichés.')).toBeVisible();
	await expect(altere.getByText('Le lien contenait un numéro DPE inconnu (2385E9999999X) ; il a été écarté.')).toBeVisible();
	await expect(page.locator('thead th[data-code]')).toHaveCount(3);
	// The third document has no establishment date: no verdict on its class.
	expect(norm(await page.locator('tr[data-cle="classe_energie"] td[data-code="2485E0000004C"] .compare-ecart').textContent())).toBe(
		'Écart non calculé · méthode non déterminée'
	);

	await ouvrirModification(page);
	await page.getByRole('textbox', { name: 'Numéro du DPE' }).fill('2385-E01');
	await page.getByRole('button', { name: 'Ajouter un DPE' }).click();
	await expect(page.getByRole('alert')).toHaveText("« 2385-E01 » n'a pas la forme d'un numéro DPE de l'ADEME (Agence de la transition écologique)."); // 04.8-08 (D-31)
	const lues = (await audit(request)).requests.filter((row) => row.pathname.startsWith('/v1/dpe'));
	expect(lues).toHaveLength(1);
});

test('natures never mix: a mixed link shows the first one with its sentence, a count of 0 keeps its link', async ({ page, request }) => {
	await configure(request, [comparaison([A, B], DEUX)]);
	await open(page, `/comparer?dpe=${ANCIEN},${ACTUEL}&adresses=${A},${B}`);

	await expect(page.getByRole('heading', { level: 1, name: 'Comparer des adresses', exact: true })).toBeVisible();
	await expect(page.getByText("Le lien mélangeait plusieurs natures d'objets ; seules les adresses sont affichées.")).toBeVisible();
	// The altered link is rewritten in place to the addresses only.
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}$`));
	const nav = page.getByRole('navigation', { name: 'Nature des objets comparés' });
	await expect(nav.getByRole('link')).toHaveText(['Communes', 'Adresses (2)', 'Documents DPE']);
	await expect(nav.getByRole('link', { name: 'Adresses (2)' })).toHaveAttribute('aria-current', 'page');
	expect(await page.evaluate(() => sessionStorage.getItem('dc:comparaison:dpe'))).toBeNull();
	await expect(page.locator('.site-nav-count')).toHaveText('2');

	await nav.getByRole('link', { name: 'Documents DPE' }).click();
	await expect(page).toHaveURL(/\/comparer\?dpe=$/);
	await expect(page.getByRole('heading', { level: 2, name: 'Choisissez deux documents DPE à comparer' })).toBeVisible();
	// Opening a nature is not a modification: the header still counts the addresses.
	await expect(page.locator('.site-nav-count')).toHaveText('2');
});

test('« Différences seulement » hides the rows served as identical for documents, a real gap stays', async ({ page, request }) => {
	await configure(request, [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL)]);
	await open(page, `/comparer?dpe=${ANCIEN},${ACTUEL}`);
	await expect(region(page)).toBeVisible();

	const identiques = ANCIEN_ACTUEL.donnees.groupes.flatMap((g) => g.lignes).filter((l) => l.identiques).map((l) => l.cle);
	expect(identiques).toEqual(expect.arrayContaining(['type_batiment', 'adresse_ban']));
	await ouvrirModification(page);
	await page.getByRole('checkbox', { name: 'Différences seulement' }).check();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN},${ACTUEL}&diff=1$`));
	for (const cle of identiques) await expect(page.locator(`tr[data-cle="${cle}"]`)).toHaveCount(0);
	await expect(page.locator('tr[data-cle="cout_annuel"]')).toHaveCount(1);
	await expect(page.locator('tr[data-cle="classe_energie"]')).toHaveCount(1);
	const lues = (await audit(request)).requests.filter((row) => row.pathname === '/v1/dpe/comparaison');
	expect(lues).toHaveLength(1);
});

test('a masked declared address never reaches the page, even when an answer would carry its text', async ({ page, request }) => {
	// SYNTHETIC fault on the served shape: the masked column and its cell carry a text.
	const fautive = structuredClone(ANCIEN_ACTUEL);
	fautive.donnees.colonnes[1].adresse_declaree = { etat: 'masquee', texte: 'SENTINELLE-0410 porte 3', motif: 'complement_present' };
	fautive.donnees.groupes.flatMap((g) => g.lignes).find((l) => l.cle === 'adresse_declaree')!.cellules[1].valeur = 'SENTINELLE-0410';
	await configure(request, [comparaisonDpe([ANCIEN, ACTUEL], fautive)]);
	await open(page, `/comparer?dpe=${ANCIEN},${ACTUEL}`);
	await expect(region(page)).toBeVisible();

	expect(norm(await page.locator(`thead th[data-code="${ACTUEL}"] .compare-identite`).textContent())).toBe('Adresse déclarée non affichée');
	expect(norm(await page.locator(`tr[data-cle="adresse_declaree"] td[data-code="${ACTUEL}"] .compare-ecart`).textContent())).toBe(
		'Écart non calculé · adresse déclarée non affichée'
	);
	expect(await page.content()).not.toContain('SENTINELLE');
});

test('a refused DPE publication is one outage state: no column, no value', async ({ page, request }) => {
	await configure(request, [
		comparaisonDpe([ANCIEN, ACTUEL], { detail: 'Donnees temporairement indisponibles' }, 1, 503)
	]);
	await open(page, `/comparer?dpe=${ANCIEN},${ACTUEL}`);

	await expect(page.getByRole('alert')).toContainText('Les données de comparaison sont momentanément indisponibles.');
	await expect(page.getByRole('button', { name: 'Recharger la comparaison' })).toBeVisible();
	await expect(region(page)).toHaveCount(0);
	await expect(page.locator('td')).toHaveCount(0);
});

test('the DPE band shows each number whole at 375, 768 and 1 280 px, a fifth of the window at most', async ({ page, request }) => {
	const numeros = TROIS.donnees.colonnes.map((colonne) => colonne.numero);
	await configure(request, [comparaisonDpe(numeros, TROIS, 3)]);
	for (const largeur of [375, 768, 1280]) {
		await page.setViewportSize({ width: largeur, height: 800 });
		await open(page, `/comparer?dpe=${numeros.join(',')}`);
		await expect(region(page)).toBeVisible();
		await depasserEntete(page);
		const textes = await textesVisibles(page);
		console.log(`RELEVE e2e ${largeur}px dpe bande_textes=${JSON.stringify(textes)}`);
		expect(textes, `${largeur}px`).toEqual(numeros);
		const hauteur = (await page.locator('.compare-bande').boundingBox())!.height;
		const plafond = await page.evaluate(() => window.innerHeight / 5);
		expect(hauteur, `${largeur}px bande ${hauteur}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	}
});

// --- 04-11: a DPE designated on a compared address (D-102, CMP-02, R06-09/7, task 5) -----------
//
// Responses: the frozen served responses of `__fixtures__/adresse-comparaison.json` with
// designations (guarded by `api/backend/tests/test_adresse_comparaison.py`); the list of documents
// of A below is SYNTHETIC and keeps the served shape of `GET /v1/adresse/{ban_id}/dpe`.

const UNE_DESIGNATION = REPONSES.une_designation;
const DEUX_DESIGNATIONS = REPONSES.deux_designations;
const UNE_ECARTEE = REPONSES.une_designation_une_ecartee;
const NOM_A = '12 Rue des Lilas, 85000 La Roche-sur-Yon';
const NOM_B = '1 Rue du Lieu, 85000 La Roche-sur-Yon';
const PHRASE_DESIGNATION = "Désigné par vous pour comparer ; ce document n'est rattaché à aucun logement.";

const carteDocument = (numero: string, classe: string, etabli: string, badges: string[] = []) => ({
	numero, etiquette_dpe: classe, etiquette_ges: 'C', type_batiment: 'maison', surface_habitable: 79, date_etablissement: etabli,
	date_fin_validite: null, badges, adresse_declaree: null
});
/** The documents declared at A, as `/v1/adresse/{A}/dpe` serves them (SYNTHETIC values). */
const DOCUMENTS_A: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			...(SANS_DOCUMENT.body as { donnees: Record<string, unknown> }).donnees,
			total: 2,
			distribution: { A: 0, B: 0, C: 1, D: 1, E: 0, F: 0, G: 0, unknown: 0 },
			documents: [carteDocument('2485E0000003C', 'C', '2024-03-01', ['plus_recent']), carteDocument(ANCIEN, 'D', '2020-05-04', ['ancienne_methode'])],
			selection: { etat: 'unique', motif: 'document_valide', date: '2024-03-01', document: { numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'C', date_etablissement: '2024-03-01' }, documents_ex_aequo: [] }
		}
	}
};
const comparaisonDesignee = (designes: string, body: unknown, max = 1): Contract =>
	attente('/v1/adresses/comparaison', { ids: `${A},${B}`, designes }, Array.from({ length: max }, () => ({ status: 200, body })), 1);

test('task 5 — designate a DPE on a compared address, then remove the designation: the link only', async ({ page, request }) => {
	await configure(request, [
		comparaison([A, B], DEUX, 2),
		attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [DOCUMENTS_A]),
		comparaisonDesignee(`${A}:${ANCIEN}`, UNE_DESIGNATION)
	]);
	await open(page, `/comparer?adresses=${A},${B}`);
	await expect(region(page)).toBeVisible();
	await expect(page.locator('tr[data-designe]')).toHaveCount(0);

	const designer = page.getByRole('button', { name: `Désigner un DPE pour ${NOM_A}` });
	await expect(designer).toHaveAttribute('aria-expanded', 'false');
	await designer.click();
	await expect(designer).toHaveAttribute('aria-expanded', 'true');
	const groupe = page.getByRole('group', { name: `Désigner un DPE pour ${NOM_A}` });
	await expect(groupe.getByRole('radio')).toHaveCount(2);
	// Nothing is chosen for the visitor; the label is the one of the contract.
	for (const radio of await groupe.getByRole('radio').all()) await expect(radio).not.toBeChecked();
	expect(norm(await groupe.locator('label').first().textContent())).toBe('DPE n° 2485E0000003C · classe C · mars 2024 Le plus récent');
	await expect(page.getByRole('button', { name: 'Désigner ce DPE' })).toBeDisabled();
	const ouvert = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(ouvert.violations, JSON.stringify(ouvert.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);

	// Escape closes the group, the focus goes back to « Désigner un DPE ».
	await groupe.getByRole('radio').first().focus();
	await page.keyboard.press('Escape');
	await expect(groupe).toHaveCount(0);
	await expect(designer).toBeFocused();
	await expect(designer).toHaveAttribute('aria-expanded', 'false');

	await designer.click();
	await page.getByRole('radio', { name: new RegExp(ANCIEN) }).check();
	await page.getByRole('button', { name: 'Désigner ce DPE' }).click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}&designes=${A}:${ANCIEN}$`));
	const lues = (await audit(request)).requests.filter((row) => row.pathname === '/v1/adresses/comparaison').map((row) => row.query.designes ?? null);
	expect(lues).toEqual([null, `${A}:${ANCIEN}`]);

	// The header names the designated DPE; its rows follow the documents, after the sentence.
	const entete = page.locator(`thead th[data-code="${A}"]`);
	expect(norm(await entete.locator('.designation-texte').textContent())).toBe(`DPE désigné : n° ${ANCIEN} · classe D · mai 2020`);
	const retirer = page.getByRole('button', { name: `Retirer la désignation du DPE pour ${NOM_A}` });
	await expect(retirer).toBeFocused();
	await expect(page.locator('tr.compare-designation-note')).toHaveCount(1);
	expect(norm(await page.locator('tr.compare-designation-note').textContent())).toBe(PHRASE_DESIGNATION);
	const designees = page.locator('tbody[data-groupe="documents"] tr[data-designe="true"]');
	expect(await designees.evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')))).toEqual(
		LIGNES_DOCUMENT.map((cle) => `designe_${cle}`)
	);
	// The other address: « Aucun DPE désigné », muted, no state badge, no gap.
	const sans = page.locator(`tr[data-designe="true"] td[data-code="${B}"]`);
	await expect(sans).toHaveCount(LIGNES_DOCUMENT.length);
	for (const cellule of await sans.all()) {
		expect(norm(await cellule.textContent())).toBe('Aucun DPE désigné');
		await expect(cellule).toHaveAttribute('data-state', 'sans-designation');
	}
	// No storage holds a designation: the tab keeps the addresses only.
	const stockage = await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }));
	expect(stockage).not.toContain(ANCIEN);
	const designe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(designe.violations, JSON.stringify(designe.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);

	// « Retirer la désignation »: no confirmation, the pair leaves the link, the focus goes back.
	await retirer.click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}$`));
	await expect(page.locator('tr[data-designe]')).toHaveCount(0);
	await expect(page.locator('tr.compare-designation-note')).toHaveCount(0);
	await expect(page.getByRole('button', { name: `Désigner un DPE pour ${NOM_A}` })).toBeFocused();
	// D-15 FRONT WR-01: the group does not reopen by itself with the removed document checked.
	await expect(groupe).toHaveCount(0);
	await expect(page.getByRole('radio')).toHaveCount(0);
	await expect(page.getByRole('button', { name: `Désigner un DPE pour ${NOM_A}` })).toHaveAttribute('aria-expanded', 'false');
});

test('D-15 FRONT WR-02: the open designation group stays readable on a phone (375 and 320 px, ten documents)', async ({ page, request }) => {
	const dix = Array.from({ length: 10 }, (_, index) => carteDocument(`2485E00000${String(index).padStart(2, '0')}C`, 'C', '2024-03-01'));
	const liste: Reply = {
		status: 200,
		body: { statut: 'ok', donnees: { ...(DOCUMENTS_A.body as { donnees: Record<string, unknown> }).donnees, total: 12, pages: 2, documents: dix } }
	};
	await configure(request, [comparaison([A, B], DEUX, 2), attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [liste, liste])]);
	for (const largeur of [375, 320]) {
		await page.setViewportSize({ width: largeur, height: 800 });
		await open(page, `/comparer?adresses=${A},${B}`);
		await expect(region(page)).toBeVisible();
		const designer = page.getByRole('button', { name: `Désigner un DPE pour ${NOM_A}` });
		await designer.click();
		await expect(page.getByRole('radio')).toHaveCount(10);
		const mesure = await page.evaluate(() => {
			// Line boxes of each number, read on its own text (independent of the markup around it).
			const lignes = [...document.querySelectorAll('label')]
				.filter((label) => label.querySelector('input[type="radio"]'))
				.map((label) => {
					const marcheur = document.createTreeWalker(label, NodeFilter.SHOW_TEXT);
					for (let noeud = marcheur.nextNode(); noeud; noeud = marcheur.nextNode()) {
						const debut = noeud.textContent!.indexOf('2485E');
						if (debut < 0) continue;
						const plage = document.createRange();
						plage.setStart(noeud, debut);
						plage.setEnd(noeud, debut + 13);
						return new Set([...plage.getClientRects()].map((rect) => Math.round(rect.top))).size;
					}
					return -1;
				});
			const ouvrir = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Désigner un DPE')!;
			const valider = [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Désigner ce DPE')!;
			return {
				lignes,
				ecart: valider.getBoundingClientRect().bottom - ouvrir.getBoundingClientRect().top,
				ecran: window.innerHeight,
				deborde: document.documentElement.scrollWidth > window.innerWidth
			};
		});
		console.log(`RELEVE e2e ${largeur}px groupe_designation_ouvert lignes_numero=${JSON.stringify(mesure.lignes)} ecart_valider=${mesure.ecart.toFixed(1)}`);
		expect(mesure.lignes, `${largeur}px: each number on one line`).toEqual(Array(10).fill(1));
		expect(mesure.ecart, `${largeur}px: « Désigner ce DPE » within one screen of « Désigner un DPE »`).toBeLessThanOrEqual(mesure.ecran);
		expect(mesure.deborde).toBe(false);
	}
});

test.describe('designation without JavaScript (04-DECISIONS-TEXTES row 67)', () => {
	test.use({ javaScriptEnabled: false });

	test('« Désigner un DPE », inert without JavaScript, is not rendered; the table is', async ({ page, request }) => {
		await configure(request, [comparaison([A, B], DEUX)]);
		await page.goto(`/comparer?adresses=${A},${B}`);
		await expect(region(page)).toBeVisible();
		await expect(page.locator('thead th.compare-commune')).toHaveCount(2);
		await expect(page.getByRole('button', { name: /^Désigner un DPE/ })).toHaveCount(0);
	});
});

test('a designated number not declared at its address is removed with the dedicated « Lien altéré »', async ({ page, request }) => {
	await configure(request, [comparaisonDesignee(`${A}:${ANCIEN},${B}:2485E0000099Z`, UNE_ECARTEE)]);
	await open(page, `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN},${B}:2485E0000099Z`);

	await expect(region(page)).toBeVisible();
	const altere = page.getByRole('heading', { name: 'Lien altéré' }).locator('..');
	// 04.8-08 (D-31): first DPE of the address comparison, spelled out.
	await expect(altere.getByText("Le DPE (diagnostic de performance énergétique) désigné pour 1 Rue du Lieu n'est pas déclaré à cette adresse ; la désignation a été retirée.")).toBeVisible();
	// The link written back keeps the designation the API kept, never the one it set aside.
	await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}&designes=${A}:${ANCIEN}$`));
	await expect(page.getByRole('button', { name: `Désigner un DPE pour ${NOM_B}` })).toBeVisible();
	await expect(page.getByRole('button', { name: `Retirer la désignation du DPE pour ${NOM_A}` })).toBeVisible();
});

test('the same couple compared directly or designated on two addresses reads the same (R06-09/7)', async ({ page, request }) => {
	await configure(request, [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL), comparaisonDesignee(`${A}:${ANCIEN},${B}:${ACTUEL}`, DEUX_DESIGNATIONS)]);
	const cellules = (selecteur: string) =>
		page.locator(selecteur).evaluateAll((rows) =>
			Object.fromEntries(
				rows.map((row) => [
					row.getAttribute('data-cle')!.replace(/^designe_/, ''),
					[row.querySelector('th')!.textContent, ...[...row.querySelectorAll('td')].map((td) => `${td.getAttribute('data-state')}|${td.textContent}`)]
						.map((texte) => (texte ?? '').replace(/[\s  ]+/g, ' ').trim())
				])
			)
		);

	await open(page, `/comparer?dpe=${ANCIEN},${ACTUEL}`);
	await expect(region(page)).toBeVisible();
	const directes = await cellules('tr[data-cle]');
	await open(page, `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN},${B}:${ACTUEL}`);
	await expect(region(page)).toBeVisible();
	const designees = await cellules('tr[data-designe="true"]');

	expect(Object.keys(directes)).toEqual(LIGNES_DOCUMENT);
	expect(designees).toEqual(directes);
	expect(designees.classe_energie[2]).toContain('Écart non calculé · méthodes de calcul différentes');
	expect(designees.cout_annuel[2]).toContain(`+200 €/an par rapport à DPE n° ${ANCIEN}`);
	expect(await page.content()).not.toContain('SENTINELLE');
});

// Codex review of the fix (plan 13): « Totaux annuels » works without JavaScript, opened by its summary
// and printed closed; the visibility follows the native [open] state in CSS, in a fresh context.
test.describe('R5 — totaux annuels sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	for (const cas of [
		{ nom: 'directe', url: () => `/comparer?dpe=${TROIS_NUMEROS.join(',')}`, prefixe: '', attentes: () => [comparaisonDpe(TROIS_NUMEROS, TROIS, 2)] },
		{
			nom: 'désignée',
			url: () => `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN},${B}:${ACTUEL}`,
			prefixe: 'designe_',
			attentes: () => [comparaisonDesignee(`${A}:${ANCIEN},${B}:${ACTUEL}`, DEUX_DESIGNATIONS, 2)]
		}
	]) {
		test(`${cas.nom} : fermé, les totaux sont masqués ; le summary les montre ; l'impression les montre fermés`, async ({ page, request }) => {
			await configure(request, cas.attentes());
			await page.goto(cas.url());
			await expect(region(page)).toBeVisible();
			const detail = page.locator('details.compare-detail');
			await expect(detail).not.toHaveAttribute('open');
			const lignes = (cles: string[]) => cles.map((cle) => page.locator(`tr[data-cle="${cas.prefixe}${cle}"]`));
			for (const tr of lignes(['consommation_m2', 'emissions_m2'])) await expect(tr).toBeVisible();
			for (const tr of lignes(['consommation', 'emissions'])) await expect(tr).toBeHidden();

			// Printed closed, without script: both totals are on paper.
			await page.emulateMedia({ media: 'print' });
			await expect(detail).not.toHaveAttribute('open');
			for (const tr of lignes(['consommation', 'emissions'])) await expect(tr).toBeVisible();
			await page.emulateMedia({ media: 'screen' });
			for (const tr of lignes(['consommation', 'emissions'])) await expect(tr).toBeHidden();

			// Opened by its summary, without script.
			await detail.locator('summary').click();
			await expect(detail).toHaveAttribute('open', '');
			for (const tr of lignes(['consommation', 'emissions'])) await expect(tr).toBeVisible();
		});
	}
});

test('with designations, the address band still tells shared prefixes apart, a fifth of the window at most', async ({ page, request }) => {
	// SYNTHETIC names and postal codes on the served shape, two DPE designated (taller headers).
	const renommer = (paires: [string, string][]) => {
		const copie = structuredClone(DEUX_DESIGNATIONS);
		copie.donnees.colonnes.forEach((colonne, index) => Object.assign(colonne, { numero_voie: paires[index][0], code_postal: paires[index][1] }));
		return copie;
	};
	const cas = [
		renommer([
			['12 avenue du Maréchal de Lattre de Tassigny', '85000'],
			['12 avenue du Maréchal Foch', '85000']
		]),
		renommer([
			['1 rue de la Gare', '85000'],
			['1 rue de la Gare', '85100']
		])
	];
	const designes = `${A}:${ANCIEN},${B}:${ACTUEL}`;
	await configure(request, [
		attente('/v1/adresses/comparaison', { ids: `${A},${B}`, designes }, [...cas, ...cas, ...cas].map((body) => ({ status: 200, body }))),
		comparaison([A, B], DEUX)
	]);
	// Height added to the headers at 375 px by the designation control alone (no designation).
	await page.setViewportSize({ width: 375, height: 800 });
	await open(page, `/comparer?adresses=${A},${B}`);
	await expect(region(page)).toBeVisible();
	const sansDesignation = await page.evaluate(() => ({
		thead: document.querySelector('thead')!.getBoundingClientRect().height,
		designation: document.querySelector('thead .designation')!.getBoundingClientRect().height,
		haut: document.querySelector('thead')!.getBoundingClientRect().top + window.scrollY
	}));
	console.log(`RELEVE e2e 375px adresses sans designation thead=${sansDesignation.thead.toFixed(1)} bloc_designation=${sansDesignation.designation.toFixed(1)} haut_thead=${sansDesignation.haut.toFixed(1)}`);
	for (const largeur of [375, 768, 1280]) {
		for (const [index] of cas.entries()) {
			await page.setViewportSize({ width: largeur, height: 800 });
			await open(page, `/comparer?adresses=${A},${B}&designes=${designes}`);
			await expect(region(page)).toBeVisible();
			const entete = await page.evaluate(() => ({
				thead: document.querySelector('thead')!.getBoundingClientRect().height,
				designation: Math.max(...[...document.querySelectorAll('thead .designation')].map((bloc) => bloc.getBoundingClientRect().height))
			}));
			console.log(`RELEVE e2e ${largeur}px adresses designation cas=${index} thead=${entete.thead.toFixed(1)} bloc_designation=${entete.designation.toFixed(1)}`);
			await depasserEntete(page);
			const textes = await textesVisibles(page);
			console.log(`RELEVE e2e ${largeur}px adresses designation cas=${index} bande_textes=${JSON.stringify(textes)}`);
			expect(new Set(textes).size, `${largeur}px: band cells the reader cannot tell apart ${JSON.stringify(textes)}`).toBe(2);
			if (index === 1) expect(textes.map((texte) => texte.slice(-7))).toEqual(['(85000)', '(85100)']);
			const hauteur = (await page.locator('.compare-bande').boundingBox())!.height;
			const plafond = await page.evaluate(() => window.innerHeight / 5);
			expect(hauteur, `${largeur}px bande ${hauteur}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		}
	}
});

// --- C39: check the documents of an address, then compare them (provisional texts, row 71) ---------

const DEUX_DOCUMENTS: Reply = {
	status: 200,
	body: {
		statut: 'ok',
		donnees: {
			etat: 'value', motif: null, total: 2, page: 1, pages: 1, par_page: 10, generation: '0123456789abcdef',
			distribution: { A: 0, B: 0, C: 0, D: 1, E: 1, F: 0, G: 0, unknown: 0 }, date_import: '2026-09-30',
			documents: [
				{ numero: ACTUEL, etiquette_dpe: 'D', etiquette_ges: 'C', type_batiment: 'maison', surface_habitable: 79,
					date_etablissement: '2024-03-12', date_fin_validite: '2034-03-11', badges: ['plus_recent'],
					adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } },
				{ numero: ANCIEN, etiquette_dpe: 'E', etiquette_ges: 'D', type_batiment: 'maison', surface_habitable: 79,
					date_etablissement: '2020-06-01', date_fin_validite: '2030-05-31', badges: [],
					adresse_declaree: { etat: 'publiee', texte: '12 rue des Lilas', motif: null } }
			],
			selection: {
				etat: 'unique', motif: 'document_valide', date: '2024-03-12',
				document: { numero: ACTUEL, etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' }, documents_ex_aequo: []
			},
			signal_plusieurs_logements: null, constat_d110: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur'
		}
	}
};

test('C39 — the documents of an address checked on a phone, then compared: exactly these two, cards within their ceiling', async ({ page, request }) => {
	await page.setViewportSize({ width: 375, height: 800 });
	await configure(request, [
		attente(cheminRapport(A), REQUETE_RAPPORT, [enRapport(FICHE_A)]),
		attente(`/v1/adresse/${A}`, {}, [FICHE_A, FICHE_A], 0),
		attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [DEUX_DOCUMENTS]),
		attente(`/v1/adresse/${A}/ventes`, { page: '1' }, [SANS_VENTE]),
		attente(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [SANS_VENTE_DECLAREE]),
		attente('/v1/commune', { code_insee: '85191' }, [COMMUNE_NON_SERVIE], 0),
		// The comparator, read once (twice if the link is preloaded on tap first).
		comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL, 2)
	]);
	await open(page, `/adresse/${A}`);

	const cartes = page.locator('section.doclist ol.doclist-cards > li');
	await expect(cartes).toHaveCount(2);
	// One box per card, labelled with its number, a 44 px target; the cards keep their ceiling
	// (160 px, 185 px with the declared address line) at 375 px; 191 and 217 px since the 14 px floor
	// of 04.8-01, the same relative margins over the densest cards at the new sizes (derivation in
	// adresse-fiche.spec.ts, PLAFOND_CARTE and PLAFOND_CARTE_LIGNE4).
	const mesures = await cartes.evaluateAll((lis) =>
		lis.map((li) => ({
			hauteur: Math.round(li.getBoundingClientRect().height * 10) / 10,
			ligne4: li.querySelector('.doccard-l4') !== null,
			cible: Math.round((li.querySelector('label.doccard-choix')?.getBoundingClientRect().height ?? 0) * 10) / 10,
			droite: Math.round((li.querySelector('label.doccard-choix')?.getBoundingClientRect().right ?? 999) * 10) / 10
		}))
	);
	console.log(`RELEVE C39 cartes=${JSON.stringify(mesures)}`);
	for (const mesure of mesures) {
		expect(mesure.hauteur).toBeLessThanOrEqual(mesure.ligne4 ? 217 : 191);
		expect(mesure.cible).toBeGreaterThanOrEqual(44);
		expect(mesure.droite).toBeLessThanOrEqual(375);
	}
	// Checked in the order of the comparison: the older document first.
	const premiere = page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${ANCIEN}` });
	const seconde = page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${ACTUEL}` });
	await expect(page.getByRole('link', { name: /^Comparer les documents/ })).toHaveCount(0);
	// B3 (UI-008, plan 04.5-05): the purpose of the boxes is said before the first one, with the count
	// of the boxes checked by the visitor.
	const consigne = page.locator('section.doclist .doclist-consigne');
	await expect(consigne).toContainText('Sélectionnez 2 à 4 documents DPE pour les comparer');
	await expect(consigne.locator('.doclist-compteur')).toHaveText('0 sélectionné');
	expect(await consigne.evaluate((noeud) => {
		const caseACocher = noeud.closest('section')!.querySelector('input[type="checkbox"]')!;
		return Boolean(noeud.compareDocumentPosition(caseACocher) & Node.DOCUMENT_POSITION_FOLLOWING);
	})).toBe(true);
	// Keyboard: focus and Space check a box.
	await premiere.focus();
	await page.keyboard.press('Space');
	await expect(premiere).toBeChecked();
	await expect(consigne.locator('.doclist-compteur')).toHaveText('1 sélectionné');
	await seconde.check();
	await expect(consigne.locator('.doclist-compteur')).toHaveText('2 sélectionnés');
	const bouton = page.getByRole('link', { name: 'Comparer les documents (2)' });
	await expect(bouton).toHaveAttribute('href', `/comparer?dpe=${ANCIEN},${ACTUEL}`);
	const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
	expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
	const resultat = await new AxeBuilder({ page }).include('section.doclist').withTags([...TAGS_WCAG]).analyze();
	expect(resultat.violations, JSON.stringify(resultat.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))).toEqual([]);

	// C42: the similar sales are prefilled from these two houses (surface of the most recent), their
	// origin said, and nothing is searched: no read of the similar sales (the source refuses any).
	// Read in the panel of the similar sales (04.7-03); the checked documents stay checked meanwhile.
	await page.locator('.actions-plus summary').click();
	await page.getByRole('link', { name: 'Ventes ressemblantes' }).first().click();
	const formulaire = page.locator('section#ventes-ressemblantes form');
	await expect(formulaire.getByRole('radio', { name: 'Maison' })).toBeChecked();
	await expect(formulaire.getByRole('spinbutton', { name: 'Surface (m²)' })).toHaveValue('79');
	// UI-007 (04.5-06): the surface names the served document it comes from (the most recent, the current one).
	await expect(formulaire.locator('.comparables-origine')).toHaveText(
		`Type d'après les DPE de cette adresse. Surface issue du DPE n° ${ACTUEL}, le plus récent de cette adresse.`
	);
	await expect(formulaire.locator('.comparables-origine a')).toHaveAttribute('href', `/dpe/${ACTUEL}`);
	expect((await audit(request)).requests.filter((row) => row.pathname.endsWith('/ventes-ressemblantes'))).toEqual([]);

	await page.keyboard.press('Escape');
	await expect(premiere).toBeChecked();
	await bouton.click();
	await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN},${ACTUEL}$`));
	// 04.8-08 (D-31): the h1 is the first prose of the main, its DPE is spelled out.
	await expect(page.getByRole('heading', { level: 1, name: 'Comparer des documents DPE (diagnostic de performance énergétique)', exact: true })).toBeVisible();
	await expect(page.locator('thead th[data-code]')).toHaveCount(2);
	expect(await page.evaluate(() => sessionStorage.getItem('dc:comparaison:dpe'))).toBe(JSON.stringify([ANCIEN, ACTUEL]));
});

// --- C4 (plan 04.5-07): an address whose declared sales are VEFA only -----------------------------
//
// The page asks for the declared sales with their VEFA (`vefa=incluses`) and shows them marked; the
// line C40 of the comparator counts no VEFA (D-12, `compte_ventes_adresse`) and keeps « Aucune vente
// rattachée » for that address. Both answers are SYNTHETIC: the declared list is the VEFA-only variant
// of fixtures/adresse-ventes.ts, the comparison is `deux_adresses` with the cell of A served as the
// API serves an address without any attached sale (not_available, `aucune_vente_rattachee`).
test('C4 — adresse aux seules VEFA (synthétique) : la page montre ses VEFA marquées, le comparateur garde « Aucune vente rattachée »', async ({ page, request }) => {
	const comparee = structuredClone(DEUX);
	const ligne = comparee.donnees.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === 'ventes_declarees')!;
	Object.assign(ligne.cellules[0], { etat: 'not_available', motif: 'aucune_vente_rattachee', valeur: null });
	Object.assign(ligne.cellules[1], { ecart: null, motif_non_calcul: 'donnee_absente', verdict: null, identique_a_la_reference: false });
	await configure(request, [
		attente(cheminRapport(A), REQUETE_RAPPORT, [enRapport(FICHE_A)]),
		attente(`/v1/adresse/${A}`, {}, [FICHE_A, FICHE_A], 0),
		attente(`/v1/adresse/${A}/dpe`, { page: '1' }, [SANS_DOCUMENT]),
		attente(`/v1/adresse/${A}/ventes`, { page: '1' }, [SANS_VENTE]),
		attente(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, [VENTES_DECLAREES_VEFA_SEULES]),
		attente('/v1/commune', { code_insee: '85191' }, [COMMUNE_NON_SERVIE], 0),
		comparaison([A, B], comparee)
	]);
	await open(page, `/adresse/${A}?theme=ventes`);

	// The list: each VEFA card marked.
	const liste = page.locator('section#ventes-adresse');
	await expect(liste.locator('ol.ventes-cartes > li [data-vefa]')).toHaveText(['VEFA, hors statistiques', 'VEFA, hors statistiques']);

	await open(page, `/comparer?adresses=${A},${B}`);
	await expect(region(page)).toBeVisible();
	const cellule = page.locator(`tr[data-cle="ventes_declarees"] td[data-code="${A}"]`);
	await expect(cellule.locator('.state-badge')).toHaveText('Aucune vente rattachée');
	expect(norm(await cellule.textContent())).not.toMatch(/[0-9]/);
});

// --- C3 (plan 04.5-08): the line C40 of an address under the number and suffix rule -------------
//
// SYNTHETIC: `deux_adresses` with the cells of the line C40 served as the API serves a generation
// imported without the suffix (A, `suffixe_non_importe`) and a suffix without single correspondence
// (B, `suffixe_sans_correspondance`). Captured at the starting SHA of plan 08 (acc3eeff), when the page
// had no wording for these reasons, and again after, same input (`DC_REFONTE_D_EVIDENCE_DIR`).
function comparaisonC3(): Reponse {
	const comparee = structuredClone(DEUX);
	const ligne = comparee.donnees.groupes.flatMap((groupe) => groupe.lignes).find((ligne) => ligne.cle === 'ventes_declarees')!;
	Object.assign(ligne.cellules[0], { etat: 'not_available', motif: 'suffixe_non_importe', valeur: null, periode: null });
	Object.assign(ligne.cellules[1], {
		etat: 'not_available',
		motif: 'suffixe_sans_correspondance',
		valeur: null,
		periode: null,
		ecart: null,
		motif_non_calcul: 'donnee_absente',
		verdict: null,
		identique_a_la_reference: false
	});
	return comparee;
}

function preuveC3(nom: string): string {
	const dossier = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	if (!dossier) return test.info().outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

test('C3 — ligne C40 (synthétique), suffixe non disponible et sans correspondance : captures 1440 et 375', async ({ page, request }) => {
	for (const [largeur, hauteur] of [[1440, 900], [375, 812]] as const) {
		await configure(request, [comparaison([A, B], comparaisonC3())]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, `/comparer?adresses=${A},${B}`);
		await expect(region(page)).toBeVisible();
		const rangee = page.locator('tr[data-cle="ventes_declarees"]');
		await expect(rangee).toBeVisible();
		console.log(`RELEVE c3 c40_${largeur}=${norm(await rangee.textContent())}`);
		// C3 (04.5-08): each reason keeps the label of the page (report 4.1-14, R10), never a figure.
		await expect(rangee.locator(`td[data-code="${A}"] .state-badge`)).toHaveText('Suffixe non disponible');
		await expect(rangee.locator(`td[data-code="${B}"] .state-badge`)).toHaveText('Suffixe sans correspondance');
		expect(norm(await rangee.locator('td').allTextContents().then((textes) => textes.join(' ')))).not.toMatch(/[0-9]/);
		await rangee.screenshot({ path: preuveC3(`comparateur-c40-${largeur}.png`), animations: 'disabled' });
	}
});

// --- B5 (plan 04.5-09, UI-012; D45-01, D45-11): header of the comparator, addresses and documents ---
//
// Inputs wired in the fixtures (amendment 2 of the counter-review): `adresses=85191_0420_00012,
// 85191_0601_00001` (`deux_adresses`) and `dpe=2185E0000001A,2385E0123456A` (`ancien_et_actuel`), at
// 1 440 × 900 and 375 × 812. Captured at the starting SHA of plan 09 (d14f433d) and after, same
// input (`DC_REFONTE_D_EVIDENCE_DIR`). No export exists in the comparator (pre-existing, amendment 5):
// the shared content kept is the copied link and the printed page.

const B5_LARGEURS = [[1440, 900], [375, 812]] as const;

function preuveB5(nom: string): string {
	const dossier = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	if (!dossier) return test.info().outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

/**
 * Lines printed outside the table (print media, after `beforeprint`), whitespace normalised. A child
 * hidden in print is skipped: `innerText` of an element that is not rendered returns its `textContent`.
 */
function lignesImprimees(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		[...document.querySelector('.comparer')!.children]
			.filter((element) => !element.classList.contains('comparer-tableau') && getComputedStyle(element).display !== 'none')
			.flatMap((element) => (element as HTMLElement).innerText.split('\n'))
			.map((ligne) => ligne.replace(/\s+/g, ' ').trim())
			.filter(Boolean)
	);
}

/** Every line printed outside the table at the starting SHA (d14f433d). */
const B5_IMPRIME_AU_DEPART: Record<'adresse' | 'dpe', string[]> = {
	adresse: [
		"Comparer sans changer le sens des données",
		"Comparer des adresses",
		"Les valeurs gardent leur période, leur définition et leur source. Aucun score global.",
		"Référence des écarts : 12 Rue des Lilas",
		"« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.",
		"Lire les écarts",
		"Une différence de taux est affichée en points. Aucun écart n'est calculé quand les périodes, les unités ou les définitions diffèrent : la ligne le dit.",
		"Les valeurs de la commune sont rangées à part : elles ne décrivent pas l'adresse."
	],
	dpe: [
		"Comparer sans changer le sens des données",
		// 04.8-08 (D-31): the only line that changes since the starting SHA, the acronym spelled out in the h1.
		"Comparer des documents DPE (diagnostic de performance énergétique)",
		"Les valeurs gardent leur période, leur définition et leur source. Aucun score global.",
		"Référence des écarts : DPE n° 2185E0000001A",
		"« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.",
		"Lire les écarts",
		"Une différence de taux est affichée en points. Aucun écart n'est calculé quand les périodes, les unités ou les définitions diffèrent : la ligne le dit.",
		"Deux DPE établis avec des méthodes différentes (avant et après juillet 2021) ne se comparent pas : la ligne le dit."
	]
};

const B5_CAS = [
	{ nature: 'adresse' as const, url: `/comparer?adresses=${A},${B}`, attentes: () => [comparaison([A, B], DEUX)] },
	{ nature: 'dpe' as const, url: `/comparer?dpe=${ANCIEN},${ACTUEL}`, attentes: () => [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL)] }
];

test('B5 — captures des comparateurs d’adresses et de documents DPE, 1 440 × 900 et 375 × 812', async ({ page, request }) => {
	for (const { nature, url, attentes } of B5_CAS) {
		for (const [largeur, hauteur] of B5_LARGEURS) {
			await configure(request, attentes());
			await page.setViewportSize({ width: largeur, height: hauteur });
			await open(page, url);
			await expect(region(page)).toBeVisible();
			await page.evaluate(async () => {
				await document.fonts.ready;
				window.scrollTo({ top: 0, behavior: 'instant' });
			});
			const nom = nature === 'adresse' ? 'adresses' : 'dpe';
			await page.screenshot({ path: preuveB5(`${nom}-${largeur}-ecran.png`), animations: 'disabled' });
			await page.screenshot({ path: preuveB5(`${nom}-${largeur}-page.png`), animations: 'disabled', fullPage: true });
		}
	}
});

test('B5 — le lien copié et la page imprimée gardent le contenu du SHA de départ (adresses, documents DPE)', async ({ page, request, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	const cas = [
		{
			nature: 'adresse' as const,
			url: `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN}&diff=1`,
			attentes: [comparaisonDesignee(`${A}:${ANCIEN}`, UNE_DESIGNATION)],
			annonce: 'Lien copié. Il reprend les adresses, leur ordre et le filtre ; les valeurs sont relues à l’ouverture.'
		},
		{
			nature: 'dpe' as const,
			url: `/comparer?dpe=${ANCIEN},${ACTUEL}&diff=1`,
			attentes: [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL)],
			annonce: 'Lien copié. Il reprend les documents DPE, leur ordre et le filtre ; les valeurs sont relues à l’ouverture.'
		}
	];
	await page.setViewportSize({ width: 1440, height: 900 });
	for (const { nature, url, attentes, annonce } of cas) {
		await configure(request, attentes);
		await page.emulateMedia({ media: 'screen' });
		await open(page, url);
		await expect(region(page)).toBeVisible();
		await page.getByRole('button', { name: 'Copier le lien', exact: true }).click();
		await expect(page.getByRole('status').filter({ hasText: 'Lien copié.' })).toHaveText(annonce);
		const copie = new URL(await page.evaluate(() => navigator.clipboard.readText()));
		expect(copie.pathname + copie.search, nature).toBe(url);

		await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
		await page.emulateMedia({ media: 'print' });
		const imprimees = await lignesImprimees(page);
		console.log(`RELEVE b5 imprime_${nature}=${JSON.stringify(imprimees)}`);
		for (const ligne of B5_IMPRIME_AU_DEPART[nature]) expect(imprimees, `${nature} : ${ligne}`).toContain(ligne);
		await expect(page.locator('tr[data-cle]').last()).toBeVisible();
		await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
	}
});

// --- B5, task 2 (plan 04.5-09; D45-11, amendments 3, 6 and 7): every control reachable at the keyboard ---
//
// For the three natures with two objects: from the top of the page, Tab follows the visual order (nature
// switch, « Modifier la sélection », « Copier le lien », the table, the folded reference); the panel opens
// at the keyboard and holds the add control then the filter; each control is activated by Enter or Space;
// « Retirer » from two to one with the panel closed opens it and puts the focus on the add control. With
// four objects the add control is disabled, announced by the sentence of the limit, and skipped by Tab.
// The 4-column answers below are SYNTHETIC copies of a served column under another key.

const COMMUNES_SERVIES = JSON.parse(
	readFileSync(new URL('../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')
) as Record<string, { donnees: { communes: { code_insee: string; nom: string }[]; criteres: { cellules: { code_insee: string }[] }[] } }>;
const COMMUNES_DEUX = COMMUNES_SERVIES.deux_communes_hebergees;

/** The answer with copies of its last column under `ids` (SYNTHETIC; communes, addresses or documents). */
function avecColonnes<T>(reponse: T, ids: string[]): T {
	const copie = structuredClone(reponse) as unknown as { donnees: Record<string, unknown> };
	const d = copie.donnees as {
		communes?: Record<string, unknown>[];
		criteres?: { cellules: Record<string, unknown>[] }[];
		colonnes?: Record<string, unknown>[];
		groupes?: { lignes: { cellules: Record<string, unknown>[] }[] }[];
	};
	for (const id of ids) {
		if (d.communes && d.criteres) {
			const commune = { ...structuredClone(d.communes[d.communes.length - 1]), code_insee: id, nom: `Commune ${id}` } as Record<string, unknown> & { synthese?: Record<string, unknown> | null };
			if (commune.synthese) Object.assign(commune.synthese, { code_insee: id, nom_commune: commune.nom });
			d.communes.push(commune);
			for (const critere of d.criteres) critere.cellules.push({ ...critere.cellules[critere.cellules.length - 1], code_insee: id });
		} else {
			const cle = 'ban_id' in d.colonnes![0] ? 'ban_id' : 'numero';
			d.colonnes!.push({ ...structuredClone(d.colonnes![d.colonnes!.length - 1]), [cle]: id, ...(cle === 'ban_id' ? { numero_voie: `${id.slice(-1)} Rue Copiée`, libelle: `${id.slice(-1)} Rue Copiée, 85000 La Roche-sur-Yon` } : {}) });
			for (const ligne of d.groupes!.flatMap((groupe) => groupe.lignes)) {
				ligne.cellules.push({ ...ligne.cellules[ligne.cellules.length - 1], [cle]: id });
			}
		}
	}
	return copie as unknown as T;
}

/** Presses Tab from the focused element until `cible` has the focus; never focuses it by program. */
async function tabulerJusqua(page: Page, cible: ReturnType<Page['locator']>, max: number): Promise<number> {
	for (let pas = 1; pas <= max; pas++) {
		await page.keyboard.press('Tab');
		if (await cible.evaluate((element) => element === document.activeElement)) return pas;
	}
	throw new Error(`not reached by Tab within ${max} presses`);
}

interface CasClavier {
	nature: 'commune' | 'adresse' | 'dpe';
	url: string;
	attentes: () => Contract[];
	/** The add control: its button (communes, addresses) or its number field (documents). */
	ajout: (page: Page) => ReturnType<Page['locator']>;
	/** Activates the add control at the keyboard and checks its effect. */
	activerAjout: (page: Page) => Promise<void>;
	retirer?: string | RegExp;
	apresRetrait?: RegExp;
}

const CAS_CLAVIER: CasClavier[] = [
	{
		nature: 'commune',
		url: '/comparer?communes=44991,44992',
		attentes: () => [
			attente('/v1/communes/comparaison', { codes: '44991,44992' }, [{ status: 200, body: COMMUNES_DEUX }]),
			attente('/v1/communes', { code_insee: '44991' }, [
				{ status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '44991', nom: 'Ancienne', departement: '44', code_postal: null }] } } }
			])
		],
		ajout: (page) => page.getByRole('button', { name: 'Ajouter une commune' }),
		activerAjout: async (page) => {
			await page.keyboard.press('Enter');
			await expect(page.getByRole('combobox', { name: 'Rechercher une commune' })).toBeFocused();
		},
		retirer: 'Retirer Courante',
		apresRetrait: /\/comparer\?communes=44991$/
	},
	{
		nature: 'adresse',
		url: `/comparer?adresses=${A},${B}`,
		attentes: () => [comparaison([A, B], DEUX), attente(`/v1/adresse/${A}`, {}, [FICHE_A])],
		ajout: (page) => page.getByRole('button', { name: 'Ajouter une adresse' }),
		activerAjout: async (page) => {
			await page.keyboard.press('Enter');
			await expect(page.getByRole('combobox', { name: 'Adresse' })).toBeFocused();
		},
		retirer: 'Retirer 1 Rue du Lieu, 85000 La Roche-sur-Yon',
		apresRetrait: new RegExp(`/comparer\\?adresses=${A}$`)
	},
	{
		nature: 'dpe',
		url: `/comparer?dpe=${ANCIEN},${ACTUEL}`,
		attentes: () => [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL), attente(`/v1/dpe/${ANCIEN}`, {}, [DOCUMENT_ANCIEN])],
		ajout: (page) => page.getByRole('textbox', { name: 'Numéro du DPE' }),
		activerAjout: async (page) => {
			// A malformed number typed, then Enter: the form answers under the field, nothing is sent.
			await page.keyboard.type('2385-E01');
			await page.keyboard.press('Enter');
			await expect(page.getByRole('alert')).toHaveText("« 2385-E01 » n'a pas la forme d'un numéro DPE de l'ADEME (Agence de la transition écologique)."); // 04.8-08 (D-31)
		},
		// The column name carries a no-break space after « n° » (titreDocument).
		retirer: new RegExp(`^Retirer DPE n°\\s${ACTUEL}$`),
		apresRetrait: new RegExp(`/comparer\\?dpe=${ANCIEN}$`)
	}
];

for (const cas of CAS_CLAVIER) {
	test(`B5 clavier — ${cas.nature}, deux objets : chaque contrôle atteint par Tab dans l’ordre visuel et activé ; 2 → 1 panneau fermé`, async ({ page, request, context }) => {
		// A walk through the whole table, Tab by Tab.
		test.setTimeout(120_000);
		await context.grantPermissions(['clipboard-read', 'clipboard-write']);
		await configure(request, cas.attentes());
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, cas.url);
		await expect(region(page)).toBeVisible();
		const modifier = page.locator('details.comparer-modifier');
		const resumeModifier = modifier.locator('summary');
		const lecture = page.locator('details.comparer-lecture');
		const resumeLecture = lecture.locator('summary');
		const nav = page.getByRole('navigation', { name: 'Nature des objets comparés' });
		const copier = page.getByRole('button', { name: 'Copier le lien', exact: true });
		const placer = page.getByRole('button', { name: /^Placer .* en premier$/ }).first();
		await expect(modifier).not.toHaveAttribute('open', '');

		// Tab order follows the visual order, from the top of the page.
		await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
		const pas: number[] = [];
		for (const [cible, max] of [
			[nav.getByRole('link').first(), 40],
			[nav.getByRole('link').last(), 3],
			[resumeModifier, 3],
			[copier, 3],
			[placer, 30],
			[resumeLecture, 600]
		] as const) {
			pas.push(await tabulerJusqua(page, cible, max));
		}
		console.log(`RELEVE b5 clavier_${cas.nature}_2 pas=${JSON.stringify(pas)}`);

		// The panel opens at the keyboard; inside it the add control, then the filter, then out to « Copier le lien ».
		await resumeModifier.focus();
		await page.keyboard.press('Enter');
		await expect(modifier).toHaveAttribute('open', '');
		await tabulerJusqua(page, cas.ajout(page), 2);
		const filtre = page.getByRole('checkbox', { name: 'Différences seulement' });
		await tabulerJusqua(page, filtre, 3);
		await tabulerJusqua(page, copier, 3);

		// Each one activated at the keyboard.
		await filtre.focus();
		await page.keyboard.press('Space');
		await expect(page).toHaveURL(/&diff=1$/);
		await page.keyboard.press('Space');
		await expect(page).not.toHaveURL(/diff=1/);
		await copier.focus();
		await page.keyboard.press('Enter');
		await expect(page.getByRole('status').filter({ hasText: 'Lien copié.' })).toBeVisible();
		expect(new URL(await page.evaluate(() => navigator.clipboard.readText())).search).toBe(new URL(cas.url, 'http://x').search);
		await cas.ajout(page).focus();
		await cas.activerAjout(page);
		await expect(placer).toHaveAccessibleDescription(
			'« Placer en premier » change la référence utilisée pour lire les écarts. L’ordre ne constitue pas un classement.'
		);

		// The folded reference opens at the keyboard; an access link reaches its group.
		await resumeLecture.focus();
		await page.keyboard.press('Enter');
		await expect(lecture).toHaveAttribute('open', '');
		const lien = lecture.getByRole('navigation', { name: 'Accès aux groupes de comparaison' }).getByRole('link').first();
		await tabulerJusqua(page, lien, 3);
		const cible = (await lien.getAttribute('href'))!;
		await page.keyboard.press('Enter');
		await expect(page.locator(cible)).toBeFocused();

		// The panel closed again at the keyboard, then « Retirer » from two to one (amendment 3).
		await resumeModifier.focus();
		await page.keyboard.press('Enter');
		await expect(modifier).not.toHaveAttribute('open', '');
		// « Retirer » reached by Tab from the summary, never focused by program (plan 09 review, MINEUR).
		await tabulerJusqua(page, page.getByRole('button', { name: cas.retirer!, exact: true }), 60);
		await page.keyboard.press('Enter');
		await expect(page).toHaveURL(cas.apresRetrait!);
		await expect(modifier).toHaveAttribute('open', '');
		await expect(cas.ajout(page)).toBeFocused();
		await expect(cas.ajout(page)).toBeVisible();
	});
}

const AJOUT_4 = ['2485E0000006C'];
const QUATRE_DOCUMENTS = avecColonnes(TROIS, AJOUT_4);
const TROIS_NUMEROS = TROIS.donnees.colonnes.map((colonne) => colonne.numero);

test('B5 clavier — documents DPE de 3 à 4 : ajout au clavier, focus sur la phrase de la limite, ajout désactivé et sauté par Tab (amendement 6)', async ({ page, request }) => {
	const quatre = [...TROIS_NUMEROS, ...AJOUT_4];
	await configure(request, [comparaisonDpe(TROIS_NUMEROS, TROIS), comparaisonDpe(quatre, QUATRE_DOCUMENTS)]);
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, `/comparer?dpe=${TROIS_NUMEROS.join(',')}`);
	await expect(region(page)).toBeVisible();
	const modifier = page.locator('details.comparer-modifier');
	await modifier.locator('summary').focus();
	await page.keyboard.press('Enter');
	const champ = page.getByRole('textbox', { name: 'Numéro du DPE' });
	await tabulerJusqua(page, champ, 2);
	await page.keyboard.type(AJOUT_4[0]);
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${quatre.join(',')}$`));
	await expect(page.locator('thead th[data-code]')).toHaveCount(4);
	const plein = page.locator('#comparer-plein');
	await expect(plein).toBeFocused();
	await expect(plein).toHaveText('Quatre documents DPE au maximum : retirez-en un pour en ajouter un autre.');
	await expect(modifier).toHaveAttribute('open', '');
	await expect(champ).toBeDisabled();
	await expect(page.getByRole('button', { name: 'Ajouter un DPE' })).toBeDisabled();
	// From the sentence, Tab skips the disabled field and button and reaches the filter.
	await tabulerJusqua(page, page.getByRole('checkbox', { name: 'Différences seulement' }), 1);
});

const QUATRE_CAS: {
	nature: string;
	url: string;
	attentes: () => Contract[];
	ajout: (page: Page) => ReturnType<Page['locator']>;
	phrase: string;
	/** After « Retirer » on the fourth column: the link and the neighbouring column that takes the focus. */
	apresRetrait: RegExp;
	voisine: string;
}[] = [
	{
		nature: 'commune',
		url: '/comparer?communes=44991,44992,44993,44994',
		attentes: () => [
			attente('/v1/communes/comparaison', { codes: '44991,44992,44993,44994' }, [{ status: 200, body: avecColonnes(COMMUNES_DEUX, ['44993', '44994']) }]),
			attente('/v1/communes/comparaison', { codes: '44991,44992,44993' }, [{ status: 200, body: avecColonnes(COMMUNES_DEUX, ['44993']) }])
		],
		ajout: (page) => page.getByRole('button', { name: 'Ajouter une commune' }),
		phrase: 'Quatre communes au maximum : retirez-en une pour en ajouter une autre.',
		apresRetrait: /\/comparer\?communes=44991,44992,44993$/,
		voisine: '44993'
	},
	{
		nature: 'adresse',
		url: `/comparer?adresses=${A},${B},85191_0601_00002,85191_0601_00003`,
		attentes: () => [
			comparaison([A, B, '85191_0601_00002', '85191_0601_00003'], avecColonnes(DEUX, ['85191_0601_00002', '85191_0601_00003'])),
			comparaison([A, B, '85191_0601_00002'], avecColonnes(DEUX, ['85191_0601_00002']))
		],
		ajout: (page) => page.getByRole('button', { name: 'Ajouter une adresse' }),
		phrase: 'Quatre adresses au maximum : retirez-en une pour en ajouter une autre.',
		apresRetrait: new RegExp(`/comparer\\?adresses=${A},${B},85191_0601_00002$`),
		voisine: '85191_0601_00002'
	},
	{
		nature: 'dpe',
		url: `/comparer?dpe=${[...TROIS_NUMEROS, ...AJOUT_4].join(',')}`,
		attentes: () => [comparaisonDpe([...TROIS_NUMEROS, ...AJOUT_4], QUATRE_DOCUMENTS), comparaisonDpe(TROIS_NUMEROS, TROIS)],
		ajout: (page) => page.getByRole('textbox', { name: 'Numéro du DPE' }),
		phrase: 'Quatre documents DPE au maximum : retirez-en un pour en ajouter un autre.',
		apresRetrait: new RegExp(`/comparer\\?dpe=${TROIS_NUMEROS.join(',')}$`),
		voisine: TROIS_NUMEROS[2]
	}
];

for (const cas of QUATRE_CAS) {
	test(`B5 clavier — ${cas.nature}, quatre objets : ajout désactivé et annoncé, les autres contrôles atteints par Tab, « Retirer » par Tab : 4 → 3`, async ({ page, request }) => {
		// A walk through the whole table of four columns, Tab by Tab.
		test.setTimeout(180_000);
		await configure(request, cas.attentes());
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, cas.url);
		await expect(page.locator('thead th[data-code]')).toHaveCount(4);
		const modifier = page.locator('details.comparer-modifier');
		const copier = page.getByRole('button', { name: 'Copier le lien', exact: true });
		await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
		await tabulerJusqua(page, modifier.locator('summary'), 45);
		await page.keyboard.press('Enter');
		await expect(modifier).toHaveAttribute('open', '');
		await expect(cas.ajout(page)).toBeDisabled();
		await expect(page.locator('#comparer-plein')).toHaveText(cas.phrase);
		if (cas.nature !== 'dpe') await expect(cas.ajout(page)).toHaveAccessibleDescription(cas.phrase);
		// Tab skips the disabled add control: the filter, then « Copier le lien », then the table.
		await tabulerJusqua(page, page.getByRole('checkbox', { name: 'Différences seulement' }), 1);
		await tabulerJusqua(page, copier, 1);
		await tabulerJusqua(page, page.getByRole('button', { name: /^Placer .* en premier$/ }).first(), 30);
		await tabulerJusqua(page, page.locator('details.comparer-lecture > summary'), 900);

		// « Retirer » of the fourth column reached by Tab from the summary, then activated: 4 → 3, the focus on
		// the neighbouring column header (plan 09 review, MINEUR: never focused by program).
		await modifier.locator('summary').focus();
		const retirer = page.locator('thead th[data-code]').last().getByRole('button', { name: /^Retirer / });
		await tabulerJusqua(page, retirer, 80);
		await page.keyboard.press('Enter');
		await expect(page).toHaveURL(cas.apresRetrait);
		await expect(page.locator('thead th[data-code]')).toHaveCount(3);
		await expect(page.locator(`thead th[data-code="${cas.voisine}"]`)).toBeFocused();
		await expect(cas.ajout(page)).toBeEnabled();
	});
}

// Amendment 7: without JavaScript, the server renders the same order, and the GET form of the DPE
// numbers works from the folded panel (a native `details`, opened by its summary).
test.describe('B5 sans JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('ordre des blocs rendu par le serveur, puis ajout d’un DPE par le formulaire GET', async ({ page, request }) => {
		const ajoute = '2485E0000006C';
		const trois = [ANCIEN, ACTUEL, ajoute];
		await configure(request, [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL), comparaisonDpe(trois, avecColonnes(ANCIEN_ACTUEL, [ajoute]))]);
		await page.goto(`/comparer?dpe=${ANCIEN},${ACTUEL}`);
		await expect(region(page)).toBeVisible();
		const ordre = await page.evaluate(() => {
			const blocs = ['h1', '.comparer-objets', 'details.comparer-modifier > summary', 'table.compare-table', 'details.comparer-lecture > summary'];
			const elements = blocs.map((selecteur) => document.querySelector(selecteur));
			return elements.map((element, i) =>
				i === 0 ? element !== null : !!element && !!elements[i - 1] && Boolean(elements[i - 1]!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)
			);
		});
		expect(ordre).toEqual([true, true, true, true, true]);
		const modifier = page.locator('details.comparer-modifier');
		await expect(modifier).not.toHaveAttribute('open', '');
		await modifier.locator('summary').click();
		await expect(modifier).toHaveAttribute('open', '');
		await page.getByRole('textbox', { name: 'Numéro du DPE' }).fill(ajoute);
		await page.getByRole('button', { name: 'Ajouter un DPE' }).click();
		await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN}(%2C|,)${ACTUEL}&dpe=${ajoute}$`));
		await expect(page.locator('thead th[data-code]')).toHaveCount(3);
		await expect(page.locator(`thead th[data-code="${ajoute}"]`)).toHaveCount(1);
	});
});

// --- B6 (plan 04.5-10): one continuous table, a Source per cell, keyboard and names -----------------
//
// For the three natures, with two and four columns (the 4-column answers are SYNTHETIC copies of a served
// column, `avecColonnes`), at 1 440 × 900 and 375 × 812: one `table.compare-table` whose groups are
// consecutive `tbody[data-groupe]` children of it; `th[scope=col]` per object, `th[scope=row]` per row;
// each Source button named by its row label and its object (`nomAccessible`); from the first column
// header, Tab reaches the Source buttons of one row in the column order, Enter opens the panel, Escape
// gives the focus back; axe without violation; the document never scrolls sideways. After captures in
// `DC_REFONTE_D_EVIDENCE_DIR` (same inputs as plan 09 for two columns).

/** A label as an accessible-name pattern: regex characters escaped, any space matching any blank (no-break included). */
const motif = (texte: string) => texte.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s');
const nomSource = (libelle: string, objet: string) => new RegExp(`^Détail : ${motif(libelle)} · ${motif(objet)}$`);

interface CasB6 {
	nature: 'commune' | 'adresse' | 'dpe';
	url: string;
	attentes: () => Contract[];
	/** The objects as their actions and Source buttons name them, in column order (written here). */
	objets: string[];
}

const ADRESSES_4 = ['85191_0601_00002', '85191_0601_00003'];
const B6_CAS: CasB6[] = [
	{
		nature: 'commune',
		url: '/comparer?communes=44991,44992',
		attentes: () => [attente('/v1/communes/comparaison', { codes: '44991,44992' }, [{ status: 200, body: COMMUNES_DEUX }])],
		objets: ['Ancienne', 'Courante']
	},
	{
		nature: 'commune',
		url: '/comparer?communes=44991,44992,44993,44994',
		attentes: () => [attente('/v1/communes/comparaison', { codes: '44991,44992,44993,44994' }, [{ status: 200, body: avecColonnes(COMMUNES_DEUX, ['44993', '44994']) }])],
		objets: ['Ancienne', 'Courante', 'Commune 44993', 'Commune 44994']
	},
	{ nature: 'adresse', url: `/comparer?adresses=${A},${B}`, attentes: () => [comparaison([A, B], DEUX)], objets: [NOM_A, NOM_B] },
	{
		nature: 'adresse',
		url: `/comparer?adresses=${[A, B, ...ADRESSES_4].join(',')}`,
		attentes: () => [comparaison([A, B, ...ADRESSES_4], avecColonnes(DEUX, ADRESSES_4))],
		objets: [NOM_A, NOM_B, '2 Rue Copiée, 85000 La Roche-sur-Yon', '3 Rue Copiée, 85000 La Roche-sur-Yon']
	},
	{
		nature: 'dpe',
		url: `/comparer?dpe=${ANCIEN},${ACTUEL}`,
		attentes: () => [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL)],
		objets: [ANCIEN, ACTUEL].map((numero) => `DPE n° ${numero}`)
	},
	{
		nature: 'dpe',
		url: `/comparer?dpe=${[...TROIS_NUMEROS, ...AJOUT_4].join(',')}`,
		attentes: () => [comparaisonDpe([...TROIS_NUMEROS, ...AJOUT_4], QUATRE_DOCUMENTS)],
		objets: [...TROIS_NUMEROS, ...AJOUT_4].map((numero) => `DPE n° ${numero}`)
	}
];

/** Structure of the table as rendered: tables, groups as consecutive children, headers. */
function structureB6(page: Page) {
	return page.evaluate(() => {
		const tables = [...document.querySelectorAll('table.compare-table')];
		const groupes = [...document.querySelectorAll('tbody[data-groupe]')];
		return {
			tables: tables.length,
			groupes: groupes.length,
			enfants: groupes.every((tbody) => tbody.parentElement === tables[0]),
			consecutifs: groupes.slice(1).every((tbody, index) => groupes[index].nextElementSibling === tbody),
			colonnes: document.querySelectorAll('table.compare-table thead th[scope="col"][data-code]').length,
			lignes: document.querySelectorAll('table.compare-table tbody th[scope="row"]').length,
			debordement: document.documentElement.scrollWidth > window.innerWidth
		};
	});
}

/** The first row whose every cell carries a Source button: its key and its label. */
function ligneSourcee(page: Page) {
	return page.evaluate(() => {
		const tr = [...document.querySelectorAll('tbody[data-groupe] tr[data-cle]')].find((ligne) => {
			const cellules = [...ligne.querySelectorAll('td')];
			return cellules.length > 0 && cellules.every((cellule) => cellule.querySelector('.source-button'));
		})!;
		return { cle: tr.getAttribute('data-cle')!, libelle: tr.querySelector('th[scope="row"] > span')!.textContent!.replace(/\s+/g, ' ').trim() };
	});
}

for (const cas of B6_CAS) {
	const n = cas.objets.length;
	test(`B6 — ${cas.nature}, ${n} objets : un seul tableau continu, en-têtes, Source nommée par ligne et objet, axe, à 1 440 et 375`, async ({ page, request }) => {
		test.setTimeout(120_000);
		for (const [largeur, hauteur] of B5_LARGEURS) {
			await configure(request, cas.attentes());
			await page.setViewportSize({ width: largeur, height: hauteur });
			await open(page, cas.url);
			await expect(region(page)).toBeVisible();
			await expect(page.locator('table.compare-table')).toHaveCount(1);
			const structure = await structureB6(page);
			expect(structure, `${largeur} px`).toMatchObject({ tables: 1, enfants: true, consecutifs: true, colonnes: n, debordement: false });
			expect(structure.groupes).toBeGreaterThan(1);
			expect(structure.lignes).toBeGreaterThan(0);
			const { cle, libelle } = await ligneSourcee(page);
			for (const objet of cas.objets) {
				await expect(page.getByRole('button', { name: nomSource(libelle, objet) }), `${cle} — ${objet}`).toHaveCount(1);
			}
			const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
			expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
			await page.evaluate(async () => {
				await document.fonts.ready;
				window.scrollTo({ top: 0, behavior: 'instant' });
			});
			const nom = `b6-${cas.nature}-${n}-${largeur}`;
			await page.screenshot({ path: preuveB5(`${nom}-ecran.png`), animations: 'disabled' });
			await page.screenshot({ path: preuveB5(`${nom}-page.png`), animations: 'disabled', fullPage: true });
		}
	});

	test(`B6 clavier — ${cas.nature}, ${n} objets : depuis le premier en-tête de colonne, Tab atteint les Source d’une ligne dans l’ordre des colonnes ; Entrée ouvre, Échap rend le focus`, async ({ page, request }) => {
		test.setTimeout(120_000);
		await configure(request, cas.attentes());
		await page.setViewportSize({ width: 1440, height: 900 });
		await open(page, cas.url);
		await expect(region(page)).toBeVisible();
		const { cle, libelle } = await ligneSourcee(page);
		const ligne = page.locator(`tbody[data-groupe] tr[data-cle="${cle}"]`);
		// The starting point: the header of the first column (focusable by program, as « Retirer » leaves it).
		await page.locator('thead th[data-code]').first().focus();
		const boutons = cas.objets.map((objet) => ligne.getByRole('button', { name: nomSource(libelle, objet) }));
		const pas = [await tabulerJusqua(page, boutons[0], 250)];
		// Then one Tab per column, in the column order.
		for (const bouton of boutons.slice(1)) pas.push(await tabulerJusqua(page, bouton, 1));
		console.log(`RELEVE b6 clavier_${cas.nature}_${n} ligne=${cle} pas=${JSON.stringify(pas)}`);
		const dernier = boutons[boutons.length - 1];
		await page.keyboard.press('Enter');
		const volet = page.getByRole('dialog');
		await expect(volet.getByRole('heading', { name: new RegExp(`^${motif(libelle)} · ${motif(cas.objets[n - 1])}$`) })).toBeFocused();
		await page.keyboard.press('Escape');
		await expect(volet).toBeHidden();
		await expect(dernier).toBeFocused();
	});
}

// Counter-review I4: two « 12 Rue des Lilas » (85000 and 85100) have Source buttons of their own.
test('B6 — deux « 12 Rue des Lilas » (85000, 85100) : boutons Source et titres du volet distincts', async ({ page, request }) => {
	const homonymes = REPONSES.trois_dont_hors_couverture;
	const ids = homonymes.donnees.colonnes.map((colonne) => colonne.ban_id);
	await configure(request, [comparaison(ids, homonymes)]);
	await open(page, `/comparer?adresses=${ids.join(',')}`);
	await expect(region(page)).toBeVisible();
	const ligne = page.locator('tr[data-cle="dpe_declares"]');
	const noms = await ligne.locator('td .source-button').evaluateAll((boutons) => boutons.map((bouton) => bouton.getAttribute('aria-label')));
	expect(noms).toEqual([
		'Détail : DPE déclarés · 12 Rue des Lilas, 85000 La Roche-sur-Yon',
		'Détail : DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon'
	]);
	const bouton = page.getByRole('button', { name: 'Détail : DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon', exact: true });
	await bouton.click();
	await expect(page.getByRole('dialog').getByRole('heading', { name: 'DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon', exact: true })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(bouton).toBeFocused();
});

// UI-014: no cell of the address and document tables shows a raw ISO day or quarter; a date says its role
// when the address page already names it (« Corpus importé le … »), a neutral « Période : … » otherwise,
// « Période non renseignée » for a value served without period. The Source panel keeps the served form.
const DATE_BRUTE = /\d{4}-\d{2}-\d{2}|\d{4}[-_]T\d/;
const B6_DATES: { nom: string; url: string; attentes: () => Contract[] }[] = [
	...(['deux_adresses', 'trois_dont_hors_couverture'] as const).map((cle) => {
		const ids = REPONSES[cle].donnees.colonnes.map((colonne) => colonne.ban_id);
		return { nom: `adresses ${cle}`, url: `/comparer?adresses=${ids.join(',')}`, attentes: () => [comparaison(ids, REPONSES[cle])] };
	}),
	{
		nom: 'adresses deux_designations',
		url: `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN},${B}:${ACTUEL}`,
		attentes: () => [comparaisonDesignee(`${A}:${ANCIEN},${B}:${ACTUEL}`, DEUX_DESIGNATIONS)]
	},
	{ nom: 'documents ancien_et_actuel', url: `/comparer?dpe=${ANCIEN},${ACTUEL}`, attentes: () => [comparaisonDpe([ANCIEN, ACTUEL], ANCIEN_ACTUEL)] },
	{ nom: 'documents meme_methode_trois', url: `/comparer?dpe=${TROIS_NUMEROS.join(',')}`, attentes: () => [comparaisonDpe(TROIS_NUMEROS, TROIS)] }
];

for (const cas of B6_DATES) {
	test(`B6 dates — ${cas.nom} : aucune date ISO ni trimestre brut dans les cellules`, async ({ page, request }) => {
		await configure(request, cas.attentes());
		await open(page, cas.url);
		await expect(region(page)).toBeVisible();
		const textes = await page.locator('table.compare-table tbody td').allInnerTexts();
		expect(textes.length).toBeGreaterThan(0);
		expect(textes.filter((texte) => DATE_BRUTE.test(texte))).toEqual([]);
	});
}

test('B6 dates — adresses : le rôle nommé par la fiche, un libellé neutre sinon, la forme servie gardée dans le volet', async ({ page, request }) => {
	for (const [largeur, hauteur] of B5_LARGEURS) {
		await configure(request, [comparaison([A, B], DEUX)]);
		await page.setViewportSize({ width: largeur, height: hauteur });
		await open(page, `/comparer?adresses=${A},${B}`);
		await expect(region(page)).toBeVisible();
		const cellule = (cle: string, index: number) => page.locator(`tr[data-cle="${cle}"] td`).nth(index);
		for (const index of [0, 1]) {
			await expect(cellule('dpe_declares', index).locator('.compare-meta')).toContainText('Corpus importé le 30 septembre 2026');
			await expect(cellule('fibre', index).locator('.compare-meta')).toContainText(/Période\s:\s2e trimestre 2026/);
			await expect(cellule('hauteur', index).locator('.compare-meta')).toContainText('Période non renseignée');
			await expect(cellule('hauteur', index)).toHaveAttribute('data-state', 'value');
		}
		// An absence keeps its served state and reason, without any period line.
		await expect(cellule('ventes_parcelle', 1)).toHaveAttribute('data-state', 'not_available');
		await expect(cellule('ventes_parcelle', 1)).not.toContainText('Période');
		// The Source panel keeps the served day, exactly.
		const bouton = cellule('dpe_declares', 0).locator('.source-button');
		await bouton.click();
		const volet = page.getByRole('dialog');
		await expect(volet).toContainText('2026-09-30');
		await page.keyboard.press('Escape');
		await expect(bouton).toBeFocused();
	}
});

// --- R5 (plan 04.5-13): the per-m² measures at first level, the yearly totals after them ----------
//
// The document comparator reads the per-m² measures published by the ADEME (`dpe.document.conso_ep_m2`,
// `dpe.document.emissions_ges_m2`), the yearly totals keep their rows and fields after them. A document
// without a published per-m² value says « non renseigné », never total ÷ surface; every gap is served.

function preuveR5(nom: string): string {
	const dossier = process.env.DC_REFONTE_D_EVIDENCE_DIR;
	if (!dossier) return test.info().outputPath(nom);
	mkdirSync(dossier, { recursive: true });
	return join(dossier, nom);
}

test('R5 — énergie et climat par m² au premier niveau, totaux après ; un manque reste « non renseigné », la Source s’ouvre', async ({ page, request }) => {
	const [premier, second, troisieme] = TROIS_NUMEROS;
	await configure(request, [comparaisonDpe(TROIS_NUMEROS, TROIS, 3)]);
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, `/comparer?dpe=${TROIS_NUMEROS.join(',')}`);
	await expect(region(page)).toBeVisible();

	expect(await page.locator('tbody[data-groupe="etiquettes"] tr[data-cle]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')))).toEqual(
		['classe_energie', 'consommation_m2', 'classe_climat', 'emissions_m2', 'consommation', 'emissions']
	);
	const valeur = async (cle: string, code: string) =>
		norm(await page.locator(`tr[data-cle="${cle}"] td[data-code="${code}"] .state-value`).textContent());
	const ecart = async (cle: string, code: string) =>
		norm(await page.locator(`tr[data-cle="${cle}"] td[data-code="${code}"] .compare-ecart`).textContent());
	const consommation = page.locator('tr[data-cle="consommation_m2"]');
	expect(norm(await consommation.locator('th.compare-critere').textContent())).toContain('Consommation par m²');
	expect(await valeur('consommation_m2', premier)).toBe('231 kWhEP/m²/an');
	expect(await valeur('consommation_m2', second)).toBe('158 kWhEP/m²/an');
	expect(await ecart('consommation_m2', second)).toBe(`−73 kWhEP/m²/an par rapport à DPE n° ${premier}`);
	expect(await valeur('emissions_m2', second)).toBe('12 kg CO₂ éq/m²/an');
	expect(await ecart('emissions_m2', second)).toBe(`−35 kg CO₂ éq/m²/an par rapport à DPE n° ${premier}`);
	// Total and surface served, no energy per m²: « non renseigné », never their quotient; a zero is a value.
	const manque = consommation.locator(`td[data-code="${troisieme}"]`);
	await expect(manque).toHaveAttribute('data-state', 'not_reported');
	await expect(manque).not.toContainText('kWhEP');
	expect(await valeur('emissions_m2', troisieme)).toBe('0 kg CO₂ éq/m²/an');
	// The yearly totals keep their rows, values and units.
	expect(await valeur('consommation', troisieme)).toBe('17 830 kWhEP/an');
	expect(norm(await page.locator('tr[data-cle="consommation"] th.compare-critere').textContent())).toContain("Consommation annuelle d'énergie primaire");
	expect(norm(await page.locator('tr[data-cle="emissions"] th.compare-critere').textContent())).toContain('Émissions annuelles de gaz à effet de serre');

	// The Source of the energy row opens on its definition, the focus comes back.
	const bouton = consommation.getByRole('button', { name: `Détail : Consommation par m² · DPE n° ${premier}`, exact: true });
	await bouton.click();
	const volet = page.getByRole('dialog');
	await expect(volet.getByRole('heading', { name: `Consommation par m² · DPE n° ${premier}`, exact: true })).toBeFocused();
	await expect(volet).toContainText("Consommation d'énergie primaire des cinq usages par mètre carré et par an");
	await page.keyboard.press('Escape');
	await expect(bouton).toBeFocused();

	for (const [largeur, hauteur] of [[1440, 900], [375, 812]] as const) {
		await page.setViewportSize({ width: largeur, height: hauteur });
		await consommation.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: preuveR5(`r5-documents-${largeur}-ecran.png`), animations: 'disabled' });
	}
});

// Codex review of plan 13 (IMPORTANT): « totaux en détail ». The yearly totals follow the per-m² rows in
// a closed detail level of the table, reached at the keyboard (real Tab and Enter presses from the first
// column header, never a focus given to the target), direct and designated alike.
test('R5 — les totaux annuels dans un niveau de détail fermé, atteint par Tab puis Entrée, en comparaison directe et désignée', async ({ page, request }) => {
	const [premier] = TROIS_NUMEROS;
	await configure(request, [
		comparaisonDpe(TROIS_NUMEROS, TROIS, 3),
		comparaisonDesignee(`${A}:${ANCIEN},${B}:${ACTUEL}`, DEUX_DESIGNATIONS)
	]);
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, `/comparer?dpe=${TROIS_NUMEROS.join(',')}`);
	await expect(region(page)).toBeVisible();

	const detail = page.locator('details.compare-detail');
	const resume = detail.locator('summary');
	await expect(detail).toHaveCount(1);
	await expect(resume).toHaveText('Totaux annuels');
	await expect(detail).not.toHaveAttribute('open');
	for (const cle of ['consommation_m2', 'emissions_m2']) await expect(page.locator(`tr[data-cle="${cle}"]`)).toBeVisible();
	for (const cle of ['consommation', 'emissions']) await expect(page.locator(`tr[data-cle="${cle}"]`)).toBeHidden();

	// The starting point: the header of the first column; then Tab only, Enter opens.
	await page.locator('thead th[data-code]').first().focus();
	const pas = await tabulerJusqua(page, resume, 250);
	console.log(`RELEVE r5 clavier pas_jusqu_au_resume=${pas}`);
	await page.keyboard.press('Enter');
	await expect(detail).toHaveAttribute('open', '');
	for (const cle of ['consommation', 'emissions']) await expect(page.locator(`tr[data-cle="${cle}"]`)).toBeVisible();
	expect(norm(await page.locator(`tr[data-cle="consommation"] td[data-code="${premier}"] .state-value`).textContent())).toBe('17 830 kWhEP/an');
	// The next Tab presses reach the Source of the first total; Enter opens it, Escape gives the focus back.
	const bouton = page
		.locator('tr[data-cle="consommation"]')
		.getByRole('button', { name: new RegExp(`^Détail : Consommation annuelle d'énergie primaire · DPE n°.${premier}$`) });
	await tabulerJusqua(page, bouton, 3);
	await page.keyboard.press('Enter');
	const volet = page.getByRole('dialog');
	await expect(volet.getByRole('heading', { name: new RegExp(`^Consommation annuelle d'énergie primaire · DPE n°.${premier}$`) })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(bouton).toBeFocused();
	for (const [largeur, hauteur] of [[1440, 900], [375, 812]] as const) {
		await page.setViewportSize({ width: largeur, height: hauteur });
		await resume.evaluate((element) => element.scrollIntoView({ block: 'center', behavior: 'instant' }));
		await page.screenshot({ path: preuveR5(`r5-totaux-ouverts-${largeur}-ecran.png`), animations: 'disabled' });
	}

	// The DPE designated on two addresses: the same detail level, closed, after the per-m² rows.
	await page.setViewportSize({ width: 1440, height: 900 });
	await open(page, `/comparer?adresses=${A},${B}&designes=${A}:${ANCIEN},${B}:${ACTUEL}`);
	await expect(region(page)).toBeVisible();
	await expect(detail).toHaveCount(1);
	await expect(detail).not.toHaveAttribute('open');
	await expect(page.locator('tr[data-cle="designe_consommation_m2"]')).toBeVisible();
	await expect(page.locator('tr[data-cle="designe_consommation"]')).toBeHidden();
	await resume.click();
	await expect(page.locator('tr[data-cle="designe_consommation"]')).toBeVisible();
	await expect(page.locator('tr[data-cle="designe_emissions"]')).toBeVisible();
});
