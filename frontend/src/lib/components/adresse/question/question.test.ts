// Rows, head and categories of the address fiche (04.8-03, reading of 06/11; UI-SPEC v4 § 3.3, § 5, § 7.1;
// CADRAGE R1 to R10; D-08, D-11, D-32, D-33, D-37, D-40). What is served is what is shown: title, verdict,
// attachment alert (French spacing at render time), headline as served, badge on statutDe(), the
// served reason, the partial-outage note; the detail is a native <details> opened by its anchor.
// Rendered with Testing Library, like CarteAdresse.test.ts.
import { createRawSnippet, tick } from 'svelte';
import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChiffreCleRapport, GranulariteRapport, ResumeServi, ThemeRapport } from '$lib/api/rapport';
import ChiffresCles from '$lib/components/adresse/rapport/ChiffresCles.svelte';
import Sommaire from '$lib/components/adresse/rapport/Sommaire.svelte';
import { placerThemes, type ArgsCritere } from '$lib/data/questions';
import LigneCritere from './LigneCritere.svelte';
import PartieQuestion from './PartieQuestion.svelte';
import BilanRisques from './BilanRisques.svelte';
import EnBref from './EnBref.svelte';
import { lireResume } from '$lib/data/adresse-rapport';

const NBSP = String.fromCharCode(0xa0);
const NNBSP = String.fromCharCode(0x202f);

const g = (niveau: string, rayon_m: number | null = null): GranulariteRapport => ({ niveau, rayon_m, arrondissement: null });

const SOURCE = {
	cle: 'georisques',
	nom: 'Géorisques',
	producteur: 'BRGM',
	url: 'https://www.georisques.gouv.fr',
	licence: 'Licence Ouverte 2.0',
	millesime: '2026-03',
	mode: 'direct' as const
};

/** A served theme; every field can be overridden. */
function theme(id: string, extra: Partial<ThemeRapport> = {}): ThemeRapport {
	return {
		id,
		section: 'risques',
		titre: `Titre ${id}`,
		etat: 'ok',
		ton: 'neutre',
		granularite: g('adresse'),
		verdict: `Verdict ${id}.`,
		chiffres: [],
		details: {},
		rattachement: null,
		sources: [],
		motif: null,
		etat_libelle: null,
		accroche: null,
		pannes_partielles: [],
		...extra
	};
}

const resumeVide = (): ResumeServi => ({
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
	parSection: {
		batiment: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		risques: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		marche: { vigilance: 0, pannes: 0, lus: 0, total: 0 },
		vivre: { vigilance: 0, pannes: 0, lus: 0, total: 0 }
	}
});

const li = (c: HTMLElement, id: string) => c.querySelector(`li#critere-${id}`) as HTMLElement;

describe('A3 — the sector title, now in the block of « Risques »', () => {
	it.each([
		['commune', 'Vigilances pour toute la commune'],
		['epci', 'Vigilances pour un périmètre plus large que l’adresse'],
		['rayon', 'Vigilances pour un périmètre plus large que l’adresse']
	])('names the perimeter served for %s', (niveau, titre) => {
		const t = theme('radon', { granularite: g(niveau, niveau === 'rayon' ? 5000 : null), ton: 'vigilance' });
		const servi = { ...resumeVide(), pointsSecteur: ['radon'], lus: 1, lusSecteur: 1 };
		const { container } = render(BilanRisques, { props: { resume: lireResume(servi, [t])! } });
		expect(container.querySelector('h4')?.textContent).toBe(titre);
		expect(container.querySelector('a[href="#critere-radon"]')).not.toBeNull();
	});

	it('keeps the communal title for an empty served list, without claiming a reading', () => {
		const { container } = render(BilanRisques, { props: { resume: lireResume(resumeVide(), [])! } });
		expect(container.querySelector('h4')?.textContent).toBe('Vigilances pour toute la commune');
		expect(container.querySelector('.bref-vide')?.textContent).toBe('Aucun critère lu à cette échelle.');
	});
});

afterEach(() => {
	window.location.hash = '';
	vi.restoreAllMocks();
});

