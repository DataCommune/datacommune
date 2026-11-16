// « Données & méthode » lists the datasets of the catalogue only (03-11, decision of the
// orchestrator after the D-15 review). `/v1/datasets` also serves metadata rows outside the
// catalogue (`scoring`, seeded by migration 039, a derived score locked by migration 066) for the
// administration: such a row carries no `hebergement`. The public page neither shows it nor counts
// it, and it raises no console error. Display selection in the load, not a computation; the API
// is unchanged.
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/svelte';
import type { Writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return { page: writable({ url: new URL('https://site.invalid/datasets'), state: {} }) };
});

import { page } from '$app/stores';
import Page from './+page.svelte';
import { load, type DatasetsPageData } from './+page';
import fixture from '$lib/data/__fixtures__/datasets-api.json';
import { GROUPES_CATALOGUE, groupeDuJeu } from '$lib/data/catalogue-groupes';
import { lignesEchelles, lignesEtats } from '$lib/data/lire-une-fiche';
import { TITRE_ACCES_REFUSE, TITRE_DEMANDE_REFUSEE, TITRE_LIMITE, messageLimite } from '$lib/data/panne';

vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn(), goto: vi.fn(), afterNavigate: vi.fn() }));
import { goto } from '$app/navigation';

// The row as migration 039 seeds it and the API serves it since 9750daf.
const SCORING = {
	nom: 'scoring',
	statut: 'proxy_amont',
	derniere_maj: null,
	nombre_lignes: null,
	duree_import_secondes: null,
	cron_schedule: null,
	fraicheur: null,
	etat_pipeline: 'sain',
	prochaine_maj_prevue: null,
	derniere_run: null,
	provenance: null,
	hebergement: null,
	reutilisation: null,
	quarantaine: null,
	limites_source: null
};

