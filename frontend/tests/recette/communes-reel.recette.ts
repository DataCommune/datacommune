import { getExplication, type CoveredFieldId } from '../../src/lib/data/explications';
import type { RechercheAdresses, ResultatRechercheAdresse } from '../../src/lib/api/adresses';
import type { CommuneSearchResult } from '../../src/lib/api/communes';
import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { textesVisiblesDeLaBande } from '../e2e/fixtures/bande';

// Acceptance replay of phase 3 on REAL data 44/85 (plan 03-13, D-53): the four written tasks of
// 03-RECETTE.md (internal tracking), headless at 375, 768 and 1280 px,
// against the work base API (scripts/test-recette.ps1). Outside the hermetic gate. This is not
// Ianis's acceptance (D-36): it proves the path on real data before he plays it himself.
//
// Since plan 03-14 (R1 closed by route A) the commune referential of the work base covers the
// whole of France while the commune data stay bounded to 44/85. Real inputs:
//   - « Saint-Philbert » has real homonyms: 44188 Saint-Philbert-de-Grand-Lieu (44310) and 85262
//     Saint-Philbert-de-Bouaine (85660), hosted, plus communes of other departements proposed
//     with their « Hors couverture » badge. Their codes are read from the served search, never
//     written here beyond the target and its hosted homonym;
//   - 79210, Le Pin (Deux-Sèvres), is known and out of coverage: its fiche is the typed « hors
//     couverture » state and the comparator keeps its column « Hors couverture »;
//   - 79211 names no commune of the producer: it stays unknown (« inconnu » is kept for codes that
//     do not exist);
//   - postal codes (03-15, CR-01): 33000 (Bordeaux, departement not hosted) is served out of
//     coverage and rendered as a typed state, never a zero; 85660 proposes the target;
//   - empty served lists (03-15, CR-02): the real comparison never serves an empty list as a value,
//     and a commune serving `modes: []` renders « Sans base de calcul » (when observed).
// Since 04-17 `/` has the single field « Commune, code postal ou adresse » (C21, 261008-rcf): it
// sends the communes search (limit=8) and the address search (limite=8) together and lists at most
// 8 options composed in the served order (the head type up to 5, the other up to 3, the remainder
// filled), then « Voir tous les résultats pour « … » ». The listed options are read from the screen
// and checked against rules written here, from both served answers (WR-02 of
// 04-REVIEW-D15-RECETTE-LOTS.md): never against `composerOptions`, the function the page composes
// them with. Each address option is compared field by field with the address served at its rank —
// number, suffix, street and place (A2 of the GPT review of 2026-10-11, C52) — by the rule written in
// `comparerOptionAdresse`, never by the page's composer. If the target is not listed, the path is
// written here: the departement filter of the
// field, kept while typing (D-43). The comparator keeps its communes-only field « Rechercher une
// commune ».
// Expected values are read from the API through the dev relay, then compared with the screen:
// the same data gives the same relevé (idempotence, UI-02).
//
// Rate limits of the API are real here (120 requests per minute per IP since C36; the pauses below
// date from the 30 of 04-20 and are kept, the replay only gets slower than needed, IN-01): tests start
// at least PAS_MS apart, a pause no fixture can replace. This file runs right after `adresses-reel`, whose last
// test (the SEM-02 audit) leaves the sliding window of the anonymous key almost full: before its first
// test the window is emptied (FENETRE_VIDE_MS, the 60 s `_WINDOW_SECONDS` of app/rate_limit.py plus
// one second; C27, WINDOWS n° 88). No ceiling and no assertion is changed.

const LARGEURS = [375, 768, 1280] as const;
const PAS_MS = 45_000;
const FENETRE_VIDE_MS = 61_000;
const REGION = 'Tableau de comparaison';

const RECHERCHE = 'Saint-Philbert';
const CIBLE = { code: '85262', nom: 'Saint-Philbert-de-Bouaine', departement: 'Vendée (85)', codePostal: '85660' };
const HOMONYME = { code: '44188', nom: 'Saint-Philbert-de-Grand-Lieu', departement: 'Loire-Atlantique (44)', codePostal: '44310' };
const HORS_PERIMETRE = '79210';
const INEXISTANT = '79211';
const HEBERGES = ['44', '85'];
const CODE_POSTAL_HORS = '33000';
const CODE_POSTAL_CIBLE = CIBLE.codePostal;
// Texts since 04.3: recherche-code-postal.ts:23, etats-libelles.ts:60 (phrase of the served reason
// `territoire_hors_perimetre`), commune/[code]/+page.svelte:264, datasets/+page.svelte:159.
const TITRE_CODE_POSTAL_HORS_COUVERTURE = 'Code postal hors couverture';
const PHRASE_HORS_PERIMETRE = 'Ce site ne couvre pas ce département.';
const TITRE_COMMUNE_HORS_COUVERTURE = 'Commune hors couverture';
const REGION_COUVERTURE = 'Couverture';
const CLE_MODES = 'indicateurs_dpe.resultat.modes';
const MAX_OPTIONS = 8;
const CHAMP_PRIX_MAISONS = 'dvf.prix_m2_median_maison';

const KEY = process.env.PUBLIC_DC_API_KEY ?? '';
// Public definition of a field, read from `EXPLICATIONS` as the source panel renders it (03-17 F66),
// never the technical description of the contract.
const definition = (fieldId: CoveredFieldId) => getExplication(fieldId).definition;

let dernierDepart = 0;
test.beforeEach(async ({}, testInfo) => {
	// First test of the file: the window of the previous file's reads is emptied, inside a delay
	// extended by as much (the content delays stay as they are).
	const attente = dernierDepart ? dernierDepart + PAS_MS - Date.now() : FENETRE_VIDE_MS;
	if (!dernierDepart) testInfo.setTimeout(testInfo.timeout + FENETRE_VIDE_MS);
	if (attente > 0) await new Promise((fin) => setTimeout(fin, attente));
	dernierDepart = Date.now();
});

