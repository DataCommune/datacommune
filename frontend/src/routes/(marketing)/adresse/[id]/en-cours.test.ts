// R-07 (2026-11-10): the first view of an address answers after a short wait; a criterion whose
// source has not answered yet says « Lecture en cours », never « Source indisponible », and its value
// arrives without a reload. The weekly allowance is the real one here: the follow-up of a fiche is
// never counted as a second fiche.
import { render, waitFor } from '@testing-library/svelte';
import type { Cookies } from '@sveltejs/kit';
import type { Writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidate: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		page: writable({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012') }),
		navigating: writable(null)
	};
});

import { invalidateAll } from '$app/navigation';
import { page } from '$app/stores';
import type { IssueRapportAdresse } from '$lib/api/adresses';
import { projeterRapport, lecturesEnCours } from '$lib/api/rapport';
import { LIBELLE_STATUT, lireResume, mentionLectures, phraseEnBref, statutDe } from '$lib/data/adresse-rapport';
import { admettreFiche, QUOTA_COOKIE } from '$lib/server/fiche-quota';
import { load as loadIdentite } from './+layout.server';
import Page from './+page.svelte';
import { GET as suite } from './suite/+server';

const ID = '85191_0420_00012';
const SOURCE_ERRIAL = { cle: 'errial', nom: 'ERRIAL', producteur: 'Géorisques', url: 'https://errial.georisques.gouv.fr', licence: 'Licence Ouverte 2.0', millesime: null, mode: 'direct' };
const SOURCE_DVF = { ...SOURCE_ERRIAL, cle: 'dvf', nom: 'DVF', producteur: 'DGFiP', mode: 'import' };

const theme = (id: string, titre: string, plus: Record<string, unknown>) => ({
	id,
	section: 'risques',
	titre,
	granularite: { niveau: 'parcelle', rayon_m: null },
	chiffres: [],
	details: {},
	rattachement: null,
	sources: [SOURCE_ERRIAL],
	motif: null,
	pannes_partielles: [],
	etat_libelle: null,
	...plus
});

const PRIX = theme('prix_commune', 'Prix dans la commune', {
	section: 'marche',
	etat: 'ok',
	ton: 'neutre',
	verdict: 'Prix médian des appartements : 3 200 €/m².',
	sources: [SOURCE_DVF],
	accroche: '3 200 €/m²'
});
const SOLS_EN_COURS = theme('pollution_sols', 'Sols pollués', {
	etat: 'en_cours',
	ton: 'info',
	verdict: 'La source n’a pas encore répondu.',
	accroche: 'Lecture en cours'
});
const SOLS_LUS = theme('pollution_sols', 'Sols pollués', {
	etat: 'zero',
	ton: 'rassurant',
	verdict: 'Aucun site pollué recensé sur la parcelle.',
	accroche: 'Aucun'
});
const SOLS_EN_PANNE = theme('pollution_sols', 'Sols pollués', {
	etat: 'panne',
	ton: 'info',
	verdict: 'Source momentanément indisponible : réessayez plus tard.',
	motif: 'amont_indisponible',
	accroche: 'Source indisponible'
});

const point = (t: { id: string; titre: string; verdict: string; granularite: unknown }) => ({ theme: t.id, titre: t.titre, verdict: t.verdict, granularite: t.granularite });
const SECTION = { vigilance: 0, pannes: 0, lus: 0, total: 0 };

