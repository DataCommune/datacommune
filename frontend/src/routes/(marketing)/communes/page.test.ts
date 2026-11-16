// A failed search is typed on `/communes` (WR-01, D-15 review of 03-16): a shared-key rate limit or
// a refused access is not an outage of the service, and the notice says so with a constant of the
// client, never the server body.
import { render } from '@testing-library/svelte';
import { writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	MESSAGE_ACCES_REFUSE,
	messageLimite,
	TITRE_ACCES_REFUSE,
	TITRE_DEMANDE_REFUSEE,
	TITRE_LIMITE
} from '$lib/data/panne';
import type {
	CommuneAnnuaireRow,
	CommunesAnnuaireResponse,
	PrixTypeAnnuaire
} from '$lib/api/communes';
import type { DatasetHosting } from '$lib/api/admin';
import type { EtatColonne } from '$lib/api/commune';
import {
	AUCUNE_VENTE_RETENUE,
	FILTRES_PAR_DEFAUT,
	PRIX_INDISPONIBLE,
	celluleDePrix,
	periodeDesPrix,
	periodeDUnPrix,
	hrefAnnuaire,
	lireFiltresAnnuaire,
	pagesVoisines,
	parametresApi,
	type FiltresAnnuaire
} from '$lib/data/annuaire-communes';
import { comparisonSelection } from '$lib/stores/comparison-selection.svelte';
import { tick } from 'svelte';
import { LIEN_COUVERTURE, TITRE_CODE_POSTAL_HORS_COUVERTURE } from '$lib/data/recherche-code-postal';
import type { CommuneSearchResult } from '$lib/api/communes';
import type { ResultatRechercheAdresse } from '$lib/api/adresses';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', () => ({
	navigating: writable(null),
	page: writable({ url: new URL('https://fixture/communes?q=nantes') })
}));

import { load, type CommunesPageData } from './+page';
import Page from './+page.svelte';

const GENERIQUE = 'La recherche est momentanément indisponible';

function reponse(status: number, headers: Record<string, string> = {}) {
	return new Response(JSON.stringify({ detail: 'texte serveur' }), {
		status,
		headers: { 'Content-Type': 'application/json', ...headers }
	});
}

async function charger(fetch: typeof globalThis.fetch): Promise<CommunesPageData> {
	return (await (
		load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>
	)({ url: new URL('https://fixture/communes?q=nantes'), fetch })) as CommunesPageData;
}

afterEach(() => vi.restoreAllMocks());

describe('/communes — a failed search is typed (WR-01)', () => {
	it('a shared-key rate limit (429 with Retry-After) renders the rate-limit sentence, never the generic outage', async () => {
		const fetch = vi.fn(async () => reponse(429, { 'Retry-After': '60' }));
		const data = await charger(fetch);
		expect(data.mode).toBe('recherche');
		expect(data.panne).toEqual({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(60) });

		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.querySelector('.notice-title')?.textContent).toBe(TITRE_LIMITE);
		expect(alerte.textContent).toContain(messageLimite(60));
		expect(alerte.textContent).not.toContain(GENERIQUE);
		expect(alerte.textContent).not.toContain('texte serveur');
	});

	it('a refused access (403) renders the refused-access sentence', async () => {
		const fetch = vi.fn(async () => reponse(403));
		const data = await charger(fetch);
		expect(data.panne).toEqual({ statut: 403, titre: TITRE_ACCES_REFUSE, message: MESSAGE_ACCES_REFUSE });

		const { getByRole } = render(Page, { props: { data } });
		expect(getByRole('alert').querySelector('.notice-title')?.textContent).toBe(TITRE_ACCES_REFUSE);
		expect(getByRole('alert').textContent).toContain(MESSAGE_ACCES_REFUSE);
		expect(getByRole('alert').textContent).not.toContain(GENERIQUE);
	});

	it('a refused request (409) is titled by the client and carries its status (03-17 B C1)', async () => {
		const fetch = vi.fn(async () => reponse(409));
		const data = await charger(fetch);

		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.querySelector('.notice-title')?.textContent).toBe(TITRE_DEMANDE_REFUSEE);
		expect(alerte.textContent).toContain('erreur 409');
		expect(alerte.textContent).not.toContain(GENERIQUE);
		expect(alerte.textContent).not.toContain('texte serveur');
	});

	// D8 (2026-11-10): the outage is the Notice of the fiche; without a typed title, its generic sentence is the title.
	it('a 5xx keeps the generic outage text, without a typed title', async () => {
		const fetch = vi.fn(async () => reponse(503));
		const data = await charger(fetch);
		expect(data.panne).toEqual({ statut: 503, titre: null, message: null });

		const { getByRole } = render(Page, { props: { data } });
		const alerte = getByRole('alert');
		expect(alerte.classList.contains('notice')).toBe(true);
		expect(alerte.querySelector('.notice-title')?.textContent).toContain(GENERIQUE);
		expect(alerte.textContent?.split(GENERIQUE)).toHaveLength(2);
		for (const titre of [TITRE_LIMITE, TITRE_ACCES_REFUSE, TITRE_DEMANDE_REFUSEE]) expect(alerte.textContent).not.toContain(titre);
	});
});