function fetchReturning(body: unknown) {
	return vi.fn(
		async () =>
			new Response(JSON.stringify(body), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
	);
}

async function charger(donnees: unknown[]): Promise<DatasetsPageData> {
	const fetch = fetchReturning({ ...fixture, donnees });
	return (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({
		fetch
	})) as DatasetsPageData;
}

const pageStore = page as unknown as Writable<{ url: URL; state: Record<string, unknown> }>;

/** The URL of the page, as the browser and the store of SvelteKit both see it. */
function aLAdresse(recherche: string) {
	pageStore.set({ url: new URL(`https://site.invalid/datasets${recherche}`), state: {} });
	history.replaceState(null, '', `/datasets${recherche}`);
}

beforeEach(() => {
	aLAdresse('');
	// A client navigation of SvelteKit: the store and the address follow the target.
	vi.mocked(goto).mockReset();
	vi.mocked(goto).mockImplementation(async (cible) => {
		const url = new URL(String(cible), 'https://site.invalid/datasets');
		aLAdresse(`${url.search}${url.hash}`);
	});
});
afterEach(() => vi.restoreAllMocks());

// Search aliases of the page (`THEMES_RECHERCHE`), read here on the fixture: the keys a theme finds.
const SERVICES_ET_TRANSPORTS = ['antennes', 'connectivite', 'equipements', 'gares', 'scolaire', 'sirene', 'transports'];
const sousLaRecherche = (theme: string) =>
	theme === 'services et transports' ? fixture.donnees.filter((jeu) => SERVICES_ET_TRANSPORTS.includes(jeu.nom)) : [];
const autourDeTous = fixture.donnees.filter((jeu) => groupeDuJeu(jeu.nom) === 'quotidien');
/** Chips the page shows for these datasets: « Tout », then every group that holds one. */
const pucesAttendues = (jeux: { nom: string }[]) => [
	`Tout · ${jeux.length}`,
	...GROUPES_CATALOGUE.map((groupe) => [groupe.titre, jeux.filter((jeu) => groupeDuJeu(jeu.nom) === groupe.id).length] as const)
		.filter(([, n]) => n > 0)
		.map(([titre, n]) => `${titre} · ${n}`)
];

/** Name of a mounted detail: the row it follows names it (aria-labelledby), the detail does not repeat it. */
const nomDuDetail = (detail: Element) =>
	detail.ownerDocument.getElementById(detail.getAttribute('aria-labelledby')!)?.querySelector('.dataset-ligne-nom')?.textContent;

describe('/datasets — datasets of the catalogue only', () => {
	it('indexes names, producers and explicit themes without another API call', async () => {
		const fetch = fetchReturning(fixture);
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({
			fetch
		})) as DatasetsPageData;
		const { container, getByRole } = render(Page, { props: { data } });
		const recherche = getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });

		await fireEvent.input(recherche, { target: { value: 'GEORISQUES' } });
		await waitFor(() => expect([...container.querySelectorAll('article')].map((item) => item.id)).toEqual(['georisques']));

		await fireEvent.input(recherche, { target: { value: 'energie' } });
		await waitFor(() =>
			expect([...container.querySelectorAll('article')].map((item) => item.id)).toEqual([
				'dpe',
				'bdnb',
				'batiments'
			])
		);

		// Review of 2026-11-11: the rows follow the groups of the catalogue, no longer the served order.
		await fireEvent.input(recherche, { target: { value: 'insee' } });
		await waitFor(() =>
			expect([...container.querySelectorAll('article')].map((item) => item.id)).toEqual([
				'equipements',
				'sirene',
				'demographie',
				'revenus'
			])
		);

		await fireEvent.input(recherche, { target: { value: 'services et transports' } });
		await waitFor(() => expect(container.querySelectorAll('article')).toHaveLength(7));

		await fireEvent.input(recherche, { target: { value: 'immobilier' } });
		await waitFor(() => {
			expect(container.querySelector('article#encadrement_loyers')).toBeTruthy();
			expect(container.querySelector('article#copropriete')).toBeTruthy();
		});

		await fireEvent.input(recherche, { target: { value: 'risques et urbanisme' } });
		await waitFor(() => expect(container.querySelector('article#permis')).toBeTruthy());
		expect(container.querySelector('article#permis')?.textContent).toContain('Non qualifié');
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('combines local search and theme counts, then clears without fetching again', async () => {
		const fetch = fetchReturning(fixture);
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({
			fetch
		})) as DatasetsPageData;
		const { container, getByRole } = render(Page, { props: { data } });
		const recherche = getByRole('searchbox', { name: 'Rechercher une source ou un producteur' });
		const filtres = getByRole('group', { name: 'Filtrer les sources par thème' });

		await fireEvent.input(recherche, { target: { value: 'services et transports' } });
		await waitFor(() => expect(within(filtres).getByRole('button', { name: 'Tout · 7' })).toBeTruthy());
		const autour = sousLaRecherche('services et transports').filter((jeu) => groupeDuJeu(jeu.nom) === 'quotidien');
		expect(autour.length).toBeGreaterThan(0);
		expect(autour.length).toBeLessThan(7);
		await fireEvent.click(within(filtres).getByRole('button', { name: `Autour de l’adresse · ${autour.length}` }));
		await waitFor(() => expect(container.querySelectorAll('article')).toHaveLength(autour.length));
		expect(container.querySelector('article#connectivite')).toBeNull();

		await fireEvent.click(getByRole('button', { name: 'Effacer la recherche' }));
		await waitFor(() => expect(recherche).toHaveValue(''));
		expect(container.querySelectorAll('article')).toHaveLength(autourDeTous.length);
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	// Plan 04.5-11 (B7, UI-016) replaces the three links « Couverture », « Comprendre un chiffre »,
	// « Sources »: the two entries of D come first, the coverage stays one link away; same targets.
	it('offers the two entries of the catalogue, then the coverage, whose unique targets are focusable section headings', async () => {
		const data = await charger(fixture.donnees);
		const { container, getByRole } = render(Page, { props: { data } });
		const sommaire = getByRole('navigation', { name: 'Accès directs aux données et à la méthode' });
		const liens = within(sommaire).getAllByRole('link');
		const attendus = [
			['Les sources', '#catalogue-titre'],
			['Lire une fiche', '#lire-titre'],
			['Couverture', '#couverture-titre']
		];

		expect(liens.map((lien) => [lien.textContent?.trim(), lien.getAttribute('href')])).toEqual(attendus);
		// Lot 8 (4.9): three entries of one style, none set apart.
		expect(new Set(liens.map((lien) => lien.className))).toEqual(new Set([liens[0].className]));
		expect(liens[0].className).toContain('methode-entree');
		for (const [, href] of attendus) {
			const cibles = container.querySelectorAll(href);
			expect(cibles).toHaveLength(1);
			expect(cibles[0].tagName).toBe('H2');
			expect(cibles[0]).toHaveAttribute('tabindex', '-1');
		}
		const ids = [...container.querySelectorAll('[id]')].map((element) => element.id);
		expect(new Set(ids).size).toBe(ids.length);
		const couverture = container.querySelector('section[aria-labelledby="couverture-titre"]')!;
		expect(couverture.textContent).toContain('Ce site sert les données de');
		expect(couverture.textContent).not.toContain('Cette instance');
		expect(document.querySelector('meta[name="description"]')?.getAttribute('content')).toContain('Ce que couvre ce site');
	});

	it('drops a served entry without hebergement from the page data', async () => {
		const data = await charger([...fixture.donnees, SCORING]);
		expect(data.panne).toBeNull();
		expect(data.jeux.map((jeu) => jeu.nom)).not.toContain('scoring');
		expect(data.jeux).toHaveLength(fixture.donnees.length);
	});

	it('neither renders nor counts it, and raises no console error', async () => {
		const error = vi.spyOn(console, 'error').mockImplementation(() => {});
		const data = await charger([...fixture.donnees, SCORING]);
		const { container } = render(Page, { props: { data } });

		expect(container.querySelector('article#scoring')).toBeNull();
		expect(container.textContent).not.toContain('Non servi');
		const n = fixture.donnees.length;
		const filtres = within(container).getByRole('group', { name: 'Filtrer les sources par thème' });
		expect(within(filtres).getAllByRole('button')[0].textContent?.trim()).toBe(`Tout · ${n}`);
		expect(container.querySelectorAll('article')).toHaveLength(n);
		expect(error).not.toHaveBeenCalled();
	});
});

describe('/datasets — a failed call is typed (WR-01)', () => {
	it('keeps only links whose targets exist when the catalogue call fails', async () => {
		const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: 'indisponible' }), { status: 503 }));
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({ fetch })) as DatasetsPageData;
		const { container, getByRole } = render(Page, { props: { data } });
		const liens = within(getByRole('navigation', { name: 'Accès directs aux données et à la méthode' })).getAllByRole('link');

		expect(liens.map((lien) => lien.getAttribute('href'))).toEqual(['#lire-titre', '#couverture-titre']);
		for (const lien of liens) expect(container.querySelectorAll(lien.getAttribute('href')!)).toHaveLength(1);
		expect(container.querySelector('#catalogue-titre')).toBeNull();
	});

	it('a shared-key rate limit (429 with Retry-After) carries the rate-limit sentence, rendered in the notice', async () => {
		const fetch = vi.fn(
			async () =>
				new Response(JSON.stringify({ detail: 'texte serveur' }), {
					status: 429,
					headers: { 'Content-Type': 'application/json', 'Retry-After': '60' }
				})
		);
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({
			fetch
		})) as DatasetsPageData;
		expect(data.panne?.statut).toBe(429);
		expect(data.panne?.message).toBe(messageLimite(60));
		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain(data.panne!.message!);
		expect(alerte.textContent).not.toContain("Le service n'a pas répondu");
		expect(alerte.textContent).not.toContain('texte serveur');
	});

	// 03-17 D-15 G2: the title of the notice agrees with its body. A 4xx is an answer of the
	// service (refused request, limit, refused access), never « momentanément indisponible »,
	// which stays the title of a 5xx or a network failure.
	async function panneRendue(status: number) {
		const fetch = vi.fn(
			async () =>
				new Response(JSON.stringify({ detail: 'texte serveur' }), {
					status,
					headers: { 'Content-Type': 'application/json' }
				})
		);
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({
			fetch
		})) as DatasetsPageData;
		return render(Page, { props: { data } }).getByRole('alert');
	}

	it.each([
		[409, TITRE_DEMANDE_REFUSEE],
		[422, TITRE_DEMANDE_REFUSEE],
		[429, TITRE_LIMITE],
		[403, TITRE_ACCES_REFUSE]
	])('HTTP %i: the notice is titled « %s », never « indisponible »', async (status, titre) => {
		const alerte = await panneRendue(status);
		expect(alerte.textContent).toContain(titre);
		expect(alerte.textContent).not.toContain('indisponible');
	});

	it('HTTP 503 keeps the outage title and text', async () => {
		const alerte = await panneRendue(503);
		expect(alerte.textContent).toContain('La liste des jeux de données est momentanément indisponible');
		expect(alerte.textContent).toContain("Le service n'a pas répondu");
	});
});