/** Navigates and waits for hydration (the root layout reads the session once hydrated). */
async function ouvrir(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
}

/** One read of the API through the dev relay, with the session's public key. */
async function lireApi<T>(request: APIRequestContext, path: string): Promise<T> {
	const response = await request.get(path, { headers: { 'X-API-Key': KEY } });
	expect(response.status(), path).toBe(200);
	return (await response.json()) as T;
}

interface LigneCommune {
	prix_m2_median_maison: number | null;
	nb_mutations_12m_maison: number | null;
	// Inclusive window of the served median (04.3-01, ad6a0a03), rendered under marker ①.
	fenetre_mediane_dvf: string | null;
	fibre_pct: number | null;
}

interface ResultatRecherche {
	code_insee: string;
	nom: string;
	departement: string;
	code_postal: string | null;
	hebergement: { etat: string; motif: string | null };
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const chiffres = (value: string | null) => (value ?? '').replace(/\s/g, '');
const norm = (value: string | null | undefined) => value?.replace(/\s+/g, ' ').trim() ?? null;
// The comparator's communes-only field.
const champ = (page: Page) => page.getByRole('combobox', { name: 'Rechercher une commune' });
const propositions = (page: Page) => page.getByRole('listbox', { name: 'Communes proposées' }).getByRole('option');
// The single field of `/` (04-17).
const champUnique = (page: Page) => page.getByRole('combobox', { name: 'Commune, code postal ou adresse' });
const propositionsLieux = (page: Page) => page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
// Its commune options only: an address option also names a departement and a postal code.
const communesProposees = (page: Page) =>
	propositionsLieux(page).filter({ has: page.locator('.cs-type', { hasText: /^Commune$/ }) });
const formulaire = (page: Page) => page.getByRole('search', { name: 'Rechercher un lieu' });
const region = (page: Page) => page.getByRole('region', { name: REGION });
const entetes = (page: Page) =>
	page.locator('thead th[data-code]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-code')!));

/**
 * One action on an `i` button opens its source panel, focus on its title; Escape gives it back.
 * A value the instance does not publish (real data: the dated DVF trend) has a definition and its
 * reason, but no source to name: `publiee: false`.
 */
async function voletEnUneAction(page: Page, bouton: Locator, publiee = true) {
	const nom = (await bouton.getAttribute('aria-label'))!.replace(/^Source et définition : /, '');
	await expect(bouton).not.toHaveAttribute('title');
	await bouton.click();
	const volet = page.getByRole('dialog', { name: nom });
	await expect(volet).toBeVisible();
	await expect(volet.getByRole('heading', { name: nom })).toBeFocused();
	await expect(volet.locator('dt', { hasText: /^Définition$/ })).toBeVisible();
	if (publiee) await expect(volet.locator('dt', { hasText: /^Source$/ })).toBeVisible();
	else await expect(volet).toContainText('Non publiée');
	await page.keyboard.press('Escape');
	await expect(volet).toBeHidden();
	await expect(bouton).toBeFocused();
}

/** One listed address option, as read from the screen (texts already whitespace-normalised). */
interface OptionAdresseLue {
	nom: string;
	meta: string;
}

/**
 * Field-by-field oracle of one address option against the address served at its rank (A2, C52),
 * written from 04-UI-SPEC.md (« Option adresse » and « Voie sans numéro (D-107) »), never from the
 * page's composer:
 *   - line 1 is « {numéro}{indice} {voie} »: when a number is served, the line starts with it, the
 *     suffix (`rep`, glued to the number, D-107) follows, then ONE space and the served street; a
 *     null street leaves nothing after the number (strict equality, never a `toContain('')`);
 *     without a served number, line 1 is exactly the street;
 *   - line 2, split on « · », is in this order: « {CP} {Commune} » (the served parts), the
 *     departement « {nom} ({code}) » or « Département {code} » without a served name, then
 *     « Précisez le numéro » exactly when the result is a street (`type_resultat === 'voie'`).
 * Returns, per field, what is read and what the rule expects.
 */
function comparerOptionAdresse(r: ResultatRechercheAdresse, option: OptionAdresseLue) {
	const normaliser = (texte: string) => texte.replace(/\s+/g, ' ').trim();
	const numero = normaliser(r.numero ?? '');
	const suffixe = numero ? normaliser(r.rep ?? '') : '';
	const voie = normaliser(r.nom_voie ?? '');
	const reste = option.nom.slice(numero.length + suffixe.length);
	const commune = [r.code_postal ?? '', r.nom_commune ?? ''].filter(Boolean).join(' ');
	const departement = r.departement
		? r.departement.nom
			? `${r.departement.nom} (${r.departement.code})`
			: `Département ${r.departement.code}`
		: '';
	return {
		numero: { lu: option.nom.slice(0, numero.length), attendu: numero },
		suffixe: { lu: option.nom.slice(numero.length, numero.length + suffixe.length), attendu: suffixe },
		voie: { lu: reste, attendu: numero ? (voie ? ` ${voie}` : '') : voie },
		lieu: {
			lu: option.meta ? option.meta.split('·').map(normaliser) : [],
			attendu: [commune, normaliser(departement), r.type_resultat === 'voie' ? 'Précisez le numéro' : ''].filter(Boolean)
		}
	};
}

