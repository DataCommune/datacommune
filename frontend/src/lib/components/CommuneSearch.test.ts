import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, within } from '@testing-library/svelte';

// The component's only data source is this instance's API (D-42): the search function is
// replaced by a double, and navigation is observed through `goto`.
vi.mock('$lib/api/communes', () => ({ searchCommunes: vi.fn() }));
vi.mock('$lib/api/adresses', () => ({ searchAdresses: vi.fn() }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import {
	searchCommunes,
	type CommuneSearchResult,
	type CommunesSearchResponse
} from '$lib/api/communes';
import {
	type CorrespondanceNumeroRecherche,
	searchAdresses,
	type RechercheAdresses,
	type ResultatRechercheAdresse
} from '$lib/api/adresses';
import type { DatasetHosting } from '$lib/api/admin';
import { ApiError } from '$lib/api/client';
import { TITRE_LIMITE, messageLimite } from '$lib/data/panne';
import {
	ANNONCE_CODE_POSTAL_HORS_COUVERTURE,
	LIEN_COUVERTURE,
	TITRE_CODE_POSTAL_HORS_COUVERTURE
} from '$lib/data/recherche-code-postal';
import { goto } from '$app/navigation';
import CommuneSearch from './CommuneSearch.svelte';

const mockSearch = searchCommunes as unknown as ReturnType<typeof vi.fn>;
const mockAdresses = searchAdresses as unknown as ReturnType<typeof vi.fn>;
const mockGoto = goto as unknown as ReturnType<typeof vi.fn>;

function commune(code: string, nom: string, overrides: Partial<CommuneSearchResult> = {}) {
	return {
		code_insee: code,
		nom,
		code_postal: null,
		departement: code.startsWith('97') ? code.slice(0, 3) : code.slice(0, 2),
		population: 1000,
		population_annee: null,
		population_source: 'geo_communes',
		hebergement: { etat: 'heberge', motif: null, departements: [] },
		...overrides
	} satisfies CommuneSearchResult;
}

const LE_PIN_79 = commune('79211', 'Le Pin', {
	hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] }
});
const LE_PIN_35 = commune('35999', 'Le Pin', { code_postal: '35170' });

const OUTSIDE: DatasetHosting = {
	etat: 'hors_couverture',
	motif: 'territoire_hors_perimetre',
	departements: []
};

/** The envelope `searchCommunes` returns: results, and the meta when the API serves one. */
function envelope(
	donnees: CommuneSearchResult[],
	ban?: DatasetHosting
): CommunesSearchResponse {
	return ban ? { donnees, meta: { hebergement: { ban } } } : { donnees };
}

const HEBERGE: DatasetHosting = { etat: 'heberge', motif: null, departements: ['85'] };

function adresse(
	banId: string,
	numero: string,
	voie: string,
	overrides: Partial<ResultatRechercheAdresse> = {}
): ResultatRechercheAdresse {
	return {
		type_resultat: 'adresse',
		ban_id: banId,
		numero,
		rep: null,
		nom_voie: voie,
		code_postal: '85000',
		code_commune: '85191',
		nom_commune: 'La Roche-sur-Yon',
		departement: { code: '85', nom: 'Vendée' },
		...overrides
	};
}

/** The answer `searchAdresses` returns: served order, typed results, BAN hosting. */
function adresses(
	resultats: ResultatRechercheAdresse[],
	ordre: RechercheAdresses['ordre_types'] = ['adresse', 'commune'],
	ban: RechercheAdresses['hebergement']['ban'] = HEBERGE,
	correspondanceNumero: CorrespondanceNumeroRecherche | null = null
): RechercheAdresses {
	return { ordre_types: ordre, resultats, hebergement: { ban }, correspondance_numero: correspondanceNumero };
}

const LILAS = adresse('85191_0420_00012', '12', 'Rue des Lilas');

// The phase 3 suites below exercise the communes-only field (`types: ['commune']`, the comparator
// of communes); the single field communes + adresses (default `types`) has its own suites.
function setup(props: Record<string, unknown> = {}) {
	const view = render(CommuneSearch, { props: { types: ['commune'], ...props } });
	const input = view.getByRole('combobox', { name: 'Rechercher une commune' });
	// Options of the listbox only: the departement <select> carries its own <option>s.
	const listOptions = () => within(view.getByRole('listbox')).getAllByRole('option');
	return { ...view, input, listOptions };
}

async function type(input: HTMLElement, value: string) {
	await fireEvent.input(input, { target: { value } });
}

/** Lets a resolved search promise reach the component. */
async function flush() {
	await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
	vi.useFakeTimers();
	mockSearch.mockReset();
	mockAdresses.mockReset();
	mockGoto.mockReset();
	mockSearch.mockResolvedValue(envelope([]));
	mockAdresses.mockResolvedValue(adresses([]));
});

afterEach(() => {
	vi.useRealTimers();
});