// Plan 04.5-11 (B7, UI-017, UI-029, UI-030): the source read is `jeu` in the URL, read by the page and
// never by the loader; it is checked against the served keys and never reflected when unknown. Each
// row links to its source, the other parameters of the URL kept.
describe('/datasets — the source read is in the URL (`jeu`)', () => {
	it('mounts the detail of `?jeu=<key>` once, after its row, and no other', async () => {
		aLAdresse('?jeu=dvf');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const details = container.querySelectorAll('.dataset-detail');
		expect(details).toHaveLength(1);
		expect(nomDuDetail(details[0])).toBe('Demandes de valeurs foncières (DVF)');
		expect(container.querySelector('article#dvf')!.nextElementSibling?.contains(details[0])).toBe(true);
		expect(container.querySelectorAll('article')).toHaveLength(fixture.donnees.length);
	});

	it('shows no detail without `jeu` when several datasets remain', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelectorAll('.dataset-detail')).toHaveLength(0);
		// 04.8-06: the breadcrumb marks the page itself; no row of the catalogue is current.
		expect(container.querySelectorAll('.methode-cards li').length).toBeGreaterThan(1);
		expect(container.querySelector('.methode-cards [aria-current]')).toBeNull();
	});

	it('renders no detail for an unknown key and never writes it into the page', async () => {
		aLAdresse('?jeu=%3Cscript%3Eintrus%3C%2Fscript%3E');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelectorAll('.dataset-detail')).toHaveLength(0);
		expect(container.innerHTML).not.toContain('intrus');
		for (const lien of container.querySelectorAll('a')) expect(lien.getAttribute('href') ?? '').not.toContain('intrus');
	});

	// Review M1: an invalid key is not an absent one. The single result of the search opens when `jeu` is
	// absent or names a served dataset the search filtered out of the list; an unknown key opens nothing.
	it('an unknown key opens no detail, even when the search leaves a single dataset', async () => {
		aLAdresse('?q=ademe&jeu=inconnu');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelectorAll('article')).toHaveLength(1);
		expect(container.querySelectorAll('.dataset-detail')).toHaveLength(0);
		expect(container.innerHTML).not.toContain('inconnu');
		for (const lien of container.querySelectorAll('a')) expect(lien.getAttribute('href') ?? '').not.toContain('inconnu');
	});

	it('a served key filtered out by the search still opens the single dataset left', async () => {
		aLAdresse('?q=ademe&jeu=permis');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect([...container.querySelectorAll('article')].map((item) => item.id)).toEqual(['dpe']);
		const details = container.querySelectorAll('.dataset-detail');
		expect(details).toHaveLength(1);
		expect(nomDuDetail(details[0])).toBe(
			fixture.donnees.find((jeu) => jeu.nom === 'dpe')!.provenance!.nom
		);
	});

	// D45-11: an old link `/datasets#<key>` opens the detail of its source, in a replaced entry.
	it('migrates an old anchor `/datasets#dpe` to the detail of dpe when the URL carries no `jeu`', async () => {
		aLAdresse('#dpe');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		await waitFor(() => expect(goto).toHaveBeenCalledTimes(1));
		const [cible, options] = vi.mocked(goto).mock.calls[0];
		expect(cible).toBe('/datasets?jeu=dpe#dpe');
		expect(options?.replaceState).toBe(true);
		await waitFor(() => expect(container.querySelectorAll('.dataset-detail')).toHaveLength(1));
		expect(container.querySelector('article#dpe a')).toHaveAttribute('aria-current', 'true');
	});

	// Review 04.5-11: an unknown `jeu` is not an absent one; the fragment is then only the row target.
	it('an unknown `jeu` with a served fragment selects nothing and never navigates to that fragment', async () => {
		aLAdresse('?q=ademe&jeu=inconnu#dpe');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(goto).not.toHaveBeenCalled();
		expect(container.querySelectorAll('article')).toHaveLength(1);
		expect(container.querySelectorAll('.dataset-detail')).toHaveLength(0);
		expect(location.search).toBe('?q=ademe&jeu=inconnu');
	});

	it('links every row to its source, keeping the search and the filter of the URL and changing `jeu` only', async () => {
		aLAdresse('?q=loyers&filtre=prix&jeu=dpe');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		// Under a group title of the catalogue, a row is a heading of level 4.
		expect(container.querySelector('article h3')).toBeNull();
		const liens = [...container.querySelectorAll<HTMLAnchorElement>('article h4 a')];
		expect(liens.length).toBeGreaterThan(0);
		for (const lien of liens) {
			const cle = lien.closest('article')!.id;
			const url = new URL(lien.getAttribute('href')!, 'https://site.invalid/datasets');
			expect(url.pathname).toBe('/datasets');
			expect(url.searchParams.get('q')).toBe('loyers');
			expect(url.searchParams.get('filtre')).toBe('prix');
			expect(url.searchParams.get('jeu')).toBe(cle);
			expect(url.hash).toBe(`#${cle}`);
		}
	});
});

