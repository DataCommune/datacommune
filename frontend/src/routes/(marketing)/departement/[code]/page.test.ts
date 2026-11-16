// The departement page (04.7-10, decision 6): four cards « value + definition + base » that follow
// the state of their own block, the principal communes, the cross links, the sources, and the
// structured data of an indexed page that serves a figure. An absence is never shown as a zero.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SITE_URL } from '$lib/config';
import {
	DEPARTEMENT_22,
	DEPARTEMENT_29,
	DEPARTEMENT_44,
	DEPARTEMENT_75,
	DEPARTEMENTS_LISTE
} from '$lib/data/__fixtures__/departement';
import { cartesDepartement, cartesEssentiel, descriptionDataset, titreChiffres } from '$lib/data/departement-presentation';
import { getExplication } from '$lib/data/explications';
import { messageLimite, TITRE_LIMITE } from '$lib/data/panne';
import type { DepartementPageData } from './+page';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/departement/44'), state: {} })
}));

import { load } from './+page';
import Page from './+page.svelte';
import { sansDeveloppement } from '$lib/utils/sigles';

const SOURCES = [
	{ nom: 'DVF', producteur: 'DGFiP / Etalab', licence: 'Licence Ouverte 2.0', url: 'https://www.data.gouv.fr/dvf' },
	{ nom: 'DPE', producteur: 'ADEME', licence: 'Licence Ouverte 2.0', url: 'javascript:alert(1)' }
];

const donnees = (synthese = DEPARTEMENT_44, extra: Partial<Extract<DepartementPageData, { etat: 'synthese' }>> = {}): DepartementPageData => ({
	etat: 'synthese',
	code: synthese.code,
	synthese,
	sources: SOURCES,
	departements: DEPARTEMENTS_LISTE,
	fiches: null,
	...extra
});

const meta = (nom: string) => document.head.querySelector(`meta[name="${nom}"]`)?.getAttribute('content') ?? null;
const jsonLd = () =>
	[...document.head.querySelectorAll('script[type="application/ld+json"]')].map((script) => JSON.parse(script.textContent ?? '{}'));
const texte = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

afterEach(() => {
	document.head.innerHTML = '';
});

describe('/departement/[code] — en-tête et fil d’Ariane', () => {
	it('titles the page with the departement, its code and its region', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		expect(container.querySelector('h1')?.textContent).toBe('Loire-Atlantique');
		expect(container.querySelector('.dep-overline')?.textContent).toBe('Département 44 · Pays de la Loire');
		expect(document.title).toMatch(/^Loire-Atlantique.*44.*— DataCommune$/);
	});

	it('has the breadcrumb Accueil > Départements (T6, decided by Ianis on 2026-11-10) > the departement, the last step being the page', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const fil = container.querySelector('nav[aria-label="Fil d\'Ariane"]')!;
		expect([...fil.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['Accueil', '/'],
			['Départements', '/departements']
		]);
		expect(fil.querySelector('[aria-current="page"]')?.textContent).toBe('Loire-Atlantique');
	});

	it('says the day of the last import, or that none was made for a hosted departement', () => {
		expect(texte(render(Page, { props: { data: donnees() } }).container.querySelector('.dep-import'))).toBe('Données importées le 30 octobre 2026.');
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_29) } });
		expect(texte(container.querySelector('.dep-import'))).toBe('Aucune donnée n’a encore été importée pour ce département.');
	});
});

