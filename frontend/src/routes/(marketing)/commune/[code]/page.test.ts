// A failed synthesis call is typed on the commune page (WR-01, D-15 review of 03-16): a shared-key
// rate limit or a refused access is not an outage of the service, and the notice says so with a
// constant of the client, never the server body.
import { fireEvent, render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	MESSAGE_ACCES_REFUSE,
	messageLimite,
	TITRE_ACCES_REFUSE,
	TITRE_DEMANDE_REFUSEE,
	TITRE_LIMITE
} from '$lib/data/panne';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://site.invalid/commune/85191?theme=risques-urbanisme'), state: {} })
}));

import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { lectureCommune } from '$lib/data/lecture';
import { mapCommuneToGrid, themesCommune } from '$lib/data/commune-grid';
import { categoriesCommune } from '$lib/data/commune-categories';
import { COMMUNE_META_SOURCE_NOMS, COMMUNE_META_SOURCE_ORDER, getExplication } from '$lib/data/explications';
import { communeKpis, reperesConnus, reservesSynthese, TENDANCE_NON_PUBLIEE } from '$lib/data/commune-kpi';
import { COMMUNE_85047_CHALLANS } from '$lib/data/__fixtures__/commune-85';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { COMMUNE_DPE_15 } from '$lib/data/__fixtures__/commune-indicateurs-dpe-15';
import { faqCommune } from '$lib/data/commune-faq';
import { load, type FicheCommuneData } from './+page';
import Page from './+page.svelte';
import { navigating } from '$app/stores';
import type { Writable } from 'svelte/store';
import { sansDeveloppement } from '$lib/utils/sigles';

const navigatingStore = navigating as unknown as Writable<unknown>;

const GENERIQUE = "Le service n'a pas répondu";

function reponse(status: number, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify({ detail: 'texte serveur' }), {
		status,
		headers: { 'Content-Type': 'application/json', ...headers }
	});
}

async function charger(fetch: typeof globalThis.fetch): Promise<FicheCommuneData> {
	return (await (
		load as unknown as (event: { params: { code: string }; fetch: typeof fetch }) => Promise<FicheCommuneData>
	)({ params: { code: '44109' }, fetch })) as FicheCommuneData;
}

afterEach(() => vi.restoreAllMocks());

describe('/commune/[code] — an answer without data nor reason (03-17 F1)', () => {
	it('is titled as an unread page, never with the served sub-state « Non renseigné »', () => {
		const data = { etat: 'non-renseignee', code: '44109', identite: { nom: 'Nantes', departement: '44', codePostal: '44000' } } as FicheCommuneData;
		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain("Les données de cette commune n'ont pas pu être lues");
		expect(alerte.textContent).toContain('Le service a répondu sans données ni motif pour cette commune.');
		expect(alerte.textContent).not.toContain('Non renseigné');
		expect(alerte.querySelector('a[href="/datasets"]')?.textContent).toBe('Voir la couverture des données');
		expect(alerte.querySelector('a[href="/"]')?.textContent).toBe('Revenir à la recherche');
	});
});

describe('/commune/[code] — a failed synthesis is typed (WR-01)', () => {
	it('a shared-key rate limit (429 with Retry-After) renders the rate-limit sentence, never the generic outage', async () => {
		const fetch = vi.fn(async () => reponse(429, { 'Retry-After': '60' }));
		const data = await charger(fetch);
		expect(data.etat).toBe('panne');
		if (data.etat !== 'panne') return;
		expect(data.detail).toEqual({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(60) });

		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain(TITRE_LIMITE);
		expect(alerte.textContent).toContain(messageLimite(60));
		expect(alerte.textContent).not.toContain('indisponible');
		expect(alerte.textContent).not.toContain(GENERIQUE);
		expect(alerte.textContent).not.toContain('texte serveur');
	});

	it('a refused access (401) renders the refused-access sentence', async () => {
		const fetch = vi.fn(async () => reponse(401));
		const data = await charger(fetch);
		expect(data.etat).toBe('panne');
		if (data.etat !== 'panne') return;
		expect(data.detail).toEqual({ statut: 401, titre: TITRE_ACCES_REFUSE, message: MESSAGE_ACCES_REFUSE });

		const { getByRole } = render(Page, { props: { data } });
		expect(getByRole('alert').textContent).toContain(TITRE_ACCES_REFUSE);
		expect(getByRole('alert').textContent).toContain(MESSAGE_ACCES_REFUSE);
		expect(getByRole('alert').textContent).not.toContain(GENERIQUE);
	});

	it('a refused request (409) is titled by the client and carries its status (03-17 B C1)', async () => {
		const fetch = vi.fn(async () => reponse(409));
		const data = await charger(fetch);
		expect(data.etat).toBe('panne');
		if (data.etat !== 'panne') return;

		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain(TITRE_DEMANDE_REFUSEE);
		expect(alerte.textContent).toContain('erreur 409');
		expect(alerte.textContent).not.toContain(GENERIQUE);
		expect(alerte.textContent).not.toContain('texte serveur');
	});

	it('a 5xx keeps the generic outage title and text', async () => {
		const fetch = vi.fn(async () => reponse(503));
		const data = await charger(fetch);
		expect(data.etat).toBe('panne');
		if (data.etat !== 'panne') return;
		expect(data.detail).toEqual({ statut: 503, titre: null, message: null });

		const { getByRole } = render(Page, { props: { data } });
		expect(getByRole('alert').textContent).toContain(GENERIQUE);
		expect(getByRole('alert').textContent).toContain('Les données de cette commune sont momentanément indisponibles');
	});
});