describe('LigneCritere — la ligne d’un critère (§ 3.3)', () => {
	it('renders li#critere-{id} with its id, state and scale classes, the title in an h4 under a titled group and in an h3 otherwise', () => {
		const t = theme('inondation', { etat: 'ok', ton: 'vigilance' });
		const { container, unmount } = render(LigneCritere, { props: { theme: t, niveauTitre: 4 } });
		const ligne = li(container, 'inondation');
		expect(ligne).not.toBeNull();
		expect(ligne.getAttribute('data-theme')).toBe('inondation');
		expect(ligne.getAttribute('data-etat')).toBe('ok');
		expect(ligne.classList.contains('ligne-vigilance')).toBe(true);
		expect(ligne.classList.contains('is-calme')).toBe(false);
		expect(ligne.classList.contains('is-commune')).toBe(false);
		expect(ligne.querySelector('h4.ligne-titre')?.textContent).toBe('Titre inondation');
		unmount();
		const { container: c3 } = render(LigneCritere, { props: { theme: t, niveauTitre: 3 } });
		expect(li(c3, 'inondation').querySelector('h3.ligne-titre')?.textContent).toBe('Titre inondation');
		expect(li(c3, 'inondation').querySelector('h4')).toBeNull();
	});

	it('passes the title and the verdict through typographieFr (D-37), nothing else', () => {
		const t = theme('altitude', {
			titre: 'Altitude : le point',
			verdict: 'Altitude du sol au point de l’adresse : 21 m.'
		});
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3 } });
		expect(container.querySelector('.ligne-titre')?.textContent).toBe(`Altitude${NBSP}: le point`);
		expect(container.querySelector('.ligne-phrase')?.textContent).toBe(`Altitude du sol au point de l’adresse${NBSP}: 21${NBSP}m.`);
	});

	it('shows the served attachment alert with French spacing, only when the link is not sure', () => {
		const rattachement = (confiance: 'sure' | 'incertaine') => ({
			entite: 'parcelle',
			cle: null,
			confiance,
			candidats: null,
			motif: null,
			alerte: 'Plusieurs bâtiments possibles : le bâtiment lu peut ne pas être le vôtre !',
			maillons: null
		});
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('batiment', { rattachement: rattachement('incertaine') }), niveauTitre: 3 } });
		expect(container.querySelector('.ligne-rat')?.textContent).toBe(`Plusieurs bâtiments possibles${NBSP}: le bâtiment lu peut ne pas être le vôtre${NNBSP}!`);
		unmount();
		const { container: sur } = render(LigneCritere, { props: { theme: theme('batiment', { rattachement: rattachement('sure') }), niveauTitre: 3 } });
		expect(sur.querySelector('.ligne-rat')).toBeNull();
	});

	it('shows the headline as served and never cuts it; nothing when it is null', () => {
		const quarante = 'x'.repeat(40);
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('altitude', { accroche: quarante }), niveauTitre: 3 } });
		expect(container.querySelector('.val')?.textContent).toBe(quarante);
		unmount();
		const { container: sans } = render(LigneCritere, { props: { theme: theme('altitude', { accroche: null }), niveauTitre: 3 } });
		expect(sans.querySelector('.val')).toBeNull();
	});

	it('wears the full badge of a vigilance and the word of statutDe() for the other states', () => {
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('a', { ton: 'vigilance' }), niveauTitre: 3 } });
		const badge = container.querySelector('.badge') as HTMLElement;
		expect(badge.textContent).toBe('Vigilance');
		expect(badge.classList.contains('badge-plein')).toBe(true);
		expect(badge.classList.contains('badge-contour')).toBe(false);
		unmount();
		const { container: panne } = render(LigneCritere, { props: { theme: theme('b', { etat: 'panne', ton: 'info', verdict: 'La source n’a pas répondu.' }), niveauTitre: 3 } });
		expect(panne.querySelector('.badge')?.textContent).toBe('Source indisponible');
		// A neutral served state has no badge in the DOM at all.
		const { container: neutre } = render(LigneCritere, { props: { theme: theme('c', { ton: 'neutre' }), niveauTitre: 3 } });
		expect(neutre.querySelector('.badge')).toBeNull();
	});

	it('keeps a favourable or not-applicable row calm and compact, a row of absence full', () => {
		const calme = (t: ThemeRapport) => render(LigneCritere, { props: { theme: t, niveauTitre: 3 } }).container.querySelector('li')!;
		expect(calme(theme('a', { ton: 'rassurant' })).classList.contains('is-calme')).toBe(true);
		expect(calme(theme('b', { etat: 'non_concerne', ton: 'neutre' })).classList.contains('is-calme')).toBe(true);
		expect(calme(theme('c', { etat: 'panne', ton: 'info' })).classList.contains('is-calme')).toBe(false);
		expect(calme(theme('d', { etat: 'manque', ton: 'info' })).classList.contains('is-calme')).toBe(false);
		expect(calme(theme('e', { etat: 'hors_couverture', ton: 'info' })).classList.contains('is-calme')).toBe(false);
	});

	it('shows the served reason on its own line when it differs from the word of the badge', () => {
		const long = 'La source ne publie pas cette donnée pour cette commune';
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('a', { etat: 'hors_couverture', ton: 'info', etat_libelle: long }), niveauTitre: 3 } });
		expect(container.querySelector('.badge')?.textContent).toBe('Hors couverture');
		expect(container.querySelector('.ligne-etat')?.textContent).toBe(long);
		unmount();
		// A short reason is the word of the badge itself: it is not written twice.
		const { container: court } = render(LigneCritere, { props: { theme: theme('b', { etat: 'manque', ton: 'info', etat_libelle: 'Non publié' }), niveauTitre: 3 } });
		expect(court.querySelector('.badge')?.textContent).toBe('Non publié');
		expect(court.querySelector('.ligne-etat')).toBeNull();
	});

	it('says a partial outage: « Une partie des sources n’a pas répondu : {sources} »', () => {
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('a', { pannes_partielles: ['statut_adresse', 'rnb'] }), niveauTitre: 3 } });
		expect(container.querySelector('.ligne-etat')?.textContent).toBe(`Une partie des sources n’a pas répondu${NBSP}: statut à l’adresse, registre national des bâtiments`);
		unmount();
		const { container: sans } = render(LigneCritere, { props: { theme: theme('b'), niveauTitre: 3 } });
		expect(sans.querySelector('.ligne-etat')).toBeNull();
	});

	it('marks the scale with a pictogram and its visible word, long words wrapping in the column', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('transports', { granularite: g('rayon', 500) }), niveauTitre: 3 } });
		const mot = container.querySelector('.ech-mot') as HTMLElement;
		expect(mot.textContent).toBe(`500${NBSP}m autour`);
		expect(mot.classList.contains('sr')).toBe(false);
		expect(container.querySelector('.ech svg[aria-hidden="true"]')).not.toBeNull();
	});

	it('renders the DPE distribution as served, as the scale A to G (counts of documents), for the dpe theme only', () => {
		const repartition = { A: 0, B: 0, C: 1, D: 2, E: 2, F: 4, G: 2, unknown: 0 };
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('dpe', { details: { repartition } }), niveauTitre: 3 } });
		const echelle = container.querySelector('.ligne-dpe ol') as HTMLElement;
		expect(echelle).not.toBeNull();
		const cases = [...echelle.querySelectorAll('li')];
		expect(cases).toHaveLength(7);
		// Read « Classe C : 1 DPE »: the boxes show the letter and the served count, documents and not dwellings.
		const attendus = [0, 0, 1, 2, 2, 4, 2];
		cases.forEach((c, i) => {
			const lettre = 'ABCDEFG'[i];
			expect(c.classList.contains(`dpe-${lettre}`)).toBe(true);
			expect(c.textContent).toMatch(new RegExp(`Classe\\s*${lettre}\\s*:\\s*${attendus[i]}\\s*DPE`));
		});
		unmount();
		const { container: autre } = render(LigneCritere, { props: { theme: theme('inondation', { details: { repartition } }), niveauTitre: 3 } });
		expect(autre.querySelector('.ligne-dpe')).toBeNull();
		const { container: sans } = render(LigneCritere, { props: { theme: theme('dpe', { etat: 'zero', details: {} }), niveauTitre: 3 } });
		expect(sans.querySelector('.ligne-dpe')).toBeNull();
	});
});

