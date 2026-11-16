// The shared pieces of the public site (socle of 2026-11-09): the button and the fold every page copied on
// its side now live in `app.css`. Headless runner, isolated context, the synthetic source of the project,
// every off-local request refused (AGENTS.md).
//
// No page wears these classes yet: each test adds, beside the element a page draws today, the same element
// under the shared class, and compares what the browser computes for both. What this spec locks:
//   - `.bouton-primaire` and `.bouton-secondaire` render as the reference buttons of the commune fiche
//     (« Comparer avec une autre commune », « Télécharger (CSV) »): box, colours, weight, hover and focus
//     ring, on a link and on a button alike; `.bouton-lien` as its text action (« Copier le code INSEE »);
//   - `.depliant` renders as the fold of the commune fiche (its questions), `.depliant-accent` as the fold
//     of the comparator, `.depliant-cadre` as the framed fold of the directory; each has its chevron, a
//     44 px target and the focus ring of the contract.
import { test, expect, type Locator, type Page } from '@playwright/test';
import { expectNoUnexpectedOffLocalRequest, installLocalNetworkGuard } from './fixtures/local-network';
import { configureSsrSource } from './fixtures/bien-ssr-source';
import { PAGES_PUBLIQUES, type PagePublique } from './fixtures/pages-publiques';

const cible = (id: string) => PAGES_PUBLIQUES.find((page) => page.id === id)!;

async function ouvrir(page: Page, publique: PagePublique, largeur: number) {
	await configureSsrSource(page.request, publique.source());
	await page.setViewportSize({ width: largeur, height: 900 });
	const hydratee = page.waitForResponse((reponse) => new URL(reponse.url()).pathname === '/auth/me');
	await page.goto(publique.chemin);
	await hydratee;
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
}

const BOITE = [
	'display', 'alignItems', 'justifyContent', 'boxSizing', 'width', 'height', 'minHeight',
	'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
	'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'borderTopStyle',
	'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor',
	'borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius',
	'backgroundColor', 'color', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight',
	'textDecorationLine', 'cursor', 'opacity'
] as const;
const ANNEAU = ['outlineStyle', 'outlineWidth', 'outlineColor', 'outlineOffset'] as const;
const SURVOL = ['backgroundColor', 'color', 'borderTopColor', 'textDecorationLine', 'textDecorationThickness'] as const;

const styles = (element: Locator, proprietes: readonly string[], pseudo?: string) =>
	element.evaluate(
		(el, [noms, p]) => {
			const style = getComputedStyle(el, p ?? undefined);
			return Object.fromEntries((noms as string[]).map((nom) => [nom, style[nom as keyof CSSStyleDeclaration] as string]));
		},
		[proprietes, pseudo ?? null] as const
	);

/** Adds, right after `reference`, the same element under `classes` instead of its own; returns it. */
async function jumeau(page: Page, reference: Locator, classes: string): Promise<Locator> {
	const id = `jumeau-${classes.replace(/\s+/g, '-')}`;
	await reference.evaluate(
		(el, [classes, id]) => {
			const copie = el.cloneNode(true) as HTMLElement;
			copie.className = classes;
			copie.id = id;
			for (const attribut of [...copie.attributes]) if (attribut.name.startsWith('data-')) copie.removeAttribute(attribut.name);
			el.after(copie);
		},
		[classes, id] as const
	);
	return page.locator(`#${id}`);
}

/** Focus by the keyboard: the ring is the one of `:focus-visible`. */
async function focusClavier(page: Page, element: Locator) {
	await element.focus();
	await page.keyboard.press('Shift+Tab');
	await page.keyboard.press('Tab');
	await expect(element).toBeFocused();
}