// 04.7-10, decision 11: the load hands the event's setHeaders to the reading, so that an outage is
// answered 503 by hooks.server.ts (the page itself stays rendered and readable).
describe('/commune/[code] — the load marks an outage for the 503 (04.7-10)', () => {
	type Charge = (event: { params: { code: string }; fetch: typeof fetch; setHeaders: (h: Record<string, string>) => void }) => Promise<FicheCommuneData>;

	it('posts the marker and the served Retry-After through the setHeaders of the event', async () => {
		const setHeaders = vi.fn();
		const fetch = vi.fn(async () => reponse(429, { 'Retry-After': '60' }));
		const data = await (load as unknown as Charge)({ params: { code: '44109' }, fetch, setHeaders });
		expect(data.etat).toBe('panne');
		expect(setHeaders).toHaveBeenCalledTimes(1);
		expect(setHeaders).toHaveBeenCalledWith({ 'x-dc-etat-page': 'panne', 'x-dc-retry-after': '60' });
	});

	it('posts nothing when the fiche is served', async () => {
		const setHeaders = vi.fn();
		const fetch = vi.fn(
			async () =>
				new Response(JSON.stringify({ statut: 'ok', donnees: [COMMUNE_85047_CHALLANS], meta: { source: [] } }), {
					status: 200,
					headers: { 'Content-Type': 'application/json' }
				})
		);
		const data = await (load as unknown as Charge)({ params: { code: '85047' }, fetch, setHeaders });
		expect(data.etat).toBe('fiche');
		expect(setHeaders).not.toHaveBeenCalled();
	});
});

// The categories of the address fiche (4.9): the rows of the five themes of the reading projection, each
// once, in « Prix et ventes », « Logement et DPE », « Vivre dans la commune », in `dl.tab`, each group in the
// served order of its theme. The five themes stay those of the CSV export (D-35).
describe('/commune/[code] — the categories of the address fiche (4.9)', () => {
	it('renders the categories in the order of the address fiche, every row of the reading projection once', () => {
		const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
		const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
		const data: FicheCommuneData = { etat: 'fiche', code: '85191', synthese, sources, consulteLe: '24 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		expect([...container.querySelectorAll('details.cat')].map((d) => d.id)).toEqual(['q-prix', 'q-logement', 'q-quotidien']);
		expect([...container.querySelectorAll('details.cat .cat-titre')].map((h) => h.textContent)).toEqual(['Prix et ventes', 'Logement et DPE', 'Vivre dans la commune']);
		const attendus = lectureCommune(synthese, sources).filter((groupe) => groupe.cle !== 'essentiel').flatMap((groupe) => groupe.lignes.map((ligne) => ligne.id));
		const rendues = [...container.querySelectorAll<HTMLElement>('details.cat dl.tab > .tab-l')];
		expect(rendues.map((l) => l.getAttribute('data-ligne')).sort()).toEqual([...attendus].sort());
		expect(rendues.every((ligne) => ligne.hasAttribute('data-field-id'))).toBe(true);
		expect(container.querySelector('section.panneau-simple#immobilier')).toBeNull();
		expect(container.textContent).not.toContain('Afficher tout');
	});

	it('keeps the order of the categories module on another answer (Challans, without sources)', () => {
		const synthese = COMMUNE_85047_CHALLANS;
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese, sources: null, consulteLe: '24 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		for (const categorie of categoriesCommune(themesCommune(synthese, null))) {
			for (const groupe of categorie.groupes) {
				const ids = [...container.querySelectorAll(`details.cat#${categorie.id} #critere-${groupe.id} dl.tab > .tab-l`)].map((l) => l.getAttribute('data-ligne'));
				expect(ids).toEqual(groupe.lignes.map((l) => l.id));
			}
		}
	});

	it('keeps each value with its field id, its served state and its unit (règle zéro, D45-01)', () => {
		const synthese = COMMUNE_85047_CHALLANS;
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese, sources: null, consulteLe: '24 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		for (const ligne of themesCommune(synthese, null).flatMap((theme) => theme.lignes)) {
			const rendus = container.querySelectorAll(`section.lecture [data-ligne="${ligne.id}"]`);
			expect(rendus).toHaveLength(1);
			const rendu = rendus[0];
			expect(rendu.getAttribute('data-field-id')).toBe(ligne.fieldId ?? null);
			if (ligne.etat.kind === 'valeur') {
				expect(rendu.querySelector('[data-state="value"] .value')?.textContent).toBe(ligne.etat.texte);
				if (ligne.etat.unite) expect(rendu.querySelector('[data-state="value"] .unit')?.textContent?.trim()).toBe(ligne.etat.unite);
			} else {
				expect(rendu.querySelector('[data-state="value"]')).toBeNull();
				expect(rendu.querySelector('.state-absence')?.getAttribute('data-state')).toBe(ligne.etat.etat);
			}
		}
	});
});