describe('/departement/[code] — les quatre cartes', () => {
	// D2 (2026-11-10): the prices first, as on the commune fiche, the population last.
	it('has four cards, prices first, each with its value and its count, the definition and the base in its panel', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const cartes = [...container.querySelectorAll('section#chiffres dl.cles > .cle')];
		expect(cartes).toHaveLength(4);
		// 04.8-08 (D-31): a label read before its definition carries the full form of its acronym; behind it, the label.
		expect(cartes.map((c) => sansDeveloppement(c.querySelector('dt')?.textContent ?? ''))).toEqual([
			'Prix médian au m² des maisons',
			'Prix médian au m² des appartements',
			'Part des DPE classés F ou G',
			'Population des communes'
		]);
		const attendues = cartesEssentiel(DEPARTEMENT_44);
		cartes.forEach((carte, i) => {
			// D3: the note is the count alone; nothing left the page, the period moved to the panel of the tile.
			const note = sansDeveloppement(texte(carte.querySelector('.cle-note')));
			expect(note, attendues[i].id).toBe((attendues[i].portee ?? '').replace(/\s+/g, ' '));
			const panneau = sansDeveloppement(texte(container.querySelectorAll('section#chiffres dialog')[i]));
			expect(panneau, attendues[i].id).toContain(getExplication(attendues[i].fieldId).definition);
			if (attendues[i].base) expect(panneau, attendues[i].id).toContain(attendues[i].base!.replace(/\s+/g, ' '));
		});
		expect(texte(cartes[3])).toMatch(/1.450.000\s+hab\./);
		expect(texte(cartes[1])).toMatch(/3.480\s+€\/m²/);
		expect(texte(cartes[2])).toMatch(/15,7\s+%/);
		expect(texte(cartes[1])).not.toContain('juin');
		expect(texte(cartes[2])).not.toContain('pas de logements');
	});

	it('says a median under the threshold as « Effectif insuffisant », with its served count, never a number nor a zero', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const maisons = container.querySelector('section#chiffres dl.cles > .cle:has([data-carte="maison"])')!;
		expect(texte(maisons)).toContain('Effectif insuffisant');
		expect(texte(maisons)).toMatch(/Trop peu d'observations \(4\)/);
		expect(texte(maisons)).not.toMatch(/€\/m²/);
		expect(maisons.querySelector('[data-state="insufficient_sample"]')).not.toBeNull();
	});

	it('shows « Non renseigné » on every card of a hosted departement that is not imported, and no zero', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_29) } });
		const cartes = [...container.querySelectorAll('section#chiffres dl.cles > .cle')];
		expect(cartes).toHaveLength(4);
		const attendues = cartesEssentiel(DEPARTEMENT_29);
		cartes.forEach((carte, i) => {
			expect(carte.querySelector('[data-state="not_reported"]'), texte(carte)).not.toBeNull();
			expect(texte(carte.querySelector('.dep-valeur'))).not.toMatch(/\d/);
			// No base: no note, and the definition stays one click away, an absent value keeps its panel.
			expect(attendues[i].base).toBeNull();
			expect(carte.querySelector('.cle-note')).toBeNull();
			expect(sansDeveloppement(texte(container.querySelectorAll('section#chiffres dialog')[i])), attendues[i].id).toContain(
				getExplication(attendues[i].fieldId).definition
			);
		});
	});

	it('shows the reason on every card of a departement out of coverage, worded, never as the technical key', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_22) } });
		const cartes = [...container.querySelectorAll('section#chiffres dl.cles > .cle')];
		expect(cartes).toHaveLength(4);
		for (const carte of cartes) {
			expect(texte(carte)).toContain('Hors couverture · département non hébergé');
			expect(texte(carte)).toContain('Ce site ne couvre pas ce département.');
		}
		expect(container.textContent).not.toContain('territoire_hors_perimetre');
		expect(container.textContent).not.toContain('out_of_coverage');
	});
});

