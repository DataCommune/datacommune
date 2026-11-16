// Base components of the v4 grammar (04.8-02, UI-SPEC v4 § 3.0-3.2, 3.5, 3.6, 3.10, 3.11): the badge
// says the word statutDe() serves and owns no table of words (D-33); a list link carries its arrow in a
// hidden span, last child of the link; the scale mark is a pictogram always followed by its word, never
// a native tooltip (D-07); the breadcrumb and the panel keep their landmarks. The text button of the
// source (E4) keeps its accessible name.
import { createRawSnippet } from 'svelte';
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { ThemeRapport } from '$lib/api/rapport';
import { LIBELLE_STATUT, statutDe } from '$lib/data/adresse-rapport';
import SourceButton from '$lib/components/fiche/SourceButton.svelte';
import BadgeEtat from './BadgeEtat.svelte';
import FilAriane from './FilAriane.svelte';
import ListeLiens from './ListeLiens.svelte';
import Panneau from './Panneau.svelte';
import RepereEchelle from './RepereEchelle.svelte';
import Tuile from './Tuile.svelte';

/** A served theme, reduced to what statutDe() reads. */
function theme(etat: ThemeRapport['etat'], ton: ThemeRapport['ton'], etatLibelle: string | null = null): ThemeRapport {
	return {
		id: 'bruit',
		section: 'risques',
		titre: 'Bruit des transports',
		etat,
		ton,
		granularite: { niveau: 'adresse', rayon_m: null },
		verdict: '',
		chiffres: [],
		details: {},
		rattachement: null,
		sources: [],
		motif: null,
		etat_libelle: etatLibelle,
		accroche: null,
		pannes_partielles: []
	} as unknown as ThemeRapport;
}

const texte = (html: string) => createRawSnippet(() => ({ render: () => html }));