// Rules of a theme row, carried over from the folded theme (ThemeSection, MetricRow, retired by
// 04.8-09, D-20): the definition comes from the contract, the vintage from the commune's own column, a
// declared absence stays visible with its glossary words, an entry the API does not serve is omitted.
describe('/commune/[code] — the rows of a theme keep their rules (03-17, D-47)', () => {
	const CHALLANS = {
		...COMMUNE_85047_CHALLANS,
		prix_m2_median_maison: 2480,
		nb_mutations_12m_maison: 268,
		prix_m2_median_appartement: null,
		nb_mutations_12m_appartement: 3,
		etats: {
			loyer_m2_appartement: { etat: 'not_available', motif: 'Loyer d’appartement non publié pour cette commune.' },
			prix_m2_median_appartement: { etat: 'insufficient_sample', motif: '3 ventes d’appartements sur 12 mois, sous le seuil de publication de 5.' }
		}
	} as CommuneSynthese;

	function page(sources: SourceAttribution[] | null = null): HTMLElement {
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese: CHALLANS, sources, consulteLe: '24 octobre 2026' };
		return render(Page, { props: { data } }).container;
	}

	const entreeDuVolet = (ligne: Element, terme: string) => [...ligne.querySelectorAll('.source-panel-entry')]
		.find((node) => node.querySelector('dt')?.textContent === terme)?.querySelector('dd')?.textContent ?? null;

	it('renders each row with its label, value or declared state, and its Source button without title', () => {
		const panneau = page().querySelector('section.lecture')!;
		const prix = panneau.querySelector('[data-field-id="dvf.prix_m2_median_appartement"]')!;
		expect(prix.querySelector('dt')?.textContent).toContain('Prix médian');
		expect(prix.querySelector('.state-badge')?.textContent).toBe('Effectif insuffisant');
		expect(prix.querySelector('.state-value')).toBeNull();
		const vacance = panneau.querySelector('[data-field-id="vacance.taux_vacance"]')!;
		expect(vacance.querySelector('.state-value .value')?.textContent).toBe('9,4');
		expect(vacance.querySelector('.state-value .unit')?.textContent?.trim()).toBe('%');
		expect(entreeDuVolet(vacance, 'Objet')).toBe('Parc privé communal');
		// Object and caveats are read in the panel, not under the value (03-17 R-1).
		const horsVolet = vacance.querySelector('dd')!.cloneNode(true) as Element;
		horsVolet.querySelectorAll('dialog').forEach((dialog) => dialog.remove());
		expect(horsVolet.textContent).not.toContain('Parc privé communal');
		expect(entreeDuVolet(vacance, 'Limites')).toContain('parc privé');
		const boutons = panneau.querySelectorAll('.source-button');
		expect(boutons.length).toBeGreaterThan(0);
		for (const bouton of boutons) {
			expect(bouton.getAttribute('aria-label')).toMatch(/^Détail : /);
			expect(bouton.hasAttribute('title')).toBe(false);
		}
	});

	it('opens the source panel of a row on its public definition, never a text written here (03-17 F66)', async () => {
		const revenu = page().querySelector<HTMLElement>('#q-quotidien [data-field-id="revenus.revenu_median"]')!;
		await fireEvent.click(revenu.querySelector('.source-button')!);
		expect(revenu.querySelector('dialog.source-panel')?.hasAttribute('open')).toBe(true);
		expect(entreeDuVolet(revenu, 'Définition')).toBe(getExplication('revenus.revenu_median').definition);
		expect(entreeDuVolet(revenu, 'Période')).toBe('Millésime 2021');
		expect(revenu.querySelector('.tab-note-texte')?.textContent).toBe('Millésime 2021');
	});

	it('keeps a declared absence visible with the glossary label, its sentence one click away (A2)', () => {
		const loyer = page().querySelector('#q-prix [data-field-id="loyers.loyer_m2_appartement"]')!;
		expect(loyer.querySelector('.tab-v .state-badge')?.textContent).toBe('Non publiée');
		expect(loyer.querySelector('.tab-v .state-help')).toBeNull();
		expect(loyer.querySelector('dialog .state-help')?.textContent).toBe('La source ne publie pas cette valeur pour ce territoire.');
		expect(loyer.textContent).not.toMatch(/non disponible|not_available/);
	});

	it('keeps the sentence of « Hors couverture » on the row (A2)', () => {
		const synthese = {
			...CHALLANS,
			taux_vacance: null,
			etats: { ...CHALLANS.etats, taux_vacance: { etat: 'out_of_coverage', motif: 'dataset_non_heberge' } }
		} as CommuneSynthese;
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese, sources: null, consulteLe: '24 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		const ligne = container.querySelector('[data-field-id="vacance.taux_vacance"]')!;
		expect(ligne.querySelector('.tab-v .state-help')?.textContent).toBe('Ce site ne propose pas ce jeu de données.');
	});

	it('the period qualifies a served value only', () => {
		const absente = themesCommune(CHALLANS).flatMap((theme) => theme.lignes).find((ligne) => ligne.etat.kind !== 'valeur' && ligne.periode);
		expect(absente, 'the fixture holds an absent row with a period').toBeDefined();
		const rendue = page().querySelector(`section.lecture [data-ligne="${absente!.id}"]`)!;
		expect(rendue.querySelector('.tab-note-texte')).toBeNull();
	});

	const revenu = (container: Element) => container.querySelector('#q-quotidien [data-field-id="revenus.revenu_median"]')!;

	it('omits a source the response does not serve (03-17 R-1)', () => {
		const ligne = revenu(page());
		expect(entreeDuVolet(ligne, 'Source')).toBeNull();
		expect(ligne.querySelector('.tab-note-source')).toBeNull();
	});

	it('names a served source by its dataset in the panel (03-17 R-1)', () => {
		const sources = COMMUNE_META_SOURCE_ORDER.map((dataset) => ({ nom: COMMUNE_META_SOURCE_NOMS[dataset] })) as SourceAttribution[];
		const ligne = revenu(page(sources));
		expect(entreeDuVolet(ligne, 'Source')).toBe(COMMUNE_META_SOURCE_NOMS.revenus);
		// One explanation per row: the name of the source is not repeated beside its button (lot 3).
		const horsVolet = ligne.querySelector('dd')!.cloneNode(true) as Element;
		horsVolet.querySelectorAll('dialog').forEach((dialog) => dialog.remove());
		expect(horsVolet.textContent).not.toContain(COMMUNE_META_SOURCE_NOMS.revenus);
		expect(horsVolet.querySelectorAll('button.source-button')).toHaveLength(1);
	});
});