// Plan 04.5-11 (B7, UI-030): search (`q`) and filter (`filtre`) are GET parameters read by the page; a
// GET form serves them without JavaScript; with JavaScript a chip, a submitted search and the first
// key typed create a history entry (the next keys replace it), and nothing is read from the API again.
describe('/datasets — search and filter in the URL', () => {
	const articles = (container: HTMLElement) => [...container.querySelectorAll('article')].map((item) => item.id);
	const puce = (container: HTMLElement, id: string) =>
		container.querySelector<HTMLButtonElement>(`[role="group"] button[name="filtre"][value="${id}"]`)!;
	const champDe = (getByRole: (role: string, options: { name: string }) => HTMLElement) =>
		getByRole('searchbox', { name: 'Rechercher une source ou un producteur' }) as HTMLInputElement;

	it('reads q and filtre in the URL: the list, the counts and the field are those of the URL', async () => {
		aLAdresse('?q=insee&filtre=territoire');
		const { container, getByRole } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(articles(container)).toEqual(['demographie', 'revenus']);
		expect(champDe(getByRole)).toHaveValue('insee');
		expect(puce(container, 'territoire')).toHaveAttribute('aria-pressed', 'true');
		expect(puce(container, 'tous')).toHaveAttribute('aria-pressed', 'false');
		expect(puce(container, 'tous').textContent?.trim()).toBe('Tout · 4');
	});

	it('brings an unknown filter back to « Tout » and never writes it into the page', async () => {
		aLAdresse('?filtre=%22%3E%3Cb%3Eintrus');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(articles(container)).toHaveLength(fixture.donnees.length);
		expect(puce(container, 'tous')).toHaveAttribute('aria-pressed', 'true');
		expect(container.innerHTML).not.toContain('intrus');
	});

	// Review of 2026-11-11: the state filters left with their chips; a link kept from before shows everything.
	it.each(['disponibilite', 'hors-couverture', 'non-qualifies'])('a former state filter `%s` reads as « Tout », every dataset listed', async (ancien) => {
		aLAdresse(`?filtre=${ancien}`);
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(articles(container)).toHaveLength(fixture.donnees.length);
		expect(puce(container, 'tous')).toHaveAttribute('aria-pressed', 'true');
		for (const lien of container.querySelectorAll('article a')) expect(lien.getAttribute('href')).not.toContain('filtre=');
	});

	it('renders q escaped, as the value of the field only', async () => {
		aLAdresse('?q=%3Cimg%20src%3Dx%3E');
		const { container, getByRole } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelector('img')).toBeNull();
		expect(champDe(getByRole)).toHaveValue('<img src=x>');
	});

	it('is a GET form: q, the chips as submit buttons, a default button keeping the filter, jeu kept when served', async () => {
		aLAdresse('?filtre=logement&jeu=permis');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const formulaire = container.querySelector('form[role="search"]')!;
		expect(formulaire.getAttribute('method')).toBe('get');
		expect(formulaire.getAttribute('action')).toBe('/datasets');
		expect(formulaire.querySelector('input[type="search"]')?.getAttribute('name')).toBe('q');
		const boutons = [...formulaire.querySelectorAll<HTMLButtonElement>('button[type="submit"]')];
		// The first submit button is the default one (Enter in the field): it keeps the current filter.
		expect(boutons[0].textContent?.trim()).toBe('Rechercher');
		expect([boutons[0].name, boutons[0].value]).toEqual(['filtre', 'logement']);
		// Typing filters at once: the button is kept for the form without JavaScript, and hidden with it.
		expect(boutons[0].hidden).toBe(true);
		expect(boutons.slice(1).map((bouton) => [bouton.name, bouton.value])).toEqual([
			['filtre', 'tous'],
			['filtre', 'prix'],
			['filtre', 'logement'],
			['filtre', 'quotidien'],
			['filtre', 'risques'],
			['filtre', 'territoire']
		]);
		expect(formulaire.querySelector<HTMLInputElement>('input[type="hidden"][name="jeu"]')?.value).toBe('permis');
	});

	it('sends no filter by its default button under « Tous », and no unknown jeu', async () => {
		aLAdresse('?jeu=inconnu');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const formulaire = container.querySelector('form[role="search"]')!;
		expect(formulaire.querySelector<HTMLButtonElement>('button[type="submit"]')!.hasAttribute('name')).toBe(false);
		expect(formulaire.querySelector('input[name="jeu"]')).toBeNull();
	});

	it('shows the detail of the only dataset left without jeu, and none when several remain', async () => {
		aLAdresse('?q=ademe');
		let rendu = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(articles(rendu.container)).toEqual(['dpe']);
		expect(rendu.container.querySelectorAll('.dataset-detail')).toHaveLength(1);
		expect(rendu.container.querySelector('article#dpe')!.nextElementSibling?.classList.contains('dataset-ouvert')).toBe(true);
		expect(rendu.container.querySelector('article#dpe a')).toHaveAttribute('aria-current', 'true');
		rendu.unmount();
		aLAdresse('?q=energie');
		rendu = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(rendu.container.querySelectorAll('.dataset-detail')).toHaveLength(0);
	});

	it('a chip creates a history entry carrying the search and the source, and reads nothing', async () => {
		aLAdresse('?q=insee&jeu=revenus');
		const fetch = fetchReturning(fixture);
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({ fetch })) as DatasetsPageData;
		const { container } = render(Page, { props: { data } });
		await fireEvent.click(puce(container, 'territoire'));
		expect(goto).toHaveBeenCalledTimes(1);
		const [cible, options] = vi.mocked(goto).mock.calls[0];
		expect(cible).toBe('/datasets?q=insee&filtre=territoire&jeu=revenus');
		expect(options?.replaceState ?? false).toBe(false);
		await waitFor(() => expect(puce(container, 'territoire')).toHaveAttribute('aria-pressed', 'true'));
		expect(articles(container)).toEqual(['demographie', 'revenus']);
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('typing writes q into the URL: an entry for the first key, replaced by the next ones', async () => {
		const { getByRole } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const champ = champDe(getByRole);
		await fireEvent.input(champ, { target: { value: 'e' } });
		await fireEvent.input(champ, { target: { value: 'en' } });
		const appels = vi.mocked(goto).mock.calls;
		expect(appels.map(([cible]) => cible)).toEqual(['/datasets?q=e', '/datasets?q=en']);
		expect(appels.map(([, options]) => options?.replaceState ?? false)).toEqual([false, true]);
		expect(appels.every(([, options]) => options?.keepFocus === true && options?.noScroll === true)).toBe(true);
	});

	it('submitting the search creates an entry with the filter and the source of the URL', async () => {
		aLAdresse('?filtre=prix&jeu=dvf');
		const { getByRole } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const champ = champDe(getByRole);
		await fireEvent.input(champ, { target: { value: 'dvf' } });
		vi.mocked(goto).mockClear();
		await fireEvent.submit(champ.closest('form')!);
		const [cible, options] = vi.mocked(goto).mock.calls.at(-1)!;
		expect(cible).toBe('/datasets?q=dvf&filtre=prix&jeu=dvf');
		expect(options?.replaceState ?? false).toBe(false);
	});
});