function donnees(sols: ReturnType<typeof theme> & { etat?: unknown }) {
	const etat = (sols as { etat: string }).etat;
	return {
		adresse: {
			ban_id: ID,
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			numero: '12',
			nom_voie: 'Rue des Lilas',
			code_postal: '85000',
			commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
			departement: { code: '85', nom: 'Vendée' },
			parcelle: { etat: 'unique', id_parcelle: '85191000AB0012' }
		},
		couverture: { etat: 'heberge', motif: null },
		date: '2026-11-10',
		themes: [PRIX, sols],
		lectures_en_cours: etat === 'en_cours' ? ['pollution_sols'] : [],
		resume: {
			points_adresse: [],
			points_commune: [],
			rassurants: [],
			rassurants_adresse: etat === 'zero' ? [point(sols as never)] : [],
			rassurants_commune: [],
			pannes: etat === 'panne' ? [point(sols as never)] : [],
			sources_en_panne: etat === 'panne' ? ['errial'] : [],
			non_concernes: [],
			partiels: [],
			sans_valeur: [],
			en_cours: etat === 'en_cours' ? [point(sols as never)] : [],
			lus: etat === 'zero' ? 2 : 1,
			lus_adresse: etat === 'zero' ? 1 : 0,
			lus_secteur: 1,
			total_criteres: 2,
			par_section: { batiment: SECTION, risques: { ...SECTION, total: 1 }, marche: { ...SECTION, lus: 1, total: 1 }, vivre: SECTION },
			chiffres_cles: []
		}
	};
}

const reponseApi = (sols: ReturnType<typeof theme>) => ({ statut: 'ok', donnees: donnees(sols), meta: { source: [] } });

function issueDe(sols: ReturnType<typeof theme>): IssueRapportAdresse {
	const d = donnees(sols);
	return {
		kind: 'ok',
		fiche: {
			identite: { etat: 'etablie', ...d.adresse, rep: null, parcelle: { etat: 'unique', id_parcelle: '85191000AB0012' } },
			couverture: { etat: 'heberge', motif: null }
		} as never,
		rapport: projeterRapport(d),
		source: null
	};
}

let numeroSession = 0;
function bocal() {
	numeroSession += 1;
	const session = `${'cd'.repeat(12)}${String(numeroSession).padStart(8, '0')}`;
	const jar = new Map<string, string>([[QUOTA_COOKIE, session]]);
	return { get: (name: string) => jar.get(name), set: (name: string, value: string) => void jar.set(name, value) } as unknown as Cookies;
}

function evenement(chemin: string, cookies: Cookies) {
	const url = new URL(`https://site.invalid${chemin}`);
	return {
		params: { id: ID },
		url,
		request: new Request(url, { headers: { 'user-agent': 'Mozilla/5.0 Firefox/140.0' } }),
		platform: undefined,
		cookies,
		getClientAddress: () => '192.0.2.1',
		setHeaders: vi.fn(),
		depends: vi.fn()
	};
}

/** The no-break spaces of the French punctuation, read as plain spaces. */
const plat = (s: string | null | undefined): string => (s ?? '').replace(/ /g, ' ');

const json = (corps: unknown, status = 200) => new Response(JSON.stringify(corps), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => {
	vi.unstubAllGlobals();
	vi.clearAllMocks();
	(page as unknown as Writable<unknown>).set({ status: 200, error: null, url: new URL('https://site.invalid/adresse/85191_0420_00012') });
});

describe('R-07 — the served state', () => {
	it('« en_cours » is kept as such: never a gap, never an outage, no figure, the tone info', () => {
		const rapport = projeterRapport(donnees(SOLS_EN_COURS));
		const sols = rapport.themes[1];
		expect(sols.etat).toBe('en_cours');
		expect(sols.ton).toBe('info');
		expect(lecturesEnCours(rapport)).toEqual(['pollution_sols']);
		expect(statutDe(sols)).toEqual({ cle: 'en_cours', texte: 'Lecture en cours' });
		expect(LIBELLE_STATUT.en_cours).not.toBe(LIBELLE_STATUT.panne);
		expect(lecturesEnCours(projeterRapport(donnees(SOLS_LUS)))).toEqual([]);
	});

	it('a read in progress is never a bare « rien à signaler »', () => {
		const rapport = projeterRapport(donnees(SOLS_EN_COURS));
		const resume = lireResume(rapport.resume, rapport.themes)!;
		expect(resume.enCours?.map((t) => t.id)).toEqual(['pollution_sols']);
		expect(phraseEnBref(resume).map((s) => s.texte).join('')).toBe('Rien à signaler parmi les critères lus.');
	});

	it('the mention counts the criteria that wait, then says how the reads ended', () => {
		expect(mentionLectures({ attendues: 1, restantes: 1, pannes: 0, interrompue: false })).toBe('1 critère attend encore la réponse de sa source.');
		expect(mentionLectures({ attendues: 3, restantes: 3, pannes: 0, interrompue: false })).toBe('3 critères attendent encore la réponse de leur source.');
		expect(mentionLectures({ attendues: 3, restantes: 0, pannes: 0, interrompue: false })).toBe('Toutes les sources ont répondu.');
		expect(plat(mentionLectures({ attendues: 3, restantes: 0, pannes: 1, interrompue: false }))).toBe('Lecture terminée : 1 critère reste sans réponse de sa source.');
		expect(plat(mentionLectures({ attendues: 3, restantes: 0, pannes: 2, interrompue: false }))).toBe('Lecture terminée : 2 critères restent sans réponse de leur source.');
		expect(plat(mentionLectures({ attendues: 3, restantes: 3, pannes: 0, interrompue: true }))).toBe('La lecture continue : rechargez la fiche dans quelques secondes.');
		expect(mentionLectures({ attendues: 0, restantes: 0, pannes: 0, interrompue: false })).toBeNull();
	});
});