// « L'essentiel » (04.8-04, UI-SPEC v4 § 6.2; 4.9, lot 3): the scale mark Commune and its sentence, then
// the markers known for this commune as tiles in the order served; definition and source in the panel.
describe('/commune/[code] — L’essentiel en tuiles (UI-SPEC v4 § 6.2)', () => {
	const fiche = (synthese: CommuneSynthese, sources: SourceAttribution[] | null = null, code = '85047'): FicheCommuneData => ({ etat: 'fiche', code, synthese, sources, consulteLe: '31 octobre 2026' });

	it('opens with the scale mark Commune and the sentence of the page', () => {
		const { container } = render(Page, { props: { data: fiche(COMMUNE_85047_CHALLANS) } });
		const essentiel = container.querySelector('section.panneau-essentiel')!;
		expect(essentiel.querySelector('h2')?.textContent).toBe('L’essentiel');
		const chapo = essentiel.querySelector('.essentiel-chapo')!;
		expect(chapo.querySelector('.ech svg')).not.toBeNull();
		expect(chapo.querySelector('.ech-mot')?.textContent).toBe('Commune');
		expect(chapo.textContent).toContain('Toutes les valeurs de cette page valent pour la commune entière, jamais pour une adresse.');
		expect(chapo.textContent!.indexOf('Commune')).toBeLessThan(chapo.textContent!.indexOf('Toutes les valeurs'));
	});

	it('renders the known markers as tiles in the served order, one button each and no note', () => {
		const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
		const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
		const { container } = render(Page, { props: { data: fiche(synthese, sources, '85191') } });
		const kpis = reperesConnus(communeKpis(synthese, sources));
		expect(kpis.length).toBeGreaterThan(0);
		const tuiles = [...container.querySelectorAll('section.panneau-essentiel dl.cles > div.cle')];
		expect(tuiles.map((t) => t.getAttribute('data-kpi'))).toEqual(kpis.map((k) => k.id));
		for (const [index, kpi] of kpis.entries()) {
			// 04.8-08 (D-31): a label may carry the full form of its acronym, said once on the page.
			expect(sansDeveloppement(tuiles[index].querySelector('dt')?.textContent ?? '')).toBe(kpi.libelle);
			expect(tuiles[index].querySelector('.cle-note')).toBeNull();
			expect(tuiles[index].querySelectorAll('button.source-button')).toHaveLength(1);
		}
	});

	it('shows no state word in « L’essentiel »: an absence stays in its theme', () => {
		const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
		const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
		const { container } = render(Page, { props: { data: fiche(synthese, sources, '85191') } });
		const essentiel = container.querySelector('section.panneau-essentiel')!;
		expect(essentiel.querySelectorAll('.state-badge, .state-absence')).toHaveLength(0);
		expect(essentiel.querySelector('[data-kpi="tendance"]')).toBeNull();
		const ligne = [...container.querySelectorAll('#q-prix .tab-l')].find((item) => item.querySelector('dt')?.textContent === 'Tendance du prix médian')!;
		expect(ligne.querySelector('.tab-v .state-badge')?.textContent).toBe('Non publiée');
		expect(ligne.querySelector('dialog')?.textContent).toContain(TENDANCE_NON_PUBLIEE);
	});

	it('says so when no marker is known, without an empty list', () => {
		const synthese = {
			...COMMUNE_85047_CHALLANS,
			indicateurs_dvf: { etat: 'not_qualified', motif: 'commune_hors_build_qualifie' },
			indicateurs_dpe: { etat: 'not_qualified', motif: 'commune_hors_build_qualifie' }
		} as unknown as CommuneSynthese;
		const { container } = render(Page, { props: { data: fiche(synthese) } });
		const essentiel = container.querySelector('section.panneau-essentiel')!;
		expect(essentiel.querySelector('dl.cles')).toBeNull();
		expect(essentiel.querySelector('.essentiel-vide')?.textContent).toBe('Prix et DPE sont sans valeur ici : chaque thème ci-dessous dit pourquoi.');
	});

	it('writes the definitions for paper, hidden on screen', () => {
		const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
		const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
		const { container } = render(Page, { props: { data: fiche(synthese, sources, '85191') } });
		const bloc = container.querySelector('#definitions-impression')!;
		const premier = reperesConnus(communeKpis(synthese, sources))[0];
		expect(bloc.querySelector('h3')?.textContent).toBe('Définitions');
		expect(bloc.textContent).toContain(premier.definition);
		expect(bloc.textContent).toContain(TENDANCE_NON_PUBLIEE);
	});

	it('names an arrondissement as such in the sentence of the page', () => {
		const synthese = { ...COMMUNE_85047_CHALLANS, code_insee: '75112', nom_commune: 'Paris 12e Arrondissement', code_departement: '75', type_zone: 'arrondissement', commune_parent: '75056' } as CommuneSynthese;
		const data = { ...fiche(synthese, null, '75112'), parent: { code: '75056', nom: 'Paris' } } as FicheCommuneData;
		const { container } = render(Page, { props: { data } });
		const chapo = container.querySelector('.essentiel-chapo')!;
		expect(chapo.querySelector('.ech-mot')?.textContent).toBe('Arrondissement');
		expect(chapo.textContent).toContain('valent pour l’arrondissement entier, jamais pour une adresse.');
	});

	it('lists the caveats as anchor links (↓) to their marker; no caveat, no panel', () => {
		const sources = [...COMMUNE_85191_SOURCES] as SourceAttribution[];
		const synthese = COMMUNE_85191_OBSERVED as CommuneSynthese;
		const reserves = reservesSynthese(synthese, sources, mapCommuneToGrid(synthese, sources));
		const { container } = render(Page, { props: { data: fiche(synthese, sources, '85191') } });
		const panneau = [...container.querySelectorAll('section.panneau')].find((s) => s.querySelector('h2')?.textContent === 'Ce qui mérite une lecture attentive');
		if (!reserves.length) {
			expect(panneau).toBeUndefined();
			return;
		}
		const liens = [...panneau!.querySelectorAll('ul.liste-liens a')];
		expect(liens.map((a) => a.getAttribute('href'))).toEqual(reserves.map((r) => r.ancre));
		expect(liens.map((a) => a.querySelector('.lien-n')?.textContent)).toEqual(reserves.map((r) => r.texte));
		expect(liens.every((a) => a.querySelector('.fleche')?.textContent === '↓')).toBe(true);
		for (const reserve of reserves) expect(container.querySelector(reserve.ancre)).not.toBeNull();
	});

	it('a commune without caveat has no caveat panel', () => {
		const { container } = render(Page, { props: { data: fiche(COMMUNE_85047_CHALLANS) } });
		const reserves = reservesSynthese(COMMUNE_85047_CHALLANS, null, mapCommuneToGrid(COMMUNE_85047_CHALLANS, null));
		const titres = [...container.querySelectorAll('section.panneau h2')].map((h) => h.textContent);
		expect(titres.includes('Ce qui mérite une lecture attentive')).toBe(reserves.length > 0);
	});
});