// Review of 2026-11-11 (Ianis, the six proposals of the internal note 2026-11-11-page-donnees-methode):
// three panels — the sources, one glossary, the coverage — and the sources read group by group in the
// order of the address fiche. Replaces the five panels of 04.5-11 and 04.8-06 and the four state chips of
// R9 and of lot 8 of 4.9; the search still heads the list and no chip leads to an empty list.
describe('/datasets — three panels, the sources grouped in the reading order of the fiche', () => {
	const libelles = (container: HTMLElement) =>
		within(within(container).getByRole('group', { name: 'Filtrer les sources par thème' }))
			.getAllByRole('button')
			.map((bouton) => bouton.textContent?.trim());

	it('gives three panels: the sources, « Lire une fiche », the coverage', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect([...container.querySelectorAll('h2')].map((titre) => titre.textContent)).toEqual(['Les sources', 'Lire une fiche', 'Couverture']);
		const catalogue = container.querySelector('section[aria-labelledby="catalogue-titre"]')!;
		// The search heads the list: nothing but the title of the section comes before its form.
		expect(catalogue.querySelector('h2')!.nextElementSibling!.firstElementChild).toBe(catalogue.querySelector('form[role="search"]'));
		expect(container.textContent).not.toContain('Portée des objets');
		expect(container.textContent).not.toContain('Le sens des quatre états');
	});

	it('says under the title what the page holds, with the number of sources the API served', async () => {
		const data = await charger(fixture.donnees);
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('h1')!.nextElementSibling?.textContent).toBe(
			`${fixture.donnees.length} sources publiques. Pour chacune : son producteur, sa licence, sa date et ses limites.`
		);
		cleanup();
		const seule = render(Page, { props: { data: await charger(fixture.donnees.slice(0, 1)) } });
		expect(seule.container.querySelector('h1')!.nextElementSibling?.textContent).toBe(
			'1 source publique. Son producteur, sa licence, sa date et ses limites.'
		);
	});

	it('writes no number under the title when the catalogue did not answer', async () => {
		const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: 'indisponible' }), { status: 503 }));
		const data = (await (load as unknown as (event: { fetch: typeof fetch }) => Promise<DatasetsPageData>)({ fetch })) as DatasetsPageData;
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('h1')!.nextElementSibling?.textContent).toBe(
			'Des sources publiques. Pour chacune : son producteur, sa licence, sa date et ses limites.'
		);
		// The notice of the outage is read before the glossary, not after it.
		expect([...container.querySelectorAll('section.panneau > h2')].map((titre) => titre.textContent)).toEqual(['Couverture', 'Lire une fiche']);
		expect(container.querySelector('[role="alert"]')!.closest('section.panneau')?.querySelector('h2')?.textContent).toBe('Couverture');
	});

	it('04.8-06 : fil « Accueil › Données & méthode », h1 d’aujourd’hui, panneaux v4 et légende complète en #lire', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const fil = container.querySelector("nav[aria-label=\"Fil d'Ariane\"]")!;
		expect([...fil.querySelectorAll('li')].map((li) => li.textContent?.trim())).toEqual(['Accueil', 'Données & méthode']);
		expect(container.querySelector('h1')?.textContent).toBe('Données & méthode');
		expect(fil.compareDocumentPosition(container.querySelector('h1')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		// Every h2 opens a panel.
		expect(container.querySelectorAll('section.panneau > h2').length).toBe(container.querySelectorAll('h2').length);
		const lire = container.querySelector('#lire')!;
		const [echelles, etats] = [...lire.querySelectorAll('ul.lignes')];
		expect(echelles.querySelectorAll(':scope > li').length).toBe(lignesEchelles().length);
		expect(etats.querySelectorAll(':scope > li').length).toBe(lignesEtats().length);
		// The anchors of the access links keep their targets, the titles of the panels.
		for (const ancre of ['catalogue-titre', 'couverture-titre', 'lire-titre']) {
			expect(container.querySelector(`a[href="#${ancre}"]`)).not.toBeNull();
			expect(container.querySelector(`h2#${ancre}`)?.getAttribute('tabindex')).toBe('-1');
		}
		// A link kept from before the review still lands on the four states.
		expect(lire.querySelector('#etats-titre')?.textContent).toBe('Quand une valeur manque');
		expect(container.querySelector('.methode-page, .public-article')).toBeNull();
	});

	it('titles each group as the fiche titles its parts, in its order, prices first', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const catalogue = container.querySelector('section[aria-labelledby="catalogue-titre"]')!;
		const groupes = [...catalogue.querySelectorAll('.catalogue-groupe')];
		expect(groupes.map((groupe) => groupe.querySelector('h3')?.textContent?.trim())).toEqual([
			'Prix et ventes',
			'Logement et DPE',
			'Autour de l’adresse',
			'Risques',
			'Territoire et population'
		]);
		expect(catalogue.querySelector('article')?.id).toBe('dvf');
		expect(groupes[1].querySelector('article')?.id).toBe('dpe');
		for (const groupe of groupes) {
			const n = groupe.querySelectorAll('article').length;
			expect(groupe.querySelector('.catalogue-groupe-compte')?.textContent).toBe(n === 1 ? '1 source' : `${n} sources`);
			for (const article of groupe.querySelectorAll('article')) {
				expect(groupeDuJeu(article.id)).toBe(GROUPES_CATALOGUE.find((g) => g.titre === groupe.querySelector('h3')!.textContent!.trim())!.id);
			}
		}
		// Every served dataset is listed once, whatever its group.
		expect([...catalogue.querySelectorAll('article')].map((article) => article.id).sort()).toEqual(fixture.donnees.map((jeu) => jeu.nom).sort());
	});

	it('lists a served key the table of groups does not know, under « Autres sources »', async () => {
		const inconnu = { ...structuredClone(fixture.donnees.find((jeu) => jeu.nom === 'ban')!), nom: 'cle_nouvelle' };
		const { container } = render(Page, { props: { data: await charger([inconnu, ...fixture.donnees]) } });
		const dernier = [...container.querySelectorAll('.catalogue-groupe')].at(-1)!;
		expect(dernier.querySelector('h3')?.textContent?.trim()).toBe('Autres sources');
		expect([...dernier.querySelectorAll('article')].map((article) => article.id)).toEqual(['cle_nouvelle']);
		expect(libelles(container).at(-1)).toBe('Autres sources · 1');
	});

	it('filters by group: « Tout », then one chip per group, with its count', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(libelles(container)).toEqual(pucesAttendues(fixture.donnees));
		expect(libelles(container)).toHaveLength(6);
		expect(container.querySelector('[role="status"]')?.textContent?.trim()).toBe(`Tout : ${fixture.donnees.length} sources`);
	});

	it('a chip lists its group only, under its title, and the status says it', async () => {
		aLAdresse('?filtre=risques');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const attendus = fixture.donnees.filter((jeu) => groupeDuJeu(jeu.nom) === 'risques').map((jeu) => jeu.nom);
		expect([...container.querySelectorAll('article')].map((item) => item.id).sort()).toEqual([...attendus].sort());
		expect([...container.querySelectorAll('.catalogue-groupe h3')].map((titre) => titre.textContent?.trim())).toEqual(['Risques']);
		expect(container.querySelector('[role="status"]')?.textContent?.trim()).toBe(`Risques : ${attendus.length} sources`);
	});

	it('hides a group that leaves no dataset under the current search', async () => {
		aLAdresse('?q=insee');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(libelles(container)).toEqual(['Tout · 4', 'Autour de l’adresse · 2', 'Territoire et population · 2']);
	});

	it('keeps the active group at 0, so the reader sees what empties the list, and the way back', async () => {
		aLAdresse('?q=insee&filtre=risques');
		const { container, getByRole } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(libelles(container)).toEqual(['Tout · 4', 'Autour de l’adresse · 2', 'Risques · 0', 'Territoire et population · 2']);
		expect(container.querySelector('[role="group"] button[value="risques"]')).toHaveAttribute('aria-pressed', 'true');
		expect(container.querySelectorAll('article')).toHaveLength(0);
		expect(container.textContent).toContain('Aucune source ne correspond à cette recherche.');
		expect(getByRole('button', { name: 'Effacer la recherche' })).toBeTruthy();
	});

	it('keeps « Tout » when the search finds nothing: it is the way back', async () => {
		aLAdresse('?q=introuvable');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(libelles(container)).toEqual(['Tout · 0']);
	});

	it('a group emptied without a search offers to show every source', async () => {
		const sansRisques = fixture.donnees.filter((jeu) => groupeDuJeu(jeu.nom) !== 'risques');
		aLAdresse('?filtre=risques');
		const { container, getByRole } = render(Page, { props: { data: await charger(sansRisques) } });
		expect(container.textContent).toContain('Aucune source dans ce thème.');
		await fireEvent.click(getByRole('button', { name: 'Afficher toutes les sources' }));
		expect(vi.mocked(goto).mock.calls.at(-1)![0]).toBe('/datasets');
	});
});