describe('R-07 — the loaders', () => {
	it('the fiche asks for the short wait, its follow-up for the complete one, and the fiche is counted once', async () => {
		const appels: string[] = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (input: RequestInfo | URL) => {
				const url = new URL(String(input), 'http://o.invalid');
				appels.push(`${url.pathname}${url.search}`);
				return json(reponseApi(url.searchParams.get('attente') === 'courte' ? SOLS_EN_COURS : SOLS_LUS));
			})
		);
		const cookies = bocal();
		const premiere = (await (loadIdentite as unknown as (e: unknown) => Promise<{ issue: IssueRapportAdresse }>)(evenement(`/adresse/${ID}`, cookies))).issue;
		expect(premiere.kind === 'ok' && lecturesEnCours(premiere.rapport)).toEqual(['pollution_sols']);

		const e = evenement(`/adresse/${ID}/suite`, cookies);
		const reponse = await (suite as unknown as (e: unknown) => Promise<Response>)(e);
		expect(reponse.status).toBe(200);
		// Never indexed, never stored: a partial or a private answer stays out of every cache.
		expect(e.setHeaders).toHaveBeenCalledWith({ 'x-robots-tag': 'noindex, nofollow', 'cache-control': 'private, no-store' });
		const { issue } = (await reponse.json()) as { issue: IssueRapportAdresse };
		expect(issue.kind === 'ok' && issue.rapport.themes[1].etat).toBe('zero');
		expect(appels).toEqual([`/v1/adresse/${ID}/rapport?attente=courte`, `/v1/adresse/${ID}/rapport?attente=complete`]);

		// The allowance counted one fiche: the follow-up gives nothing back because it counted nothing.
		const admission = await admettreFiche({ cookies, request: new Request('https://site.invalid/'), getClientAddress: () => '192.0.2.1' }, ID);
		expect(admission).toEqual({ admise: true });
		const autre = await admettreFiche({ cookies, request: new Request('https://site.invalid/'), getClientAddress: () => '192.0.2.1' }, '44109_0001_00001');
		expect(autre.admise && typeof autre.rendre).toBe('function');
	});

	it('the follow-up passes the gate of the fiche: a malformed key is unknown, nothing read', async () => {
		const fetchStub = vi.fn();
		vi.stubGlobal('fetch', fetchStub);
		const e = { ...evenement('/adresse/xyz/suite', bocal()), params: { id: 'xyz' } };
		await expect((suite as unknown as (e: unknown) => Promise<Response>)(e)).rejects.toMatchObject({ status: 404 });
		expect(fetchStub).not.toHaveBeenCalled();
	});

	it('a follow-up the API does not serve is an outage of the follow-up (503), never a report', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'x' }, 500)));
		await expect((suite as unknown as (e: unknown) => Promise<Response>)(evenement(`/adresse/${ID}/suite`, bocal()))).rejects.toMatchObject({ status: 503 });
	});
});

