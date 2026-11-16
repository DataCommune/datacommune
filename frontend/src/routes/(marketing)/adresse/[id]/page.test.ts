// `/adresse/[id]` (UI-SPEC « /adresse/[id] » point 1, D-112, LIEU-01): the page carries the
// address as served and nothing it does not prove. The server loader projects a public model, the
// only thing serialized in the HTML (no coordinate, no header of the API), and marks the page
// `noindex, nofollow`.
import { fireEvent, render, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import type { Writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidate: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
// The weekly fiche allowance (H-3) has its own tests (`$lib/server/fiche-quota.test.ts`): here every
// fiche is admitted unless a test says otherwise.
vi.mock('$lib/server/fiche-quota', () => ({ admettreFiche: vi.fn(async () => ({ admise: true })) }));
vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		page: writable({ status: 200, error: null, url: new URL('https://site.invalid/') }),
		// 04-18: the page shows skeletons while a client navigation opens another address.
		navigating: writable(null)
	};
});

import { invalidate, invalidateAll } from '$app/navigation';
import { goto } from '$app/navigation';
import { addressComparisonSelection } from '$lib/stores/comparison-selection.svelte';
import { navigating, page } from '$app/stores';
import type { FicheAdressePublique, IssueRapportAdresse } from '$lib/api/adresses';
import { projeterTheme, type RapportPublic, type ResumeServi, type ThemeRapport } from '$lib/api/rapport';
import { aucunZeroFabrique, verdictCoherent } from '$lib/data/adresse-rapport';
import dpeReel from '$lib/data/__fixtures__/rapport-theme-dpe-plusieurs-batiments.json';
import {
	MESSAGE_ACCES_REFUSE,
	TITRE_ACCES_REFUSE,
	TITRE_DEMANDE_REFUSEE,
	TITRE_LIMITE,
	messageDemandeRefusee,
	messageLimite
} from '$lib/data/panne';
// FRONT WR-03 of the D-15 review: the identity is read by the layout, the list by the page.
import { load as loadIdentite } from './+layout.server';
import { admettreFiche } from '$lib/server/fiche-quota';
import { load } from './+page.server';
import Page from './+page.svelte';
import ErrorPage from '../+error.svelte';
import { sansDeveloppement } from '$lib/utils/sigles';

const pageStore = page as unknown as Writable<{ status: number; error: { message: string } | null; url: URL }>;
const navigatingStore = navigating as unknown as Writable<unknown>;

const FICHE: FicheAdressePublique = {
	identite: {
		etat: 'etablie',
		ban_id: '85191_0420_00012',
		libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
		numero: '12',
		rep: null,
		nom_voie: 'Rue des Lilas',
		code_postal: '85000',
		commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
		departement: { code: '85', nom: 'Vendée' },
		parcelle: { etat: 'unique', id_parcelle: '85191000AB0012' }
	},
	couverture: { etat: 'heberge', motif: null }
};

const SOURCE = { nom: 'Base Adresse Nationale', url: 'https://adresse.data.gouv.fr' };

// 04-18: an address without declared document, as served by `/v1/adresse/{id}/dpe` (04-03).
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };
const DOCUMENTS_VIDES = {
	etat: 'value',
	motif: null,
	source: SOURCE_DPE,
	total: 0,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: '0123456789abcdef',
	distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
	date_import: '2026-09-30',
	documents: [],
	selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] },
	signal_plusieurs_logements: null,
	// 04-19: served by the list, null when no D-110 finding is established.
	constat_d110: null,
	rattachement: 'adresse_saisie_diagnostiqueur'
};
const { rattachement: _rattachement, ...DOCUMENTS_VIDES_PROJETES } = DOCUMENTS_VIDES;

// 04-05: a parcel without sale, as served by `/v1/adresse/{id}/ventes` (a measured zero).
const SOURCE_DVF = { cle: 'dvf', nom: 'Demandes de valeurs foncières (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees' };
const VENTES_VIDES = {
	etat: 'value',
	motif: null,
	nombre_parcelles: null,
	rattachement: 'parcelle_du_point',
	source: SOURCE_DVF,
	periode: '2021-2025',
	total: 0,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: 'fedcba9876543210',
	ventes: []
};
const { rattachement: _rattachementVentes, ...VENTES_VIDES_PROJETEES } = VENTES_VIDES;

// 04.7-03: a report that serves no theme; the lists of the page are rendered on their own.
const SECTIONS_VIDES: ResumeServi['parSection'] = {
	batiment: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
	risques: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
	marche: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
	vivre: { vigilance: 0, pannes: 0, lus: 0, total: 0 }
};
/** A served summary (04.7-17): empty lists and counts, then what the case serves. */
const resumeServi = (r: Partial<ResumeServi> = {}): ResumeServi => ({
	pointsAdresse: [],
	pointsSecteur: [],
	favorablesAdresse: [],
	favorablesSecteur: [],
	pannes: [],
	sourcesEnPanne: [],
	partiels: [],
	sansObjet: [],
	sansValeur: [],
	lus: 0,
	lusAdresse: 0,
	lusSecteur: 0,
	total: 0,
	parSection: SECTIONS_VIDES,
	...r
});
const RAPPORT_VIDE: RapportPublic = { date: '2026-10-30', themes: [], chiffresCles: [], rattachementPhrase: null, resume: resumeServi(), carte: null };

// The sales criterion of 04.7-01 (`ventes_parcelle`): its detail holds the two lists of sales.
const THEME_VENTES: ThemeRapport = {
	id: 'ventes_parcelle',
	section: 'marche',
	titre: 'Ventes de la parcelle',
	etat: 'ok',
	ton: 'neutre',
	granularite: { niveau: 'parcelle', rayon_m: null },
	verdict: '2 ventes enregistrées sur la parcelle de l’adresse, la dernière en mars 2024.',
	chiffres: [{ id: 'nombre', libelle: 'Ventes sur la parcelle', valeur: 2, unite: 'count', millesime: '2021-2025' }],
	details: { periode: '2021-2025', nombre: 2 },
	rattachement: { entite: 'parcelle_du_point', cle: '85191000AB0012', confiance: 'sure', candidats: null, motif: null, alerte: null, maillons: [{ lien: 'parcelle_du_point', confiance: 'sure' }] },
	sources: [{ cle: 'dvf', nom: 'Demandes de valeurs foncières', producteur: 'DGFiP', url: 'https://www.data.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: '2025-06', mode: 'import' }],
	motif: null,
	etat_libelle: null,
	accroche: '2 ventes sur la parcelle',
	pannes_partielles: []
};

function servie(fiche: FicheAdressePublique = FICHE, rapport: RapportPublic = RAPPORT_VIDE): { issue: IssueRapportAdresse; documents: null } {
	return { issue: { kind: 'ok', fiche, rapport, source: SOURCE }, documents: null };
}

/** Opens the panel of the similar sales from its first link (closed until asked, UI-SPEC v3 § 4.8). */
async function ouvrirVentesRessemblantes(rendu: { getAllByRole: (role: string, options: { name: string }) => HTMLElement[] }) {
	await fireEvent.click(rendu.getAllByRole('link', { name: 'Ventes ressemblantes' })[0]);
}

function avecParcelle(parcelle: FicheAdressePublique['identite']['parcelle']): FicheAdressePublique {
	return { ...FICHE, identite: { ...FICHE.identite, parcelle } };
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('/adresse/[id] — undetermined identity (04-DECISIONS-TEXTES, 2026-10-05, row 7)', () => {
	// 04.8-03 (UI-SPEC v4 § 4.2): the h1 is composed of the served number and street; an identity with
	// no such field (the position or the rows disagree) falls back on the served label, then on the title « Adresse à … ».
	const indeterminee = (motif: string): FicheAdressePublique => ({
		...FICHE,
		identite: { ...FICHE.identite, etat: 'indeterminee', libelle: null, numero: null, nom_voie: null, parcelle: { etat: 'indeterminee', id_parcelle: null, motif } }
	});

	it('contradictory positions: title « Adresse à {Commune} ({dép.}) » kept, the decided sentence under it', () => {
		const { getByRole, container } = render(Page, { props: { data: servie(indeterminee('positions_contradictoires')) } });

		expect(getByRole('heading', { level: 1 }).textContent).toMatch(/^Adresse à La Roche-sur-Yon\s\(85\)$/);
		expect(container.querySelector('.adresse-indeterminee')?.textContent).toBe(
			"Les sources ne s'accordent pas sur la position de cette adresse\u202f; les repères qui en dépendent ne sont pas calculés."
		);
	});

	it('a missing position: the decided sentence of row 22, never the one of a disagreement', () => {
		const { container } = render(Page, { props: { data: servie(indeterminee('position_absente')) } });

		expect(container.querySelector('.adresse-indeterminee')?.textContent).toBe(
			"La position de cette adresse n'est pas publiée par la BAN\u202f; les repères qui en dépendent ne sont pas calculés."
		);
		expect(container.textContent).not.toContain("ne s'accordent pas");
	});

	it('BAN rows disagreeing around one position: the decided sentence of row 23', () => {
		const fiche: FicheAdressePublique = { ...FICHE, identite: { ...FICHE.identite, etat: 'indeterminee', libelle: null } };
		const { container } = render(Page, { props: { data: servie(fiche) } });

		expect(container.querySelector('.adresse-indeterminee')?.textContent).toBe(
			"Les lignes de la BAN ne s'accordent pas sur cette adresse\u202f; les repères qui en dépendent ne sont pas calculés."
		);
	});

	it('an established identity: no sentence', () => {
		const { container } = render(Page, { props: { data: servie() } });

		expect(container.querySelector('.adresse-indeterminee')).toBeNull();
	});
});

describe('/adresse/[id] — identity of a served address', () => {
	it('breadcrumb « Recherche / {Commune} / {numéro} {voie} », h1 = the served address', () => {
		const { getByRole, container } = render(Page, { props: { data: servie() } });

		const ariane = getByRole('navigation', { name: "Fil d'Ariane" });
		const liens = within(ariane).getAllByRole('link');
		expect(liens.map((lien) => [lien.textContent, lien.getAttribute('href')])).toEqual([
			['Recherche', '/'],
			['La Roche-sur-Yon', '/commune/85191']
		]);
		// D-33, UI-SPEC v4 § 4.2 and § 7.5: composed of the served number and street, the street readable;
		// neither the postal code nor the commune are in it (they are in the subtitle).
		expect(ariane.querySelector('[aria-current="page"]')?.textContent).toBe('12 rue des Lilas');

		const h1 = getByRole('heading', { level: 1 });
		expect(h1.textContent).toBe('12 rue des Lilas');
		expect(container.querySelectorAll('h1')).toHaveLength(1);
	});

	it('h1 composed of the served number, suffix and readable street; the label only when a field is missing', () => {
		const avecVoie = (champs: Partial<FicheAdressePublique['identite']>): FicheAdressePublique => ({ ...FICHE, identite: { ...FICHE.identite, ...champs } });

		const bis = render(Page, { props: { data: servie(avecVoie({ numero: '4', rep: 'bis', nom_voie: 'Chemin Puits Fictif (du)' })) } });
		expect(bis.getByRole('heading', { level: 1 }).textContent).toBe('4 bis chemin du Puits Fictif');
		expect(within(bis.getByRole('navigation', { name: "Fil d'Ariane" })).getByText('4 bis chemin du Puits Fictif').getAttribute('aria-current')).toBe('page');
		bis.unmount();

		const sansNumero = render(Page, { props: { data: servie(avecVoie({ numero: null })) } });
		expect(sansNumero.getByRole('heading', { level: 1 }).textContent).toBe('12 Rue des Lilas, 85000 La Roche-sur-Yon');
	});

	it('the subtitle reads « {code postal} {commune} · {département} · lu le … » beside the day of reading', () => {
		const { container } = render(Page, { props: { data: servie() } });

		const ligne = container.querySelector('.adresse-identity')?.textContent?.replace(/\s+/g, ' ') ?? '';
		expect(ligne).toContain('85000 La Roche-sur-Yon · Vendée (85)');
		expect(ligne).toContain('· lu le 30 octobre 2026');
	});

	it('identity line: commune link, departement (code), then the `i` button; the parcel moves to its panel, one click away', async () => {
		HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
			this.setAttribute('open', '');
		};
		const { container, getByRole } = render(Page, { props: { data: servie() } });

		const ligne = container.querySelector('.adresse-identity');
		expect(ligne?.textContent).toContain('La Roche-sur-Yon · Vendée\u00a0(85)');
		expect(ligne?.textContent).not.toContain('85191000AB0012');
		expect(ligne?.querySelector('a')?.getAttribute('href')).toBe('/commune/85191');
		await fireEvent.click(getByRole('button', { name: 'Source et définition : adresse' }));
		expect(container.querySelector('dialog.source-panel')?.textContent).toContain('85191000AB0012');
	});

	it('the address source panel names this site in plain language', async () => {
		const { getByRole } = render(Page, { props: { data: servie() } });

		await fireEvent.click(getByRole('button', { name: 'Source et définition : adresse' }));

		expect(getByRole('dialog', { name: 'Adresse' }).textContent).toContain(
			'Adresse de la Base Adresse Nationale (BAN) importée par ce site, désignée par sa clé BAN.'
		);
	});

	it.each([
		['point_hors_parcelle', { etat: 'point_hors_parcelle', id_parcelle: null }],
		['ambigue', { etat: 'ambigue', id_parcelle: null }],
		['hors_couverture', { etat: 'hors_couverture', id_parcelle: null }],
		['indisponible', { etat: 'indisponible', id_parcelle: null }],
		['indeterminee', { etat: 'indeterminee', id_parcelle: null }],
		['absente', null]
	] as const)('no « Parcelle » in the identity line when the relation is %s (2026-10-02)', (_etat, parcelle) => {
		const { container } = render(Page, { props: { data: servie(avecParcelle(parcelle)) } });

		const ligne = container.querySelector('.adresse-identity')?.textContent ?? '';
		expect(ligne).not.toContain('Parcelle');
		expect(ligne).toContain('La Roche-sur-Yon · Vendée\u00a0(85)');
	});

	it('never names the page « bien » (D-101)', () => {
		const { container } = render(Page, { props: { data: servie() } });

		expect(container.textContent).not.toMatch(/\bbien\b/i);
	});
});

describe('/adresse/[id] — server loader', () => {
	function evenement(id: string, cookie: string | null = null) {
		const setHeaders = vi.fn();
		const headers = new Headers();
		if (cookie) headers.set('cookie', cookie);
		return {
			event: { params: { id }, request: new Request('https://site.invalid/adresse/' + id, { headers }), platform: undefined, setHeaders },
			setHeaders
		};
	}

	async function charger(event: ReturnType<typeof evenement>['event']) {
		return (loadIdentite as unknown as (e: typeof event) => Promise<{ issue: IssueRapportAdresse }>)(event);
	}

	async function chargerListe(event: ReturnType<typeof evenement>['event']) {
		const url = new URL(event.request.url);
		const depends = vi.fn();
		return (load as unknown as (e: unknown) => Promise<unknown>)({ ...event, url, depends });
	}

	it('marks the page noindex, nofollow and returns the projected model only', async () => {
		const fetchStub = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(
				JSON.stringify(
					String(input).includes('/dpe?')
						? { statut: 'ok', donnees: { ...DOCUMENTS_VIDES, interne: 'en-tete-interne' } }
						: String(input).includes('/ventes-declarees?')
							? { statut: 'ok', donnees: { ...VENTES_ADRESSE_VIDES, interne: 'en-tete-interne' } }
						: String(input).includes('/ventes?')
							? { statut: 'ok', donnees: { ...VENTES_VIDES, interne: 'en-tete-interne' } }
								: {
								statut: 'ok',
								donnees: {
									adresse: { ...FICHE.identite, lon: -1.426123, lat: 46.670345 },
									couverture: { etat: 'heberge', motif: null }
								},
								meta: { source: [{ ...SOURCE, licence: 'Licence Ouverte' }] }
							}
				),
				{ status: 200, headers: { 'Content-Type': 'application/json', 'X-Request-Id': 'en-tete-interne' } }
			)
		);
		vi.stubGlobal('fetch', fetchStub);
		const { event, setHeaders } = evenement('85191_0420_00012');

		const [data, liste] = await Promise.all([charger(event), chargerListe(event)]);

		// Never indexed, never stored by a cache (IN-10 of the D-15 review).
		expect(setHeaders).toHaveBeenCalledWith({ 'x-robots-tag': 'noindex, nofollow', 'cache-control': 'private, no-store' });
		// 04-18: the identity and the first page of the declared documents, read in parallel, each
		// by its own load (FRONT WR-03).
		expect(fetchStub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid').pathname).sort()).toEqual([
			'/v1/adresse/85191_0420_00012/dpe',
			// 04.7-03: the identity and the report, one read.
			'/v1/adresse/85191_0420_00012/rapport',
			// 04-05: the first page of the sales of the parcel, read with the documents.
			'/v1/adresse/85191_0420_00012/ventes',
			// C37: the first page of the sales declared at the address, read with the lists.
			'/v1/adresse/85191_0420_00012/ventes-declarees'
		]);
		const fiche = {
			...FICHE,
			identite: { ...FICHE.identite, parcelle: { etat: 'unique', id_parcelle: '85191000AB0012', nombre_candidats: null } },
			reperes: null,
			// 04-06: no theme served, none projected.
			themes: []
		};
		expect(data).toEqual({ issue: { kind: 'ok', fiche, rapport: { date: null, themes: [], chiffresCles: [], rattachementPhrase: null, resume: null, carte: null }, source: SOURCE } });
		expect(liste).toEqual({
			// 04.5-05: an answer without the raw-address group projects no group (`null`), never an empty one.
			documents: { kind: 'ok', documents: { ...DOCUMENTS_VIDES_PROJETES, adresseBrute: null } },
			ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES },
			ventesAdresse: { kind: 'ok', ventes: VENTES_ADRESSE_VIDES_PROJETEES },
			// 04-05 Task 2: no type in the URL, the similar sales are not read (Q6).
			comparables: { kind: 'sans_type' }
		});
		const serialise = JSON.stringify([data, liste]);
		expect(serialise).not.toMatch(/-?\d{1,3}\.\d{3,}/);
		expect(serialise).not.toContain('en-tete-interne');
	});

	it('the identity load never reads the URL: a page change of the list does not rerun it (FRONT WR-03)', async () => {
		const fetchStub = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify({ statut: 'ok', donnees: { adresse: FICHE.identite, couverture: FICHE.couverture }, meta: { source: [SOURCE] } }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
		);
		vi.stubGlobal('fetch', fetchStub);
		const { event } = evenement('85191_0420_00012');
		const lu = vi.fn();
		const espion = Object.defineProperty({ ...event }, 'url', { get: () => (lu(), new URL(event.request.url)) });

		await (loadIdentite as unknown as (e: unknown) => Promise<unknown>)(espion);

		expect(lu).not.toHaveBeenCalled();
		expect(fetchStub).toHaveBeenCalledTimes(1);
		// Bounded in time (FRONT WR-03): every server call carries a deadline signal.
		expect((fetchStub.mock.calls[0][1] as RequestInit).signal).toBeInstanceOf(AbortSignal);
	});

	it('the list load reads the lists only, never the identity (FRONT WR-03)', async () => {
		const fetchStub = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify({ statut: 'ok', donnees: String(input).includes('/ventes?') ? VENTES_VIDES : DOCUMENTS_VIDES }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
		);
		vi.stubGlobal('fetch', fetchStub);
		const { event } = evenement('85191_0420_00012');
		const request = new Request('https://site.invalid/adresse/85191_0420_00012?page=2&g=0123456789abcdef');

		await chargerListe({ ...event, request });

		expect(fetchStub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid').pathname)).toEqual([
			'/v1/adresse/85191_0420_00012/dpe',
			'/v1/adresse/85191_0420_00012/ventes',
			'/v1/adresse/85191_0420_00012/ventes-declarees'
		]);
	});

	it('relays the access cookie alone, through the extracted route fetch policy', async () => {
		const fetchStub = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
			new Response(JSON.stringify({ statut: 'ok', donnees: { adresse: FICHE.identite, couverture: FICHE.couverture }, meta: { source: [SOURCE] } }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' }
			})
		);
		vi.stubGlobal('fetch', fetchStub);
		const { event } = evenement('85191_0420_00012', 'theme=sombre; access_token=jeton; autre=x');

		await charger(event);

		const init = fetchStub.mock.calls[0][1] as RequestInit;
		expect(new Headers(init.headers).get('Cookie')).toBe('access_token=jeton');
	});
});