// 03-17 V14, X1, A19: a postal code out of coverage has the warn notice of the fiche, its own title
// and the coverage link; a zero result stays neutral. Colour AND title tell them apart.
describe('/communes — out of coverage is not a zero', () => {
	const base: CommunesPageData = {
		mode: 'recherche',
		q: '33000',
		departement: '',
		limite: 20,
		results: [],
		annuaire: null,
		panne: null,
		couverture: null,
		adresses: null,
		ordre: ['commune', 'adresse'],
		suite: null
	};

	// 04.7-09 (review Codex 3): an arrondissement found is never counted as a commune.
	it('counts the arrondissements found apart from the communes', () => {
		const resultat = (code_insee: string, nom: string, type_zone: 'commune' | 'arrondissement') =>
			({ code_insee, nom, code_postal: null, departement: code_insee.slice(0, 2), population: 1000,
			   population_annee: 2022, population_source: 'demographie',
			   hebergement: { etat: 'heberge', motif: null, departements: [code_insee.slice(0, 2)] },
			   type_zone, commune_parent: type_zone === 'arrondissement' ? '75056' : null }) as unknown as CommuneSearchResult;
		const seul = render(Page, { props: { data: { ...base, q: 'Paris 12e', results: [resultat('75112', 'Paris 12e Arrondissement', 'arrondissement')] } } });
		expect(seul.container.textContent).toContain('1 arrondissement trouvé');
		expect(seul.container.textContent).not.toContain('commune trouvée');
		seul.unmount();
		const deux = render(Page, {
			props: { data: { ...base, q: 'Paris', results: [resultat('75056', 'Paris', 'commune'), resultat('75112', 'Paris 12e Arrondissement', 'arrondissement')] } }
		});
		expect(deux.container.textContent).toContain('1 commune et 1 arrondissement trouvés');
		deux.unmount();
		const communes = render(Page, { props: { data: { ...base, q: 'Nan', results: [resultat('44109', 'Nantes', 'commune')] } } });
		expect(communes.container.textContent).toContain('1 commune trouvée');
	});

	it('a postal code out of coverage: warn notice, glossary title, coverage link', () => {
		const data: CommunesPageData = { ...base, couverture: { kind: 'hors-couverture', phrase: 'Territoire hors du périmètre.' } };
		const { container } = render(Page, { props: { data } });
		const encart = container.querySelector('.communes-notice.communes-notice-warn');
		expect(encart).not.toBeNull();
		// D8 (2026-11-10): the state panel of the fiche, warn.
		expect(encart?.querySelector('.notice.notice-warn[role="status"]')).not.toBeNull();
		expect(encart?.querySelector('.notice-title')?.textContent).toBe(TITRE_CODE_POSTAL_HORS_COUVERTURE);
		expect(encart?.querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		expect(encart?.textContent).not.toMatch(/indisponible/);
	});

	it('a zero result stays a neutral notice, titled « Aucune commune trouvée »', () => {
		const data: CommunesPageData = { ...base, q: 'zzzzqqq' };
		const { container } = render(Page, { props: { data } });
		const encart = container.querySelector('.communes-notice');
		expect(encart).not.toBeNull();
		expect(encart?.classList.contains('communes-notice-warn')).toBe(false);
		expect(encart?.querySelector('.notice:not(.notice-warn)[role="status"]')).not.toBeNull();
		expect(encart?.querySelector('.notice-title')?.textContent).toContain('Aucune commune trouvée');
	});
});

// SEM-02 / H6 (quick 260929-v84): the directory serves the population of the commune page. A value
// from `demographie` carries its year and « INSEE »; the referential value
// (`geo_communes`, year unknown) is shown alone, and a year is never invented.
// 04.6-02: the directory is the default view of `/communes` (no `q`), with the served prices.
const HEBERGEE: DatasetHosting = { etat: 'heberge', motif: null, departements: ['35'] };
const FENETRE = '2024-12-31 → 2025-12-31';

function prixServi(mediane_m2: number | null, effectif: number | null, etat: EtatColonne | null = null): PrixTypeAnnuaire {
	return { mediane_m2, effectif, etat };
}

function communeAnnuaire(partial: Partial<CommuneAnnuaireRow> & Pick<CommuneAnnuaireRow, 'code_insee' | 'nom'>): CommuneAnnuaireRow {
	return {
		departement: partial.code_insee.slice(0, 2),
		population: 1000,
		population_annee: 2022,
		population_source: 'demographie',
		hebergement: HEBERGEE,
		prix: { maison: prixServi(2500, 40), appartement: prixServi(3100, 12) },
		...partial
	};
}

function reponseAnnuaire(results: CommuneAnnuaireRow[], extra: Partial<CommunesAnnuaireResponse> = {}): CommunesAnnuaireResponse {
	return {
		results,
		total_count: results.length,
		page: 1,
		per_page: 50,
		has_next: false,
		couverture: 'servies',
		departements_servis: ['35'],
		prix: { etat: 'servi', motif: null, fenetre_mediane_dvf: FENETRE },
		...extra
	};
}

function pageAnnuaire(reponse: CommunesAnnuaireResponse | null, filtres: Partial<FiltresAnnuaire> = {}): CommunesPageData {
	return {
		mode: 'annuaire',
		q: '',
		departement: '',
		limite: 20,
		results: [],
		annuaire: { filtres: { ...FILTRES_PAR_DEFAUT, ...filtres }, reponse },
		panne: null,
		couverture: null,
		adresses: null,
		ordre: ['commune', 'adresse'],
		suite: null
	};
}

/** Text with every kind of space (narrow no-break of fr-FR numbers included) as one space. */
const norm = (texte: string | null | undefined) => (texte ?? '').replace(/\s+/g, ' ').trim();

/** Text of the table row of *code* (the desktop rendering). */
function rangee(container: HTMLElement, code: string): HTMLElement {
	const row = container.querySelector<HTMLElement>(`table.annuaire-table tr[data-code-insee="${code}"]`);
	expect(row, code).not.toBeNull();
	return row!;
}

describe('/communes — annuaire : arrondissements sous leur commune (04.7-09)', () => {
	const paris = communeAnnuaire({ code_insee: '75056', nom: 'Paris', departement: '75', population: 2_100_000 });
	const lyon = communeAnnuaire({ code_insee: '69123', nom: 'Lyon', departement: '69', population: 520_000 });
	const arr = (code: string, nom: string, parent: string, population: number) =>
		communeAnnuaire({ code_insee: code, nom, departement: parent.slice(0, 2), population, type_zone: 'arrondissement', commune_parent: parent });

	it('counts communes and arrondissements together where a PLM departement is served', () => {
		const avec = render(Page, { props: { data: pageAnnuaire(reponseAnnuaire([paris, lyon], { departements_servis: ['75', '44'] })) } });
		expect(avec.container.textContent).toContain('2 communes et arrondissements');
		avec.unmount();
		const sans = render(Page, { props: { data: pageAnnuaire(reponseAnnuaire([paris, lyon], { departements_servis: ['44'] })) } });
		expect(sans.container.textContent).toContain('2 communes');
		expect(sans.container.textContent).not.toContain('arrondissements');
	});

	it('lists an arrondissement right under its commune, not at its own population rank', () => {
		// The API sorts by population: Paris 12e (140 000) lands after Lyon, away from Paris.
		const data = pageAnnuaire(
			reponseAnnuaire([paris, lyon, arr('75112', 'Paris 12e Arrondissement', '75056', 140_000), arr('75101', 'Paris 1er Arrondissement', '75056', 16_000)])
		);
		const { container } = render(Page, { props: { data } });
		const codes = [...container.querySelectorAll('table.annuaire-table tbody tr')].map((tr) => tr.getAttribute('data-code-insee'));
		expect(codes).toEqual(['75056', '75101', '75112', '69123']);
	});

	it('words the arrondissement by its rank, links its own fiche and never sums it into the commune', () => {
		const data = pageAnnuaire(reponseAnnuaire([paris, arr('75112', 'Paris 12e Arrondissement', '75056', 140_000)]));
		const { container } = render(Page, { props: { data } });
		const ligne = rangee(container, '75112');
		expect(ligne.classList.contains('annuaire-sous')).toBe(true);
		const lien = ligne.querySelector('th a');
		expect(norm(lien?.textContent)).toBe('12e arrondissement de Paris');
		expect(norm(lien?.firstChild?.textContent)).toBe('12e arrondissement');
		expect(lien?.getAttribute('href')).toBe('/commune/75112');
		// Paris keeps its own served population: the arrondissement is not added to it.
		// D7 (2026-11-10): the population comes after the prices; its cell is read by its class, not its rank.
		expect(norm(rangee(container, '75056').querySelector('td.annuaire-population')?.textContent)).toContain('2 100 000');
		expect(norm(ligne.querySelector('td.annuaire-population')?.textContent)).toContain('140 000');
	});

	it('two arrondissements of the same rank in two cities keep distinct accessible link names (table and cards)', () => {
		const lyon1 = arr('69381', 'Lyon 1er Arrondissement', '69123', 29_000);
		const paris1 = arr('75101', 'Paris 1er Arrondissement', '75056', 16_000);
		const { container } = render(Page, { props: { data: pageAnnuaire(reponseAnnuaire([paris, lyon, paris1, lyon1])) } });
		for (const racine of ['table.annuaire-table', 'ul.annuaire-cartes']) {
			const noms = ['75101', '69381'].map((code) => {
				const lien = container.querySelector(`${racine} [data-code-insee="${code}"] a`);
				expect(lien, `${racine} ${code}`).not.toBeNull();
				// Visual label stays short, the hidden text completes the accessible name.
				expect(lien!.querySelector('.annuaire-sr')).not.toBeNull();
				return norm(lien!.textContent);
			});
			expect(noms).toEqual(['1er arrondissement de Paris', '1er arrondissement de Lyon']);
		}
	});

	it('an arrondissement whose commune is not on the page stays in place and says whose it is', () => {
		const data = pageAnnuaire(reponseAnnuaire([lyon, arr('75112', 'Paris 12e Arrondissement', '75056', 140_000)]));
		const { container } = render(Page, { props: { data } });
		const ligne = rangee(container, '75112');
		expect(ligne.classList.contains('annuaire-sous')).toBe(false);
		expect(ligne.querySelector('th a')?.textContent).toBe('Paris 12e Arrondissement');
		expect(norm(ligne.querySelector('th')?.textContent)).toContain('arrondissement de Paris');
	});

	it('the cards follow the same order as the table', () => {
		const data = pageAnnuaire(reponseAnnuaire([paris, lyon, arr('75112', 'Paris 12e Arrondissement', '75056', 140_000)]));
		const { container } = render(Page, { props: { data } });
		const codes = [...container.querySelectorAll('.annuaire-cartes > li')].map((li) => li.getAttribute('data-code-insee'));
		expect(codes).toEqual(['75056', '75112', '69123']);
	});
});

describe('/communes — annuaire : population datée et sourcée (H6)', () => {
	// A year and « INSEE » right after the unit: the source suffix.
	const SUFFIXE_SOURCE = /habitants?[\s ]*·[\s ]*\d{4}[\s ]*·[\s ]*INSEE/;

	it('a demographie value shows its year and INSEE; a referential value shows the unit alone', () => {
		const data = pageAnnuaire(
			reponseAnnuaire([
				communeAnnuaire({ code_insee: '35238', nom: 'Rennes', population: 227830, population_annee: 2022 }),
				communeAnnuaire({ code_insee: '35902', nom: 'Rang B', population: 4000, population_annee: null, population_source: 'geo_communes' }),
				communeAnnuaire({ code_insee: '35903', nom: 'Sans population', population: null, population_annee: null, population_source: null })
			])
		);
		const { container } = render(Page, { props: { data } });

		expect(rangee(container, '35238').textContent).toMatch(/227[\s  ]830 habitants · 2022 · INSEE/);
		const referentiel = rangee(container, '35902').textContent ?? '';
		expect(referentiel).toMatch(/4[\s  ]000 habitants/);
		expect(referentiel).not.toMatch(SUFFIXE_SOURCE);
		const sans = rangee(container, '35903').textContent ?? '';
		expect(sans).not.toContain('habitant');
		expect(sans).toContain('Non renseignée');
	});

	it('a demographie source without a year never shows an invented year', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes', population: 227830, population_annee: null })]));
		const { container } = render(Page, { props: { data } });

		const texte = rangee(container, '35238').textContent ?? '';
		expect(texte).toMatch(/227[\s  ]830 habitants/);
		expect(texte).not.toMatch(SUFFIXE_SOURCE);
	});

	it('a population of 1 keeps the singular', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35904', nom: 'Hameau', population: 1 })]));
		const { container } = render(Page, { props: { data } });

		const texte = rangee(container, '35904').textContent ?? '';
		expect(texte).toMatch(/1 habitant · 2022 · INSEE/);
		expect(texte).not.toContain('habitants');
	});
});