describe('CommuneSearch — requests', () => {
	it('sends no request under two characters', async () => {
		const { input } = setup();

		await type(input, 'l');
		await vi.advanceTimersByTimeAsync(1000);

		expect(mockSearch).not.toHaveBeenCalled();
	});

	it('sends one request 200 ms after the last keystroke, from two characters', async () => {
		const { input } = setup();

		await type(input, 'le');
		await vi.advanceTimersByTimeAsync(100);
		await type(input, 'le p');
		await vi.advanceTimersByTimeAsync(199);
		expect(mockSearch).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);

		expect(mockSearch).toHaveBeenCalledTimes(1);
		expect(mockSearch.mock.calls[0][0]).toBe('le p');
		expect(mockSearch.mock.calls[0][1]).toMatchObject({ limit: 8 });
	});

	// 04.7-09: the arrondissements ride the search, under their commune, and Paris is listed once.
	it('asks for the arrondissements, and lists Paris once with its arrondissement told as such', async () => {
		const paris = commune('75056', 'Paris', { departement: '75', type_zone: 'commune', commune_parent: null });
		const arr12 = commune('75112', 'Paris 12e Arrondissement', {
			departement: '75',
			type_zone: 'arrondissement',
			commune_parent: '75056'
		});
		mockSearch.mockResolvedValue(envelope([paris, arr12]));
		const { input, listOptions } = setup();

		await type(input, 'paris');
		await vi.advanceTimersByTimeAsync(200);

		expect(mockSearch.mock.calls[0][1]).toMatchObject({ inclureArrondissements: true });
		const options = listOptions();
		expect(options.map((o) => o.querySelector('.cs-option-name')?.textContent)).toEqual(['Paris', 'Paris 12e Arrondissement', undefined]);
		expect(options[0].textContent).not.toContain('Arrondissement de Paris');
		expect(options[1].querySelector('.cs-option-meta')?.textContent).toContain('Arrondissement de Paris');
	});

	it('the option can be turned off', async () => {
		const { input } = setup({ inclureArrondissements: false });
		await type(input, 'paris');
		await vi.advanceTimersByTimeAsync(200);
		expect(mockSearch.mock.calls[0][1]).toMatchObject({ inclureArrondissements: false });
	});

	it('never renders the answer of a superseded request, and aborts it', async () => {
		let resolveFirst!: (res: CommunesSearchResponse) => void;
		mockSearch
			.mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)))
			.mockResolvedValueOnce(envelope([LE_PIN_35]));
		const { input, listOptions } = setup();

		await type(input, 'le');
		await vi.advanceTimersByTimeAsync(200);
		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		resolveFirst(envelope([commune('11111', 'Lescure')]));
		await flush();

		const firstSignal = mockSearch.mock.calls[0][3] as AbortSignal;
		expect(firstSignal.aborted).toBe(true);
		const names = listOptions().map((option) => option.textContent ?? '');
		expect(names.some((name) => name.includes('Lescure'))).toBe(false);
		expect(names[0]).toContain('Le Pin');
	});

	it('keeps the departement filter while typing', async () => {
		const { input, getByRole } = setup();

		await fireEvent.change(getByRole('combobox', { name: 'Département' }), {
			target: { value: '79' }
		});
		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);

		expect(mockSearch.mock.calls.at(-1)?.[1]).toMatchObject({ departement: '79', limit: 8 });
	});
});

describe('CommuneSearch — listbox and keyboard', () => {
	async function openWith(rows: CommuneSearchResult[], props: Record<string, unknown> = {}) {
		mockSearch.mockResolvedValue(envelope(rows));
		const view = setup(props);
		await type(view.input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		return view;
	}

	it('offers at most eight communes, then « Voir tous les résultats »', async () => {
		const rows = Array.from({ length: 10 }, (_, i) => commune(`350${10 + i}`, `Commune ${i}`));
		const { listOptions, input } = await openWith(rows);

		const options = listOptions();
		expect(options).toHaveLength(9);
		expect(options[8].textContent).toContain('Voir tous les résultats pour «\u202fle pin\u202f»');
		expect(input.getAttribute('aria-expanded')).toBe('true');
	});

	it('shows name, departement and postal code, and the badge of a commune out of coverage', async () => {
		const { listOptions } = await openWith([LE_PIN_79, LE_PIN_35]);

		const [outside, hosted] = listOptions();
		expect(outside.textContent).toContain('Deux-Sèvres\u00a0(79)');
		expect(outside.textContent).toContain('Hors couverture');
		expect(hosted.textContent).toContain('Ille-et-Vilaine\u00a0(35) · 35170');
		expect(hosted.textContent).not.toContain('Hors couverture');
	});

	it('moves aria-activedescendant with the arrows and opens the active fiche on Enter', async () => {
		const { input, listOptions } = await openWith([LE_PIN_79, LE_PIN_35]);
		const options = listOptions();

		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(input.getAttribute('aria-activedescendant')).toBe(options[1].id);
		await fireEvent.keyDown(input, { key: 'ArrowUp' });
		expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
		expect(options[0].getAttribute('aria-selected')).toBe('true');

		await fireEvent.keyDown(input, { key: 'Enter' });

		expect(mockGoto).toHaveBeenCalledWith('/commune/79211');
	});

	it('hands the chosen commune to onselect when given (comparator mode)', async () => {
		const onselect = vi.fn();
		const { input } = await openWith([LE_PIN_35], { onselect });

		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });

		expect(onselect).toHaveBeenCalledWith(LE_PIN_35);
		expect(mockGoto).not.toHaveBeenCalled();
	});

	it('Enter on « Voir tous les résultats » opens the results page with q and the filter', async () => {
		const { input, getByRole } = await openWith([LE_PIN_79], { initialDepartement: '79' });

		await fireEvent.keyDown(input, { key: 'ArrowUp' });
		expect(input.getAttribute('aria-activedescendant')).toBe(
			getByRole('option', { name: /Voir tous les résultats/ }).id
		);
		await fireEvent.keyDown(input, { key: 'Enter' });

		expect(mockGoto).toHaveBeenCalledWith('/communes?q=le+pin&departement=79');
	});

	it('Escape closes the list, then clears the field', async () => {
		const { input, queryByRole } = await openWith([LE_PIN_35]);

		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(queryByRole('listbox')).toBeNull();
		expect(input.getAttribute('aria-expanded')).toBe('false');
		expect((input as HTMLInputElement).value).toBe('le pin');

		await fireEvent.keyDown(input, { key: 'Escape' });
		expect((input as HTMLInputElement).value).toBe('');
	});

	it('Tab leaves without choosing', async () => {
		const { input, container, queryByRole } = await openWith([LE_PIN_35]);
		const select = container.querySelector('select')!;

		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Tab' });
		await fireEvent.focusOut(input, { relatedTarget: select });

		expect(queryByRole('listbox')).toBeNull();
		expect(mockGoto).not.toHaveBeenCalled();
	});
});