describe('/departement/[code] — la portée des chiffres (Opus P3-6)', () => {
	it('says « l’ensemble des communes » when the population counts them all', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		// 4.9 (A9-7): the sentence is in the panel of each tile, not on the page.
		expect(container.querySelector('.dep-portee')).toBeNull();
		expect(texte(container.querySelector('dialog'))).toContain('l’ensemble des communes du département');
	});

	it('says how many communes the population counts, out of how many, instead, when some are missing', () => {
		const partielle = { ...DEPARTEMENT_44, population: { ...DEPARTEMENT_44.population, communes_comptees: 200 } };
		const { container } = render(Page, { props: { data: donnees(partielle) } });
		const portee = texte(container.querySelector('dialog'));
		expect(portee).toContain('200 communes sur 207');
		expect(portee).not.toContain('ensemble des communes');
		// The reserve changes the meaning of the figure: it stays visible on the tile.
		expect(texte(container.querySelector('.cle:has([data-carte="population"]) .cle-note'))).toContain('200 communes sur 207');
		// And the description of the Dataset, which is read from the visible texts, says it too.
		expect(jsonLd().find((o) => o['@type'] === 'Dataset').description).toContain('200');
	});
});

describe('/departement/[code] — communes principales', () => {
	it('lists the principal communes as links to their fiche, with their population', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const table = container.querySelector('table.dc-table')!;
		expect(texte(table.querySelector('caption'))).toContain('Classées par population décroissante');
		expect([...table.querySelectorAll('tbody th a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['Nantes', '/commune/44109'],
			['Saint-Nazaire', '/commune/44184'],
			['Rezé', '/commune/44162']
		]);
		const lignes = [...table.querySelectorAll('tbody tr')].map((tr) => texte(tr.querySelector('td')));
		expect(lignes[0]).toMatch(/320.732 habitants · 2022 · INSEE/);
		// T8, D5 (2026-11-10): the year and the source on their own line, the « · » before them read, not shown.
		expect(texte(table.querySelector('tbody tr .dep-millesime'))).toBe('· 2022 · INSEE');
		// The population of the referential has no year: never invented.
		expect(lignes[2]).toMatch(/43.500 habitants$/);
		expect(container.querySelector('section#communes-principales > h2')?.textContent).toBe('Communes les plus peuplées');
		const region = table.closest('div[role="region"][tabindex="0"]')!;
		expect(region.getAttribute('aria-label')).toBe('Communes les plus peuplées');
	});

	// D5 (2026-11-10): the caption is read by assistive technologies and printed, not shown in bold on screen.
	it('keeps the caption of the table for screen readers and paper, out of the screen', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const legende = container.querySelector('table.dc-table caption')!;
		expect(legende.classList.contains('dep-legende')).toBe(true);
		expect(texte(legende)).toContain('Classées par population décroissante');
	});

	it('lists the arrondissements for a departement of one commune (Paris)', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_75) } });
		expect(container.querySelector('section#communes-principales > h2')?.textContent).toBe('Arrondissements les plus peuplés');
		expect(container.querySelector('table.dc-table')!.closest('div[role="region"][tabindex="0"]')?.getAttribute('aria-label')).toBe('Arrondissements les plus peuplés');
		expect([...container.querySelectorAll('table.dc-table tbody th a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['15e arrondissement de Paris', '/commune/75115'],
			['20e arrondissement de Paris', '/commune/75120'],
			['12e arrondissement de Paris', '/commune/75112']
		]);
	});

	it('shows no empty table when the list is empty: it says why', () => {
		const sans = { ...DEPARTEMENT_22, communes_principales: [] };
		const { container } = render(Page, { props: { data: donnees(sans) } });
		expect(container.querySelector('table.dc-table')).toBeNull();
		expect(texte(container.querySelector('.dep-principales-vide'))).toBe('Ce site ne couvre pas ce département.');
		const horsDeLaListe = render(Page, { props: { data: donnees({ ...DEPARTEMENT_44, communes_principales: [] }) } });
		expect(texte(horsDeLaListe.container.querySelector('.dep-principales-vide'))).toBe('Le référentiel ne liste aucune commune pour ce département.');
	});
});