// --- Task 3 — the states of the page (SEM-01, D-102, D-105, D-107) -------------------------

describe('/adresse/[id] — out of coverage (D-105, N-07)', () => {
	const RENNES: FicheAdressePublique = {
		identite: {
			etat: 'etablie',
			ban_id: '35238_1234_00001',
			libelle: null,
			numero: null,
			rep: null,
			nom_voie: null,
			code_postal: null,
			commune: { code_insee: '35238', nom: 'Rennes' },
			departement: { code: '35', nom: 'Ille-et-Vilaine' },
			parcelle: null
		},
		couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' }
	};

	it('h1 « Adresse à {Commune} ({dép.}) », warn notice of the contract, its three links, no marker', () => {
		const { getByRole, container } = render(Page, { props: { data: { issue: { kind: 'hors_couverture', fiche: RENNES, rapport: RAPPORT_VIDE }, documents: null } } });

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Adresse à Rennes\u00a0(35)');
		const encart = container.querySelector('.notice.notice-warn');
		expect(encart?.querySelector('h2')?.textContent).toBe('Adresse hors couverture');
		expect(encart?.textContent).toContain(
			'Ce site ne couvre pas ce département. Une absence de couverture ne vaut pas zéro.'
		);
		const liens = [...(encart?.querySelectorAll('a') ?? [])].map((a) => [a.textContent, a.getAttribute('href')]);
		expect(liens).toEqual([
			['Voir la couverture des données', '/datasets'],
			['Limites connues des données', '/couverture'],
			['Voir la fiche de Rennes', '/commune/35238']
		]);
		expect(container.querySelector('[data-kpi]')).toBeNull();
		expect(container.textContent).not.toMatch(/\b0\b/);
		expect(container.textContent).not.toContain('Parcelle');
	});
});

describe('/adresse/[id] — a refused request is typed, never an outage (SEM-01)', () => {
	it.each([
		[409, TITRE_DEMANDE_REFUSEE, messageDemandeRefusee(409)],
		[429, TITRE_LIMITE, messageLimite(null)],
		[403, TITRE_ACCES_REFUSE, MESSAGE_ACCES_REFUSE]
	])('%i: titled by panne.ts, « Recharger la fiche », no value', (statut, titre, message) => {
		const panne = { statut, titre, message };
		const { getByRole, container } = render(Page, { props: { data: { issue: { kind: 'panne', panne }, documents: null } } });

		expect(getByRole('heading', { level: 1 }).textContent).toBe(titre);
		expect(getByRole('alert').textContent).toContain(panne.message);
		expect(getByRole('button', { name: 'Recharger la fiche' })).toBeTruthy();
		expect(container.textContent).not.toContain('momentanément indisponible');
	});
});

describe('/adresse/[id] — server loader: one page status per served outcome', () => {
	function evenementId(id: string) {
		return {
			params: { id },
			request: new Request('https://site.invalid/adresse/' + id),
			platform: undefined,
			setHeaders: vi.fn()
		};
	}

	function repondre(status: number, body: unknown) {
		vi.stubGlobal(
			'fetch',
			vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
				new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
			)
		);
	}

	async function chargerId(id: string) {
		const event = evenementId(id);
		return (loadIdentite as unknown as (e: typeof event) => Promise<{ issue: IssueRapportAdresse }>)(event);
	}

	it.each(['xyz', '85191_0420_00012_' + 'x'.repeat(11), '85191_0420_00012 bis', 'A5191_0420_00012', '85191_0420_00012/../a'])(
		'a key of a form the API refuses (%s) → page 404 « Adresse inconnue », the API is not called (IN-12)',
		async (id) => {
			const fetchStub = vi.fn();
			vi.stubGlobal('fetch', fetchStub);
			await expect(chargerId(id)).rejects.toMatchObject({ status: 404, body: { message: 'Adresse inconnue' } });
			expect(fetchStub).not.toHaveBeenCalled();
		}
	);

	it('404 `adresse_inconnue` → page 404 « Adresse inconnue »', async () => {
		repondre(404, { title: 'Adresse inconnue', status: 404, code: 'adresse_inconnue' });
		await expect(chargerId('85191_9999_00001')).rejects.toMatchObject({ status: 404, body: { message: 'Adresse inconnue' } });
	});

	it('404 `voie_sans_numero` → page 404 « Précisez le numéro »', async () => {
		repondre(404, { title: 'Voie sans numero', status: 404, code: 'voie_sans_numero' });
		await expect(chargerId('85191_0420')).rejects.toMatchObject({ status: 404, body: { message: 'Précisez le numéro' } });
	});

	it.each([
		[503, { title: 'x', status: 503, code: 'adresse_indisponible' }],
		[503, { title: 'x', status: 503, code: 'geocodage_indisponible' }],
		[500, { detail: 'x' }]
	])('%i → page 503, the outage contract', async (status, body) => {
		repondre(status, body);
		await expect(chargerId('85191_0420_00012')).rejects.toMatchObject({
			status: 503,
			body: { message: 'Adresse momentanément indisponible' }
		});
	});

	it('a spent weekly allowance → page 429 with the threshold and the date, the API is not called (H-3)', async () => {
		const fetchStub = vi.fn();
		vi.stubGlobal('fetch', fetchStub);
		vi.mocked(admettreFiche).mockResolvedValueOnce({ admise: false, limite: 100, reprise: new Date('2026-10-28T10:00:00Z') });
		await expect(chargerId('85191_0420_00012')).rejects.toMatchObject({
			status: 429,
			body: { message: expect.stringMatching(/100 fiches d'adresse différentes cette semaine.*le 28 octobre/) }
		});
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('a network failure → page 503', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('fetch failed'); }));
		await expect(chargerId('85191_0420_00012')).rejects.toMatchObject({ status: 503 });
	});

	it.each([409, 429, 401, 403])('%i → the typed refusal is rendered by the page, never a 503', async (status) => {
		repondre(status, { detail: 'texte serveur' });
		const data = await chargerId('85191_0420_00012');
		expect(data.issue.kind).toBe('panne');
		expect(JSON.stringify(data)).not.toContain('texte serveur');
	});

	it('out of coverage stays a 200 page', async () => {
		repondre(200, {
			statut: 'ok',
			donnees: {
				adresse: { ...FICHE.identite, ban_id: '35238_1234_00001', commune: { code_insee: '35238', nom: 'Rennes' } },
				couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' }
			}
		});
		const data = await chargerId('35238_1234_00001');
		expect(data.issue.kind).toBe('hors_couverture');
	});
});

describe('/adresse/[id] — error page: unknown, street, outage', () => {
	function rendreErreur(status: number, message: string) {
		pageStore.set({ status, error: { message }, url: new URL('https://site.invalid/adresse/x') });
		return render(ErrorPage);
	}

	it('404 « Adresse inconnue »: the contract sentence and « Revenir à la recherche »', () => {
		const { getByRole, getByText } = rendreErreur(404, 'Adresse inconnue');

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Adresse inconnue');
		expect(getByText("Aucune adresse ne porte cet identifiant dans la base d'adresses de ce site.")).toBeTruthy();
		expect(getByRole('link', { name: 'Revenir à la recherche' }).getAttribute('href')).toBe('/');
	});

	it('404 « Précisez le numéro »: a street has no page (D-107)', () => {
		const { getByRole, getByText } = rendreErreur(404, 'Précisez le numéro');

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Précisez le numéro');
		expect(
			getByText("Une page d'adresse porte sur un numéro précis. Choisissez le numéro dans la recherche.")
		).toBeTruthy();
	});

	it('503: one warn notice, no value, « Recharger la fiche » reruns the load', async () => {
		const { getByRole, container } = rendreErreur(503, 'Adresse momentanément indisponible');

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Adresse momentanément indisponible');
		const alerte = getByRole('alert');
		expect(alerte.querySelector('h2')?.textContent).toBe('Les données de cette adresse sont momentanément indisponibles');
		expect(alerte.textContent).toContain(
			"Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète."
		);
		expect(container.querySelectorAll('.notice')).toHaveLength(1);
		expect(container.querySelector('.adresse-identity')).toBeNull();

		await fireEvent.click(getByRole('button', { name: 'Recharger la fiche' }));
		expect(invalidateAll).toHaveBeenCalledTimes(1);
	});

	it('429: the weekly allowance is spent, the loader message and « Revenir à la recherche » (H-3)', () => {
		const message = "Vous avez consulté 100 fiches d'adresse différentes cette semaine.";
		const { getByRole, getByText, container } = rendreErreur(429, message);

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Limite de consultation atteinte');
		expect(getByText(message)).toBeTruthy();
		expect(getByRole('link', { name: 'Revenir à la recherche' }).getAttribute('href')).toBe('/');
		expect(container.querySelector('[role="alert"]')).toBeNull();
	});

	it('T6 (2026-11-10): the breadcrumb of an error page starts at « Accueil », as every page of the site', () => {
		for (const [status, message] of [[404, 'Adresse inconnue'], [429, 'Vous avez consulté 100 fiches.'], [503, 'Adresse momentanément indisponible']] as const) {
			const { getByRole, unmount } = rendreErreur(status, message);
			const fil = getByRole('navigation', { name: "Fil d'Ariane" });
			expect([...fil.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['Accueil', '/']]);
			unmount();
		}
	});

	it('every error page of an address is noindex, nofollow', () => {
		rendreErreur(404, 'Adresse inconnue');
		expect(document.head.querySelector('meta[name="robots"]')?.getAttribute('content')).toBe('noindex, nofollow');
	});
});

// --- 04-18 Task 2 — the four landmarks on screen (D-113, CMP-01, SEM-01) ---------------------

describe('/adresse/[id] — loading in client navigation (04-18)', () => {
	it('navigating to another address: aria-busy, « Chargement des données de l’adresse… », no figure', () => {
		navigatingStore.set({ to: { route: { id: '/(marketing)/adresse/[id]' }, params: { id: '85191_0420_00014' } } });
		try {
			const { container, getByRole } = render(Page, { props: { data: servie() } });

			const chargement = container.querySelector('section[aria-busy="true"]');
			expect(chargement).not.toBeNull();
			expect(getByRole('heading', { level: 2, name: "Chargement des données de l'adresse…" })).toBeTruthy();
			expect(container.querySelector('.chiffres-cles')).toBeNull();
			expect(container.querySelector('section.doclist')).toBeNull();
		} finally {
			navigatingStore.set(null);
		}
	});

	it('a page change of the list on the same address is not a loading of the page', () => {
		navigatingStore.set({ to: { route: { id: '/(marketing)/adresse/[id]' }, params: { id: '85191_0420_00012' } } });
		try {
			const { container } = render(Page, { props: { data: servie() } });

			expect(container.querySelector('section[aria-busy="true"]')).toBeNull();
		} finally {
			navigatingStore.set(null);
		}
	});
});