// Review of 2026-11-11: the row says how fresh the source is, in plain text; a badge is kept for what the
// reader must notice — a dataset not qualified, out of coverage or not imported yet.
describe('/datasets — the row says the freshness, a badge marks the exception only', () => {
	it('words an imported dataset with the date of its last import, a live read without a date', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelector('article#dvf .dataset-etat')?.textContent).toBe('Importé le 22 sept. 2026');
		expect(container.querySelector('article#georisques .dataset-etat')?.textContent).toBe('Lu en direct');
		expect(container.querySelector('article#dvf .dataset-badge')).toBeNull();
		expect(container.querySelector('article#georisques .dataset-badge')).toBeNull();
	});

	it('keeps a badge on every dataset that is not read, and on those only', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const lus = fixture.donnees
			.filter((jeu) => jeu.hebergement?.etat === 'heberge' && (jeu.statut === 'disponible' || jeu.statut === 'proxy_amont'))
			.map((jeu) => jeu.nom);
		expect(lus.length).toBeGreaterThan(0);
		expect(lus.length).toBeLessThan(fixture.donnees.length);
		for (const jeu of fixture.donnees) {
			const ligne = container.querySelector(`article[id="${jeu.nom}"]`)!;
			expect(ligne.querySelectorAll('.dataset-badge').length, jeu.nom).toBe(lus.includes(jeu.nom) ? 0 : 1);
			expect(ligne.querySelectorAll('.dataset-etat').length, jeu.nom).toBe(lus.includes(jeu.nom) ? 1 : 0);
		}
		const badges = new Set([...container.querySelectorAll('article .dataset-badge')].map((badge) => badge.textContent));
		expect(badges).toContain('Non qualifié');
		expect(badges).toContain('Pas encore importé');
		expect(badges).not.toContain('Importé');
	});
});