test.describe('socle partagé', () => {
	test.beforeEach(async ({ page }) => {
		await installLocalNetworkGuard(page);
	});
	test.afterEach(async ({ page }) => {
		expectNoUnexpectedOffLocalRequest(page);
	});

	for (const largeur of [1440, 375] as const) {
		test(`boutons à ${largeur} px : les trois rôles rendent comme les boutons de la fiche commune`, async ({ page }) => {
			await ouvrir(page, cible('commune'), largeur);
			const actions = page.locator('main .fiche-actions');
			const paires: Array<[string, Locator, string]> = [
				['primaire, lien', actions.getByRole('link', { name: 'Comparer avec une autre commune' }).first(), 'bouton-primaire'],
				['secondaire, bouton', actions.getByRole('button', { name: 'Télécharger (CSV)' }).first(), 'bouton-secondaire'],
				['secondaire, lien', actions.getByRole('link', { name: /JSON/ }).first(), 'bouton-secondaire'],
				['lien d’action', page.getByRole('button', { name: 'Copier le code INSEE' }).first(), 'bouton-lien']
			];
			for (const [role, reference, classe] of paires) {
				await expect(reference, role).toBeVisible();
				const partage = await jumeau(page, reference, classe);
				// The text action of the fiche is pulled into its line by a side padding and negative margins of its
				// own, and takes the weight and the line of that line: the shared one has no side padding.
				const PROPRES_A_LA_LIGNE = ['width', 'height', 'paddingLeft', 'paddingRight', 'fontWeight', 'lineHeight', 'fontFamily'];
				const proprietes = classe === 'bouton-lien' ? BOITE.filter((p) => !PROPRES_A_LA_LIGNE.includes(p)) : BOITE;
				// At rest: the pointer of the previous pair is out of the way.
				await page.mouse.move(0, 0);
				expect(await styles(partage, proprietes), `${role} : au repos`).toEqual(await styles(reference, proprietes));
				expect((await partage.boundingBox())!.height, `${role} : cible de 44 px`).toBeGreaterThanOrEqual(44);

				await reference.hover();
				const survolReference = await styles(reference, SURVOL);
				await partage.hover();
				// The shared text action darkens and thickens its underline under the pointer, as a link of a sentence
				// does; the fiche's one does not move. It has no border: its border colour only follows its text.
				const survol = classe === 'bouton-lien' ? (['backgroundColor', 'textDecorationLine'] as const) : SURVOL;
				expect(await styles(partage, survol), `${role} : survolé`).toEqual(Object.fromEntries(survol.map((p) => [p, survolReference[p]])));

				await focusClavier(page, reference);
				const anneauReference = await styles(reference, ANNEAU);
				expect(anneauReference.outlineStyle, `${role} : l’anneau de la référence`).toBe('solid');
				await focusClavier(page, partage);
				expect(await styles(partage, ANNEAU), `${role} : au clavier`).toEqual(anneauReference);
				await partage.evaluate((el) => el.remove());
			}
		});
	}

	test('bouton : désactivé il s’efface, occupé il attend, le primaire désactivé devient gris', async ({ page }) => {
		await ouvrir(page, cible('commune'), 1440);
		const reference = page.locator('main .fiche-actions').getByRole('button', { name: 'Télécharger (CSV)' });
		const etats = await reference.evaluate((el) => {
			const lire = (classe: string, occupe: boolean) => {
				const bouton = document.createElement('button');
				bouton.type = 'button';
				bouton.className = classe;
				bouton.disabled = true;
				if (occupe) bouton.setAttribute('aria-busy', 'true');
				bouton.textContent = 'Recharger';
				el.after(bouton);
				const style = getComputedStyle(bouton);
				const lu = { curseur: style.cursor, opacite: style.opacity, fond: style.backgroundColor, texte: style.color };
				bouton.remove();
				return lu;
			};
			const jeton = (nom: string) => {
				const sonde = document.createElement('span');
				sonde.style.color = `var(${nom})`;
				el.after(sonde);
				const couleur = getComputedStyle(sonde).color;
				sonde.remove();
				return couleur;
			};
			return {
				secondaireOccupe: lire('bouton-secondaire', true),
				secondaireDesactive: lire('bouton-secondaire', false),
				primaireDesactive: lire('bouton-primaire', false),
				primaireOccupe: lire('bouton-primaire', true),
				doux: jeton('--color-soft'),
				attenue: jeton('--color-muted'),
				accent: jeton('--color-accent')
			};
		});
		// « Recharger la fiche » of the commune fiche: disabled and busy, it waits at 70 %.
		expect(etats.secondaireOccupe).toMatchObject({ curseur: 'progress', opacite: '0.7' });
		expect(etats.secondaireDesactive).toMatchObject({ curseur: 'not-allowed', opacite: '0.7' });
		expect(etats.primaireOccupe).toMatchObject({ curseur: 'progress', opacite: '0.7', fond: etats.accent });
		expect(etats.primaireDesactive).toEqual({ curseur: 'not-allowed', opacite: '1', fond: etats.doux, texte: etats.attenue });
	});

	const RESUME = [
		'display', 'alignItems', 'columnGap', 'width', 'height', 'minHeight', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
		'color', 'fontSize', 'fontWeight', 'lineHeight', 'listStyleType', 'cursor'
	] as const;
	const CHEVRON = ['content', 'width', 'height', 'borderRightWidth', 'borderRightColor', 'borderBottomWidth', 'borderBottomColor', 'transform'] as const;
	const CADRE = [
		'borderTopWidth', 'borderTopColor', 'borderTopLeftRadius', 'backgroundColor', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'width'
	] as const;

	const REPLIS = [
		{ nom: 'fiche commune', page: 'commune', reference: 'main [data-faq] details.det', classes: 'depliant', resume: RESUME, chevron: CHEVRON, cadre: false },
		{ nom: 'comparateur', page: 'comparer-communes', reference: 'main details.det.comparer-modifier', classes: 'depliant depliant-accent', resume: RESUME, chevron: CHEVRON, cadre: false },
		// The directory draws its chevron 2 px thick in the ink of its text and leaves its summary the line
		// height of the page (1.5): the shared fold keeps the accent chevron and the 1.4 of the two other
		// folds. Same box and same height on one line; only a label that wraps would differ.
		{
			nom: 'annuaire',
			page: 'annuaire',
			reference: 'main details.det',
			classes: 'depliant depliant-cadre',
			resume: RESUME.filter((p) => p !== 'lineHeight'),
			chevron: ['content', 'width', 'height', 'transform'],
			cadre: true
		}
	] as const;

	for (const repli of REPLIS) {
		for (const largeur of [1440, 375] as const) {
			test(`repli à ${largeur} px : « ${repli.classes} » rend comme le repli de ${repli.nom}`, async ({ page }) => {
				await ouvrir(page, cible(repli.page), largeur);
				const reference = page.locator(repli.reference).first();
				await expect(reference).toBeVisible();
				const partage = await jumeau(page, reference, repli.classes);
				const resumes = [reference.locator('> summary'), partage.locator('> summary')] as const;
				for (const ouvert of [false, true]) {
					for (const details of [reference, partage]) await details.evaluate((el, o) => ((el as HTMLDetailsElement).open = o), ouvert);
					// Read once the 150 ms turn of the chevron is over.
					for (const resume of resumes) await resume.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
					const etat = ouvert ? 'ouvert' : 'fermé';
					expect(await styles(resumes[1], repli.resume), `résumé ${etat}`).toEqual(await styles(resumes[0], repli.resume));
					expect(await styles(resumes[1], repli.chevron, '::before'), `chevron ${etat}`).toEqual(await styles(resumes[0], repli.chevron, '::before'));
					if (repli.cadre) expect(await styles(partage, CADRE), `cadre ${etat}`).toEqual(await styles(reference, CADRE));
				}
				const tourne = await styles(resumes[1], ['transform'], '::before');
				for (const details of [reference, partage]) await details.evaluate((el) => ((el as HTMLDetailsElement).open = false));
				await resumes[1].evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
				expect((await styles(resumes[1], ['transform'], '::before')).transform, 'le chevron tourne à l’ouverture').not.toBe(tourne.transform);

				expect((await resumes[1].boundingBox())!.height, 'cible de 44 px').toBeGreaterThanOrEqual(44);
				await focusClavier(page, resumes[1]);
				expect(await styles(resumes[1], ANNEAU)).toMatchObject({ outlineStyle: 'solid', outlineWidth: '3px', outlineOffset: '4px' });
				await focusClavier(page, resumes[0]);
				expect(await styles(resumes[0], ANNEAU), 'le même anneau que la référence').toMatchObject({ outlineStyle: 'solid', outlineWidth: '3px', outlineOffset: '4px' });
			});
		}
	}

	test('repli : sur le papier, le chevron disparaît et le contenu replié s’imprime', async ({ page }) => {
		await ouvrir(page, cible('commune'), 1440);
		const partage = await jumeau(page, page.locator('main [data-faq] details.det').first(), 'depliant');
		await partage.evaluate((el) => ((el as HTMLDetailsElement).open = false));
		await page.emulateMedia({ media: 'print' });
		expect((await styles(partage.locator('> summary'), ['display'], '::before')).display).toBe('none');
		expect(await partage.evaluate((el) => getComputedStyle(el, '::details-content').contentVisibility)).toBe('visible');
	});
});