describe('LigneCritere — le détail natif (§ 3.3, § 3.10)', () => {
	it('is a native details « Détail, échelle et source » + « : {titre} » for screen readers, closed by default', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('altitude', { titre: 'Altitude' }), niveauTitre: 3 } });
		const det = container.querySelector('details.det') as HTMLDetailsElement;
		expect(det.open).toBe(false);
		const resume = det.querySelector('summary') as HTMLElement;
		expect(resume.textContent).toBe(`Détail, échelle et source${NBSP}: Altitude`);
		// An « i » alone on screen: the whole name is read by screen readers.
		expect(resume.querySelector('span.sr')?.textContent).toBe(`Détail, échelle et source${NBSP}: Altitude`);
		expect(resume.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true');
	});

	it('opens on arrival at #critere-{id} and on hashchange, and only for its own anchor', async () => {
		window.location.hash = '#critere-altitude';
		const { container } = render(LigneCritere, { props: { theme: theme('altitude'), niveauTitre: 3 } });
		expect((container.querySelector('details') as HTMLDetailsElement).open).toBe(true);
		const { container: autre } = render(LigneCritere, { props: { theme: theme('argiles'), niveauTitre: 3 } });
		const det = autre.querySelector('details') as HTMLDetailsElement;
		expect(det.open).toBe(false);
		window.location.hash = '#critere-argiles';
		await fireEvent(window, new HashChangeEvent('hashchange'));
		expect(det.open).toBe(true);
	});

	it('starts open when asked (the page binds it for the DPE and the sales)', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('dpe'), niveauTitre: 3, ouvert: true } });
		expect((container.querySelector('details') as HTMLDetailsElement).open).toBe(true);
	});

	it('holds the served figures, the reason, the generic details, the lists of the page, the long form of the scale', () => {
		const t = theme('transports', {
			etat: 'manque',
			ton: 'info',
			etat_libelle: 'Aucune offre publiée',
			chiffres: [],
			granularite: g('rayon', 500),
			details: { mesure: { valeur: 3, unite: 'cm' } }
		});
		const suite = createRawSnippet(() => ({ render: () => '<p class="liste-de-la-page">Documents DPE de la page</p>' }));
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3, suite } });
		const interieur = container.querySelector('.det-in') as HTMLElement;
		expect(interieur.querySelector('.motif')?.textContent).toBe('Aucune offre publiée.');
		expect(interieur.textContent).toContain('Mesure');
		expect(interieur.querySelector('.liste-de-la-page')?.textContent).toBe('Documents DPE de la page');
		expect(interieur.querySelector('.det-ech')?.textContent).toBe(`Cette valeur vaut dans un rayon de 500${NBSP}m autour de l’adresse.`);
	});

	it('says the long form of the scale: address, commune, unknown level', () => {
		const dit = (granularite: GranulariteRapport) =>
			render(LigneCritere, { props: { theme: theme('x', { granularite }), niveauTitre: 3 } }).container.querySelector('.det-ech')?.textContent;
		expect(dit(g('adresse'))).toBe('Cette valeur vaut au point de l’adresse.');
		expect(dit(g('commune'))).toBe('Cette valeur vaut pour toute la commune.');
		expect(dit(g('niveau_futur'))).toContain('Cette valeur vaut pour l’échelle');
	});

	it('writes the served figures with their unit and date, a DPE class as a badge', () => {
		const t = theme('dpe', {
			chiffres: [
				{ id: 'effectif', libelle: 'DPE déclarés', valeur: 11, unite: 'count', millesime: '2026-10' },
				{ id: 'classe_dominante', libelle: 'Classe la plus fréquente', valeur: 'F', unite: 'classe_dpe', millesime: null }
			]
		});
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3 } });
		const lignes = [...container.querySelectorAll('.chiffres > div')];
		expect(lignes).toHaveLength(2);
		expect(lignes[0].querySelector('dt')?.textContent).toBe('DPE déclarés');
		expect(lignes[0].querySelector('dd')?.textContent).toBe('11');
		expect(lignes[0].textContent).toContain('octobre 2026');
		expect(lignes[1].querySelector('.dpe-badge.dpe-F')?.textContent).toBe('F');
	});

	it('says the attachment: confidence, entity, candidates, served links', () => {
		const t = theme('batiment', {
			granularite: g('batiment'),
			rattachement: {
				entite: 'plusieurs_batiments_sur_la_parcelle',
				cle: null,
				confiance: 'probable',
				candidats: 3,
				motif: null,
				alerte: null,
				maillons: [
					{ lien: 'parcelle', confiance: 'sure' },
					{ lien: 'batiment', confiance: 'probable' }
				]
			}
		});
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3 } });
		const rat = container.querySelector('.rattachement') as HTMLElement;
		expect(rat.textContent).toContain(`Rattachement probable${NBSP}:`);
		expect(rat.textContent).toContain('3 candidats');
		expect(rat.textContent).toContain(`Parcelle${NBSP}(sûr)`);
		expect(rat.textContent).toContain(`Batiment${NBSP}(probable)`);
		expect(rat.classList.contains('incertain')).toBe(true);
	});

	it('copies the identifier and says « Copié. » in a status region', async () => {
		const ecrire = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, 'clipboard', { value: { writeText: ecrire }, configurable: true });
		const t = theme('parcelle', {
			rattachement: { entite: 'parcelle', cle: '44109000AB0012', confiance: 'sure', candidats: null, motif: null, alerte: null, maillons: null }
		});
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3, ouvert: true } });
		expect(container.querySelector('.rattachement .chiffre')?.textContent).toBe('44109000AB0012');
		expect(screen.queryByRole('status')).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Copier l’identifiant 44109000AB0012' }));
		expect(ecrire).toHaveBeenCalledWith('44109000AB0012');
		expect((await screen.findByRole('status')).textContent).toBe('Copié.');
	});

	it('lists the sources: name as a link, producer, vintage or its absence, licence, mode', () => {
		const t = theme('inondation', {
			sources: [
				SOURCE,
				{ ...SOURCE, cle: 'dvf', nom: 'Demandes de valeurs foncières', producteur: 'DGFiP', url: '', millesime: null, mode: 'import' as const }
			]
		});
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 3 } });
		const sources = [...container.querySelectorAll('.dc-sources li')];
		expect(sources).toHaveLength(2);
		const lien = sources[0].querySelector('a') as HTMLAnchorElement;
		expect(lien.textContent).toBe('Géorisques');
		expect(lien.getAttribute('href')).toBe('https://www.georisques.gouv.fr');
		expect(lien.getAttribute('rel')).toBe('noopener noreferrer');
		expect(sources[0].textContent).toContain('BRGM · mars 2026 · Licence Ouverte 2.0 · lu en direct chez le producteur');
		expect(sources[1].querySelector('a')).toBeNull();
		expect(sources[1].textContent).toContain('année des données non indiquée par la source');
		expect(sources[1].textContent).toContain('copie importée par DataCommune');
	});
});

