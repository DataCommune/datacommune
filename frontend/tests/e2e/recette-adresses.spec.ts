import { test, expect, type Page } from '@playwright/test';
import { cheminRapport, corpsRapport, REQUETE_RAPPORT } from './fixtures/rapport';
import { LIBELLE_HORS_COUVERTURE } from '../../src/lib/data/etats-libelles';
import { conclusionsTrouvees } from './fixtures/conclusions-interdites';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';
import { PREMIERE_PAGE_DECLAREES } from './fixtures/adresse-ventes';
import {
	A,
	ACTUEL,
	ADRESSE_DECLAREE_RECENT,
	ANCIEN,
	B,
	CLES_SYNTHETIQUES,
	COMPARAISON_AB_DESIGNEE,
	COMPARAISON_DPE,
	COMPARAISON_TROIS,
	CRITERES_TACHE_4,
	DESIGNATION,
	DOCUMENT_RECENT,
	FICHE_A,
	LIBELLE_A,
	NB,
	NOMBRE_DOCUMENTS_A,
	PHRASE_TACHE_4,
	RECENT,
	REGION_COMPARATEUR,
	SAISIE_A,
	SAISIE_B,
	SAISIE_RENNES,
	VENTES_A,
	ATTENDU_MIXTE,
	VENTES_DECLAREES_A,
	VENTES_DECLAREES_A_INCLUSES,
	VENTES_DECLAREES_MIXTE,
	VENTES_DECLAREES_MIXTE_EXCLUES,
	comparaisonAB,
	comparaisonABDesignee,
	comparaisonDpe,
	compagnons,
	configurer,
	contratTenu,
	documentAncien,
	documentRecent,
	documentsA,
	lecture,
	mesurerInvariants,
	norm,
	ouvrir,
	pageA,
	rechercheA,
	rechercheB,
	rechercheRennes,
	ressemblantesA,
	reponses,
	sourceDeLAudit,
	type Attente
} from './fixtures/recette-adresses';
import { auditSem02, classer, collecterReponses, lignesDuRapport, totalRegistre } from './fixtures/sem02-audit';

// Acceptance replay of phase 4 on the SYNTHETIC source (plan 04-09, D-117, UI-01, SEM-01, SEM-02):
// the seven written tasks of `04-UI-SPEC.md` § « Recette », each at 375, 768 and 1 280 px, closed by
// the reading the task asks for; the UI-01 invariants of the four screens at 375 × 800; the four
// states of each surface; the SEM-02 audit of every public leaf with its four counter-proofs; the
// limits of 04-15 and 04-16 restituted (no conclusion forbidden by 04-DPE-EVALUATION.md, identity
// never said proven, D-109 tie, D-110 positive and negative, declared address published and not
// shown). A local synthetic proof: never Ianis's acceptance (D-36), never real data (the same tasks
// on real data: tests/recette/adresses-reel.recette.ts, played in 04-20).
//
// Task 6 on real data depends on the base holding a document established before July 2021 (none
// observed in 04-MESURE-DPE block A): here it is played on the synthetic source only.
//
// Every read goes to the synthetic source under an exact contract (fail-closed journal checked after
// each test) and no browser request leaves the loopback environment.

const LARGEURS = [375, 768, 1280] as const;
const NNB = ' ';
/** The public key the synthetic source expects (scripts/test-e2e.ps1), read by the SEM-02 collector. */
const CLE_PUBLIQUE = { 'X-API-Key': process.env.PUBLIC_DC_API_KEY ?? 'dc_anon_e2e_fixture' };

// 04.8-07 (UI-SPEC § 3.13, § 6.6): the large field of the home page is labelled « Adresse, commune ou code postal ».
const champUnique = (page: Page) => page.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
const propositions = (page: Page) => page.getByRole('listbox', { name: 'Lieux proposés' }).getByRole('option');
const cartes = (page: Page) => page.locator('ol.doclist-cards > li');
const region = (page: Page) => page.getByRole('region', { name: REGION_COMPARATEUR });

/** Forbidden conclusions of 04-DPE-EVALUATION.md (C11) in the text the visitor reads, title included. */
async function conclusionsInterdites(page: Page): Promise<string[]> {
	const texte = await page.evaluate(() => `${document.title}\n${document.body.innerText}`);
	return conclusionsTrouvees(texte);
}

async function sansConclusionInterdite(page: Page) {
	expect(await conclusionsInterdites(page), 'conclusion interdite par 04-DPE-EVALUATION.md').toEqual([]);
}

test.beforeEach(async ({ page }) => {
	await installLocalNetworkGuard(page);
});

test.afterEach(async ({ page, request }) => {
	await contratTenu(request);
	expectNoUnexpectedOffLocalRequest(page);
});

// --- The seven tasks -------------------------------------------------------------------------------