// Loading and outage (UI Considerations): skeleton tiles and empty panels while another commune loads,
// never the previous figures; the state panel on an outage.
describe('/commune/[code] — chargement et panne (04.8-04)', () => {
	afterEach(() => navigatingStore.set(null));

	it('shows busy skeleton tiles and panels, without any figure, while another commune loads', () => {
		navigatingStore.set({ to: { route: { id: '/(marketing)/commune/[code]' }, params: { code: '44109' } } });
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese: COMMUNE_85047_CHALLANS, sources: null, consulteLe: '31 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		const busy = container.querySelector('[aria-busy="true"]')!;
		expect(busy).not.toBeNull();
		expect(busy.querySelectorAll('[data-squelette] .cle-squelette')).toHaveLength(4);
		expect(busy.querySelectorAll('.panneau-squelette')).toHaveLength(3);
		expect(container.querySelector('[data-state="value"]')).toBeNull();
		expect(container.querySelector('dl.cles')).toBeNull();
	});
});

// 04.7-15 T2, T4: the served DPE distribution under the markers, and one computed FAQPage whose
// JSON-LD is the visible text, word for word, on the « fiche » state only.
describe('/commune/[code] — répartition des DPE et FAQ calculée (04.7-15)', () => {
	const fiche = (synthese: CommuneSynthese): FicheCommuneData => ({ etat: 'fiche', code: '85047', synthese, sources: null, consulteLe: '31 octobre 2026' });
	const faqPages = () =>
		[...document.head.querySelectorAll('script[type="application/ld+json"]')]
			.map((script) => JSON.parse(script.textContent ?? '{}'))
			.filter((objet) => objet['@type'] === 'FAQPage');

	afterEach(() => document.head.innerHTML = '');

	it('renders the chart with its letters and counts, and its reading sentence', () => {
		const { container } = render(Page, { props: { data: fiche(COMMUNE_DPE_15) } });
		const figure = container.querySelector('[data-repartition-dpe]')!;
		const lignes = [...figure.querySelectorAll('[data-classe]')];
		expect(lignes.map((li) => li.getAttribute('data-classe'))).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G']);
		// The letter and the served count are written on each row, the count read with its noun.
		expect(lignes.map((li) => li.querySelector('.repartition-lettre')?.textContent)).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G']);
		expect(lignes.map((li) => li.querySelector('.repartition-effectif')?.textContent)).toEqual(
			['40', '120', '610', '1 071', '1 131', '300', '184'].map((n) => `${n} diagnostics`)
		);
		expect(figure.querySelectorAll('.repartition-barre')).toHaveLength(7);
		for (const barre of figure.querySelectorAll('.repartition-barre')) {
			expect(barre.className).toMatch(/\bdpe-[A-G]\b/);
			expect((barre as HTMLElement).style.background + (barre as HTMLElement).style.backgroundColor).toBe('');
		}
		expect(figure.querySelector('[data-lecture]')?.textContent).toContain('la classe la plus fréquente est E');
	});

	it('no chart without a served distribution', () => {
		const { container } = render(Page, { props: { data: fiche({ ...COMMUNE_DPE_15, indicateurs_dpe: null }) } });
		expect(container.querySelector('[data-repartition-dpe]')).toBeNull();
	});

	it('one FAQPage, its questions and answers being the visible ones', () => {
		const { container } = render(Page, { props: { data: fiche(COMMUNE_DPE_15) } });
		const visibles = [...container.querySelectorAll('[data-faq-question]')].map((item) => [item.querySelector('summary')?.textContent, item.querySelector('p')?.textContent]);
		expect([...container.querySelectorAll('[data-faq-question]')].every((item) => item.matches('details.det'))).toBe(true);
		expect(visibles).toEqual(faqCommune(COMMUNE_DPE_15, null, COMMUNE_DPE_15.nom_commune!).map((item) => [item.question, item.reponse]));
		const pages = faqPages();
		expect(pages).toHaveLength(1);
		expect(pages[0].mainEntity.map((item: { name: string; acceptedAnswer: { text: string } }) => [item.name, item.acceptedAnswer.text])).toEqual(visibles);
	});

	it('the identity line words a served zero population, never « 0 habitants » (55039, SEO 2026-11-14)', () => {
		const vide = { ...COMMUNE_DPE_15, population: 0, annee_demographie: 2022 } as CommuneSynthese;
		const { container } = render(Page, { props: { data: fiche(vide) } });
		const ligne = container.querySelector('.fiche-sous')?.textContent ?? '';
		expect(ligne).toContain('· aucun habitant recensé (2022, INSEE)');
		expect(ligne).not.toMatch(/\b0\s*habitants/);
	});

	it('no FAQ nor FAQPage outside the « fiche » state', () => {
		const data = { etat: 'hors-couverture', code: '44109', identite: { nom: 'Nantes', departement: '44', codePostal: '44000' }, motif: null } as unknown as FicheCommuneData;
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('[data-faq]')).toBeNull();
		expect(faqPages()).toHaveLength(0);
	});
});

