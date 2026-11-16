// Panel of the selected parcel (04.10 UI contract § 2.5): what identifies the parcel, its served surface
// or the state of the glossary when the cadastre gives none, and the badge of the parcel that contains
// the point of the address, on that parcel only. 04.10-07: its sales, read by the browser after the
// choice, five per page with the cards and the price rule of the fiche; zero, reading, out of coverage,
// outage and rate limit are five displays.
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ParcelleCarte } from '$lib/api/carte-parcelles';
import ParcelPanel from './ParcelPanel.svelte';

const NB = String.fromCharCode(0xa0);
const BAN_ID = '00000_0001_00001';
const EXEMPLE = JSON.parse(readFileSync(join(resolve(process.cwd(), '..'), 'contracts/carte/exemples/ventes-parcelle.json'), 'utf8'));

function parcelle(id: string, contenance: number | null): ParcelleCarte {
	return {
		type: 'Feature',
		id,
		geometry: { type: 'Polygon', coordinates: [[[-3.5002, 46.49986], [-3.4998, 46.49986], [-3.4998, 46.50014], [-3.5002, 46.49986]]] },
		properties: { id_parcelle: id, contenance, etiquette: [-3.5, 46.5], ventes: null }
	};
}

const paires = (conteneur: HTMLElement) =>
	[...conteneur.querySelectorAll('dl > div')].map((rang) => [rang.querySelector('dt')!.textContent!.trim(), rang.querySelector('dd')!.textContent!.trim()]);

/** A copy of the contract example, changed in place by *retouche*. */
function exemple(retouche: (donnees: any) => void = () => {}) {
	const copie = structuredClone(EXEMPLE);
	retouche(copie.donnees);
	return copie;
}

/** Page *numero* of 12 sales, five per page, under the generation of the example. */
function pageDe(numero: number) {
	return exemple((d) => {
		const n = numero < 3 ? 5 : 2;
		const modele = d.ventes[0];
		d.ventes = Array.from({ length: n }, (_, i) => ({ ...modele, date: `202${5 - numero}-0${i + 1}-15` }));
		d.groupes = Array.from({ length: n }, () => null);
		Object.assign(d, { total: 12, page: numero, pages: 3 });
	});
}

const json = (status: number, corps: unknown, headers: Record<string, string> = {}) =>
	new Response(JSON.stringify(corps), { status, headers: { 'Content-Type': 'application/json', ...headers } });

interface Appel {
	url: string;
	signal: AbortSignal | undefined;
	repondre: (reponse: Response) => void;
}

/** A fetch whose answers the test gives, one per call, when it wants. An abort does not settle it. */
function fetchManuel() {
	const appels: Appel[] = [];
	const f = vi.fn(
		(input: RequestInfo | URL, init?: RequestInit) =>
			new Promise<Response>((repondre) => appels.push({ url: String(input), signal: init?.signal ?? undefined, repondre }))
	);
	vi.stubGlobal('fetch', f);
	return { f, appels };
}

/** A fetch that answers every call at once with *reponse*. */
function fetchRepondant(reponse: () => Response) {
	const f = vi.fn(async () => reponse());
	vi.stubGlobal('fetch', f);
	return f;
}

const sectionVentes = (conteneur: HTMLElement) => conteneur.querySelector<HTMLElement>('.parcelle-ventes')!;
const cartes = (conteneur: HTMLElement) => [...conteneur.querySelectorAll('.ventes-cartes > li')];