/** Task 1 on real data: from the search to the fiche of the right « Saint-Philbert ». */
async function trouverParmiLesHomonymes(page: Page, request: APIRequestContext) {
	const q = encodeURIComponent(RECHERCHE);
	const lire = async (limit: number) =>
		(await lireApi<{ donnees: CommuneSearchResult[] }>(request, `/v1/communes/search?q=${q}&limit=${limit}`)).donnees;
	const servis = await lire(MAX_OPTIONS);
	const tous = await lire(50);
	// The address half of the single field, same query and limit as the field sends.
	const adresses = (await lireApi<{ donnees: RechercheAdresses }>(request, `/v1/adresses/recherche?q=${q}&limite=${MAX_OPTIONS}`))
		.donnees;
	const homonymes = servis.filter((r) => r.nom.startsWith(RECHERCHE));
	const horsCouverture = servis.filter((r) => !HEBERGES.includes(r.departement));
	// Real homonyms, at least one of another departement than 44 and 85, served out of coverage.
	expect(homonymes.length, 'homonymes servis').toBeGreaterThanOrEqual(2);
	expect(horsCouverture.length, 'homonymes hors couverture').toBeGreaterThanOrEqual(1);
	for (const r of horsCouverture) expect(r.hebergement.etat, r.code_insee).toBe('hors_couverture');

	await ouvrir(page, '/');
	await champUnique(page).fill(RECHERCHE);
	// Rules of the field (04-17, C21), written here (WR-02): at most MAX_OPTIONS options, the remainder
	// filled, so as many as both answers serve up to that limit, then « Voir tous » last.
	const attendues = Math.min(MAX_OPTIONS, servis.length + adresses.resultats.length);
	await expect(propositionsLieux(page)).toHaveCount(attendues + 1);
	await expect(propositionsLieux(page).last()).toHaveText(`Voir tous les résultats pour « ${RECHERCHE} »`);
	const lues = (
		await propositionsLieux(page).evaluateAll((noeuds) =>
			noeuds.map((noeud) => ({
				type: noeud.querySelector('.cs-type')?.textContent?.trim() ?? '',
				nom: noeud.querySelector('.cs-option-name')?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
				meta: noeud.querySelector('.cs-option-meta')?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
			}))
		)
	).slice(0, -1);
	// Since 04.3 an address option is typed « Voie » when the served result is a street, « Adresse »
	// otherwise (CommuneSearch.svelte:621); both belong to the address half of the field.
	const communesLues = lues.filter((option) => option.type === 'Commune');
	const adressesLues = lues.filter((option) => option.type === 'Adresse' || option.type === 'Voie');
	const moitie = (type: string) => (type === 'Commune' ? 'commune' : 'adresse');
	const tete = adresses.ordre_types[0] === 'adresse' ? 'adresse' : 'commune';
	expect(communesLues.length + adressesLues.length, 'options typées').toBe(lues.length);
	// The head type of the served order first, each type in its served order (a prefix of its answer).
	expect(lues.map((option) => moitie(option.type)), 'type de tête en premier').toEqual(
		[...lues.filter((option) => moitie(option.type) === tete), ...lues.filter((option) => moitie(option.type) !== tete)].map((option) =>
			moitie(option.type)
		)
	);
	expect(communesLues.map((option) => option.nom), 'communes dans l’ordre servi').toEqual(
		servis.slice(0, communesLues.length).map((r) => r.nom)
	);
	// Each listed address option against the address served at the same rank (A2, C52).
	adresses.resultats.slice(0, adressesLues.length).forEach((r, index) => {
		const champs = comparerOptionAdresse(r, adressesLues[index]);
		const rang = `option adresse ${index + 1}`;
		expect(adressesLues[index].type, `type, ${rang}`).toBe(r.type_resultat === 'voie' ? 'Voie' : 'Adresse');
		expect(champs.numero.lu, `numéro, ${rang}`).toBe(champs.numero.attendu);
		expect(champs.suffixe.lu, `suffixe, ${rang}`).toBe(champs.suffixe.attendu);
		expect(champs.voie.lu, `voie, ${rang}`).toBe(champs.voie.attendu);
		expect(champs.lieu.lu, `lieu, ${rang}`).toEqual(champs.lieu.attendu);
	});
	// Quotas: the head type keeps up to 5 options, the other up to 3, whatever the remainder.
	const quota = (type: string) => (type === tete ? 5 : 3);
	expect(communesLues.length, 'quota des communes').toBeGreaterThanOrEqual(Math.min(quota('commune'), servis.length));
	expect(adressesLues.length, 'quota des adresses').toBeGreaterThanOrEqual(Math.min(quota('adresse'), adresses.resultats.length));

	const listees = servis.slice(0, communesLues.length);
	const horsCouvertureListees = listees.filter((r) => !HEBERGES.includes(r.departement));
	const direct = listees.some((r) => r.code_insee === CIBLE.code);
	const chemin = direct ? 'propositions_directes' : 'filtre_departement_85';
	console.log(
		`RELEVE homonymes q=${RECHERCHE} limite_effective=${MAX_OPTIONS} servis_limit8=${servis.length} ` +
			`servis_limit50=${tous.length} adresses_servies=${adresses.resultats.length} ordre=${adresses.ordre_types.join(',')} ` +
			`options=${lues.length} communes_listees=${communesLues.length} chemin=${chemin} ` +
			`proposes=${servis.map((r) => `${r.code_insee}:${r.hebergement.etat}`).join(',')} ` +
			`hors_couverture=${horsCouverture.map((r) => `${r.code_insee}:${r.departement}:${r.hebergement.motif}`).join(',')}`
	);
	if (direct) {
		// The listed homonyms out of coverage stay proposed with their badge (at least one listed).
		expect(horsCouvertureListees.length, 'homonymes hors couverture listés').toBeGreaterThanOrEqual(1);
		for (const r of horsCouvertureListees) {
			const option = communesProposees(page)
				.filter({ has: page.locator('.cs-option-name', { hasText: new RegExp(`^${escapeRegExp(r.nom)}$`) }) })
				.filter({ hasText: `(${r.departement})` });
			await expect(option).toHaveCount(1);
			await expect(option).toContainText('Hors couverture');
		}
		const autre = communesProposees(page).filter({ hasText: HOMONYME.departement });
		await expect(autre).toContainText(HOMONYME.nom);
		await expect(autre).toContainText(HOMONYME.codePostal);
		await expect(autre).not.toContainText('Hors couverture');
	} else {
		// Fallback of the contract: the departement filter is kept while typing (D-43).
		await page.getByLabel('Département').selectOption('85');
		await champUnique(page).fill(RECHERCHE);
	}
	const cible = communesProposees(page).filter({ hasText: CIBLE.departement }).filter({ hasText: CIBLE.codePostal });
	await expect(cible).toHaveCount(1);
	await expect(cible).toContainText(CIBLE.nom);
	await expect(cible).not.toContainText('Hors couverture');
	await cible.click();

	await expect(page).toHaveURL(new RegExp(`/commune/${CIBLE.code}$`));
	await expect(page.getByRole('heading', { level: 1, name: CIBLE.nom })).toBeVisible();
	await expect(page.locator('.fiche-identity')).toContainText(`${CIBLE.departement} · INSEE ${CIBLE.code}`);
}

