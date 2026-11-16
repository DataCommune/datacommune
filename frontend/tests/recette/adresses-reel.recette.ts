import { existsSync, readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { conclusionsTrouvees } from '../e2e/fixtures/conclusions-interdites';
import { mesurerInvariants, norm, ouvrir, REGION_COMPARATEUR } from '../e2e/fixtures/recette-adresses';
import { auditSem02, collecterReponses, lignesDuRapport } from '../e2e/fixtures/sem02-audit';
import { ORIGINE_TYPE_ATTENDUE, provenanceAttendue } from './provenance-attendue';

// Acceptance replay of phase 4 on REAL data 44/85 (plan 04-09, D-117): the seven written tasks of
// `04-UI-SPEC.md` § « Recette », headless at 375, 768 and 1 280 px, against the work base API
// (scripts/test-recette.ps1, after `workbase-recette.sh up` on a deployed SHA: plan 04-20). Outside
// the hermetic gate. Not Ianis's acceptance (D-36): it proves the path on real data before he plays
// it himself. The same tasks on the synthetic source: tests/e2e/recette-adresses.spec.ts.
//
// Real inputs are PRIVATE: `tests/recette/private/adresses-reel.json` (ignored by git, chosen by
// read-only aggregate queries on the work base, plan 04-09):
//   - `adresse_riche`: an address of 85191 with two to ten declared DPE, at least one valid, the
//     point in one parcel that carries at least one DVF sale;
//   - `adresse_b`: another address of 85191 with a declared DPE (the comparator);
//   - `adresse_sans_dpe`: an address of 85191 without any declared DPE (a measured zero);
//   - `rennes_saisie`: a typed address of Rennes (out of coverage).
// No key, address or number is written here or in a relevé; Ianis's own address is never used.
// Expected values are read from the API through the dev relay, then compared with the screen.
//
// Task 6 needs a document established before July 2021: none in the work base measured by 04-09
// (aggregate count, 2026-10-06) nor by 04-MESURE-DPE block A; set `dpe_ancien` in the private file
// when the base holds one, otherwise the task is skipped with its reason (proved on the synthetic
// source only).
//
// Rate limits of the API are real here: 120 requests per sliding minute for the workstation's IP since
// C36 (30 when these pauses were set in 04-20), the page loads and the reads of the specs together.
// Tests start at least PAS_MS apart. The SEM-02 collector first lets the window empty
// (FENETRE_VIDE_MS, longer than the 60 s window of app/rate_limit.py), then reads PAUSE_COLLECTE_MS
// apart: 24 reads a minute at most (C21, 261008-rcf; at 2 100 ms right after the previous test it met
// a 429 in 04-20 under the old 30). Kept as they are: the replay is slower than needed, never refused
// (IN-01 of 04-REVIEW-D15-RECETTE-LOTS.md).
//
// On real data a navigation may wait for an upstream (Carto GPU: 5 s timeout, then a typed outage):
// the URL assertions after an action wait NAVIGATION_MS. No content assertion has its delay changed.

const LARGEURS = [375, 768, 1280] as const;
const PAS_MS = 45_000;
const FENETRE_VIDE_MS = 61_000;
const PAUSE_COLLECTE_MS = 2_500;
const NAVIGATION_MS = 15_000;
const NB = ' ';
const KEY = process.env.PUBLIC_DC_API_KEY ?? '';
const FICHIER_PRIVE = new URL('./private/adresses-reel.json', import.meta.url);
const CRITERES_TACHE_4 = { type: 'maison', surface: '90', tolerance: '20', rayon: '1000', periode: '5' };

interface Cles {
	adresse_riche: string;
	adresse_b: string;
	adresse_sans_dpe: string;
	rennes_saisie: string;
	dpe_ancien?: string;
}

/** The private keys; an absent or incomplete file fails, never a default value. */
function cles(): Cles {
	if (!existsSync(FICHIER_PRIVE)) {
		throw new Error('Fichier privé absent : frontend/tests/recette/private/adresses-reel.json (clés BAN réelles, plan 04-09).');
	}
	const lues = JSON.parse(readFileSync(FICHIER_PRIVE, 'utf8')) as Partial<Cles>;
	for (const champ of ['adresse_riche', 'adresse_b', 'adresse_sans_dpe', 'rennes_saisie'] as const) {
		if (typeof lues[champ] !== 'string' || !lues[champ]) {
			throw new Error(`Fichier privé incomplet : champ « ${champ} » absent de frontend/tests/recette/private/adresses-reel.json.`);
		}
	}
	return lues as Cles;
}

let dernierDepart = 0;
test.beforeEach(async () => {
	cles();
	const attente = dernierDepart + PAS_MS - Date.now();
	if (dernierDepart && attente > 0) await new Promise((fin) => setTimeout(fin, attente));
	dernierDepart = Date.now();
});

/** One read of the API through the dev relay, with the session's public key. */
async function lireApi<T>(request: APIRequestContext, path: string): Promise<T> {
	const response = await request.get(path, { headers: { 'X-API-Key': KEY } });
	expect(response.status(), path.split('?')[0].replace(/\/[^/]+$/, '/…')).toBe(200);
	return (await response.json()) as T;
}

interface Selection {
	etat: string;
	motif: string | null;
	date: string | null;
	document: { numero: string; etiquette_dpe: string | null } | null;
	documents_ex_aequo: { numero: string }[];
}

const MOIS_ANNEE = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' });

/**
 * Values of marker ① since 04.3-03 (760e1a82): the served count, then, when a selection is served
 * and the count is not zero, a distinct line for the most recent document. Texts recopied from
 * `frontend/src/lib/data/adresse-fiche.ts` (lines 309, 319 and 328), each read from the served
 * selection: its month « mars 2024 » (or « Date non renseignée »), « {k} ex aequo », « Indéterminé ».
 */
function valeursRepereDpe(un: Fiche['donnees']['reperes']['dpe']): string[] {
	const valeurs = [String(un.valeur)];
	const selection = un.selection;
	if (!selection || un.valeur === 0) return valeurs;
	if (selection.etat === 'unique') {
		const iso = /^\d{4}-\d{2}-\d{2}/.exec(selection.date ?? '')?.[0];
		const jour = iso ? new Date(`${iso}T00:00:00Z`) : null;
		valeurs.push(jour && !Number.isNaN(jour.getTime()) ? MOIS_ANNEE.format(jour) : 'Date non renseignée');
	} else if (selection.etat === 'egalite') valeurs.push(`${selection.documents_ex_aequo.length} ex aequo`);
	else valeurs.push('Indéterminé');
	return valeurs;
}
interface Fiche {
	donnees: {
		identite: { libelle: string; commune: { nom: string } };
		reperes: { dpe: { etat: string; valeur: number | null; selection: Selection | null } };
	};
}
interface Documents {
	donnees: { total: number | null; documents: { numero: string; date_etablissement: string | null }[]; selection: Selection | null };
}
/** C37: the sales declared at the address, as served (only the state and the counts are read). */
interface VentesDeclarees {
	donnees: { etat: string; total: number | null; ventes: unknown[] };
}

const champUnique = (page: Page) => page.getByRole('combobox', { name: 'Commune, code postal ou adresse' });
const propositions = (page: Page) => page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
const repere = (page: Page, id: string) => page.locator(`[data-kpi="${id}"]`);
// The markers ① to ④ of the address: the strip of its own region. On real data the theme « Commune de
// l'adresse (référence) » renders a second KpiStrip lower on the page (C21).
const reperes = (page: Page) => page.getByRole('region', { name: "Repères pour comprendre l'adresse" }).locator('ol.kpi-strip');
const cartes = (page: Page) => page.locator('ol.doclist-cards > li');
const region = (page: Page) => page.getByRole('region', { name: REGION_COMPARATEUR });

async function sansConclusionInterdite(page: Page) {
	// The whole served text, closed panels, folded themes and print table included (FRONT IN-02):
	// `innerText` reads only what is rendered visible.
	const texte = await page.evaluate(() => {
		const clone = document.body.cloneNode(true) as HTMLElement;
		clone.querySelectorAll('script, style').forEach((el) => el.remove());
		return `${document.title}\n${clone.textContent ?? ''}`;
	});
	expect(conclusionsTrouvees(texte), 'conclusion interdite par 04-DPE-EVALUATION.md').toEqual([]);
}

/** The documents of the rich address, first page: the most recent first in the served order. */
async function documentsRiches(request: APIRequestContext) {
	return lireApi<Documents>(request, `/v1/adresse/${encodeURIComponent(cles().adresse_riche)}/dpe?page=1`);
}

for (const largeur of LARGEURS) {
	test.describe(`${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		test('Tâche 1 — retrouver l’adresse depuis `/` : nombre de DPE déclarés, le plus récent', async ({ page, request }) => {
			const { adresse_riche } = cles();
			const fiche = await lireApi<Fiche>(request, `/v1/adresse/${encodeURIComponent(adresse_riche)}`);
			const { libelle } = fiche.donnees.identite;
			const un = fiche.donnees.reperes.dpe;
			await ouvrir(page, '/');
			await champUnique(page).fill(libelle.split(',')[0]);
			await propositions(page).filter({ hasText: libelle.split(',')[0] }).first().click();
			await expect(page).toHaveURL(new RegExp(`/adresse/${adresse_riche}$`), { timeout: NAVIGATION_MS });

			// Every value of the marker, in order: the count, then the most recent document (04.3-03).
			await expect(repere(page, 'dpe').locator('.kpi-value .value')).toHaveText(valeursRepereDpe(un));
			const phrase = norm(await page.locator('.doclist-phrase').textContent());
			expect(phrase.startsWith(`${un.valeur} DPE déclaré`), 'phrase D-109').toBe(true);
			console.log(`RELEVE ${largeur}px tache1 dpe=${un.valeur} selection=${un.selection?.etat ?? 'aucune'} phrase_ok=true`);
			await sansConclusionInterdite(page);
		});

		test('Tâche 2 — la carte ④ porte « Référence · {commune} »', async ({ page, request }) => {
			const { adresse_riche } = cles();
			const fiche = await lireApi<Fiche>(request, `/v1/adresse/${encodeURIComponent(adresse_riche)}`);
			await ouvrir(page, `/adresse/${adresse_riche}`);
			const quatre = reperes(page).locator(':scope > li').last();
			// Overline since 04.3 (RepereReference.svelte:31), the served name of the commune.
			await expect(quatre.locator('.repere-reference-surtitre')).toHaveText(`Référence · ${fiche.donnees.identite.commune.nom}`);
			console.log(`RELEVE ${largeur}px tache2 reference_commune=visible`);
			await sansConclusionInterdite(page);
		});

		test('Tâche 3 — ouvrir le DPE le plus récent : son numéro et l’adresse qu’il déclare', async ({ page, request }) => {
			const { adresse_riche } = cles();
			const liste = await documentsRiches(request);
			const numero = liste.donnees.selection?.document?.numero ?? liste.donnees.documents[0].numero;
			const document = await lireApi<{ donnees: { adresse_declaree: { etat: string; texte: string | null } | null } }>(request, `/v1/dpe/${numero}`);
			await ouvrir(page, `/adresse/${adresse_riche}`);
			await cartes(page).locator(`a[href="/dpe/${numero}"]`).first().click();
			await page.waitForURL(`**/dpe/${numero}`, { timeout: NAVIGATION_MS });
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(`DPE n°${NB}${numero}`);
			const declaree = norm(await page.locator('[data-ligne="adresse-declaree"] dd').textContent());
			const etat = document.donnees.adresse_declaree?.etat ?? 'absente';
			if (etat === 'publiee') expect(declaree).toContain(norm(document.donnees.adresse_declaree!.texte));
			else expect(declaree).toMatch(/non affichée|Non renseigné/);
			console.log(`RELEVE ${largeur}px tache3 adresse_declaree=${etat}`);
			await sansConclusionInterdite(page);
		});

		test('Tâche 4 — ventes ressemblantes d’une maison de 90 m² à 1 km : effectif et critères', async ({ page, request }) => {
			const { adresse_riche } = cles();
			const query = new URLSearchParams({ ...CRITERES_TACHE_4, page: '1' }).toString();
			const servies = await lireApi<{ donnees: { etat: string; effectif: number | null } }>(
				request,
				`/v1/adresse/${encodeURIComponent(adresse_riche)}/ventes-ressemblantes?${query}`
			);
			await ouvrir(page, `/adresse/${adresse_riche}`);
			const formulaire = page.locator('section#ventes-ressemblantes form');
			await formulaire.getByRole('radio', { name: 'Maison' }).check();
			await formulaire.getByLabel('Surface (m²)').fill('90');
			await formulaire.getByLabel('Rayon').selectOption('1000');
			await formulaire.getByRole('button', { name: 'Afficher les ventes ressemblantes' }).click();
			await expect(page).toHaveURL(/#ventes-ressemblantes$/, { timeout: NAVIGATION_MS });
			const section = page.locator('section#ventes-ressemblantes');
			if (servies.donnees.etat === 'value') {
				const phrase = norm(await section.locator('.comparables-effectif > span').first().textContent());
				expect(phrase).toMatch(new RegExp(`^${servies.donnees.effectif} vente`));
				expect(phrase).toContain('maisons · 72 à 108 m² · 1 km');
			} else {
				// Not available or out of coverage: its served state is rendered, never a count (FRONT IN-02).
				await expect(section.locator(`[data-state="${servies.donnees.etat}"]`)).toBeVisible();
			}
			console.log(`RELEVE ${largeur}px tache4 etat=${servies.donnees.etat} effectif=${servies.donnees.effectif ?? 'null'}`);
			await sansConclusionInterdite(page);
		});

		test('Tâche 5 — comparer à une autre adresse : la commune en dernier ; désigner puis retirer un DPE', async ({ page, request }) => {
			const { adresse_riche, adresse_b } = cles();
			const liste = await documentsRiches(request);
			const numero = liste.donnees.documents[0].numero;
			const fiche = await lireApi<Fiche>(request, `/v1/adresse/${encodeURIComponent(adresse_riche)}`);
			await ouvrir(page, `/comparer?adresses=${adresse_riche},${adresse_b}`);
			await expect(region(page)).toBeVisible();
			const groupes = await page.locator('tbody').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-groupe')));
			expect(groupes.at(-1)).toBe('commune_reference');
			await expect(page.locator('tbody[data-groupe="commune_reference"] .compare-reference-phrase')).toHaveText(
				"Ces valeurs décrivent la commune, pas l'adresse."
			);
			await page.getByRole('button', { name: `Désigner un DPE pour ${fiche.donnees.identite.libelle}` }).click();
			await page.getByRole('radio', { name: new RegExp(numero) }).check();
			await page.getByRole('button', { name: 'Désigner ce DPE' }).click();
			await expect(page).toHaveURL(new RegExp(`designes=${adresse_riche}:${numero}$`), { timeout: NAVIGATION_MS });
			await sansConclusionInterdite(page);
			await page.getByRole('button', { name: `Retirer la désignation du DPE pour ${fiche.donnees.identite.libelle}` }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${adresse_riche},${adresse_b}$`), { timeout: NAVIGATION_MS });
			console.log(`RELEVE ${largeur}px tache5 groupes=${groupes.join(',')} designation=aller_retour`);
		});

		test('Tâche 6 — comparer deux DPE dont un d’avant juillet 2021 (si la base en contient)', async ({ page, request }) => {
			const { dpe_ancien } = cles();
			if (!dpe_ancien) {
				console.log(`RELEVE ${largeur}px tache6 ignoree motif=aucun_dpe_avant_juillet_2021_dans_la_base preuve=synthetique_seulement`);
				test.skip(true, 'Aucun DPE établi avant juillet 2021 dans la base de travail : tâche prouvée sur source synthétique seulement.');
				return;
			}
			const liste = await documentsRiches(request);
			const actuel = liste.donnees.documents[0].numero;
			await ouvrir(page, `/comparer?dpe=${dpe_ancien},${actuel}`);
			await expect(region(page)).toBeVisible();
			const ecart = norm(await page.locator(`tr[data-cle="classe_energie"] td[data-code="${actuel}"] .compare-ecart`).textContent());
			expect(ecart).toBe('Écart non calculé · méthodes de calcul différentes');
			console.log(`RELEVE ${largeur}px tache6 motif=methodes_differentes`);
			await sansConclusionInterdite(page);
		});

		test('Tâche 7 — une adresse à Rennes : hors couverture', async ({ page }) => {
			const { rennes_saisie } = cles();
			await ouvrir(page, '/');
			await champUnique(page).fill(rennes_saisie);
			const encart = page.getByRole('search', { name: 'Rechercher un lieu' }).locator('.cs-notice-adresses');
			// A string, not a regular expression: Playwright normalises the no-break spaces the page writes
			// (« Rennes (35) : ») only when it compares a string (C21). Title since 04.3: recherche-lieux.ts:199.
			await expect(encart.locator('.cs-notice-title')).toHaveText('Rennes (35) : adresses hors couverture');
			console.log(`RELEVE ${largeur}px tache7 encart=${norm(await encart.locator('.cs-notice-title').textContent())}`);
		});
	});
}

