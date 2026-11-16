import AxeBuilder from '@axe-core/playwright';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { configureSsrSource, type SourceExpectation } from './fixtures/bien-ssr-source';
import { mockAuthAnonyme } from './fixtures/mocks-matrice';
import { verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

// 04.8-07 — search, directory, home and root 404 in their v4 form (UI-SPEC § 6.5, § 6.6, § 6.10; D-15,
// D-16, D-34).
//
// What this spec locks:
//   - /communes?q=: breadcrumb « Accueil › Recherche », the h1 « Résultats pour « q » », the large field,
//     the state in a `p role="status"`, the results in two simple panels « Adresses » then « Communes »,
//     each row a whole-row link ending with the accent arrow; with a 200-character query the h1 and the
//     state wrap without overflow at 320 px;
//   - /communes without q: the h1 « Annuaire des communes » (D4, 4.9 lot 7), the filters in a `details`
//     « Filtrer », the directory as a table in a panel;
//   - /: prerendered, the h1 « Une adresse, en clair », the large field on the solid head with the rings
//     centred on it, « Pour commencer » without any count or place, the scales and their link to
//     /datasets#lire, no « Gratuit, sans compte, sans publicité. », no /v1 request at load;
//   - the root 404: neither breadcrumb nor header search; the large field and « Pour repartir »;
//   - each surface at 1440, 375 and 320 px: axe without violation, no overflow, 14 px floor, locked
//     weights, 60ch measure.
// Captures go to `DC_RECHERCHE_EVIDENCE_DIR` when set, else to the output folder of the run.

const TAGS_WCAG = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
const LARGEURS = [1440, 375, 320] as const;
const HEBERGE = { etat: 'heberge', motif: null, departements: ['35'] };

function dossierPreuves(testInfo: TestInfo): string {
	const dossier = process.env.DC_RECHERCHE_EVIDENCE_DIR || testInfo.outputPath('preuves');
	mkdirSync(dossier, { recursive: true });
	return dossier;
}

function commune(code: string, departement: string, extra: Record<string, unknown> = {}) {
	return {
		code_insee: code,
		nom: 'Le Pin',
		code_postal: null,
		departement,
		population: 1000,
		population_annee: null,
		population_source: 'geo_communes',
		hebergement: { ...HEBERGE, departements: [departement] },
		...extra
	};
}

const ADRESSE = {
	ban_id: '35999_0100_00012',
	type_resultat: 'adresse',
	numero: '12',
	rep: null,
	nom_voie: 'Rue des Lilas',
	code_postal: '35170',
	code_commune: '35999',
	nom_commune: 'Le Pin',
	departement: { code: '35', nom: 'Ille-et-Vilaine' }
};

/** SSR answers of the search: one address, two homonym communes (one out of coverage). */
function sourceRecherche(q: string, adresses: unknown[] = [ADRESSE]): SourceExpectation[] {
	return [
		{
			method: 'GET',
			pathname: '/v1/adresses/recherche',
			query: { q, limite: '8' },
			minCalls: 1,
			maxCalls: 1,
			responses: [
				{
					status: 200,
					body: { statut: 'ok', donnees: { ordre_types: ['adresse', 'commune'], resultats: adresses, hebergement: { ban: HEBERGE } } }
				}
			]
		},
		{
			method: 'GET',
			pathname: '/v1/communes/search',
			query: { q, limit: '20', inclure_arrondissements: 'true' },
			minCalls: 1,
			maxCalls: 1,
			responses: [
				{
					status: 200,
					body: {
						statut: 'ok',
						donnees: [
							commune('79211', '79', { hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] } }),
							commune('35999', '35', { code_postal: '35170', population: 750, population_annee: 2022, population_source: 'demographie' })
						]
					}
				}
			]
		}
	];
}

const DEFAUT_ANNUAIRE = { couverture: 'servies', sort_by: 'population', order: 'desc', page: '1', per_page: '50', inclure_arrondissements: 'true' };