describe('LigneCritere — une valeur plus large que l’adresse (D-11, D-32, R8)', () => {
	it('stays the same row, the vigilance badge outlined (not filled), the value in ink', () => {
		const t = theme('catnat', { granularite: g('commune'), ton: 'vigilance' });
		const { container } = render(LigneCritere, { props: { theme: t, niveauTitre: 4 } });
		const ligne = li(container, 'catnat');
		expect(ligne.classList.contains('is-commune')).toBe(true);
		const badge = ligne.querySelector('.badge') as HTMLElement;
		expect(badge.textContent).toBe('Vigilance');
		expect(badge.classList.contains('badge-contour')).toBe(true);
		expect(badge.classList.contains('badge-plein')).toBe(false);
	});

	it.each([
		['a radius of 5 km', g('rayon', 5000), `5${NBSP}km autour`],
		['an IRIS', g('iris'), 'Quartier IRIS'],
		['an arrondissement', g('arrondissement'), 'Arrondissement'],
		['an intercommunality', g('epci'), 'Intercommunalité'],
		['a department', g('departement'), 'Département'],
		['a commune', g('commune'), 'Commune']
	])('keeps the word of %s beside its value', (_nom, granularite, mot) => {
		const { container } = render(LigneCritere, { props: { theme: theme('seveso', { granularite }), niveauTitre: 4 } });
		const visible = container.querySelector('.ligne-ech .ech-mot') as HTMLElement;
		expect(visible.textContent).toBe(mot);
		expect(visible.classList.contains('sr')).toBe(false);
	});

	it('shows an unknown level as « Échelle : {niveau} », without a pictogram and without error', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('x', { granularite: g('niveau_futur') }), niveauTitre: 4 } });
		expect(container.querySelector('.ech-mot')?.textContent).toBe(`Échelle${NBSP}: niveau_futur`);
		expect(container.querySelector('.ech svg')).toBeNull();
	});
});

describe('4.9 lot 5 — un état, une valeur, une raison par ligne', () => {
	it('a state without value is said once, by its badge, in the four states', () => {
		const cas: [ThemeRapport['etat'], string][] = [
			['panne', 'Source indisponible'],
			['manque', 'Non publié'],
			['hors_couverture', 'Hors couverture'],
			['non_concerne', 'Sans objet']
		];
		for (const [etat, mot] of cas) {
			const { container, unmount } = render(LigneCritere, { props: { theme: theme('a', { etat, ton: 'info', accroche: mot }), niveauTitre: 3 } });
			expect(container.querySelector('.badge')?.textContent, etat).toBe(mot);
			expect(container.querySelector('.val'), etat).toBeNull();
			unmount();
		}
	});

	it('a value keeps its headline beside its badge', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('a', { etat: 'zero', ton: 'rassurant', accroche: 'Aucun' }), niveauTitre: 3 } });
		expect(container.querySelector('.val')?.textContent).toBe('Aucun');
		expect(container.querySelector('.badge')?.textContent).toBe('Favorable');
	});

	it('A2: the reason of a « Non publié » goes to the detail, nothing lost; « Hors couverture » keeps it on the row', () => {
		const long = 'L’état des risques porte sur une autre parcelle';
		const { container, unmount } = render(LigneCritere, { props: { theme: theme('a', { etat: 'manque', ton: 'info', etat_libelle: long }), niveauTitre: 3 } });
		expect(container.querySelector('.ligne-etat')).toBeNull();
		expect(container.querySelector('.det .motif')?.textContent).toBe(`${long}.`);
		unmount();
		const { container: hors } = render(LigneCritere, { props: { theme: theme('b', { etat: 'hors_couverture', ton: 'info', etat_libelle: 'Source sans couverture' }), niveauTitre: 3 } });
		expect(hors.querySelector('.ligne-etat')?.textContent).toBe('Source sans couverture');
		expect(hors.querySelector('.det .motif')).toBeNull();
	});

	it('A5: the served sentence stays visible, even when it repeats the value (D-08)', () => {
		const { container } = render(LigneCritere, { props: { theme: theme('a', { verdict: 'Altitude du sol : 24 m.', accroche: '24 m' }), niveauTitre: 3 } });
		expect(container.querySelector('.ligne-phrase')?.textContent).toMatch(/24\sm\.$/);
		expect(container.querySelector('.val')?.textContent).toBe('24 m');
	});
});

