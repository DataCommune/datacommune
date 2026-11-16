import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { E2E_SOURCE_ORIGIN } from './fixtures/ports';

// Readability of `/comparer` (Ianis, 2026-11-10: « pas belle, un peu illisible »). Form only: no wording
// and no cell content is read here, the frozen references keep those. Diagnosis and measures:
// the internal note 2026-11-10-comparateur-lisibilite.
// Headless runner, isolated context, every SSR read served by the local fail-closed source (AGENTS.md).
// Responses: the frozen harness response of 03-09 (`__fixtures__/commune-comparaison.json`); the four
// columns are SYNTHETIC (the two hosted communes cloned under two other codes).

interface Reponse { statut: string; donnees: { communes: Array<{ code_insee: string; nom: string; synthese: Record<string, unknown> | null }>; criteres: Array<{ cellules: Array<{ code_insee: string }> }>; codes_inconnus: string[] }; meta: unknown }
const REPONSES = JSON.parse(readFileSync(new URL('../../src/lib/data/__fixtures__/commune-comparaison.json', import.meta.url), 'utf8')) as Record<string, Reponse>;
const DEUX = REPONSES.deux_communes_hebergees;
const CODES = ['44991', '44992'];
const QUATRE = ['44991', '44992', '44993', '44994'];
const LECTURES = 6;

type Reply = { status: number; body: unknown };
const lecture = (pathname: string, query: Record<string, string>, reponse: Reply) => ({
	method: 'GET', pathname, query, minCalls: 0, maxCalls: LECTURES, responses: Array.from({ length: LECTURES }, () => reponse)
});
const comparaison = (codes: string[], body: unknown) => lecture('/v1/communes/comparaison', { codes: codes.join(',') }, { status: 200, body });

function quatre(): Reponse {
	const reponse = structuredClone(DEUX);
	[['44993', 'Troisième'], ['44994', 'Quatrième']].forEach(([code, nom], i) => {
		const commune = structuredClone(DEUX.donnees.communes[i]);
		Object.assign(commune, { code_insee: code, nom });
		if (commune.synthese) Object.assign(commune.synthese, { code_insee: code, nom_commune: nom });
		reponse.donnees.communes.push(commune);
		for (const critere of reponse.donnees.criteres) critere.cellules.push({ ...structuredClone(critere.cellules[1]), code_insee: code });
	});
	return reponse;
}

/**
 * Long states (SYNTHETIC, seen on real data on 2026-11-10 with Toulouse, Montpellier and Strasbourg): the last
 * column reads « Hors couverture · non couvert par la source » in its cells, and the values of the second one
 * carry « Écart non calculé · donnée absente » where they are served.
 */
function etatsLongs(nombre: 2 | 3 | 4): { codes: string[]; reponse: Reponse } {
	const reponse = structuredClone(nombre === 2 ? DEUX : quatre());
	if (nombre === 3) {
		reponse.donnees.communes.pop();
		for (const critere of reponse.donnees.criteres) critere.cellules.pop();
	}
	const derniere = nombre - 1;
	Object.assign(reponse.donnees.communes[derniere], { hebergement: { etat: 'heberge', motif: 'source_sans_couverture', departements: ['44'] } });
	reponse.donnees.criteres.forEach((critere, rang) => {
		const cellules = critere.cellules as Array<Record<string, unknown>>;
		// With two communes the second column carries both: one row in two each.
		if (nombre > 2 || rang % 2 === 0) Object.assign(cellules[derniere], { etat: 'out_of_coverage', valeur: null, periode: null, ecart: null, motif_non_calcul: 'donnee_absente', identique_a_la_reference: false });
		if ((nombre > 2 || rang % 2 === 1) && cellules[1].etat === 'value') Object.assign(cellules[1], { ecart: null, motif_non_calcul: 'donnee_absente', identique_a_la_reference: false });
	});
	return { codes: reponse.donnees.communes.map((commune) => commune.code_insee), reponse };
}