describe('/datasets — the detail of a source in place', () => {
	it('keeps the long wording of a direct read in the detail, never in the row', async () => {
		aLAdresse('?jeu=georisques');
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(container.querySelector('article#georisques .dataset-etat')?.textContent).toBe('Lu en direct');
		expect(container.querySelector('.dataset-detail .dataset-badge')?.textContent).toBe('Lu en direct chez le producteur, sans import');
	});

	it('invites to choose a source only while none is read', async () => {
		const rendu = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(rendu.container.querySelector('.catalogue-invite')?.textContent).toContain('Choisissez une source');
		rendu.unmount();
		aLAdresse('?jeu=dvf');
		const choisi = render(Page, { props: { data: await charger(fixture.donnees) } });
		expect(choisi.container.querySelector('.catalogue-invite')).toBeNull();
	});
});

describe('/datasets — coverage in one sentence, the list folded', () => {
	const couverture = (container: HTMLElement) => container.querySelector<HTMLElement>('section[aria-labelledby="couverture-titre"]')!;

	// Review of 2026-11-11: the grey paragraph left, the page « Couverture et limites » says it; the caution
	// « une absence de couverture ne vaut pas zéro » is read in the glossary, on the row « Hors couverture ».
	it('says the number, folds the list and links to the page of the coverage', async () => {
		const data = await charger(fixture.donnees);
		const { container } = render(Page, { props: { data } });
		const section = couverture(container);
		expect(section.querySelector('h2')?.textContent).toBe('Couverture');
		expect(section.textContent).toContain(`Ce site sert les données de ${data.departementsHeberges.length} départements.`);
		const repli = section.querySelector('details')!;
		expect(repli).not.toHaveAttribute('open');
		expect(repli.querySelector('summary')?.textContent).toBe('Voir la liste des départements');
		expect(repli.querySelectorAll('li')).toHaveLength(data.departementsHeberges.length);
		expect(section.querySelector('.methode-muted')).toBeNull();
		const lien = within(section).getByRole('link', { name: 'Couverture et limites' });
		expect(lien.getAttribute('href')).toBe('/couverture');
		expect(container.querySelector('#lire')!.textContent).toContain('Une absence de couverture ne vaut pas zéro.');
	});

	it('writes the list out for paper, outside the fold', async () => {
		const data = await charger(fixture.donnees);
		const { container } = render(Page, { props: { data } });
		const papier = couverture(container).querySelector('.methode-impression')!;
		expect(papier.closest('details')).toBeNull();
		expect(papier.querySelectorAll('li')).toHaveLength(data.departementsHeberges.length);
	});

	it('agrees with one departement', async () => {
		const seul = structuredClone(fixture.donnees).map((jeu) =>
			jeu.hebergement?.etat === 'heberge' ? { ...jeu, hebergement: { ...jeu.hebergement, departements: ['44'] } } : jeu
		);
		const { container } = render(Page, { props: { data: await charger(seul) } });
		expect(couverture(container).textContent).toContain('Ce site sert les données d’un département.');
	});
});