describe('/communes — annuaire : états des prix (SEM-01, D-47)', () => {
	const PEU: EtatColonne = { etat: 'insufficient_sample', motif: '3 mutations de maisons retenues sur 12 mois, sous le seuil de publication de 5.' };
	const ZERO: EtatColonne = { etat: 'insufficient_sample', motif: "0 mutations d'appartements retenues sur 12 mois, sous le seuil de publication de 5." };
	const SANS_LIGNE: EtatColonne = { etat: 'not_reported', motif: 'Aucune mutation retenue pour la commune dans la fenêtre des 12 derniers mois.' };
	const HORS: EtatColonne = { etat: 'out_of_coverage', motif: 'territoire_hors_perimetre' };

	it('a value with its sales, the window once for the page; n.d. with its count; no sale only on a served zero', () => {
		const valeur = celluleDePrix(prixServi(3650, 412));
		expect([valeur.kind, norm(valeur.texte), norm(valeur.detail)]).toEqual([
			'valeur',
			'3 650 €/m²',
			'412 ventes'
		]);
		const nd = celluleDePrix(prixServi(null, 3, PEU));
		expect([nd.kind, norm(nd.texte), nd.detail]).toEqual(['nd', 'n.d. (3 ventes)', null]);
		expect(norm(periodeDesPrix(FENETRE))).toBe('Prix médians au m² des ventes retenues (DVF) du 31 décembre 2024 au 31 décembre 2025');
		expect(periodeDesPrix(null)).toBeNull();
		expect(norm(periodeDUnPrix(valeur, FENETRE))).toBe('ventes retenues du 31 décembre 2024 au 31 décembre 2025');
		expect(norm(periodeDUnPrix(nd, FENETRE))).toBe('ventes retenues du 31 décembre 2024 au 31 décembre 2025');
		expect(periodeDUnPrix(valeur, null)).toBeNull();
		expect(periodeDUnPrix(celluleDePrix(null), FENETRE)).toBeNull();
		expect(norm(celluleDePrix(prixServi(null, 1, PEU)).texte)).toBe('n.d. (1 vente)');
		expect(celluleDePrix(prixServi(null, 0, ZERO))).toMatchObject({ kind: 'aucune-vente', texte: AUCUNE_VENTE_RETENUE });
	});

	it('not_reported is « Non renseigné », never zero; out of coverage keeps its reason; a failed read is unavailable', () => {
		expect(celluleDePrix(prixServi(null, null, SANS_LIGNE))).toEqual({ kind: 'etat', texte: 'Non renseigné', detail: null });
		expect(celluleDePrix(prixServi(null, null, HORS)).texte).toBe('Hors couverture · département non hébergé');
		expect(celluleDePrix(null)).toEqual({ kind: 'indisponible', texte: PRIX_INDISPONIBLE, detail: null });
		// An absence without its served state is said as such, never disguised as a known state.
		expect(celluleDePrix(prixServi(null, null, null)).texte).toBe('État non déclaré');
	});

	it('renders each state in its cell, in the table and in the card, and no zero for a missing value', () => {
		const data = pageAnnuaire(
			reponseAnnuaire([
				communeAnnuaire({ code_insee: '35238', nom: 'Rennes', prix: { maison: prixServi(3650, 412), appartement: prixServi(null, 3, PEU) } }),
				communeAnnuaire({ code_insee: '35001', nom: 'Sans Ligne', prix: { maison: prixServi(null, null, SANS_LIGNE), appartement: prixServi(null, 0, ZERO) } }),
				communeAnnuaire({
					code_insee: '79191',
					nom: 'Niort',
					departement: '79',
					hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] },
					prix: { maison: prixServi(null, null, HORS), appartement: prixServi(null, null, HORS) }
				})
			])
		);
		const { container } = render(Page, { props: { data } });

		const rennes = rangee(container, '35238');
		// D7 (2026-11-10): the two prices are the first two cells of the row.
		const [maison, appartement] = [...rennes.querySelectorAll('td.num')].slice(0, 2).map((td) => norm(td.textContent));
		expect(maison).toContain('3 650 €/m²');
		expect(maison).toContain('412 ventes');
		// Said once for the page (caption and the line above the cards), and in each price for a
		// reader of that cell alone, visually hidden.
		expect(norm(container.querySelector('caption')?.textContent)).toContain('ventes retenues (DVF) du 31 décembre 2024 au 31 décembre 2025');
		expect(norm(rennes.querySelector('td.num:nth-of-type(1) .annuaire-sr')?.textContent)).toBe(', ventes retenues du 31 décembre 2024 au 31 décembre 2025');
		const prixCarte = container.querySelector('.annuaire-carte[data-code-insee="35238"] dd');
		expect(norm(prixCarte?.querySelector('.annuaire-sr')?.textContent)).toBe(', ventes retenues du 31 décembre 2024 au 31 décembre 2025');
		expect(norm(prixCarte?.querySelector('.prix-detail')?.textContent)).toBe('412 ventes');
		expect(appartement).toContain('n.d. (3 ventes)');

		const sansLigne = norm(rangee(container, '35001').textContent);
		expect(sansLigne).toContain('Non renseigné');
		expect(sansLigne).toContain(AUCUNE_VENTE_RETENUE);
		expect(sansLigne).not.toMatch(/\b0 €/);

		expect(rangee(container, '79191').textContent).toContain('Hors couverture · département non hébergé');

		const carte = container.querySelector('.annuaire-cartes li[data-code-insee="35238"]');
		expect(carte?.querySelectorAll('dt')).toHaveLength(2);
		expect(carte?.textContent).toContain('Maisons');
		expect(norm(carte?.textContent)).toContain('3 650 €/m²');
		// The technical keys never reach the screen.
		expect(container.textContent).not.toMatch(/territoire_hors_perimetre|insufficient_sample|not_reported/);
	});

	it('a failed price read keeps the list, says so with a relaunch, and shows no price', () => {
		const data = pageAnnuaire(
			reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes', prix: null })], {
				prix: { etat: 'indisponible', motif: 'Synthèse communale temporairement indisponible.', fenetre_mediane_dvf: null }
			}),
			{ nom: 'ren', population: '10000-49999' }
		);
		const { container, getByRole } = render(Page, { props: { data } });

		const alerte = getByRole('alert');
		expect(norm(alerte.textContent)).toContain('Prix momentanément indisponibles');
		expect(norm(alerte.textContent)).toContain('Ce n’est pas une absence de ventes');
		const relance = alerte.querySelector('form');
		expect(relance?.getAttribute('method')).toBe('get');
		const caches = [...(relance?.querySelectorAll('input[type="hidden"]') ?? [])].map((i) => [i.getAttribute('name'), i.getAttribute('value')]);
		expect(caches).toEqual([
			['nom', 'ren'],
			['population', '10000-49999']
		]);
		expect(rangee(container, '35238').textContent).toContain(PRIX_INDISPONIBLE);
		expect(rangee(container, '35238').textContent).not.toMatch(/€\/m²|ventes?/);
	});

	it('a failed list is the page outage, with a relaunch of the same filters', async () => {
		const fetch = vi.fn(async () => reponse(503));
		const data = (await (load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>)({
			url: new URL('https://fixture/communes?departement=35'),
			fetch
		})) as CommunesPageData;

		expect(data.mode).toBe('annuaire');
		expect(data.annuaire?.reponse).toBeNull();
		const { getByRole } = render(Page, { props: { data } });
		expect(norm(getByRole('alert').textContent)).toContain('L’annuaire est momentanément indisponible');
		expect(getByRole('alert').querySelector('input[name="departement"]')?.getAttribute('value')).toBe('35');
	});
});