async function configure(request: APIRequestContext, expectations: unknown[]) {
	expect((await request.post(`${E2E_SOURCE_ORIGIN}/__configure`, { data: { reset: true, expectations } })).ok()).toBe(true);
}
async function open(page: Page, url: string) {
	const hydrated = page.waitForResponse((response) => new URL(response.url()).pathname === '/auth/me');
	await page.goto(url);
	await hydrated;
	await page.evaluate(() => document.fonts.ready);
}

/** Right edge, in the page, of each served value of a column, row by row of « Repères ». */
const bordsDroits = (page: Page, code: string) =>
	page.locator(`tbody[data-groupe="reperes"] td[data-code="${code}"] .compare-valeur > :last-child`).evaluateAll((valeurs) =>
		valeurs.map((valeur) => Math.round(valeur.getBoundingClientRect().right))
	);

test.describe('comparaison de communes à 1440 px', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
	});

	test('les valeurs d’une colonne finissent sur un même bord droit ; l’écart se lit à leur droite, sur leur ligne', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		for (const code of CODES) {
			const bords = await bordsDroits(page, code);
			expect(bords.length, `valeurs lues dans la colonne ${code}`).toBeGreaterThanOrEqual(6);
			// One edge to the pixel: a cell with a gap and a cell without one do not round the same half pixel
			// (the space of the line before the gap is 4.4 px wide).
			expect(Math.max(...bords) - Math.min(...bords), `bords droits de la colonne ${code} : ${bords.join(', ')}`).toBeLessThanOrEqual(1);
			const nom = await page.locator(`thead th[data-code="${code}"] .compare-nom`).evaluate((lien) => {
				const zone = document.createRange();
				zone.selectNodeContents(lien);
				return Math.round(zone.getBoundingClientRect().right);
			});
			// The first column has no gap: its values end where its name ends. The next ones leave the slot of
			// the gap free on their right (72 px after 12 px and the space of the line), every row alike.
			if (code === CODES[0]) expect(Math.abs(nom - bords[0]), `le nom de ${code} finit à ${nom}, ses valeurs à ${bords[0]}`).toBeLessThanOrEqual(1);
			else expect(Math.abs(nom - bords[0] - 88), `créneau de l’écart à droite des valeurs de ${code} (${nom - bords[0]} px)`).toBeLessThanOrEqual(1);
		}
		// Direction A: the gap is read to the right of its value, on its line.
		const cellule = page.locator(`tbody[data-groupe="reperes"] td[data-code="44992"]:has(.compare-ecart[data-ecart="ecart"])`).first();
		const mesure = await cellule.evaluate((td) => {
			const ligne = td.querySelector('.compare-ecart')!;
			// The gap itself: its first text node (the words kept for screen readers follow it, out of the flow).
			const zone = document.createRange();
			zone.selectNode([...ligne.childNodes].find((noeud) => noeud.nodeType === Node.TEXT_NODE && noeud.textContent!.trim() !== '')!);
			const ecart = zone.getBoundingClientRect();
			const valeur = td.querySelector('.compare-valeur > :last-child')!.getBoundingClientRect();
			return { aDroite: ecart.left >= valeur.right, memeLigne: ecart.top < valeur.bottom && ecart.bottom > valeur.top, dansLaCellule: ecart.right <= td.getBoundingClientRect().right + 1 };
		});
		expect(mesure).toEqual({ aDroite: true, memeLigne: true, dansLaCellule: true });
		// A badge keeps one line beside the free slot (seen at the slot: « Non renseigné » broke in two).
		const badges = await page.locator('tbody[data-groupe="reperes"] td .state-badge').evaluateAll((liste) => liste.map((badge) => Math.round(badge.getBoundingClientRect().height)));
		expect(badges.length).toBeGreaterThan(3);
		expect(Math.max(...badges), `hauteurs des badges : ${badges.join(', ')}`).toBeLessThanOrEqual(32);
		const hauteurs = await page.locator('tbody[data-groupe="reperes"] tr[data-ligne]').evaluateAll((lignes) => lignes.map((ligne) => Math.round(ligne.getBoundingClientRect().height)));
		console.log(`RELEVE lisibilite hauteurs des lignes de repères 1440 ${JSON.stringify(hauteurs)}`);
	});

	test('deux communes : le tableau n’est pas étiré sur la page, le libellé reste près de ses valeurs', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const mesures = await page.evaluate(() => {
			const boite = (selecteur: string) => document.querySelector(selecteur)!.getBoundingClientRect();
			return {
				tableau: Math.round(boite('table.compare-table').width),
				gabarit: Math.round(boite('.comparer-tableau').width),
				libelle: Math.round(boite('tbody[data-groupe="reperes"] th.compare-critere').width),
				colonne: Math.round(boite('thead th.compare-commune').width)
			};
		});
		console.log(`RELEVE lisibilite largeurs 2 communes 1440 ${JSON.stringify(mesures)}`);
		expect(mesures.tableau, 'largeur du tableau').toBeLessThanOrEqual(360 + 2 * 272 + 1);
		expect(mesures.tableau).toBeLessThan(mesures.gabarit);
		expect(mesures.libelle).toBeGreaterThanOrEqual(320);
		expect(mesures.colonne).toBeLessThanOrEqual(274);
	});

	test('quatre communes tiennent dans la page, sans défilement latéral', async ({ page, request }) => {
		await configure(request, [comparaison(QUATRE, quatre())]);
		await open(page, `/comparer?communes=${QUATRE.join(',')}`);
		const region = page.getByRole('region', { name: 'Tableau de comparaison' });
		expect(await region.evaluate((zone) => zone.scrollWidth <= zone.clientWidth)).toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		expect((await page.locator('thead th.compare-commune').first().boundingBox())!.width).toBeGreaterThanOrEqual(210);
		// With four communes a column has no room for the slot of the gap: it stays under its value.
		const sous = await page.locator('tbody[data-groupe="reperes"] td[data-code="44992"]:has(.compare-ecart[data-ecart="ecart"])').first().evaluate((td) => {
			return td.querySelector('.compare-ecart')!.getBoundingClientRect().top >= td.querySelector('.state-value')!.getBoundingClientRect().bottom - 1;
		});
		expect(sous, 'l’écart reste sous sa valeur').toBe(true);
	});

	for (const nombre of [2, 3, 4] as const) {
		test(`${nombre} communes aux états longs : le tableau ne défile pas, chaque badge et chaque motif tient dans sa cellule`, async ({ page, request }) => {
			const { codes, reponse } = etatsLongs(nombre);
			await configure(request, [comparaison(codes, reponse)]);
			await open(page, `/comparer?communes=${codes.join(',')}`);
			const region = page.getByRole('region', { name: 'Tableau de comparaison' });
			await expect(region.getByText('Hors couverture · non couvert par la source').first()).toBeVisible();
			await expect(region.getByText('Écart non calculé · donnée absente').first()).toBeVisible();
			const largeurs = await region.evaluate((zone) => ({ contenu: zone.scrollWidth, zone: zone.clientWidth }));
			expect(largeurs.contenu, `le tableau défile (${JSON.stringify(largeurs)})`).toBeLessThanOrEqual(largeurs.zone);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'la page ne défile pas').toBe(true);
			await expect(page.locator('.compare-defilement')).toBeHidden();
			const hors = await page.locator('tbody[data-groupe="reperes"] td').evaluateAll((cellules) =>
				cellules.flatMap((td) => {
					const cadre = td.getBoundingClientRect();
					return [...td.querySelectorAll('.state-badge, .compare-ecart:not([data-tu])')]
						.map((boite) => ({ texte: boite.textContent!.trim().slice(0, 40), boite: boite.getBoundingClientRect() }))
						.filter(({ boite }) => boite.left < cadre.left - 1 || boite.right > cadre.right + 1)
						.map(({ texte, boite }) => `${texte} [${Math.round(boite.left)}, ${Math.round(boite.right)}] hors de [${Math.round(cadre.left)}, ${Math.round(cadre.right)}]`);
				})
			);
			expect(hors, 'badges ou motifs qui sortent de leur cellule').toEqual([]);
		});
	}

	test('« Critère » est une étiquette, pas un nom : plus petite que les noms des colonnes, sous un filet marqué', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const styles = await page.evaluate(() => {
			const de = (selecteur: string) => getComputedStyle(document.querySelector(selecteur)!);
			return {
				coin: parseFloat(de('thead th.compare-coin').fontSize),
				nom: parseFloat(de('thead th.compare-commune .compare-nom').fontSize),
				filetTete: de('thead th.compare-commune').borderBottomColor,
				filetLigne: de('tbody[data-groupe="reperes"] tr[data-ligne] td').borderBottomColor
			};
		});
		expect(styles.coin).toBeLessThan(styles.nom);
		expect(styles.coin).toBeGreaterThanOrEqual(14);
		expect(styles.filetTete, 'le filet sous l’en-tête n’est pas celui d’une ligne').not.toBe(styles.filetLigne);
	});

	test('direction A : l’unité est dite après le libellé et n’est plus dessinée dans les cellules ; un verdict est un signe ; une absence garde son badge seul', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const ligne = page.locator('tbody[data-groupe="reperes"] tr[data-unite-dite="true"]').first();
		await expect(ligne.locator('th .compare-unite')).toBeVisible();
		// The unit of a cell stays in the page, clipped: one pixel at most on screen.
		const unite = (await ligne.locator('td .state-value .unit').first().boundingBox())!;
		expect(unite.width).toBeLessThanOrEqual(1);
		expect(await ligne.locator('td .state-value .value').first().innerText()).toMatch(/^[\d\s  ,]+$/);
		// The unit follows the last word of its label on the same line (rule of 09/11: never alone under it).
		const memeLigne = await ligne.locator('th').evaluate((tete) => {
			const unite = tete.querySelector('.compare-unite')!.getBoundingClientRect();
			const zone = document.createRange();
			zone.selectNodeContents(tete.querySelector('.compare-libelle')!);
			const lignes = [...zone.getClientRects()];
			const derniere = lignes[lignes.length - 1];
			return unite.top < derniere.bottom && unite.bottom > derniere.top;
		});
		expect(memeLigne).toBe(true);
		const verdict = page.locator('tbody[data-groupe="reperes"] .compare-ecart[data-ecart="verdict"]').first();
		expect(await verdict.evaluate((p) => getComputedStyle(p, '::before').content)).toMatch(/^"[=≠]"$/);
		const tu = page.locator('tbody[data-groupe="reperes"] .compare-ecart[data-tu="true"]').first();
		expect((await tu.boundingBox())!.height, 'la raison de l’écart ne prend plus de place sous le badge').toBeLessThanOrEqual(1);
		// The title names the communes: the line that repeats them is not drawn beyond 720 px.
		await expect(page.locator('.comparer-objets')).toBeHidden();
		// A folded category says how many rows it holds.
		expect(await page.locator('tbody[data-groupe="q-prix"] .compare-groupe-compte').evaluate((compte) => getComputedStyle(compte, '::after').content)).toMatch(/^"\d+ lignes"$/);
	});

	test('à 375 px deux communes se lisent sans défilement latéral : le libellé sur sa ligne, les deux valeurs dessous', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const region = page.getByRole('region', { name: 'Tableau de comparaison' });
		expect(await region.evaluate((zone) => zone.scrollWidth <= zone.clientWidth), 'le tableau ne défile pas').toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'la page ne défile pas').toBe(true);
		// The corner keeps its word for screen readers and draws the reference of the gaps only.
		const coin = page.locator('thead th.compare-coin');
		await expect(coin).toContainText('Critère');
		expect((await coin.locator('.compare-coin-mot').boundingBox())!.width).toBeLessThanOrEqual(1);
		await expect(coin.locator('.compare-reference-ecarts')).toBeVisible();
		const ligne = page.locator('tbody[data-groupe="reperes"] tr[data-ligne]').first();
		const forme = await ligne.evaluate((tr) => {
			const libelle = tr.querySelector('th')!.getBoundingClientRect();
			const cellules = [...tr.querySelectorAll('td')].map((td) => td.getBoundingClientRect());
			return {
				libelle: Math.round(libelle.width),
				sousLeLibelle: cellules.every((cellule) => cellule.top >= libelle.bottom - 1),
				memeLigne: new Set(cellules.map((cellule) => Math.round(cellule.top))).size === 1,
				largeurs: [...new Set(cellules.map((cellule) => Math.round(cellule.width)))]
			};
		});
		console.log(`RELEVE lisibilite forme 375 deux communes ${JSON.stringify(forme)}`);
		expect(forme.libelle, 'le libellé prend la largeur du tableau').toBeGreaterThanOrEqual(330);
		expect(forme.sousLeLibelle).toBe(true);
		expect(forme.memeLigne).toBe(true);
		expect(forme.largeurs.length, 'colonnes égales').toBeLessThanOrEqual(2);
		// A gap starts under its value, not under the control before it.
		const sous = await page.locator('tbody[data-groupe="reperes"] td[data-code="44992"]:has(.compare-ecart[data-ecart="ecart"])').first().evaluate((td) => {
			const zone = document.createRange();
			zone.selectNode([...td.querySelector('.compare-ecart')!.childNodes].find((noeud) => noeud.nodeType === Node.TEXT_NODE && noeud.textContent!.trim() !== '')!);
			return Math.round(zone.getBoundingClientRect().left - td.querySelector('.state-value')!.getBoundingClientRect().left);
		});
		expect(Math.abs(sous), `écart sous sa valeur (${sous} px)`).toBeLessThanOrEqual(2);
		// The table is still read as a table: its row headers and its column headers keep their roles.
		expect(await region.getByRole('rowheader').count()).toBeGreaterThan(5);
		await expect(region.getByRole('columnheader')).toHaveCount(3);
		// UI-012: a served value in the first screen.
		const premiere = (await page.locator('tbody[data-groupe="reperes"] td .state-value').first().boundingBox())!;
		console.log(`RELEVE lisibilite premiere valeur 375 deux communes ${Math.round(premiere.y + premiere.height)}`);
		expect(premiere.y + premiere.height, 'première valeur dans le premier écran (UI-012)').toBeLessThanOrEqual(812);
	});

	test('à 375 px quatre communes gardent le tableau qui défile : la mise en lignes ne vaut que pour deux', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		await configure(request, [comparaison(QUATRE, quatre())]);
		await open(page, `/comparer?communes=${QUATRE.join(',')}`);
		const region = page.getByRole('region', { name: 'Tableau de comparaison' });
		expect(await region.evaluate((zone) => zone.scrollWidth > zone.clientWidth), 'le tableau défile').toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'la page ne défile pas').toBe(true);
		expect(await page.locator('.compare-table').evaluate((table) => getComputedStyle(table).display)).toBe('table');
	});

	test('à 375 px trois communes ne font pas déborder la page, et l’unité reste dans les cellules', async ({ page, request }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		const trois = REPONSES.trois_communes_dont_hors_couverture;
		const codes = trois.donnees.communes.map((commune) => commune.code_insee);
		await configure(request, [comparaison(codes, trois)]);
		await open(page, `/comparer?communes=${codes.join(',')}`);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'pas de défilement latéral de la page').toBe(true);
		await expect(page.locator('th.compare-critere .compare-unite').first()).toBeHidden();
		expect((await page.locator('tr[data-unite-dite="true"] td .state-value .unit').first().boundingBox())!.width).toBeGreaterThan(1);
	});

	test('une ligne se suit au survol, et les deux actions d’une colonne sont séparées', async ({ page, request }) => {
		await configure(request, [comparaison(CODES, DEUX)]);
		await open(page, `/comparer?communes=${CODES.join(',')}`);
		const ligne = page.locator('tbody[data-groupe="reperes"] tr[data-ligne]').first();
		const fond = () => ligne.locator('td').first().evaluate((cellule) => getComputedStyle(cellule).backgroundColor);
		const avant = await fond();
		await ligne.hover();
		await expect.poll(fond).not.toBe(avant);
		expect(await ligne.locator('th').evaluate((cellule) => getComputedStyle(cellule).backgroundColor)).toBe(await fond());
		const actions = await page.locator('thead th[data-code="44992"] .compare-action').evaluateAll((boutons) => boutons.map((bouton) => bouton.getBoundingClientRect()));
		expect(actions).toHaveLength(2);
		expect(actions[1].left - actions[0].right, 'espace entre « Placer en premier » et « Retirer »').toBeGreaterThanOrEqual(12);
	});
});