for (const largeur of LARGEURS) {
	test.describe(`${largeur} px`, () => {
		test.use({ viewport: { width: largeur, height: 800 } });

		test('Tâche 1 — retrouver l’adresse depuis `/` : nombre de DPE déclarés, le plus récent (classe et date)', async ({ page, request }) => {
			await configurer(request, [...rechercheA(), ...pageA(2)]);
			await ouvrir(page, '/');

			await champUnique(page).fill(SAISIE_A);
			const option = propositions(page).first();
			await expect(option.locator('.cs-type')).toHaveText('Adresse');
			await option.click();
			await expect(page).toHaveURL(new RegExp(`/adresse/${A}$`));
			// D-33 (04.8-03): the h1 is the number and the readable street; the rest of the label is on the identity line.
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('12 rue des Lilas');
			await expect(page.locator('.adresse-identity')).toContainText('85000 La Roche-sur-Yon');

			// The reading of the task: the count and the most recent, in the sentence of the list.
			await expect(page.locator('.doclist-phrase')).toHaveText(
				`${NOMBRE_DOCUMENTS_A} DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024`
			);
			// D-110 negative: no finding served, no sentence.
			await expect(page.locator('[data-constat-d110]')).toHaveCount(0);
			await sansConclusionInterdite(page);
		});

		test('Tâche 3 — ouvrir le DPE le plus récent : son numéro et l’adresse qu’il déclare', async ({ page, request }) => {
			// The address may be preloaded again from the document page (« Voir l'adresse »).
			await configurer(request, [...pageA(3), documentRecent(2)]);
			await ouvrir(page, `/adresse/${A}`);

			// Both hit zones must stay usable when the card is nested in the mobile fiche.
			const choix = cartes(page).first().locator('label.doccard-choix');
			const caseDpe = choix.getByRole('checkbox');
			await choix.click();
			await expect(caseDpe).toBeChecked();
			await choix.click();
			await expect(caseDpe).not.toBeChecked();

			await cartes(page).first().getByRole('link', { name: `DPE n°${NB}${RECENT}` }).click();
			await page.waitForURL(`**/dpe/${RECENT}`);
			// 04.8-04 (UI-SPEC v4 § 6.7): the served number is the line under the h1.
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Diagnostic de performance énergétique/);
			await expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${RECENT}`);
			await expect(page.locator('[data-ligne="adresse-declaree"] dd')).toHaveText(`«${NNB}${ADRESSE_DECLAREE_RECENT}${NNB}»`);
			await expect(page.getByText("Ce document n'est rattaché à aucun logement précis.")).toHaveCount(1);
			await sansConclusionInterdite(page);
		});

		test('Tâche 4 — ventes ressemblantes d’une maison de 90 m² à 1 km : effectif et critères servis', async ({ page, request }) => {
			await configurer(request, [...pageA(2), ressemblantesA()]);
			await ouvrir(page, `/adresse/${A}`);
			// The similar sales are read in their panel (UI-SPEC v3 § 4.8).
			await page.locator('.actions-plus summary').click();
			await page.getByRole('link', { name: 'Ventes ressemblantes' }).first().click();

			const sous = page.locator('section#ventes-ressemblantes');
			const formulaire = sous.locator('form');
			await formulaire.getByRole('radio', { name: 'Maison' }).check();
			await formulaire.getByLabel('Surface (m²)').fill('90');
			await formulaire.getByLabel('Rayon').selectOption('1000');
			await formulaire.getByRole('button', { name: 'Afficher les ventes ressemblantes' }).click();

			const query = new URLSearchParams(CRITERES_TACHE_4).toString();
			await expect(page).toHaveURL(new RegExp(`/adresse/${A}\\?${query}#ventes-ressemblantes$`));
			// The reading of the task: the count and the criteria, in the served sentence.
			expect(norm(await sous.locator('.comparables-effectif > span').first().textContent())).toBe(PHRASE_TACHE_4);
			await expect(sous.locator('ol.ventes-cartes > li')).toHaveCount(6);
			await sansConclusionInterdite(page);
		});

		test('Tâche 5 — comparer à une autre adresse : la commune en dernier ; désigner un DPE puis retirer la désignation', async ({ page, request }) => {
			await configurer(request, [
				// The page of A, then the comparator naming A alone (preloaded on tap), then the documents
				// of A listed by the designation group.
				lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A), 1, 1),
				lecture(`/v1/adresse/${A}`, {}, FICHE_A, 2, 1),
				lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documentsA(1), 3, 2),
				lecture(`/v1/adresse/${A}/ventes`, { page: '1' }, VENTES_A, 2, 0),
				lecture(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, VENTES_DECLAREES_A_INCLUSES, 2, 0),
				lecture('/v1/commune', { code_insee: '85191' }, { statut: 'ok', donnees: [], meta: { source: [] } }, 2, 0),
				rechercheB(),
				comparaisonAB(3),
				comparaisonABDesignee()
			]);
			await ouvrir(page, `/adresse/${A}`);

			await page.getByRole('link', { name: 'Comparer avec une autre adresse' }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A}$`));
			await page.getByRole('button', { name: 'Ajouter une adresse' }).click();
			await page.getByRole('combobox', { name: 'Adresse' }).fill(SAISIE_B);
			await page.getByRole('option').filter({ hasText: 'La Roche-sur-Yon' }).first().click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}$`));
			await expect(region(page)).toBeVisible();

			// The reading of the task: the part of the table that describes the commune, last.
			expect(await page.locator('tbody').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-groupe')))).toEqual([
				'documents',
				'ventes',
				'lieu',
				'commune_reference'
			]);
			await expect(page.locator('tbody[data-groupe="commune_reference"] .compare-reference-phrase')).toHaveText(
				"Ces valeurs décrivent la commune, pas l'adresse."
			);

			// Designate the 2020 document on A, read it in the link, then remove it.
			await page.getByRole('button', { name: `Désigner un DPE pour ${LIBELLE_A}` }).click();
			await page.getByRole('radio', { name: new RegExp(ANCIEN) }).check();
			await page.getByRole('button', { name: 'Désigner ce DPE' }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}&designes=${DESIGNATION}$`));
			expect(norm(await page.locator(`thead th[data-code="${A}"] .designation-texte`).textContent())).toBe(
				`DPE désigné : n° ${ANCIEN} · classe D · mai 2020`
			);
			expect(norm(await page.locator('tr.compare-designation-note').textContent())).toBe(
				"Désigné par vous pour comparer ; ce document n'est rattaché à aucun logement."
			);
			await sansConclusionInterdite(page);
			await page.getByRole('button', { name: `Retirer la désignation du DPE pour ${LIBELLE_A}` }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?adresses=${A},${B}$`));
			await expect(page.locator('tr[data-designe]')).toHaveCount(0);
		});

		test('Tâche 6 — comparer deux DPE dont un d’avant juillet 2021 : « méthodes de calcul différentes »', async ({ page, request }) => {
			await configurer(request, [documentAncien(3, 1), ...pageA(2, 0), comparaisonDpe(2)]);
			await ouvrir(page, `/dpe/${ANCIEN}`);

			await page.getByRole('link', { name: 'Comparer avec un autre DPE' }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN}$`));
			await page.getByRole('textbox', { name: 'Numéro du DPE' }).fill(ACTUEL);
			await page.getByRole('button', { name: 'Ajouter un DPE' }).click();
			await expect(page).toHaveURL(new RegExp(`/comparer\\?dpe=${ANCIEN},${ACTUEL}$`));
			await expect(region(page)).toBeVisible();

			// The reading of the task: why the class gap is not computed, as served.
			const ecart = page.locator(`tr[data-cle="classe_energie"] td[data-code="${ACTUEL}"] .compare-ecart`);
			expect(norm(await ecart.textContent())).toBe('Écart non calculé · méthodes de calcul différentes');
			expect(norm(await page.locator(`thead th[data-code="${ANCIEN}"] .compare-badge`).textContent())).toBe('Ancienne méthode');
			await sansConclusionInterdite(page);
		});

		test('Tâche 7 — une adresse à Rennes : « adresses hors couverture »', async ({ page, request }) => {
			await configurer(request, rechercheRennes());
			await ouvrir(page, '/');

			await champUnique(page).fill(SAISIE_RENNES);
			const encart = page.getByRole('search', { name: 'Rechercher un lieu' }).locator('.cs-notice-adresses');
			await expect(encart.locator('.cs-notice-title')).toHaveText('Rennes\u00a0(35)\u00a0: adresses hors couverture');
			await expect(encart).toContainText('Une absence de couverture ne vaut pas zéro.');
			await expect(page.getByText(/Aucune commune ni adresse/)).toHaveCount(0);
		});
	});
}

// --- UI-01 invariants at 375 × 800 ------------------------------------------------------------------

interface Ecran {
	nom: string;
	url: string;
	attentes: () => Attente[];
	pret: (page: Page) => Promise<void>;
	bande?: boolean;
}

const ECRANS: Ecran[] = [
	{ nom: 'adresse', url: `/adresse/${A}`, attentes: () => pageA(1), pret: (page) => expect(cartes(page)).toHaveCount(10) },
	{
		nom: 'document',
		url: `/dpe/${RECENT}`,
		attentes: () => [documentRecent(1), ...pageA(2, 0)],
		// 04.8-04 (UI-SPEC v4 § 6.7): the served number is the line under the h1.
		pret: (page) => expect(page.locator('.document-sous .document-numero')).toHaveText(`N°${NB}${RECENT}`)
	},
	{ nom: 'comparateur d’adresses', url: `/comparer?adresses=${A},${B}`, attentes: () => [comparaisonAB(1)], pret: (page) => expect(region(page)).toBeVisible(), bande: true },
	{ nom: 'comparateur de DPE', url: `/comparer?dpe=${ANCIEN},${ACTUEL}`, attentes: () => [comparaisonDpe(1)], pret: (page) => expect(region(page)).toBeVisible(), bande: true }
];

test.describe('Invariants UI-01 à 375 × 800', () => {
	test.use({ viewport: { width: 375, height: 800 } });

	for (const ecran of ECRANS) {
		test(`${ecran.nom} : aucun débordement, aucun title interactif, aucun fixed hors <dialog>, plafonds de hauteur`, async ({ page, request }) => {
			await configurer(request, ecran.attentes());
			await ouvrir(page, ecran.url);
			await ecran.pret(page);

			const mesure = await page.evaluate(mesurerInvariants, REGION_COMPARATEUR);
			expect(mesure.scrollWidth, `document ${mesure.scrollWidth}px > fenêtre ${mesure.innerWidth}px`).toBeLessThanOrEqual(mesure.innerWidth);
			expect(mesure.titres, 'élément interactif avec title').toEqual([]);
			expect(mesure.fixes, 'position: fixed hors <dialog> et lien d’évitement').toEqual([]);
			expect(mesure.horsCadre, 'boîte hors de la fenêtre').toEqual([]);

			if (ecran.nom === 'adresse') {
				const hauteurs = await cartes(page).evaluateAll((lis) =>
					lis.map((li) => ({ hauteur: Math.round(li.getBoundingClientRect().height * 10) / 10, ligne4: li.querySelector('.doccard-l4') !== null }))
				);
				console.log(`RELEVE synthetique invariants 375px adresse cartes=${hauteurs.map((c) => c.hauteur).join('/')}`);
				// 160 px per card; 185 px for a card carrying the declared-address line (04-DECISIONS-TEXTES n° 21);
				// 191 and 217 px since the 14 px floor of 04.8-01, the same relative margins over the densest
				// cards at the new sizes (derivation in adresse-fiche.spec.ts, PLAFOND_CARTE).
				for (const carte of hauteurs) expect(carte.hauteur, JSON.stringify(carte)).toBeLessThanOrEqual(carte.ligne4 ? 217 : 191);
			}
			if (ecran.bande) {
				await page.evaluate(() => window.scrollTo(0, window.scrollY + document.querySelector('thead')!.getBoundingClientRect().bottom + 120));
				const bande = page.locator('.compare-bande');
				await expect(bande).toHaveCSS('visibility', 'visible');
				const hauteur = (await bande.boundingBox())!.height;
				const plafond = await page.evaluate(() => window.innerHeight / 5);
				console.log(`RELEVE synthetique invariants 375px ${ecran.nom} bande=${hauteur} plafond=${plafond}`);
				expect(hauteur, `bande ${hauteur}px > un cinquième de la fenêtre`).toBeLessThanOrEqual(plafond);
			}
		});
	}
});

// --- The states of each surface (SEM-01) --------------------------------------------------------------

const probleme = (status: number, code: string, title: string) => ({ status, body: { title, status, code } });
const IDENTITE_RENNES = {
	etat: 'etablie', ban_id: '35238_1234_00001', libelle: null, numero: null, rep: null, nom_voie: null, code_postal: null,
	commune: { code_insee: '35238', nom: 'Rennes' }, departement: { code: '35', nom: 'Ille-et-Vilaine' }, parcelle: null
};

/** The page of A with its documents or sales answer replaced. */
function pageAAvec(remplacer: { documents?: unknown; documentsStatus?: number; ventes?: unknown; ventesStatus?: number }): Attente[] {
	return [
		lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A)),
		reponses(`/v1/adresse/${A}/dpe`, { page: '1' }, [{ status: remplacer.documentsStatus ?? 200, body: remplacer.documents ?? documentsA(1) }]),
		reponses(`/v1/adresse/${A}/ventes`, { page: '1' }, [{ status: remplacer.ventesStatus ?? 200, body: remplacer.ventes ?? VENTES_A }], 0),
		lecture(`/v1/adresse/${A}/ventes-declarees`, { ...PREMIERE_PAGE_DECLAREES }, VENTES_DECLAREES_A_INCLUSES, 1, 0),
		lecture('/v1/commune', { code_insee: '85191' }, { statut: 'ok', donnees: [], meta: { source: [] } }, 1, 0)
	];
}

const AUCUN = { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] };
const DOCUMENTS_ZERO = (() => {
	const zero = documentsA(1);
	Object.assign(zero.donnees, { total: 0, pages: 1, documents: [], selection: AUCUN, distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 } });
	return zero;
})();
const DOCUMENTS_HORS_COUVERTURE = (() => {
	const hors = documentsA(1);
	Object.assign(hors.donnees, { etat: 'out_of_coverage', motif: 'dataset_non_heberge', total: null, distribution: null, documents: [], selection: null });
	return hors;
})();

test.describe('Quatre états par surface (SEM-01)', () => {
	test('adresse (identité) — hors couverture : encart warn, aucun repère, jamais un zéro', async ({ page, request }) => {
		await configurer(request, [
			lecture(cheminRapport('35238_1234_00001'), REQUETE_RAPPORT, corpsRapport({ statut: 'ok', donnees: { identite: IDENTITE_RENNES, couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' } }, meta: { source: [] } })),
			...compagnons('35238_1234_00001')
		]);
		expect((await ouvrir(page, '/adresse/35238_1234_00001'))?.status()).toBe(200);
		await expect(page.locator('.notice.notice-warn').getByRole('heading', { level: 2 })).toHaveText('Adresse hors couverture');
		await expect(page.locator('[data-kpi]')).toHaveCount(0);
		await expect(page.getByText(/momentanément indisponible|Aucun DPE/)).toHaveCount(0);
	});

	test('adresse (identité) — panne 503 : un encart, aucune valeur ; 409 et 429 titrés, jamais une panne', async ({ page, request }) => {
		await configurer(request, [
			reponses(cheminRapport(A), REQUETE_RAPPORT, [probleme(503, 'adresse_indisponible', 'Adresse momentanement indisponible')]),
			reponses(cheminRapport('85191_0420_00013'), REQUETE_RAPPORT, [{ status: 409, body: { detail: 'texte serveur' } }]),
			reponses(cheminRapport('85191_0420_00014'), REQUETE_RAPPORT, [{ status: 429, body: { detail: 'texte serveur' } }]),
			// Three pages of the same commune: its companion read three times at most.
			...[A, '85191_0420_00013', '85191_0420_00014'].flatMap(compagnons).filter((attente) => attente.pathname !== '/v1/commune'),
			lecture('/v1/commune', { code_insee: '85191' }, { statut: 'ok', donnees: [], meta: { source: [] } }, 3, 0)
		]);
		expect((await ouvrir(page, `/adresse/${A}`))?.status()).toBe(503);
		await expect(page.getByRole('alert').getByRole('heading', { level: 2 })).toHaveText('Les données de cette adresse sont momentanément indisponibles');
		await expect(page.locator('[data-kpi]')).toHaveCount(0);

		await ouvrir(page, '/adresse/85191_0420_00013');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Demande refusée');
		await ouvrir(page, '/adresse/85191_0420_00014');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText('Trop de requêtes pour le moment');
		await expect(page.getByText(/momentanément indisponible/)).toHaveCount(0);
		await expect(page.getByText('texte serveur')).toHaveCount(0);
	});

	for (const [etat, remplacer, lecture_] of [
		[
			'zéro mesuré',
			{ documents: DOCUMENTS_ZERO },
			async (page: Page) => {
				await expect(page.locator('.doclist-phrase')).toContainText('Aucun DPE déclaré à cette adresse dans la base ADEME importée le 30 septembre 2026.');
			}
		],
		[
			'hors couverture',
			{ documents: DOCUMENTS_HORS_COUVERTURE },
			async (page: Page) => {
				await expect(page.locator('section.doclist')).toContainText(LIBELLE_HORS_COUVERTURE);
				await expect(page.locator('section.doclist')).not.toContainText(/Aucun DPE/i);
			}
		],
		[
			'panne 503',
			{ documents: { title: 'Documents DPE indisponibles', status: 503, code: 'documents_dpe_indisponibles' }, documentsStatus: 503 },
			async (page: Page) => {
				await expect(page.locator('.doclist-panne')).toContainText("Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché.");
				await expect(page.locator('section.doclist')).not.toContainText(/Aucun DPE/i);
			}
		]
	] as const) {
		test(`adresse (documents DPE) — ${etat} : dit comme tel, jamais confondu`, async ({ page, request }) => {
			await configurer(request, pageAAvec(remplacer));
			await ouvrir(page, `/adresse/${A}`);
			await lecture_(page);
			await sansConclusionInterdite(page);
		});
	}

	for (const [etat, remplacer, lecture_] of [
		[
			'zéro mesuré',
			{ ventes: { ...VENTES_A, donnees: { ...VENTES_A.donnees, total: 0, ventes: [] } } },
			async (page: Page) => {
				await expect(page.locator('section.ventes .ventes-phrase')).toHaveText('Aucune vente publiée sur la parcelle dans DVF (2021-2025).');
			}
		],
		[
			'point hors parcelle (manque)',
			{ ventes: { ...VENTES_A, donnees: { ...VENTES_A.donnees, etat: 'not_available', motif: 'point_hors_parcelle', total: null, periode: null, generation: null, ventes: [] } } },
			async (page: Page) => {
				await expect(page.locator('section.ventes .state-badge')).toHaveText('Point hors parcelle');
				await expect(page.locator('section.ventes')).not.toContainText('Aucune vente');
			}
		],
		[
			'panne 503',
			{ ventes: { title: 'Ventes indisponibles', status: 503, code: 'ventes_indisponibles' }, ventesStatus: 503 },
			async (page: Page) => {
				await expect(page.locator('section.ventes').getByRole('alert')).toContainText("Les ventes n'ont pas pu être lues.");
				await expect(page.locator('section.ventes')).not.toContainText('Aucune vente');
			}
		]
	] as const) {
		test(`adresse (ventes de la parcelle) — ${etat} : dit comme tel, jamais confondu`, async ({ page, request }) => {
			const attentes = pageAAvec(remplacer).map((attente) => (attente.pathname.endsWith('/ventes') ? { ...attente, minCalls: 1 } : attente));
			await configurer(request, attentes);
			await ouvrir(page, `/adresse/${A}`);
			await lecture_(page);
		});
	}

	test('document — inconnu (404) : couverture nommée ; panne 503 (publication refusée) : encart ; 429 : titré', async ({ page, request }) => {
		await configurer(request, [
			reponses(`/v1/dpe/${RECENT}`, {}, [{ status: 404, body: { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['44', '85'] } } }]),
			reponses(`/v1/dpe/${ANCIEN}`, {}, [{ status: 503, body: { detail: 'Données temporairement indisponibles : publication non validée.' } }]),
			reponses(`/v1/dpe/${ACTUEL}`, {}, [{ status: 429, body: { title: 'Trop de demandes', status: 429 } }])
		]);
		expect((await ouvrir(page, `/dpe/${RECENT}`))?.status()).toBe(404);
		await expect(page.locator('main')).toContainText("Cela ne veut pas dire qu'il n'existe pas.");
		await expect(page.getByRole('alert')).toHaveCount(0);

		expect((await ouvrir(page, `/dpe/${ANCIEN}`))?.status()).toBe(503);
		await expect(page.getByRole('alert').getByRole('heading', { level: 2 })).toHaveText('Ce document est momentanément indisponible');
		await expect(page.getByText('publication non validée')).toHaveCount(0);

		expect((await ouvrir(page, `/dpe/${ACTUEL}`))?.status()).toBe(200);
		await expect(page.getByRole('alert').getByRole('heading', { level: 2 })).toHaveText('Trop de requêtes pour le moment');
		await expect(page.getByText(/momentanément indisponible/)).toHaveCount(0);
	});

	test('comparateur de DPE — publication refusée (503) : un seul état de panne, aucune colonne', async ({ page, request }) => {
		await configurer(request, [reponses('/v1/dpe/comparaison', { numeros: `${ANCIEN},${ACTUEL}` }, [{ status: 503, body: { detail: 'Donnees temporairement indisponibles' } }])]);
		await ouvrir(page, `/comparer?dpe=${ANCIEN},${ACTUEL}`);
		await expect(page.getByRole('alert')).toContainText('Les données de comparaison sont momentanément indisponibles.');
		await expect(region(page)).toHaveCount(0);
	});

	test('comparateur d’adresses — une colonne hors couverture garde sa colonne, ses cellules « Hors couverture »', async ({ page, request }) => {
		const ids = (COMPARAISON_TROIS.donnees.colonnes as { ban_id: string }[]).map((colonne) => colonne.ban_id);
		await configurer(request, [lecture('/v1/adresses/comparaison', { ids: ids.join(',') }, COMPARAISON_TROIS)]);
		await ouvrir(page, `/comparer?adresses=${ids.join(',')}`);
		await expect(region(page)).toBeVisible();
		const hors = ids[2];
		await expect(page.locator(`thead th[data-code="${hors}"]`)).toHaveCount(1);
		await expect(page.locator(`td[data-code="${hors}"]`).first()).toContainText(LIBELLE_HORS_COUVERTURE);
	});
});

// --- Limits of 04-15 and 04-16 restituted (DPE-02, DPE-03; 04-DPE-EVALUATION.md) ----------------------

test.describe('Restitution des limites — conclusions interdites, D-109, D-110, adresse déclarée', () => {
	test('égalité D-109 : aucun document désigné, « Même date la plus récente », jamais « Le plus récent »', async ({ page, request }) => {
		const egalite = {
			etat: 'egalite', motif: 'document_valide', date: '2024-03-12', document: null,
			documents_ex_aequo: [
				{ numero: RECENT, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
				{ numero: '2485E0000004D', etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' }
			]
		};
		const fiche = structuredClone(FICHE_A);
		fiche.donnees.reperes.dpe.selection = egalite as never;
		const documents = documentsA(1);
		documents.donnees.selection = egalite as never;
		documents.donnees.documents[0].badges = ['meme_date_plus_recente'];
		documents.donnees.documents[1] = { ...documents.donnees.documents[1], numero: '2485E0000004D', etiquette_dpe: 'D', date_etablissement: '2024-03-12', badges: ['meme_date_plus_recente'] };
		await configurer(request, [lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(fiche)), lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documents), ...pageA(1, 0).slice(2)]);
		await ouvrir(page, `/adresse/${A}`);

		await expect(page.locator('.doclist-phrase')).toHaveText(
			`${NOMBRE_DOCUMENTS_A} DPE déclarés à cette adresse · 2 documents partagent la date la plus récente${NB}: 12 mars 2024 · classes C, D`
		);
		await expect(page.locator('body')).not.toContainText('Le plus récent');
		await sansConclusionInterdite(page);
	});

	test('constat D-110 servi : la concordance de critères, « ne prouve pas l’identité d’un logement »', async ({ page, request }) => {
		const documents = documentsA(1);
		Object.assign(documents.donnees, {
			total: 1,
			pages: 1,
			documents: documents.donnees.documents.slice(0, 1),
			distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
			constat_d110: {
				criteres: ['maison', 'document_unique_cle', 'concordance_stricte', 'batiment_unique_parcelle_du_point', 'annexes_exclues_usage_inconnu_compte'],
				sources: [{ cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' }]
			}
		});
		await configurer(request, [lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A)), lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documents), ...pageA(1, 0).slice(2)]);
		await ouvrir(page, `/adresse/${A}`);

		const encart = page.locator('[data-constat-d110]');
		await expect(encart.getByRole('heading', { level: 3 })).toHaveText(`Une adresse, un bâtiment, un DPE${NB}: les éléments concordent`);
		await encart.getByRole('button', { name: 'Source et définition : constat de concordance' }).click();
		await expect(page.getByRole('dialog')).toContainText("Ce constat ne prouve pas l'identité d'un logement.");
		await sansConclusionInterdite(page);
	});

	test('adresse déclarée non affichée : carte « non affichée », page du document « non affichée : {motif} »', async ({ page, request }) => {
		const documents = documentsA(1);
		documents.donnees.documents[0].adresse_declaree = { etat: 'masquee', texte: null, motif: 'complement_present' };
		const document = structuredClone(DOCUMENT_RECENT);
		document.donnees.adresse_declaree = { etat: 'masquee', texte: null, motif: 'complement_present' } as never;
		await configurer(request, [
			lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A), 3),
			lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documents, 3),
			...pageA(3, 0).slice(2),
			lecture(`/v1/dpe/${RECENT}`, {}, document, 2)
		]);
		await ouvrir(page, `/adresse/${A}`);
		await expect(cartes(page).first().locator('.doccard-l4')).toHaveText('Adresse déclarée non affichée');

		await cartes(page).first().getByRole('link', { name: `DPE n°${NB}${RECENT}` }).click();
		await page.waitForURL(`**/dpe/${RECENT}`);
		await expect(page.locator('[data-ligne="adresse-declaree"] dd')).toHaveText(
			`non affichée${NB}: le document y joint un complément (étage, porte, bâtiment ou nom).`
		);
		await sansConclusionInterdite(page);
	});

	test('contre-épreuve : une conclusion interdite insérée dans une réponse synthétique est relevée sur la page', async ({ page, request }) => {
		const documents = documentsA(1);
		// SYNTHETIC fault: a served text carrying a conclusion forbidden by 04-DPE-EVALUATION.md.
		documents.donnees.documents[0].adresse_declaree = { etat: 'publiee', texte: '12 rue des Lilas, DPE le plus probable', motif: null };
		await configurer(request, [lecture(cheminRapport(A), REQUETE_RAPPORT, corpsRapport(FICHE_A)), lecture(`/v1/adresse/${A}/dpe`, { page: '1' }, documents), ...pageA(1, 0).slice(2)]);
		await ouvrir(page, `/adresse/${A}`);
		await expect(cartes(page).first().locator('.doccard-l4')).toContainText('DPE le plus probable');

		const trouvees = await conclusionsInterdites(page);
		console.log(`RELEVE synthetique contre-epreuve conclusion_interdite=${JSON.stringify(trouvees)}`);
		expect(trouvees).toEqual([expect.stringContaining('choix automatique')]);
	});
});

// --- SEM-02 audit of every public leaf (R06-09/8) ----------------------------------------------------

test.describe('Audit SEM-02 — source synthétique', () => {
	test('toutes les feuilles des fiches, des listes (toutes pages) et des deux comparateurs : classées, dénominateur non nul', async ({ request }) => {
		await configurer(request, sourceDeLAudit());
		const lues = await collecterReponses(request, E2E_SOURCE_ORIGIN, CLES_SYNTHETIQUES, CLE_PUBLIQUE);
		expect(lues.filter((lue) => lue.surface === 'documents')).toHaveLength(2);
		// C4 (04.5-07): the declared sales are audited in both modes, each its own surface; C3 (04.5-08):
		// both by number and suffix, the rule of the page and of the comparator.
		expect(lues.filter((lue) => lue.surface === 'declarees').map((lue) => lue.url)).toEqual([`/v1/adresse/${A}/ventes-declarees?regle=numero_et_suffixe&page=1`]);
		expect(lues.filter((lue) => lue.surface === 'declarees-vefa').map((lue) => lue.url)).toEqual([
			`/v1/adresse/${A}/ventes-declarees?vefa=incluses&regle=numero_et_suffixe&page=1`
		]);
		const rapport = auditSem02(lues, 'exiger');
		for (const ligne of lignesDuRapport(rapport, 'synthetique')) console.log(ligne);

		expect(rapport.echecs).toEqual([]);
		expect(rapport.surfaces.map((s) => s.surface).sort()).toEqual(['cmp-adresses', 'cmp-dpe', 'declarees', 'declarees-vefa', 'document', 'documents', 'fiche', 'ressemblantes', 'ventes']);
		for (const surface of rapport.surfaces) {
			expect(surface.semantiques, `${surface.surface} : dénominateur`).toBeGreaterThan(0);
			expect(surface.nonClassees, surface.surface).toEqual([]);
			expect(surface.schemaNonClasse, surface.surface).toEqual([]);
		}
		// No known gap since C14: every semantic leaf of both registers conforms.
		const adresse = totalRegistre(rapport, 'adresse');
		const dpe = totalRegistre(rapport, 'dpe');
		expect(adresse.ecartsConnus + dpe.ecartsConnus).toBe(0);
		expect(adresse.conformes).toBe(adresse.semantiques);
		expect(dpe.conformes).toBe(dpe.semantiques);
	});

	test('valeurs affichées dans le dénominateur : la parcelle de l’identité, l’adresse BAN d’un document (FRONT WR-04)', () => {
		// Displayed « Parcelle 85191000AB0012 » on the identity line and the BAN address of the
		// document page are values the visitor reads: never `technique`.
		expect(classer('fiche', 'donnees.identite.parcelle.id_parcelle')?.classe).toBe('semantique');
		expect(classer('document', 'donnees.adresse_ban.libelle')?.classe).toBe('semantique');
	});

	// C4 (04.5-07, counter-review I): the total of the declared list is `adresse.dvf.ventes_adresse.nombre` in
	// the default mode only; under `vefa=incluses` it is the total of the listed mutations, a metadata of
	// the list, and `nombre_vefa` a metadata too. Checked on a mixed answer whose values are fixed in the
	// fixture (3 listed, 2 VEFA, 1 sale), never computed here.
	test('les deux modes des ventes déclarées : total de liste et champ statistique distincts (contre-épreuve mixte)', async ({ request }) => {
		expect(classer('declarees', 'donnees.total')?.classe).toBe('semantique');
		expect(classer('declarees-vefa', 'donnees.total')?.classe).toBe('metadonnee');
		expect(classer('declarees-vefa', 'donnees.nombre_vefa')?.classe).toBe('metadonnee');
		expect(classer('declarees-vefa', 'donnees.ventes[].hors_stats_vefa')?.classe).toBe('metadonnee');

		await configurer(request, sourceDeLAudit({ declarees: VENTES_DECLAREES_MIXTE_EXCLUES, declareesIncluses: VENTES_DECLAREES_MIXTE }));
		const lues = await collecterReponses(request, E2E_SOURCE_ORIGIN, CLES_SYNTHETIQUES, CLE_PUBLIQUE);
		const rapport = auditSem02(lues, 'exiger');
		expect(rapport.echecs).toEqual([]);
		const donnees = (surface: string) => (lues.find((lue) => lue.surface === surface)!.corps as { donnees: { total: number; nombre_vefa?: number | null } }).donnees;
		expect(donnees('declarees').total).toBe(ATTENDU_MIXTE.nombre_statistique);
		expect([donnees('declarees-vefa').total, donnees('declarees-vefa').nombre_vefa]).toEqual([ATTENDU_MIXTE.total_liste, ATTENDU_MIXTE.nombre_vefa]);
		console.log(`RELEVE synthetique sem02 mixte declarees=${donnees('declarees').total} declarees_vefa=${donnees('declarees-vefa').total}/${donnees('declarees-vefa').nombre_vefa} ok=${rapport.ok}`);
	});

	const CONTRE_EPREUVES = [
		{
			nom: 'source retirée d’une carte DPE (source de la liste dont chaque carte hérite)',
			remplacer: () => {
				const documents = documentsA(1);
				documents.donnees.source = null as never;
				return { documents1: documents };
			},
			attendu: 'documents : donnees.documents[].etiquette_dpe (dpe.document.etiquette_dpe) sans source'
		},
		{
			nom: 'rattachement retiré d’un prix DVF',
			remplacer: () => {
				const ventes = structuredClone(VENTES_A);
				ventes.donnees.rattachement = null as never;
				return { ventes };
			},
			attendu: 'ventes : donnees.ventes[].prix (adresse.dvf.vente.prix) sans rattachement'
		},
		{
			nom: 'rattachement retiré d’une vente déclarée à l’adresse (C37)',
			remplacer: () => {
				const declarees = structuredClone(VENTES_DECLAREES_A);
				declarees.donnees.rattachement = null as never;
				return { declarees };
			},
			attendu: 'declarees : donnees.ventes[].prix (adresse.dvf.vente.prix) sans rattachement'
		},
		// C4 (04.5-07): a list total that counts VEFA, served where the default mode serves the number of
		// sales of the address, is not that number: the audit refuses to read it as the field.
		{
			nom: 'total de liste avec VEFA (contre-épreuve mixte) servi en mode par défaut comme nombre de ventes de l’adresse',
			remplacer: () => ({ declarees: VENTES_DECLAREES_MIXTE }),
			attendu: 'declarees : donnees.total (sans champ) sans definition'
		},
		{
			nom: 'valeur scalaire ajoutée sans entrée de contrat',
			remplacer: () => {
				const documents = documentsA(1);
				(documents.donnees.documents[0] as Record<string, unknown>).indice_confiance = 0.88;
				return { documents1: documents };
			},
			attendu: 'documents : feuille servie non classée donnees.documents[].indice_confiance ×1'
		},
		{
			nom: 'méthode retirée d’une comparaison (méthode de calcul d’une colonne du comparateur de DPE)',
			remplacer: () => {
				const comparaison = structuredClone(COMPARAISON_DPE);
				(comparaison.donnees.colonnes as Record<string, unknown>[])[1].methode = null;
				return { cmpDpe: comparaison };
			},
			attendu: 'cmp-dpe : donnees.groupes[].lignes[].cellules[].motif_non_calcul (dpe.document.etiquette_dpe) sans methode'
		},
		// Final review FRONT WR-04: a period, a definition, a backed identity value, a comparison result.
		{
			nom: 'période retirée du repère ①',
			remplacer: () => {
				const fiche = structuredClone(FICHE_A) as { donnees: { reperes: { dpe: Record<string, unknown> } } };
				fiche.donnees.reperes.dpe.periode = null;
				return { fiche };
			},
			attendu: 'fiche : donnees.reperes.dpe.valeur (adresse.dpe.nombre_declares) sans periode'
		},
		{
			nom: 'field_id inconnu du contrat sur une valeur du lieu',
			remplacer: () => {
				const fiche = structuredClone(FICHE_A) as { donnees: { themes: { groupes: { champs: Record<string, unknown>[] }[] }[] } };
				fiche.donnees.themes[0].groupes[0].champs[0].field_id = 'adresse.champ_hors_contrat';
				return { fiche };
			},
			attendu: 'fiche : donnees.themes[].groupes[].champs[].valeur (adresse.champ_hors_contrat) sans definition'
		},
		{
			nom: 'parcelle de l’identité sans la valeur servie au thème « Bâtiment et parcelle »',
			remplacer: () => {
				const fiche = structuredClone(FICHE_A) as { donnees: { themes: { groupes: { champs: Record<string, unknown>[] }[] }[] } };
				for (const theme of fiche.donnees.themes) {
					for (const groupe of theme.groupes) groupe.champs = groupe.champs.filter((champ) => champ.field_id !== 'adresse.parcelle.identifiant');
				}
				return { fiche };
			},
			attendu: 'fiche : donnees.identite.parcelle.id_parcelle (adresse.parcelle.identifiant) sans source'
		},
		{
			nom: 'résultat de comparaison absent d’une cellule servie, colonne non référente',
			remplacer: () => {
				const comparaison = structuredClone(COMPARAISON_AB_DESIGNEE) as unknown as { donnees: { groupes: { lignes: { field_id: string; cellules: Record<string, unknown>[] }[] }[] } };
				const lignes = comparaison.donnees.groupes.flatMap((groupe) => groupe.lignes);
				const cible = lignes.find((ligne) => ligne.field_id === 'adresse.parcelle.contenance');
				if (!cible || cible.cellules[1].etat !== 'value') throw new Error('contre-épreuve : cellule servie attendue');
				Object.assign(cible.cellules[1], { ecart: null, verdict: null, motif_non_calcul: null, identique_a_la_reference: null });
				return { cmpAdresses: comparaison };
			},
			attendu: 'cmp-adresses : donnees.groupes[].lignes[].cellules[].verdict (adresse.parcelle.contenance) sans unite_ou_motif'
		}
	];

	for (const epreuve of CONTRE_EPREUVES) {
		test(`contre-épreuve — ${epreuve.nom} : l’audit échoue`, async ({ request }) => {
			await configurer(request, sourceDeLAudit(epreuve.remplacer()));
			const rapport = auditSem02(await collecterReponses(request, E2E_SOURCE_ORIGIN, CLES_SYNTHETIQUES, CLE_PUBLIQUE), 'exiger');
			console.log(`RELEVE synthetique contre-epreuve sem02 "${epreuve.nom}" ok=${rapport.ok} echecs=${rapport.echecs.length} premier=${rapport.echecs[0] ?? ''}`);
			expect(rapport.ok).toBe(false);
			expect(rapport.echecs).toContain(epreuve.attendu);
		});
	}
});