describe('/communes — annuaire : filtres dans l’URL et modes', () => {
	async function chargerUrl(url: string, fetch: typeof globalThis.fetch) {
		return (await (load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>)({
			url: new URL(url),
			fetch
		})) as CommunesPageData;
	}

	function json(body: unknown) {
		return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
	}

	it('without q, the directory is loaded with the default filters, no departement required', async () => {
		const fetch = vi.fn(async (_input: RequestInfo | URL) => json({ statut: 'ok', donnees: reponseAnnuaire([]) }));

		const data = await chargerUrl('https://fixture/communes', fetch);

		expect(data.mode).toBe('annuaire');
		expect(fetch).toHaveBeenCalledTimes(1);
		const url = new URL(String(fetch.mock.calls[0][0]), 'http://origin.invalid');
		expect(url.pathname).toBe('/v1/communes/annuaire');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			couverture: 'servies',
			sort_by: 'population',
			order: 'desc',
			page: '1',
			per_page: '50',
			inclure_arrondissements: 'true'
		});
	});

	it('the URL filters reach the API; an unknown value is dropped, never sent', async () => {
		const fetch = vi.fn(async (_input: RequestInfo | URL) => json({ statut: 'ok', donnees: reponseAnnuaire([]) }));

		await chargerUrl(
			'https://fixture/communes?nom=%20saint%20&departement=35&population=2000-9999&couverture=toutes&tri=nom-asc&page=3',
			fetch
		);
		await chargerUrl('https://fixture/communes?departement=00&population=1-2&tri=prix&couverture=x&page=-4', fetch);

		const [filtree, inconnue] = fetch.mock.calls.map(([input]) => Object.fromEntries(new URL(String(input), 'http://o.invalid').searchParams));
		expect(filtree).toEqual({
			nom: 'saint',
			departement: '35',
			population_min: '2000',
			population_max: '9999',
			couverture: 'toutes',
			sort_by: 'nom',
			order: 'asc',
			page: '3',
			per_page: '50',
			inclure_arrondissements: 'true'
		});
		expect(inconnue).toEqual({
			couverture: 'servies',
			sort_by: 'population',
			order: 'desc',
			page: '1',
			per_page: '50',
			inclure_arrondissements: 'true'
		});
	});

	it.each(['Infinity', '1e309', '-Infinity', 'NaN', 'abc', '0', '-3', ''])('page=%s falls back to page 1', (page) => {
		expect(lireFiltresAnnuaire(new URLSearchParams({ page })).page).toBe(1);
	});

	it('a finite page is kept, its fraction dropped', () => {
		expect(lireFiltresAnnuaire(new URLSearchParams({ page: '7.9' })).page).toBe(7);
	});

	it('the open range sends its lower bound only', () => {
		expect(parametresApi({ ...FILTRES_PAR_DEFAUT, population: '50000-' })).toMatchObject({ population_min: 50000, population_max: undefined });
	});

	it('q keeps the single search: no directory read', async () => {
		const fetch = vi.fn(async (input: RequestInfo | URL) =>
			new URL(String(input), 'http://o.invalid').pathname === '/v1/adresses/recherche'
				? json({ statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: HEBERGEE } } })
				: json({ statut: 'ok', donnees: [] })
		);

		const data = await chargerUrl('https://fixture/communes?q=rennes', fetch);

		expect(data.mode).toBe('recherche');
		expect(data.annuaire).toBeNull();
		const chemins = fetch.mock.calls.map(([input]) => new URL(String(input), 'http://o.invalid').pathname).sort();
		// No commune found for « rennes »: the two shortened prefixes of the suggested next step (04.7-14)
		// are commune searches too; the directory is never read.
		expect(chemins).toEqual(['/v1/adresses/recherche', '/v1/communes/search', '/v1/communes/search', '/v1/communes/search']);
	});

	it('links keep the filters and leave the defaults out; a shared URL reads back the same filters', () => {
		const filtres = lireFiltresAnnuaire(new URLSearchParams('nom=ren&population=500-1999&tri=nom-desc'));
		expect(hrefAnnuaire(filtres, { page: 2 })).toBe('/communes?nom=ren&population=500-1999&tri=nom-desc&page=2');
		expect(hrefAnnuaire(FILTRES_PAR_DEFAUT)).toBe('/communes');
		expect(lireFiltresAnnuaire(new URL(`https://f${hrefAnnuaire(filtres, { page: 2 })}`).searchParams)).toEqual({ ...filtres, page: 2 });
	});

	it('without JavaScript: a GET form of named fields holding the URL state, and plain pagination links', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })], { total_count: 120, page: 2, has_next: true }), {
			nom: 'ren',
			departement: '35',
			population: '10000-49999',
			couverture: 'toutes',
			tri: 'nom-asc',
			page: 2
		});
		const { container } = render(Page, { props: { data } });

		const form = container.querySelector('form.annuaire-filtres');
		expect(form?.getAttribute('action')).toBe('/communes');
		expect(form?.getAttribute('method')).toBe('get');
		const champs = Object.fromEntries(
			[...(form?.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input[name], select[name]') ?? [])].map((c) => [c.name, c.value])
		);
		expect(champs).toEqual({ nom: 'ren', departement: '35', population: '10000-49999', couverture: 'toutes', tri: 'nom-asc' });
		for (const id of ['annuaire-nom', 'annuaire-departement', 'annuaire-population', 'annuaire-couverture', 'annuaire-tri']) {
			expect(container.querySelector(`label[for="${id}"]`), id).not.toBeNull();
		}

		const pages = container.querySelector('nav[aria-label="Pages de l\'annuaire"]');
		const liens = [...(pages?.querySelectorAll('a') ?? [])].map((a) => [a.textContent?.trim(), a.getAttribute('href')]);
		const premiere = '/communes?nom=ren&departement=35&population=10000-49999&couverture=toutes&tri=nom-asc';
		const derniere = `${premiere}&page=3`;
		expect(liens).toEqual([
			['Première page', premiere],
			['Page précédente', premiere],
			['1', premiere],
			['3', derniere],
			['Page suivante', derniere],
			['Dernière page', derniere]
		]);
		expect(pages?.textContent).toContain('Page 2 sur 3');
		// 4.9 lot 7: the number of a page reads as such, and the current page is said, not linked.
		expect(pages?.querySelector('a[href$="page=3"][aria-label="Page 3"]')).not.toBeNull();
		expect(pages?.querySelector('[aria-current="page"]')?.textContent?.trim()).toBe('Page 2');
		// The GET form of a page number keeps the filters and sends no second `page`.
		const saut = pages?.querySelector<HTMLFormElement>('form[action="/communes"][method="get"]');
		expect(Object.fromEntries(new FormData(saut!))).toEqual({ nom: 'ren', departement: '35', population: '10000-49999', couverture: 'toutes', tri: 'nom-asc', page: '2' });
		const numero = saut?.querySelector<HTMLInputElement>('input[name="page"]');
		expect(saut?.querySelector(`label[for="${numero?.id}"]`)?.textContent).toBe('Aller à la page');
		expect([numero?.min, numero?.max, numero?.required]).toEqual(['1', '3', true]);
		expect(norm(container.textContent)).toContain('120 communes');
	});

	// 4.9 lot 7 (C5): 699 pages are not walked one by one.
	it.each([
		[1, 699, [1, 2, 3]],
		[2, 699, [1, 2, 3, 4]],
		[350, 699, [348, 349, 350, 351, 352]],
		[698, 699, [696, 697, 698, 699]],
		[699, 699, [697, 698, 699]],
		[1, 2, [1, 2]]
	])('page %i of %i: the neighbours are the pages within two', (page, total, attendues) => {
		expect(pagesVoisines(page, total)).toEqual(attendues);
	});

	it('the first page offers neither first nor previous, the last neither next nor last', () => {
		const noms = (page: number) => {
			const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })], { total_count: 34950, page }));
			const { container, unmount } = render(Page, { props: { data } });
			const liens = [...container.querySelectorAll('nav[aria-label="Pages de l\'annuaire"] a')].map((a) => a.textContent?.trim());
			unmount();
			return liens;
		};
		expect(noms(1)).toEqual(['2', '3', 'Page suivante', 'Dernière page']);
		expect(noms(699)).toEqual(['Première page', 'Page précédente', '697', '698']);
	});

	it('a table with caption, column headers and row headers; one card per commune', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' }), communeAnnuaire({ code_insee: '35047', nom: 'Bruz' })]));
		const { container } = render(Page, { props: { data } });

		const table = container.querySelector('table.annuaire-table');
		expect(table?.querySelector('caption')?.textContent).toMatch(/Prix\s+médians au m²/);
		expect([...(table?.querySelectorAll('thead th') ?? [])].map((th) => th.getAttribute('scope'))).toEqual(Array(6).fill('col'));
		expect(table?.querySelectorAll('tbody th[scope="row"] a[href^="/commune/"]')).toHaveLength(2);
		expect(container.querySelectorAll('.annuaire-cartes > li')).toHaveLength(2);
	});

	// D7 (2026-11-10): the prices first, in the tint of the prices; the department and the population after.
	it('reads the prices first, their headers in the tint of the prices, on the table and on the cards', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]));
		const { container } = render(Page, { props: { data } });
		const entetes = [...container.querySelectorAll('table.annuaire-table thead th')];
		expect(entetes.map((th) => norm(th.textContent))).toEqual([
			'Commune',
			'Maisons, prix médian',
			'Appartements, prix médian',
			'Population',
			'Département',
			'Comparaison'
		]);
		expect(entetes.map((th) => th.classList.contains('teinte-q-prix'))).toEqual([false, true, true, false, false, false]);
		const carte = container.querySelector('.annuaire-cartes > li')!;
		const prix = carte.querySelector('.annuaire-carte-prix')!;
		const meta = carte.querySelector('.annuaire-carte-meta')!;
		expect(prix.compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(prix.classList.contains('teinte-q-prix')).toBe(true);
	});

	// T8 (2026-11-10): the year and the source of a dated population take their own line, never a « · » left
	// alone at the end of a line; the text read stays the same.
	it('puts the year and the source of a dated population on their own line, the text unchanged', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes', population: 222485, population_annee: 2022, population_source: 'demographie' })]));
		const { container } = render(Page, { props: { data } });
		const cellule = container.querySelector('tr[data-code-insee="35238"] td.annuaire-population')!;
		expect(norm(cellule.textContent)).toBe('222 485 habitants · 2022 · INSEE');
		expect(norm(cellule.querySelector('.pop-millesime')?.textContent)).toBe('· 2022 · INSEE');
		const meta = container.querySelector('li[data-code-insee="35238"] .annuaire-carte-meta')!;
		expect(meta.querySelector('.pop-millesime')).not.toBeNull();
	});

	it('an empty selection says so; a departement outside the served ones offers all communes', () => {
		const data = pageAnnuaire(reponseAnnuaire([], { departements_servis: ['35'] }), { departement: '79' });
		const { container } = render(Page, { props: { data } });

		expect(container.textContent).toContain('Aucune commune ne correspond à ces filtres');
		expect(container.textContent).toContain('Ce département n’est pas servi par ce site');
		const lien = [...container.querySelectorAll('a')].find((a) => a.textContent === 'Afficher les communes de ce département');
		expect(lien?.getAttribute('href')).toBe('/communes?departement=79&couverture=toutes');
		expect(container.querySelector('table.annuaire-table')).toBeNull();
	});

	it('a page past the total is empty with the total, and links back to the first page', () => {
		const data = pageAnnuaire(reponseAnnuaire([], { total_count: 120, page: 9 }), { page: 9 });
		const { container } = render(Page, { props: { data } });

		expect(norm(container.textContent)).toContain('120 communes');
		expect(container.textContent).toContain('Cette page est au-delà de la sélection');
		const retour = [...container.querySelectorAll('a')].find((a) => a.textContent === 'Revenir à la première page');
		expect(retour?.getAttribute('href')).toBe('/communes');
	});

	it('names the served departements from the answer, never from the screen', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })], { departements_servis: ['35', '79'] }));
		const { container } = render(Page, { props: { data } });

		expect(norm(container.querySelector('#annuaire-couverture-aide')?.textContent)).toContain('Départements servis : Ille-et-Vilaine (35), Deux-Sèvres (79).');
	});
});