/** Task 2 on real data: marker ① against the served row, then its source panel in one action. */
async function lirePrixEtSource(page: Page, ligne: LigneCommune) {
	const kpi = page.locator('[data-kpi="prix-maisons"]');
	await expect(kpi.getByRole('heading', { name: 'Prix médian des maisons' })).toBeVisible();
	expect(ligne.prix_m2_median_maison, 'prix médian servi').not.toBeNull();
	expect(chiffres(await kpi.locator('.state-value .value').textContent())).toBe(String(ligne.prix_m2_median_maison));
	await expect(kpi.locator('.state-value .unit')).toHaveText('€/m²');
	// One short line under the value, no source on the card (03-17 R-1): the served size, then the
	// served window of the median (04.3-01, commune-kpi.ts:330).
	expect(ligne.fenetre_mediane_dvf, 'fenêtre servie de la médiane').not.toBeNull();
	await expect(kpi.locator('.kpi-line .kpi-meta')).toHaveText(`${ligne.nb_mutations_12m_maison} ventes · ${ligne.fenetre_mediane_dvf}`);
	await expect(kpi.locator('.kpi-source')).toHaveCount(0);
	if (page.viewportSize()?.width === 375) {
		const strip = (await page.locator('ol.kpi-strip').boundingBox())!.height;
		const moitie = await page.evaluate(() => window.innerHeight / 2);
		console.log(`RELEVE 375px reperes=${strip} plafond=${moitie}`);
		expect(strip, 'bande des repères').toBeLessThanOrEqual(moitie);
	}
	console.log(`RELEVE ${CIBLE.code} prix_m2_median_maison=${ligne.prix_m2_median_maison} nb_mutations_12m_maison=${ligne.nb_mutations_12m_maison}`);

	const bouton = kpi.getByRole('button', { name: 'Source et définition : Prix médian des maisons' });
	await bouton.click();
	const volet = page.getByRole('dialog', { name: 'Prix médian des maisons' });
	await expect(volet.getByRole('heading', { name: 'Prix médian des maisons' })).toBeFocused();
	const entree = (terme: string) =>
		volet.locator('.source-panel-entry', { has: page.locator('dt', { hasText: new RegExp(`^${terme}$`) }) }).locator('dd');
	await expect(entree('Définition')).toHaveText(definition(CHAMP_PRIX_MAISONS));
	await expect(entree('Période')).toHaveText(ligne.fenetre_mediane_dvf!);
	// The source is read in the panel only (03-17 K12: accented at the source, R-1: off the card).
	await expect(entree('Source')).toHaveText('Demandes de valeurs foncières (DVF)');
	await page.keyboard.press('Escape');
	await expect(volet).toBeHidden();
	await expect(bouton).toBeFocused();
}

test('Tracer — « Saint-Philbert » jusqu’à la fiche, repère ① et son volet, fibre hébergée (375 px)', async ({
	page,
	request
}) => {
	await page.setViewportSize({ width: 375, height: 800 });
	const reponse = await lireApi<{ donnees: LigneCommune[] }>(request, `/v1/commune?code_insee=${CIBLE.code}`);
	await trouverParmiLesHomonymes(page, request);
	await lirePrixEtSource(page, reponse.donnees[0]);

	// On a phone the site header takes a fifth of the window at most (03-17 H-01).
	const enTete = (await page.locator('header.site-header').boundingBox())!.height;
	const plafondEnTete = await page.evaluate(() => window.innerHeight / 5);
	console.log(`RELEVE 375px entete_site=${enTete} plafond=${plafondEnTete}`);
	expect(enTete, 'en-tête du site').toBeLessThanOrEqual(plafondEnTete);

	// connectivite is hosted since plan 02-22: the served percentage, or a typed absence
	// (not_reported when the commune has no building), never out of coverage, never 0 nor blank.
	// Under 1081 px the category « Logement et DPE » is a closed fold: the reader opens it to read its fibre row.
	const logement = page.locator('details.cat#q-logement');
	if (!(await logement.evaluate((node: HTMLDetailsElement) => node.open))) await logement.locator(':scope > summary').click();
	const theme = page.locator('#critere-fibre');
	const fibre = theme.locator('.tab-l', { hasText: 'Fibre (immeubles observés)' });
	await expect(fibre).not.toContainText('Hors couverture');
	await expect(fibre).not.toContainText('jeu non installé');
	const fibrePct = reponse.donnees[0].fibre_pct;
	if (fibrePct == null) {
		expect(await fibre.innerText()).not.toMatch(/\d/);
	} else {
		await expect(fibre).toContainText(new RegExp(`${String(fibrePct).replace('.', ',')}\\s?%`));
	}
	console.log(`RELEVE ${CIBLE.code} fibre_pct=${fibrePct}`);
});