// 04.7-09: the fiche of an arrondissement (/commune/75112): its own title, canonical and JSON-LD,
// a breadcrumb through its commune, and a plain commune left exactly as before.
describe('/commune/[code] — arrondissement (04.7-09)', () => {
	const synthese: CommuneSynthese = {
		...COMMUNE_85047_CHALLANS,
		code_insee: '75112',
		nom_commune: 'Paris 12e Arrondissement',
		code_departement: '75',
		type_zone: 'arrondissement',
		commune_parent: '75056'
	};
	const arrondissement: FicheCommuneData = {
		etat: 'fiche',
		code: '75112',
		parent: { code: '75056', nom: 'Paris' },
		synthese,
		sources: null,
		consulteLe: '31 octobre 2026'
	};
	const jsonLd = () =>
		[...document.head.querySelectorAll('script[type="application/ld+json"]')].map((script) => JSON.parse(script.textContent ?? '{}'));

	afterEach(() => (document.head.innerHTML = ''));

	it('is titled « 12e arrondissement de Paris », not the served name', () => {
		const { container } = render(Page, { props: { data: arrondissement } });
		expect(container.querySelector('h1')?.textContent).toBe('12e arrondissement de Paris');
		expect(document.title).toBe('12e arrondissement de Paris (75) : prix immobilier, loyers, DPE et services — DataCommune');
		expect(container.querySelector('.fiche-overline')?.textContent).toBe('Fiche arrondissement');
	});

	it('has the breadcrumb Paris (département) / Paris / 12e arrondissement, each linking its own page, no two links sharing a name', () => {
		const { container } = render(Page, { props: { data: arrondissement } });
		const fil = container.querySelector('nav[aria-label="Fil d\'Ariane"]')!;
		const liens = [...fil.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')]);
		expect(liens).toEqual([
			['Accueil', '/'],
			['Paris (département)', '/departement/75'],
			['Paris', '/commune/75056']
		]);
		// WCAG 2.4.4: two links of the same name must not lead to two targets (Opus P3-4).
		expect(new Set(liens.map(([nom]) => nom)).size).toBe(liens.length);
		expect(fil.querySelector('[aria-current="page"]')?.textContent).toBe('12e arrondissement');
	});

	it('is canonical to itself, never to Paris', () => {
		render(Page, { props: { data: arrondissement } });
		expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')).toMatch(/\/commune\/75112$/);
	});

	it('says in its JSON-LD that the place is contained in the commune, itself contained in the department', () => {
		render(Page, { props: { data: arrondissement } });
		const dataset = jsonLd().find((objet) => objet['@type'] === 'Dataset');
		expect(dataset.name).toBe('Données publiques du 12e arrondissement de Paris');
		expect(dataset.spatialCoverage).toMatchObject({
			'@type': 'Place',
			identifier: '75112',
			containedInPlace: { '@type': 'Place', name: 'Paris', identifier: '75056' }
		});
		expect(dataset.spatialCoverage.containedInPlace.url).toMatch(/\/commune\/75056$/);
		expect(dataset.spatialCoverage.containedInPlace.containedInPlace).toMatchObject({
			'@type': 'AdministrativeArea',
			name: 'Paris',
			identifier: '75'
		});
		expect(dataset.spatialCoverage.containedInPlace.containedInPlace.url).toMatch(/\/departement\/75$/);
	});

	it('titles the unread page of an arrondissement the same way (identity from the referential)', () => {
		const data = {
			etat: 'non-renseignee',
			code: '75112',
			parent: { code: '75056', nom: 'Paris' },
			identite: { nom: 'Paris 12e Arrondissement', departement: '75', codePostal: null }
		} as FicheCommuneData;
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('h1')?.textContent).toBe('12e arrondissement de Paris');
		expect(container.querySelector('nav[aria-label="Fil d\'Ariane"] a[href="/commune/75056"]')).not.toBeNull();
	});

	it('a commune is not an arrondissement: its title and overline are unchanged, its place is contained in its department only', () => {
		const data: FicheCommuneData = { etat: 'fiche', code: '85047', synthese: COMMUNE_85047_CHALLANS, sources: null, consulteLe: '31 octobre 2026' };
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('.fiche-overline')?.textContent).toBe('Fiche commune');
		const lieu = jsonLd().find((objet) => objet['@type'] === 'Dataset').spatialCoverage;
		expect(lieu.containedInPlace['@type']).toBe('AdministrativeArea');
		expect(lieu.containedInPlace.containedInPlace).toBeUndefined();
	});
});