// --- 04-18 Task 3 — the list page bound to its generation (D-109, T-04-18-01) -------------------

describe('/adresse/[id] — server loader, page of the declared documents', () => {
	function evenementPage(search: string) {
		const url = new URL(`https://site.invalid/adresse/85191_0420_00012${search}`);
		return {
			params: { id: '85191_0420_00012' },
			url,
			request: new Request(url),
			platform: undefined,
			setHeaders: vi.fn(),
			depends: vi.fn()
		};
	}

	const VENTES_OK = { status: 200, body: { statut: 'ok', donnees: VENTES_VIDES } };

	function fetchSelon(dpe: { status: number; body: unknown }, ventes: { status: number; body: unknown } = VENTES_OK) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const brut = String(input);
			const corps = brut.includes('/dpe?')
				? dpe
				: brut.includes('/ventes?')
					? ventes
					: brut.includes('/ventes-declarees?')
						? { status: 200, body: { statut: 'ok', donnees: VENTES_ADRESSE_VIDES } }
						: { status: 200, body: { statut: 'ok', donnees: { adresse: FICHE.identite, couverture: { etat: 'heberge', motif: null } }, meta: { source: [SOURCE] } } };
			return new Response(JSON.stringify(corps.body), { status: corps.status, headers: { 'Content-Type': 'application/json' } });
		});
	}

	async function chargerPage(search: string) {
		return (load as unknown as (e: ReturnType<typeof evenementPage>) => Promise<unknown>)(evenementPage(search));
	}

	it('relays ?page= and g as read', async () => {
		const fetchStub = fetchSelon({ status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } });
		vi.stubGlobal('fetch', fetchStub);

		await chargerPage('?page=2&g=0123456789abcdef');

		const dpe = fetchStub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid')).find((u) => u.pathname.endsWith('/dpe'));
		expect(Object.fromEntries(dpe!.searchParams)).toEqual({ page: '2', g: '0123456789abcdef' });
	});

	it.each([
		['?page=2&g=ffffffffffffffff', 409, 'generation_changee'],
		['?page=9', 404, 'page_hors_limites']
	])('%s answered %i `%s`: 303 back to ?page=1', async (search, status, code) => {
		vi.stubGlobal('fetch', fetchSelon({ status, body: { title: 'x', status, code } }));

		await expect(chargerPage(search)).rejects.toMatchObject({ status: 303, location: '/adresse/85191_0420_00012?page=1' });
	});

	// FRONT WR-02 of the D-15 review: a page or a token the API would refuse (422) leads back to
	// page 1, like a page beyond the last one, instead of an error that « Recharger » repeats.
	it.each(['?page=', '?page=abc', '?page=0', '?page=100001', '?page=1.5', '?page=2&g=0123456789ab', '?g=zz', '?page=2&g='])(
		'%s malformed: 303 back to ?page=1, the list is not read',
		async (search) => {
			const fetchStub = fetchSelon({ status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } });
			vi.stubGlobal('fetch', fetchStub);

			await expect(chargerPage(search)).rejects.toMatchObject({ status: 303, location: '/adresse/85191_0420_00012?page=1' });
			expect(fetchStub.mock.calls.some((call) => String(call[0]).includes('/dpe?'))).toBe(false);
		}
	);

	it.each(['?page=1', '?page=100000', '?page=3&g=0123456789abcdef'])('%s well formed: relayed, no redirect', async (search) => {
		vi.stubGlobal('fetch', fetchSelon({ status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } }));

		await expect(chargerPage(search)).resolves.toBeTruthy();
	});

	it('page 1 without a token cannot be stale: no redirect loop, an outage of the section', async () => {
		vi.stubGlobal('fetch', fetchSelon({ status: 409, body: { title: 'x', status: 409, code: 'generation_changee' } }));

		const data = (await chargerPage('')) as { documents: unknown };

		expect(data.documents).toEqual({ kind: 'panne', panne: { statut: null, titre: null, message: null } });
	});

	it('503 of the list: no throw, the section carries the outage (the identity is read by the layout)', async () => {
		vi.stubGlobal('fetch', fetchSelon({ status: 503, body: { title: 'x', status: 503, code: 'documents_dpe_indisponibles' } }));

		const data = (await chargerPage('')) as { documents: unknown };

		expect(data).toEqual({
			documents: { kind: 'panne', panne: { statut: 503, titre: null, message: null } },
			ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES },
			ventesAdresse: { kind: 'ok', ventes: VENTES_ADRESSE_VIDES_PROJETEES },
			comparables: { kind: 'sans_type' }
		});
	});

	it('the list load declares its own dependency, the only one « Recharger les documents DPE » invalidates', async () => {
		vi.stubGlobal('fetch', fetchSelon({ status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } }));
		const event = evenementPage('');

		await (load as unknown as (e: typeof event) => Promise<unknown>)(event);

		expect(event.depends).toHaveBeenCalledWith('app:adresse-documents');
	});
});

// --- 04-05 Task 1 — sales of the parcel of the point (D-115, LIEU-02, N-01, N-02) ---------------

describe('/adresse/[id] — server loader, sales of the parcel (04-05)', () => {
	function evenementVentes(search: string) {
		const url = new URL(`https://site.invalid/adresse/85191_0420_00012${search}`);
		return { params: { id: '85191_0420_00012' }, url, request: new Request(url), platform: undefined, setHeaders: vi.fn(), depends: vi.fn() };
	}

	function fetchVentes(ventes: { status: number; body: unknown }) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const brut = String(input);
			const corps = brut.includes('/ventes?') ? ventes : { status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } };
			return new Response(JSON.stringify(corps.body), { status: corps.status, headers: { 'Content-Type': 'application/json' } });
		});
	}

	const OK = { status: 200, body: { statut: 'ok', donnees: VENTES_VIDES } };
	const charger = (search: string) => (load as unknown as (e: unknown) => Promise<unknown>)(evenementVentes(search));
	const appelVentes = (stub: ReturnType<typeof fetchVentes>) =>
		stub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid')).find((u) => u.pathname.endsWith('/ventes'));

	it('first page by default, without a token', async () => {
		const stub = fetchVentes(OK);
		vi.stubGlobal('fetch', stub);

		await charger('');

		expect(Object.fromEntries(appelVentes(stub)!.searchParams)).toEqual({ page: '1' });
	});

	it('relays ventes_page and vg as read', async () => {
		const stub = fetchVentes(OK);
		vi.stubGlobal('fetch', stub);

		await charger('?ventes_page=3&vg=fedcba9876543210');

		expect(Object.fromEntries(appelVentes(stub)!.searchParams)).toEqual({ page: '3', vg: 'fedcba9876543210' });
	});

	it.each([
		['?page=2&ventes_page=abc&type=maison', '/adresse/85191_0420_00012?page=2&ventes_page=1&type=maison'],
		['?ventes_page=0', '/adresse/85191_0420_00012?ventes_page=1'],
		['?ventes_page=2&vg=zz', '/adresse/85191_0420_00012?ventes_page=1']
	])('%s malformed: 303 to the first page of the sales, the other parameters kept, the sales not read', async (search, location) => {
		const stub = fetchVentes(OK);
		vi.stubGlobal('fetch', stub);

		await expect(charger(search)).rejects.toMatchObject({ status: 303, location });
		expect(appelVentes(stub)).toBeUndefined();
	});

	it.each([
		[409, 'generation_changee'],
		[404, 'page_hors_limites']
	])('answered %i `%s`: 303 to the first page of the sales, documents parameters kept', async (status, code) => {
		vi.stubGlobal('fetch', fetchVentes({ status, body: { title: 'x', status, code } }));

		await expect(charger('?page=2&g=0123456789abcdef&ventes_page=2&vg=fedcba9876543210')).rejects.toMatchObject({
			status: 303,
			location: '/adresse/85191_0420_00012?page=2&g=0123456789abcdef&ventes_page=1'
		});
	});

	it('first page without a token cannot be stale: an outage of the section, no redirect loop', async () => {
		vi.stubGlobal('fetch', fetchVentes({ status: 409, body: { title: 'x', status: 409, code: 'generation_changee' } }));

		const data = (await charger('')) as { ventes: unknown };

		expect(data.ventes).toEqual({ kind: 'panne', panne: { statut: null, titre: null, message: null } });
	});

	it('503 of the sales: the section carries the outage, the documents are served', async () => {
		vi.stubGlobal('fetch', fetchVentes({ status: 503, body: { title: 'x', status: 503, code: 'ventes_indisponibles' } }));

		const data = (await charger('')) as { documents: { kind: string }; ventes: unknown };

		expect(data.documents.kind).toBe('ok');
		expect(data.ventes).toEqual({ kind: 'panne', panne: { statut: 503, titre: null, message: null } });
	});

	it('declares the dependency « Recharger les ventes » invalidates', async () => {
		vi.stubGlobal('fetch', fetchVentes(OK));
		const event = evenementVentes('');

		await (load as unknown as (e: typeof event) => Promise<unknown>)(event);

		expect(event.depends).toHaveBeenCalledWith('app:adresse-ventes');
	});
});

describe('/adresse/[id] — section « Ventes publiées sur la parcelle » (04-05)', () => {
	const CARTE = {
		date: '2025-11-20',
		nature: 'Vente',
		composition: { maisons: 1, appartements: 0, dependances: 1, locaux_activite: 0 },
		surface_batie: 90,
		prix: 238000,
		prix_m2: 2644,
		prix_affiche: true,
		minimisation: null,
		distance_m: null
	};

	function avecVentes(ventes: Record<string, unknown>, documents: unknown = null) {
		return { ...servie(), documents, ventes: { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, ...ventes } } } as never;
	}

	const section = (container: HTMLElement) => container.querySelector('section.ventes') as HTMLElement;

	it('served sales: h2, ONE common line « … rattachées à aucun logement », its `i` button, the cards', () => {
		const { container, getByRole } = render(Page, { props: { data: avecVentes({ total: 2, ventes: [CARTE, { ...CARTE, date: '2019-03-15' }] }) } });

		expect(getByRole('heading', { level: 3, name: 'Ventes publiées sur la parcelle' })).toBeTruthy();
		const ventes = section(container);
		expect(ventes.querySelectorAll('.ventes-commune')).toHaveLength(1);
		expect(ventes.querySelector('.ventes-commune span')?.textContent).toBe(
			'DVF (DGFiP) · 2021-2025 · ventes de la parcelle, rattachées à aucun logement'
		);
		expect(getByRole('button', { name: 'Source et définition : ventes publiées sur la parcelle' })).toBeTruthy();
		expect(ventes.querySelectorAll('ol.ventes-cartes > li')).toHaveLength(2);
		expect(ventes.textContent).not.toMatch(/logement (recherché|du bien)|dans l'immeuble/);
	});

	it('a served zero: « Aucune vente publiée sur la parcelle dans DVF ({période}). », no card, no range', () => {
		const { container } = render(Page, { props: { data: avecVentes({}) } });

		const ventes = section(container);
		expect(ventes.querySelector('.ventes-phrase')?.textContent).toBe('Aucune vente publiée sur la parcelle dans DVF (2021-2025).');
		expect(ventes.querySelector('ol.ventes-cartes')).toBeNull();
		expect(ventes.querySelector('.ventes-range')).toBeNull();
	});

	it('a withheld price: the badge « Prix non affiché », no amount', () => {
		const retenue = { ...CARTE, prix: null, prix_m2: null, prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' } };
		const { container } = render(Page, { props: { data: avecVentes({ total: 1, ventes: [retenue] }) } });

		const ventes = section(container);
		expect(ventes.querySelector('.ventecard-badge')?.textContent).toBe('Prix non affiché');
		expect(ventes.textContent).not.toContain('€');
	});

	it.each([
		['point_hors_parcelle', null, 'Point hors parcelle', "Le point de l'adresse ne tombe dans aucune parcelle du cadastre importé"],
		['parcelles_multiples', 2, 'Plusieurs parcelles', "Le point de l'adresse touche 2 parcelles"],
		['parcelle_indeterminee', null, 'Parcelle non déterminée', "Le point de l'adresse ne désigne pas une seule parcelle."]
	])('%s: said as such, never a zero', (motif, nombre, badge, phrase) => {
		const { container } = render(Page, {
			props: { data: avecVentes({ etat: 'not_available', motif, nombre_parcelles: nombre, total: null, periode: null, generation: null }) }
		});

		const ventes = section(container);
		expect(ventes.querySelector('.state-badge')?.textContent).toBe(badge);
		expect(ventes.querySelector('.state-help')?.textContent).toContain(phrase);
		expect(ventes.textContent).not.toMatch(/Aucune vente|\b0\b/);
	});

	it('DVF out of coverage: the warn badge of its reason, never a zero', () => {
		const { container } = render(Page, {
			props: { data: avecVentes({ etat: 'out_of_coverage', motif: 'dataset_non_heberge', total: null, periode: null }) }
		});

		const ventes = section(container);
		expect(ventes.querySelector('.state-badge-warn')).not.toBeNull();
		expect(ventes.textContent).not.toMatch(/Aucune vente/);
	});

	it.each([
		['an outage of the API', { kind: 'panne', panne: { statut: 503, titre: null, message: null } }],
		['a cadastre outage served by the API', { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, etat: 'temporary_error', motif: 'lecture_indisponible', total: null } }],
		['a value without a readable count', { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, total: null } }]
	])('%s: the outage of the section alone, « Recharger les ventes » reads the lists again', async (_cas, ventes) => {
		const { invalidate } = await import('$app/navigation');
		vi.mocked(invalidate).mockClear();
		vi.mocked(invalidateAll).mockClear();
		const data = { ...servie(), documents: { kind: 'ok', documents: DOCUMENTS_VIDES_PROJETES }, ventes } as never;
		const { container, getByRole } = render(Page, { props: { data } });

		const ventesSection = section(container);
		expect(ventesSection.querySelector('[role="alert"]')?.textContent).toContain(
			"Les ventes n'ont pas pu être lues. Le reste de la page reste affiché."
		);
		// The identity, the landmarks band and the documents stay displayed.
		expect(getByRole('heading', { level: 1 }).textContent).toBe('12 rue des Lilas');
		expect(container.querySelector('section.doclist')).not.toBeNull();
		await fireEvent.click(getByRole('button', { name: 'Recharger les ventes' }));
		expect(invalidate).toHaveBeenCalledWith('app:adresse-ventes');
		expect(invalidateAll).not.toHaveBeenCalled();
	});

	it('page links carry the DVF generation and keep the other parameters of the page', () => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012?page=2&g=0123456789abcdef') });
		try {
			const ventes = Array.from({ length: 10 }, () => CARTE);
			const { getByRole } = render(Page, { props: { data: avecVentes({ total: 23, pages: 3, ventes }) } });

			expect(getByRole('link', { name: 'Page suivante' }).getAttribute('href')).toBe(
				'?page=2&g=0123456789abcdef&ventes_page=2&vg=fedcba9876543210#ventes'
			);
			expect(getByRole('navigation', { name: 'Pages des ventes publiées sur la parcelle' })).toBeTruthy();
		} finally {
			pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
		}
	});

	it('the page links of the documents keep the sales parameters', () => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012?ventes_page=2&vg=fedcba9876543210') });
		try {
			const documents = {
				kind: 'ok',
				documents: { ...DOCUMENTS_VIDES_PROJETES, total: 23, pages: 3, documents: DOCUMENTS_VIDES_PROJETES.documents }
			};
			const cartes = Array.from({ length: 10 }, (_, rang) => ({
				numero: `2485E00000${String(rang).padStart(2, '0')}C`,
				etiquette_dpe: 'C',
				etiquette_ges: 'B',
				type_batiment: 'maison',
				surface_habitable: 90,
				date_etablissement: '2024-03-12',
				date_fin_validite: '2034-03-11',
				badges: [],
				adresse_declaree: null
			}));
			documents.documents.documents = cartes as never;
			const { getByRole } = render(Page, { props: { data: avecVentes({}, documents) } });

			expect(getByRole('link', { name: 'Page suivante' }).getAttribute('href')).toBe(
				'?page=2&g=0123456789abcdef&ventes_page=2&vg=fedcba9876543210'
			);
		} finally {
			pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
		}
	});
});

