// The form of the public site, measured on every public page at 1440, 1024, 768, 375 and 320 px (phase 4.9,
// harmonisation after Ianis's review of 06/11). Headless runner, isolated context, the synthetic source of
// the project, every off-local request refused (AGENTS.md).
//
// What this spec locks:
//   - one left edge per page: the brand, the breadcrumb, the h1, the first-level blocks of the page and the
//     footer start on the same x; a band as wide as the window (`.pleine-largeur`) paints beyond it, its
//     container does not;
//   - opening a fold stretches nothing: a closed neighbour in the same grid or flex row keeps its height;
//   - one radius for every public control, the value of `--radius-control`, a text control included;
//   - the band of a panel covers the panel, a state panel keeps the gap under its title, the fields of the
//     directory share both edges of the block under them, En bref leaves no empty column beside it, the
//     header holds one row above 720 px, the focus ring of the « i » stays in its tile, clear of its label;
//   - nothing shows under the footer: on a page shorter than the window, the footer ends where the window
//     ends (socle of 2026-11-09); on paper the page stays a plain block.
// `DC_FORME_EVIDENCE_DIR` also keeps a full-page capture of each page at each width, for a human to read.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import { attendreAucuneCarteNonReconnue, repondreCarteStatique } from './fixtures/carte-parcelles';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { PAGES_PUBLIQUES, type PagePublique } from './fixtures/pages-publiques';

const LARGEURS = [1440, 1024, 768, 375, 320] as const;
const HAUTEUR = 900;
const PREUVES = process.env.DC_FORME_EVIDENCE_DIR;