describe('ChiffresCles — la réponse de tête, une valeur inconnue n’occupe pas une tuile (R1, R8, R9, D-33)', () => {
	const prix = (partiel: Partial<ChiffreCleRapport> = {}): ChiffreCleRapport => ({
		id: 'prix_m2_median',
		libelle: 'Prix médian au m²',
		theme: 'prix_commune',
		etat: 'ok',
		granularite: g('commune'),
		chiffres: [
			{ id: 'maison', libelle: 'Prix médian maisons', valeur: 4110, unite: 'euro_per_m2', millesime: null },
			{ id: 'appartement', libelle: 'Prix médian appartements', valeur: 3385, unite: 'euro_per_m2', millesime: null }
		],
		motif: null,
		...partiel
	});
	const loyer = (partiel: Partial<ChiffreCleRapport> = {}): ChiffreCleRapport => ({
		id: 'loyer_m2',
		libelle: 'Loyer au m²',
		theme: 'loyers',
		etat: 'ok',
		granularite: g('commune'),
		chiffres: [{ id: 'appartement', libelle: 'Loyer appartement', valeur: 14.5, unite: 'euro_per_m2_mois', millesime: null }],
		motif: null,
		...partiel
	});
	const dpe = (partiel: Partial<ChiffreCleRapport> = {}): ChiffreCleRapport => ({
		id: 'dpe_dominant',
		libelle: 'DPE dominant à l’adresse',
		theme: 'dpe',
		etat: 'ok',
		granularite: g('adresse'),
		chiffres: [{ id: 'classe_dominante', libelle: 'Classe la plus fréquente', valeur: 'D', unite: 'classe_dpe', millesime: null }],
		motif: null,
		...partiel
	});
	const annee: ChiffreCleRapport = { id: 'annee_construction', libelle: 'Année de construction', theme: 'construction', etat: 'manque', granularite: g('batiment'), chiffres: [], motif: 'annee_non_renseignee' };
	const rendre = (chiffres: ChiffreCleRapport[], themes: ThemeRapport[] = [], lienCommune?: string) => render(ChiffresCles, { props: { chiffres, themes, lienCommune } }).container;
	const tuiles = (c: HTMLElement) => [...c.querySelectorAll('dl.chiffres-cles > .cle')] as HTMLElement[];
	const HORS = theme('prix_commune', { etat: 'hors_couverture', ton: 'info', verdict: 'Source non couverte ici par DataCommune.' });

	it('answers with price, rent and DPE, each with its scale word; the build year leaves the head, even with a value', () => {
		const valeur = { ...annee, etat: 'ok' as const, chiffres: [{ id: 'annee', libelle: 'Année de construction', valeur: 1956, unite: 'annee', millesime: null }], motif: null };
		const c = rendre([prix(), loyer(), dpe(), valeur], [theme('dpe', { accroche: '5 DPE déclarés' })]);
		const lues = tuiles(c);
		expect(lues.map((t) => t.querySelector('dt')?.textContent)).toEqual(['Prix médian au m²', 'Loyer au m²', 'DPE dominant à l’adresse']);
		expect(lues.map((t) => t.querySelector('.ech-mot')?.textContent)).toEqual(['Commune', 'Commune', 'Adresse']);
		expect(c.textContent).not.toContain('1956');
		expect(c.textContent).not.toContain('Année de construction');
		expect(c.querySelector('.absences')).toBeNull();
	});

	it('keeps one order of the figures in price and rent, under their type', () => {
		const c = rendre([prix()]);
		expect([...c.querySelectorAll('.valeur')].map((v) => v.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['3 385 €/m² Appartement', '4 110 €/m² Maison']);
	});

	it('gives the DPE its class, the served count and the seven classes, the most frequent one marked', () => {
		const dpeTheme = theme('dpe', { accroche: '5 DPE déclarés', details: { repartition: { A: 0, B: 0, C: 1, D: 2, E: 1, F: 1, G: 0, unknown: 0 } } });
		const c = rendre([dpe()], [dpeTheme]);
		const t = tuiles(c)[0];
		expect(t.querySelector('.dpe-pastille')?.textContent).toBe('D');
		expect(t.textContent).toContain('Classe la plus fréquente');
		expect(t.textContent).toContain('5 DPE déclarés');
		expect([...t.querySelectorAll('.dpe-bande > span')].map((x) => x.textContent)).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G']);
		expect(t.querySelectorAll('.dpe-bande .dpe-dominante')).toHaveLength(1);
		expect(t.querySelector('.dpe-bande .dpe-dominante')?.textContent).toBe('D');
	});

	it('without any DPE: the served sentence and where to go next, never the look of an outage', () => {
		const zero = theme('dpe', { etat: 'zero', accroche: 'Aucun', verdict: 'Aucun DPE déclaré à cette adresse.' });
		const c = rendre([dpe({ etat: 'zero', chiffres: [] })], [zero], '/commune/85191');
		const t = tuiles(c)[0];
		expect(t.querySelector('.cle-zero')?.textContent).toBe('Aucun DPE déclaré à cette adresse.');
		expect(t.querySelector('a')?.getAttribute('href')).toBe('/commune/85191');
		expect(t.querySelector('a')?.textContent).toBe('Voir les DPE de la commune');
		expect(t.querySelector('.ech-mot')?.textContent).toBe('Adresse');
		expect(t.querySelector('.dpe-pastille')).toBeNull();
		expect(c.querySelector('.badge-panne')).toBeNull();
		// The sentence says it: the served word « Aucun » is not written again beside the scale.
		expect(t.querySelector('.cle-n')?.textContent).not.toContain('Aucun');
	});

	it('an outage of the DPE is not a zero: no tile, its word and its sentence on one line', () => {
		const panne = theme('dpe', { etat: 'panne', ton: 'info', verdict: 'Source momentanément indisponible.' });
		const c = rendre([prix(), dpe({ etat: 'panne', chiffres: [] })], [panne]);
		expect(tuiles(c)).toHaveLength(1);
		const ligne = c.querySelector('.absences li') as HTMLElement;
		expect(ligne.querySelector('.badge')?.textContent).toBe('Source indisponible');
		expect(ligne.textContent).toContain('DPE dominant à l’adresse');
		expect(ligne.textContent).toContain('Source momentanément indisponible.');
		expect(c.querySelector('.cle-zero')).toBeNull();
	});

	it('documents without a dominant class still answer: the served count and range, in a tile', () => {
		const plusieurs = theme('dpe', { accroche: '13 DPE déclarés', chiffres: [{ id: 'plage', libelle: 'Classes', valeur: 'C–E', unite: 'classe_dpe', millesime: null }] });
		const c = rendre([dpe({ etat: 'manque', chiffres: [], motif: 'chiffre_absent' })], [plusieurs]);
		expect(tuiles(c)[0].textContent).toContain('13 DPE déclarés');
		expect(tuiles(c)[0].textContent).toContain(`Classes${NBSP}C–E`);
		expect(c.querySelector('.absences')).toBeNull();
	});

	it('prices and rents out of coverage are one line, before the tiles, and never a suggested zero', () => {
		const hors = { etat: 'hors_couverture' as const, chiffres: [], motif: 'source_sans_couverture' };
		const c = rendre([prix(hors), loyer(hors), dpe({ etat: 'zero', chiffres: [] })], [HORS, { ...HORS, id: 'loyers' }, theme('dpe', { etat: 'zero', verdict: 'Aucun DPE déclaré à cette adresse.' })]);
		expect(tuiles(c)).toHaveLength(1);
		const lignes = [...c.querySelectorAll('.absences li')];
		expect(lignes).toHaveLength(1);
		expect(lignes[0].querySelector('.badge')?.textContent).toBe('Hors couverture');
		expect(lignes[0].textContent).toContain('Prix médian au m² et Loyer au m²');
		expect(lignes[0].textContent).toContain('Une absence de couverture ne vaut pas zéro.');
		expect(c.querySelector('.absences')?.compareDocumentPosition(c.querySelector('.chiffres-cles')!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
		expect(c.textContent).not.toMatch(/\b0\b/);
	});

	it('everything out of coverage: one line and no empty box', () => {
		const hors = { etat: 'hors_couverture' as const, chiffres: [], motif: 'source_sans_couverture' };
		const c = rendre([prix(hors), loyer(hors), dpe(hors)], [HORS, { ...HORS, id: 'loyers' }, { ...HORS, id: 'dpe' }]);
		expect(c.querySelector('.chiffres-cles')).toBeNull();
		expect(c.querySelectorAll('.absences li')).toHaveLength(1);
	});

	it('two states stay two lines: an outage never joins a gap', () => {
		const c = rendre([prix({ etat: 'panne', chiffres: [] }), loyer({ etat: 'hors_couverture', chiffres: [] })]);
		expect([...c.querySelectorAll('.absences .badge')].map((b) => b.textContent)).toEqual(['Source indisponible', 'Hors couverture']);
	});

	it('says the served attachment of a figure, only when the link is not sure', () => {
		const rattachement = { entite: 'parcelle', cle: null, confiance: 'probable' as const, candidats: null, motif: null, alerte: 'Rattachement probable.', maillons: null };
		const c = rendre([dpe()], [theme('dpe', { rattachement })]);
		expect(c.querySelector('.cle-note')?.textContent).toBe('Rattachement probable.');
		const sur = rendre([dpe()], [theme('dpe', { rattachement: { ...rattachement, confiance: 'sure' } })]);
		expect(sur.querySelector('.cle-note')).toBeNull();
	});

	it('puts the next step under the price, under the band when the price has no tile', () => {
		const suitePrix = createRawSnippet(() => ({ render: () => '<a class="suite" href="#x">Suite</a>' }));
		const avec = render(ChiffresCles, { props: { chiffres: [prix(), loyer()], themes: [], suitePrix } }).container;
		expect(tuiles(avec)[0].querySelector('.suite')).not.toBeNull();
		expect(avec.querySelector('.bande-suite')).toBeNull();
		const sans = render(ChiffresCles, { props: { chiffres: [prix({ etat: 'hors_couverture', chiffres: [] })], themes: [], suitePrix } }).container;
		expect(sans.querySelector('.bande-suite .suite')).not.toBeNull();
	});
});

describe('EnBref — l’arrivée nomme ce qui touche l’adresse, rien de la commune (R3)', () => {
	const bref = (themes: ThemeRapport[], partiel: Partial<ResumeServi>) => render(EnBref, { props: { resume: lireResume({ ...resumeVide(), ...partiel }, themes), chiffres: [], themes } }).container;

	it('names the served points at the address, each with its link and its served headline', () => {
		const themes = [theme('inondation', { titre: 'Inondation', ton: 'vigilance' }), theme('grand_axe', { titre: 'Grands axes routiers', ton: 'vigilance', accroche: '16 m' })];
		const c = bref(themes, { pointsAdresse: ['inondation', 'grand_axe'], lus: 2, lusAdresse: 2 });
		expect(c.querySelector('.arrivee h3')?.textContent).toBe('Vigilances à l’adresse ou autour');
		expect([...c.querySelectorAll('.bref-l-adresse a')].map((a) => a.getAttribute('href'))).toEqual(['#critere-inondation', '#critere-grand_axe']);
		expect(c.querySelector('a[href="#critere-grand_axe"] .bref-v')?.textContent).toBe('16 m');
	});

	it('never names a vigilance of the whole commune, nor the sentence, nor an outage', () => {
		const themes = [theme('inondation', { titre: 'Inondation' }), theme('radon', { titre: 'Radon', granularite: g('commune') }), theme('urbanisme', { titre: 'Urbanisme', etat: 'panne', ton: 'info' })];
		const c = bref(themes, { pointsAdresse: ['inondation'], pointsSecteur: ['radon'], pannes: ['urbanisme'], sourcesEnPanne: ['urbanisme'], lus: 2, lusAdresse: 1, lusSecteur: 1 });
		expect(c.textContent).not.toContain('Radon');
		expect(c.textContent).not.toContain('Urbanisme');
		expect(c.querySelector('.bref-p')).toBeNull();
		expect(c.textContent).not.toContain('point de vigilance');
	});

	it('says there is nothing only when something was read', () => {
		expect(bref([theme('fibre')], { lus: 1, lusAdresse: 1 }).querySelector('.bref-vide')?.textContent).toBe('Rien à signaler au point, au bâtiment ni aux alentours.');
		expect(bref([theme('fibre')], { lus: 0, lusAdresse: 0 }).querySelector('.bref-vide')?.textContent).toBe('Aucun critère lu à cette échelle.');
	});

	it('says a summary that is not served, and fills nothing', () => {
		const { container } = render(EnBref, { props: { resume: null, chiffres: [], themes: [] } });
		expect(container.querySelector('.bref-indisponible')?.textContent).toBe('Le résumé de cette fiche n’est pas disponible.');
		expect(container.querySelector('.arrivee')).toBeNull();
	});
});

describe('BilanRisques — les comptes de toute la fiche, dans Risques', () => {
	it('keeps the sentence, its figures in bold, and says that it covers the whole fiche', () => {
		const themes = [theme('inondation'), theme('radon', { granularite: g('commune') })];
		const servi = { ...resumeVide(), pointsAdresse: ['inondation'], pointsSecteur: ['radon'], lus: 2, lusAdresse: 1, lusSecteur: 1 };
		const { container } = render(BilanRisques, { props: { resume: lireResume(servi, themes)! } });
		const phrase = container.querySelector('.bref-p') as HTMLElement;
		expect(phrase.textContent).toBe(`Sur toute la fiche${NBSP}: 1 point de vigilance à l’adresse ou autour, 1 pour toute la commune.`);
		expect(phrase.querySelector('strong')?.textContent).toBe('1 point de vigilance');
	});
});

describe('PartieQuestion — une catégorie fermée, sa ligne de résultat, sa liste (R4, R5)', () => {
	const partieDe = (themes: ThemeRapport[]) => placerThemes(themes).parties[0];
	const FIN = `${NBSP}: ne dit pas si l’adresse est concernée`;

	it('is a native details closed by default, on its anchor, its title an h3 in the summary', () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes' } });
		const cat = container.querySelector('details#q-risques') as HTMLDetailsElement;
		expect(cat.open).toBe(false);
		expect(cat.querySelector('summary h3')?.textContent).toBe('Risques');
	});

	it('opens when the page asks and tells the page when it opens', async () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes', ouverte: true } });
		expect((container.querySelector('details#q-risques') as HTMLDetailsElement).open).toBe(true);
	});

	it('the closed row announces a served result and the served counts, the whole row being the control', () => {
		const r = { ...resumeVide(), pointsAdresse: ['inondation'], pointsSecteur: ['catnat'], pannes: ['geologie'] };
		const themes = [
			theme('inondation', { ton: 'vigilance' }),
			theme('catnat', { granularite: g('commune'), ton: 'vigilance', accroche: '17 arrêtés CatNat' }),
			theme('geologie', { granularite: g('commune'), etat: 'panne', ton: 'info', titre: 'Géologie' })
		];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: r, commune: 'Nantes' } });
		const closed = container.querySelector('summary') as HTMLElement;
		expect(closed.querySelector('.cat-annonce')?.textContent).toBe('17 arrêtés CatNat (commune)');
		expect([...closed.querySelectorAll('.signal')].map((s) => [s.textContent, s.className.split(' ').find((c) => c.startsWith('signal-'))])).toEqual([
			['1 vigilance à l’adresse ou autour', 'signal-vigilance'],
			['1 pour toute la commune', 'signal-discret'],
			[`Source indisponible${NBSP}: Géologie`, 'signal-panne']
		]);
		expect(closed.querySelector('a, button')).toBeNull();
	});

	it('counts nothing without a served summary, the result stays', () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes' } });
		expect(container.querySelector('.cat-signaux')).toBeNull();
		expect(container.querySelector('.cat-annonce')?.textContent).toBe('1 critère');
	});

	it('renders the rows in the order of the classement, whatever their scale, under the title of their group', () => {
		const themes = [theme('catnat', { granularite: g('commune') }), theme('argiles'), theme('inondation', { granularite: g('commune') })];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes' } });
		expect([...container.querySelectorAll('ul.lignes li.ligne')].map((l) => l.id)).toEqual(['critere-inondation', 'critere-argiles', 'critere-catnat']);
		const titres = [...container.querySelectorAll('h4.sous-theme')];
		expect(titres.map((h) => h.textContent)).toEqual(['Eau et sol', 'Arrêtés et risques recensés']);
		expect(container.querySelectorAll('li.ligne h5.ligne-titre')).toHaveLength(3);
	});

	it('an untitled group keeps its rows as h4', () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('dpe'), theme('fibre')]), resume: null, commune: 'Nantes' } });
		expect(container.querySelector('.sous-theme')).toBeNull();
		expect(container.querySelectorAll('li.ligne h4.ligne-titre')).toHaveLength(2);
	});

	it('above the threshold of two columns: a card always open, its summary a title and no control', async () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes', deuxColonnes: true } });
		const cat = container.querySelector('details#q-risques') as HTMLDetailsElement;
		const titre = cat.querySelector('summary') as HTMLElement;
		expect(cat.open).toBe(true);
		expect(titre.getAttribute('tabindex')).toBe('-1');
		await fireEvent.click(titre);
		expect(cat.open).toBe(true);
	});

	it('under the threshold the category folds again, and keeps the choice of the reader', async () => {
		const { container, rerender } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes', deuxColonnes: true } });
		const cat = container.querySelector('details#q-risques') as HTMLDetailsElement;
		await rerender({ deuxColonnes: false });
		expect(cat.open).toBe(false);
		expect(cat.querySelector('summary')?.hasAttribute('tabindex')).toBe(false);
	});

	it('shows every row of the category: after named groups the rest takes a title of its own, no fold in a fold', () => {
		const themes = [theme('inondation'), theme('radon', { granularite: g('commune') }), theme('altitude')];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes' } });
		const cat = container.querySelector('details#q-risques') as HTMLElement;
		expect(cat.querySelector('.voir-autres')).toBeNull();
		expect([...cat.querySelectorAll('.sous-theme')].map((h) => h.textContent)).toEqual(['Eau et sol', 'Autres critères']);
		for (const id of ['radon', 'altitude']) expect(li(container, id).closest('details')).toBe(cat);
	});

	it('without named groups the rest prolongs the list, under no title', () => {
		const themes = [theme('prix_commune'), theme('zonage_abc')];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes' } });
		expect(container.querySelector('.sous-theme')).toBeNull();
		expect(container.querySelector('ul.lignes-suite li#critere-zonage_abc')).not.toBeNull();
	});

	it('D-40: the wider rows all communal: the locked sentence once, its pictogram hidden from assistive technologies', () => {
		const themes = [theme('geologie', { granularite: g('commune') }), theme('radon', { granularite: g('commune') }), theme('inondation')];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes' } });
		const titre = container.querySelector('.com-t') as HTMLElement;
		expect(container.querySelectorAll('.com-t')).toHaveLength(1);
		expect(titre.textContent?.trim()).toBe(`Pour toute la commune de Nantes${FIN}`);
		expect(titre.querySelector('[aria-hidden="true"] svg, svg[aria-hidden="true"]')).not.toBeNull();
	});

	it.each([
		['a radius of 5 km', g('rayon', 5000), `5${NBSP}km autour`],
		['an IRIS', g('iris'), 'Quartier IRIS'],
		['an arrondissement', g('arrondissement'), 'Arrondissement'],
		['an intercommunality', g('epci'), 'Intercommunalité'],
		['a department', g('departement'), 'Département']
	])('D-40: with %s the sentence widens, without pictogram, and each row keeps its word', (_nom, granularite, mot) => {
		const themes = [theme('geologie', { granularite: g('commune') }), theme('radon', { granularite })];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes' } });
		const titre = container.querySelector('.com-t') as HTMLElement;
		expect(titre.textContent?.trim()).toBe(`Pour un périmètre plus large que l’adresse${FIN}`);
		expect(titre.querySelector('svg')).toBeNull();
		expect(li(container, 'radon').querySelector('.ech-mot')?.textContent).toBe(mot);
		expect(li(container, 'geologie').querySelector('.ech-mot')?.textContent).toBe('Commune');
	});

	it('a category at the address only says no such sentence', () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation'), theme('remontee_nappe')]), resume: null, commune: 'Nantes' } });
		expect(container.querySelector('.com-t')).toBeNull();
	});

	it('lets the page render each criterion through the critere snippet, with the heading level', () => {
		const critere = createRawSnippet((args: () => ArgsCritere) => ({
			render: () => `<li class="rendu-par-la-page" data-id="${args().theme.id}" data-niveau="${args().niveauTitre}"></li>`
		}));
		const themes = [theme('altitude'), theme('inondation', { granularite: g('commune') })];
		const { container } = render(PartieQuestion, { props: { partie: partieDe(themes), resume: null, commune: 'Nantes', critere } });
		expect(container.querySelector('li.ligne')).toBeNull();
		expect([...container.querySelectorAll('li.rendu-par-la-page')].map((x) => ({ ...(x as HTMLElement).dataset }))).toEqual([
			{ id: 'inondation', niveau: '5' },
			{ id: 'altitude', niveau: '5' }
		]);
	});

	it('holds the blocks the page gives it, inside the open category', () => {
		const entete = createRawSnippet(() => ({ render: () => '<p class="bloc-entete">Bilan</p>' }));
		const pied = createRawSnippet(() => ({ render: () => '<p class="bloc-pied">Ventes</p>' }));
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('inondation')]), resume: null, commune: 'Nantes', entete, pied } });
		const corps = container.querySelector('details .cat-corps') as HTMLElement;
		expect(corps.firstElementChild?.className).toBe('bloc-entete');
		expect(corps.lastElementChild?.className).toBe('bloc-pied');
	});

	it('names the commune only as served', () => {
		const { container } = render(PartieQuestion, { props: { partie: partieDe([theme('geologie', { granularite: g('commune') })]), resume: null, commune: null } });
		expect(container.querySelector('.com-t')?.textContent?.trim()).toBe(`Pour toute la commune${FIN}`);
	});
});