for (const largeur of [1440, 375] as const) {
	test.describe(`moins de deux communes à ${largeur} px`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: largeur, height: largeur === 1440 ? 900 : 812 });
		});

		test('comparateur vide : ce qu’il faut faire est dit avant le bouton, sans repli ni panneau autour', async ({ page, request }) => {
			await configure(request, []);
			await open(page, '/comparer');
			const titre = page.getByRole('heading', { level: 2, name: 'Choisissez deux communes à comparer' });
			const ajouter = page.getByRole('button', { name: 'Ajouter une commune' });
			await expect(ajouter).toBeVisible();
			expect((await titre.boundingBox())!.y, 'le titre de l’état est au-dessus du bouton').toBeLessThan((await ajouter.boundingBox())!.y);
			await expect(page.locator('details.comparer-modifier > summary')).toBeHidden();
			await expect(page.locator('details.comparer-modifier')).toHaveAttribute('open', '');
			const corps = await page.locator('.comparer-modifier-corps').evaluate((bloc) => {
				const style = getComputedStyle(bloc);
				return { fond: style.backgroundColor, marge: style.paddingLeft };
			});
			expect(corps).toEqual({ fond: 'rgba(0, 0, 0, 0)', marge: '0px' });
			// Direction A (2026-11-10): the shape of the result beside the control from 721 px, under it on a phone;
			// a drawing, never a figure. The empty comparator is no longer the short page of forme-publique (D6).
			const apercu = page.locator('aside.comparer-apercu');
			await expect(apercu).toBeVisible();
			const boites = { apercu: (await apercu.boundingBox())!, ajouter: (await ajouter.boundingBox())! };
			if (largeur === 1440) expect(boites.apercu.x, 'l’aperçu est à droite du bouton').toBeGreaterThan(boites.ajouter.x + boites.ajouter.width);
			else expect(boites.apercu.y, 'l’aperçu est sous le bouton').toBeGreaterThan(boites.ajouter.y + boites.ajouter.height);
			expect(await apercu.locator('table').innerText()).not.toMatch(/\d/);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'pas de défilement latéral').toBe(true);
			// Ready comparisons under the control, each a 44 px target; an empty nature shows no « (0) ».
			const exemples = page.locator('.comparer-exemples a');
			expect(await exemples.count()).toBeGreaterThanOrEqual(2);
			for (const exemple of await exemples.all()) expect((await exemple.boundingBox())!.height).toBeGreaterThanOrEqual(44);
			await expect(page.getByRole('navigation', { name: 'Nature des objets comparés' })).not.toContainText('(0)');
			console.log(`RELEVE lisibilite comparateur vide ${largeur} ${JSON.stringify(await page.evaluate(() => ({ document: document.documentElement.scrollHeight, fenetre: window.innerHeight })))}`);
			// The add control still opens its search.
			await ajouter.click();
			await expect(page.getByRole('combobox', { name: 'Rechercher une commune' })).toBeFocused();
		});

		test('une seule commune : son nom et la phrase précèdent le bouton d’ajout', async ({ page, request }) => {
			await configure(request, [
				lecture('/v1/communes', { code_insee: '44991' }, { status: 200, body: { statut: 'ok', donnees: { results: [{ code_insee: '44991', nom: 'Ancienne', departement: '44' }], total_count: 1 } } })
			]);
			await open(page, '/comparer?communes=44991');
			const phrase = page.getByText('Ajoutez une deuxième commune pour lire les écarts.');
			const ajouter = page.getByRole('button', { name: 'Ajouter une commune' });
			expect((await phrase.boundingBox())!.y).toBeLessThan((await ajouter.boundingBox())!.y);
			await expect(page.locator('details.comparer-modifier > summary')).toBeHidden();
			await expect(page.getByRole('button', { name: 'Copier le lien' })).toBeVisible();
		});

		test('témoin : avec deux communes le repli « Modifier la sélection » est là, fermé', async ({ page, request }) => {
			await configure(request, [comparaison(CODES, DEUX)]);
			await open(page, `/comparer?communes=${CODES.join(',')}`);
			await expect(page.locator('details.comparer-modifier > summary')).toBeVisible();
			await expect(page.locator('details.comparer-modifier')).not.toHaveAttribute('open', '');
		});
	});
}