for (const largeur of LARGEURS) {
	test.describe(`${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		test('Tâche 1 — homonymes : de la recherche à la fiche de la bonne commune', async ({ page, request }) => {
			await trouverParmiLesHomonymes(page, request);
		});

		test('Tâche 2 — prix médian des maisons, son effectif, sa fenêtre et sa source', async ({ page, request }) => {
			const reponse = await lireApi<{ donnees: LigneCommune[] }>(request, `/v1/commune?code_insee=${CIBLE.code}`);
			await ouvrir(page, `/commune/${CIBLE.code}`);
			await lirePrixEtSource(page, reponse.donnees[0]);
			// Every value met by the task (the four markers of the synthesis) opens its panel in one action.
			const reperes = page.locator('[data-kpi]');
			await expect(reperes).toHaveCount(4);
			for (const repere of await reperes.all()) {
				const publiee = !(await repere.innerText()).includes('Non publiée');
				await voletEnUneAction(page, repere.locator('.source-button'), publiee);
			}
		});

		test('Tâche 3 — comparer depuis la fiche, puis une commune hors périmètre de l’instance', async ({ page, request }) => {
			await ouvrir(page, `/commune/${CIBLE.code}`);
			await page.getByRole('link', { name: 'Comparer avec une autre commune' }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?communes=${CIBLE.code}$`));

			const servie = page.waitForResponse((response) => new URL(response.url()).pathname === '/v1/communes/comparaison');
			await page.getByRole('button', { name: 'Ajouter une commune' }).click();
			await expect(champ(page)).toBeFocused();
			await champ(page).fill('Saint-Philbert-de-Grand');
			await propositions(page).filter({ hasText: HOMONYME.nom }).click();
			await expect(page.getByRole('status').filter({ hasText: 'ajoutée' })).toContainText(
				`${HOMONYME.nom} ajoutée à la comparaison`
			);
			const comparaison = (await (await servie).json()) as {
				donnees: {
					criteres: {
						cle: string;
						field_id: string;
						cellules: { code_insee: string; ecart: { valeur: number } | null; identique_a_la_reference: boolean | null }[];
					}[];
				};
			};
			await expect(region(page)).toBeVisible();
			await expect.poll(() => entetes(page)).toEqual([CIBLE.code, HOMONYME.code]);
			// Two hosted communes: the column header row takes a fifth of the window at most at 375 px.
			const enteteHebergees = (await page.locator('thead tr').boundingBox())!.height;
			const plafondHebergees = await page.evaluate(() => window.innerHeight / 5);
			console.log(
				`RELEVE ${largeur}px entete_colonnes_hebergees=${enteteHebergees} colonnes=2 plafond=${plafondHebergees}`
			);
			if (largeur === 375) expect(enteteHebergees, 'thead sans colonne hors couverture').toBeLessThanOrEqual(plafondHebergees);

			// The served gap on the median house price, recopied, relative to the first commune.
			const critere = comparaison.donnees.criteres.find((un) => un.field_id === CHAMP_PRIX_MAISONS)!;
			const servieHomonyme = critere.cellules.find((cellule) => cellule.code_insee === HOMONYME.code)!;
			const ecart = servieHomonyme.ecart!;
			const signe = ecart.valeur > 0 ? '+' : '';
			const cellule = page.locator(`tr[data-cle="${critere.cle}"] td[data-code="${HOMONYME.code}"] .compare-ecart`);
			// C41: two values served as the same read « Identique », never « 0 €/m² par rapport à … ».
			expect(chiffres(norm(await cellule.textContent()))).toBe(
				servieHomonyme.identique_a_la_reference === true ? 'Identique' : chiffres(`${signe}${ecart.valeur} €/m² par rapport à ${CIBLE.nom}`)
			);

			// A commune outside the instance, known to the referential (R1 route A, 03-14): its column
			// is kept with its reason, read on screen and in the served comparison; no « Lien altéré ».
			const trois = `${CIBLE.code},${HOMONYME.code},${HORS_PERIMETRE}`;
			const servie3 = await lireApi<{
				donnees: {
					codes_inconnus: string[];
					communes: { code_insee: string; hebergement: { etat: string; motif: string | null } }[];
					criteres: { cle: string; field_id: string }[];
				};
			}>(request, `/v1/communes/comparaison?codes=${trois}`);
			expect(servie3.donnees.codes_inconnus).toEqual([]);
			const dehors = servie3.donnees.communes.find((une) => une.code_insee === HORS_PERIMETRE)!;
			expect(dehors.hebergement).toMatchObject({ etat: 'hors_couverture', motif: 'territoire_hors_perimetre' });
			const cle3 = servie3.donnees.criteres.find((un) => un.field_id === CHAMP_PRIX_MAISONS)!.cle;
			await ouvrir(page, `/comparer?communes=${trois}`);
			await expect.poll(() => entetes(page)).toEqual([CIBLE.code, HOMONYME.code, HORS_PERIMETRE]);
			await expect(page.locator(`thead th[data-code="${HORS_PERIMETRE}"] .state-badge`)).toHaveText(
				'Hors couverture · département non hébergé'
			);
			// The column out of coverage says its state once per cell, without a repeated gap (03-18 C-03).
			const cellule3 = page.locator(`tr[data-cle="${cle3}"] td[data-code="${HORS_PERIMETRE}"]`);
			await expect(cellule3).toHaveText('Hors couverture');
			await expect(cellule3).toHaveAttribute('data-state', 'out_of_coverage');
			await expect(cellule3.locator('.compare-ecart')).toHaveCount(0);
			await expect(page.locator(`thead th[data-code="${HORS_PERIMETRE}"] .compare-hc-note`)).toHaveText(
				'Aucune valeur : ne vaut pas zéro.'
			);
			await expect(page.getByText('Lien altéré')).toHaveCount(0);

			// On a phone the page scrolls, the region only horizontally (03-18, R-2); criteria stick
			// left; the column names follow in the duplicated band, aligned on the real `th`.
			const mesures = await region(page).evaluate(async (element) => {
				const horizontal = element.scrollWidth > element.clientWidth;
				const vertical = element.scrollHeight > element.clientHeight + 1;
				element.scrollLeft = element.scrollWidth;
				await new Promise((fin) => requestAnimationFrame(() => requestAnimationFrame(fin)));
				const zone = element.getBoundingClientRect();
				const critereBox = element.querySelector('tbody th[scope="row"]')!.getBoundingClientRect();
				element.scrollLeft = 0;
				return { horizontal, vertical, critere: critereBox.left - zone.left, maxHeight: getComputedStyle(element).maxHeight };
			});
			const ligneEntete = (await page.locator('thead tr').boundingBox())!.height;
			const plafond = await page.evaluate(() => window.innerHeight / 5);
			let cibles44 = true;
			for (const bouton of await page.locator('thead .compare-action').all()) {
				const boite = (await bouton.boundingBox())!;
				cibles44 &&= boite.width >= 44 && boite.height >= 44;
			}
			const haut = () =>
				page.evaluate(() => document.querySelector('table.compare-table')!.getBoundingClientRect().top + window.scrollY);
			const avant = await haut();
			await page.evaluate(() =>
				window.scrollTo(0, window.scrollY + document.querySelector('thead')!.getBoundingClientRect().bottom + 120)
			);
			const bandeLoc = page.locator('.compare-bande');
			let bande: string;
			try {
				await expect(bandeLoc).toHaveCSS('visibility', 'visible');
				bande = 'visible';
			} catch (erreur) {
				// Recorded with its cause: a closed page or a navigation error is not an absent band (D-15 IN-08).
				bande = `absente (${String(erreur).slice(0, 120)})`;
			}
			const hauteurBande = bande === 'visible' ? (await bandeLoc.boundingBox())!.height : 0;
			const deplacement = Math.abs((await haut()) - avant);
			const aligne = await page.evaluate((code) => {
				const cellule = document.querySelector(`.compare-bande-cellule[data-code="${code}"]`)!.getBoundingClientRect();
				const th = document.querySelector(`thead th[data-code="${code}"]`)!.getBoundingClientRect();
				const haut = document.querySelector('.compare-bande')!.getBoundingClientRect().top;
				const bas = document.querySelector('header.site-header')!.getBoundingClientRect().bottom;
				return { gauche: cellule.left - th.left, largeur: cellule.width - th.width, haut: haut - bas };
			}, HOMONYME.code);
			// The two Saint-Philbert and Le Pin stay distinct in the band, whatever the cut (D-15 CR-01).
			const textesBande = bande === 'visible' ? await textesVisiblesDeLaBande(page) : [];
			console.log(`RELEVE ${largeur}px bande_textes=${JSON.stringify(textesBande)}`);
			console.log(
				`RELEVE ${largeur}px ecart ${CHAMP_PRIX_MAISONS} ${HOMONYME.code}/${CIBLE.code}=${ecart.valeur} ` +
					`colonnes=3 hors_couverture=${HORS_PERIMETRE} defilement_horizontal=${mesures.horizontal} defilement_vertical=${mesures.vertical} ` +
					`entete_colonnes=${ligneEntete} plafond=${plafond} bande=${bande} hauteur_bande=${hauteurBande} cibles_44=${cibles44}`
			);
			expect(mesures.vertical, JSON.stringify(mesures)).toBe(false);
			expect(mesures.maxHeight, JSON.stringify(mesures)).toBe('none');
			if (largeur === 375) expect(mesures.horizontal, JSON.stringify(mesures)).toBe(true);
			expect(Math.abs(mesures.critere), JSON.stringify(mesures)).toBeLessThan(2);
			expect(bande, 'bande d’en-tête').toBe('visible');
			await expect(bandeLoc).toHaveAttribute('aria-hidden', 'true');
			expect(deplacement, 'the band pushes nothing').toBeLessThanOrEqual(1);
			for (const ecartPx of Object.values(aligne)) expect(Math.abs(ecartPx), JSON.stringify(aligne)).toBeLessThanOrEqual(2);
			expect(cibles44, 'cibles 44 px').toBe(true);
			expect(textesBande, 'bande d’en-tête').toHaveLength(3);
			expect(new Set(textesBande).size, `cellules de bande indistinctes ${JSON.stringify(textesBande)}`).toBe(3);
			if (largeur === 375) {
				await expect(page.locator('.compare-defilement')).toBeVisible();
				// What sticks while reading takes a fifth of the window at most; the real header row,
				// with a column out of coverage, scrolls with the page: recorded above (option (a)).
				expect(hauteurBande, `bande ${hauteurBande}px > ${plafond}px`).toBeLessThanOrEqual(plafond);
				// Non-regression guard, not the R-2 criterion (D-15 WR-03): 251 px measured on 2026-09-29.
				expect(ligneEntete, `garde de non-régression : thead ${ligneEntete}px > ${2 * plafond}px`).toBeLessThanOrEqual(2 * plafond);
			}
			await page.evaluate(() => window.scrollTo(0, 0));
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		});

		test('Fiche hors couverture — une commune hors périmètre est connue, un code inexistant ne l’est pas', async ({
			page,
			request
		}) => {
			const identite = await lireApi<{ donnees: { results: { code_insee: string; nom: string; departement: string }[] } }>(
				request,
				`/v1/communes?code_insee=${HORS_PERIMETRE}`
			);
			const [ligne] = identite.donnees.results;
			expect(ligne).toMatchObject({ code_insee: HORS_PERIMETRE, departement: '79' });
			const inexistant = await lireApi<{ donnees: { results: unknown[] } }>(request, `/v1/communes?code_insee=${INEXISTANT}`);
			expect(inexistant.donnees.results).toEqual([]);
			console.log(`RELEVE fiche ${HORS_PERIMETRE} nom=${ligne.nom} departement=${ligne.departement} ${INEXISTANT}=inconnu`);

			const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
			const reponse = await page.goto(`/commune/${HORS_PERIMETRE}`);
			await hydrated;
			expect(reponse?.status(), 'jamais la 404').toBe(200);
			await expect(page.getByRole('heading', { level: 1, name: ligne.nom })).toBeVisible();
			await expect(page.getByRole('heading', { level: 1, name: `Commune INSEE ${HORS_PERIMETRE}` })).toHaveCount(0);
			const encart = page.getByRole('status').filter({ hasText: TITRE_COMMUNE_HORS_COUVERTURE });
			await expect(encart).toContainText(PHRASE_HORS_PERIMETRE);
			await expect(page.locator('[data-kpi]')).toHaveCount(0);
		});

		test('Recherche par code postal — hors périmètre : l’état, jamais un zéro ; dans le périmètre : la commune', async ({
			page,
			request
		}) => {
			type Ban = { etat: string; motif: string | null; departements: string[] };
			type Recherche = { donnees: ResultatRecherche[]; meta?: { hebergement?: { ban?: Ban } } };

			// Outside 44/85: nothing served, the BAN coverage typed with its reason.
			const hors = await lireApi<Recherche>(request, `/v1/communes/search?q=${CODE_POSTAL_HORS}&limit=8`);
			const banHors = hors.meta?.hebergement?.ban;
			console.log(
				`RELEVE code_postal ${CODE_POSTAL_HORS} donnees=${hors.donnees.length} ban=${banHors?.etat}:${banHors?.motif}`
			);
			expect(hors.donnees).toEqual([]);
			expect(banHors).toMatchObject({ etat: 'hors_couverture', motif: 'territoire_hors_perimetre' });

			await ouvrir(page, '/');
			await champUnique(page).fill(CODE_POSTAL_HORS);
			const form = formulaire(page);
			// The visible title of the notice, and the same words announced by the live region (03-17 A12):
			// two elements, read one by one as in the synthetic spec (commune-search.spec.ts).
			await expect(form.locator('.cs-notice-title')).toHaveText(TITRE_CODE_POSTAL_HORS_COUVERTURE);
			await expect(form.locator('.cs-notice-title')).toBeVisible();
			await expect(form.getByRole('status')).toHaveText(TITRE_CODE_POSTAL_HORS_COUVERTURE);
			await expect(form).toContainText(PHRASE_HORS_PERIMETRE);
			await expect(form.getByText(/Aucune commune trouvée/)).toHaveCount(0);
			await expect(page.getByText(/territoire_hors_perimetre|hors_couverture/)).toHaveCount(0);

			await ouvrir(page, `/communes?q=${CODE_POSTAL_HORS}`);
			const resultats = page.getByRole('region', { name: 'Résultats' });
			await expect(resultats.getByRole('heading', { level: 2 })).toHaveText(TITRE_CODE_POSTAL_HORS_COUVERTURE);
			await expect(resultats).toContainText(PHRASE_HORS_PERIMETRE);
			await expect(resultats.getByText(/Aucune commune trouvée/)).toHaveCount(0);
			await expect(page.getByText(/territoire_hors_perimetre|hors_couverture/)).toHaveCount(0);

			// Inside 44/85: the target is proposed, the BAN served for the hosted departements.
			const dedans = await lireApi<Recherche>(request, `/v1/communes/search?q=${CODE_POSTAL_CIBLE}&limit=8`);
			const banDedans = dedans.meta?.hebergement?.ban;
			console.log(
				`RELEVE code_postal ${CODE_POSTAL_CIBLE} proposes=${dedans.donnees.map((r) => r.code_insee).join(',')} ` +
					`ban=${banDedans?.etat}:${banDedans?.departements?.join(',')}`
			);
			expect(dedans.donnees.map((r) => r.code_insee)).toContain(CIBLE.code);
			expect(banDedans?.etat).toBe('heberge');
			expect(banDedans?.departements).toEqual(HEBERGES);

			await ouvrir(page, '/');
			await champUnique(page).fill(CODE_POSTAL_CIBLE);
			const cible = communesProposees(page).filter({ hasText: `${CIBLE.departement} · ${CIBLE.codePostal}` });
			await expect(cible).toHaveCount(1);
			await expect(cible).toContainText(CIBLE.nom);
			await expect(cible).not.toContainText('Hors couverture');
		});

		test('Tâche 4 — la méthode en une action depuis la fiche et le comparateur, puis « Données & méthode »', async ({ page }) => {
			await ouvrir(page, `/commune/${CIBLE.code}`);
			const theme = page.locator('#critere-prix_commune');
			await voletEnUneAction(page, theme.locator('.tab-l').first().getByRole('button', { name: /^Source et définition : / }));

			await ouvrir(page, `/comparer?communes=${CIBLE.code},${HOMONYME.code}`);
			await voletEnUneAction(
				page,
				page.getByRole('button', { name: new RegExp(`^Source et définition : .+ — ${HOMONYME.nom}$`) }).first()
			);

			await page
				.getByRole('navigation', { name: 'Navigation principale' })
				.getByRole('link', { name: 'Données & méthode' })
				.click();
			await expect(page).toHaveURL(/\/datasets$/);
			await expect(page.getByRole('heading', { level: 1, name: 'Données & méthode' })).toBeVisible();
			const couverture = page.getByRole('region', { name: REGION_COUVERTURE, exact: true });
			await expect(couverture).toBeVisible();
			await expect(couverture).toContainText('Loire-Atlantique (44)');
			await expect(couverture).toContainText('Vendée (85)');
		});
	});
}