beforeEach(() => {
	// By default the sales never answer: the tests of the identity of the parcel do not depend on them.
	fetchManuel();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('ParcelPanel', () => {
	it('without a selection, asks to choose a parcel and names none', () => {
		const { container } = render(ParcelPanel, { banId: BAN_ID, parcelle: null, estAdresse: false, contexte: null });
		expect(container.textContent!.trim()).toBe('Choisissez une parcelle sur la carte ou dans la liste.');
		expect(screen.queryByRole('heading')).toBeNull();
		expect(container.querySelector('dl')).toBeNull();
	});

	it('without the map (04.10-09), asks to choose a parcel in the list only', () => {
		const { container } = render(ParcelPanel, { banId: BAN_ID, parcelle: null, estAdresse: false, contexte: null, sansCarte: true });
		expect(container.textContent!.trim()).toBe('Choisissez une parcelle dans la liste.');
	});

	it('names the parcel in a level-3 heading that can take the focus, then its surface and its reference', () => {
		const { container } = render(ParcelPanel, { banId: BAN_ID, parcelle: parcelle('00000000ZA0001', 1960), estAdresse: false, contexte: null });
		const titre = screen.getByRole('heading', { level: 3 });
		expect(titre.textContent).toBe('Parcelle ZA 0001');
		expect(titre.getAttribute('tabindex')).toBe('-1');
		// The section is named by its heading.
		expect(container.querySelector('section')!.getAttribute('aria-labelledby')).toBe(titre.id);
		expect(paires(container)).toEqual([
			['Surface cadastrale', `1${String.fromCharCode(0x202f)}960${NB}m²`],
			['Référence cadastrale', '00000 000 ZA 0001']
		]);
	});

	it('an absent surface is the state of the glossary, never « 0 m² »', () => {
		const { container } = render(ParcelPanel, { banId: BAN_ID, parcelle: parcelle('00000000ZA0003', null), estAdresse: false, contexte: null });
		const surface = container.querySelector('dl > div dd')!;
		expect(surface.querySelector('[data-state="not_reported"]')!.textContent!.trim()).toBe('Non renseigné');
		expect(surface.textContent).not.toContain('m²');
		expect(surface.textContent).not.toMatch(/\d/);
	});

	it('a served surface of zero stays a value', () => {
		const { container } = render(ParcelPanel, { banId: BAN_ID, parcelle: parcelle('00000000ZA0002', 0), estAdresse: false, contexte: null });
		expect(paires(container)[0]).toEqual(['Surface cadastrale', `0${NB}m²`]);
		expect(container.querySelector('[data-state="not_reported"]')).toBeNull();
	});

	it('marks the parcel of the address, and that one only', () => {
		const adresse = render(ParcelPanel, { banId: BAN_ID, parcelle: parcelle('00000000ZA0001', 450), estAdresse: true, contexte: null });
		expect(adresse.container.querySelector('.parcelle-badge')!.textContent).toBe('Parcelle de l’adresse');
		adresse.unmount();
		const autre = render(ParcelPanel, { banId: BAN_ID, parcelle: parcelle('00000000ZA0002', 612), estAdresse: false, contexte: null });
		expect(autre.container.querySelector('.parcelle-badge')).toBeNull();
		expect(autre.container.textContent).not.toContain('Parcelle de l’adresse');
	});
});

describe('ParcelPanel — ventes de la parcelle (04.10-07)', () => {
	const props = (id = '00000000ZA0001') => ({ banId: BAN_ID, parcelle: parcelle(id, 450), estAdresse: false, contexte: null });

	it('surface and reference at once; while the sales are read, T-06 in their place and the section busy', () => {
		const { appels } = fetchManuel();
		const { container } = render(ParcelPanel, props());
		expect(paires(container).map(([terme]) => terme)).toEqual(['Surface cadastrale', 'Référence cadastrale']);
		expect(appels.map((a) => a.url)).toEqual([`/carte/${BAN_ID}/parcelle/00000000ZA0001/ventes?page=1`]);
		const section = sectionVentes(container);
		expect(section.getAttribute('aria-busy')).toBe('true');
		expect(section.textContent).toContain('Chargement des ventes de la parcelle…');
		expect(section.textContent).not.toMatch(/Aucune vente/);
		expect(cartes(container)).toEqual([]);
	});

	it('then the title of the sales, the source line of the fiche with the served period, and the cards', async () => {
		fetchRepondant(() => json(200, EXEMPLE));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(cartes(container)).toHaveLength(4));
		const titre = screen.getByRole('heading', { level: 4 });
		expect(titre.textContent).toBe('Ventes publiées sur la parcelle');
		const section = sectionVentes(container);
		expect(section.getAttribute('aria-labelledby')).toBe(titre.id);
		expect(section.getAttribute('aria-busy')).toBe('false');
		expect(section.querySelector('.parcelle-ventes-source')!.textContent).toBe('DVF (DGFiP) · 2021-2025 · ventes de la parcelle, rattachées à aucun logement');
		expect(cartes(container)).toHaveLength(4);
		expect(section.textContent).not.toContain('Chargement des ventes');
		// One page only: the range is there for print, no navigation.
		expect(section.querySelector('nav')).toBeNull();
	});

	it('five cards at most per page, the served range, and the buttons of the pagination', async () => {
		fetchRepondant(() => json(200, pageDe(1)));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(cartes(container)).toHaveLength(5));
		expect(container.querySelector('.pagination-range')!.textContent).toBe('Ventes 1 à 5 sur 12');
		expect(screen.getByRole('button', { name: 'Page suivante' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Page précédente' })).toBeNull();
		expect(container.querySelector('.parcelle-ventes nav a')).toBeNull();
	});

	it('a card whose price is withheld shows the badge of the fiche, never an amount', async () => {
		fetchRepondant(() =>
			json(
				200,
				exemple((d) => {
					// An amount served next to a minimisation: the projection of the fiche drops it.
					d.ventes[1].prix = 199000;
					d.ventes[1].prix_affiche = true;
				})
			)
		);
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(cartes(container)).toHaveLength(4));
		const retenue = cartes(container)[1];
		expect(retenue.querySelector('.ventecard-badge')!.textContent).toBe('Prix non affiché');
		expect(retenue.textContent).not.toMatch(/€|199/);
	});

	it('zero sale: the sentence of the fiche with the served period, then T-27 with the served first year', async () => {
		fetchRepondant(() => json(200, exemple((d) => Object.assign(d, { total: 0, pages: 1, ventes: [], groupes: [], periode: '2020-2024', annee_debut: 2020 }))));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(sectionVentes(container).textContent).toContain('Aucune vente publiée'));
		const phrases = [...sectionVentes(container).querySelectorAll('.parcelle-ventes-zero')].map((p) => p.textContent);
		expect(phrases).toEqual(['Aucune vente publiée sur la parcelle dans DVF (2020-2024).', 'Avant 2020, les ventes ne sont pas connues ici.']);
		expect(cartes(container)).toEqual([]);
	});

	it('zero sale without a served first year: no T-27', async () => {
		fetchRepondant(() => json(200, exemple((d) => Object.assign(d, { total: 0, pages: 1, ventes: [], groupes: [], annee_debut: null }))));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(sectionVentes(container).textContent).toContain('Aucune vente publiée'));
		expect(sectionVentes(container).textContent).not.toContain('Avant ');
	});

	it('out of coverage: the state of the glossary with its served reason, no list, no zero', async () => {
		fetchRepondant(() => json(200, { statut: 'ok', donnees: { etat: 'out_of_coverage', motif: 'dataset_non_heberge', page: 1, par_page: 5 } }));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(sectionVentes(container).querySelector('[data-state="out_of_coverage"]')).not.toBeNull());
		expect(sectionVentes(container).textContent).not.toContain('Aucune vente');
		expect(cartes(container)).toEqual([]);
		expect(sectionVentes(container).querySelector('[role="alert"]')).toBeNull();
	});

	it.each([
		['503', () => json(503, { title: 'Ventes indisponibles' })],
		['404 of a parcel refused by the service', () => json(404, { title: 'Parcelle introuvable', status: 404 })]
	])('outage (%s): the text of the drawer of sales in an alert, and its reload', async (_nom, reponse) => {
		const f = fetchRepondant(reponse);
		const { container } = render(ParcelPanel, props());
		const alerte = await screen.findByRole('alert');
		expect(alerte.textContent).toContain("Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes.");
		expect(sectionVentes(container).textContent).not.toContain('Aucune vente publiée');
		f.mockImplementation(async () => json(200, EXEMPLE));
		await fireEvent.click(screen.getByRole('button', { name: 'Recharger les ventes' }));
		await waitFor(() => expect(cartes(container)).toHaveLength(4));
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it('rate limit: the message of the limit with the served delay, never an outage', async () => {
		fetchRepondant(() => json(429, { title: 'Trop de demandes' }, { 'Retry-After': '30' }));
		const { container } = render(ParcelPanel, props());
		await waitFor(() => expect(sectionVentes(container).textContent).toContain(`La limite de requêtes est atteinte. Réessayez dans 30${NB}secondes.`));
		expect(sectionVentes(container).textContent).not.toContain('Source indisponible');
		expect(cartes(container)).toEqual([]);
	});

	it('next page: the section is busy, the generation is carried, the range is announced and the focus goes to the title', async () => {
		const { appels } = fetchManuel();
		const { container } = render(ParcelPanel, props());
		appels[0].repondre(json(200, pageDe(1)));
		await waitFor(() => expect(cartes(container)).toHaveLength(5));
		await fireEvent.click(screen.getByRole('button', { name: 'Page suivante' }));
		expect(appels[1].url).toBe(`/carte/${BAN_ID}/parcelle/00000000ZA0001/ventes?page=2&vg=0123456789abcdef`);
		// Busy, the cards of the page read so far stay until the next one comes.
		expect(sectionVentes(container).getAttribute('aria-busy')).toBe('true');
		expect(cartes(container)).toHaveLength(5);
		appels[1].repondre(json(200, pageDe(2)));
		await waitFor(() => expect(container.querySelector('.pagination-range')!.textContent).toBe('Ventes 6 à 10 sur 12'));
		expect(sectionVentes(container).getAttribute('aria-busy')).toBe('false');
		await waitFor(() => expect(container.querySelector('.parcelle-ventes-annonce')!.textContent).toBe('Ventes 6 à 10 sur 12'));
		expect(document.activeElement).toBe(screen.getByRole('heading', { level: 3 }));
		expect(screen.getByRole('button', { name: 'Page précédente' })).toBeTruthy();
	});

	it('an invalid page (changed generation) goes back to page 1, read again', async () => {
		const { appels } = fetchManuel();
		const { container } = render(ParcelPanel, props());
		appels[0].repondre(json(200, pageDe(1)));
		await waitFor(() => expect(cartes(container)).toHaveLength(5));
		await fireEvent.click(screen.getByRole('button', { name: 'Page suivante' }));
		appels[1].repondre(json(409, { title: 'Generation changee', status: 409, code: 'generation_changee' }));
		await waitFor(() => expect(appels).toHaveLength(3));
		expect(appels[2].url).toBe(`/carte/${BAN_ID}/parcelle/00000000ZA0001/ventes?page=1`);
		appels[2].repondre(json(200, pageDe(1)));
		await waitFor(() => expect(container.querySelector('.pagination-range')!.textContent).toBe('Ventes 1 à 5 sur 12'));
		expect(screen.queryByRole('alert')).toBeNull();
	});

	it('choosing another parcel drops the reading in flight and never shows the sales of the former one', async () => {
		const { appels } = fetchManuel();
		const { container, rerender } = render(ParcelPanel, props('00000000ZA0001'));
		await rerender(props('00000000ZA0002'));
		expect(appels.map((a) => a.url)).toEqual([
			`/carte/${BAN_ID}/parcelle/00000000ZA0001/ventes?page=1`,
			`/carte/${BAN_ID}/parcelle/00000000ZA0002/ventes?page=1`
		]);
		expect(appels[0].signal!.aborted).toBe(true);
		expect(appels[1].signal!.aborted).toBe(false);
		// The former answer comes late, after the new one was asked: it is never shown.
		appels[0].repondre(json(200, EXEMPLE));
		await new Promise((r) => setTimeout(r, 0));
		expect(cartes(container)).toEqual([]);
		expect(sectionVentes(container).textContent).toContain('Chargement des ventes de la parcelle…');
		appels[1].repondre(json(200, pageDe(1)));
		await waitFor(() => expect(cartes(container)).toHaveLength(5));
	});

	it('choosing another parcel after a page change starts again from its first page, the sales of the former gone at once', async () => {
		const { appels } = fetchManuel();
		const { container, rerender } = render(ParcelPanel, props('00000000ZA0001'));
		appels[0].repondre(json(200, pageDe(1)));
		await waitFor(() => expect(cartes(container)).toHaveLength(5));
		await rerender(props('00000000ZA0002'));
		expect(cartes(container)).toEqual([]);
		expect(appels[1].url).toBe(`/carte/${BAN_ID}/parcelle/00000000ZA0002/ventes?page=1`);
	});

	it('formats no price itself: the cards are those of VenteList', () => {
		const source = readFileSync(resolve(process.cwd(), 'src/lib/components/adresse/carte/ParcelPanel.svelte'), 'utf8');
		expect(source).toContain('<VenteList');
		expect(source).not.toMatch(/lignePrix|montant|NumberFormat|currency|€/);
	});
});

// A sale of several parcels (04.10-07, task 2; UI contract § 2.5 orders 5 and 6, T-20 to T-26): the foot of
// its card says it, with its reserves; the toggle tells the map which group to show. Every count and the
// surface are served: the panel adds nothing up.
describe('ParcelPanel — vente à plusieurs parcelles (04.10-07)', () => {
	const NNB = String.fromCharCode(0x202f);
	const props = (extra: Record<string, unknown> = {}, id = '00000000ZA0001') => ({
		banId: BAN_ID,
		parcelle: parcelle(id, 450),
		estAdresse: false,
		contexte: null,
		...extra
	});
	const pied = (carte: Element) => carte.querySelector('.vente-groupe');
	const textes = (carte: Element) => [...(pied(carte)?.querySelectorAll('p') ?? [])].map((p) => p.textContent);
	const bascule = (carte: Element) => carte.querySelector<HTMLButtonElement>('button.bouton-lien');

	async function rendre(extra: Record<string, unknown> = {}, corps: unknown = EXEMPLE) {
		fetchRepondant(() => json(200, corps));
		const rendu = render(ParcelPanel, props(extra));
		await waitFor(() => expect(cartes(rendu.container)).toHaveLength(4));
		return rendu;
	}

	it('a sale of the parcel alone says nothing of a group', async () => {
		const { container } = await rendre();
		expect(pied(cartes(container)[0])).toBeNull();
		expect(cartes(container)[0].textContent).not.toMatch(/Vendue avec|Prix de toute la vente/);
	});

	it('several parcels: « Vendue avec … » agreed, the price reserve only under a displayed price, the toggle', async () => {
		const { container } = await rendre();
		const [, retenue, avecPrix, auDela] = cartes(container);
		// Withheld price: no T-20.
		expect(textes(retenue)).toEqual(['Vendue avec 1 autre parcelle']);
		expect(textes(avecPrix)).toEqual(['Prix de toute la vente (3 parcelles).', 'Vendue avec 2 autres parcelles']);
		for (const carte of [retenue, avecPrix]) {
			const b = bascule(carte)!;
			expect(b.textContent).toBe('Montrer ces parcelles');
			expect(b.getAttribute('aria-pressed')).toBe('false');
			expect(b.getAttribute('type')).toBe('button');
		}
		// Beyond the cap of the route (`parcelles` null): T-20 and T-21 stay, no toggle, and T-45 says why
		// (R4 of the review of 2026-11-15).
		expect(textes(auDela)).toEqual([
			'Prix de toute la vente (60 parcelles).',
			'Vendue avec 59 autres parcelles',
			'Groupe trop grand pour être montré sur la carte (60 parcelles).'
		]);
		expect(bascule(auDela)).toBeNull();
	});

	it('the toggle shows the group: T-23 with the date of the card and the served surface, T-24, and tells the map', async () => {
		const ongroupe = vi.fn();
		const { container } = await rendre({ ongroupe });
		const carte = cartes(container)[1];
		await fireEvent.click(bascule(carte)!);
		expect(bascule(carte)!.getAttribute('aria-pressed')).toBe('true');
		expect(textes(carte).slice(1)).toEqual([`2 parcelles vendues ensemble · juin 2024 · 830${NB}m² au total`, 'Ne dit pas qui les possède aujourd’hui.']);
		expect(ongroupe).toHaveBeenLastCalledWith(EXEMPLE.donnees.groupes[1]);
		await fireEvent.click(bascule(carte)!);
		expect(bascule(carte)!.getAttribute('aria-pressed')).toBe('false');
		expect(textes(carte)).toEqual(['Vendue avec 1 autre parcelle']);
		expect(ongroupe).toHaveBeenLastCalledWith(null);
	});

	it('the surface is the served one, never a sum made here', async () => {
		const corps = exemple((d) => (d.groupes[1].surface_totale_m2 = 12345));
		const { container } = await rendre({}, corps);
		await fireEvent.click(bascule(cartes(container)[1])!);
		expect(textes(cartes(container)[1])[1]).toBe(`2 parcelles vendues ensemble · juin 2024 · 12${NNB}345${NB}m² au total`);
	});

	it('an incomplete surface with parcels gone from the cadastre: the short T-23, then T-25 agreed', async () => {
		const { container } = await rendre();
		const carte = cartes(container)[2];
		await fireEvent.click(bascule(carte)!);
		expect(textes(carte).slice(2)).toEqual([
			'3 parcelles vendues ensemble · septembre 2022',
			'Ne dit pas qui les possède aujourd’hui.',
			`Surface totale non calculée${NB}: 1 de ces parcelles ne figure plus au cadastre.`
		]);
		expect(textes(carte).join(' ')).not.toContain('m²');
	});

	it('T-25 in the plural for several parcels gone', async () => {
		const { container } = await rendre({}, exemple((d) => (d.groupes[2].absentes_du_cadastre = 2)));
		await fireEvent.click(bascule(cartes(container)[2])!);
		expect(textes(cartes(container)[2]).at(-1)).toBe(`Surface totale non calculée${NB}: 2 de ces parcelles ne figurent plus au cadastre.`);
	});

	it('a null surface while no parcel is gone: the short T-23, then T-44 with the served count without a surface (R3, 2026-11-15)', async () => {
		const { container } = await rendre({}, exemple((d) => ((d.groupes[2].absentes_du_cadastre = 0), (d.groupes[2].sans_surface = 1))));
		await fireEvent.click(bascule(cartes(container)[2])!);
		expect(textes(cartes(container)[2]).slice(2)).toEqual([
			'3 parcelles vendues ensemble · septembre 2022',
			'Ne dit pas qui les possède aujourd’hui.',
			`Surface totale non calculée${NB}: 1 de ces parcelles n’a pas de surface au cadastre.`
		]);
	});

	it('parcels of the shown group too far from the address to be drawn: T-46 agreed from the served count, nothing at zero (S2, 2026-11-15)', async () => {
		const { container } = await rendre({}, exemple((d) => (d.groupes[1].hors_emprise = 2)));
		await fireEvent.click(bascule(cartes(container)[1])!);
		expect(textes(cartes(container)[1]).at(-1)).toBe('2 de ces parcelles sont trop loin de l’adresse pour être montrées.');
		const un = await rendre({}, exemple((d) => (d.groupes[1].hors_emprise = 1)));
		await fireEvent.click(bascule(cartes(un.container)[1])!);
		expect(textes(cartes(un.container)[1]).at(-1)).toBe('1 de ces parcelles est trop loin de l’adresse pour être montrée.');
	});

	it('without the map, what the listed zone leaves out is T-48, never « hors de la vue » (S6, 2026-11-15)', async () => {
		const { container, rerender } = await rendre({ sansCarte: true, horsVue: 3 });
		await fireEvent.click(bascule(cartes(container)[1])!);
		expect(textes(cartes(container)[1]).at(-1)).toBe('3 de ces parcelles sont hors de la zone listée.');
		await rerender(props({ sansCarte: true, horsVue: 1 }));
		expect(textes(cartes(container)[1]).at(-1)).toBe('1 de ces parcelles est hors de la zone listée.');
		expect(textes(cartes(container)[1]).join(' ')).not.toContain('hors de la vue');
	});

	it('parcels of the shown group out of the view, counted by the map: T-26 agreed, nothing at zero or null', async () => {
		const { container, rerender } = await rendre({ horsVue: null });
		const carte = () => cartes(container)[1];
		await fireEvent.click(bascule(carte())!);
		expect(textes(carte())).toHaveLength(3);
		await rerender(props({ horsVue: 1 }));
		expect(textes(carte()).at(-1)).toBe('1 de ces parcelles est hors de la vue.');
		await rerender(props({ horsVue: 4 }));
		expect(textes(carte()).at(-1)).toBe('4 de ces parcelles sont hors de la vue.');
		await rerender(props({ horsVue: 0 }));
		expect(textes(carte())).toHaveLength(3);
	});

	it('one group at a time: showing another one turns the first off', async () => {
		const ongroupe = vi.fn();
		const { container } = await rendre({ ongroupe });
		const [, premiere, seconde] = cartes(container);
		await fireEvent.click(bascule(premiere)!);
		await fireEvent.click(bascule(seconde)!);
		expect(bascule(premiere)!.getAttribute('aria-pressed')).toBe('false');
		expect(bascule(seconde)!.getAttribute('aria-pressed')).toBe('true');
		expect(container.querySelectorAll('[aria-pressed="true"]')).toHaveLength(1);
		expect(ongroupe).toHaveBeenLastCalledWith(EXEMPLE.donnees.groupes[2]);
	});

	it('choosing another parcel turns the group off and tells the map', async () => {
		const ongroupe = vi.fn();
		const { container, rerender } = await rendre({ ongroupe });
		await fireEvent.click(bascule(cartes(container)[1])!);
		ongroupe.mockClear();
		await rerender(props({ ongroupe }, '00000000ZA0002'));
		expect(ongroupe).toHaveBeenCalledWith(null);
		await waitFor(() => expect(cartes(container)).toHaveLength(4));
		expect(container.querySelectorAll('[aria-pressed="true"]')).toHaveLength(0);
	});

	it('changing page turns the group off and tells the map', async () => {
		const ongroupe = vi.fn();
		const page1 = exemple((d) => Object.assign(d, { total: 9, pages: 2 }));
		const { appels } = fetchManuel();
		const { container } = render(ParcelPanel, props({ ongroupe }));
		appels[0].repondre(json(200, page1));
		await waitFor(() => expect(cartes(container)).toHaveLength(4));
		await fireEvent.click(bascule(cartes(container)[1])!);
		ongroupe.mockClear();
		await fireEvent.click(screen.getByRole('button', { name: 'Page suivante' }));
		expect(ongroupe).toHaveBeenCalledWith(null);
		expect(container.querySelectorAll('[aria-pressed="true"]')).toHaveLength(0);
	});
});