describe('CommuneSearch — announced states', () => {
	it('announces « Recherche en cours… » while the request runs', async () => {
		mockSearch.mockReturnValue(new Promise(() => {}));
		const { input, getByRole } = setup();

		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);

		expect(getByRole('status').textContent).toContain('Recherche en cours…');
	});

	it('announces the number of communes, singular and plural', async () => {
		mockSearch
			.mockResolvedValueOnce(envelope([LE_PIN_35]))
			.mockResolvedValueOnce(envelope([LE_PIN_79, LE_PIN_35]));
		const { input, getByRole } = setup();

		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		expect(getByRole('status').textContent).toContain('1 commune proposée');

		await type(input, 'le pi');
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		expect(getByRole('status').textContent).toContain('2 communes proposées');
	});

	it('0 result: the « Aucun résultat » contract, the filter removal, « Voir tous les résultats » kept', async () => {
		mockSearch.mockResolvedValue(envelope([]));
		const { input, getByText, getByRole } = setup({ initialDepartement: '79' });

		await type(input, 'zzz');
		await vi.advanceTimersByTimeAsync(200);
		await flush();

		expect(getByText('Aucune commune trouvée pour « zzz »')).toBeTruthy();
		expect(getByText(/Cela ne veut pas dire qu'elle n'existe pas\./)).toBeTruthy();
		expect(getByRole('option', { name: /Voir tous les résultats pour «\u202fzzz\u202f»/ })).toBeTruthy();

		await fireEvent.click(getByRole('button', { name: 'Retirer le filtre département' }));
		await vi.advanceTimersByTimeAsync(200);

		expect(mockSearch.mock.calls.at(-1)?.[1]).toMatchObject({ departement: undefined });
		expect((getByRole('combobox', { name: 'Département' }) as HTMLSelectElement).value).toBe('');
	});

	it('0 result: another spelling is offered as a search link (04.7-14, R16)', async () => {
		mockSearch.mockResolvedValue(envelope([]));
		const { input, getByRole, queryByRole } = setup();

		await type(input, 'St Herblain');
		await vi.advanceTimersByTimeAsync(200);
		await flush();

		expect(getByRole('link', { name: 'Essayer « Saint Herblain »' }).getAttribute('href')).toBe('/communes?q=Saint+Herblain');

		await type(input, 'zzz');
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		expect(queryByRole('link', { name: /Essayer/ })).toBeNull();
	});

	it('0 result with a departement filter: the other spelling keeps the filter', async () => {
		mockSearch.mockResolvedValue(envelope([]));
		const { input, getByRole } = setup({ initialDepartement: '85' });

		await type(input, 'St Martin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();

		expect(getByRole('link', { name: 'Essayer « Saint Martin »' }).getAttribute('href')).toBe(
			'/communes?q=Saint+Martin&departement=85'
		);
	});

	it('an outage is not an empty result: contract text and « Relancer la recherche »', async () => {
		mockSearch
			.mockRejectedValueOnce(new Error('503'))
			.mockResolvedValueOnce(envelope([LE_PIN_35]));
		const { input, getByText, getByRole, queryByText } = setup();

		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();

		expect(
			getByText('La recherche est momentanément indisponible. Réessayez dans un instant.')
		).toBeTruthy();
		expect(queryByText(/Aucune commune trouvée/)).toBeNull();

		await fireEvent.click(getByRole('button', { name: 'Relancer la recherche' }));
		await flush();

		expect(mockSearch).toHaveBeenCalledTimes(2);
		expect(within(getByRole('listbox')).getAllByRole('option')[0].textContent).toContain('Le Pin');
	});

	it('a shared-key rate limit (429) is worded as such, never as an outage (WR-01)', async () => {
		mockSearch.mockRejectedValueOnce(new ApiError(429, 'phrase du client', true, 'Trop de requetes', { retryAfter: 60, anonyme: true }));
		const { input, container, getByRole, queryByText } = setup();

		await type(input, 'le pin');
		await vi.advanceTimersByTimeAsync(200);
		await flush();

		expect(container.querySelector('.cs-notice-warn .cs-notice-title')?.textContent).toBe(TITRE_LIMITE);
		expect(container.querySelector('.cs-notice-warn p:not(.cs-notice-title)')?.textContent).toBe(messageLimite(60));
		expect(getByRole('status').textContent).toBe(messageLimite(60));
		expect(container.textContent).not.toContain('phrase du client');
		expect(queryByText(/momentanément indisponible/)).toBeNull();
		expect(getByRole('button', { name: 'Relancer la recherche' })).toBeTruthy();
	});
});