describe('/communes — annuaire : comparaison (amélioration JavaScript)', () => {
	it('adds a commune to the tab selection and confirms in place', async () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]));
		const { container } = render(Page, { props: { data } });

		const rangeeRennes = rangee(container, '35238');
		const bouton = rangeeRennes.querySelector('button');
		expect(bouton?.textContent).toContain('Ajouter à la comparaison');
		// D7, D11 (2026-11-10): an action written as a link, lighter than the row it follows.
		expect(bouton?.classList.contains('bouton-lien')).toBe(true);
		bouton!.click();
		await tick();
		expect(rangeeRennes.querySelector('[role="status"]')?.textContent).toContain('Ajoutée à la comparaison (1/4)');
		expect(comparisonSelection.has('35238')).toBe(true);
		comparisonSelection.remove('35238');
	});
});

// 04-17 (UI-SPEC « /communes?q= — résultats typés », D-105..D-107): the results page searches the
// communes and the addresses of this instance in parallel, and shows two typed groups in the
// served order. A failed search is the typed state of its own group, never an empty one.
describe('/communes?q= — typed results, communes and addresses', () => {
	const LE_PIN: CommuneSearchResult = {
		code_insee: '35999',
		nom: 'Le Pin',
		code_postal: '35170',
		departement: '35',
		population: 750,
		population_annee: 2022,
		population_source: 'demographie',
		hebergement: { etat: 'heberge', motif: null, departements: ['35'] }
	};
	const LILAS: ResultatRechercheAdresse = {
		type_resultat: 'adresse',
		ban_id: '85191_0420_00012',
		numero: '12',
		rep: null,
		nom_voie: 'Rue des Lilas',
		code_postal: '85000',
		code_commune: '85191',
		nom_commune: 'La Roche-sur-Yon',
		departement: { code: '85', nom: 'Vendée' }
	};
	const PAIX: ResultatRechercheAdresse = { ...LILAS, type_resultat: 'voie', ban_id: null, numero: null, nom_voie: 'Rue de la Paix' };
	const HEBERGE = { etat: 'heberge' as const, motif: null, departements: ['85'] };

	function json(body: unknown, status = 200) {
		return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
	}

	/** One fetch answering the two searches by path, as the API would. */
	function api(communes: Response | (() => Response), adresses: Response | (() => Response)) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const url = new URL(String(input), 'http://origin.invalid');
			const reponse = url.pathname === '/v1/adresses/recherche' ? adresses : communes;
			return typeof reponse === 'function' ? reponse() : reponse;
		});
	}

	async function chargerQ(q: string, fetch: typeof globalThis.fetch, departement = '') {
		const url = new URL('https://fixture/communes');
		url.searchParams.set('q', q);
		if (departement) url.searchParams.set('departement', departement);
		return (await (
			load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>
		)({ url, fetch })) as CommunesPageData;
	}

	const okCommunes = (donnees: CommuneSearchResult[]) => json({ statut: 'ok', donnees });
	const okAdresses = (
		resultats: ResultatRechercheAdresse[],
		ordre: string[],
		ban: unknown = HEBERGE,
		correspondanceNumero: unknown = null
	) => json({ statut: 'ok', donnees: { ordre_types: ordre, resultats, hebergement: { ban }, correspondance_numero: correspondanceNumero } });

	it('loads both searches in parallel, the departement filter on each, and keeps the served order', async () => {
		const fetch = api(okCommunes([LE_PIN]), okAdresses([LILAS], ['adresse', 'commune']));

		const data = await chargerQ('12 rue des Lilas', fetch, '85');

		expect(fetch).toHaveBeenCalledTimes(2);
		const urls = fetch.mock.calls.map(([input]) => new URL(String(input), 'http://origin.invalid'));
		const adresses = urls.find((url) => url.pathname === '/v1/adresses/recherche');
		expect(adresses?.searchParams.get('q')).toBe('12 rue des Lilas');
		expect(adresses?.searchParams.get('departement')).toBe('85');
		expect(adresses?.searchParams.get('limite')).toBe('8');
		expect(data.ordre).toEqual(['adresse', 'commune']);
		expect(data.adresses).toEqual({ etat: 'ok', resultats: [LILAS], hebergement: HEBERGE, correspondanceNumero: null });
	});

	it('passes the served number qualification through the loader without parsing the query', async () => {
		const qualification = {
			etat: 'proches',
			numero_demande: 12,
			suffixe_demande: 'bis',
			portee: 'resultats_retournes'
		};
		const data = await chargerQ(
			'query opaque',
			api(okCommunes([]), okAdresses([LILAS], ['adresse', 'commune'], HEBERGE, qualification))
		);

		expect(data.adresses?.etat === 'ok' ? data.adresses.correspondanceNumero : null).toEqual(qualification);
	});

	it('two h2 groups in the served order; compact address rows keep API rank and one primary link', async () => {
		const TREIZE = { ...LILAS, ban_id: '85191_0420_00013', numero: '13' };
		const data = await chargerQ('12 rue des Lilas', api(okCommunes([LE_PIN]), okAdresses([TREIZE, LILAS], ['adresse', 'commune'])));
		const { getByRole, container } = render(Page, { props: { data } });

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Résultats pour « 12 rue des Lilas »');
		// 04.8-07 (UI-SPEC § 6.5): the groups are simple panels, each address a whole-row link (→).
		const groupes = [...container.querySelectorAll('section.panneau-simple > h2')].map((h2) => h2.textContent);
		expect(groupes).toEqual(['Adresses', 'Communes']);
		const lignes = [...container.querySelectorAll('section.panneau-simple')[0].querySelectorAll('.liste-liens li')];
		expect(lignes.map((ligne) => ligne.querySelector('a')?.getAttribute('href'))).toEqual([
			'/adresse/85191_0420_00013',
			'/adresse/85191_0420_00012'
		]);
		const carte = lignes[1];
		expect(carte?.querySelector('.lien-t')?.textContent).toBe('12 Rue des Lilas');
		expect(carte?.querySelector('.fleche')?.textContent).toBe('→');
		expect(carte?.querySelector('.fleche')?.getAttribute('aria-hidden')).toBe('true');
		expect(carte?.querySelector('.lien-n')?.textContent).toMatch(/^Adresse · /);
		expect(carte?.textContent).toContain('85000 La Roche-sur-Yon · Vendée\u00a0(85)');
		expect(carte?.querySelectorAll('a')).toHaveLength(1);
		// D11 (2026-11-10): the panel « Communes » does not count again what the state line above says.
		const communes = container.querySelectorAll('section.panneau-simple')[1];
		expect(communes.querySelector('.communes-count')).toBeNull();
		expect(container.querySelector('p.r-etat[role="status"]')?.textContent).toMatch(/proposée?s?$/);
	});

	// D11 (2026-11-10): the INSEE code tells homonyms apart (D-43); a name found once does without it.
	it('writes the INSEE code of a commune only when another result bears its name', async () => {
		const homonyme = { ...LE_PIN, code_insee: '79211', code_postal: null, departement: '79' };
		const data = await chargerQ('le pin', api(okCommunes([LE_PIN, homonyme]), okAdresses([], ['commune', 'adresse'])));
		const { container } = render(Page, { props: { data } });
		const notes = [...container.querySelectorAll('li.resultat .lien-n')].map((n) => norm(n.textContent));
		expect(notes[0]).toContain('INSEE 35999');
		expect(notes[1]).toContain('INSEE 79211');
		const seul = await chargerQ('le pin', api(okCommunes([LE_PIN]), okAdresses([], ['commune', 'adresse'])));
		document.body.innerHTML = '';
		const rendu = render(Page, { props: { data: seul } });
		expect(norm(rendu.container.querySelector('li.resultat .lien-n')?.textContent)).not.toContain('INSEE 35999');
	});

	it('names address, street and mixed groups from returned result types', async () => {
		const mixed = await chargerQ('opaque', api(okCommunes([]), okAdresses([LILAS, PAIX], ['adresse', 'commune'])));
		const { container } = render(Page, { props: { data: mixed } });
		expect(container.querySelector('section.panneau-simple > h2')?.textContent).toBe('Adresses et voies');
		// The overline is gone (UI-SPEC § 6.5): the state line counts each served type.
		expect(container.querySelector('p.r-etat[role="status"]')?.textContent).toBe('1 adresse et 1 voie proposées');
	});

	it('a street served before an address keeps its served rank in the group (D-106)', async () => {
		const data = await chargerQ('opaque', api(okCommunes([]), okAdresses([PAIX, LILAS], ['adresse', 'commune'])));
		const { container } = render(Page, { props: { data } });
		const lignes = [...container.querySelectorAll('section.panneau-simple li')].map((li) => li.querySelector('.lien-t')?.textContent);
		expect(lignes).toEqual(['Rue de la Paix', '12 Rue des Lilas']);
	});

	it('communes first when served so; an empty group is not shown', async () => {
		const data = await chargerQ('le pin', api(okCommunes([LE_PIN]), okAdresses([], ['commune', 'adresse'])));
		const { container } = render(Page, { props: { data } });

		const groupes = [...container.querySelectorAll('section.panneau-simple > h2')].map((h2) => h2.textContent);
		expect(groupes).toEqual(['Communes']);
		expect(container.querySelector('li[data-code-insee="35999"] a[href="/commune/35999"]')).not.toBeNull();
	});

	it('a street card says « Précisez le numéro » and links to no address page (D-107)', async () => {
		const data = await chargerQ('rue de la Paix', api(okCommunes([]), okAdresses([PAIX], ['adresse', 'commune'])));
		const { container } = render(Page, { props: { data } });

		const groupe = container.querySelector('section.panneau-simple');
		const voie = groupe?.querySelector('li.voie');
		expect(voie?.querySelector('.lien-t')?.textContent).toBe('Rue de la Paix');
		expect(voie?.textContent).toContain('Précisez le numéro');
		expect(groupe?.querySelector('a[href^="/adresse/"]')).toBeNull();
	});

	it('both empty: the « Aucun résultat » contract, never silent', async () => {
		const data = await chargerQ('zzzz', api(okCommunes([]), okAdresses([], ['commune', 'adresse'])));
		const { container } = render(Page, { props: { data } });

		// D8 (2026-11-10, agreed by the orchestrator): the Notice of the fiche, its title announced by its status role.
		const encart = container.querySelector('.communes-notice');
		expect(encart?.querySelector('.notice:not(.notice-warn)[role="status"] > h2.notice-title')?.textContent).toBe('Aucune commune ni adresse trouvée pour « zzzz »');
		expect(encart?.textContent).toContain(
			"Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, ajoutez la commune ou le code postal, ou retirez le filtre département."
		);
		expect(container.querySelectorAll('section.panneau')).toHaveLength(0);
		expect(container.querySelectorAll('.liste-liens, .resultats')).toHaveLength(0);
	});

	it('an address search in outage is the typed state of its group; the communes stay', async () => {
		const data = await chargerQ('le pin', api(okCommunes([LE_PIN]), () => json({ title: 'x', status: 503, code: 'geocodage_indisponible' }, 503)));
		const { container, getByRole } = render(Page, { props: { data } });

		expect(data.adresses).toEqual({ etat: 'panne', panne: { statut: 503, titre: null, message: null } });
		const alerte = getByRole('alert');
		expect(alerte.textContent).toContain("La recherche d'adresses est momentanément indisponible.");
		expect(alerte.textContent).toContain('Relancer la recherche');
		expect(alerte.textContent).not.toContain('Aucune');
		expect(container.querySelector('li[data-code-insee="35999"]')).not.toBeNull();
		// The failed half is not counted: the state counts the communes only.
		expect(container.querySelector('p.r-etat[role="status"]')?.textContent).toBe('1 commune proposée');
	});

	it('a commune search in outage is the typed state of its group; the addresses stay', async () => {
		const data = await chargerQ('12 rue des Lilas', api(() => json({ detail: 'x' }, 503), okAdresses([LILAS], ['adresse', 'commune'])));
		const { container, getByRole } = render(Page, { props: { data } });

		expect(getByRole('alert').textContent).toContain('La recherche de communes est momentanément indisponible.');
		expect(container.querySelector('a[href="/adresse/85191_0420_00012"]')).not.toBeNull();
	});

	it('addresses out of coverage: a warn notice with the served territory, never a zero (D-105)', async () => {
		const ban = {
			etat: 'hors_couverture',
			motif: 'territoire_hors_perimetre',
			departements: [],
			territoire: { code_insee: '35238', nom: 'Rennes', departement: { code: '35', nom: 'Ille-et-Vilaine' } }
		};
		const data = await chargerQ('1 rue de la Gare Rennes', api(okCommunes([]), okAdresses([], ['adresse', 'commune'], ban)));
		const { container } = render(Page, { props: { data } });

		const encart = container.querySelector('.communes-notice-warn');
		expect(encart?.querySelector('.notice-title')?.textContent).toBe(
			'Rennes\u00a0(35)\u00a0: adresses hors couverture'
		);
		expect(encart?.textContent).toContain('Une absence de couverture ne vaut pas zéro.');
		expect(encart?.querySelector('.notice')?.getAttribute('role')).toBe('status');
		expect(encart?.querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		expect(container.textContent).not.toContain('Aucune commune ni adresse');
	});

	// C22 (Ianis, 2026-10-08): a postal code out of coverage keeps the phase 3 title, even when the
	// address search answers out of coverage too (real data, 04-20: « Gironde (33) : adresses… »).
	it('a postal code out of coverage for both searches: one warn notice, the phase 3 title', async () => {
		const horsBan = { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] };
		const banGironde = { ...horsBan, territoire: { code_insee: null, nom: null, departement: { code: '33', nom: 'Gironde' } } };
		const data = await chargerQ(
			'33000',
			api(json({ statut: 'ok', donnees: [], meta: { hebergement: { ban: horsBan } } }), okAdresses([], ['commune', 'adresse'], banGironde))
		);
		const { container } = render(Page, { props: { data } });

		const resultats = container.querySelector('section[aria-label="Résultats"]');
		const titres = resultats?.querySelectorAll('h2') ?? [];
		expect(titres).toHaveLength(1);
		expect(titres[0].textContent).toBe(TITRE_CODE_POSTAL_HORS_COUVERTURE);
		expect(titres[0].closest('.communes-notice')?.classList.contains('communes-notice-warn')).toBe(true);
		// 04.8-07 (UI-SPEC § 6.5): out of coverage is the announced state of the search, never a zero.
		expect(titres[0].closest('[role="status"]')).not.toBeNull();
		expect(resultats?.textContent?.replace(/\s+/g, ' ')).toContain(
			'Ce site ne couvre pas ce département. Cherchez la commune par son nom.'
		);
		expect(resultats?.querySelector('a[href="/datasets"]')?.textContent).toBe(LIEN_COUVERTURE);
		expect(resultats?.textContent).not.toContain('Gironde');
		expect(resultats?.textContent).not.toContain('Aucune commune');
	});

	it('both in outage: one page-level notice, as before', async () => {
		const data = await chargerQ('le pin', api(() => json({ detail: 'x' }, 503), () => json({ detail: 'x' }, 503)));
		const { getAllByRole } = render(Page, { props: { data } });

		expect(getAllByRole('alert')).toHaveLength(1);
	});
});