describe('/departement/[code] — toutes les communes de A à Z', () => {
	const FICHES = [
		{ code_insee: '44109', nom: 'Nantes', type_zone: 'commune' as const },
		{ code_insee: '44003', nom: 'Ancenis-Saint-Géréon', type_zone: 'commune' as const },
		{ code_insee: '44162', nom: 'Saint-Herblain', type_zone: 'commune' as const }
	];

	it('links every page of the departement, folded, grouped by letter, rendered without opening the fold', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_44, { fiches: FICHES }) } });
		const annuaire = container.querySelector('section#annuaire')!;
		expect(annuaire.querySelector('h2')?.textContent).toBe('Toutes les communes de A à Z');
		const pli = annuaire.querySelector('details')!;
		expect(pli.open).toBe(false);
		expect(texte(pli.querySelector('summary'))).toBe('Afficher les 3 fiches du département');
		expect([...pli.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(['A', 'N', 'S']);
		expect([...pli.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['Ancenis-Saint-Géréon', '/commune/44003'],
			['Nantes', '/commune/44109'],
			['Saint-Herblain', '/commune/44162']
		]);
	});

	it('has no directory when the pages could not be read, or none is listed', () => {
		for (const fiches of [null, []]) {
			const { container, unmount } = render(Page, { props: { data: donnees(DEPARTEMENT_44, { fiches }) } });
			expect(container.querySelector('#annuaire')).toBeNull();
			expect(container.querySelector('section#parcourir'), String(fiches)).not.toBeNull();
			unmount();
		}
	});
});

describe('/departement/[code] — liens croisés et sources', () => {
	it('links the directory, the departements of the region and the list of departements', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const parcours = container.querySelector('section#parcourir')!;
		const principal = parcours.querySelector('ul.liste-liens a[href^="/communes"]')!;
		expect(principal.getAttribute('href')).toBe('/communes?departement=44');
		expect(principal.querySelector('.lien-t')?.textContent).toBe('Toutes les communes');
		expect(texte(principal.querySelector('.lien-n'))).toBe('207 communes');
		expect([...parcours.querySelectorAll('h3 + ul.liste-liens a')].map((a) => [a.querySelector('.lien-t')?.textContent, a.getAttribute('href')])).toEqual([
			[`Vendée${String.fromCharCode(0xa0)}(85)`, '/departement/85']
		]);
		expect(parcours.querySelector('a[href="/departements"] .lien-t')?.textContent).toBe('Tous les départements');
	});

	it('counts the arrondissements apart from the communes, never added to them', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_75) } });
		expect(texte(container.querySelector('#parcourir a[href^="/communes"] .lien-n'))).toBe('1 commune et 20 arrondissements');
	});

	it('leaves the links of the region out, and keeps the directory, when the list of departements failed', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_44, { departements: null }) } });
		expect(container.querySelector('#parcourir h3')).toBeNull();
		expect(container.querySelector('#parcourir ul.liste-liens a[href^="/communes"]')?.getAttribute('href')).toBe('/communes?departement=44');
		expect(container.querySelector('a[href="/departements"]')).not.toBeNull();
	});

	it('names the sources with their producer and licence, and links only a web address', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		// The linked sources come first (external links), then the ones without a link. An acronym the
		// page has not spelled out yet is spelled out once (04.8-08): since the definitions left the
		// tiles (4.9), DGFiP is first said here, like ADEME.
		const liees = [...container.querySelectorAll('#sources ul.liste-liens li')];
		expect(liees.map((li) => [texte(li.querySelector('.lien-t')), texte(li.querySelector('.lien-n'))])).toEqual([
			['DVF (Demandes de valeurs foncières)', 'DGFiP (Direction générale des finances publiques) / Etalab · Licence Ouverte 2.0']
		]);
		expect(liees[0].querySelector('a')?.getAttribute('href')).toBe('https://www.data.gouv.fr/dvf');
		const sansLien = [...container.querySelectorAll('#sources ul.dep-sources li')];
		expect(sansLien.map((li) => [texte(li.querySelector('.dep-source-nom')), texte(li.querySelector('.dep-source-detail'))])).toEqual([
			['DPE', '· ADEME (Agence de la transition écologique) · Licence Ouverte 2.0']
		]);
		// The unsafe address is never a link.
		expect(sansLien[0].querySelector('a')).toBeNull();
		expect(container.querySelector('#sources a[href^="javascript"]')).toBeNull();
	});

	it('has no section of sources when none was served', () => {
		const { container } = render(Page, { props: { data: donnees(DEPARTEMENT_44, { sources: [] }) } });
		expect(container.querySelector('#sources')).toBeNull();
	});
});