describe('CommuneSearch — postal-code search (CR-01)', () => {
	async function answer(q: string, res: CommunesSearchResponse) {
		mockSearch.mockResolvedValue(res);
		const view = setup();
		await type(view.input, q);
		await vi.advanceTimersByTimeAsync(200);
		await flush();
		return view;
	}

	it('a listed commune wins over an out-of-coverage meta: no typed message', async () => {
		const voisine = commune('35998', 'Bord de Vilaine', { code_postal: '49730' });
		const { listOptions, getByRole, queryByText } = await answer(
			'49730',
			envelope([voisine], OUTSIDE)
		);

		const [option] = listOptions();
		expect(option.textContent).toContain('Ille-et-Vilaine\u00a0(35) · 49730');
		expect(queryByText(TITRE_CODE_POSTAL_HORS_COUVERTURE)).toBeNull();
		expect(getByRole('status').textContent).toContain('1 commune proposée');
	});

	it('out of coverage: the typed state and its reason, never « Aucune commune trouvée »', async () => {
		const { container, getByText, getByRole, queryByText } = await answer('33000', envelope([], OUTSIDE));

		const encart = container.querySelector('.cs-notice');
		expect(encart?.classList.contains('cs-notice-warn')).toBe(true);
		expect(encart?.querySelector('.cs-notice-title')?.textContent).toBe(TITRE_CODE_POSTAL_HORS_COUVERTURE);
		expect(encart?.querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		expect(encart?.textContent).not.toMatch(/indisponible/);
		expect(
			getByText(/Ce site ne couvre pas ce département\. Cherchez la commune par son nom\./)
		).toBeTruthy();
		expect(queryByText(/Aucune commune trouvée/)).toBeNull();
		expect(queryByText(/territoire_hors_perimetre/)).toBeNull();
		expect(getByRole('status').textContent).toBe(ANNONCE_CODE_POSTAL_HORS_COUVERTURE);
		expect(getByRole('option', { name: /Voir tous les résultats pour «\u202f33000\u202f»/ })).toBeTruthy();
	});

	it('hosted without an address: a zero bounded to the served departements', async () => {
		const hosted: DatasetHosting = { etat: 'heberge', motif: null, departements: ['35', '56'] };
		const { container, getByText, queryByText } = await answer('35999', envelope([], hosted));

		expect(getByText('Aucune commune trouvée pour « 35999 »')).toBeTruthy();
		expect(
			getByText(/La recherche par code postal porte sur : Ille-et-Vilaine \(35\), Morbihan \(56\)\./)
		).toBeTruthy();
		expect(queryByText(/Vérifiez l'orthographe/)).toBeNull();
		expect(queryByText(TITRE_CODE_POSTAL_HORS_COUVERTURE)).toBeNull();
		expect(container.querySelector('.cs-notice')?.classList.contains('cs-notice-warn')).toBe(false);
	});
});

describe('CommuneSearch — without JavaScript', () => {
	it('is a GET form to /communes carrying q and departement', () => {
		const { container, input, getByRole } = setup({ initialQuery: 'le pin', initialDepartement: '79' });

		const form = container.querySelector('form');
		expect(form?.getAttribute('action')).toBe('/communes');
		expect(form?.getAttribute('method')).toBe('get');
		expect(input.getAttribute('name')).toBe('q');
		expect((input as HTMLInputElement).value).toBe('le pin');
		expect(input.getAttribute('placeholder')).toBe('Nom de commune ou code postal');
		const select = getByRole('combobox', { name: 'Département' }) as HTMLSelectElement;
		expect(select.getAttribute('name')).toBe('departement');
		expect(select.value).toBe('79');
	});

	it('can name and explain the department control as a referential filter', () => {
		const { getByRole, getByText } = setupLieu({
			departementLabel: 'Filtrer par département',
			departementHint: 'Ce filtre parcourt le référentiel des départements.'
		});
		const select = getByRole('combobox', { name: 'Filtrer par département' });
		const hint = getByText('Ce filtre parcourt le référentiel des départements.');
		expect(select.getAttribute('aria-describedby')).toBe(hint.id);
	});

	it('folds the department filter on demand, the field staying in the GET form', () => {
		const { container, getByText } = setupLieu({ departementReplie: true, departementLabel: 'Filtrer par département' });
		const repli = container.querySelector('details');
		expect(repli?.open).toBe(false);
		expect(getByText('Affiner par département').tagName).toBe('SUMMARY');
		expect(repli?.querySelector('select[name="departement"]')).not.toBeNull();
	});

	it('keeps a chosen department in sight when the filter is folded', () => {
		const { container } = setupLieu({ departementReplie: true, initialDepartement: '35' });
		expect(container.querySelector('details')?.open).toBe(true);
	});

	it('renders no departement control at all with sansDepartement (home page)', () => {
		const { container, queryByRole } = setupLieu({ sansDepartement: true });
		expect(container.querySelector('[name="departement"]')).toBeNull();
		expect(container.querySelector('details')).toBeNull();
		expect(queryByRole('combobox', { name: /département/i })).toBeNull();
		expect(container.querySelector('input[name="q"]')).not.toBeNull();
	});

	it('does not fold the filter by default', () => {
		const { container } = setupLieu();
		expect(container.querySelector('details')).toBeNull();
	});
});

// --- Single field communes + adresses (default `types`, D-105, D-106, D-112) ----------------

function setupLieu(props: Record<string, unknown> = {}) {
	const view = render(CommuneSearch, { props });
	const input = view.getByRole('combobox', { name: 'Commune, code postal ou adresse' });
	const listOptions = () => within(view.getByRole('listbox')).getAllByRole('option');
	return { ...view, input, listOptions };
}

async function chercher(input: HTMLElement, q: string) {
	await type(input, q);
	await vi.advanceTimersByTimeAsync(200);
	await flush();
}

describe('CommuneSearch — single field: an address option opens its page', () => {
	it('searches communes and addresses in parallel, once, with the same filter', async () => {
		mockAdresses.mockResolvedValue(adresses([LILAS]));
		const { input } = setupLieu({ initialDepartement: '85' });

		await chercher(input, '12 rue des Lilas');

		expect(mockSearch).toHaveBeenCalledTimes(1);
		expect(mockAdresses).toHaveBeenCalledTimes(1);
		expect(mockAdresses.mock.calls[0][0]).toBe('12 rue des Lilas');
		expect(mockAdresses.mock.calls[0][1]).toMatchObject({ departement: '85', limite: 8 });
		expect(mockAdresses.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
	});

	it('an address option carries its type first, then « {numéro} {voie} » and « {CP} {Commune} · {Département (code)} »', async () => {
		mockAdresses.mockResolvedValue(adresses([LILAS]));
		const { input, listOptions } = setupLieu();

		await chercher(input, '12 rue des Lilas');

		const [option] = listOptions();
		const badge = option.querySelector('.cs-type');
		expect(badge?.textContent).toBe('Adresse');
		expect(option.firstElementChild).toBe(badge);
		expect(option.querySelector('.cs-option-name')?.textContent).toBe('12 Rue des Lilas');
		expect(option.querySelector('.cs-option-meta')?.textContent).toBe('85000 La Roche-sur-Yon · Vendée (85)');
	});

	// 04.7-09 (review Codex 4): in the mixed field, an arrondissement is typed and announced as such.
	it('an arrondissement option of the single field is typed « Arrondissement » and announced apart', async () => {
		const arr12 = commune('75112', 'Paris 12e Arrondissement', { departement: '75', type_zone: 'arrondissement', commune_parent: '75056' });
		mockSearch.mockResolvedValue(envelope([arr12]));
		mockAdresses.mockResolvedValue(adresses([], ['commune', 'adresse']));
		const { input, listOptions, container } = setupLieu();

		await chercher(input, 'paris 12');

		const [option] = listOptions();
		expect(option.querySelector('.cs-type')?.textContent).toBe('Arrondissement');
		expect(container.textContent).toContain('1 arrondissement proposé');
		expect(container.textContent).not.toContain('commune proposée');
	});

	it('a commune option of the single field is typed « Commune »', async () => {
		mockSearch.mockResolvedValue(envelope([LE_PIN_35]));
		mockAdresses.mockResolvedValue(adresses([], ['commune', 'adresse']));
		const { input, listOptions } = setupLieu();

		await chercher(input, 'le pin');

		const [option] = listOptions();
		expect(option.querySelector('.cs-type')?.textContent).toBe('Commune');
		expect(option.querySelector('.cs-option-name')?.textContent).toBe('Le Pin');
	});

	it('Enter on an address option opens /adresse/{ban_id}, never a coordinate', async () => {
		mockAdresses.mockResolvedValue(adresses([LILAS]));
		const { input } = setupLieu();

		await chercher(input, '12 rue des Lilas');
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });

		expect(mockGoto).toHaveBeenCalledWith('/adresse/85191_0420_00012');
	});

	it('the single field: its label, placeholder and primary action', () => {
		const { input, getByRole } = setupLieu();

		expect(input.getAttribute('placeholder')).toBe('8 rue de Metz 31000');
		expect(getByRole('button', { name: 'Rechercher un lieu' })).toBeTruthy();
		expect(getByRole('search', { name: 'Rechercher un lieu' })).toBeTruthy();
	});

	it('uses the same real example in address-only mode', () => {
		const view = render(CommuneSearch, { props: { types: ['adresse'] } });
		const input = view.getByRole('combobox', { name: 'Adresse' });

		expect(input.getAttribute('placeholder')).toBe('8 rue de Metz 31000');
	});

	it("`types: ['commune']` never calls the address search (comparator of communes)", async () => {
		const { input } = setup();

		await chercher(input, '12 rue des Lilas');

		expect(mockSearch).toHaveBeenCalledTimes(1);
		expect(mockAdresses).not.toHaveBeenCalled();
	});
});

describe('CommuneSearch — single field: served order, 5 / 3 (D-106)', () => {
	const sixAdresses = Array.from({ length: 6 }, (_, i) => adresse(`85191_0420_0000${i + 1}`, String(i + 1), 'Rue des Lilas'));
	const quatreCommunes = Array.from({ length: 4 }, (_, i) => commune(`8519${i}`, `Commune ${i}`));

	it('addresses first when the API serves them first: 5 addresses, then 3 communes', async () => {
		mockSearch.mockResolvedValue(envelope(quatreCommunes));
		mockAdresses.mockResolvedValue(adresses(sixAdresses, ['adresse', 'commune']));
		const { input, listOptions } = setupLieu();

		await chercher(input, '12 rue des Lilas');

		const types = listOptions().map((option) => option.querySelector('.cs-type')?.textContent ?? 'tout');
		expect(types).toEqual([...Array(5).fill('Adresse'), ...Array(3).fill('Commune'), 'tout']);
	});

	it('communes first when the API serves them first: never re-ordered by the client', async () => {
		mockSearch.mockResolvedValue(envelope(quatreCommunes));
		mockAdresses.mockResolvedValue(adresses(sixAdresses, ['commune', 'adresse']));
		const { input, listOptions } = setupLieu();

		await chercher(input, 'Nantes');

		const types = listOptions().map((option) => option.querySelector('.cs-type')?.textContent ?? 'tout');
		expect(types).toEqual([...Array(4).fill('Commune'), ...Array(4).fill('Adresse'), 'tout']);
	});
});

describe('CommuneSearch — served number qualification', () => {
	const proches: CorrespondanceNumeroRecherche = {
		etat: 'proches',
		numero_demande: 12,
		suffixe_demande: null,
		portee: 'resultats_retournes'
	};

	it('keeps the bounded message outside the listbox with one live region after closing', async () => {
		mockAdresses.mockResolvedValue(adresses([adresse('near-11', '11', 'Rue des Lilas')], ['adresse', 'commune'], HEBERGE, proches));
		const { input, container, getByRole, queryByRole } = setupLieu();

		await chercher(input, '12 rue des Lilas');

		const notice = container.querySelector('[data-number-match="proches"]');
		expect(notice?.textContent).toContain(
			"Le numéro 12 n'apparaît pas dans ces résultats. Voici les résultats proches renvoyés."
		);
		expect(notice?.closest('[role="listbox"]')).toBeNull();
		expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
		expect(getByRole('status').textContent).toContain("Le numéro 12 n'apparaît pas dans ces résultats.");

		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(queryByRole('listbox')).toBeNull();
		const closedNotice = container.querySelector('[data-number-match="proches"]');
		expect(container.querySelectorAll('[data-number-match="proches"]')).toHaveLength(1);
		expect(container.querySelector('.cs-popup')).toBeNull();
		expect(closedNotice?.parentElement).toBe(container.querySelector('form'));
		expect(closedNotice?.previousElementSibling).toBe(container.querySelector('.cs-row'));

		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(getByRole('listbox')).toBeTruthy();
		await fireEvent.keyDown(input, { key: 'Tab' });
		expect(queryByRole('listbox')).toBeNull();
		expect(container.querySelectorAll('[data-number-match="proches"]')).toHaveLength(1);
		expect(container.querySelector('.cs-popup')).toBeNull();
	});

	it('clears the previous qualification while the next debounced request is pending', async () => {
		mockAdresses.mockResolvedValueOnce(adresses([adresse('near-11', '11', 'Rue des Lilas')], ['adresse', 'commune'], HEBERGE, proches));
		const { input, container } = setupLieu();
		await chercher(input, '12 rue des Lilas');
		expect(container.querySelector('[data-number-match="proches"]')).not.toBeNull();

		await type(input, '13 rue des Lilas');

		expect(container.querySelector('[data-number-match="proches"]')).toBeNull();
	});

	it.each([null, { ...proches, etat: 'exacte' as const }])(
		'does not invent a proximity message for an old API or an exact answer',
		async (qualification) => {
			mockAdresses.mockResolvedValue(adresses([LILAS], ['adresse', 'commune'], HEBERGE, qualification));
			const { input, container } = setupLieu();
			await chercher(input, '12 rue des Lilas');
			expect(container.querySelector('[data-number-match]')).toBeNull();
		}
	);
});

describe('CommuneSearch — single field: street without number (D-107)', () => {
	const PAIX: ResultatRechercheAdresse = {
		...adresse('', '', 'Rue de la Paix'),
		type_resultat: 'voie',
		ban_id: null,
		numero: null
	};

	it('a street is a « Voie » option whose second line ends with « · Précisez le numéro »', async () => {
		mockAdresses.mockResolvedValue(adresses([PAIX]));
		const { input, listOptions } = setupLieu();

		await chercher(input, 'rue de la Paix');

		const [option] = listOptions();
		expect(option.querySelector('.cs-type')?.textContent).toBe('Voie');
		expect(option.querySelector('.cs-option-name')?.textContent).toBe('Rue de la Paix');
		expect(option.querySelector('.cs-option-meta')?.textContent).toBe(
			'85000 La Roche-sur-Yon · Vendée (85) · Précisez le numéro'
		);
	});

	it('choosing it does not navigate: the field gets « {voie}, {CP} {Commune} », the caret at the start, and the announcement', async () => {
		mockAdresses.mockResolvedValue(adresses([PAIX]));
		const { input, getByRole, queryByRole } = setupLieu();

		await chercher(input, 'rue de la Paix');
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });
		await flush();

		expect(mockGoto).not.toHaveBeenCalled();
		const champ = input as HTMLInputElement;
		expect(champ.value).toBe('Rue de la Paix, 85000 La Roche-sur-Yon');
		expect(champ.selectionStart).toBe(0);
		expect(champ.selectionEnd).toBe(0);
		expect(getByRole('status').textContent).toBe('Ajoutez le numéro de voie au début de la saisie.');
		expect(queryByRole('listbox')).toBeNull();
	});
});