test('Invariants téléphone — page adresse, zéro mesuré, document, deux comparateurs (375 × 800)', async ({ page, request }) => {
	await page.setViewportSize({ width: 375, height: 800 });
	const { adresse_riche, adresse_b, adresse_sans_dpe } = cles();
	const liste = await documentsRiches(request);
	const [premier, second] = liste.donnees.documents.map((document) => document.numero);
	const ecrans: [string, string, boolean][] = [
		['adresse', `/adresse/${adresse_riche}`, false],
		['adresse_sans_dpe', `/adresse/${adresse_sans_dpe}`, false],
		['document', `/dpe/${premier}`, false],
		['comparateur_adresses', `/comparer?adresses=${adresse_riche},${adresse_b}`, true],
		['comparateur_dpe', `/comparer?dpe=${premier},${second}`, true]
	];
	const releve: string[] = [];
	for (const [nom, url, bande] of ecrans) {
		await ouvrir(page, url);
		await page.waitForLoadState('networkidle');
		const mesure = await page.evaluate(mesurerInvariants, REGION_COMPARATEUR);
		expect(mesure.scrollWidth, `${nom} : document plus large que la fenêtre`).toBeLessThanOrEqual(mesure.innerWidth);
		expect(mesure.titres, `${nom} : title interactif`).toEqual([]);
		expect(mesure.fixes, `${nom} : position fixed`).toEqual([]);
		expect(mesure.horsCadre, `${nom} : boîte hors de la fenêtre`).toEqual([]);
		let detail = '';
		if (nom === 'adresse') {
			const strip = (await reperes(page).boundingBox())!;
			const un = (await repere(page, 'dpe').boundingBox())!;
			const hauteurs = await cartes(page).evaluateAll((lis) =>
				lis.map((li) => ({ h: Math.round(li.getBoundingClientRect().height * 10) / 10, l4: li.querySelector('.doccard-l4') !== null }))
			);
			expect(strip.height, 'bande des repères').toBeLessThanOrEqual(400);
			expect(un.y + un.height, 'repère ① dans le premier écran').toBeLessThanOrEqual(800);
			for (const carte of hauteurs) expect(carte.h).toBeLessThanOrEqual(carte.l4 ? 185 : 160);
			// C37: the section « Ventes déclarées à cette adresse », under the band, as the API serves it to
			// the page: with its VEFA since 04.5-07 (C4), by number and suffix since 04.5-08 (C3).
			const declarees = await lireApi<VentesDeclarees>(request, `/v1/adresse/${encodeURIComponent(adresse_riche)}/ventes-declarees?page=1&vefa=incluses&regle=numero_et_suffixe`);
			const section = page.locator('section#ventes-adresse');
			await expect(section.getByRole('heading', { level: 2 })).toHaveText('Ventes déclarées à cette adresse');
			expect((await section.boundingBox())!.y, 'section sous la bande des repères').toBeGreaterThan(strip.y + strip.height);
			if (declarees.donnees.etat === 'value' && declarees.donnees.total === 0) {
				// C44: no sale attached, not a zero of the source.
				await expect(section.locator('.ventes-phrase')).toContainText('Aucune vente rattachée à cette adresse');
			} else if (declarees.donnees.etat === 'value') {
				await expect(section.locator('ol.ventes-cartes > li')).toHaveCount(declarees.donnees.ventes.length);
			} else {
				await expect(section.locator(`[data-state="${declarees.donnees.etat}"]`)).toBeVisible();
			}
			detail = ` reperes=${strip.height} cartes=${hauteurs.map((c) => c.h).join('/')} ventes_declarees=${declarees.donnees.etat}:${declarees.donnees.total ?? '-'}`;
		}
		if (nom === 'adresse_sans_dpe') {
			// A measured zero is said as such, never an outage nor « hors couverture ».
			await expect(page.locator('.doclist-phrase')).toContainText('Aucun DPE déclaré à cette adresse');
			await expect(repere(page, 'dpe').locator('.kpi-value .value')).toHaveText('0');
		}
		if (bande) {
			await page.evaluate(() => window.scrollTo(0, window.scrollY + document.querySelector('thead')!.getBoundingClientRect().bottom + 120));
			const hauteur = (await page.locator('.compare-bande').boundingBox())!.height;
			expect(hauteur, `${nom} : bande > un cinquième`).toBeLessThanOrEqual(800 / 5);
			detail = ` bande=${hauteur}`;
		}
		await sansConclusionInterdite(page);
		releve.push(`${nom}:scroll=${mesure.scrollWidth}${detail}`);
		await new Promise((fin) => setTimeout(fin, 10_000));
	}
	console.log(`RELEVE invariants 375px ${releve.join(' ')}`);
});

