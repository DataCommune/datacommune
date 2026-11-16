import type { Page } from '@playwright/test';
import { test, expect, verifierGraisses, verifierMesure, verifierPlancherTypo } from './fixtures/quality';

// 04.8-01 Task 2 — the three rendered checks of the v4 type contract (UI-SPEC § 2.2 and § 2.6
// n° 8; D-03, D-03b, D-35), each proven red then green on a test block mounted inside the real
// `.public-site` of a public page, so that the real app.css applies:
//   - verifierPlancherTypo: no visible text node under 14 px;
//   - verifierGraisses: computed weight Inter ∈ {400, 600}, Newsreader ∈ {400, 500}; `strong`, `b`
//     and `th` render 600 in an Inter context and 500 in a Newsreader one (app.css);
//   - verifierMesure: after `document.fonts.ready`, the rendered content width of a paragraph is
//     at most the width of 60ch in its own font (substitute of the 75-character rule).
// The specs of the public pages call the same functions on their real content.

const HOTE = 'contrat-typo-essai';
const PORTEE = `#${HOTE}`;
const LONG =
	'Les chiffres de cette fiche viennent des sources publiques citées en bas de page, sans calcul de notre part, et chaque valeur garde sa date.';

// The blocks are mounted on the page as served, without JavaScript. With it, the app hydrates after
// the first block is posed: the block is a node the server did not render, so Svelte reports
// hydration_mismatch, remounts the app and drops it, and the session read that follows (/auth/me, 401
// for a visitor) logs a console error that the quality guard refuses. Either made a long test flaky.
test.use({ javaScriptEnabled: false });

async function ouvrirPagePublique(page: Page) {
	const sessions: string[] = [];
	page.on('request', (requete) => {
		if (new URL(requete.url()).pathname === '/auth/me') sessions.push(requete.url());
	});
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/mentions-legales');
	await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
	await expect(page.locator('.public-site')).toHaveCount(1);
	// No hydration: the root layout would read the session once hydrated.
	expect(sessions).toEqual([]);
}

/** Mount (or replace) the test block at the end of `.public-site`, 1000 px wide. */
async function poser(page: Page, html: string) {
	await page.evaluate(
		({ id, contenu }) => {
			document.getElementById(id)?.remove();
			const hote = document.createElement('div');
			hote.id = id;
			hote.style.width = '1000px';
			hote.innerHTML = contenu;
			document.querySelector('.public-site')!.append(hote);
		},
		{ id: HOTE, contenu: html }
	);
}

test.beforeEach(async ({ page }) => {
	await ouvrirPagePublique(page);
});

test('plancher de 14 px : un texte à 13 px est une infraction, à 14 px aucune', async ({ page }) => {
	await poser(page, '<p style="font-size: 13px">Texte de métadonnées à treize pixels</p>');
	const rouge = await verifierPlancherTypo(page, PORTEE);
	expect(rouge).toHaveLength(1);
	expect(rouge[0]).toContain('13px');
	expect(rouge[0]).toContain('Texte de métadonnées');

	await poser(page, '<p style="font-size: 14px">Texte de métadonnées à quatorze pixels</p>');
	expect(await verifierPlancherTypo(page, PORTEE)).toEqual([]);
});

test('plancher de 14 px : un texte masqué n’est pas lu, un texte visible sous un parent à 13 px l’est', async ({ page }) => {
	await poser(
		page,
		'<p style="font-size: 12px; display: none">caché</p><div style="font-size: 13px"><span>hérité</span></div>'
	);
	expect(await verifierPlancherTypo(page, PORTEE)).toEqual([expect.stringContaining('hérité')]);
});