describe('CommuneSearch — single field: addresses out of coverage (D-105)', () => {
	const RENNES = commune('35238', 'Rennes', {
		code_postal: '35000',
		hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] }
	});
	const BAN_RENNES = {
		etat: 'hors_couverture' as const,
		motif: 'territoire_hors_perimetre' as const,
		departements: [],
		territoire: { code_insee: '35238', nom: 'Rennes', departement: { code: '35', nom: 'Ille-et-Vilaine' } }
	};

	it('a titled warn notice under the field, outside the listbox; the commune option stays with its badge', async () => {
		mockSearch.mockResolvedValue(envelope([RENNES]));
		mockAdresses.mockResolvedValue(adresses([], ['adresse', 'commune'], BAN_RENNES));
		const { input, container, getByRole, listOptions, queryByRole, queryByText } = setupLieu();

		await chercher(input, '1 rue de la Gare Rennes');

		const encart = container.querySelector('.cs-notice-adresses');
		expect(encart?.classList.contains('cs-notice-warn')).toBe(true);
		expect(encart?.closest('[role="listbox"]')).toBeNull();
		expect(encart?.querySelector('.cs-notice-title')?.textContent).toBe(
			'Rennes (35) : adresses hors couverture'
		);
		expect(encart?.textContent).toContain(
			'Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.'
		);
		expect(encart?.querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		const [option] = listOptions();
		expect(option.querySelector('.cs-type')?.textContent).toBe('Commune');
		expect(option.textContent).toContain('Rennes');
		expect(option.querySelector('.cs-badge')?.textContent).toBe('Hors couverture · département non hébergé');
		expect(queryByText(/Aucune commune ni adresse/)).toBeNull();
		expect(getByRole('status').textContent).toBe('1 commune proposée');

		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(queryByRole('listbox')).toBeNull();
		expect(container.querySelectorAll('.cs-notice-adresses')).toHaveLength(1);
	});

	it('out of coverage is never announced as a zero when nothing is listed', async () => {
		mockAdresses.mockResolvedValue(
			adresses([], ['adresse', 'commune'], BAN_RENNES, {
				etat: 'proches',
				numero_demande: 1,
				suffixe_demande: null,
				portee: 'resultats_retournes'
			})
		);
		const { input, getByRole, queryByText, container } = setupLieu();

		await chercher(input, '1 rue de la Gare Rennes');

		expect(queryByText(/Aucune commune ni adresse/)).toBeNull();
		expect(getByRole('status').textContent).toBe('Rennes (35) : adresses hors couverture');
		expect(container.querySelector('[data-number-match]')).toBeNull();
	});
});