/** C40/C41: the served address comparison, as the API serves it (only states, gaps and verdicts read). */
interface ComparaisonAdresses {
	donnees: {
		groupes: {
			cle: string;
			lignes: { cle: string; cellules: { ban_id: string; etat: string; motif: string | null; valeur: unknown; ecart: unknown; identique_a_la_reference: boolean | null }[] }[];
		}[];
	};
}

test('C38 à C41 — ordre des ventes, DPE cochés puis comparés, préremplissage, ligne déclarée et « Identique » au comparateur (375 × 800)', async ({
	page,
	request
}) => {
	await page.setViewportSize({ width: 375, height: 800 });
	const { adresse_riche, adresse_b } = cles();
	const liste = await documentsRiches(request);
	await ouvrir(page, `/adresse/${adresse_riche}`);
	await page.waitForLoadState('networkidle');

	// C38: the sales of the parcel, then those declared at the address, then the similar sales.
	const ordre = await page.evaluate(() =>
		[...document.querySelectorAll('section#ventes, section#ventes-adresse, section#ventes-ressemblantes')].map((section) => section.id)
	);
	expect(ordre).toEqual(['ventes', 'ventes-adresse', 'ventes-ressemblantes']);

	// C42: a link without criteria is prefilled from the documents (or not), the visitor clicks.
	const formulaire = page.locator('section#ventes-ressemblantes form');
	const coche = (await formulaire.getByRole('radio', { name: 'Maison' }).isChecked())
		? 'maison'
		: (await formulaire.getByRole('radio', { name: 'Appartement' }).isChecked())
			? 'appartement'
			: 'aucun';
	const surface = (await formulaire.getByLabel('Surface (m²)').inputValue()) ? 'remplie' : 'vide';
	const origine = (await formulaire.locator('.comparables-origine').count()) ? 'dite' : 'absente';
	expect(origine === 'dite', 'ligne d’origine si et seulement si un critère est prérempli').toBe(coche !== 'aucun' || surface === 'remplie');
	// C42 (WINDOWS 113), UI-007 (04.5-06): the visible line says where each prefilled criterion comes
	// from — the type from the DPE of the address, the surface from the document of the served
	// selection, its number linked to its page and followed by the served qualification of that
	// selection when it is not a valid document. Read on the form itself and checked against the
	// served list, so it stays deterministic whatever the real data prefills. The full text is compared
	// to the one expected for the served reason (provenance-attendue.ts, written apart from the
	// application; review of 04.5-06, MINOR): a replaced qualification fails.
	const ligneOrigine = formulaire.locator('.comparables-origine');
	if (surface === 'remplie') {
		const selection = liste.donnees.selection;
		expect(selection?.etat, 'surface préremplie depuis la sélection servie').toBe('unique');
		const numero = selection!.document!.numero;
		const lien = ligneOrigine.locator('a');
		await expect(lien).toHaveCount(1);
		expect(norm(await lien.textContent())).toBe(`Surface issue du DPE n° ${numero}`);
		await expect(lien).toHaveAttribute('href', `/dpe/${encodeURIComponent(numero)}`);
		const lu = norm(await ligneOrigine.textContent());
		expect(lu, `qualification servie (${selection!.motif})`).toBe(provenanceAttendue(numero, selection!.motif ?? 'motif_non_servi', coche !== 'aucun'));
	} else if (coche !== 'aucun') await expect(ligneOrigine).toHaveText(ORIGINE_TYPE_ATTENDUE);
	else await expect(ligneOrigine).toHaveCount(0);
	await expect(page.locator('section#ventes-ressemblantes .comparables-effectif')).toHaveCount(0);

	// C39: from two documents, one box per card; two checked open the document comparator.
	let comparaisonDocuments = 'sans_objet';
	const total = liste.donnees.total ?? 0;
	if (total >= 2) {
		const [premier, second] = liste.donnees.documents.map((document) => document.numero);
		await expect(cartes(page).locator('input[type="checkbox"]')).toHaveCount(liste.donnees.documents.length);
		await page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${premier}` }).check();
		await page.getByRole('checkbox', { name: `Sélectionner le DPE n° ${second}` }).check();
		const bouton = page.getByRole('link', { name: 'Comparer les documents (2)' });
		await expect(bouton).toHaveAttribute('href', `/comparer?dpe=${premier},${second}`);
		await bouton.click();
		await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${premier},${second}$`), { timeout: NAVIGATION_MS });
		await expect(region(page)).toBeVisible();
		await expect(page.locator('thead th[data-code]')).toHaveCount(2);
		comparaisonDocuments = 'deux_colonnes';
	}

	// C40, C41: the address comparator, read from the API then compared with the screen.
	const servie = await lireApi<ComparaisonAdresses>(request, `/v1/adresses/comparaison?ids=${adresse_riche},${adresse_b}`);
	await ouvrir(page, `/comparer?adresses=${adresse_riche},${adresse_b}`);
	await expect(region(page)).toBeVisible();
	const ventes = servie.donnees.groupes.find((groupe) => groupe.cle === 'ventes')!;
	expect(ventes.lignes.map((ligne) => ligne.cle)).toEqual(['ventes_parcelle', 'derniere_vente', 'ventes_declarees']);
	expect(
		await page.locator('tbody[data-groupe="ventes"] tr[data-cle]').evaluateAll((rows) => rows.map((row) => row.getAttribute('data-cle')))
	).toEqual(['ventes_parcelle', 'derniere_vente', 'ventes_declarees']);
	const declarees = ventes.lignes.find((ligne) => ligne.cle === 'ventes_declarees')!;
	for (const [position, cle] of [adresse_riche, adresse_b].entries()) {
		const route = await lireApi<VentesDeclarees>(request, `/v1/adresse/${encodeURIComponent(cle)}/ventes-declarees?page=1&regle=numero_et_suffixe`);
		const cellule = declarees.cellules[position];
		if (route.donnees.etat === 'value' && route.donnees.total === 0) {
			// C44 (CR-02): the row says no sale attached, never the figure 0 as a fact.
			expect([cellule.etat, cellule.motif, cellule.valeur ?? null], 'aucune vente rattachée').toEqual([
				'not_available',
				'aucune_vente_rattachee',
				null
			]);
			const td = page.locator(`tr[data-cle="ventes_declarees"] td[data-code="${cellule.ban_id}"]`);
			await expect(td.locator('.state-badge')).toHaveText('Aucune vente rattachée');
		} else {
			expect([cellule.etat, cellule.valeur ?? null], 'même compte que la section de la page').toEqual([route.donnees.etat, route.donnees.total]);
		}
	}
	const identiques: string[] = [];
	for (const groupe of servie.donnees.groupes) {
		for (const ligne of groupe.lignes) {
			const cellule = ligne.cellules[1];
			if (!cellule.ecart || cellule.identique_a_la_reference !== true) continue;
			identiques.push(ligne.cle);
			const texte = norm(await page.locator(`tr[data-cle="${ligne.cle}"] td[data-code="${cellule.ban_id}"] .compare-ecart`).textContent());
			expect(texte, `${ligne.cle} : deux valeurs égales`).toBe('Identique');
		}
	}
	console.log(
		`RELEVE c38-c41 375px ordre=ok prerempli_type=${coche} prerempli_surface=${surface} origine=${origine} ` +
			`dpe_coches=${comparaisonDocuments} ventes_declarees=${declarees.cellules.map((c) => `${c.etat}:${c.valeur ?? '-'}`).join('/')} ` +
			`identiques_numeriques=${identiques.length}`
	);
	await sansConclusionInterdite(page);
});