async function ouvrir(page: Page, cible: PagePublique, largeur: number) {
	await configureSsrSource(page.request, cible.source());
	// The map of the fiche comes from our own proxy under `/carte/`: a 1 × 1 grey PNG is enough for the frame
	// of its two pictures; any other map path fails the scenario.
	if (cible.carte) await repondreCarteStatique(page, { cote: 1 });
	await page.setViewportSize({ width: largeur, height: HAUTEUR });
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto(cible.chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

function collecterErreurs(page: Page): string[] {
	const erreurs: string[] = [];
	page.on('pageerror', (err) => erreurs.push(`pageerror: ${err.message}`));
	page.on('console', (message) => {
		if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) erreurs.push(`console: ${message.text()}`);
	});
	return erreurs;
}

/** Every box that must start on the edge of the page, with the x it starts on. */
async function bordsDeLaPage(page: Page) {
	return page.evaluate(() => {
		const gauche = (el: Element | null) => (el ? Math.round(el.getBoundingClientRect().left * 2) / 2 : null);
		const visible = (el: Element) => {
			const boite = el.getBoundingClientRect();
			return boite.width > 0 && boite.height > 0 && getComputedStyle(el).visibility !== 'hidden';
		};
		const nom = (el: Element) => `${el.tagName.toLowerCase()}${el.classList.length ? `.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')}` : ''}`;
		const main = document.querySelector('main');
		const racine = main?.firstElementChild ?? null;
		const reperes: Array<[string, number | null]> = [
			['marque', gauche(document.querySelector('.site-brand'))],
			['pied', gauche(document.querySelector('.site-footer-links'))],
			['h1', gauche(main?.querySelector('h1') ?? null)]
		];
		const ariane = main?.querySelector('nav[aria-label="Fil d’Ariane"], nav[aria-label="Fil d\'Ariane"]') ?? null;
		if (ariane) reperes.push(['ariane', gauche(ariane.querySelector('a, span, li') ?? ariane)]);
		for (const panneau of [...(main?.querySelectorAll('.panneau') ?? [])].filter(visible)) {
			// A panel nested in a panel, or in a column of the fiche, has its own edge: only the page's count.
			if (panneau.parentElement === racine) reperes.push([nom(panneau), gauche(panneau)]);
		}
		// The edge of a container is where its content starts, inside its gutter.
		const bord = (el: Element) => {
			const x = gauche(el);
			return x !== null && el.matches('.gabarit') ? x + Number.parseFloat(getComputedStyle(el).paddingLeft) : x;
		};
		for (const bloc of [...(racine?.children ?? [])].filter(visible)) {
			const cadre = bloc.matches('.pleine-largeur') ? bloc.querySelector(':scope > .gabarit') : bloc;
			reperes.push([nom(cadre ?? bloc), bord(cadre ?? bloc)]);
		}
		return reperes.filter((repere): repere is [string, number] => repere[1] !== null);
	});
}

/** Opens each fold of the page in turn and returns the neighbours whose height moved. */
async function etirementsALOuverture(page: Page) {
	return page.evaluate(() => {
		const decrire = (el: Element) => `${el.tagName.toLowerCase()}.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')}`;
		const estRangee = (el: Element) => {
			const style = getComputedStyle(el);
			if (style.display.includes('grid')) return true;
			return style.display.includes('flex') && !style.flexDirection.startsWith('column');
		};
		const rapports: string[] = [];
		const plis = [...document.querySelectorAll('main details')].filter((d) => d.getBoundingClientRect().height > 0);
		for (const pli of plis as HTMLDetailsElement[]) {
			// Every row, up to the page, that holds this fold beside something else.
			const voisins: Array<{ rangee: Element; voisin: Element }> = [];
			let item: Element = pli;
			for (let rangee = pli.parentElement; rangee && rangee !== document.body; rangee = rangee.parentElement) {
				if (estRangee(rangee)) {
					for (const voisin of rangee.children) {
						if (voisin !== item && voisin.getBoundingClientRect().height > 0) voisins.push({ rangee, voisin });
					}
				}
				item = rangee;
			}
			if (voisins.length === 0) continue;
			const etat = pli.open;
			pli.open = false;
			const fermes = voisins.map(({ voisin }) => voisin.getBoundingClientRect().height);
			pli.open = true;
			const ouverts = voisins.map(({ voisin }) => voisin.getBoundingClientRect().height);
			pli.open = etat;
			voisins.forEach(({ rangee, voisin }, i) => {
				if (Math.abs(ouverts[i] - fermes[i]) > 1) {
					rapports.push(`${decrire(rangee)}: ${decrire(voisin)} ${Math.round(fermes[i])} → ${Math.round(ouverts[i])} px`);
				}
			});
		}
		return [...new Set(rapports)];
	});
}

/** The rendered radius of every control; a link counts when it draws a box. */
async function rayonsDesControles(page: Page) {
	return page.evaluate(() => {
		const decrire = (el: Element) => `${el.tagName.toLowerCase()}.${[...el.classList].filter((c) => !c.startsWith('svelte-')).join('.')}`;
		const attendu = getComputedStyle(document.querySelector('.public-site')!).getPropertyValue('--radius-control').trim();
		const dessineUneBoite = (style: CSSStyleDeclaration) =>
			(parseFloat(style.borderTopWidth) > 0 && parseFloat(style.borderLeftWidth) > 0 && style.borderTopStyle !== 'none') ||
			!['rgba(0, 0, 0, 0)', 'transparent'].includes(style.backgroundColor);
		const fautifs = new Set<string>();
		const controles = document.querySelectorAll('.public-site :is(button, input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea, summary, a)');
		for (const el of controles) {
			if (el.getBoundingClientRect().height === 0) continue;
			const style = getComputedStyle(el);
			if (el.tagName === 'A' && !dessineUneBoite(style)) continue;
			// A field and its button joined in one bar: the joined corners are square, the outer ones are not.
			const rayons = [style.borderTopLeftRadius, style.borderTopRightRadius, style.borderBottomRightRadius, style.borderBottomLeftRadius];
			const nonNuls = rayons.filter((rayon) => parseFloat(rayon) > 0);
			if (nonNuls.length === 0 || nonNuls.some((rayon) => rayon !== attendu)) fautifs.add(`${decrire(el)}: ${rayons.join(' ')}`);
		}
		return { attendu, fautifs: [...fautifs] };
	});
}

/**
 * What is left under the footer, down to the bottom of the document or of the window, whichever is lower.
 * The height of a document is a whole number of pixels, the bottom of a box is not: under 1 px is nothing.
 */
async function sousLePied(page: Page) {
	return page.evaluate(() => {
		const pied = document.querySelector('footer.site-footer')!.getBoundingClientRect();
		const bas = Math.max(document.documentElement.scrollHeight, window.innerHeight);
		return { ecart: Math.round((bas - (pied.bottom + window.scrollY)) * 2) / 2, courte: document.documentElement.scrollHeight <= window.innerHeight };
	});
}

/** Left and right of an element, rounded to the half pixel. */
async function bords(page: Page, selecteur: string) {
	return page.locator(selecteur).first().evaluate((el) => {
		const boite = el.getBoundingClientRect();
		return { gauche: Math.round(boite.left * 2) / 2, droite: Math.round(boite.right * 2) / 2 };
	});
}

test.describe('forme du site public', () => {
	test.beforeEach(async ({ page }) => {
		await installLocalNetworkGuard(page);
	});
	test.afterEach(async ({ page }) => {
		expectNoUnexpectedOffLocalRequest(page);
	});

	for (const cible of PAGES_PUBLIQUES) {
		test(`${cible.id} : un seul bord gauche, aucune ouverture n'étire, un seul rayon, rien sous le pied`, async ({ page }) => {
			test.setTimeout(180_000);
			const erreurs = collecterErreurs(page);
			const constats: string[] = [];
			for (const largeur of LARGEURS) {
				await ouvrir(page, cible, largeur);

				if (PREUVES) {
					mkdirSync(PREUVES, { recursive: true });
					await page.screenshot({ path: join(PREUVES, `${cible.id}-${largeur}.png`), fullPage: true });
				}

				const bords = await bordsDeLaPage(page);
				const reference = bords.find(([nom]) => nom === 'marque')?.[1];
				for (const [nom, x] of bords) {
					if (x !== reference) constats.push(`${largeur} px, bord : ${nom} à ${x}, marque à ${reference}`);
				}
				for (const etirement of await etirementsALOuverture(page)) constats.push(`${largeur} px, étirement : ${etirement}`);
				const { attendu, fautifs } = await rayonsDesControles(page);
				expect(attendu, 'le jeton des contrôles sur .public-site').toBe('6px');
				for (const fautif of fautifs) constats.push(`${largeur} px, rayon (attendu ${attendu}) : ${fautif}`);
				const pied = await sousLePied(page);
				if (Math.abs(pied.ecart) >= 1) constats.push(`${largeur} px, pied : ${pied.ecart} px sous le pied (page ${pied.courte ? 'plus courte que' : 'plus longue que'} la fenêtre)`);
			}
			if (PREUVES) writeFileSync(join(PREUVES, `${cible.id}.constats.txt`), `${constats.join('\n')}\n`);
			expect(constats, `${cible.chemin}\n${constats.join('\n')}`).toEqual([]);
			expect(erreurs).toEqual([]);
			if (cible.carte) attendreAucuneCarteNonReconnue(page);
		});
	}

	test('une page plus courte que la fenêtre : le pied touche le bas de la fenêtre, aux cinq largeurs', async ({ page }) => {
		// The empty comparator of addresses is the short page measured (D6, 2026-11-10: the empty comparator of
		// communes, measured here until then with 7 px to spare at 1440 x 900, now previews the shape of a result
		// and is taller; the page not found, tried first, is taller than 900 px too). The assertion is unchanged;
		// only the page is. A window of 2 400 px is taller than it at every width.
		const vide: PagePublique = { id: 'comparer-vide-adresses', chemin: '/comparer?adresses=', source: () => [] };
		for (const largeur of LARGEURS) {
			await ouvrir(page, vide, largeur);
			await page.setViewportSize({ width: largeur, height: 2400 });
			const pied = await sousLePied(page);
			expect(pied.courte, `${largeur} px : la page est plus courte que la fenêtre`).toBe(true);
			expect(Math.abs(pied.ecart), `${largeur} px : rien sous le pied`).toBeLessThan(1);
		}
		// At the height of the tour of 09/11 (1440 × 900), where the pale band showed under the footer.
		await page.setViewportSize({ width: 1440, height: HAUTEUR });
		const tour = await sousLePied(page);
		expect(tour.courte, '1440 × 900 : la page est plus courte que la fenêtre').toBe(true);
		expect(Math.abs(tour.ecart), '1440 × 900 : rien sous le pied').toBeLessThan(1);
		// On paper the page is a plain block: nothing is pushed down a sheet.
		await page.emulateMedia({ media: 'print' });
		const papier = await page.evaluate(() => {
			const site = getComputedStyle(document.querySelector('.public-site')!);
			return { display: site.display, hauteurMinimale: site.minHeight };
		});
		expect(papier).toEqual({ display: 'block', hauteurMinimale: '0px' });
	});

	test('la bande d’un panneau couvre le panneau, sa phrase garde la mesure', async ({ page }) => {
		const datasets = PAGES_PUBLIQUES.find((cible) => cible.id === 'datasets')!;
		for (const largeur of LARGEURS) {
			await ouvrir(page, datasets, largeur);
			const mesures = await page.locator('main .panneau:has(.panneau-chapo)').first().evaluate((panneau) => {
				const bande = Number.parseFloat(getComputedStyle(panneau, '::before').width);
				const chapo = panneau.querySelector('.panneau-chapo')!;
				return { panneau: panneau.getBoundingClientRect().width, bande, mesure: getComputedStyle(chapo).maxWidth };
			});
			expect(Math.abs(mesures.panneau - 2 - mesures.bande), `${largeur} px`).toBeLessThanOrEqual(1);
			expect(mesures.mesure, `${largeur} px`).not.toBe('none');
		}
	});

	test('un panneau d’état du comparateur garde l’écart sous son titre', async ({ page }) => {
		const panne = PAGES_PUBLIQUES.find((cible) => cible.id === 'comparer-panne')!;
		await ouvrir(page, panne, 1440);
		const ecart = await page.locator('main .notice').first().evaluate((notice) => {
			const titre = notice.querySelector('.notice-title')!.getBoundingClientRect();
			const texte = notice.querySelector('.notice-corps p')!.getBoundingClientRect();
			return texte.top - titre.bottom;
		});
		expect(ecart).toBeGreaterThanOrEqual(16);
	});

	test('l’annuaire : le champ, le département et « Filtrer » ont les mêmes bords', async ({ page }) => {
		const annuaire = PAGES_PUBLIQUES.find((cible) => cible.id === 'annuaire')!;
		for (const largeur of LARGEURS) {
			await ouvrir(page, annuaire, largeur);
			const champ = await bords(page, 'main .cs-saisie');
			const departement = await bords(page, 'main .cs-field-select select');
			const filtrer = await bords(page, 'main details.det');
			expect(champ.gauche, `${largeur} px`).toBe(filtrer.gauche);
			expect(departement.droite, `${largeur} px`).toBe(filtrer.droite);
			// Stacked up to 720 px: the same height only means something on one row.
			if (largeur > 720) {
				const hauteurs = await page.evaluate(() =>
					['.cs-saisie .cs-input', '.cs-field-select select'].map((s) => document.querySelector(`main ${s}`)!.getBoundingClientRect().height)
				);
				expect(Math.abs(hauteurs[0] - hauteurs[1]), `${largeur} px`).toBeLessThanOrEqual(1);
			}
		}
	});

	test('la fiche adresse à 1440 px : En bref sans colonne vide, l’identité sur une ligne', async ({ page }) => {
		await ouvrir(page, PAGES_PUBLIQUES.find((cible) => cible.id === 'adresse')!, 1440);
		const bref = await bords(page, 'main .bref');
		const marque = await bords(page, '.site-brand');
		const compte = await bords(page, '.site-account-button');
		expect(bref.gauche).toBe(marque.gauche);
		expect(bref.droite).toBe(compte.droite);
		const identite = await page.locator('.adresse-identity').evaluate((el) => getComputedStyle(el).maxWidth);
		expect(identite).toBe('none');
	});

	test('la bande DPE de la tuile peint les sept classes de leur couleur pleine, de A à G ; seule la plus fréquente est encadrée', async ({ page }) => {
		// The fixture serves no dominant class: the served figure is added, on a copy of the report.
		const cible = PAGES_PUBLIQUES.find((page_) => page_.id === 'adresse')!;
		const source = () =>
			cible.source().map((attente) => {
				if (!attente.pathname.endsWith('/rapport')) return attente;
				const copie = structuredClone(attente) as typeof attente & {
					responses: { body: { donnees: { resume: { chiffres_cles: Record<string, unknown>[] } } } }[];
				};
				for (const reponse of copie.responses) {
					const dpe = reponse.body.donnees.resume.chiffres_cles.find((chiffre) => chiffre.theme === 'dpe')!;
					Object.assign(dpe, { etat: 'ok', motif: null, chiffres: [{ id: 'classe', libelle: 'classe la plus fréquente', valeur: 'C', unite: 'classe_dpe' }] });
				}
				return copie;
			});
		await ouvrir(page, { ...cible, source }, 1440);
		// `pleine`: the colour of the class as the global `.dpe-X` paints it, read on a probe.
		const cases = await page.locator('.dpe-bande > span').evaluateAll((spans) =>
			spans.map((span) => {
				const sonde = document.createElement('span');
				sonde.className = `dpe-${span.textContent}`;
				document.body.appendChild(sonde);
				const pleine = getComputedStyle(sonde).backgroundColor;
				sonde.remove();
				const style = getComputedStyle(span);
				return { classe: span.textContent, dominante: span.classList.contains('dpe-dominante'), fond: style.backgroundColor, marque: style.boxShadow !== 'none', graisse: style.fontWeight, pleine };
			})
		);
		expect(cases, 'les sept classes').toHaveLength(7);
		expect(cases.filter((c) => c.dominante).map((c) => c.classe), 'une seule classe encadrée, la plus fréquente').toEqual(['C']);
		expect(new Set(cases.map((c) => c.fond)).size, 'sept couleurs distinctes').toBe(7);
		for (const c of cases) {
			expect(c.fond, `aucune case blanche ${JSON.stringify(c)}`).not.toBe('rgb(255, 255, 255)');
			expect(c.fond, `la couleur pleine de la classe ${JSON.stringify(c)}`).toBe(c.pleine);
			expect(c.marque, `aucune marque hors de la plus fréquente ${JSON.stringify(c)}`).toBe(c.dominante);
			expect(c.graisse === '600', `grasse si la plus fréquente seulement ${JSON.stringify(c)}`).toBe(c.dominante);
		}
	});

	test('le bandeau pâle des deux fiches va d’un bord à l’autre de la fenêtre, sans trou derrière le texte', async ({ page }) => {
		for (const [id, selecteur] of [['adresse', '.fiche-identite'], ['commune', '.fiche-tete']] as const) {
			await ouvrir(page, PAGES_PUBLIQUES.find((cible) => cible.id === id)!, 1440);
			const bandeau = await page.locator(selecteur).evaluate((el) => {
				const sonde = document.createElement('span');
				sonde.style.color = 'var(--color-bande)';
				document.querySelector('.public-site')!.appendChild(sonde);
				const attendu = getComputedStyle(sonde).color;
				sonde.remove();
				const style = getComputedStyle(el);
				return { attendu, fond: style.backgroundColor, ombre: style.boxShadow };
			});
			expect(bandeau.fond, `${id} : le fond derrière le texte`).toBe(bandeau.attendu);
			expect(bandeau.ombre, `${id} : le débord jusqu’à la fenêtre`).toContain(bandeau.attendu);
		}
	});

	test('l’en-tête tient sur une rangée au-dessus de 720 px', async ({ page }) => {
		const commune = PAGES_PUBLIQUES.find((cible) => cible.id === 'commune')!;
		for (const largeur of [1440, 1280, 1121, 1120, 1024, 900, 800, 768, 721]) {
			await ouvrir(page, commune, largeur);
			const hauteur = await page.locator('header.site-header').evaluate((el) => el.getBoundingClientRect().height);
			expect(hauteur, `${largeur} px`).toBeLessThanOrEqual(70);
		}
	});

	test('l’anneau de focus du i reste dans sa tuile, dans son coin', async ({ page }) => {
		const commune = PAGES_PUBLIQUES.find((cible) => cible.id === 'commune')!;
		for (const largeur of LARGEURS) {
			await ouvrir(page, commune, largeur);
			const bouton = page.locator('main dl.cles .cle .source-button').first();
			await bouton.focus();
			await page.keyboard.press('Shift+Tab');
			await page.keyboard.press('Tab');
			await expect(bouton).toBeFocused();
			const mesure = await bouton.evaluate((el) => {
				const style = getComputedStyle(el);
				const anneau = el.getBoundingClientRect().top - Number.parseFloat(style.outlineOffset) - Number.parseFloat(style.outlineWidth);
				const tuile = el.closest('.cle')!.getBoundingClientRect();
				const libelle = el.closest('.cle')!.querySelector('dt')!;
				const plage = document.createRange();
				plage.selectNodeContents(libelle);
				return { anneau, haut: tuile.top, droite: tuile.right, bord: el.getBoundingClientRect().right, texte: plage.getBoundingClientRect().right, gauche: el.getBoundingClientRect().left + 10, style: style.outlineStyle, epaisseur: style.outlineWidth };
			});
			expect(mesure.style, `${largeur} px`).toBe('solid');
			expect(mesure.epaisseur, `${largeur} px`).toBe('3px');
			expect(mesure.anneau, `${largeur} px : ${JSON.stringify(mesure)}`).toBeGreaterThanOrEqual(mesure.haut);
			expect(mesure.bord, `${largeur} px : ${JSON.stringify(mesure)}`).toBeLessThanOrEqual(mesure.droite + 0.5);
			expect(mesure.texte, `libellé hors du i, ${largeur} px : ${JSON.stringify(mesure)}`).toBeLessThanOrEqual(mesure.gauche);
		}
	});
});