// C22 (Ianis, 2026-10-08): a postal code out of coverage keeps the phase 3 title, even when the
// address search answers out of coverage too (real data, 04-20: « Gironde (33) : adresses… »).
describe('CommuneSearch — single field: a postal code out of coverage (C22)', () => {
	it('out of coverage for both searches: one notice, the phase 3 title and announcement', async () => {
		const banGironde = {
			etat: 'hors_couverture' as const,
			motif: 'territoire_hors_perimetre' as const,
			departements: [],
			territoire: { code_insee: null, nom: null, departement: { code: '33', nom: 'Gironde' } }
		};
		mockSearch.mockResolvedValue(envelope([], OUTSIDE));
		mockAdresses.mockResolvedValue(adresses([], ['commune', 'adresse'], banGironde));
		const { input, container, getByRole, queryByText } = setupLieu();

		await chercher(input, '33000');

		const encarts = container.querySelectorAll('.cs-notice');
		expect(encarts).toHaveLength(1);
		expect(encarts[0].classList.contains('cs-notice-warn')).toBe(true);
		expect(encarts[0].querySelector('.cs-notice-title')?.textContent).toBe(TITRE_CODE_POSTAL_HORS_COUVERTURE);
		expect(encarts[0].textContent).toContain(
			'Ce site ne couvre pas ce département. Cherchez la commune par son nom.'
		);
		expect(encarts[0].querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		expect(container.querySelector('.cs-notice-adresses')).toBeNull();
		expect(encarts[0].textContent).not.toContain('Gironde');
		expect(queryByText(/Aucune commune/)).toBeNull();
		expect(getByRole('status').textContent).toBe(ANNONCE_CODE_POSTAL_HORS_COUVERTURE);
	});
});

describe('CommuneSearch — single field: partial outage (SEM-01)', () => {
	it('addresses in 503, communes served: the communes stay, a head line says it and « Relancer la recherche » reruns', async () => {
		mockSearch.mockResolvedValue(envelope([LE_PIN_35]));
		mockAdresses
			.mockRejectedValueOnce(new ApiError(503, 'x', false, 'Recherche d’adresses momentanement indisponible'))
			.mockResolvedValueOnce(adresses([LILAS], ['commune', 'adresse']));
		const { input, listOptions, getByRole, queryByText } = setupLieu();

		await chercher(input, 'le pin');

		const options = listOptions();
		expect(options[0].textContent).toContain("La recherche d'adresses est momentanément indisponible.");
		expect(options[0].textContent).toContain('Relancer la recherche');
		expect(options[1].textContent).toContain('Le Pin');
		expect(queryByText(/Aucune commune/)).toBeNull();
		expect(getByRole('status').textContent).toBe('1 commune proposée');

		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });
		await flush();

		expect(mockAdresses).toHaveBeenCalledTimes(2);
		expect(mockSearch).toHaveBeenCalledTimes(2);
		expect(listOptions().map((option) => option.querySelector('.cs-type')?.textContent ?? 'tout')).toEqual([
			'Commune',
			'Adresse',
			'tout'
		]);
	});

	it('communes in 503, addresses served: the addresses stay, the head line names the communes', async () => {
		mockSearch.mockRejectedValue(new ApiError(503, 'x'));
		mockAdresses.mockResolvedValue(adresses([LILAS]));
		const { input, listOptions } = setupLieu();

		await chercher(input, '12 rue des Lilas');

		const options = listOptions();
		expect(options[0].textContent).toContain('La recherche de communes est momentanément indisponible.');
		expect(options[1].querySelector('.cs-type')?.textContent).toBe('Adresse');
	});

	it('a 429 on the addresses stays typed by panne.ts, never « momentanément indisponible »', async () => {
		mockSearch.mockResolvedValue(envelope([LE_PIN_35]));
		mockAdresses.mockRejectedValue(new ApiError(429, 'x', true, 'Trop de requetes', { retryAfter: 30, anonyme: true }));
		const { input, listOptions } = setupLieu();

		await chercher(input, 'le pin');

		const [ligne] = listOptions();
		expect(ligne.textContent).toContain(messageLimite(30));
		expect(ligne.textContent).not.toContain('momentanément indisponible');
	});

	it('both in outage: the outage notice of the field, never an empty result', async () => {
		mockSearch.mockRejectedValue(new ApiError(503, 'x'));
		mockAdresses.mockRejectedValue(new ApiError(503, 'x'));
		const { input, getByText, queryByText, queryByRole, container } = setupLieu();

		await chercher(input, 'le pin');

		expect(getByText('La recherche est momentanément indisponible. Réessayez dans un instant.')).toBeTruthy();
		expect(queryByText(/Aucune commune/)).toBeNull();
		expect(queryByRole('listbox')).toBeNull();
		expect(container.querySelector('[data-number-match]')).toBeNull();
	});
});