describe('/departement/[code] — indexation et données structurées', () => {
	it('is canonical to itself and indexable when the departement is: no robots meta', () => {
		render(Page, { props: { data: donnees() } });
		expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(`${SITE_URL}/departement/44`);
		expect(meta('robots')).toBeNull();
	});

	it('is noindex, follow, and still canonical, when the departement is not indexable', () => {
		for (const synthese of [DEPARTEMENT_29, DEPARTEMENT_22, { ...DEPARTEMENT_44, indexable: false }]) {
			document.head.innerHTML = '';
			render(Page, { props: { data: donnees(synthese) } });
			expect(meta('robots'), synthese.code).toBe('noindex, follow');
			expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(`${SITE_URL}/departement/${synthese.code}`);
		}
	});

	it('carries a BreadcrumbList and a Dataset for an indexed page that serves a figure', () => {
		render(Page, { props: { data: donnees() } });
		expect(jsonLd().map((o) => o['@type'])).toEqual(['BreadcrumbList', 'Dataset']);
	});

	// D1 (2026-11-10): the panel is « L’essentiel », as on the commune fiche; the Dataset keeps its name, the
	// words of the page title (« chiffres clés du département »), so the structured data do not move.
	it('names the Dataset « Chiffres clés : {nom} », as the page title says, and describes it by the visible texts', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const dataset = jsonLd().find((o) => o['@type'] === 'Dataset');
		expect(container.querySelector('section#chiffres > h2')?.textContent).toBe('L’essentiel');
		expect(dataset.name).toBe(titreChiffres('Loire-Atlantique'));
		expect(document.title).toContain('chiffres clés du département');
		expect(dataset.description).toBe(descriptionDataset(DEPARTEMENT_44));
		expect(dataset.description.length).toBeGreaterThanOrEqual(50);
		expect(dataset.description.length).toBeLessThanOrEqual(5000);
		// Every figure the description states is on the page, in the same words.
		// 04.8-08 (D-31): read behind the full forms the page adds to its acronyms.
		const page = sansDeveloppement(texte(container));
		for (const carte of cartesDepartement(DEPARTEMENT_44).filter((c) => c.etat.kind === 'valeur')) {
			expect(page).toContain(carte.libelle);
			expect(page).toContain(carte.base!.replace(/\s+/g, ' '));
		}
	});

	it('carries no structured data when the page is noindex, even if it serves a figure', () => {
		render(Page, { props: { data: donnees({ ...DEPARTEMENT_44, indexable: false }) } });
		expect(jsonLd()).toEqual([]);
	});

	it('carries no Dataset on a page of absences, indexable or not', () => {
		for (const synthese of [DEPARTEMENT_29, DEPARTEMENT_22, { ...DEPARTEMENT_22, indexable: true }]) {
			document.head.innerHTML = '';
			render(Page, { props: { data: donnees(synthese) } });
			expect(jsonLd().filter((o) => o['@type'] === 'Dataset'), synthese.code).toEqual([]);
		}
	});

	it('writes a meta description from the figures served, and the same one in the social tags', () => {
		render(Page, { props: { data: donnees() } });
		// T1 (2026-11-10): the tags of `MetaPartage`, each once.
		for (const selecteur of ['meta[name="description"]', 'meta[property="og:title"]', 'meta[name="twitter:card"]', 'link[rel="canonical"]']) {
			expect(document.head.querySelectorAll(selecteur), selecteur).toHaveLength(1);
		}
		expect(meta('description')).toMatch(/1.450.000\shab\./);
		expect(document.head.querySelector('meta[property="og:description"]')?.getAttribute('content')).toBe(meta('description'));
		expect(document.head.querySelector('meta[property="og:url"]')?.getAttribute('content')).toBe(`${SITE_URL}/departement/44`);
	});

	it('keeps every script ld+json inert when a name holds </script>', () => {
		const hostile = `Évil</script><img id="intrus" src="x">&amp;`;
		render(Page, { props: { data: donnees({ ...DEPARTEMENT_44, nom: hostile }) } });
		expect(document.head.querySelector('#intrus')).toBeNull();
		const scripts = [...document.head.querySelectorAll('script[type="application/ld+json"]')];
		expect(scripts).toHaveLength(2);
		for (const script of scripts) expect(script.textContent).not.toMatch(/[<>&]/);
		expect(jsonLd().find((o) => o['@type'] === 'Dataset').spatialCoverage.name).toBe(hostile);
	});
});