// --- 04-05 Task 2 — similar sales (D-114) --------------------------------------------------------

const CRITERES_SERVIS = {
	type: 'maison',
	surface: 90,
	tolerance: 20,
	surface_min: 72,
	surface_max: 108,
	perimetre: 'rayon',
	rayon_m: 1000,
	periode: { annees: 5, debut: 2021, fin: 2025 }
};
const CARTE_RESSEMBLANTE = {
	date: '2025-02-01',
	nature: 'Vente',
	composition: { maisons: 1, appartements: 0, dependances: 0, locaux_activite: 0 },
	surface_batie: 90,
	prix: 300000,
	prix_m2: 3333,
	prix_affiche: true,
	minimisation: null,
	distance_m: 120
};
const COMPARABLES = {
	etat: 'value',
	motif: null,
	rattachement: 'rayon_du_point',
	source: SOURCE_DVF,
	criteres_appliques: CRITERES_SERVIS,
	effectif: 6,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: 'fedcba9876543210',
	ventes: Array.from({ length: 6 }, () => CARTE_RESSEMBLANTE)
};

describe('/adresse/[id] — server loader, similar sales (04-05, D-114)', () => {
	function evenementComparables(search: string) {
		const url = new URL(`https://site.invalid/adresse/85191_0420_00012${search}`);
		return { params: { id: '85191_0420_00012' }, url, request: new Request(url), platform: undefined, setHeaders: vi.fn(), depends: vi.fn() };
	}

	function fetchComparables(comparables: { status: number; body: unknown }) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const brut = String(input);
			const corps = brut.includes('/ventes-ressemblantes?')
				? comparables
				: brut.includes('/ventes?')
					? { status: 200, body: { statut: 'ok', donnees: VENTES_VIDES } }
					: { status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } };
			return new Response(JSON.stringify(corps.body), { status: corps.status, headers: { 'Content-Type': 'application/json' } });
		});
	}

	const OK = { status: 200, body: { statut: 'ok', donnees: COMPARABLES } };
	const charger = (search: string) => (load as unknown as (e: unknown) => Promise<{ comparables: unknown }>)(evenementComparables(search));
	const appel = (stub: ReturnType<typeof fetchComparables>) =>
		stub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid')).find((u) => u.pathname.endsWith('/ventes-ressemblantes'));

	it('no type in the URL: nothing is read, the invitation state (Q6)', async () => {
		const stub = fetchComparables(OK);
		vi.stubGlobal('fetch', stub);

		const data = await charger('?surface=90&rayon=1000');

		expect(appel(stub)).toBeUndefined();
		expect(data.comparables).toEqual({ kind: 'sans_type' });
	});

	it('recipe task 4: the criteria of the URL relayed as read, page 1', async () => {
		const stub = fetchComparables(OK);
		vi.stubGlobal('fetch', stub);

		const data = await charger('?type=maison&surface=90&rayon=1000');

		expect(Object.fromEntries(appel(stub)!.searchParams)).toEqual({ type: 'maison', surface: '90', rayon: '1000', page: '1' });
		expect(data.comparables).toMatchObject({ kind: 'ok', comparables: { effectif: 6, criteres_appliques: CRITERES_SERVIS } });
	});

	it('a page of the similar sales carries vr_page and the DVF generation', async () => {
		const stub = fetchComparables(OK);
		vi.stubGlobal('fetch', stub);

		await charger('?type=maison&tolerance=20&periode=5&vr_page=2&vg=fedcba9876543210');

		expect(Object.fromEntries(appel(stub)!.searchParams)).toEqual({
			type: 'maison',
			tolerance: '20',
			periode: '5',
			page: '2',
			vg: 'fedcba9876543210'
		});
	});

	it.each(['?type=maison&rayon=5000', '?type=maison&tolerance=50', '?type=maison&periode=10', '?type=bureau', '?type=maison&surface=5'])(
		'%s: « critère non reconnu », no read, never widened',
		async (search) => {
			const stub = fetchComparables(OK);
			vi.stubGlobal('fetch', stub);

			const data = await charger(search);

			expect(appel(stub)).toBeUndefined();
			expect(data.comparables).toEqual({ kind: 'criteres_non_reconnus' });
		}
	);

	it('a 422 of the API is a criterion not recognized, never an outage', async () => {
		vi.stubGlobal('fetch', fetchComparables({ status: 422, body: { detail: 'x' } }));

		expect((await charger('?type=maison')).comparables).toEqual({ kind: 'criteres_non_reconnus' });
	});

	it('a stale generation: 303 to the first page, the criteria kept', async () => {
		vi.stubGlobal('fetch', fetchComparables({ status: 409, body: { title: 'x', status: 409, code: 'generation_changee' } }));

		await expect(charger('?type=maison&rayon=1000&vr_page=2&vg=fedcba9876543210')).rejects.toMatchObject({
			status: 303,
			location: '/adresse/85191_0420_00012?type=maison&rayon=1000&ventes_page=1'
		});
	});

	it('a malformed vr_page: 303, nothing read', async () => {
		const stub = fetchComparables(OK);
		vi.stubGlobal('fetch', stub);

		await expect(charger('?type=maison&vr_page=abc')).rejects.toMatchObject({ status: 303 });
		expect(appel(stub)).toBeUndefined();
	});

	it('503 of the similar sales: the outage of its section, the criteria stay in the URL', async () => {
		vi.stubGlobal('fetch', fetchComparables({ status: 503, body: { title: 'x', status: 503, code: 'ventes_indisponibles' } }));

		const data = await charger('?type=maison&surface=90');

		expect(data.comparables).toEqual({ kind: 'panne', panne: { statut: 503, titre: null, message: null } });
	});
});

describe('/adresse/[id] — « Ventes ressemblantes » (04-05, D-114; own section since C38)', () => {
	function avecComparables(comparables: unknown, url = 'https://site.invalid/adresse/85191_0420_00012') {
		pageStore.set({ status: 200, error: null, url: new URL(url) });
		return { ...servie(), documents: null, ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES }, comparables } as never;
	}

	const sousSection = (container: HTMLElement) => container.querySelector('section#ventes-ressemblantes') as HTMLElement;

	afterEach(() => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('its own panel after the sales (C38), closed until asked: h2, the form, the invitation while no type is chosen', async () => {
		const rendu = render(Page, { props: { data: avecComparables({ kind: 'sans_type' }) } });
		const { container, getByRole, queryByRole } = rendu;

		expect(queryByRole('heading', { level: 2, name: 'Ventes ressemblantes' })).toBeNull();
		await ouvrirVentesRessemblantes(rendu);
		const section = sousSection(container);
		// C38: no longer inside the parcel section, so that the declared sales can come between them.
		expect(section.closest('section.ventes')).toBeNull();
		expect(getByRole('heading', { level: 2, name: 'Ventes ressemblantes' })).toBeTruthy();
		expect(section.querySelector('form.comparables-form')).not.toBeNull();
		expect(section.querySelector('.comparables-phrase')?.textContent).toBe('Choisissez un type pour lister les ventes ressemblantes.');
		expect(section.querySelector('ol.ventes-cartes')).toBeNull();
	});

	it('served: the count sentence from the served criteria, cards with their distance, the `i` of the section', () => {
		const data = avecComparables({ kind: 'ok', comparables: COMPARABLES }, 'https://site.invalid/adresse/85191_0420_00012?type=maison&surface=90&rayon=1000');
		const { container, getByRole } = render(Page, { props: { data } });

		const section = sousSection(container);
		expect(section.querySelector('.comparables-effectif span')?.textContent?.replace(/\u00a0/g, ' ')).toBe(
			'6 ventes ressemblantes · maisons · 72 à 108 m² · 1 km · 2021 → 2025'
		);
		expect(section.querySelectorAll('ol.ventes-cartes > li')).toHaveLength(6);
		expect(section.querySelector('.ventecard-distance')?.textContent?.replace(/\u00a0/g, ' ')).toBe("à 120 m (à vol d'oiseau)");
		expect(getByRole('button', { name: 'Source et définition : ventes ressemblantes' })).toBeTruthy();
		expect((getByRole('radio', { name: 'Maison' }) as HTMLInputElement).checked).toBe(true);
	});

	it('zero: the count sentence with the served criteria, then the zero sentence of the contract', () => {
		const data = avecComparables({ kind: 'ok', comparables: { ...COMPARABLES, effectif: 0, ventes: [] } });
		const { container } = render(Page, { props: { data } });

		const section = sousSection(container);
		expect(section.querySelector('.comparables-effectif span')?.textContent).toMatch(/^0\u00a0vente ressemblante · maisons/);
		expect(section.querySelector('.comparables-phrase')?.textContent).toBe(
			'Aucune vente ne correspond à ces critères. Élargissez le rayon, la période ou la tolérance de surface.'
		);
	});

	it('a criterion not recognized: the form stays, the decided sentence (row 34), no card', () => {
		const { container } = render(Page, { props: { data: avecComparables({ kind: 'criteres_non_reconnus' }) } });

		const section = sousSection(container);
		expect(section.querySelector('form.comparables-form')).not.toBeNull();
		const phrase = section.querySelector('[data-criteres="non-reconnus"]');
		// Was the provisional fallback « Valeur non affichable » marked data-provisoire.
		expect(phrase?.textContent).toBe("Ce critère n'est pas reconnu\u202f; choisissez une valeur dans la liste.");
		expect(phrase?.hasAttribute('data-provisoire')).toBe(false);
		expect(section.querySelector('ol.ventes-cartes')).toBeNull();
	});

	it('similar sales without a single point: « Position non déterminée » and the decided sentence (row 33)', () => {
		const servi = {
			etat: 'not_available',
			motif: 'position_indeterminee',
			source: null,
			rattachement: 'rayon_du_point',
			criteres_appliques: null,
			effectif: null,
			ventes: [],
			page: 1,
			par_page: 10,
			pages: 1,
			generation: null
		};
		const { container } = render(Page, {
			props: { data: avecComparables({ kind: 'ok', comparables: servi } as never, 'https://site.invalid/adresse/85191_0420_00012?type=maison') }
		});

		const section = sousSection(container);
		expect(section.querySelector('.state-badge')?.textContent).toBe('Position non déterminée');
		expect(section.querySelector('.state-help')?.textContent).toBe(
			"La position de l'adresse n'est pas établie\u202f; les ventes ressemblantes ne sont pas recherchées autour d'elle."
		);
	});

	it('outage: the encart of the section, the form and the criteria stay', () => {
		const data = avecComparables({ kind: 'panne', panne: { statut: 503, titre: null, message: null } }, 'https://site.invalid/adresse/85191_0420_00012?type=maison&surface=90');
		const { container } = render(Page, { props: { data } });

		const section = sousSection(container);
		expect(section.querySelector('[role="alert"] p')?.textContent).toBe("Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes.");
		expect(section.querySelector('[role="alert"] button')?.textContent?.trim()).toBe('Relancer la recherche des ventes');
		expect((section.querySelector('input[name="surface"]') as HTMLInputElement).value).toBe('90');
	});

	it('A5: a precise served outage message precedes the drawer copy', () => {
		const data = avecComparables({ kind: 'panne', panne: { statut: 503, titre: null, message: 'La lecture DVF a expiré.' } }, 'https://site.invalid/adresse/85191_0420_00012?type=maison');
		const { container } = render(Page, { props: { data } });
		expect([...sousSection(container).querySelectorAll('[role="alert"] p')].map((p) => p.textContent)).toEqual([
			'La lecture DVF a expiré.',
			"Source indisponible. La base des ventes n'a pas répondu. Ce n'est pas une absence de ventes."
		]);
	});

	it('no single point: « Position non déterminée », without the sentence of landmark ③', () => {
		const data = avecComparables({ kind: 'ok', comparables: { ...COMPARABLES, etat: 'not_available', motif: 'position_indeterminee', effectif: null, ventes: [] } });
		const { container } = render(Page, { props: { data } });

		const section = sousSection(container);
		expect(section.querySelector('.state-badge')?.textContent).toBe('Position non déterminée');
		expect(section.textContent).not.toContain('exposition');
	});

	it('client navigation to new criteria: « Chargement des ventes ressemblantes… », aria-busy, form usable', () => {
		navigatingStore.set({
			to: {
				route: { id: '/(marketing)/adresse/[id]' },
				params: { id: '85191_0420_00012' },
				url: new URL('https://site.invalid/adresse/85191_0420_00012?type=maison&rayon=1000')
			}
		});
		try {
			const { container } = render(Page, { props: { data: avecComparables({ kind: 'sans_type' }) } });

			const section = sousSection(container);
			expect(section.getAttribute('aria-busy')).toBe('true');
			expect(section.querySelector('[aria-busy="true"]')?.textContent).toBe('Chargement des ventes ressemblantes…');
			expect(section.querySelector('button[type="submit"]')?.hasAttribute('disabled')).toBe(false);
			// The rest of the page is not a loading of the address.
			expect(container.querySelector('h1')?.textContent).toBe('12 rue des Lilas');
		} finally {
			navigatingStore.set(null);
		}
	});

	it('page links of the similar sales: vr_page and vg, every other parameter kept', () => {
		const data = avecComparables(
			{ kind: 'ok', comparables: { ...COMPARABLES, effectif: 13, pages: 2, ventes: Array.from({ length: 10 }, () => CARTE_RESSEMBLANTE) } },
			'https://site.invalid/adresse/85191_0420_00012?type=maison&rayon=commune'
		);
		const { container } = render(Page, { props: { data } });

		const suivant = sousSection(container).querySelector('a.comparables-page') as HTMLAnchorElement;
		expect(suivant.textContent).toBe('Page suivante');
		expect(suivant.getAttribute('href')).toBe('?type=maison&rayon=commune&vr_page=2&vg=fedcba9876543210#ventes-ressemblantes');
	});
});

// --- 04-06 — export and print (D-104, UI-01) ---------------------------------------------------

