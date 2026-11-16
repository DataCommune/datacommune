// Home page beyond the search (04.6-01): the search stays the first control, the national landmarks
// are official figures with their date, field and source, and no counter of what the site hosts is
// written (the catalogue is the only source of perimeter figures).
import { fireEvent, render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SITE_URL } from '$lib/config';
import { EXEMPLE_SAISIE, EXEMPLES_FICHES, REPERES_NATIONAUX, USAGES } from '$lib/data/accueil';
import { EXEMPLES_ACCUEIL } from '$lib/data/recherche-lieux';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://fixture/') })
}));

import Page from './+page.svelte';

// Same pattern as tests/e2e/marketing-truth.spec.ts: a figure followed by what a landing counts;
// 04.8-07 (D-34): the counts of DPE documents, sales and diagnostics too.
const COUNTER = /\d[\d\s  ]*\s*(sources?|jeux\s+de\s+données|datasets?|départements?|thèmes|indicateurs?|communes?|documents?\s+DPE|ventes?|diagnostics?)\b/i;
const SOURCES_OFFICIELLES = /^https:\/\/www\.(insee\.fr|statistiques\.developpement-durable\.gouv\.fr)\//;

/** The text of the page outside the example: only a dated reading of one address may hold a count. */
function texteHorsExemple(container: HTMLElement): string {
	const copie = container.cloneNode(true) as HTMLElement;
	copie.querySelectorAll('[data-exemple]').forEach((bloc) => bloc.remove());
	return copie.textContent ?? '';
}

// Independent oracle: the figures as read in the publications on 29/10/2026 (Insee Focus n° 388,
// § 1 ; ONRE/SDES, novembre 2025, p. 1–2). A change here means a new edition was checked by hand.
const PUBLIES = [
	{ valeur: '7,6 %', libelle: /2,9 millions.*38,6 millions/, portee: /1er janvier 2026.*France hors Mayotte.*provisoire/ },
	{ valeur: '33,7 %', libelle: /étiquette D.*8,6 %.*A et B/, portee: /1er janvier 2025.*France métropolitaine.*30,9 millions/ },
	{ valeur: 'Près de 40 %', libelle: /baisse estimée des passoires.*réforme du DPE.*réforme du calcul pour les petites surfaces/, portee: /F ou G.*2024.*2025.*France métropolitaine/ }
];

describe('national landmarks', () => {
	it('match the figures read in the publications', () => {
		expect(REPERES_NATIONAUX.map((r) => r.valeur)).toEqual(PUBLIES.map((p) => p.valeur));
		REPERES_NATIONAUX.forEach((repere, index) => {
			// 4.9 lot 1: the published details left the label for the note, on the same tile.
			expect(`${repere.libelle} · ${repere.portee}`).toMatch(PUBLIES[index].libelle);
			expect(repere.portee).toMatch(PUBLIES[index].portee);
		});
	});

	it('each landmark carries a value, a reference date, a field and an official https source', () => {
		expect(REPERES_NATIONAUX).toHaveLength(3);
		for (const repere of REPERES_NATIONAUX) {
			expect(repere.valeur).toMatch(/\d/);
			expect(repere.portee).toMatch(/1er janvier 20\d\d/);
			expect(repere.portee).toMatch(/France/);
			expect(repere.source).toMatch(/20\d\d$/);
			expect(repere.url).toMatch(SOURCES_OFFICIELLES);
		}
	});

	it('says « document » nowhere it means a dwelling and counts nothing the site hosts', () => {
		const texte = [...REPERES_NATIONAUX, ...USAGES].map((item) => Object.values(item).join(' ')).join('\n');
		expect(texte).not.toMatch(/logements?\s+class(é|e)s?\s+F/i);
		expect(texte.match(COUNTER)?.[0] ?? null).toBeNull();
	});
});