describe('/departement/[code] — panne', () => {
	const panne = (
		detail: { statut: number | null; titre: string | null; message: string | null } = { statut: 503, titre: null, message: null },
		retryAfter: number | null = null
	): DepartementPageData => ({
		etat: 'panne',
		code: '44',
		nom: 'Loire-Atlantique',
		detail,
		retryAfter
	});

	it('shows an alert and no figure, no table, no structured data, nothing partial', () => {
		const { container, getByRole } = render(Page, { props: { data: panne() } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain('Les chiffres de ce département sont momentanément indisponibles');
		expect(container.querySelector('h1')?.textContent).toBe('Loire-Atlantique');
		expect(container.querySelector('dl.cles')).toBeNull();
		expect(container.querySelector('table')).toBeNull();
		expect(container.textContent).not.toMatch(/€\/m²|hab\./);
		expect(jsonLd()).toEqual([]);
		expect(getByRole('button', { name: 'Recharger les chiffres' })).toBeTruthy();
	});

	it('words a rate limit as such, with the served delay, never as an outage of the service', () => {
		const { getByRole } = render(Page, { props: { data: panne({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(60) }, 60) } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain(TITRE_LIMITE);
		expect(alerte.textContent).toContain(messageLimite(60));
		expect(alerte.textContent).not.toContain('indisponibles');
	});

	it('is still canonical to itself', () => {
		render(Page, { props: { data: panne() } });
		expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(`${SITE_URL}/departement/44`);
	});
});

describe('/departement/[code] — chargement', () => {
	const charger = (fetchImpl: typeof fetch, setHeaders = vi.fn()) =>
		(load as unknown as (e: unknown) => Promise<DepartementPageData>)({ params: { code: '44' }, fetch: fetchImpl, setHeaders });

	it('posts the outage marker through the setHeaders of the event, and nothing for a served page', async () => {
		const setHeaders = vi.fn();
		const panne = vi.fn(async () => new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch;
		expect((await charger(panne, setHeaders)).etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledWith({ 'x-dc-etat-page': 'panne' });

		const ok = vi.fn(async (input: RequestInfo | URL) => {
			const corps = String(input).endsWith('/v1/departements') ? DEPARTEMENTS_LISTE : DEPARTEMENT_44;
			return new Response(JSON.stringify({ statut: 'ok', donnees: corps }), { status: 200, headers: { 'Content-Type': 'application/json' } });
		}) as unknown as typeof fetch;
		const calme = vi.fn();
		expect((await charger(ok, calme)).etat).toBe('synthese');
		expect(calme).not.toHaveBeenCalled();
	});
});

describe('/departement/[code] — forme direction A (04.8-05)', () => {
	// D1 (2026-11-10): the head of the commune fiche — a pale band around the breadcrumb, the title and its line —
	// and « L’essentiel » as the h2 of the essential panel, its price tiles in the tint of the prices, the DPE
	// tile in the tint of the housing, the population neutral (the classes of the commune tiles, `Kpi.svelte`).
	it('has the pale head band and « L’essentiel » as the h2 of an essential panel of four tinted tiles, prices first', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const tete = container.querySelector('.dep-tete')!;
		expect(tete.querySelector('nav')).not.toBeNull();
		expect(tete.querySelector('h1')?.textContent).toBe('Loire-Atlantique');
		expect(tete.querySelector('.fiche-sous')).not.toBeNull();
		expect(tete.querySelector('section')).toBeNull();
		const panneau = container.querySelector('section.panneau-essentiel')!;
		expect(panneau.querySelector('h2')?.textContent).toBe('L’essentiel');
		const tuiles = [...panneau.querySelectorAll('.cle')];
		expect(tuiles).toHaveLength(4);
		expect(tuiles.map((t) => sansDeveloppement(t.querySelector('dt')?.textContent ?? ''))).toEqual(cartesEssentiel(DEPARTEMENT_44).map((c) => c.libelle));
		const teintes: Record<string, string | null> = { maison: 'q-prix', appartement: 'q-prix', dpe: 'q-logement', population: null };
		tuiles.forEach((tuile, i) => {
			const id = cartesEssentiel(DEPARTEMENT_44)[i].id;
			const valeur = tuile.querySelector(`[data-carte="${id}"]`)!;
			expect(valeur, id).not.toBeNull();
			expect(valeur.getAttribute('data-teinte'), id).toBe(teintes[id]);
		});
	});

	it('ends every link of « Parcourir » with a → arrow and gives none a rel', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		const liens = [...container.querySelectorAll('#parcourir a')];
		expect(liens.length).toBeGreaterThanOrEqual(3);
		for (const lien of liens) {
			expect(lien.querySelector('.fleche')?.textContent, lien.getAttribute('href') ?? '').toBe('→');
			expect(lien.querySelector('.fleche')?.getAttribute('aria-hidden')).toBe('true');
			expect(lien.hasAttribute('rel')).toBe(false);
		}
	});

	it('opens every source link of « Sources et licences » with rel="noopener noreferrer" and a ↗ arrow', () => {
		const { container } = render(Page, { props: { data: donnees() } });
		expect(container.querySelector('#sources > h2')?.textContent).toBe('Sources et licences');
		const liens = [...container.querySelectorAll('#sources ul.liste-liens a')];
		expect(liens.length).toBeGreaterThanOrEqual(1);
		for (const lien of liens) {
			expect(lien.getAttribute('rel')).toBe('noopener noreferrer');
			expect(lien.querySelector('.fleche')?.textContent).toBe('↗');
		}
	});

	it('keeps today’s empty sentence for a departement without any figure to list', () => {
		const sans = { ...DEPARTEMENT_44, communes_principales: [] };
		const { container } = render(Page, { props: { data: donnees(sans) } });
		expect(container.querySelector('section#communes-principales > h2')?.textContent).toBe('Communes les plus peuplées');
		expect(texte(container.querySelector('.dep-principales-vide'))).toBe('Le référentiel ne liste aucune commune pour ce département.');
		expect(container.querySelector('table')).toBeNull();
	});

	it('keeps the alert and the « Recharger les chiffres » button on an outage', () => {
		const panne: DepartementPageData = {
			etat: 'panne',
			code: '44',
			nom: 'Loire-Atlantique',
			detail: { statut: 503, titre: null, message: null },
			retryAfter: null
		};
		const { getByRole } = render(Page, { props: { data: panne } });
		expect(getByRole('alert')).toBeTruthy();
		// T2 (2026-11-10): the secondary button of the socle, weight 400.
		expect(getByRole('button', { name: 'Recharger les chiffres' }).classList.contains('bouton-secondaire')).toBe(true);
	});
});