// 04.7-10, decision 9: the breadcrumb goes through the department (Recherche > Vendée > Challans),
// one list read by the visible nav and by the BreadcrumbList, and the Dataset says in which
// department its place lies.
describe('/commune/[code] — fil d’Ariane par le département (04.7-10)', () => {
	const commune: FicheCommuneData = { etat: 'fiche', code: '85047', synthese: COMMUNE_85047_CHALLANS, sources: null, consulteLe: '31 octobre 2026' };
	const arrondissement: FicheCommuneData = {
		etat: 'fiche',
		code: '75112',
		parent: { code: '75056', nom: 'Paris' },
		synthese: {
			...COMMUNE_85047_CHALLANS,
			code_insee: '75112',
			nom_commune: 'Paris 12e Arrondissement',
			code_departement: '75',
			type_zone: 'arrondissement',
			commune_parent: '75056'
		},
		sources: null,
		consulteLe: '31 octobre 2026'
	};
	const jsonLd = () =>
		[...document.head.querySelectorAll('script[type="application/ld+json"]')].map((script) => JSON.parse(script.textContent ?? '{}'));
	const fil = () => jsonLd().filter((objet) => objet['@type'] === 'BreadcrumbList');
	/** The visible crumbs, in order: text and href (none for the current page). */
	const visibles = (container: HTMLElement) =>
		[...container.querySelectorAll('nav[aria-label="Fil d\'Ariane"] li > a, nav[aria-label="Fil d\'Ariane"] li > [aria-current]')].map((el) => [
			el.textContent,
			el.getAttribute('href')
		]);

	afterEach(() => (document.head.innerHTML = ''));

	it('a commune: Recherche > its department (linked) > the commune (current page)', () => {
		const { container } = render(Page, { props: { data: commune } });
		expect(visibles(container)).toEqual([
			['Accueil', '/'],
			['Vendée', '/departement/85'],
			[COMMUNE_85047_CHALLANS.nom_commune, null]
		]);
		expect(container.querySelector('nav[aria-label="Fil d\'Ariane"] [aria-current="page"]')?.textContent).toBe('Challans');
	});

	it('an arrondissement: Recherche > department > parent commune > rank', () => {
		const { container } = render(Page, { props: { data: arrondissement } });
		expect(visibles(container)).toEqual([
			['Accueil', '/'],
			['Paris (département)', '/departement/75'],
			['Paris', '/commune/75056'],
			['12e arrondissement', null]
		]);
	});

	it('the BreadcrumbList repeats the visible crumbs, with absolute URLs, the last one being the canonical', () => {
		for (const data of [commune, arrondissement]) {
			document.head.innerHTML = '';
			const { container } = render(Page, { props: { data } });
			const listes = fil();
			expect(listes).toHaveLength(1);
			const [liste] = listes;
			expect(liste['@context']).toBe('https://schema.org');
			expect(liste.itemListElement.map((e: { position: number }) => e.position)).toEqual(visibles(container).map((_, i) => i + 1));
			expect(liste.itemListElement.map((e: { name: string }) => e.name)).toEqual(visibles(container).map(([nom]) => nom));
			for (const element of liste.itemListElement) {
				expect(element['@type']).toBe('ListItem');
				expect(element.item).toMatch(/^https:\/\/[^/]+\//);
			}
			const canonique = document.head.querySelector('link[rel="canonical"]')?.getAttribute('href');
			expect(liste.itemListElement.at(-1).item).toBe(canonique);
			expect(liste.itemListElement[1].item).toMatch(/\/departement\/(85|75)$/);
		}
	});

	it('Paris, a commune that bears the name of its departement, tells the two apart: « Paris (département) » then « Paris »', () => {
		const paris: FicheCommuneData = {
			etat: 'fiche',
			code: '75056',
			synthese: { ...COMMUNE_85047_CHALLANS, code_insee: '75056', nom_commune: 'Paris', code_departement: '75', type_zone: 'commune', commune_parent: null },
			sources: null,
			consulteLe: '31 octobre 2026'
		};
		const { container } = render(Page, { props: { data: paris } });
		expect(visibles(container)).toEqual([
			['Accueil', '/'],
			['Paris (département)', '/departement/75'],
			['Paris', null]
		]);
		// The BreadcrumbList says what the page shows; the area of the Dataset keeps the real name.
		expect(fil()[0].itemListElement.map((e: { name: string }) => e.name)).toEqual(['Accueil', 'Paris (département)', 'Paris']);
		const dataset = jsonLd().find((objet) => objet['@type'] === 'Dataset');
		expect(dataset.spatialCoverage.containedInPlace.name).toBe('Paris');
	});

	it('leaves the name of the departement alone when no commune of the path bears it', () => {
		for (const data of [commune, arrondissement]) {
			document.head.innerHTML = '';
			const { container } = render(Page, { props: { data } });
			const noms = visibles(container).map(([nom]) => nom);
			expect(noms.some((nom) => /\(département\)/.test(String(nom)))).toBe(data === arrondissement);
		}
	});

	it('the Dataset places the commune in its department: AdministrativeArea with name, code and page', () => {
		render(Page, { props: { data: commune } });
		const dataset = jsonLd().find((objet) => objet['@type'] === 'Dataset');
		expect(dataset.spatialCoverage).toMatchObject({
			'@type': 'Place',
			name: 'Challans',
			identifier: '85047',
			containedInPlace: { '@type': 'AdministrativeArea', name: 'Vendée', identifier: '85' }
		});
		expect(dataset.spatialCoverage.containedInPlace.url).toBe('https://datacommune.fr/departement/85');
	});

	it('an unread or out-of-coverage page still goes through the department served by the referential, with no JSON-LD', () => {
		for (const data of [
			{ etat: 'non-renseignee', code: '85047', identite: { nom: 'Challans', departement: '85', codePostal: '85300' } },
			{ etat: 'hors-couverture', code: '85047', identite: { nom: 'Challans', departement: '85', codePostal: '85300' }, motif: null }
		] as unknown as FicheCommuneData[]) {
			document.head.innerHTML = '';
			const { container } = render(Page, { props: { data } });
			expect(visibles(container).slice(0, 2)).toEqual([
				['Accueil', '/'],
				['Vendée', '/departement/85']
			]);
			expect(jsonLd()).toHaveLength(0);
		}
	});

	it('a department that is not served or not known leaves no dead link: Recherche > the commune', () => {
		const sansDepartement = { ...commune, synthese: { ...COMMUNE_85047_CHALLANS, code_departement: null } } as unknown as FicheCommuneData;
		const inconnu = { ...commune, synthese: { ...COMMUNE_85047_CHALLANS, code_departement: '00' } } as unknown as FicheCommuneData;
		for (const data of [sansDepartement, inconnu]) {
			document.head.innerHTML = '';
			const { container } = render(Page, { props: { data } });
			expect(visibles(container)).toEqual([
				['Accueil', '/'],
				['Challans', null]
			]);
			expect(container.querySelector('nav[aria-label="Fil d\'Ariane"] a[href^="/departement/"]')).toBeNull();
			const dataset = jsonLd().find((objet) => objet['@type'] === 'Dataset');
			expect(dataset.spatialCoverage.containedInPlace).toBeUndefined();
			expect(fil()[0].itemListElement).toHaveLength(2);
		}
	});
});

// 04.7-10, decision 10: every JSON-LD of the page goes through one serializer; a name read from the
// API that holds </script> neither closes the tag nor adds an element, and no script holds a
// character that HTML or JavaScript could read.
describe('/commune/[code] — JSON-LD inerte (04.7-10)', () => {
	const hostile = `Évil</script><img id="intrus" src="x">&amp;${String.fromCharCode(0x2028)}${String.fromCharCode(0x2029)}`;
	const data: FicheCommuneData = {
		etat: 'fiche',
		code: '85047',
		synthese: { ...COMMUNE_85047_CHALLANS, nom_commune: hostile },
		sources: null,
		consulteLe: '31 octobre 2026'
	};

	afterEach(() => (document.head.innerHTML = ''));

	it('keeps every script ld+json free of < > & U+2028 U+2029, and reads back the original name', () => {
		render(Page, { props: { data } });
		expect(document.head.querySelector('#intrus')).toBeNull();
		const scripts = [...document.head.querySelectorAll('script[type="application/ld+json"]')];
		expect(scripts.length).toBeGreaterThanOrEqual(2);
		for (const script of scripts) {
			expect(script.textContent, 'script text').not.toMatch(new RegExp(`[<>&${String.fromCharCode(0x2028)}${String.fromCharCode(0x2029)}]`));
		}
		const dataset = scripts.map((s) => JSON.parse(s.textContent ?? '{}')).find((objet) => objet['@type'] === 'Dataset');
		expect(dataset.name).toBe(`Données publiques d’${hostile}`);
		expect(dataset.spatialCoverage.name).toBe(hostile);
	});
});

// SEO audit 2026-11-14: every commune fiche is reached by links rendered by the server — the communes
// around it, then its departement, whose page lists all its communes.
describe('/commune/[code] — autour de la commune', () => {
	const VOISINES = [
		{ code_insee: '85194', nom: 'Sallertaine', departement_code: '85', type_zone: 'commune' as const, limitrophe: true },
		{ code_insee: '44131', nom: 'Machecoul-Saint-Même', departement_code: '44', type_zone: 'commune' as const, limitrophe: false }
	];

	it('links the communes around, in the served order, the code of another departement given, then the departement', () => {
		const data = { etat: 'fiche', code: '85047', synthese: COMMUNE_85047_CHALLANS, sources: null, consulteLe: '31 octobre 2026', voisines: VOISINES } as FicheCommuneData;
		const { container } = render(Page, { props: { data } });
		const autour = container.querySelector('section#autour')!;
		expect(autour.querySelector('h2')?.textContent).toBe('Autour de Challans');
		expect([...autour.querySelectorAll('ul.voisines a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['Sallertaine', '/commune/85194'],
			[`Machecoul-Saint-Même${String.fromCharCode(0xa0)}(44)`, '/commune/44131']
		]);
		expect(autour.querySelector('ul.liste-liens a')?.getAttribute('href')).toBe('/departement/85');
	});

	it('keeps the departement link alone when the neighbours could not be read, on a page out of coverage too', () => {
		for (const data of [
			{ etat: 'fiche', code: '85047', synthese: COMMUNE_85047_CHALLANS, sources: null, consulteLe: '31 octobre 2026', voisines: null },
			{ etat: 'hors-couverture', code: '44109', identite: { nom: 'Nantes', departement: '44', codePostal: '44000' }, motif: null }
		] as unknown as FicheCommuneData[]) {
			const { container, unmount } = render(Page, { props: { data } });
			expect(container.querySelector('#autour ul.voisines')).toBeNull();
			expect(container.querySelector('#autour ul.liste-liens a')?.getAttribute('href')).toMatch(/^\/departement\/(85|44)$/);
			unmount();
		}
	});
});