describe('/adresse/[id] — export and print (04-06, D-104, UI-01)', () => {
	it('« Télécharger (CSV) » is a secondary button that opens the export of the phase 3', async () => {
		const { getByRole, queryByRole } = render(Page, { props: { data: { ...servie({ ...FICHE, themes: [] }), ventes: null, comparables: null } } });

		const bouton = getByRole('button', { name: 'Télécharger (CSV)' });
		expect(bouton.classList.contains('adresse-secondary')).toBe(true);
		expect(queryByRole('dialog', { name: 'Exporter les données' })).toBeNull();
		await fireEvent.click(bouton);
		expect(getByRole('dialog', { name: 'Exporter les données' })).toBeTruthy();
	});

	it('the printed header and the printed sources are mounted, never for an address out of coverage', () => {
		const servieRendue = render(Page, { props: { data: { ...servie({ ...FICHE, themes: [] }), ventes: null, comparables: null } } });
		expect(servieRendue.container.querySelector('.adresse-actions')).not.toBeNull();
		expect(servieRendue.container.textContent).toMatch(/Consulté le \d{2}\/\d{2}\/\d{4}/);
		servieRendue.unmount();

		const hors = render(Page, { props: { data: { issue: { kind: 'hors_couverture', fiche: { ...FICHE, couverture: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre' } }, rapport: RAPPORT_VIDE }, documents: null } } });
		expect(hors.container.querySelector('.adresse-actions')).toBeNull();
		expect(hors.queryByRole('button', { name: 'Télécharger (CSV)' })).toBeNull();
	});
});

describe('/adresse/[id] — « Comparer avec une autre adresse » with a full selection (D-15 FRONT WR-03)', () => {
	it('opens the four addresses kept in the tab, never replaces them with this one', async () => {
		const gardees = ['85191_0601_00001', '85191_0602_00002', '85191_0603_00003', '85191_0604_00004'];
		addressComparisonSelection.replace(gardees);
		vi.mocked(goto).mockClear();
		const { getByRole } = render(Page, { props: { data: servie() } });

		await fireEvent.click(getByRole('link', { name: 'Comparer avec une autre adresse' }));

		expect(goto).toHaveBeenCalledWith(`/comparer?adresses=${gardees.join(',')}`);
		expect(addressComparisonSelection.codes).toEqual(gardees);
		addressComparisonSelection.replace([]);
	});
});

// --- C37: sales declared at the address -----------------------------------------------------------

// The sales declared at the address, as served by `/v1/adresse/{id}/ventes-declarees` (none attached).
const VENTES_ADRESSE_VIDES = {
	etat: 'value',
	motif: null,
	rattachement: 'adresse_declaree_vente',
	source: SOURCE_DVF,
	periode: '2021-2025',
	total: 0,
	// C4 (04.5-07): served under `vefa=incluses`, no VEFA.
	nombre_vefa: 0,
	page: 1,
	pages: 1,
	par_page: 10,
	generation: 'fedcba9876543210',
	ventes: []
};
const { rattachement: _rattachementAdresse, ...VENTES_ADRESSE_VIDES_PROJETEES } = VENTES_ADRESSE_VIDES;

describe('/adresse/[id] — server loader, sales declared at the address (C37)', () => {
	function evenementVentes(search: string) {
		const url = new URL(`https://site.invalid/adresse/85191_0420_00012${search}`);
		return { params: { id: '85191_0420_00012' }, url, request: new Request(url), platform: undefined, setHeaders: vi.fn(), depends: vi.fn() };
	}

	function fetchDeclarees(declarees: { status: number; body: unknown }) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const brut = String(input);
			const corps = brut.includes('/ventes-declarees?')
				? declarees
				: brut.includes('/ventes?')
					? { status: 200, body: { statut: 'ok', donnees: VENTES_VIDES } }
					: { status: 200, body: { statut: 'ok', donnees: DOCUMENTS_VIDES } };
			return new Response(JSON.stringify(corps.body), { status: corps.status, headers: { 'Content-Type': 'application/json' } });
		});
	}

	const OK = { status: 200, body: { statut: 'ok', donnees: VENTES_ADRESSE_VIDES } };
	const charger = (search: string) => (load as unknown as (e: unknown) => Promise<unknown>)(evenementVentes(search));
	const appel = (stub: ReturnType<typeof fetchDeclarees>) =>
		stub.mock.calls.map((call) => new URL(String(call[0]), 'http://o.invalid')).find((u) => u.pathname.endsWith('/ventes-declarees'));

	it('read with the other lists, first page by default, projected without its relation key', async () => {
		const stub = fetchDeclarees(OK);
		vi.stubGlobal('fetch', stub);

		const data = (await charger('')) as { ventesAdresse: unknown };

		expect(appel(stub)!.pathname).toBe('/v1/adresse/85191_0420_00012/ventes-declarees');
		// C4 (04.5-07, R8): the VEFA are asked for, listed and marked, never counted; C3 (04.5-08): by
		// number and suffix.
		expect(Object.fromEntries(appel(stub)!.searchParams)).toEqual({ page: '1', vefa: 'incluses', regle: 'numero_et_suffixe' });
		expect(data.ventesAdresse).toEqual({ kind: 'ok', ventes: VENTES_ADRESSE_VIDES_PROJETEES });
	});

	it('relays vd_page and the DVF token vg as read', async () => {
		const stub = fetchDeclarees(OK);
		vi.stubGlobal('fetch', stub);

		await charger('?vd_page=2&vg=fedcba9876543210');

		expect(Object.fromEntries(appel(stub)!.searchParams)).toEqual({ page: '2', vefa: 'incluses', regle: 'numero_et_suffixe', vg: 'fedcba9876543210' });
	});

	it('a page change of the parcel list leaves its first page without token', async () => {
		const stub = fetchDeclarees(OK);
		vi.stubGlobal('fetch', stub);

		await charger('?ventes_page=2&vg=fedcba9876543210');

		expect(Object.fromEntries(appel(stub)!.searchParams)).toEqual({ page: '1', vefa: 'incluses', regle: 'numero_et_suffixe' });
	});

	it.each([
		['?page=2&vd_page=abc', '/adresse/85191_0420_00012?page=2&ventes_page=1'],
		['?vd_page=0&type=maison', '/adresse/85191_0420_00012?type=maison&ventes_page=1']
	])('%s malformed: 303 to the first page of the sales, nothing read', async (search, location) => {
		const stub = fetchDeclarees(OK);
		vi.stubGlobal('fetch', stub);

		await expect(charger(search)).rejects.toMatchObject({ status: 303, location });
		expect(appel(stub)).toBeUndefined();
	});

	it.each([
		[409, 'generation_changee'],
		[404, 'page_hors_limites']
	])('answered %i `%s`: 303 to the first page of the sales', async (status, code) => {
		vi.stubGlobal('fetch', fetchDeclarees({ status, body: { title: 'x', status, code } }));

		await expect(charger('?vd_page=2&vg=fedcba9876543210')).rejects.toMatchObject({
			status: 303,
			location: '/adresse/85191_0420_00012?ventes_page=1'
		});
	});

	it('503: the outage of its section, the parcel sales and the documents served', async () => {
		vi.stubGlobal('fetch', fetchDeclarees({ status: 503, body: { title: 'x', status: 503, code: 'ventes_indisponibles' } }));

		const data = (await charger('')) as { documents: { kind: string }; ventes: { kind: string }; ventesAdresse: unknown };

		expect(data.documents.kind).toBe('ok');
		expect(data.ventes.kind).toBe('ok');
		expect(data.ventesAdresse).toEqual({ kind: 'panne', panne: { statut: 503, titre: null, message: null } });
	});

	it('an unreadable state is an outage, never a served list', async () => {
		vi.stubGlobal('fetch', fetchDeclarees({ status: 200, body: { statut: 'ok', donnees: { ...VENTES_ADRESSE_VIDES, etat: 'inconnu' } } }));

		const data = (await charger('')) as { ventesAdresse: { kind: string } };

		expect(data.ventesAdresse.kind).toBe('panne');
	});
});

describe('/adresse/[id] — section « Ventes déclarées à cette adresse » (C37)', () => {
	const CARTE = {
		date: '2019-05-01',
		nature: 'Vente',
		composition: { maisons: 0, appartements: 2, dependances: 1, locaux_activite: 0 },
		surface_batie: null,
		prix: 456789,
		prix_m2: null,
		prix_affiche: true,
		minimisation: null,
		distance_m: null
	};

	function avecDeclarees(ventesAdresse: unknown, ventes: unknown = { kind: 'ok', ventes: VENTES_VIDES_PROJETEES }) {
		return { ...servie(), ventes, ventesAdresse } as never;
	}
	const servies = (partiel: Record<string, unknown>) => ({ kind: 'ok', ventes: { ...VENTES_ADRESSE_VIDES_PROJETEES, ...partiel } });
	const section = (container: HTMLElement) => container.querySelector('section#ventes-adresse') as HTMLElement;

	it('its own h2 after the sales of the parcel, ONE common line, its `i` button, the cards with the whole amount', () => {
		const { container, getByRole } = render(Page, { props: { data: avecDeclarees(servies({ total: 1, ventes: [CARTE] })) } });

		expect(getByRole('heading', { level: 3, name: 'Ventes déclarées à cette adresse' })).toBeTruthy();
		// Right after the section of the parcel sales, never inside it (C38: before the similar sales).
		expect(container.querySelector('section#ventes')?.nextElementSibling?.id).toBe('ventes-adresse');
		const declarees = section(container);
		expect(declarees.querySelectorAll('.ventes-commune')).toHaveLength(1);
		expect(declarees.querySelector('.ventes-commune span')?.textContent?.replace(/\s+/g, ' ')).toBe(
			'DVF (DGFiP) · 2021-2025 · ventes déclarées à ce numéro, rattachées à aucun logement'
		);
		expect(getByRole('button', { name: 'Source et définition : ventes déclarées à cette adresse' })).toBeTruthy();
		const cartes = declarees.querySelectorAll('ol.ventes-cartes > li');
		expect(cartes).toHaveLength(1);
		expect(cartes[0].textContent).toContain('2 appartements, 1 dépendance');
		expect(cartes[0].textContent?.replace(/\s/g, '')).toContain('456789€');
	});

	it('none attached (C44): « Aucune vente rattachée à cette adresse dans DVF ({période}). », next to a parcel point outside every parcel', () => {
		const horsParcelle = { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, etat: 'not_available', motif: 'point_hors_parcelle', total: null, periode: null, generation: null } };
		const { container } = render(Page, { props: { data: avecDeclarees(servies({}), horsParcelle) } });

		expect(section(container).querySelector('.ventes-phrase')?.textContent).toBe('Aucune vente rattachée à cette adresse dans DVF (2021-2025).');
		expect(section(container).querySelector('ol.ventes-cartes')).toBeNull();
		// The two relations stay apart: the parcel says its own state.
		expect(container.querySelector('section#ventes .state-badge')?.textContent).toBe('Point hors parcelle');
	});

	it('an address with a suffix: its label and the suffix the imported data do not keep (C44), never a zero', () => {
		const { container } = render(Page, {
			props: { data: avecDeclarees(servies({ etat: 'not_available', motif: 'suffixe_non_distingue', total: null, periode: null, generation: null })) }
		});

		const declarees = section(container);
		expect(declarees.querySelector('.state-badge')?.textContent).toBe('Suffixe non distingué');
		expect(declarees.querySelector('.state-help')?.textContent).toContain('Les données DVF importées ne gardent pas le suffixe');
		expect(declarees.textContent).not.toMatch(/Aucune vente|\b0\b/);
		expect(declarees.querySelector('.ventes-adresse-absence')?.hasAttribute('data-provisoire')).toBe(false);
	});

	it('Codex review M1 (04.5-08): a served reason without wording is « Valeur non affichable », marked provisional, without a sentence', () => {
		const { container } = render(Page, {
			props: { data: avecDeclarees(servies({ etat: 'not_available', motif: 'suffixe_futur', total: null, periode: null, generation: null })) }
		});

		const declarees = section(container);
		const absence = declarees.querySelector('.ventes-adresse-absence');
		expect(absence?.getAttribute('data-provisoire')).toBe('true');
		expect(absence?.querySelector('.state-badge')?.textContent).toBe('Valeur non affichable');
		expect(absence?.querySelector('.state-help')).toBeNull();
		// Second Codex review (D45-01, D45-08): the served absence stays typed, with its served reason.
		expect(absence?.getAttribute('data-motif')).toBe('suffixe_futur');
		expect(absence?.querySelector('.state-absence')?.getAttribute('data-state')).toBe('not_available');
		// Never the glossary's bare state and sentence, another reason, nor a zero.
		expect(declarees.textContent).not.toMatch(/Non publiée|La source ne publie pas|Suffixe|Aucune vente|\b0\b/);
	});

	it('DVF out of coverage: the warn badge of its reason, never a zero', () => {
		const { container } = render(Page, {
			props: { data: avecDeclarees(servies({ etat: 'out_of_coverage', motif: 'dataset_non_heberge', total: null, periode: null })) }
		});

		expect(section(container).querySelector('.state-badge-warn')).not.toBeNull();
		expect(section(container).textContent).not.toMatch(/Aucune vente/);
	});

	it('an outage: the alert of its section alone, « Recharger les ventes » reads the sales lists again', async () => {
		const { invalidate } = await import('$app/navigation');
		vi.mocked(invalidate).mockClear();
		const { container } = render(Page, { props: { data: avecDeclarees({ kind: 'panne', panne: { statut: 503, titre: null, message: null } }) } });

		const declarees = section(container);
		expect(declarees.querySelector('[role="alert"]')?.textContent).toContain("Les ventes n'ont pas pu être lues.");
		expect(container.querySelector('section#ventes [role="alert"]')).toBeNull();
		await fireEvent.click(within(declarees).getByRole('button', { name: 'Recharger les ventes' }));
		expect(invalidate).toHaveBeenCalledWith('app:adresse-ventes');
	});

	// UI-005 (plan 04.5-06): the two relations stay two lists; the same sale may be in both, said once,
	// and the declared list is folded, its title and served range in its summary.
	it('both relations with sales: the overlap sentence, then the declared list, never folded', () => {
		const parcelle = { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, total: 1, ventes: [CARTE] } };
		const { container } = render(Page, { props: { data: avecDeclarees(servies({ total: 1, ventes: [CARTE] }), parcelle) } });

		const declarees = section(container);
		const phrase = declarees.querySelector('.ventes-recouvrement');
		expect(phrase?.textContent).toContain('Une même vente peut figurer dans les deux listes');
		expect(declarees.querySelector('details')).toBeNull();
		const titre = declarees.querySelector('h3#ventes-adresse-titre');
		expect(titre?.textContent).toBe('Ventes déclarées à cette adresse');
		expect(titre?.getAttribute('tabindex')).toBe('-1');
		expect(declarees.querySelectorAll('ol.ventes-cartes > li')).toHaveLength(1);
		expect(declarees.querySelectorAll('.ventes-commune')).toHaveLength(1);
		expect(container.querySelectorAll('section#ventes ol.ventes-cartes > li')).toHaveLength(1);
	});

	it('a parcel without sale: no overlap sentence', () => {
		const horsParcelle = { kind: 'ok', ventes: { ...VENTES_VIDES_PROJETEES, etat: 'not_available', motif: 'point_hors_parcelle', total: null, periode: null, generation: null } };
		const { container } = render(Page, { props: { data: avecDeclarees(servies({ total: 1, ventes: [CARTE] }), horsParcelle) } });
		expect(section(container).querySelector('.ventes-recouvrement')).toBeNull();
	});

	it('`vd_page` in the URL: the category and the criterion of the sales are rendered open on its lists', () => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012?vd_page=2&vg=fedcba9876543210') });
		const cartes = Array.from({ length: 2 }, (_, i) => ({ ...CARTE, date: `2019-05-${String(i + 1).padStart(2, '0')}` }));
		const data = {
			...servie(FICHE, { ...RAPPORT_VIDE, themes: [THEME_VENTES] }),
			ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES },
			ventesAdresse: servies({ total: 12, pages: 2, page: 2, ventes: cartes })
		} as never;
		const { container } = render(Page, { props: { data } });

		const critere = container.querySelector('li#critere-ventes_parcelle details.det') as HTMLDetailsElement;
		expect(critere.open).toBe(true);
		expect(critere.querySelector('section#ventes-adresse')).not.toBeNull();
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('pages of its own (`vd_page`), bound to the DVF generation, the other lists kept', () => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012?page=2&g=0123456789abcdef&ventes_page=2') });
		const cartes = Array.from({ length: 10 }, (_, i) => ({ ...CARTE, date: `2019-05-${String(i + 1).padStart(2, '0')}` }));
		const { container } = render(Page, { props: { data: avecDeclarees(servies({ total: 12, pages: 2, ventes: cartes })) } });

		const suivant = section(container).querySelector('a.ventes-adresse-page') as HTMLAnchorElement;
		expect(suivant.textContent).toBe('Page suivante');
		expect(suivant.getAttribute('href')).toBe('?page=2&g=0123456789abcdef&ventes_page=2&vd_page=2&vg=fedcba9876543210#ventes-adresse');
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});
});