test('Invariants téléphone — vocabulaire et états distincts sur données réelles (375 px)', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 800 });
	// Vocabulary the screens must never show (03-17): the retired « Non servi », an absence titled as
	// a heading, the retired « Passoires ». A `Non renseigné` badge of a served value stays licit.
	const vocabulaire = async () => ({
		non_servi: await page.getByText('Non servi', { exact: true }).count(),
		non_renseigne_titre: await page.getByRole('heading', { name: 'Non renseigné' }).count(),
		passoires: await page.getByText(/Passoires/).count()
	});
	const total = { non_servi: 0, non_renseigne_titre: 0, passoires: 0 };
	const ajouter = (releve: typeof total) => {
		for (const cle of Object.keys(total) as (keyof typeof total)[]) total[cle] += releve[cle];
	};

	await ouvrir(page, `/commune/${CIBLE.code}`);
	await expect(page.locator('[data-kpi="prix-maisons"]')).toBeVisible();
	ajouter(await vocabulaire());
	await ouvrir(page, `/comparer?communes=${CIBLE.code},${HOMONYME.code},${HORS_PERIMETRE}`);
	await expect(region(page)).toBeVisible();
	ajouter(await vocabulaire());

	// Distinct states: a postal code out of coverage in the warn notice, titled; a commune out of
	// coverage in the warn notice of the contract (the outage and the refusal are proven on the
	// synthetic source: they cannot be provoked on the work base).
	await ouvrir(page, `/communes?q=${CODE_POSTAL_HORS}`);
	const resultats = page.getByRole('region', { name: 'Résultats' });
	await expect(resultats.getByRole('heading', { level: 2 })).toHaveText(TITRE_CODE_POSTAL_HORS_COUVERTURE);
	const postalWarn = await resultats.locator('.communes-notice').evaluate((node) => node.classList.contains('communes-notice-warn'));
	await ouvrir(page, `/commune/${HORS_PERIMETRE}`);
	const encart = page.getByRole('status').filter({ hasText: TITRE_COMMUNE_HORS_COUVERTURE });
	await expect(encart).toBeVisible();
	const etatsDistincts = postalWarn && (await page.locator('[data-kpi]').count()) === 0;
	console.log(
		`RELEVE invariants 375px non_servi=${total.non_servi} non_renseigne_titre=${total.non_renseigne_titre} ` +
			`passoires=${total.passoires} etats_distincts=${etatsDistincts}`
	);
	expect(total).toEqual({ non_servi: 0, non_renseigne_titre: 0, passoires: 0 });
	expect(etatsDistincts, 'code postal en encart warn, fiche hors couverture sans repère').toBe(true);
});