function sourceAnnuaire(): SourceExpectation[] {
	const ligne = (code: string, nom: string, population: number) => ({
		code_insee: code,
		nom,
		departement: '35',
		population,
		population_annee: 2022,
		population_source: 'demographie',
		hebergement: HEBERGE,
		prix: {
			maison: { mediane_m2: 3200, effectif: 120, etat: null },
			appartement: { mediane_m2: 3900, effectif: 300, etat: null }
		}
	});
	return [
		{
			method: 'GET',
			pathname: '/v1/communes/annuaire',
			query: DEFAUT_ANNUAIRE,
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

async function ouvrir(page: Page, chemin: string, largeur: number, source?: SourceExpectation[]) {
	await mockAuthAnonyme(page);
	const sonde = source ? await configureSsrSource(page.request, source) : null;
	await page.setViewportSize({ width: largeur, height: 900 });
	const reponse = await page.goto(chemin);
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
	return { reponse, sonde };
}

async function verifierQualite(page: Page, libelle: string) {
	const axe = await new AxeBuilder({ page }).withTags([...TAGS_WCAG]).analyze();
	expect(axe.violations.map((v) => `${v.id}: ${v.nodes.length}`), `axe ${libelle}`).toEqual([]);
	const disposition = await page.evaluate(() => ({ largeur: innerWidth, defilement: document.documentElement.scrollWidth }));
	expect(disposition.defilement, `débordement ${libelle}`).toBeLessThanOrEqual(disposition.largeur);
	expect(await verifierPlancherTypo(page, 'main'), `plancher ${libelle}`).toEqual([]);
	expect(await verifierGraisses(page, 'main'), `graisses ${libelle}`).toEqual([]);
	expect(await verifierMesure(page, 'main', ['p']), `mesure ${libelle}`).toEqual([]);
}

async function capturer(page: Page, testInfo: TestInfo, nom: string, largeur: number) {
	if (largeur === 320) return;
	await page.screenshot({ path: join(dossierPreuves(testInfo), `${nom}-${largeur}.png`), fullPage: true, animations: 'disabled' });
}

/** The site header carries no search on these three surfaces. */
async function sansRechercheEnTete(page: Page) {
	await expect(page.getByRole('banner').getByRole('search')).toHaveCount(0);
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

test.describe('recherche /communes?q= (UI-SPEC § 6.5)', () => {
	test('fil, h1, grand champ, état, panneaux « Adresses » puis « Communes » en lignes de liens', async ({ page }, testInfo) => {
		for (const largeur of LARGEURS) {
			const { sonde } = await ouvrir(page, '/communes?q=le+pin', largeur, sourceRecherche('le pin'));
			await sonde!.assert();
			const ariane = page.getByRole('navigation', { name: "Fil d'Ariane" });
			await expect(ariane.locator('[aria-current="page"]')).toHaveText('Recherche');
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Résultats pour « le pin »');
			await sansRechercheEnTete(page);
			await expect(page.getByRole('main').getByRole('combobox', { name: 'Adresse, commune ou code postal' })).toBeVisible();
			// The state line of the page, not the live region of the field (`.cs-sr-only`, empty at rest).
			// Served counts only; the commune out of coverage is listed and counted, never as a zero.
			await expect(page.locator('main p.r-etat[role="status"]')).toHaveText('2 communes et 1 adresse proposées');
			await expect(page.locator('main section.panneau-simple > h2')).toHaveText([/^Adresses?/, 'Communes']);
			const adresses = page.locator('main section.panneau-simple').nth(0);
			await expect(adresses.getByRole('link', { name: '12 Rue des Lilas' })).toHaveAttribute('href', '/adresse/35999_0100_00012');
			await expect(adresses.locator('.lien-n').first()).toHaveText('Adresse · 35170 Le Pin · Ille-et-Vilaine (35)');
			const communes = page.locator('main section.panneau-simple').nth(1);
			// Served fields only: the departement and its code, the INSEE code that tells homonyms apart, the
			// population dated only when served so (H6).
			await expect(communes.locator('.lien-n')).toHaveText([
				'Commune · Deux-Sèvres (79) · INSEE 79211 · 1 000 habitants',
				'Commune · 35170 · Ille-et-Vilaine (35) · INSEE 35999 · 750 habitants · 2022 · INSEE'
			]);
			// One arrow per row: one address and two communes (a loop over nothing would prove nothing).
			await expect(page.locator('main section.panneau-simple a .fleche')).toHaveCount(3);
			for (const fleche of await page.locator('main section.panneau-simple a .fleche').all()) {
				await expect(fleche).toHaveText('→');
				await expect(fleche).toHaveAttribute('aria-hidden', 'true');
			}
			await verifierQualite(page, `/communes?q= à ${largeur} px`);
			await capturer(page, testInfo, 'recherche', largeur);
		}
	});

	test('requête de 200 caractères : titre et état à la ligne, sans débordement à 320 px', async ({ page }) => {
		const q = 'chemin du moulin de la grande prairie '.repeat(6).slice(0, 200).trim();
		expect(q.length).toBeGreaterThan(190);
		const { sonde } = await ouvrir(page, `/communes?q=${encodeURIComponent(q)}`, 320, sourceRecherche(q, []));
		await sonde!.assert();
		const titre = page.getByRole('heading', { level: 1 });
		await expect(titre).toContainText(q.slice(0, 40));
		expect(await titre.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThan(80);
		const disposition = await page.evaluate(() => ({ largeur: innerWidth, defilement: document.documentElement.scrollWidth }));
		expect(disposition.defilement).toBeLessThanOrEqual(disposition.largeur);
		for (const etat of await page.locator('main p[role="status"]').all()) {
			expect(await etat.evaluate((el) => el.getBoundingClientRect().right)).toBeLessThanOrEqual(320);
		}
	});
});

test.describe('annuaire /communes (UI-SPEC § 6.5)', () => {
	test('h1, filtres dans « Filtrer », tableau au bureau, lignes de liens sous 720 px', async ({ page }, testInfo) => {
		for (const largeur of LARGEURS) {
			const { sonde } = await ouvrir(page, '/communes', largeur, sourceAnnuaire());
			await sonde!.assert();
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Annuaire des communes');
			await sansRechercheEnTete(page);
			const filtrer = page.locator('main details.det');
			await expect(filtrer.locator('summary')).toHaveText('Filtrer');
			await expect(page.getByRole('table')).toBeVisible({ visible: largeur > 720 });
			if (largeur > 720) {
				// A column header is never cut inside a word (« Commun / e », seen on the first capture).
				const lignes = await page.locator('table thead th').first().evaluate((th) => {
					const plage = document.createRange();
					plage.selectNodeContents(th);
					return new Set([...plage.getClientRects()].map((r) => Math.round(r.top))).size;
				});
				expect(lignes, 'en-tête « Commune » sur une ligne').toBe(1);
			}
			await verifierQualite(page, `/communes à ${largeur} px`);
			await capturer(page, testInfo, 'annuaire', largeur);
		}
	});

	test('de 721 à 1040 px, le tableau défile dans sa région sans faire déborder la page', async ({ page }) => {
		for (const largeur of [768, 1024]) {
			const { sonde } = await ouvrir(page, '/communes', largeur, sourceAnnuaire());
			await sonde!.assert();
			const disposition = await page.evaluate(() => ({ largeur: innerWidth, defilement: document.documentElement.scrollWidth }));
			expect(disposition.defilement, `débordement à ${largeur} px`).toBeLessThanOrEqual(disposition.largeur);
			await expect(page.getByRole('region', { name: 'Tableau de l’annuaire des communes' })).toHaveAttribute('tabindex', '0');
		}
	});
});

test.describe('accueil / (UI-SPEC § 6.6 ; D-15, D-16, D-34)', () => {
	test('tête, « Pour commencer », les échelles, rien de la promesse ni de compteur hors de l’exemple daté', async ({ page }, testInfo) => {
		const requetesApi: string[] = [];
		page.on('request', (requete) => {
			if (new URL(requete.url()).pathname.startsWith('/v1')) requetesApi.push(requete.url());
		});
		for (const largeur of LARGEURS) {
			await ouvrir(page, '/', largeur);
			const h1 = page.getByRole('heading', { level: 1 });
			await expect(h1).toHaveText('Une adresse, en clair');
			expect(await h1.evaluate((el) => getComputedStyle(el).fontSize)).toBe(largeur > 1080 ? '64px' : largeur > 720 ? '48px' : '38px');
			await sansRechercheEnTete(page);
			await expect(page.getByRole('main').getByRole('combobox', { name: 'Adresse, commune ou code postal' })).toBeVisible();
			const commencer = page.getByRole('region', { name: 'Pour commencer' });
			await expect(commencer.locator('.fleche')).toHaveCount(6);
			for (const fleche of await commencer.locator('.fleche').all()) await expect(fleche).toHaveText('→');
			expect(await commencer.innerText()).not.toMatch(/\d/);
			// The link of the professionals' sentence keeps a 44 px target without stretching the line that carries it.
			const pros = await page.locator('p.pros').evaluate((p) => {
				// The lines of the text itself, never the box of the link.
				const tops: number[] = [];
				const textes = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
				for (let n = textes.nextNode(); n; n = textes.nextNode()) {
					const plage = document.createRange();
					plage.selectNodeContents(n);
					for (const r of plage.getClientRects()) if (r.width > 0) tops.push(Math.round(r.top));
				}
				const hauts = [...new Set(tops)].sort((a, b) => a - b);
				return {
					pas: hauts.slice(1).map((h, i) => h - hauts[i]),
					interligne: parseFloat(getComputedStyle(p).lineHeight),
					cible: p.querySelector('a')!.getBoundingClientRect().height
				};
			});
			for (const pas of pros.pas) expect(pas, `interligne de la phrase des professionnels à ${largeur} px`).toBeLessThanOrEqual(pros.interligne + 1);
			expect(pros.cible, `cible du lien à ${largeur} px`).toBeGreaterThanOrEqual(44);
			const echelle = page.getByRole('region', { name: 'Chaque chiffre dit jusqu’où il vaut' });
			await expect(echelle.locator('li')).toHaveCount(5);
			// The circles grow with the scale; the titles of one row stay on one line (« pas bien aligné », 06/11).
			const titres = await echelle.locator('li').evaluateAll((items) =>
				items.map((li) => ({ rangee: Math.round(li.getBoundingClientRect().top), titre: Math.round(li.querySelector('h3')!.getBoundingClientRect().top) }))
			);
			const parRangee = new Map<number, Set<number>>();
			for (const { rangee, titre } of titres) parRangee.set(rangee, (parRangee.get(rangee) ?? new Set()).add(titre));
			for (const [rangee, hauts] of parRangee) expect([...hauts], `titres de la rangée ${rangee} à ${largeur} px`).toHaveLength(1);
			await expect(echelle.getByRole('link', { name: /Comment lire une fiche/ })).toHaveAttribute('href', '/datasets#lire');
			// 4.9 lot 9: one way to the legend in the content; the footer keeps its own.
			await expect(page.getByRole('main').getByText('Comment lire une fiche')).toHaveCount(1);
			await expect(page.getByRole('contentinfo').getByRole('link', { name: 'Lire une fiche' })).toHaveAttribute('href', '/datasets#lire');
			// The head is painted as it is built: the rings are centred on the field, and behind what reads over them.
			// The field is measured once the page is hydrated: until then the rings wait in the middle of the head.
			await expect
				.poll(() => page.locator('.hero').evaluate((el) => (el as HTMLElement).style.getPropertyValue('--centre-anneaux')))
				.not.toBe('');
			const peint = await page.evaluate(() => {
				const boite = (selecteur: string) => document.querySelector(selecteur)!.getBoundingClientRect();
				const champ = boite('.acc-champ .cs-saisie');
				const anneaux = boite('.anneaux');
				const dessus = (el: Element) => {
					const r = el.getBoundingClientRect();
					const vu = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
					return vu === el || el.contains(vu);
				};
				return {
					ecartX: Math.abs(champ.left + champ.width / 2 - (anneaux.left + anneaux.width / 2)),
					ecartY: Math.abs(champ.top + champ.height / 2 - (anneaux.top + anneaux.height / 2)),
					cache: ['.hero h1', '.acc-p', '.acc-champ .cs-input', '.hero-suite a'].filter((s) => !dessus(document.querySelector(s)!)),
					faits: [...document.querySelectorAll('.fait')].filter((f) => f.getBoundingClientRect().width > 0 && !dessus(f)).length
				};
			});
			expect(peint.ecartX, 'anneaux centrés sur le champ').toBeLessThanOrEqual(1);
			expect(peint.ecartY, 'anneaux centrés sur le champ').toBeLessThanOrEqual(1);
			expect(peint.cache, 'rien de la tête sous les anneaux').toEqual([]);
			expect(peint.faits, 'aucun fait sous les anneaux').toBe(0);
			// The field in focus leaves the head under the example: only its suggestions may pass over the card.
			await page.getByRole('main').getByRole('combobox', { name: 'Adresse, commune ou code postal' }).focus();
			const titreExemple = page.locator('#ex-titre');
			await titreExemple.scrollIntoViewIfNeeded();
			const carteDevant = await titreExemple.evaluate((titre) => {
				const r = titre.getBoundingClientRect();
				const vu = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
				return vu === titre || titre.contains(vu);
			});
			expect(carteDevant, 'la carte d’exemple reste devant la tête, champ actif').toBe(true);
			// Links in a sentence or alone under a block are underlined and in accent (UI-SPEC § 3.0): the legend
			// link, the sources of the landmarks, the actions of the publics, the method links.
			const accent = await page.evaluate(() => {
				const sonde = document.createElement('span');
				sonde.style.color = 'var(--color-accent)';
				document.querySelector('.public-site')!.appendChild(sonde);
				const couleur = getComputedStyle(sonde).color;
				sonde.remove();
				return couleur;
			});
			for (const lien of await page.locator('.acc-lien a, .pros a, .home-method-links a').all()) {
				const style = await lien.evaluate((a) => ({ ligne: getComputedStyle(a).textDecorationLine, couleur: getComputedStyle(a).color }));
				expect(style, (await lien.textContent()) ?? '').toEqual({ ligne: 'underline', couleur: accent });
			}
			const main = await page.getByRole('main').innerText();
			expect(main).not.toContain('Gratuit, sans compte, sans publicité.');
			expect(main).not.toMatch(/\bVigilance\b|\bFavorable\b|\bConforme\b/);
			// The counts of the example are one address, dated: D-34 holds everywhere else.
			const horsExemple = await page.getByRole('main').evaluate((el) => {
				const copie = el.cloneNode(true) as HTMLElement;
				copie.querySelectorAll('[data-exemple]').forEach((bloc) => bloc.remove());
				return copie.textContent ?? '';
			});
			expect(horsExemple).not.toMatch(/\d+\s+(documents?\s+DPE|ventes?|diagnostics?)\b/i);
			await verifierQualite(page, `/ à ${largeur} px`);
			await capturer(page, testInfo, 'accueil', largeur);
		}
		expect(requetesApi).toEqual([]);
	});

	// Ianis, 11/11: three examples (large city, mid-sized town, village) behind three buttons. A choice asks
	// nothing of the network and moves nothing: the page and the card keep their height, to the pixel.
	test('accueil : trois exemples au choix, sans requête ni saut de mise en page', async ({ page }, testInfo) => {
		const EXEMPLES = [
			{ bouton: /^Grande ville\s*·?\s*Toulouse$/, adresse: '8 rue de Metz, Toulouse', fiche: '/adresse/31555_5904_00008', commune: '/commune/31555', ville: 'Toulouse' },
			{ bouton: /^Ville moyenne\s*·?\s*Quimper$/, adresse: '24 rue du Parc, Quimper', fiche: '/adresse/29232_4750_00024', commune: '/commune/29232', ville: 'Quimper' },
			{ bouton: /^Village\s*·?\s*Barfleur$/, adresse: '42 rue Saint Thomas Becket, Barfleur', fiche: '/adresse/50030_bys9nl_00042', commune: '/commune/50030', ville: 'Barfleur' }
		];
		const requetes: string[] = [];
		for (const largeur of LARGEURS) {
			// The buttons answer once the page is hydrated: the root layout reads the session then.
			const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
			await ouvrir(page, '/', largeur);
			await hydratee;
			const groupe = page.getByRole('group', { name: 'Choisir l’exemple' });
			await expect(groupe.getByRole('button')).toHaveCount(3);
			await expect(groupe.getByRole('button', { pressed: true })).toHaveText(EXEMPLES[0].bouton);
			const mesure = () =>
				page.evaluate(() => ({
					page: document.documentElement.scrollHeight,
					carte: Math.round(document.querySelector('section.exemple')!.getBoundingClientRect().height),
					hero: Math.round(document.querySelector('section.hero')!.getBoundingClientRect().height),
					deborde: document.documentElement.scrollWidth > document.documentElement.clientWidth
				}));
			const repos = await mesure();
			expect(repos.deborde, `pas de débordement à ${largeur} px`).toBe(false);
			page.on('request', (requete) => requetes.push(requete.url()));
			for (const i of [1, 2, 0]) {
				const exemple = EXEMPLES[i];
				const bouton = groupe.getByRole('button').nth(i);
				await expect(bouton).toHaveText(exemple.bouton);
				// At the keyboard: the button takes the focus and Enter chooses it.
				await bouton.focus();
				await page.keyboard.press('Enter');
				await expect(bouton).toHaveAttribute('aria-pressed', 'true');
				await expect(groupe.getByRole('button', { pressed: true })).toHaveCount(1);
				const carte = page.getByRole('region', { name: exemple.adresse });
				await expect(carte.getByRole('heading', { level: 2 })).toHaveText(exemple.adresse);
				await expect(carte.getByRole('link', { name: 'Ouvrir cette fiche' })).toHaveAttribute('href', exemple.fiche);
				await expect(carte.getByRole('link')).toHaveCount(5);
				const commencer = page.getByRole('region', { name: 'Pour commencer' });
				await expect(commencer.getByRole('link', { name: `Ouvrir la fiche de ${exemple.ville}` })).toHaveAttribute('href', exemple.commune);
				await expect(commencer.getByRole('link', { name: /^Ouvrir la fiche de / })).toHaveCount(1);
				for (const lien of await commencer.locator('.usages a').all()) expect(await lien.getAttribute('href')).toContain(`${exemple.fiche}#critere-`);
				expect(await mesure(), `${exemple.ville} à ${largeur} px`).toEqual(repos);
				await verifierQualite(page, `/ exemple ${exemple.ville} à ${largeur} px`);
				await capturer(page, testInfo, `accueil-exemple-${exemple.ville.toLowerCase()}`, largeur);
			}
			page.removeAllListeners('request');
		}
		expect(requetes, 'un choix d’exemple ne demande rien au réseau').toEqual([]);
	});

	test('accueil sans JavaScript : le premier exemple est servi entier, les boutons de choix ne s’affichent pas', async ({ browser }) => {
		const contexte = await browser.newContext({ javaScriptEnabled: false });
		const page = await contexte.newPage();
		try {
			await page.goto('/');
			await expect(page.locator('#ex-titre')).toHaveText('8 rue de Metz, Toulouse');
			await expect(page.locator('.exemple a.acc-bouton')).toHaveAttribute('href', '/adresse/31555_5904_00008');
			await expect(page.getByText('Ces chiffres sont figés')).toBeVisible();
			await expect(page.locator('.exemple .ex-corps:not(.cache) a.rangee')).toHaveCount(4);
			await expect(page.locator('.exemple .ex-corps.cache').first()).toBeHidden();
			await expect(page.locator('.choix-exemple')).toBeHidden();
		} finally {
			await contexte.close();
		}
	});

	test('accueil : le champ seul, sans filtre département ; l’affinage reste sur la page de résultats', async ({ page }) => {
		for (const largeur of [1440, 375]) {
			await ouvrir(page, '/', largeur);
			const form = page.getByRole('search', { name: 'Rechercher un lieu' });
			await expect(form).toBeVisible();
			await expect(form.getByText('Affiner par département')).toHaveCount(0);
			await expect(form.locator('select[name="departement"], [name="departement"]')).toHaveCount(0);
			await expect(form.getByRole('combobox', { name: /département/i })).toHaveCount(0);
			await expect(form.locator('details')).toHaveCount(0);
			// The form stays a GET to /communes: the refinement lives on the results page.
			await expect(form).toHaveAttribute('action', '/communes');
			await expect(form).toHaveAttribute('method', 'get');
		}
		const { sonde } = await ouvrir(page, '/communes?q=le%20pin', 1440, sourceRecherche('le pin'));
		await sonde!.assert();
		await expect(page.locator('main select[name="departement"]')).toHaveCount(1);
	});

	test('accueil : aperçu de lien complet (og:*, twitter:*)', async ({ page }) => {
		await ouvrir(page, '/', 1440);
		const canonique = await page.locator('head link[rel="canonical"]').getAttribute('href');
		await verifierApercu(page, { titre: await page.title(), url: canonique });
	});
});

test.describe('page introuvable (UI-SPEC § 6.10)', () => {
	test('ni fil ni recherche d’en-tête ; grand champ et « Pour repartir »', async ({ page }, testInfo) => {
		for (const largeur of LARGEURS) {
			const { reponse } = await ouvrir(page, '/route-inconnue-04-8-07', largeur);
			expect(reponse?.status()).toBe(404);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page introuvable');
			await expect(page.getByRole('navigation', { name: "Fil d'Ariane" })).toHaveCount(0);
			await sansRechercheEnTete(page);
			await expect(page.getByRole('main').getByRole('combobox', { name: 'Chercher une adresse ou une commune' })).toBeVisible();
			const repartir = page.getByRole('region', { name: 'Pour repartir' });
			await expect(repartir.getByRole('link').first()).toHaveAttribute('href', '/');
			// Text written on the page outside the search form: the departement filter lists every French
			// departement (static referential, not the hosted territory, D-34), as in phase42-uat.
			const texte = await page.getByRole('main').evaluate((main) => {
				const clone = main.cloneNode(true) as HTMLElement;
				clone.querySelectorAll('form, script, style').forEach((el) => el.remove());
				return clone.textContent ?? '';
			});
			expect(texte, 'texte vide : le contrôle serait vacuous').toContain('Pour repartir');
			expect(texte).not.toMatch(/Loire-Atlantique|Vendée|\d+\s+(documents?|ventes?)/);
			await verifierQualite(page, `404 à ${largeur} px`);
			await capturer(page, testInfo, '404', largeur);
		}
	});
});