test('Audit SEM-02 — toutes les feuilles publiques des clés réelles (fiches, listes, comparateurs)', async ({ request }) => {
	test.setTimeout(420_000);
	const { adresse_riche, adresse_b } = cles();
	// The sliding window of the rate limiter is emptied first: no read of a previous test counts.
	await new Promise((fin) => setTimeout(fin, FENETRE_VIDE_MS));
	const liste = await documentsRiches(request);
	const [premier, second] = liste.donnees.documents.map((document) => document.numero);
	const lues = await collecterReponses(
		request,
		'',
		{
			banId: adresse_riche,
			criteres: CRITERES_TACHE_4,
			numero: premier,
			idsComparaison: [adresse_riche, adresse_b],
			designes: `${adresse_riche}:${premier}`,
			numerosComparaison: [premier, second]
		},
		{ 'X-API-Key': KEY },
		PAUSE_COLLECTE_MS
	);
	// Real data: the known gaps are reported, not required (`signaler`).
	const rapport = auditSem02(lues, 'signaler');
	// Paths, fields and counts only: no key, address or number (R06-09/8).
	console.log(`RELEVE sem02 reel reponses=${lues.length} ok=${rapport.ok}`);
	for (const ligne of lignesDuRapport(rapport, 'reel')) console.log(ligne);
	expect(rapport.echecs).toEqual([]);
});