describe('CommuneSearch — single field: announcements and « Aucun résultat »', () => {
	it('« 1 commune et 3 adresses proposées »', async () => {
		mockSearch.mockResolvedValue(envelope([LE_PIN_35]));
		mockAdresses.mockResolvedValue(
			adresses([LILAS, adresse('85191_0420_00014', '14', 'Rue des Lilas'), adresse('85191_0420_00016', '16', 'Rue des Lilas')])
		);
		const { input, getByRole } = setupLieu();

		await chercher(input, '1 rue des Lilas');

		expect(getByRole('status').textContent).toBe('1 commune et 3 adresses proposées');
	});

	it('nothing in either type: the contract title and body, announced', async () => {
		const { input, getByRole, getByText } = setupLieu();

		await chercher(input, 'zzz');

		expect(getByText('Aucune commune ni adresse trouvée pour « zzz »')).toBeTruthy();
		expect(
			getByText(
				"Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, ajoutez la commune ou le code postal, ou retirez le filtre département."
			)
		).toBeTruthy();
		expect(getByRole('status').textContent).toBe('Aucune commune ni adresse proposée pour « zzz »');
	});
});

// --- Variant of the field (04.8-02, E6, UI-SPEC v4 § 3.13): the large field of the public pages ------
// `variante="grand"` is the large field: the visible label « Adresse, commune ou code postal », an
// 18 px field and its plain button attached to it. The default stays the compact form; the combobox
// and the GET fallback are the same in both.