// --- C38 and C42: order of the sales sections, similar sales prefilled from the documents ----------

describe('/adresse/[id] — ordre des ventes (C38) et ventes ressemblantes préremplies (C42)', () => {
	const carte = (numero: string, partiel: Record<string, unknown> = {}) => ({
		numero,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		type_batiment: 'maison',
		surface_habitable: 92.4,
		date_etablissement: '2024-03-12',
		date_fin_validite: '2034-03-11',
		badges: [],
		adresse_declaree: null,
		...partiel
	});
	const DEUX_MAISONS = {
		...DOCUMENTS_VIDES_PROJETES,
		total: 2,
		distribution: { A: 0, B: 0, C: 0, D: 2, E: 0, F: 0, G: 0, unknown: 0 },
		documents: [carte('2485E0000003C'), carte('2285E0000002D', { surface_habitable: 70, date_etablissement: '2022-06-01' })],
		selection: {
			etat: 'unique',
			motif: 'document_valide',
			date: '2024-03-12',
			document: { numero: '2485E0000003C', etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' },
			documents_ex_aequo: []
		}
	};

	function donnees(url: string, documents: unknown = DEUX_MAISONS, comparables: unknown = { kind: 'sans_type' }) {
		pageStore.set({ status: 200, error: null, url: new URL(url) });
		return {
			...servie(),
			documents: { kind: 'ok', documents },
			ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES },
			ventesAdresse: { kind: 'ok', ventes: VENTES_ADRESSE_VIDES_PROJETEES },
			comparables
		} as never;
	}

	afterEach(() => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('C38: parcel sales, then declared sales, then similar sales in their panel, in this order', () => {
		const { container } = render(Page, { props: { data: donnees('https://site.invalid/adresse/85191_0420_00012') } });

		const sections = [...container.querySelectorAll('section#ventes, section#ventes-adresse, section#ventes-ressemblantes')];
		expect(sections.map((section) => section.id)).toEqual(['ventes', 'ventes-adresse', 'ventes-ressemblantes']);
		expect(sections[0].querySelector(':scope > h3')?.textContent).toBe('Ventes publiées sur la parcelle');
		expect(sections[1].querySelector(':scope > h3')?.textContent).toBe('Ventes déclarées à cette adresse');
		expect(container.querySelector('dialog.panneau-comparables h2')?.textContent).toBe('Ventes ressemblantes');
	});

	it('C42: no criterion in the link, every document a house: type and surface prefilled, their origin said, nothing searched', async () => {
		const rendu = render(Page, { props: { data: donnees('https://site.invalid/adresse/85191_0420_00012') } });
		const { container, getByRole } = rendu;
		await ouvrirVentesRessemblantes(rendu);

		const section = container.querySelector('section#ventes-ressemblantes') as HTMLElement;
		expect((getByRole('radio', { name: 'Maison' }) as HTMLInputElement).checked).toBe(true);
		expect((getByRole('spinbutton', { name: 'Surface (m²)' }) as HTMLInputElement).value).toBe('92');
		// UI-007 (04.5-06): the surface names the served document it comes from, linked to its page.
		expect(section.querySelector('.comparables-origine')?.textContent).toMatch(
			/^Type d'après les DPE de cette adresse\. Surface issue du DPE n°\u00a0\S+, le plus récent de cette adresse\.$/
		);
		expect(section.querySelector('.comparables-origine a')?.getAttribute('href')).toMatch(/^\/dpe\/\S+$/);
		// The visitor still asks for the list: no count, no card, no « choose a type ».
		expect(section.querySelector('.comparables-effectif')).toBeNull();
		expect(section.querySelector('ol.ventes-cartes')).toBeNull();
		expect(section.querySelector('.comparables-phrase')).toBeNull();
		expect(getByRole('button', { name: 'Afficher les ventes ressemblantes' })).toBeTruthy();
	});

	it('C42: criteria in the link are the visitor own: never overwritten, no origin line', () => {
		const url = 'https://site.invalid/adresse/85191_0420_00012?type=appartement&surface=&tolerance=20';
		const comparables = { kind: 'criteres_non_reconnus' };
		const { container, getByRole } = render(Page, { props: { data: donnees(url, DEUX_MAISONS, comparables) } });

		expect((getByRole('radio', { name: 'Appartement' }) as HTMLInputElement).checked).toBe(true);
		expect((getByRole('spinbutton', { name: 'Surface (m²)' }) as HTMLInputElement).value).toBe('');
		expect(container.querySelector('.comparables-origine')).toBeNull();
	});

	it('C42: types that differ and a tie of the most recent date: nothing prefilled, the invitation stays', async () => {
		const melanges = {
			...DEUX_MAISONS,
			documents: [carte('2485E0000003C'), carte('2285E0000002D', { type_batiment: 'appartement' })],
			selection: { etat: 'egalite', motif: 'document_valide', date: '2024-03-12', document: null, documents_ex_aequo: [] }
		};
		const rendu = render(Page, { props: { data: donnees('https://site.invalid/adresse/85191_0420_00012', melanges) } });
		const { container, getByRole } = rendu;
		await ouvrirVentesRessemblantes(rendu);

		expect((getByRole('radio', { name: 'Maison' }) as HTMLInputElement).checked).toBe(false);
		expect((getByRole('radio', { name: 'Appartement' }) as HTMLInputElement).checked).toBe(false);
		expect((getByRole('spinbutton', { name: 'Surface (m²)' }) as HTMLInputElement).value).toBe('');
		expect(container.querySelector('.comparables-origine')).toBeNull();
		expect(container.querySelector('section#ventes-ressemblantes .comparables-phrase')?.textContent).toBe(
			'Choisissez un type pour lister les ventes ressemblantes.'
		);
	});
});

// --- 04.5-02 — the address page read by themes (D45-07, D45-11, UI-024) -------------------------

// --- 04.7-03 — the fiche on the report format (UI-SPEC v3, Origami A1, A5) ---------------------

describe('/adresse/[id] — la fiche sur le format du rapport (04.7-03)', () => {
	const GEORISQUES = { cle: 'georisques', nom: 'Géorisques', producteur: 'BRGM', url: 'https://www.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: null, mode: 'direct' as const };
	const INSEE = { cle: 'revenus', nom: 'Filosofi', producteur: 'INSEE', url: 'https://www.insee.fr', licence: 'Licence Ouverte 2.0', millesime: '2021', mode: 'import' as const };
	const base = (partiel: Partial<ThemeRapport> & Pick<ThemeRapport, 'id' | 'section' | 'titre' | 'etat' | 'ton' | 'verdict'>): ThemeRapport => ({
		granularite: { niveau: 'adresse', rayon_m: null },
		chiffres: [],
		details: {},
		rattachement: null,
		sources: [INSEE],
		motif: null,
		etat_libelle: null,
		accroche: null,
		pannes_partielles: [],
		...partiel
	});
	// The headline and the alert are served since 04.7-17; the real DPE theme was captured before.
	const DPE = projeterTheme({ ...dpeReel, accroche: '11 DPE déclarés', rattachement: { ...dpeReel.rattachement, alerte: 'Ces DPE peuvent venir de plusieurs bâtiments.' } })!;
	const ARGILES = base({ id: 'argiles', section: 'risques', titre: 'Retrait-gonflement des argiles', etat: 'panne', ton: 'info', verdict: 'Géorisques n’a pas répondu ; ce n’est pas une absence de risque.', sources: [GEORISQUES], motif: 'amont_indisponible', accroche: 'Source indisponible' });
	const RADON = base({ id: 'radon', section: 'risques', titre: 'Radon', etat: 'ok', ton: 'rassurant', granularite: { niveau: 'commune', rayon_m: null }, verdict: 'Potentiel radon faible sur la commune.', chiffres: [{ id: 'categorie', libelle: 'Catégorie', valeur: 1, unite: 'count', millesime: null }], sources: [GEORISQUES] });
	const ICPE = base({ id: 'icpe_commune', section: 'risques', titre: 'Installations classées', etat: 'zero', ton: 'neutre', granularite: { niveau: 'commune', rayon_m: null }, verdict: 'Aucune installation classée recensée sur la commune.', details: { nombre: 0 } });
	const COPRO = base({ id: 'copropriete', section: 'batiment', titre: 'Copropriété', etat: 'non_concerne', ton: 'neutre', verdict: 'Aucune copropriété immatriculée à cette adresse.' });
	const CONSTRUCTION = base({
		id: 'construction',
		section: 'batiment',
		titre: 'Année de construction',
		etat: 'ok',
		// Neutral as the API serves it on a probable building (04.7-17): never « Favorable » without a sure link.
		ton: 'neutre',
		granularite: { niveau: 'batiment', rayon_m: null },
		verdict: 'Bâtiment construit en 1965 selon la BDNB.',
		chiffres: [{ id: 'annee', libelle: 'Année de construction', valeur: 1965, unite: 'annee', millesime: '2025' }],
		rattachement: {
			entite: 'batiment_bdnb',
			cle: 'bdnb-bg-1234',
			confiance: 'probable',
			candidats: 2,
			motif: 'plusieurs_batiments_sur_la_parcelle',
			alerte: 'Plusieurs bâtiments sur la parcelle : celui de l’adresse n’est pas établi.',
			maillons: [{ lien: 'batiment_bdnb', confiance: 'probable' }]
		}
	});
	// A theme the front does not know, with a masked price in its details (prix_affiche everywhere).
	const EAU = base({
		id: 'eau_potable',
		section: 'vivre',
		titre: 'Eau potable',
		etat: 'ok',
		ton: 'neutre',
		granularite: { niveau: 'udi', rayon_m: null },
		verdict: 'Eau conforme aux limites de qualité sur la dernière année.',
		chiffres: [{ id: 'conformite', libelle: 'Prélèvements conformes', valeur: 98.5, unite: 'pourcent', millesime: '2026' }],
		accroche: '98,5\u00a0%',
		details: {
			ventes: [
				{ date: '2025-05-23', prix: 180000, prix_affiche: false },
				{ date: '2024-01-12', prix: 150000, prix_affiche: true }
			]
		}
	});
	const THEMES = [DPE, COPRO, CONSTRUCTION, ARGILES, RADON, ICPE, THEME_VENTES, EAU];
	const RAPPORT: RapportPublic = {
		date: '2026-10-30',
		themes: THEMES,
		rattachementPhrase: 'Ces DPE peuvent venir de plusieurs bâtiments.',
		// What the API serves for these themes (written here, never computed by the code under test).
		resume: resumeServi({
			pointsAdresse: ['dpe'],
			favorablesSecteur: ['radon'],
			pannes: ['argiles'],
			sourcesEnPanne: ['georisques'],
			sansObjet: ['copropriete'],
			lus: 7,
			lusAdresse: 4,
			lusSecteur: 3,
			total: 8,
			parSection: {
				batiment: { vigilance: 1, pannes: 0, lus: 3, total: 3 },
				risques: { vigilance: 0, pannes: 1, lus: 2, total: 3 },
				marche: { vigilance: 0, pannes: 0, lus: 1, total: 1 },
				vivre: { vigilance: 0, pannes: 0, lus: 1, total: 1 }
			}
		}),
		carte: null,
		chiffresCles: [
			{ id: 'prix_m2_median', libelle: 'Prix médian au m²', theme: 'prix_commune', etat: 'ok', granularite: { niveau: 'commune', rayon_m: null }, chiffres: [{ id: 'mediane', libelle: 'Médiane', valeur: 2450, unite: 'euro_per_m2', millesime: '2025' }], motif: null },
			{ id: 'loyer_m2', libelle: 'Loyer de marché', theme: 'loyers', etat: 'manque', granularite: null, chiffres: [], motif: 'loyer_non_publie' },
			{ id: 'dpe_dominant', libelle: 'DPE le plus fréquent', theme: 'dpe', etat: 'ok', granularite: null, chiffres: [{ id: 'classe', libelle: 'Classe', valeur: 'F', unite: 'classe_dpe', millesime: '2026-10-01' }], motif: null },
			{ id: 'annee_construction', libelle: 'Année de construction', theme: 'construction', etat: 'ok', granularite: null, chiffres: [{ id: 'annee', libelle: 'Année', valeur: 1965, unite: 'annee', millesime: '2025' }], motif: null }
		]
	};
	const rendre = () => render(Page, { props: { data: { ...servie(FICHE, RAPPORT), ventes: null, ventesAdresse: null, comparables: null } as never } });

	it('A6: the print action and the limits link retain their exact names', () => {
		const { getByRole } = rendre();
		expect(getByRole('button', { name: /^Imprimer la fiche$/ })).toBeTruthy();
		expect(getByRole('link', { name: /^Ce que cette fiche ne montre pas$/ }).getAttribute('href')).toBe('/ce-que-nous-ne-montrons-pas');
	});

	afterEach(() => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('only the served themes appear, one criterion each, in four closed categories in the order of the classement (D-09)', () => {
		const { container } = rendre();

		expect([...container.querySelectorAll('li[id^="critere-"]')].map((d) => d.getAttribute('data-theme')).sort()).toEqual(THEMES.map((t) => t.id).sort());
		expect(container.querySelectorAll('li[id^="critere-"] details.det')).toHaveLength(THEMES.length);
		const categories = [...container.querySelectorAll('details.cat')] as HTMLDetailsElement[];
		expect(categories.map((c) => [c.id, c.querySelector('summary h3')?.textContent])).toEqual([
			['q-prix', 'Prix et ventes'],
			['q-logement', 'Logement et DPE'],
			['q-quotidien', 'Autour de l’adresse'],
			['q-risques', 'Risques']
		]);
		expect(categories.every((c) => !c.open)).toBe(true);
		const ids = (partie: string) => [...container.querySelectorAll(`details#${partie} li[id^="critere-"]`)].map((li) => li.getAttribute('data-theme'));
		expect(ids('q-risques')).toEqual(['argiles', 'radon', 'icpe_commune']);
		expect(ids('q-logement')).toEqual(['dpe', 'construction', 'copropriete']);
		expect(ids('q-quotidien')).toEqual(['eau_potable']);
		expect(ids('q-prix')).toEqual(['ventes_parcelle']);
		// One form at every scale; a value wider than the address stays marked (D-11, D-32).
		expect(container.querySelector('#critere-radon')?.classList.contains('is-commune')).toBe(true);
		expect(container.querySelector('#critere-dpe')?.classList.contains('is-commune')).toBe(false);
	});

	it('no gesture opens everything: on a large screen the categories are open, on a small one each opens by itself', () => {
		const { queryByRole } = rendre();
		expect(queryByRole('button', { name: /Tout (afficher|replier|déplier)/ })).toBeNull();
	});

	it('above the threshold of two columns the four categories are open, under it they are closed', async () => {
		const large = vi.fn((requete: string) => ({ matches: requete.includes('min-width'), media: requete, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
		vi.stubGlobal('matchMedia', large);
		try {
			const { container } = rendre();
			await tick();
			await tick();
			expect(large.mock.calls[0][0]).toBe('(min-width: 1081px)');
			expect(container.querySelectorAll('details.cat:not([open])')).toHaveLength(0);
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('another address starts with its categories closed', async () => {
		const { container, rerender } = rendre();
		(container.querySelector('details#q-prix') as HTMLDetailsElement).open = true;
		await fireEvent(container.querySelector('details#q-prix') as HTMLElement, new Event('toggle'));
		await tick();
		expect(container.querySelector('details.cat[open]')).not.toBeNull();

		const autre = { ...FICHE, identite: { ...FICHE.identite, ban_id: '85191_0420_00014' } };
		await rerender({ data: { ...servie(autre, RAPPORT), ventes: null, ventesAdresse: null, comparables: null } as never });
		await tick();
		expect(container.querySelector('details.cat[open]')).toBeNull();
	});

	it('several categories stay open: opening one closes no other', async () => {
		const { container } = rendre();
		const prix = container.querySelector('details#q-prix') as HTMLDetailsElement;
		const risques = container.querySelector('details#q-risques') as HTMLDetailsElement;
		prix.open = true;
		risques.open = true;
		await fireEvent(prix, new Event('toggle'));
		await fireEvent(risques, new Event('toggle'));
		await tick();
		expect(prix.open && risques.open).toBe(true);
	});

	it('an anchor opens its category, and the rest of the list when the row is further', async () => {
		const { container } = rendre();
		try {
			window.location.hash = '#critere-radon';
			await fireEvent(window, new Event('hashchange'));
			await tick();
			expect((container.querySelector('details#q-risques') as HTMLDetailsElement).open).toBe(true);
			expect(container.querySelector('#critere-radon')?.closest('ul')?.classList.contains('replie')).toBe(false);
			expect((container.querySelector('details#q-logement') as HTMLDetailsElement).open).toBe(false);
		} finally {
			window.location.hash = '';
		}
	});

	it('a click on the link already in the URL opens the category again: the browser fires no hashchange', async () => {
		const { container } = rendre();
		try {
			window.location.hash = '#critere-dpe';
			const logement = container.querySelector('details#q-logement') as HTMLDetailsElement;
			expect(logement.open).toBe(false);
			await fireEvent.click(container.querySelector('a[href="#critere-dpe"]') as HTMLElement);
			await tick();
			expect(logement.open).toBe(true);
		} finally {
			window.location.hash = '';
		}
	});

	it('the anchor of the sources opens their list', async () => {
		const { container } = rendre();
		const repli = container.querySelector('#sources details') as HTMLDetailsElement;
		expect(repli.open).toBe(false);
		await fireEvent.click(container.querySelector('.sommaire-large a[href="#sources"]') as HTMLElement);
		await tick();
		expect(repli.open).toBe(true);
	});

	it('the similar sales: the link beside the price, the sentence that keeps them from an estimate in « Prix et ventes »', () => {
		const { container } = rendre();
		expect(container.querySelector('section#bref .lien-suite')?.textContent).toBe('Voir les ventes ressemblantes');
		const section = container.querySelector('details#q-prix section#s-comparables') as HTMLElement;
		expect(section.querySelector('h4')?.textContent).toBe('Ventes ressemblantes');
		expect(section.textContent).toContain('elles n’estiment pas ce logement');
		expect(section.textContent).toContain('seule son année est');
		expect(section.querySelector('a')?.getAttribute('href')).toBe(container.querySelector('section#bref .lien-suite')?.getAttribute('href'));
	});

	it('without a category of prices, the similar sales keep their sentence on the page', () => {
		const sansPrix = THEMES.filter((t) => t.id !== 'ventes_parcelle');
		const rapport: RapportPublic = { ...RAPPORT, themes: sansPrix };
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });
		expect(container.querySelector('details#q-prix')).toBeNull();
		expect(container.querySelector('section#s-comparables')?.textContent).toContain('elles n’estiment pas ce logement');
	});

	it('A1: each status is the one of its own theme, and the row says the served verdict', () => {
		const { container } = rendre();

		// The expected statuses are written here, never computed by the code under test.
		const ATTENDUS: Record<string, string | null> = {
			dpe: 'Vigilance',
			copropriete: 'Sans objet',
			construction: null,
			argiles: 'Source indisponible',
			radon: 'Favorable',
			icpe_commune: 'Zéro',
			ventes_parcelle: null,
			eau_potable: null
		};
		const sansInsecables = (t: string) => t.replace(/[  ]/g, ' ');
		// 04.8-08 (D-31): the page may add the full form of an acronym right after that acronym, once; with that
		// addition taken out (`sansDeveloppement`), the sentence is the served one, nothing else changed.
		for (const theme of THEMES) {
			const critere = container.querySelector(`li#critere-${theme.id}`) as HTMLElement;
			expect(verdictCoherent(theme)).toBe(true);
			expect(critere.querySelector('.ligne-val .badge')?.textContent?.trim() ?? null, theme.id).toBe(ATTENDUS[theme.id]);
			// The verdict is the served one (the typography only sets non-breaking spaces).
			expect(sansDeveloppement(sansInsecables(critere.querySelector('.ligne-phrase')?.textContent ?? '')), theme.id).toBe(sansInsecables(theme.verdict));
		}
		expect(container.querySelector('#critere-copropriete .badge')?.textContent?.trim()).toBe('Sans objet');
		expect(container.querySelector('#critere-argiles .badge')?.textContent?.trim()).toBe('Source indisponible');
		// Favorable needs a sure link: a probable building stays neutral.
		expect(container.querySelector('#critere-construction .ligne-val .badge')).toBeNull();
		// A vigilance is a full badge in a row, an outline on a tile (UI-SPEC § 3.6).
		expect(container.querySelector('#critere-dpe .badge')?.classList.contains('badge-plein')).toBe(true);
	});

	it('A5: no fabricated zero — an outage and a missing value carry no figure, the zero is the served one', () => {
		const { container } = rendre();

		const argiles = container.querySelector('#critere-argiles') as HTMLElement;
		// 4.9 lot 5: the state is said once, by the badge.
		expect(argiles.querySelector('.ligne-val .val')).toBeNull();
		expect(argiles.querySelector('.ligne-val .badge')?.textContent).toBe('Source indisponible');
		expect(argiles.querySelector('dl.chiffres')).toBeNull();
		expect(argiles.textContent).not.toMatch(/(^|\s)0(\s|$)|Aucun/);
		expect(container.querySelector('#critere-icpe_commune .badge')?.textContent?.trim()).toBe('Zéro');
		for (const theme of THEMES) expect(aucunZeroFabrique(theme)).toBe(true);
	});

	it('prix_affiche: a masked price is never shown, the other one is', () => {
		const { container } = rendre();

		const eau = container.querySelector('#critere-eau_potable') as HTMLElement;
		expect(eau.textContent).toContain('Prix non affiché');
		expect(eau.textContent).not.toMatch(/180[\s\u00a0\u202f]?000/);
		expect(eau.textContent).toMatch(/150[\s\u00a0\u202f]?000/);
	});

	it('D-12: an isolated recent sale is served by its year alone and last; the page shows the served order, never an amount', async () => {
		// As the API serves it: the recent isolated sale is minimised to its year and placed after the others.
		const ventes = [
			{ date: '2024-01-12', prix: 150000, prix_affiche: true },
			{ date: '2025', prix: 180000, prix_affiche: false, minimisation: { motif: 'vente_isolee_recente' } }
		];
		const rapport: RapportPublic = { ...RAPPORT, themes: [{ ...EAU, details: { ventes } }] };
		const { container, getByRole } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		const lignes = [...container.querySelectorAll('#critere-eau_potable tbody tr')];
		expect(lignes).toHaveLength(2);
		expect(lignes[0].textContent).toMatch(/150[\s\u00a0\u202f]?000/);
		expect(lignes[1].textContent).toContain('2025');
		expect(lignes[1].textContent).toContain('Prix non affiché');
		expect(lignes[1].textContent).not.toMatch(/180[\s\u00a0\u202f]?000|vente_isolee_recente|minimisation/);

		// Printing opens the details: the masked amount is in no part of the opened page.
		window.dispatchEvent(new Event('beforeprint'));
		expect(container.textContent).not.toMatch(/180[\s\u00a0\u202f]?000/);
		window.dispatchEvent(new Event('afterprint'));

		// The CSV export carries no individual sale at all.
		let blob: Blob | null = null;
		const { createObjectURL, revokeObjectURL } = URL;
		URL.createObjectURL = (b: Blob) => ((blob = b), 'blob:test');
		URL.revokeObjectURL = () => {};
		vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
		try {
			await fireEvent.click(getByRole('button', { name: 'Télécharger (CSV)' }));
			await fireEvent.click(within(getByRole('dialog', { name: 'Exporter les données' })).getByRole('button', { name: /Télécharger/ }));
		} finally {
			URL.createObjectURL = createObjectURL;
			URL.revokeObjectURL = revokeObjectURL;
		}
		expect(blob).not.toBeNull();
		const texte = await new Promise<string>((resolve) => {
			const lecteur = new FileReader();
			lecteur.onload = () => resolve(String(lecteur.result));
			lecteur.readAsText(blob!);
		});
		expect(texte).toContain('Eau potable');
		expect(texte).not.toMatch(/180[\s\u00a0\u202f]?000|150[\s\u00a0\u202f]?000/);
	});

	it('a theme the front does not know renders from its own fields: title, figure, scale, source', () => {
		const { container } = rendre();

		const eau = container.querySelector('#critere-eau_potable') as HTMLElement;
		expect(eau.querySelector('.ligne-titre')?.textContent).toBe('Eau potable');
		expect(eau.querySelector('.ligne-val .val')?.textContent).toBe('98,5 %');
		// A level this front does not know: its code made readable, no pictogram, no error (UI-SPEC § 3.5).
		expect(eau.querySelector('.ligne-ech .ech-mot')?.textContent).toBe('Échelle : udi');
		expect(eau.querySelector('.ligne-ech svg')).toBeNull();
		expect(eau.querySelector('.det-sources li')?.textContent).toContain('Filosofi');
		expect(eau.querySelector('.det-sources li')?.textContent).toContain('INSEE');
	});

	it('the scale of every value is written next to it', () => {
		const { container } = rendre();

		for (const theme of THEMES) {
			expect(container.querySelector(`#critere-${theme.id} .ligne-ech .ech-mot`)?.textContent?.trim(), theme.id).toBeTruthy();
			expect(container.querySelector(`#critere-${theme.id} .det-ech`)?.textContent, theme.id).toMatch(/^Cette valeur vaut .+\.$/);
		}
		// The address and the building say their word; a communal tile says it in the title of its zone.
		expect(container.querySelector('#critere-dpe .ligne-ech .ech-mot')?.textContent).toBe('Adresse');
		expect(container.querySelector('#critere-construction .ligne-ech .ech-mot')?.textContent).toBe('Bâtiment');
		expect(container.querySelector('#critere-radon .det-ech')?.textContent).toBe('Cette valeur vaut pour toute la commune.');
		expect(container.querySelector('#q-risques .com-t')?.textContent).toContain('Pour toute la commune de La Roche-sur-Yon');
	});

	it('the arrival names the points at the address; the sentence and the vigilances of the commune are in « Risques » (D-08, R3)', () => {
		const { container } = rendre();

		const bref = container.querySelector('section#bref') as HTMLElement;
		expect(bref.querySelector('h2')?.textContent).toBe('En bref');
		expect(bref.querySelector('.bref-p')).toBeNull();
		expect([...bref.querySelectorAll('.bref-l a')].map((a) => a.getAttribute('href'))).toEqual(['#critere-dpe']);
		expect(bref.querySelector('.bref-v')?.textContent).toBe('11 DPE déclarés');
		// The arrow is decoration and ends each link: its last element, kept with the value it follows.
		for (const lien of bref.querySelectorAll('.bref-l a')) {
			expect(lien.querySelectorAll('.fleche')).toHaveLength(1);
			const fleche = [...lien.querySelectorAll('*')].at(-1) as HTMLElement;
			expect(fleche.tagName).toBe('SPAN');
			expect(fleche.classList.contains('fleche')).toBe(true);
			expect(fleche.getAttribute('aria-hidden')).toBe('true');
			expect(fleche.textContent).toBe('↓');
		}
		expect(bref.textContent).not.toContain('Favorable');
		expect(bref.querySelector('a[href="#critere-radon"]')).toBeNull();

		const risques = container.querySelector('details#q-risques') as HTMLElement;
		expect(risques.querySelector('.bref-p')?.textContent?.replace(/[\u00a0\u202f]/g, ' ')).toBe(
			'Sur toute la fiche : 1 point de vigilance à l’adresse ou autour, et 1 critère n’a pas pu être lu.'
		);
		expect(risques.querySelector('.bilan h4')?.textContent).toBe('Vigilances pour toute la commune');
		// Every target of the lists exists in the page.
		for (const lien of container.querySelectorAll('.bref-l a')) expect(container.querySelector(lien.getAttribute('href')!)).not.toBeNull();
	});

	it('an outage is said on its closed row and in sight, its sources named behind the « i », never as « aucun risque »', () => {
		const { container } = rendre();

		const risques = container.querySelector('details#q-risques summary') as HTMLElement;
		expect(risques.querySelector('.signal-panne')?.textContent?.replace(/\u00a0/g, ' ')).toBe('Source indisponible : Retrait-gonflement des argiles');
		const note = container.querySelector('.lecture-sources') as HTMLElement;
		expect(note.closest('details')).toBeNull();
		expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe('Certaines sources n’ont pas répondu ; ce n’est jamais un « aucun risque ».');
		const panneau = [...container.querySelectorAll('dialog.source-panel')].find((d) => d.querySelector('h2')?.textContent === 'Sources qui n’ont pas répondu');
		expect(panneau?.textContent).toContain('Géorisques');
	});

	it('the gaps are counted on the closed row of their category, the out of coverage said apart', () => {
		const manque: ThemeRapport = { ...ARGILES, id: 'fibre', section: 'batiment', titre: 'Fibre', etat: 'manque', ton: 'info', verdict: 'Pas de valeur publiée.', motif: 'non_publie', etat_libelle: null, accroche: null };
		const hors: ThemeRapport = { ...manque, id: 'parcelle', titre: 'Parcelle', etat: 'hors_couverture' };
		const rapport: RapportPublic = {
			...RAPPORT,
			themes: [...THEMES, manque, hors],
			resume: { ...RAPPORT.resume!, sansValeur: ['fibre', 'parcelle'] }
		};
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect(container.querySelector('details#q-logement summary .signal-discret')?.textContent).toBe('2 critères sans valeur ici, dont 1 hors couverture');
		// Each row keeps the word of its badge, one click away.
		expect(container.querySelector('#critere-fibre .badge')?.textContent).toBe('Non publié');
		expect(container.querySelector('#critere-parcelle .badge')?.textContent).toBe('Hors couverture');
	});

	it('the key figures: the served value and attachment in a tile, a state without a figure on its line, the build year on its row (D-33, R9)', () => {
		const { container } = rendre();

		const cellules = [...container.querySelectorAll('section#bref .chiffres-cles .cle')];
		expect(cellules.map((c) => c.querySelector('dt')?.textContent)).toEqual(['Prix médian au m²', 'DPE (diagnostic de performance énergétique) le plus fréquent']);
		expect(cellules[0].querySelector('.cle-v')?.textContent).toBe('2 450 €/m²');
		expect(cellules[1].querySelector('.dpe-pastille')?.textContent).toBe('F');
		// A served state without a figure is said as such (its served reason), never as a figure nor as an outage.
		const ligne = container.querySelector('section#bref .absences li') as HTMLElement;
		expect(ligne.querySelector('.badge')?.textContent).toBe('Loyer non publié');
		expect(ligne.textContent).toContain('Loyer de marché');
		expect(container.querySelector('section#bref .badge-panne')).toBeNull();
		// The year leaves the head, its value and its served alert stay on its row: nothing is lost.
		expect(container.querySelector('section#bref')?.textContent).not.toContain('1965');
		expect(container.querySelector('#critere-construction')?.textContent).toContain('1965');
		expect(container.querySelector('#critere-construction .ligne-rat')?.textContent).toContain('Plusieurs bâtiments sur la parcelle');
	});

	it('a served outage of a key figure is said as an outage, where the answer is expected, not as a gap and never as a zero', () => {
		const rapport: RapportPublic = {
			...RAPPORT,
			chiffresCles: RAPPORT.chiffresCles.map((k) => (k.id === 'prix_m2_median' ? { ...k, etat: 'panne', chiffres: [], motif: 'amont_indisponible' } : k))
		};
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect(container.querySelector('section#bref .chiffres-cles')?.textContent).not.toContain('Prix médian au m²');
		const lignes = [...container.querySelectorAll('section#bref .absences li')];
		expect(lignes[0].querySelector('.badge-panne')?.textContent?.trim()).toBe('Source indisponible');
		expect(lignes[0].textContent).toContain('Prix médian au m²');
	});

	it('the attachment is said, the summary lists every row in the order of the page, every target exists', () => {
		const { container } = rendre();

		expect(container.querySelector('.rattachement-phrase')?.textContent).toContain('Ces DPE peuvent venir de plusieurs bâtiments.');
		const sommaire = container.querySelector('nav.sommaire-large') as HTMLElement;
		const cibles = [...sommaire.querySelectorAll('a')].map((a) => a.getAttribute('href') as string);
		const page = [...container.querySelectorAll('details.cat, details.cat li.ligne, details.cat #s-comparables')].map((el) => `#${el.id}`);
		expect(page.filter((id) => id.startsWith('#critere-')).length, 'the report has rows').toBeGreaterThan(1);
		expect(cibles.slice(0, -2)).toEqual(page);
		expect(cibles.slice(-2)).toEqual(['#lire', '#sources']);
		const titres = new Map([...sommaire.querySelectorAll('a')].map((a) => [a.getAttribute('href'), a.textContent]));
		for (const ligne of container.querySelectorAll('details.cat li.ligne')) expect(titres.get(`#${ligne.id}`)).toBeTruthy();
		for (const cible of cibles) expect(container.querySelector(cible), cible).not.toBeNull();
		for (const ancre of ['#bref', '#q-prix', '#q-logement', '#q-quotidien', '#q-risques', '#s-comparables', '#lire', '#sources']) expect(container.querySelector(ancre), ancre).not.toBeNull();
		expect(container.querySelector('.lien-limites a')).not.toBeNull();
	});

	it('the closed row counts the served vigilances placed in the category, never the commune as the address', () => {
		const rapport: RapportPublic = {
			...RAPPORT,
			resume: { ...RAPPORT.resume!, pointsAdresse: [], pointsSecteur: ['radon'] }
		};
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect(container.querySelector('details#q-risques summary')?.textContent).toContain('1 vigilance pour toute la commune');
		expect(container.querySelector('details#q-risques summary')?.textContent).not.toContain('à l’adresse ou autour');
		expect(container.querySelector('details#q-logement .signal-vigilance')).toBeNull();
	});

	it('a key price says what it measures; a build year stays on its row whatever its attachment, the front has no rejection rule', () => {
		const rapport: RapportPublic = {
			...RAPPORT,
			themes: THEMES.map((t) => (t.id === 'construction' ? { ...t, rattachement: null } : t)),
			chiffresCles: RAPPORT.chiffresCles.map((k) =>
				k.id === 'prix_m2_median'
					? { ...k, chiffres: [{ id: 'maisons', libelle: 'Maisons', valeur: 4357, unite: 'euro_per_m2', millesime: '2025' }, { id: 'appartements', libelle: 'Appartements', valeur: 3385, unite: 'euro_per_m2', millesime: '2025' }] }
					: k
			)
		};
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		const cellules = [...container.querySelectorAll('.chiffres-cles .cle')];
		expect(cellules[0].textContent).toContain('Maisons');
		// 04.8-03 (D-33): « Non rattachée » is not written by the front; the served `ok` and figure are shown.
		expect(container.querySelector('#critere-construction')?.textContent).toContain('1965');
		expect(container.querySelector('#critere-construction')?.textContent).not.toContain('Non rattachée');
	});

	it('on paper a table keeps its ten first rows and says so', () => {
		const lignes = Array.from({ length: 11 }, (_, i) => ({ arret: `Arrêt ${i + 1}`, distance_m: 100 + i }));
		const rapport: RapportPublic = { ...RAPPORT, themes: [{ ...EAU, id: 'arrets', details: { arrets: lignes } }] };
		const { container } = render(Page, { props: { data: { ...servie(FICHE, rapport), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect(container.querySelectorAll('#critere-arrets tbody tr')).toHaveLength(11);
		expect(container.querySelectorAll('#critere-arrets tbody tr.au-dela')).toHaveLength(1);
		expect(container.querySelector('#critere-arrets .note-impression')?.textContent).toContain('les 10 premières lignes');
	});

	it('the page is never indexed', () => {
		rendre();

		expect(document.head.querySelector('meta[name="robots"]')?.getAttribute('content')).toBe('noindex, nofollow');
	});

	it('printing opens every criterion, the legend and the sources, then gives their state back', () => {
		const { container } = rendre();
		const tous = () => [...container.querySelectorAll('details')] as HTMLDetailsElement[];
		expect(tous().some((d) => !d.open)).toBe(true);

		window.dispatchEvent(new Event('beforeprint'));
		expect(tous().every((d) => d.open)).toBe(true);
		window.dispatchEvent(new Event('afterprint'));
		expect(tous().some((d) => !d.open)).toBe(true);
	});

	it('« Télécharger (CSV) » lists one row per criterion, grouped by part', async () => {
		const { getByRole } = rendre();

		await fireEvent.click(getByRole('button', { name: 'Télécharger (CSV)' }));
		const dialogue = getByRole('dialog', { name: 'Exporter les données' });
		for (const theme of THEMES) expect(within(dialogue).getByRole('checkbox', { name: new RegExp(`^${theme.titre}`) })).toBeTruthy();
	});
});

// --- 04.7-03, Codex review 2 --------------------------------------------------------------------

describe('/adresse/[id] — revue Codex 2 (04.7-03)', () => {
	afterEach(() => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('nothing read at a scale: the arrival and the block of « Risques » say so, never « Rien à signaler »', () => {
		const panne: ThemeRapport = { ...THEME_VENTES, id: 'argiles', section: 'risques', titre: 'Argiles', etat: 'panne', ton: 'info', chiffres: [], verdict: 'Géorisques n’a pas répondu.' };
		const resume = resumeServi({ pannes: ['argiles'], sourcesEnPanne: ['georisques'], total: 1, parSection: { ...SECTIONS_VIDES, risques: { vigilance: 0, pannes: 1, lus: 0, total: 1 } } });
		const { container } = render(Page, { props: { data: { ...servie(FICHE, { ...RAPPORT_VIDE, themes: [panne], resume }), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect([...container.querySelectorAll('.bref-vide')].map((p) => p.textContent)).toEqual(['Aucun critère lu à cette échelle.', 'Aucun critère lu à cette échelle.']);
		expect(container.querySelector('section#bref')?.textContent).not.toContain('Rien à signaler');
		expect(container.querySelector('details#q-risques')?.textContent).not.toContain('Rien à signaler');
	});

	it('a scale read with no point says it in words', () => {
		const resume = resumeServi({ lus: 2, lusAdresse: 1, lusSecteur: 1, total: 2 });
		const risque: ThemeRapport = { ...THEME_VENTES, id: 'argiles', section: 'risques', titre: 'Argiles', rattachement: null };
		const { container } = render(Page, { props: { data: { ...servie(FICHE, { ...RAPPORT_VIDE, themes: [THEME_VENTES, risque], resume }), ventes: null, ventesAdresse: null, comparables: null } as never } });

		expect([...container.querySelectorAll('.bref-vide')].map((p) => p.textContent)).toEqual([
			'Rien à signaler au point, au bâtiment ni aux alentours.',
			'Rien à signaler pour toute la commune.'
		]);
	});

	it('the panel follows the URL: opened by its criteria, closed when the URL no longer asks for it', async () => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012?type=maison') });
		const data = { ...servie(), documents: null, ventes: { kind: 'ok', ventes: VENTES_VIDES_PROJETEES }, comparables: { kind: 'sans_type' } } as never;
		const { container } = render(Page, { props: { data } });
		await tick();
		const dialogue = container.querySelector('dialog.panneau-comparables') as HTMLDialogElement;
		expect(dialogue.hasAttribute('open')).toBe(true);

		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012') });
		await tick();
		expect(dialogue.hasAttribute('open')).toBe(false);
	});
});

// --- 04.7-17 — the page reads what the API serves, and its head shows a static map --------------

describe('/adresse/[id] — lecture du rapport servi et carte de l’en-tête (04.7-17)', () => {
	const CARTE = {
		plan: '/carte/85191_0420_00012/plan.png',
		parcelles: '/carte/85191_0420_00012/parcelles.png',
		empriseM: 300,
		attribution: '© IGN (Plan IGN, Parcellaire Express)',
		licence: 'Licence Ouverte 2.0',
		geoportail: 'https://www.geoportail.gouv.fr/carte?c=-1.42,46.67&z=18'
	};
	const rendre = (rapport: Partial<RapportPublic>) =>
		render(Page, { props: { data: { ...servie(FICHE, { ...RAPPORT_VIDE, themes: [THEME_VENTES], ...rapport }), ventes: null, ventesAdresse: null, comparables: null } as never } });

	afterEach(() => {
		pageStore.set({ status: 200, error: null, url: new URL('https://site.invalid/') });
	});

	it('the DOM is the order of reading: identity, answer, categories, then the attachment, the actions and the map, never by CSS order', () => {
		const { container } = rendre({ carte: CARTE, rattachementPhrase: 'Phrase servie.' });
		const ordre = ['h1', 'section#bref', '.sommaire-large', '.lecture', '.rattachement-phrase', '.adresse-actions', '.adresse-carte', '.fiche-corps'].map((s) => container.querySelector(s) as HTMLElement);
		for (let i = 1; i < ordre.length; i++) expect(ordre[i - 1].compareDocumentPosition(ordre[i]) & Node.DOCUMENT_POSITION_FOLLOWING, String(i)).toBeTruthy();
		expect(container.querySelectorAll('section#bref')).toHaveLength(1);
	});

	it('4.9 A7: the legend is folded on the fiche, under the anchor the table of contents links to', () => {
		const { container } = rendre({});
		const lire = container.querySelector('#lire') as HTMLElement;
		const repli = lire.querySelector('details') as HTMLDetailsElement;
		expect(repli.open).toBe(false);
		expect(repli.querySelector('summary')?.textContent?.trim()).toBe('Voir les échelles et les états');
		// Paper does not open a fold: the legend is written out next to it.
		expect(lire.querySelector('.lire-impression')?.textContent).toContain('Les échelles');
		expect(container.querySelector('.sommaire-large a[href="#lire"]')).not.toBeNull();
	});

	it('4.9 lot 5: one primary action in sight, the others grouped in a native <details>', () => {
		const { container } = rendre({});
		const actions = container.querySelector('.adresse-actions') as HTMLElement;
		const repli = actions.querySelector('details') as HTMLDetailsElement;
		expect(repli.open).toBe(false);
		expect(repli.querySelector('summary')?.textContent?.trim()).toBe('Autres actions');
		expect([...repli.querySelectorAll('a, button')].map((e) => e.textContent?.trim())).toEqual(['Ventes ressemblantes', 'Télécharger (CSV)', 'Imprimer la fiche']);
		const dehors = [...actions.querySelectorAll('a, button')].filter((e) => !repli.contains(e));
		expect(dehors.map((e) => e.textContent?.trim())).toEqual(['Comparer avec une autre adresse']);
		expect(dehors[0].classList.contains('adresse-primary')).toBe(true);
	});

	it('the head shows the served map, its images on the site’s own /carte/ path, beside the attachment sentence', () => {
		const { container } = rendre({ carte: CARTE, rattachementPhrase: 'Phrase servie 5.' });

		const haut = container.querySelector('.fiche-tete') as HTMLElement;
		expect(haut.classList.contains('avec-carte')).toBe(true);
		expect([...haut.querySelectorAll('.adresse-carte img')].map((i) => i.getAttribute('src'))).toEqual([CARTE.plan, CARTE.parcelles]);
		expect(haut.querySelector('.rattachement-phrase')?.textContent?.trim()).toBe('Phrase servie 5.');
	});

	it('no served map: no frame, the head keeps one column', () => {
		const { container } = rendre({ carte: null });

		expect(container.querySelector('.adresse-carte')).toBeNull();
		expect(container.querySelector('.fiche-tete')?.classList.contains('avec-carte')).toBe(false);
	});

	it('the attachment sentence is the served one, never one computed from the themes; none served, none shown', () => {
		const incertain: ThemeRapport = { ...THEME_VENTES, id: 'parcelle', rattachement: { ...THEME_VENTES.rattachement!, confiance: 'incertaine', alerte: 'Alerte de parcelle.' } };
		const { container } = rendre({ themes: [incertain], rattachementPhrase: null });

		expect(container.querySelector('.rattachement-phrase')).toBeNull();
	});

	it('a summary not served is said, never filled by the page; the categories keep their place and count nothing', () => {
		const { container } = rendre({ resume: null });

		const bref = container.querySelector('section#bref') as HTMLElement;
		expect(bref.querySelector('.bref-indisponible')?.textContent).toBe('Le résumé de cette fiche n’est pas disponible.');
		// Nothing computed in its place: no sentence, no list, no count on a closed row.
		expect(container.querySelector('.bref-p')).toBeNull();
		expect(container.querySelector('.bref-l')).toBeNull();
		expect(container.querySelector('.cat-signaux')).toBeNull();
		expect(container.querySelector('details#q-prix summary h3')?.textContent).toBe('Prix et ventes');
	});

	it('the summary columns are the API’s: a radius of 1 200 m served on the address side stays there', () => {
		const bruit: ThemeRapport = { ...THEME_VENTES, id: 'bruit', section: 'risques', titre: 'Bruit', ton: 'vigilance', granularite: { niveau: 'rayon', rayon_m: 1200 }, rattachement: null };
		const resume = resumeServi({ pointsAdresse: ['bruit'], lus: 2, lusAdresse: 2, total: 2, parSection: { ...SECTIONS_VIDES, risques: { vigilance: 1, pannes: 0, lus: 1, total: 1 }, marche: { vigilance: 0, pannes: 0, lus: 1, total: 1 } } });
		const { container } = rendre({ themes: [THEME_VENTES, bruit], resume });

		expect(container.querySelector('section#bref .bref-l a')?.getAttribute('href')).toBe('#critere-bruit');
		// A theme the front does not place stays on the page, in « Autres critères », counted in no category.
		expect(container.querySelector('section#q-autres #critere-bruit')).not.toBeNull();
		expect(container.querySelector('.signal-vigilance')).toBeNull();
	});
});