// Opus, P3-14: text that is not a text node is read too — the value of a field, its placeholder while it
// shows, and the text of a ::before or ::after.
test('plancher de 14 px : la valeur et le texte indicatif d’un champ, le texte d’un pseudo-élément sont lus', async ({ page }) => {
	const cas: [string, string][] = [
		['<input value="Valeur saisie à treize" style="font-size: 13px">', 'Valeur saisie à treize'],
		['<textarea style="font-size: 13px">Zone de texte à treize</textarea>', 'Zone de texte à treize'],
		[
			`<style>${PORTEE} input::placeholder { font-size: 13px; }</style><input placeholder="Indicatif à treize" style="font-size: 16px">`,
			'Indicatif à treize'
		],
		[`<style>${PORTEE} .avant::before { content: "Avant à treize"; font-size: 13px; }</style><p class="avant">x</p>`, 'Avant à treize'],
		[`<style>${PORTEE} .apres::after { content: "Après à treize"; font-size: 13px; }</style><p class="apres">x</p>`, 'Après à treize']
	];
	for (const [html, texte] of cas) {
		await poser(page, html);
		expect(await verifierPlancherTypo(page, PORTEE), html).toEqual([expect.stringContaining(texte)]);
		expect(await verifierPlancherTypo(page, PORTEE), html).toEqual([expect.stringContaining('13px')]);
		// The same at 14 px passes.
		await poser(page, html.replaceAll('13px', '14px'));
		expect(await verifierPlancherTypo(page, PORTEE), html).toEqual([]);
	}
	// Not read: a hidden field, a placeholder hidden by a value, an empty or decorative pseudo-element.
	await poser(
		page,
		`<style>${PORTEE} input::placeholder { font-size: 13px; } ${PORTEE} .deco::before { content: ""; font-size: 13px; } ` +
			`${PORTEE} .masque::after { content: "masqué"; font-size: 13px; display: none; }</style>` +
			'<input type="hidden" value="caché" style="font-size: 13px">' +
			'<input value="Valeur à seize" placeholder="Indicatif masqué" style="font-size: 16px">' +
			'<input value="hors de la page" style="font-size: 13px; display: none">' +
			'<p class="deco">Décor vide</p><p class="masque">Pseudo masqué</p>'
	);
	expect(await verifierPlancherTypo(page, PORTEE)).toEqual([]);
});

test('graisses : Inter 500 et un strong à 700 sont des infractions ; Inter 600 et Newsreader 500 passent', async ({ page }) => {
	await poser(page, '<p style="font-weight: 500">Inter en graisse cinq cents</p>');
	const inter500 = await verifierGraisses(page, PORTEE);
	expect(inter500).toHaveLength(1);
	expect(inter500[0]).toContain('Inter 500');

	await poser(page, '<p>Une phrase avec <strong style="font-weight: 700">un gras à 700</strong>.</p>');
	expect(await verifierGraisses(page, PORTEE)).toEqual([expect.stringContaining('un gras à 700')]);

	await poser(
		page,
		'<p style="font-weight: 600">Inter en six cents</p>' +
			'<p style="font-family: var(--font-display); font-weight: 500">Newsreader en cinq cents</p>'
	);
	expect(await verifierGraisses(page, PORTEE)).toEqual([]);
});

test('graisses : strong rend en 600 dans Inter et en 500 dans un titre Newsreader', async ({ page }) => {
	await poser(
		page,
		'<h2>Un titre avec <strong id="fort-titre">un mot fort</strong></h2>' +
			'<p>Un paragraphe avec <strong id="fort-corps">un mot fort</strong> et <b id="gras-corps">un gras</b>.</p>' +
			'<table><thead><tr><th id="entete">Colonne</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>'
	);
	const calcule = await page.evaluate(() =>
		Object.fromEntries(
			['fort-titre', 'fort-corps', 'gras-corps', 'entete'].map((id) => {
				const style = getComputedStyle(document.getElementById(id)!);
				return [id, `${style.fontFamily.split(',')[0].replace(/["']/g, '').trim()} ${style.fontWeight}`];
			})
		)
	);
	expect(calcule).toEqual({
		'fort-titre': 'Newsreader 500',
		'fort-corps': 'Inter 600',
		'gras-corps': 'Inter 600',
		entete: 'Inter 600'
	});
	expect(await verifierGraisses(page, PORTEE)).toEqual([]);
});

test('mesure : un paragraphe sans max-width dépasse 60ch, à max-width: var(--mesure) il passe', async ({ page }) => {
	await poser(page, `<p>${LONG}</p>`);
	const rouge = await verifierMesure(page, PORTEE, ['p']);
	expect(rouge).toHaveLength(1);
	expect(rouge[0]).toContain('60ch');

	await poser(page, `<p style="max-width: var(--mesure)">${LONG}</p>`);
	expect(await verifierMesure(page, PORTEE, ['p'])).toEqual([]);
});