describe('home page', () => {
	// Charter of 06/11 (amends UI-SPEC § 6.6): the search, one dated example, the uses, the scales, the method,
	// the landmarks. « Chaque chiffre dit jusqu’où il vaut » holds the scale marks (arbitration A6).
	it('keeps the search first, then the example, the uses, the scales, the method and the landmarks', () => {
		const { container, getByRole } = render(Page);
		const form = container.querySelector('form[action="/communes"]');
		const titres = [...container.querySelectorAll('h2:not(.source-panel-title)')].map((h) => h.textContent?.trim());
		expect(form).not.toBeNull();
		// The three examples are stacked in one place, one shown: each keeps its address as its title.
		expect(titres).toEqual([...EXEMPLES_FICHES.map((e) => e.adresse), 'Pour commencer', 'Chaque chiffre dit jusqu’où il vaut', 'D’où viennent les chiffres', 'Repères nationaux']);
		const reperes = getByRole('region', { name: 'Repères nationaux' });
		expect(form!.compareDocumentPosition(reperes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('the example says it is a frozen reading, dated, and opens the real fiche', () => {
		const { getByRole } = render(Page);
		const [premier] = EXEMPLES_FICHES;
		const exemple = getByRole('region', { name: premier.adresse });
		const montre = exemple.querySelector('.ex-corps:not(.cache)')!;
		// Said once for the three examples, with the one action of the card.
		expect(exemple.textContent?.match(/Ces chiffres sont figés/g)).toHaveLength(1);
		expect(exemple.textContent).toContain(`lue le ${premier.date}`);
		expect([...exemple.querySelectorAll('.acc-bouton')].map((a) => a.getAttribute('href'))).toEqual([premier.href]);
		// One row per category of the fiche, under its title, leading to its anchor.
		const rangees = [...montre.querySelectorAll('a.rangee')];
		expect(rangees.map((a) => a.querySelector('.rangee-nom')?.textContent)).toEqual(['Prix et ventes', 'Logement et DPE', 'Autour de l’adresse', 'Risques']);
		expect(rangees.map((a) => a.getAttribute('href'))).toEqual(['q-prix', 'q-logement', 'q-quotidien', 'q-risques'].map((id) => `${premier.href}#${id}`));
	});

	// 4.9 lot 1 (C2): a label reads on its own, a figure never ends a sentence that started before it.
	it('national landmarks: each label is a noun phrase that stands without its figure', () => {
		const { container } = render(Page);
		const libelles = [...container.querySelectorAll('.home-reperes > .cle > dt')].map((dt) => dt.textContent!.trim());
		expect(libelles).toHaveLength(3);
		for (const libelle of libelles) {
			expect(libelle).toMatch(/^\p{Lu}/u);
			expect(libelle).not.toMatch(/[.,]$/);
		}
		// What the sentences said is behind the « i » of the tile.
		const tuiles = container.querySelector('.home-reperes')!.textContent!;
		for (const reste of ['2,9 millions', '8,6 %', 'petites surfaces']) expect(tuiles).toContain(reste);
	});

	it('head: the h1 and lead chosen by Ianis, the large field, no map and no promise (D-15, D-16)', () => {
		const { container, getByRole } = render(Page);
		expect(getByRole('heading', { level: 1 }).textContent).toBe('Une adresse, en clair');
		expect(container.querySelector('.acc-p')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'Chaque chiffre vient d’une source publique et dit jusqu’où il vaut : le point de l’adresse, le quartier ou toute la commune.'
		);
		expect(container.querySelector('form.cs-grand[action="/communes"]')).not.toBeNull();
		expect(container.querySelector('label')?.textContent).toContain('Adresse, commune ou code postal');
		expect(container.querySelectorAll('img, figure, canvas')).toHaveLength(0);
		expect(container.textContent).not.toContain('Gratuit, sans compte, sans publicité');
		expect(container.textContent).not.toContain('Observés le');
		expect(container.textContent).not.toMatch(/\bVigilance\b|\bFavorable\b|\bConforme\b/);
	});

	it('« Pour commencer » lists the uses, then the generic entries, each a whole link ending with its arrow', () => {
		const { getByRole } = render(Page);
		const section = getByRole('region', { name: 'Pour commencer' });
		expect([...section.querySelectorAll('.usages a')].map((a) => [a.querySelector('h3')?.textContent, a.getAttribute('href')])).toEqual(
			USAGES.map((usage) => [usage.titre, `${EXEMPLES_FICHES[0].href}${usage.ancre}`])
		);
		const liens = [...section.querySelectorAll('.entrees a')];
		expect(liens.map((a) => [a.querySelector('.ex-t')?.textContent, a.getAttribute('href')])).toEqual(
			EXEMPLES_ACCUEIL.filter((exemple) => exemple.href !== '/datasets#lire').map((exemple) => [exemple.label, exemple.href])
		);
		expect(liens).toHaveLength(3);
		for (const lien of [...section.querySelectorAll('.usages a'), ...liens]) {
			expect(lien.querySelector('.fleche')?.textContent).toBe('→');
			expect(lien.querySelector('.fleche')?.getAttribute('aria-hidden')).toBe('true');
		}
		expect(section.textContent).not.toMatch(/\d/);
	});

	it('« Chaque chiffre dit jusqu’où il vaut »: five scale marks with their sentence, then the link to the legend', () => {
		const { getByRole } = render(Page);
		const section = getByRole('region', { name: 'Chaque chiffre dit jusqu’où il vaut' });
		const entrees = [...section.querySelectorAll('ol.portees > li')];
		expect(entrees.map((li) => li.querySelector('h3')?.textContent?.trim())).toEqual(['Adresse', 'Parcelle', 'Autour', 'Quartier IRIS', 'Commune']);
		for (const li of entrees) {
			expect(li.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
			expect(li.textContent?.length).toBeGreaterThan(30);
		}
		expect(section.querySelector('a[href="/datasets#lire"]')?.textContent).toBe('Comment lire une fiche : les échelles et les états');
		expect(section.textContent).not.toMatch(/Nantes|Luçon|Essais|Loire-Atlantique|Vendée/);
	});

	// 4.9 lot 9: one way to the legend in the content, under the scale marks; the footer keeps its own.
	it('says « Comment lire une fiche » once in the content', () => {
		const { container } = render(Page);
		expect(container.textContent?.match(/Comment lire une fiche/g)).toHaveLength(1);
		expect(container.querySelectorAll('a[href="/datasets#lire"]')).toHaveLength(1);
	});

	it('renders each landmark with its date and a link to its publication', () => {
		const { container } = render(Page);
		const items = container.querySelectorAll('dl.home-reperes .cle');
		expect(items).toHaveLength(REPERES_NATIONAUX.length);
		items.forEach((item, index) => {
			const repere = REPERES_NATIONAUX[index];
			expect(item.textContent).toContain(repere.valeur);
			expect(item.textContent).toContain(repere.libelle);
			expect(item.textContent).toContain(repere.portee);
			expect(item.querySelector('a')?.getAttribute('href')).toBe(repere.url);
		});
		expect(container.textContent).toContain('non calculés par DataCommune');
	});

	it('keeps every internal href unique but the directory', () => {
		const { container } = render(Page);
		const internes = [...container.querySelectorAll('a[href^="/"]')].map((a) => a.getAttribute('href'));
		// The directory is linked twice: under the field, for who has no address, and from the entries.
		expect(internes.filter((href) => href === '/communes')).toHaveLength(2);
		const autres = internes.filter((href) => href !== '/communes');
		expect(new Set(autres).size).toBe(autres.length);
		const annuaire = container.querySelector('ul.entrees a[href="/communes"] .ex-t');
		expect(annuaire?.textContent).toBe('Parcourir l’annuaire des communes');
		expect(container.querySelector('a[href="/datasets#couverture-titre"]')).not.toBeNull();
	});

	// D-34: a count of what the site hosts. The figures of the example are one address, dated, and said so.
	it('writes no counter of hosted territories or datasets outside the dated example', () => {
		const { container } = render(Page);
		expect(container.textContent?.match(COUNTER)?.[0], 'the check reads the example too').toBeTruthy();
		expect(texteHorsExemple(container).match(COUNTER)?.[0] ?? null).toBeNull();
	});
});

// Ianis, 11/11: three examples of different profiles instead of one, so the page shows the site is national.
describe('home page — three examples to choose from', () => {
	const codeDepartement = (insee: string) => insee.slice(0, 2);

	it('every example has the same shape: same facts, tiles and rows, in the same order', () => {
		expect(EXEMPLES_FICHES).toHaveLength(3);
		const forme = (e: (typeof EXEMPLES_FICHES)[number]) => ({
			faits: e.faits.map((f) => f.pictogramme),
			tuiles: e.tuiles.map((t) => [t.partie, t.libelle, t.echelle, t.valeurs.map((v) => v.qui), Boolean(t.classe)]),
			lignes: e.lignes.map((l) => l.partie)
		});
		const [premier, ...autres] = EXEMPLES_FICHES;
		expect(forme(premier).faits).toHaveLength(6);
		expect(forme(premier).tuiles).toHaveLength(3);
		expect(forme(premier).lignes).toEqual(['q-prix', 'q-logement', 'q-quotidien', 'q-risques']);
		for (const autre of autres) expect(forme(autre), autre.adresse).toEqual(forme(premier));
		// The first four facts say the same thing of each place; the fifth carries the date of its last sale.
		for (const autre of autres) {
			expect(autre.faits.map((f) => f.libelle.replace(/dernière en .*/, '')), autre.adresse).toEqual(premier.faits.map((f) => f.libelle.replace(/dernière en .*/, '')));
		}
	});

	it('each example is one real fiche of its own commune, dated, in three departments, none by default in Nantes', () => {
		for (const e of EXEMPLES_FICHES) {
			expect(e.href, e.adresse).toMatch(new RegExp(`^/adresse/${e.insee}_[0-9a-z]+_\\d{5}$`));
			expect(e.commune, e.adresse).toBe(`/commune/${e.insee}`);
			expect(e.adresse.endsWith(`, ${e.ville}`), e.adresse).toBe(true);
			expect(e.date, e.adresse).toMatch(/^\d{1,2}(er)? \p{L}+ 20\d\d$/u);
			for (const ligne of e.lignes) expect(Number.isInteger(ligne.vigilances) && ligne.vigilances >= 0, ligne.resume).toBe(true);
		}
		expect(new Set(EXEMPLES_FICHES.map((e) => e.id)).size).toBe(3);
		expect(new Set(EXEMPLES_FICHES.map((e) => codeDepartement(e.insee))).size).toBe(3);
		expect(EXEMPLES_FICHES.map((e) => e.profil)).toEqual(['Grande ville', 'Ville moyenne', 'Village']);
		expect(JSON.stringify(EXEMPLES_FICHES)).not.toMatch(/Nantes|44109/);
	});

	// The greyed text of the search field is the first example as one types it: its street, then its postal code.
	it('the typing example of the search field is the street of the first example', () => {
		const [rue] = EXEMPLES_FICHES[0].adresse.split(', ');
		expect(EXEMPLE_SAISIE).toBe('8 rue de Metz 31000');
		expect(EXEMPLE_SAISIE.startsWith(`${rue} `)).toBe(true);
		expect(EXEMPLE_SAISIE.slice(rue.length + 1)).toMatch(new RegExp(`^${EXEMPLES_FICHES[0].insee.slice(0, 2)}[0-9]{3}$`));
	});

	it('three buttons, one pressed: the first example is the one rendered at load', () => {
		const { getByRole } = render(Page);
		const groupe = getByRole('group', { name: 'Choisir l’exemple' });
		const boutons = [...groupe.querySelectorAll('button')];
		expect(boutons.map((b) => b.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['Grande ville · Toulouse', 'Ville moyenne · Quimper', 'Village · Barfleur']);
		expect(boutons.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false']);
		for (const bouton of boutons) expect(bouton.getAttribute('type')).toBe('button');
		expect(getByRole('region', { name: EXEMPLES_FICHES[0].adresse })).toBeTruthy();
	});

	it('choosing another example changes what is shown and every link that leads to it', async () => {
		const { container, getByRole } = render(Page);
		const montre = () => {
			const corps = [...container.querySelectorAll('.exemple .ex-corps')];
			expect(corps).toHaveLength(3);
			const visibles = corps.filter((c) => !c.classList.contains('cache'));
			expect(visibles).toHaveLength(1);
			return visibles[0];
		};
		const etat = () => ({
			titre: container.querySelector('#ex-titre')?.textContent,
			fiche: [...container.querySelectorAll('.exemple .acc-bouton')].map((a) => a.getAttribute('href')).join(' '),
			titres: [...container.querySelectorAll('.exemple h2:not(.cache)')].map((h) => h.textContent).join(' | '),
			rangees: [...montre().querySelectorAll('a.rangee')].map((a) => a.getAttribute('href')),
			faits: [...container.querySelectorAll('.faits .fait b')].map((b) => b.textContent),
			note: container.querySelector('.note-exemple > :not(.cache)')?.textContent?.replace(/\s+/g, ' ').trim(),
			usages: [...container.querySelectorAll('.usages a')].map((a) => a.getAttribute('href')),
			commune: [...container.querySelectorAll('.pros a:not(.cache)')].map((a) => [a.textContent, a.getAttribute('href')])
		});
		const attendu = (e: (typeof EXEMPLES_FICHES)[number]) => ({
			titre: e.adresse,
			fiche: e.href,
			titres: e.adresse,
			rangees: e.lignes.map((l) => `${e.href}#${l.partie}`),
			faits: e.faits.map((f) => f.valeur),
			note: `Autour du champ, un exemple réel : ${e.adresse}, lu le ${e.date}.`,
			usages: USAGES.map((u) => `${e.href}${u.ancre}`),
			commune: [[`Ouvrir la fiche de ${e.ville}`, e.commune]]
		});
		expect(etat()).toEqual(attendu(EXEMPLES_FICHES[0]));
		for (const i of [1, 2, 0]) {
			const e = EXEMPLES_FICHES[i];
			// By its place in the group: the name computed by the test DOM drops the spaces around the separator.
			const bouton = getByRole('group', { name: 'Choisir l’exemple' }).querySelectorAll('button')[i];
			expect(bouton.textContent).toBe(`${e.profil} · ${e.ville}`);
			await fireEvent.click(bouton);
			expect(etat(), e.adresse).toEqual(attendu(e));
			const presses = [...getByRole('group', { name: 'Choisir l’exemple' }).querySelectorAll('button')].map((b) => b.getAttribute('aria-pressed'));
			expect(presses, e.adresse).toEqual(EXEMPLES_FICHES.map((_, j) => String(j === i)));
			expect(getByRole('region', { name: e.adresse })).toBeTruthy();
		}
	});

	it('each row counts its vigilances as the fiche does, « à l’adresse ou autour »', () => {
		const { container } = render(Page);
		const tous = [...container.querySelectorAll('.exemple .ex-corps')];
		expect(tous).toHaveLength(3);
		tous.forEach((corps, i) => {
			const comptes = [...corps.querySelectorAll('a.rangee')].map((a) => a.querySelector('.rangee-compte')?.textContent?.replace(/\s+/g, ' ').trim() ?? '');
			expect(comptes, EXEMPLES_FICHES[i].adresse).toEqual(
				EXEMPLES_FICHES[i].lignes.map((l) => (l.vigilances ? `${l.vigilances} ${l.vigilances > 1 ? 'vigilances' : 'vigilance'} à l’adresse ou autour` : ''))
			);
		});
	});
});

// 04.7-10, decision 8: the home page says who it is to a search engine. A canonical to the root, the
// WebSite (name, absolute url, language) with the SearchAction of the form of the page, and a visible
// way into the department pages. Google no longer draws a search box from the SearchAction (it
// dropped it in November 2024): the markup is kept for the site name and for other readers.
describe('home page — canonical, WebSite and departments (04.7-10)', () => {
	afterEach(() => {
		document.head.innerHTML = '';
	});
	const jsonLd = () =>
		[...document.head.querySelectorAll('script[type="application/ld+json"]')].map((script) => JSON.parse(script.textContent ?? '{}'));

	it('is canonical to the root of the site, absolute', () => {
		render(Page);
		const canoniques = document.head.querySelectorAll('link[rel="canonical"]');
		expect(canoniques).toHaveLength(1);
		expect(canoniques[0].getAttribute('href')).toBe(`${SITE_URL}/`);
		expect(canoniques[0].getAttribute('href')).toMatch(/^https:\/\/[^/]+\/$/);
	});

	it('declares one WebSite named DataCommune, in French, with an absolute url', () => {
		render(Page);
		const sites = jsonLd().filter((objet) => objet['@type'] === 'WebSite');
		expect(sites).toHaveLength(1);
		expect(sites[0]).toMatchObject({
			'@context': 'https://schema.org',
			name: 'DataCommune',
			url: `${SITE_URL}/`,
			inLanguage: 'fr-FR'
		});
	});

	it('gives the SearchAction the absolute target of the form of the page, q being its field', () => {
		const { container } = render(Page);
		const [site] = jsonLd().filter((objet) => objet['@type'] === 'WebSite');
		expect(site.potentialAction).toEqual({
			'@type': 'SearchAction',
			target: `${SITE_URL}/communes?q={search_term_string}`,
			'query-input': 'required name=search_term_string'
		});
		const formulaire = container.querySelector('form[action="/communes"]')!;
		expect(formulaire.getAttribute('method')?.toLowerCase()).toBe('get');
		expect(formulaire.querySelector('[name="q"]')).not.toBeNull();
	});

	it('links the department list from the entries, after the directory', () => {
		const { container } = render(Page);
		const liens = [...container.querySelectorAll('ul.entrees a')].map((a) => [a.querySelector('.ex-t')?.textContent, a.getAttribute('href')]);
		expect(liens.slice(0, 2)).toEqual([
			['Parcourir l’annuaire des communes', '/communes'],
			['Parcourir les départements', '/departements']
		]);
	});
});