describe('/communes?q= — une recherche vide propose une suite (04.7-14, R16)', () => {
	const NANTES: CommuneSearchResult = {
		code_insee: '44109',
		nom: 'Nantes',
		code_postal: '44000',
		departement: '44',
		population: 320732,
		population_annee: 2022,
		population_source: 'demographie',
		hebergement: { etat: 'heberge', motif: null, departements: ['44'] }
	};
	const json = (body: unknown, status = 200) =>
		new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
	const banHebergee = { etat: 'heberge', motif: null, departements: ['44'] };

	/** Communes answered by name prefix from *table*; addresses always empty. */
	function api(table: Record<string, CommuneSearchResult[]>, panne = false) {
		return vi.fn(async (input: RequestInfo | URL) => {
			const url = new URL(String(input), 'http://origin.invalid');
			if (url.pathname === '/v1/adresses/recherche') {
				return json({ statut: 'ok', donnees: { resultats: [], ordre_types: ['commune', 'adresse'], hebergement: { ban: banHebergee } } });
			}
			if (panne && url.searchParams.get('q') !== 'Nantesss') return json({ detail: 'x' }, 503);
			return json({ statut: 'ok', donnees: table[url.searchParams.get('q') ?? ''] ?? [] });
		});
	}

	async function chargerQ(q: string, fetch: typeof globalThis.fetch) {
		const url = new URL('https://fixture/communes');
		url.searchParams.set('q', q);
		return (await (load as unknown as (event: { url: URL; fetch: typeof fetch }) => Promise<CommunesPageData>)({
			url,
			fetch
		})) as CommunesPageData;
	}

	it('lit les communes au nom proche par un préfixe raccourci, et les donne comme telles', async () => {
		const fetch = api({ Nantes: [NANTES] });
		const data = await chargerQ('Nantesss', fetch as unknown as typeof globalThis.fetch);
		expect(data.results).toEqual([]);
		expect(data.suite?.proches).toEqual({ prefixe: 'Nantes', communes: [NANTES] });
		const { container } = render(Page, { props: { data } });
		const texte = norm(container.textContent);
		expect(texte).toContain(norm('Communes dont le nom commence par « Nantes »'));
		expect(container.querySelector('a[href="/commune/44109"]')?.textContent).toBe('Nantes');
		// 04.8-07 (D-34): then the generic entries of the home page, no place written.
		expect(texte).toContain('Ou repartir d’une page du site');
		expect(container.querySelector('.communes-suite-liste a[href="/departements"]')).not.toBeNull();
	});

	it('propose l’autre orthographe en lien de recherche', async () => {
		const data = await chargerQ('St Herblain', api({}) as unknown as typeof globalThis.fetch);
		expect(data.suite?.orthographes).toEqual(['Saint Herblain']);
		const { container } = render(Page, { props: { data } });
		const lien = [...container.querySelectorAll('.communes-suite a')].find((a) => a.textContent === 'Saint Herblain');
		expect(lien?.getAttribute('href')).toBe('/communes?q=Saint+Herblain');
	});

	it('ne lit aucune suite quand des communes sont trouvées', async () => {
		const fetch = api({ Nantes: [NANTES] });
		const data = await chargerQ('Nantes', fetch as unknown as typeof globalThis.fetch);
		expect(data.suite).toBeNull();
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('une lecture de suite en panne ne propose rien, sans rien dire d’absent', async () => {
		const data = await chargerQ('Nantesss', api({}, true) as unknown as typeof globalThis.fetch);
		expect(data.panne).toBeNull();
		expect(data.suite?.proches).toBeNull();
		const { container } = render(Page, { props: { data } });
		expect(norm(container.textContent)).not.toContain('nom commence par');
	});
});

// 04.8-07 (UI-SPEC § 6.5): the form of the mockup over the real behaviour. Breadcrumb « Accueil ›
// Recherche », the h1, the large field, the directory filters folded in « Filtrer », the directory in a
// panel; no territory written in the template.
describe('/communes — forme v4 (04.8-07)', () => {
	it('without q: breadcrumb, the h1 of the directory, the large field and the filters in « Filtrer »', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]));
		const { container, getByRole } = render(Page, { props: { data } });

		const ariane = getByRole('navigation', { name: "Fil d'Ariane" });
		expect([...ariane.querySelectorAll('li')].map((li) => li.textContent)).toEqual(['Accueil', 'Annuaire des communes']);
		expect(ariane.querySelector('a')?.getAttribute('href')).toBe('/');
		expect(ariane.querySelector('[aria-current="page"]')?.textContent).toBe('Annuaire des communes');
		// D4 (4.9 lot 7): the h1 names what the page is, and the panel under it does not repeat it.
		expect(getByRole('heading', { level: 1 }).textContent).toBe('Annuaire des communes');
		expect(getByRole('heading', { level: 2, name: 'Liste des communes' })).toBeTruthy();
		// The directory is the indexable view: its document keeps its name.
		expect(document.title).toBe('Annuaire des communes — DataCommune');
		expect(container.querySelector('form.cs-grand')).not.toBeNull();
		const filtrer = container.querySelector('details.det');
		expect(filtrer?.querySelector('summary')?.textContent).toBe('Filtrer');
		expect(filtrer?.querySelector('form.annuaire-filtres[action="/communes"][method="get"]')).not.toBeNull();
		expect(filtrer?.hasAttribute('open')).toBe(false);
		expect(container.querySelector('section.panneau table.annuaire-table')).not.toBeNull();
		expect(container.querySelector('.communes-count[role="status"]')).not.toBeNull();
	});

	// 4.9 lot 1 (C5.3): the period is said once, in the caption read with the table.
	// T1, T2, T3 (2026-11-10): the card of a shared link, the buttons and the fold of the socle.
	it('carries the card of a shared link, each tag once, its canonical keeping the query', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]));
		render(Page, { props: { data } });
		const contenu = (selecteur: string) => [...document.head.querySelectorAll(selecteur)].map((m) => m.getAttribute('content'));
		expect(contenu('meta[name="twitter:card"]')).toEqual(['summary_large_image']);
		expect(contenu('meta[property="og:title"]')).toEqual(['Annuaire des communes — DataCommune']);
		expect(contenu('meta[name="description"]')).toHaveLength(1);
		expect(document.head.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
		expect(contenu('meta[property="og:url"]')).toEqual([document.head.querySelector('link[rel="canonical"]')?.getAttribute('href')]);
	});

	// Provenance by channel (2026-11-11): `?src=<canal>` is no part of the address of the page.
	it('opened by a channel link, the canonical and the card address drop the channel and keep the rest', async () => {
		const { page: magasin } = (await import('$app/stores')) as unknown as { page: { set: (v: { url: URL }) => void } };
		magasin.set({ url: new URL('https://fixture/communes?departement=35&src=reddit') });
		try {
			const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]), { departement: '35' });
			render(Page, { props: { data } });
			const canonique = document.head.querySelector('link[rel="canonical"]')?.getAttribute('href') ?? '';
			expect(new URL(canonique).pathname + new URL(canonique).search).toBe('/communes?departement=35');
			expect(document.head.querySelector('meta[property="og:url"]')?.getAttribute('content')).toBe(canonique);
		} finally {
			magasin.set({ url: new URL('https://fixture/communes?q=nantes') });
		}
	});

	it('takes the buttons and the fold of the socle, no house button left', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]), { departement: '35' });
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('.communes-button')).toBeNull();
		const filtrer = container.querySelector('details.det')!;
		expect(filtrer.classList.contains('depliant')).toBe(true);
		expect(filtrer.classList.contains('depliant-cadre')).toBe(true);
		const afficher = [...container.querySelectorAll('form.annuaire-filtres button')].find((b) => b.textContent?.trim() === 'Afficher')!;
		expect(afficher.classList.contains('bouton-primaire')).toBe(true);
		const effacer = [...container.querySelectorAll('form.annuaire-filtres a')].find((a) => a.textContent?.trim() === 'Effacer les filtres')!;
		expect(effacer.classList.contains('bouton-secondaire')).toBe(true);
	});

	it('the period of the prices is said once, in the caption', () => {
		const { container } = render(Page, { props: { data: pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })])) } });
		const texte = container.textContent!.replace(/\s+/g, ' ');
		expect(texte.match(/ventes retenues \(DVF\) du/g)).toHaveLength(1);
		expect(container.querySelector('table.annuaire-table caption')?.textContent).toContain('ventes retenues (DVF) du');
	});

	it('an active filter opens « Filtrer », so the filtered state stays in view', () => {
		const data = pageAnnuaire(reponseAnnuaire([communeAnnuaire({ code_insee: '35238', nom: 'Rennes' })]), { departement: '35' });
		const { container } = render(Page, { props: { data } });
		expect(container.querySelector('details.det')?.hasAttribute('open')).toBe(true);
	});

	it('a commune row is one link to its fiche, its served identity on the second line, the arrow hidden', async () => {
		const LE_PIN: CommuneSearchResult = {
			code_insee: '79211',
			nom: 'Le Pin',
			code_postal: null,
			departement: '79',
			population: 1000,
			population_annee: null,
			population_source: 'geo_communes',
			hebergement: { etat: 'hors_couverture', motif: 'territoire_hors_perimetre', departements: [] }
		} as unknown as CommuneSearchResult;
		const fetch = vi.fn(async (input: RequestInfo | URL) => {
			const url = new URL(String(input), 'http://origin.invalid');
			const body =
				url.pathname === '/v1/communes/search'
					? { statut: 'ok', donnees: [LE_PIN] }
					: { statut: 'ok', donnees: { ordre_types: ['commune', 'adresse'], resultats: [], hebergement: { ban: { etat: 'heberge', motif: null, departements: ['85'] } } } };
			return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
		});
		const data = (await (load as unknown as (event: { url: URL; fetch: typeof globalThis.fetch }) => Promise<CommunesPageData>)({
			url: new URL('http://localhost/communes?q=le+pin'),
			fetch: fetch as unknown as typeof globalThis.fetch
		})) as CommunesPageData;
		const { container, getByRole } = render(Page, { props: { data } });

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Résultats pour « le pin »');
		const ligne =container.querySelector('li[data-code-insee="79211"]');
		const liens = ligne?.querySelectorAll('a') ?? [];
		expect(liens).toHaveLength(1);
		expect(liens[0].getAttribute('href')).toBe('/commune/79211');
		expect(liens[0].querySelector('.lien-t')?.textContent).toBe('Le Pin');
		// D11 (2026-11-10): no homonym in the list, no INSEE code on the line.
		expect(norm(liens[0].querySelector('.lien-n')?.textContent ?? '')).toBe('Commune · Deux-Sèvres (79) · 1 000 habitants');
		expect(liens[0].querySelector('.fleche[aria-hidden="true"]')?.textContent).toBe('→');
		// Out of coverage: the reason stays under the row, never a zero.
		// D8 (2026-11-10): the badge of the state is the one of the fiche (StateValue), its label and sentence unchanged.
		expect(ligne?.querySelector('.resultat-etat [data-state="out_of_coverage"] .state-badge')?.textContent).toContain('Hors couverture');
		expect(ligne?.querySelector('.resultat-badge')).toBeNull();
		expect(ligne?.querySelector('.resultat-etat')?.textContent).toMatch(/\S/);
		expect(container.querySelector('p.r-etat[role="status"]')?.textContent).toBe('1 commune proposée');
	});

	it('SearchResult is no longer imported, and the template writes no territory', async () => {
		const source = (await import('./+page.svelte?raw')).default as string;
		expect(source).not.toMatch(/import\s+SearchResult\b/);
		const gabarit = source.slice(source.indexOf('</script>'), source.indexOf('<style>'));
		expect(gabarit).not.toMatch(/Loire-Atlantique|Vendée|Nantes|Luçon/);
	});
});