describe('/datasets — one glossary: the four states inside « Lire une fiche »', () => {
	it('gives one row per state, its title, its lead and every label with its sentence, the labels folded', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const lire = container.querySelector('#lire')!;
		expect([...lire.querySelectorAll('h3')].map((titre) => titre.textContent)).toEqual(['Les échelles', 'Les états', 'Quand une valeur manque']);
		expect(lire.textContent).toContain('Une valeur absente n’est jamais affichée comme un zéro. Quatre cas, toujours distingués :');
		const lignes = [...lire.querySelectorAll('.methode-etat')];
		expect(lignes.map((ligne) => ligne.querySelector('h4')?.textContent)).toEqual(['Zéro', 'Manque', 'Hors couverture', 'Panne']);
		expect(lignes.map((ligne) => ligne.querySelectorAll('details dt').length)).toEqual([0, 7, 3, 1]);
		expect(lignes.map((ligne) => ligne.querySelector('summary')?.textContent ?? null)).toEqual([
			null,
			'Voir les 7 libellés',
			'Voir les 3 libellés',
			'Voir le libellé'
		]);
		for (const repli of lire.querySelectorAll('.methode-etat details')) expect(repli).not.toHaveAttribute('open');
		for (const dd of container.querySelectorAll('.methode-etat dd')) expect(dd.textContent?.trim()).not.toBe('');
		expect(lignes[0].textContent).toContain('La source recense zéro');
		expect(lignes[2].textContent).toContain('Une absence de couverture ne');
		// No technical key of a state is ever displayed.
		expect(lire.textContent).not.toMatch(/not_reported|out_of_coverage|temporary_error/);
	});

	it('writes the labels out for paper, outside the folds', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const papier = [...container.querySelectorAll('#lire .methode-etat .methode-impression')];
		expect(papier.map((bloc) => bloc.querySelectorAll('dt').length)).toEqual([7, 3, 1]);
		for (const bloc of papier) expect(bloc.closest('details')).toBeNull();
	});

	it('says once, under the scales, that a value does not move from one scale to another', async () => {
		const { container } = render(Page, { props: { data: await charger(fixture.donnees) } });
		const rappel = container.querySelector('#lire .methode-rappel')!;
		expect(rappel.textContent).toContain('Une valeur ne se transpose pas d’une échelle à une autre : ce qui vaut pour une commune ne vaut pas pour une adresse.');
		expect(rappel.textContent).toMatch(/Un DPE(?: \([^)]+\))? est un document, pas un logement : un même logement peut en avoir connu plusieurs\./);
		// Under the list of the scales, in its column.
		expect(rappel.previousElementSibling).toBe(container.querySelector('#lire ul.lignes'));
	});
});