const sourceDuChamp = async () => (await import('./CommuneSearch.svelte?raw')).default as string;

describe('CommuneSearch — variante « grand » (E6)', () => {
	it('the visible label « Adresse, commune ou code postal » names the combobox', () => {
		const view = render(CommuneSearch, { props: { variante: 'grand' } });
		const input = view.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
		const label = view.container.querySelector(`label[for="${input.id}"]`)!;
		expect(label.textContent).toBe('Adresse, commune ou code postal');
		expect(label.classList.contains('cs-sr-only')).toBe(false);
		expect(view.container.querySelector('form')?.classList.contains('cs-grand')).toBe(true);
	});

	it('the plain button « Rechercher » is attached to the field, right after it', () => {
		const view = render(CommuneSearch, { props: { variante: 'grand' } });
		const input = view.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
		const bouton = view.getByRole('button', { name: 'Rechercher' });
		expect(bouton.getAttribute('type')).toBe('submit');
		const saisie = input.closest('.cs-saisie')!;
		expect(saisie).not.toBeNull();
		expect(saisie.lastElementChild).toBe(bouton);
		expect(saisie.firstElementChild?.contains(input)).toBe(true);
		// One submit button only.
		expect(view.container.querySelectorAll('button[type="submit"]')).toHaveLength(1);
	});

	it('an 18 px field, at most 620 px with its button (UI-SPEC § 3.13)', async () => {
		const source = await sourceDuChamp();
		expect(source).toMatch(/\.cs-grand \.cs-input \{[^}]*font-size: 18px;/);
		expect(source).toMatch(/\.cs-grand \.cs-saisie \{[^}]*display: flex;/);
		expect(source).toMatch(/\.cs-grand \.cs-field:not\(\.cs-field-select\) \{[^}]*max-width: 620px;/);
	});

	it('same ARIA 1.2 combobox and same GET fallback as the compact field', async () => {
		mockAdresses.mockResolvedValue(adresses([LILAS]));
		const view = render(CommuneSearch, { props: { variante: 'grand', initialQuery: 'le pin', initialDepartement: '85' } });
		const input = view.getByRole('combobox', { name: 'Adresse, commune ou code postal' });
		const form = view.container.querySelector('form')!;
		expect(form.getAttribute('action')).toBe('/communes');
		expect(form.getAttribute('method')).toBe('get');
		expect(form.getAttribute('role')).toBe('search');
		expect(input.getAttribute('name')).toBe('q');
		expect((input as HTMLInputElement).value).toBe('le pin');
		expect(input.getAttribute('aria-autocomplete')).toBe('list');
		expect(input.getAttribute('aria-expanded')).toBe('false');
		const select = view.getByRole('combobox', { name: 'Département' }) as HTMLSelectElement;
		expect(select.getAttribute('name')).toBe('departement');
		expect(select.value).toBe('85');

		await chercher(input, '12 rue des Lilas');
		expect(input.getAttribute('aria-expanded')).toBe('true');
		expect(input.getAttribute('aria-controls')).toBe(view.getByRole('listbox').id);
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		const [option] = within(view.getByRole('listbox')).getAllByRole('option');
		expect(input.getAttribute('aria-activedescendant')).toBe(option.id);
		await fireEvent.keyDown(input, { key: 'Enter' });
		expect(mockGoto).toHaveBeenCalledWith('/adresse/85191_0420_00012');
	});

	it('the default is the compact field, unchanged', () => {
		const defaut = render(CommuneSearch);
		expect(defaut.container.querySelector('form')?.classList.contains('cs-grand')).toBe(false);
		expect(defaut.container.querySelector('.cs-saisie')).toBeNull();
		expect(defaut.getByRole('combobox', { name: 'Commune, code postal ou adresse' })).toBeTruthy();
		expect(defaut.getByRole('button', { name: 'Rechercher un lieu' })).toBeTruthy();
		// Compact order kept: field, departement, then the button, last child of the row.
		const row = defaut.container.querySelector('.cs-row')!;
		expect(row.lastElementChild?.matches('button.cs-submit')).toBe(true);
		const compacte = render(CommuneSearch, { props: { variante: 'compacte' } });
		expect(compacte.container.querySelector('form')?.classList.contains('cs-grand')).toBe(false);
	});

	it('a communes-only large field keeps its own label: it never promises an address search', () => {
		const view = render(CommuneSearch, { props: { variante: 'grand', types: ['commune'] } });
		expect(view.getByRole('combobox', { name: 'Rechercher une commune' })).toBeTruthy();
	});
});