describe('R-07 — the page', () => {
	const servie = (sols: ReturnType<typeof theme>) => ({ issue: issueDe(sols), documents: null });

	it('says « Lecture en cours », not « Source indisponible », then shows the value without a reload', async () => {
		let repondre: (r: Response) => void = () => undefined;
		const fetchStub = vi.fn(() => new Promise<Response>((resolve) => (repondre = resolve)));
		vi.stubGlobal('fetch', fetchStub);
		const { container, getByRole } = render(Page, { props: { data: servie(SOLS_EN_COURS) } });

		const ligne = container.querySelector('#critere-pollution_sols')!;
		expect(ligne.getAttribute('data-etat')).toBe('en_cours');
		expect(ligne.textContent).toContain('Lecture en cours');
		// On the row: the legend « Lire une fiche » of the page names every state, the outage included.
		expect(ligne.textContent).not.toContain('Source indisponible');
		// One polite live region, in the served markup; the reload link is the way without JavaScript.
		const mention = getByRole('status');
		expect(mention.textContent).toContain('1 critère attend encore la réponse de sa source.');
		const lien = mention.querySelector('a')!;
		expect(lien.textContent).toBe('Recharger la fiche');
		expect(lien.getAttribute('href')).toBe(`/adresse/${ID}`);

		await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
		expect(String((fetchStub.mock.calls[0] as unknown[])[0])).toBe(`/adresse/${ID}/suite`);
		repondre(json({ issue: issueDe(SOLS_LUS) }));

		await waitFor(() => expect(container.querySelector('#critere-pollution_sols')!.getAttribute('data-etat')).toBe('zero'));
		const arrivee = container.querySelector('#critere-pollution_sols')!;
		expect(arrivee.textContent).toContain('Aucun site pollué recensé sur la parcelle.');
		expect(arrivee.textContent).not.toContain('Lecture en cours');
		// The region stays where it was and says the end; no reload was asked.
		expect(getByRole('status').textContent).toContain('Toutes les sources ont répondu.');
		expect(getByRole('status').querySelector('a')).toBeNull();
		expect(invalidateAll).not.toHaveBeenCalled();
		expect(fetchStub).toHaveBeenCalledTimes(1);
	});

	it('a source that never answered is an outage at the end, and the mention says so', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => json({ issue: issueDe(SOLS_EN_PANNE) })));
		const { container, getByRole } = render(Page, { props: { data: servie(SOLS_EN_COURS) } });
		await waitFor(() => expect(container.querySelector('#critere-pollution_sols')!.getAttribute('data-etat')).toBe('panne'));
		expect(container.querySelector('#critere-pollution_sols')!.textContent).toContain('Source indisponible');
		expect(plat(getByRole('status').textContent)).toContain('Lecture terminée : 1 critère reste sans réponse de sa source.');
	});

	it('a follow-up that fails leaves the rows as they are, says so once and never loops', async () => {
		const fetchStub = vi.fn(async () => json({ message: 'x' }, 503));
		vi.stubGlobal('fetch', fetchStub);
		const { container, getByRole } = render(Page, { props: { data: servie(SOLS_EN_COURS) } });
		await waitFor(() => expect(plat(getByRole('status').textContent)).toContain('La lecture continue : rechargez la fiche dans quelques secondes.'));
		expect(container.querySelector('#critere-pollution_sols')!.getAttribute('data-etat')).toBe('en_cours');
		expect(getByRole('status').querySelector('a')!.getAttribute('href')).toBe(`/adresse/${ID}`);
		expect(fetchStub).toHaveBeenCalledTimes(1);
		expect(invalidateAll).not.toHaveBeenCalled();
	});

	it('witness: a complete report asks for no follow-up and shows no mention', async () => {
		const fetchStub = vi.fn();
		vi.stubGlobal('fetch', fetchStub);
		const { queryByRole, container } = render(Page, { props: { data: servie(SOLS_LUS) } });
		await Promise.resolve();
		expect(container.querySelector('#critere-pollution_sols')!.getAttribute('data-etat')).toBe('zero');
		expect(queryByRole('status')).toBeNull();
		expect(fetchStub).not.toHaveBeenCalled();
	});
});