test('Comparateur — une liste servie vide est une absence, jamais une valeur (1280 px)', async ({ page, request }) => {
	await page.setViewportSize({ width: 1280, height: 800 });
	type Cellule = { code_insee: string; etat: string | null; valeur: unknown };
	const codes = `${CIBLE.code},${HOMONYME.code},${HORS_PERIMETRE}`;
	const servie = await lireApi<{ donnees: { criteres: { cle: string; cellules: Cellule[] }[] } }>(
		request,
		`/v1/communes/comparaison?codes=${codes}`
	);
	const vides = servie.donnees.criteres.flatMap((critere) =>
		critere.cellules
			.filter((cellule) => cellule.etat === 'value' && Array.isArray(cellule.valeur) && cellule.valeur.length === 0)
			.map((cellule) => `${critere.cle}:${cellule.code_insee}`)
	);
	console.log(`RELEVE comparaison listes_vides_servies=${vides.length}${vides.length ? ` ${vides.join(',')}` : ''}`);
	expect(vides).toEqual([]);

	// A real commune serving `modes: []` (its declared cause on `part_fg_pct`) is rendered as the
	// absence; when none of the candidates does, the rendering stays proven on the synthetic source.
	const candidats = [CIBLE.code, HOMONYME.code];
	const observes: string[] = [];
	for (const code of candidats) {
		const fiche = await lireApi<{
			donnees: { indicateurs_dpe?: { resultat?: { modes?: unknown; part_fg_pct?: unknown } | null } | null }[];
		}>(request, `/v1/commune?code_insee=${code}`);
		const resultat = fiche.donnees[0]?.indicateurs_dpe?.resultat;
		const partFg = resultat?.part_fg_pct;
		const etatFg = partFg && typeof partFg === 'object' ? ((partFg as { etat?: string }).etat ?? null) : null;
		if (Array.isArray(resultat?.modes) && resultat.modes.length === 0) {
			if (etatFg === 'zero_denominator') observes.push(code);
			else console.log(`RELEVE modes_vides=${code} part_fg_pct=${etatFg ?? 'valeur'}`);
		}
	}
	if (observes.length === 0) {
		console.log(`RELEVE modes_vides=non_observe candidats=${candidats.join(',')}`);
		return;
	}
	console.log(`RELEVE modes_vides=observe candidats=${observes.join(',')}`);
	for (const code of observes) {
		const autre = candidats.find((candidat) => candidat !== code)!;
		await ouvrir(page, `/comparer?communes=${autre},${code}`);
		const cellule = page.locator(`tr[data-cle="${CLE_MODES}"] td[data-code="${code}"]`);
		await expect(cellule).toContainText('Sans base de calcul');
		await expect(cellule.locator('.compare-non-affichable')).toHaveCount(0);
		expect(norm(await cellule.textContent()), code).not.toBe('');
	}
});