describe('BadgeEtat — the word of statutDe(), no table of its own (D-11, D-33)', () => {
	const cas: Array<[string, ThemeRapport, string]> = [
		['vigilance', theme('ok', 'vigilance'), LIBELLE_STATUT.vigilance],
		['favorable', theme('ok', 'rassurant'), LIBELLE_STATUT.rassurant],
		['zéro', theme('zero', 'neutre'), LIBELLE_STATUT.zero],
		['panne', theme('panne', 'neutre'), LIBELLE_STATUT.panne],
		['manque, libellé court servi', theme('manque', 'neutre', 'Non publié'), 'Non publié'],
		['hors couverture', theme('hors_couverture', 'neutre'), LIBELLE_STATUT.hors_couverture],
		['sans objet', theme('non_concerne', 'neutre'), LIBELLE_STATUT.non_concerne]
	];

	it.each(cas)('%s: the badge says the served word', (_nom, t, mot) => {
		const statut = statutDe(t);
		expect(statut?.texte).toBe(mot);
		const { container } = render(BadgeEtat, { statut });
		const badge = container.querySelector('.badge');
		expect(badge?.textContent).toBe(mot);
		expect(badge?.classList.contains(`badge-${statut!.cle}`)).toBe(true);
	});

	it('a neutral served state renders no node at all', () => {
		const statut = statutDe(theme('ok', 'neutre'));
		expect(statut).toBeNull();
		const { container } = render(BadgeEtat, { statut });
		expect(container.querySelector('.badge')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('a vigilance in a tile is outlined, in a row it is filled', () => {
		const statut = statutDe(theme('ok', 'vigilance'));
		const tuile = render(BadgeEtat, { statut, zone: 'tuile' }).container.querySelector('.badge')!;
		expect(tuile.classList.contains('badge-contour')).toBe(true);
		expect(tuile.classList.contains('badge-plein')).toBe(false);
		const ligne = render(BadgeEtat, { statut, zone: 'ligne' }).container.querySelector('.badge')!;
		expect(ligne.classList.contains('badge-plein')).toBe(true);
		expect(ligne.classList.contains('badge-contour')).toBe(false);
		// The row is the default zone.
		expect(render(BadgeEtat, { statut }).container.querySelector('.badge')!.classList.contains('badge-plein')).toBe(true);
	});

	it('the zone changes the form of a vigilance only', () => {
		const statut = statutDe(theme('ok', 'rassurant'));
		const badge = render(BadgeEtat, { statut, zone: 'tuile' }).container.querySelector('.badge')!;
		expect(badge.classList.contains('badge-contour')).toBe(false);
		expect(badge.textContent).toBe(LIBELLE_STATUT.rassurant);
	});
});

describe('ListeLiens — whole row, arrow in a hidden span, last child of the link (§ 3.10)', () => {
	const liens = [
		{ href: '/commune/85047', titre: 'Challans', note: 'Commune · Vendée' },
		{ href: '/departements', titre: 'Tous les départements' }
	];

	it.each([
		['page', '→'],
		['ancre', '↓'],
		['externe', '↗']
	] as const)('cible %s: the arrow is %s', (cible, fleche) => {
		const { container } = render(ListeLiens, { liens, cible });
		const ancres = [...container.querySelectorAll('ul.liste-liens > li > a')];
		expect(ancres).toHaveLength(2);
		for (const ancre of ancres) {
			const dernier = ancre.lastElementChild!;
			expect(dernier.tagName).toBe('SPAN');
			expect(dernier.classList.contains('fleche')).toBe(true);
			expect(dernier.getAttribute('aria-hidden')).toBe('true');
			expect(dernier.textContent).toBe(fleche);
			expect(ancre.lastChild).toBe(dernier);
		}
	});

	it('the title and the note are spans of the link; no note, no span', () => {
		const { container } = render(ListeLiens, { liens });
		const [premier, second] = [...container.querySelectorAll('a')];
		expect(premier.getAttribute('href')).toBe('/commune/85047');
		expect(premier.querySelector('.lien-t')?.textContent).toBe('Challans');
		expect(premier.querySelector('.lien-n')?.textContent).toBe('Commune · Vendée');
		expect(second.querySelector('.lien-n')).toBeNull();
		// A page of the site is the default target: same window, no rel.
		expect(premier.getAttribute('rel')).toBeNull();
		expect(premier.querySelector('.fleche')?.textContent).toBe('→');
	});

	it('an external link opens with rel="noopener noreferrer" (T-04.8-05)', () => {
		const { container } = render(ListeLiens, {
			liens: [{ href: 'https://www.data.gouv.fr/', titre: 'data.gouv.fr' }],
			cible: 'externe'
		});
		expect(container.querySelector('a')?.getAttribute('rel')).toBe('noopener noreferrer');
	});
});

describe('RepereEchelle — a pictogram followed by its word, never alone (D-07, § 3.5)', () => {
	const pictogrammes = ['adresse', 'batiment', 'parcelle', 'rayon', 'carreau', 'commune', 'departement'] as const;

	it.each(pictogrammes)('%s: an inline svg hidden from assistive technologies, then the word', (pictogramme) => {
		const { container } = render(RepereEchelle, { pictogramme, mot: 'Adresse' });
		const ech = container.querySelector('span.ech')!;
		const svg = ech.querySelector('svg')!;
		expect(svg.getAttribute('aria-hidden')).toBe('true');
		expect(svg.getAttribute('focusable')).toBe('false');
		expect(svg.getAttribute('viewBox')).toBe('0 0 24 24');
		expect(svg.children.length).toBeGreaterThan(0);
		// The word follows the pictogram.
		expect(svg.nextElementSibling?.textContent).toBe('Adresse');
		// No native tooltip anywhere: it is reachable neither by keyboard nor by touch.
		expect(container.querySelector('[title]')).toBeNull();
		expect(container.querySelector('title')).toBeNull();
	});

	it('20 px by default, 18 px in a key figure', () => {
		const defaut = render(RepereEchelle, { pictogramme: 'commune', mot: 'Commune' }).container.querySelector('svg')!;
		expect(defaut.getAttribute('width')).toBe('20');
		expect(defaut.getAttribute('height')).toBe('20');
		const cle = render(RepereEchelle, { pictogramme: 'commune', mot: 'Commune', taille: 18 }).container.querySelector('svg')!;
		expect(cle.getAttribute('width')).toBe('18');
	});

	it('a word read by screen readers only is a .sr span, never display: none', () => {
		const { container } = render(RepereEchelle, { pictogramme: 'commune', mot: 'Commune', motVisible: false });
		const mot = container.querySelector('svg')!.nextElementSibling!;
		expect(mot.textContent).toBe('Commune');
		expect(mot.classList.contains('sr')).toBe(true);
		const visible = render(RepereEchelle, { pictogramme: 'commune', mot: 'Commune' }).container.querySelector('svg')!.nextElementSibling!;
		expect(visible.classList.contains('sr')).toBe(false);
	});

	it('without a pictogram it renders the word alone', () => {
		const { container } = render(RepereEchelle, { mot: 'Échelle : quartier_inconnu' });
		expect(container.querySelector('svg')).toBeNull();
		expect(container.querySelector('span.ech')?.textContent?.trim()).toBe('Échelle : quartier_inconnu');
	});
});

describe('FilAriane — nav, ordered list, current page last (§ 3.11)', () => {
	it('renders nav[aria-label="Fil d\'Ariane"] ol, links then the current page', () => {
		const { container } = render(FilAriane, {
			etapes: [
				{ nom: 'Recherche', href: '/' },
				{ nom: 'Départements', href: '/departements' },
				{ nom: 'Vendée', href: '/departement/85' }
			]
		});
		const nav = container.querySelector('nav[aria-label="Fil d\'Ariane"]')!;
		expect(nav).not.toBeNull();
		const items = [...nav.querySelectorAll(':scope > ol > li')];
		expect(items).toHaveLength(3);
		expect(items.slice(0, 2).map((li) => li.querySelector('a')?.getAttribute('href'))).toEqual(['/', '/departements']);
		const dernier = items[2].firstElementChild!;
		expect(dernier.tagName).toBe('SPAN');
		expect(dernier.getAttribute('aria-current')).toBe('page');
		expect(dernier.textContent).toBe('Vendée');
		expect(items[2].querySelector('a')).toBeNull();
		// The separator is drawn by CSS with an empty alternative: no separator in the text of the steps.
		expect(nav.textContent?.replace(/\s+/g, '')).toBe('RechercheDépartementsVendée');
	});

	it('the current page has no link even when its step has no href', () => {
		const { container } = render(FilAriane, { etapes: [{ nom: 'Recherche', href: '/' }, { nom: 'DPE n° 1', href: null }] });
		expect(container.querySelectorAll('a')).toHaveLength(1);
		expect(container.querySelector('[aria-current="page"]')?.textContent).toBe('DPE n° 1');
	});
});

describe('Panneau — a section labelled by its h2 (§ 3.1)', () => {
	it.each(['partie', 'simple', 'essentiel'] as const)('%s: section aria-labelledby its h2', (variante) => {
		const { container } = render(Panneau, { titre: 'Risques', variante, children: texte('<p>Contenu</p>') });
		const section = container.querySelector('section')!;
		expect(section.classList.contains(`panneau-${variante}`)).toBe(true);
		const h2 = section.querySelector('h2')!;
		expect(h2.textContent).toBe('Risques');
		expect(h2.id).not.toBe('');
		expect(section.getAttribute('aria-labelledby')).toBe(h2.id);
		expect(section.textContent).toContain('Contenu');
	});

	it('a part says its lead under the title; an id is the anchor of the section', () => {
		const { container } = render(Panneau, { titre: 'Risques', variante: 'partie', chapo: 'Ce qui peut toucher le logement.', id: 'q-risques' });
		const section = container.querySelector('section#q-risques')!;
		expect(section.querySelector('h2 + p.panneau-chapo')?.textContent).toBe('Ce qui peut toucher le logement.');
	});

	it('two panels never share a title id', () => {
		const { container } = render(Panneau, { titre: 'A' });
		const autre = render(Panneau, { titre: 'B' }).container;
		expect(container.querySelector('h2')!.id).not.toBe(autre.querySelector('h2')!.id);
	});
});

describe('Tuile — a key figure, div.cle in a dl (§ 3.2)', () => {
	it('dt is the label, dd the value then the scale mark at 18 px and the note on its own line', () => {
		const { container } = render(Tuile, {
			libelle: 'Prix médian au m²',
			repere: { pictogramme: 'commune', mot: 'Commune' },
			note: 'Ventes de 2024',
			children: texte('<span class="cle-v">3 200 €</span>')
		});
		const cle = container.querySelector('div.cle')!;
		expect(cle.querySelector(':scope > dt')?.textContent).toBe('Prix médian au m²');
		const dd = cle.querySelector(':scope > dd')!;
		expect(dd.querySelector('.cle-v')?.textContent).toBe('3 200 €');
		const n = dd.querySelector('.cle-n')!;
		expect(n.querySelector('svg')?.getAttribute('width')).toBe('18');
		expect(n.querySelector('.ech')?.textContent).toContain('Commune');
		expect(n.lastElementChild?.textContent).toBe('Ventes de 2024');
	});

	it('no scale and no note: no .cle-n', () => {
		const { container } = render(Tuile, { libelle: 'Année de construction', children: texte('<span>1975</span>') });
		expect(container.querySelector('.cle-n')).toBeNull();
	});
});

describe('SourceButton — the one « i » of the site (review of 06/11)', () => {
	it('shows the icon alone, names what it opens and opens a dialog', () => {
		const { getByRole } = render(SourceButton, { libelle: 'Prix médian des maisons', onouvrir: () => {} });
		const bouton = getByRole('button', { name: 'Source et définition : Prix médian des maisons' });
		expect(bouton.textContent?.trim()).toBe('');
		expect(bouton.getAttribute('aria-haspopup')).toBe('dialog');
		expect(bouton.getAttribute('title')).toBeNull();
		expect(bouton.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
	});

	it('form « detail »: the same icon, its name only differs', () => {
		const { getByRole } = render(SourceButton, { libelle: 'Population des communes', forme: 'detail', onouvrir: () => {} });
		const bouton = getByRole('button', { name: 'Détail : Population des communes' });
		expect(bouton.textContent?.trim()).toBe('');
		expect(bouton.getAttribute('aria-haspopup')).toBe('dialog');
		expect(bouton.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
	});
});