describe('Sommaire — une vue d’ensemble, de simples liens (R10, décision du 06/11)', () => {
	const entrees = [
		{ ancre: 'q-prix', titre: 'Prix et ventes', niveau: 1 as const },
		{ ancre: 's-comparables', titre: 'Ventes ressemblantes', niveau: 2 as const },
		{ ancre: 'q-risques', titre: 'Risques', niveau: 1 as const },
		{ ancre: 'lire', titre: 'Lire une fiche', niveau: 1 as const, fin: true }
	];
	const large = (c: HTMLElement) => c.querySelector('nav.sommaire-large') as HTMLElement;

	it('is a nav of real anchors, the entries of a category under it', () => {
		const { container } = render(Sommaire, { props: { entrees } });
		const nav = large(container);
		expect(nav.getAttribute('aria-label')).toBe('Sur cette fiche');
		expect([...nav.querySelectorAll('a')].map((a) => a.getAttribute('href'))).toEqual(['#q-prix', '#s-comparables', '#q-risques', '#lire']);
		expect(nav.querySelector('ol ol a')?.textContent).toBe('Ventes ressemblantes');
		expect(nav.querySelector('.sommaire-fin a')?.textContent).toBe('Lire une fiche');
	});

	it('has no number before an entry', () => {
		const { container } = render(Sommaire, { props: { entrees } });
		expect(large(container).textContent).not.toMatch(/\d/);
	});

	it('under the threshold, the same links behind one compact native control', () => {
		const { container } = render(Sommaire, { props: { entrees } });
		const compact = container.querySelector('nav.sommaire-compact details') as HTMLDetailsElement;
		expect(compact.open).toBe(false);
		expect(compact.querySelector('summary')?.textContent).toContain('Sur cette fiche');
		expect(compact.querySelectorAll('a')).toHaveLength(4);
	});

	it('follows the reading: the last entry whose place has passed the top is current, and only that one', async () => {
		const places: Record<string, number> = { 'q-prix': -400, 's-comparables': -40, 'q-risques': 600, lire: 2000 };
		for (const [id, top] of Object.entries(places)) {
			const el = document.body.appendChild(document.createElement('section'));
			el.id = id;
			el.getBoundingClientRect = () => ({ top, height: 100 }) as DOMRect;
		}
		const { container } = render(Sommaire, { props: { entrees } });
		await fireEvent.scroll(window);
		await new Promise((r) => requestAnimationFrame(r));
		await tick();
		const courantes = [...large(container).querySelectorAll('[aria-current]')];
		expect(courantes.map((a) => [a.textContent, a.getAttribute('aria-current')])).toEqual([['Ventes ressemblantes', 'location']]);
		for (const id of Object.keys(places)) document.getElementById(id)?.remove();
	});

	it('leaves a place without a box out: a folded row is not where the reader is', async () => {
		const prix = document.body.appendChild(document.createElement('section'));
		prix.id = 'q-prix';
		prix.getBoundingClientRect = () => ({ top: -400, height: 100 }) as DOMRect;
		const replie = document.body.appendChild(document.createElement('section'));
		replie.id = 's-comparables';
		replie.getBoundingClientRect = () => ({ top: 0, height: 0 }) as DOMRect;
		const { container } = render(Sommaire, { props: { entrees } });
		await fireEvent.scroll(window);
		await new Promise((r) => requestAnimationFrame(r));
		await tick();
		expect(large(container).querySelector('[aria-current]')?.textContent).toBe('Prix et ventes');
		prix.remove();
		replie.remove();
	});

	it('leaves a row of a closed category out: it keeps a measured box the reader cannot see', async () => {
		const prix = document.body.appendChild(document.createElement('details'));
		prix.id = 'q-prix';
		prix.getBoundingClientRect = () => ({ top: -400, height: 100 }) as DOMRect;
		const ligne = prix.appendChild(document.createElement('section'));
		ligne.id = 's-comparables';
		ligne.getBoundingClientRect = () => ({ top: -40, height: 100 }) as DOMRect;
		const { container } = render(Sommaire, { props: { entrees } });
		await fireEvent.scroll(window);
		await new Promise((r) => requestAnimationFrame(r));
		await tick();
		expect(large(container).querySelector('[aria-current]')?.textContent, 'closed: its category').toBe('Prix et ventes');
		prix.open = true;
		await fireEvent.scroll(window);
		await new Promise((r) => requestAnimationFrame(r));
		await tick();
		expect(large(container).querySelector('[aria-current]')?.textContent, 'open: the row').toBe('Ventes ressemblantes');
		prix.remove();
	});

	it('names the row in the compact control, alone: the bar keeps one line and never pushes the page', async () => {
		const prix = document.body.appendChild(document.createElement('section'));
		prix.id = 'q-prix';
		let top = 400;
		prix.getBoundingClientRect = () => ({ top, height: 100 }) as DOMRect;
		const { container } = render(Sommaire, { props: { entrees } });
		const barre = container.querySelector('nav.sommaire-compact summary') as HTMLElement;
		expect(barre.querySelector('.sommaire-t')?.classList.contains('sr-only')).toBe(false);
		top = -400;
		await fireEvent.scroll(window);
		await new Promise((r) => requestAnimationFrame(r));
		await tick();
		expect(barre.querySelector('.sommaire-courante')?.textContent).toBe('Prix et ventes');
		expect(barre.querySelector('.sommaire-t')?.classList.contains('sr-only'), 'still read aloud').toBe(true);
		prix.remove();
	});
});
