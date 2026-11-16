// Section « Documents DPE déclarés à cette adresse » (04-18; UI-SPEC « /adresse/[id] » point 4,
// « Politique d'affichage DPE », « Contrat de rédaction »; D-109, D-111, LIEU-02, LIEU-03). The
// component renders the served list: the head sentence, one common line, the cards with their
// source number as the only action, and nothing it would have to compute.
import { fireEvent, render, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({
	goto: vi.fn(async () => undefined),
	invalidate: vi.fn(async () => undefined),
	invalidateAll: vi.fn(async () => undefined)
}));

import { goto, invalidate, invalidateAll } from '$app/navigation';
import type {
	DocumentDpeCarte,
	DocumentDpeCarteAdresseBrute,
	DocumentsDpeAdresse,
	GroupeAdresseBrute,
	IssueDocumentsDpe,
	SelectionPlusRecent
} from '$lib/api/adresses';
import {
	BADGE_ADRESSE_BRUTE,
	CONSIGNE_COMPARAISON_DOCUMENTS,
	EXPLICATION_ADRESSE_BRUTE,
	TITRE_GROUPE_ADRESSE_BRUTE,
	libelleCompteurSelection
} from '$lib/data/adresse-fiche';
import { documentComparisonSelection } from '$lib/stores/comparison-selection.svelte';
import DpeDocumentList from './DpeDocumentList.svelte';

const NB = ' ';

/** Collapses the source-formatting whitespace of a rendered text; no-break spaces are kept. */
function espaces(texte: string | null | undefined): string {
	return (texte ?? '').replace(/[ \t\r\n]+/g, ' ');
}

const SOURCE_DPE = {
	cle: 'dpe',
	nom: 'Diagnostics de performance énergétique (DPE)',
	producteur: 'ADEME',
	url: 'https://data.ademe.fr/datasets/dpe03existant'
};

function carte(numero: string, partiel: Partial<DocumentDpeCarte> = {}): DocumentDpeCarte {
	return {
		numero,
		etiquette_dpe: 'D',
		etiquette_ges: 'C',
		type_batiment: 'maison',
		surface_habitable: 92,
		date_etablissement: '2022-06-01',
		date_fin_validite: '2032-05-31',
		badges: [],
		adresse_declaree: null,
		...partiel
	};
}

const UNIQUE: SelectionPlusRecent = {
	etat: 'unique',
	motif: 'document_valide',
	date: '2024-03-12',
	document: { numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
	documents_ex_aequo: []
};

const TROIS = [
	carte('2485E0000003C', { etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12', date_fin_validite: '2034-03-11', badges: ['plus_recent'] }),
	carte('2285E0000002D'),
	carte('2185E0000001E', { etiquette_dpe: 'E', date_etablissement: '2021-09-15', date_fin_validite: '2031-09-14' })
];

function documents(partiel: Partial<DocumentsDpeAdresse> = {}): DocumentsDpeAdresse {
	return {
		etat: 'value',
		motif: null,
		source: SOURCE_DPE,
		total: 3,
		page: 1,
		pages: 1,
		par_page: 10,
		generation: '0123456789abcdef',
		distribution: { A: 0, B: 0, C: 1, D: 1, E: 1, F: 0, G: 0, unknown: 0 },
		date_import: '2026-09-30',
		documents: TROIS,
		selection: UNIQUE,
		signal_plusieurs_logements: null,
		constat_d110: null,
		...partiel
	};
}

function ok(partiel: Partial<DocumentsDpeAdresse> = {}): IssueDocumentsDpe {
	return { kind: 'ok', documents: documents(partiel) };
}

function rendre(issue: IssueDocumentsDpe = ok()) {
	return render(DpeDocumentList, { props: { issue, banId: '85191_0420_00012' } });
}

afterEach(() => {
	vi.clearAllMocks();
});

describe('DpeDocumentList — tracer: three declared documents (D-109, LIEU-03)', () => {
	it('h2, head sentence, one common line and its section `i` button', () => {
		const { getByRole, container } = rendre();

		expect(getByRole('heading', { level: 3, name: 'Documents DPE déclarés à cette adresse' })).toBeTruthy();
		expect(container.querySelector('.doclist-phrase')?.textContent).toBe(
			`3 DPE déclarés à cette adresse · le plus récent${NB}: classe C, mars 2024`
		);
		const communes = container.querySelectorAll('.doclist-commune');
		expect(communes).toHaveLength(1);
		expect(communes[0].textContent).toContain(
			"Adresse saisie par le diagnostiqueur · aucun document n'est rattaché à un logement précis · ADEME, dernière importation réussie le 30 septembre 2026"
		);
		expect(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' })).toBeTruthy();
	});

	it('an ordered list of three cards, in the served order, each with its source number as a link to /dpe/{numero}', () => {
		const { container } = rendre();

		const liste = container.querySelector('ol.doclist-cards');
		expect(liste).not.toBeNull();
		const cartes = [...liste!.querySelectorAll(':scope > li')];
		expect(cartes).toHaveLength(3);
		const liens = cartes.map((li) => within(li as HTMLElement).getByRole('link'));
		expect(liens.map((a) => [espaces(a.textContent).trim(), a.getAttribute('href')])).toEqual([
			[`DPE n°${NB}2485E0000003C`, '/dpe/2485E0000003C'],
			[`DPE n°${NB}2285E0000002D`, '/dpe/2285E0000002D'],
			[`DPE n°${NB}2185E0000001E`, '/dpe/2185E0000001E']
		]);
	});

	it('card lines: class tag · « GES {Y} » · « {Type} · {surface} m² », then « Établi le … · valable jusqu’au … » and the served badges', () => {
		const { container } = rendre();
		const premiere = container.querySelector('ol.doclist-cards > li')!;

		expect(premiere.querySelector('.dpe-tag')?.textContent).toBe('Classe C');
		const ligne1 = espaces(premiere.querySelector('.doccard-l1')?.textContent);
		expect(ligne1).toContain('GES B');
		expect(ligne1).toContain(`Maison · 92${NB}m²`);
		const ligne2 = espaces(premiere.querySelector('.doccard-l2')?.textContent);
		expect(ligne2).toContain("Établi le 12 mars 2024 · valable jusqu'au 11 mars 2034");
		expect(premiere.querySelector('.doccard-badges')?.textContent).toContain('Le plus récent');
	});

	it('a card holds its number link and, to compare (C39), one checkbox: no button inside the list (VN11)', () => {
		const { container } = rendre();

		const liste = container.querySelector('ol.doclist-cards')!;
		expect(liste.querySelectorAll('button')).toHaveLength(0);
		for (const li of liste.querySelectorAll(':scope > li')) {
			expect(li.querySelectorAll('a, button, select, [tabindex]')).toHaveLength(1);
			expect(li.querySelectorAll('input[type="checkbox"]')).toHaveLength(1);
		}
	});

	it('the section `i` button opens the panel: rattachement, source and last import (SEM-02)', async () => {
		const { getByRole, container } = rendre();

		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));

		const termes = [...container.querySelectorAll('.source-panel-list > div > dt')].map((dt) => dt.textContent);
		expect(termes).toEqual(['En clair', 'Définition', 'Objet', 'Rattachement', 'Source', 'Dernière importation réussie']);
		const dds = [...container.querySelectorAll('.source-panel-list > div > dd')].map((dd) => dd.textContent);
		expect(dds[2]).toBe('Document DPE');
		expect(dds[3]).toBe('Adresse saisie par le diagnostiqueur (non vérifiée)');
		expect(dds[5]).toBe('30 septembre 2026');
	});
});

// --- Task 3 — the complete section (D-109, D-111, LIEU-02, LIEU-03) ------------------------------

const VINGT_TROIS_P2 = Array.from({ length: 10 }, (_, i) => carte(`2285E00000${String(11 + i).padStart(2, '0')}D`));

describe('DpeDocumentList — zero, one, many (D-109)', () => {
	it('N = 0: the sentence of the contract, no list, no distribution, no pagination, no refresh button', () => {
		const { container, queryAllByRole } = rendre(
			ok({
				total: 0,
				documents: [],
				distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
				selection: { etat: 'indeterminee', motif: 'aucun_document', date: null, document: null, documents_ex_aequo: [] }
			})
		);

		expect(container.querySelector('.doclist-phrase')?.textContent).toBe(
			"Aucun DPE déclaré à cette adresse dans la base ADEME importée le 30 septembre 2026. Cela ne prouve pas qu'aucun DPE n'a été réalisé."
		);
		expect(container.querySelector('ol.doclist-cards')).toBeNull();
		expect(container.querySelector('.doclist-distribution')).toBeNull();
		expect(container.querySelector('.doclist-pagination')).toBeNull();
		// The only exposed button is the `i` of the section (the panel's own button is in the closed dialog).
		expect(queryAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual([
			'Source et définition : documents DPE déclarés à cette adresse'
		]);
		expect(container.textContent).not.toMatch(/Actualiser|Recharger/);
	});

	it('N = 1: no distribution line', () => {
		const { container } = rendre(ok({ total: 1, documents: [TROIS[0]], distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 0 } }));

		expect(container.querySelector('.doclist-distribution')).toBeNull();
	});

	it('N ≥ 2: seven tags A→G, each followed by its served count, zeros kept and muted', () => {
		const { container } = rendre();
		const items = [...container.querySelectorAll('.doclist-distribution > li')];

		expect(items.map((li) => espaces(li.textContent).trim())).toEqual([
			'Classe A 0',
			'Classe B 0',
			'Classe C 1',
			'Classe D 1',
			'Classe E 1',
			'Classe F 0',
			'Classe G 0'
		]);
		expect(items.map((li) => li.querySelector('.doclist-effectif')?.classList.contains('doclist-zero'))).toEqual([
			true, true, false, false, false, true, true
		]);
	});

	it('documents without a known class are counted with the glossary label, never dropped', () => {
		const { container } = rendre(ok({ distribution: { A: 0, B: 0, C: 1, D: 1, E: 0, F: 0, G: 0, unknown: 1 } }));

		const dernier = [...container.querySelectorAll('.doclist-distribution > li')].at(-1);
		expect(espaces(dernier?.textContent).trim()).toBe('Non renseigné 1');
	});
});

describe('DpeDocumentList — tie and badges served (D-109, D-111)', () => {
	it('tie: cards say « Même date la plus récente », never « Le plus récent »', () => {
		const egalite: SelectionPlusRecent = {
			etat: 'egalite',
			motif: 'document_valide',
			date: '2024-03-12',
			document: null,
			documents_ex_aequo: [
				{ numero: '2485E0000003C', etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement: '2024-03-12' },
				{ numero: '2485E0000004D', etiquette_dpe: 'D', etiquette_ges: 'C', date_etablissement: '2024-03-12' }
			]
		};
		const { container } = rendre(
			ok({
				selection: egalite,
				documents: [
					carte('2485E0000003C', { etiquette_dpe: 'C', date_etablissement: '2024-03-12', badges: ['meme_date_plus_recente'] }),
					carte('2485E0000004D', { date_etablissement: '2024-03-12', badges: ['meme_date_plus_recente'] }),
					carte('2185E0000001E')
				]
			})
		);

		expect([...container.querySelectorAll('.doccard-badge')].map((b) => b.textContent)).toEqual([
			'Même date la plus récente',
			'Même date la plus récente'
		]);
		expect(container.textContent).not.toContain('Le plus récent');
	});

	it('« Ancienne méthode », « Expiré », « Remplacement non vérifié » are neutral badges; their meaning is in the panel', async () => {
		const { container, getByRole } = rendre(
			ok({ documents: [carte('2185E0000001E', { badges: ['ancienne_methode', 'expire', 'remplacement_incertain'] })] })
		);

		const badges = [...container.querySelectorAll('.doccard-badge')];
		expect(badges.map((b) => b.textContent)).toEqual(['Ancienne méthode', 'Expiré', 'Remplacement non vérifié']);
		for (const badge of badges) expect(badge.className).not.toMatch(/warn/);

		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));
		const volet = container.querySelector('.source-panel-list')?.textContent ?? '';
		expect(volet).toContain('Ce document a dépassé sa durée de validité de 10 ans. Il reste listé avec sa date.');
		expect(volet).toContain(
			"Établi avant juillet 2021 avec l'ancienne méthode de calcul ; ses classes ne se comparent pas directement à celles des DPE récents."
		);
	});

	it('owner’s badges (2026-10-04/05): « Dates incohérentes », « Date d’établissement future », « Remplacé selon l’ADEME », no provisional mark', async () => {
		const { container, getByRole } = rendre(
			ok({ documents: [carte('2185E0000001E', { badges: ['dates_incoherentes', 'etablissement_futur', 'remplace'] })] })
		);

		const badges = [...container.querySelectorAll('.doccard-badge')];
		expect(badges.map((b) => b.textContent)).toEqual(['Dates incohérentes', "Date d'établissement future", "Remplacé selon l'ADEME"]);
		for (const badge of badges) expect(badge.hasAttribute('data-provisoire')).toBe(false);
		// No successor number on a card: it stays on /dpe/[numero].
		expect(container.querySelector('.doccard-badges')?.textContent).not.toMatch(/n°/);

		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));
		const volet = container.querySelector('.source-panel-list')?.textContent ?? '';
		expect(volet).toContain(`Les dates déclarées dans ce document ne sont pas cohérentes entre elles${NNB}; sa validité ne peut pas être établie.`);
		expect(volet).toContain(`La date d'établissement déclarée est postérieure à aujourd'hui${NNB}; sa validité ne peut pas être établie.`);
	});

	it('a badge served later without a wording stays visible, marked provisional', () => {
		const { container } = rendre(ok({ documents: [carte('2185E0000001E', { badges: ['cle_future'] })] }));

		const badge = container.querySelector('.doccard-badge');
		expect(badge?.textContent).toBe('Valeur non affichable');
		expect(badge?.getAttribute('data-provisoire')).toBe('true');
	});

	it('an empty field of a document is « Non renseigné », never hidden', () => {
		const { container } = rendre(
			ok({ documents: [carte('2185E0000001E', { etiquette_ges: null, type_batiment: null, surface_habitable: null, date_fin_validite: null })] })
		);

		const carteRendue = espaces(container.querySelector('ol.doclist-cards > li')?.textContent);
		// Inside a sentence the glossary word is lower case and agreed (IN-02 of the D-15 review); a
		// segment of its own keeps the badge form.
		expect(carteRendue).toContain('GES non renseigné');
		expect(carteRendue).toContain('Type non renseigné · surface non renseignée');
		expect(carteRendue).toContain('fin de validité non renseignée');
		expect(carteRendue).not.toMatch(/\b(le|jusqu'au|GES) Non renseigné/);
	});

	it('a building document without surface names the missing surface (UI-009)', () => {
		const { container } = rendre(ok({ documents: [carte('2185E0000001E', { type_batiment: 'immeuble', surface_habitable: null })] }));

		const carteRendue = espaces(container.querySelector('ol.doclist-cards > li')?.textContent);
		expect(carteRendue).toContain('Immeuble · surface non renseignée');
		expect(carteRendue).not.toContain('Immeuble · Non renseigné');
	});
});

describe('DpeDocumentList — signal of several dwellings (LIEU-02)', () => {
	it('served: the sentence of the contract, no dwelling named', () => {
		const { container } = rendre(
			ok({ signal_plusieurs_logements: { etat: 'value', motif: 'copropriete_immatriculee', nombre_lots_habitation: 12, source: null } })
		);

		expect(container.querySelector('.doclist-signal')?.textContent).toBe(
			'Cette adresse peut compter plusieurs logements (copropriété immatriculée au Registre national des copropriétés).'
		);
	});

	it('not served: nothing', () => {
		const { container } = rendre();

		expect(container.querySelector('.doclist-signal')).toBeNull();
	});

	it('in outage: « Indisponible » for the registry, never an absence of signal', () => {
		const { container } = rendre(
			ok({
				signal_plusieurs_logements: {
					etat: 'temporary_error',
					motif: null,
					nombre_lots_habitation: null,
					source: { cle: 'copropriete', nom: 'Registre national des copropriétés (RNIC)', producteur: 'ANAH', url: 'https://www.data.gouv.fr' }
				}
			})
		);

		const signal = container.querySelector('.doclist-signal');
		expect(signal?.textContent).toContain('Registre national des copropriétés (RNIC)');
		expect(signal?.querySelector('[data-state="temporary_error"] .state-badge')?.textContent).toBe('Indisponible');
		expect(signal?.textContent).not.toContain('peut compter plusieurs logements');
	});
});

describe('DpeDocumentList — pagination bound to the served generation (D-109, N-11)', () => {
	const PAGE_2 = ok({ total: 23, page: 2, pages: 3, documents: VINGT_TROIS_P2 });

	it('one page: pagination hidden on screen', () => {
		const { container } = rendre();

		expect(container.querySelector('.doclist-pagination nav')).toBeNull();
	});

	it('23 documents, page 2: « Documents 11 à 20 sur 23 », links carrying page and generation', () => {
		const { container, getByRole } = rendre(PAGE_2);

		expect(container.querySelectorAll('ol.doclist-cards > li')).toHaveLength(10);
		expect(container.querySelector('.doclist-range')?.textContent).toBe('Documents 11 à 20 sur 23');
		expect(getByRole('link', { name: 'Page précédente' }).getAttribute('href')).toBe('?page=1&g=0123456789abcdef');
		expect(getByRole('link', { name: 'Page suivante' }).getAttribute('href')).toBe('?page=3&g=0123456789abcdef');
	});

	it('the links of the cards and of the pagination are preloaded on tap, never on hover (FRONT WR-03)', () => {
		const { container } = rendre(PAGE_2);

		expect(container.querySelector('ol.doclist-cards')?.getAttribute('data-sveltekit-preload-data')).toBe('tap');
		expect(container.querySelector('.doclist-pagination nav')?.getAttribute('data-sveltekit-preload-data')).toBe('tap');
	});

	it('first and last pages have one link each', () => {
		const premiere = rendre(ok({ total: 23, page: 1, pages: 3, documents: VINGT_TROIS_P2 }));
		expect(premiere.queryByRole('link', { name: 'Page précédente' })).toBeNull();
		premiere.unmount();
		const derniere = rendre(ok({ total: 23, page: 3, pages: 3, documents: VINGT_TROIS_P2.slice(0, 3) }));
		expect(derniere.queryByRole('link', { name: 'Page suivante' })).toBeNull();
		expect(derniere.container.querySelector('.doclist-range')?.textContent).toBe('Documents 21 à 23 sur 23');
	});

	it('with JavaScript: the page link navigates in place, cards kept and aria-busy until the answer, then announced and focused', async () => {
		let terminer: () => void = () => undefined;
		vi.mocked(goto).mockImplementationOnce(() => new Promise<void>((resolve) => (terminer = resolve)));
		const { container, getByRole } = rendre(PAGE_2);

		await fireEvent.click(getByRole('link', { name: 'Page suivante' }));

		expect(goto).toHaveBeenCalledWith('?page=3&g=0123456789abcdef', { noScroll: true, keepFocus: true });
		const section = container.querySelector('section.doclist')!;
		expect(section.getAttribute('aria-busy')).toBe('true');
		expect(container.querySelectorAll('ol.doclist-cards > li')).toHaveLength(10);

		terminer();
		await vi.waitFor(() => expect(section.getAttribute('aria-busy')).toBe('false'));
		expect(container.querySelector('.doclist-annonce')?.textContent).toBe('Documents 11 à 20 sur 23');
		expect(document.activeElement).toBe(getByRole('heading', { level: 3, name: 'Documents DPE déclarés à cette adresse' }));
	});
});

describe('DpeDocumentList — outage of the section (UI-SPEC « Les quatre états »)', () => {
	it('503: warn box of the contract and « Recharger les documents DPE », no card', async () => {
		const { container, getByRole } = rendre({ kind: 'panne', panne: { statut: 503, titre: null, message: null } });

		const encart = container.querySelector('.doclist-panne');
		expect(encart?.getAttribute('role')).toBe('alert');
		expect(encart?.textContent).toContain("Les documents DPE n'ont pas pu être lus. Le reste de la page reste affiché.");
		expect(container.querySelector('ol.doclist-cards')).toBeNull();
		await fireEvent.click(getByRole('button', { name: 'Recharger les documents DPE' }));
		// The list only, never the identity nor Géorisques (FRONT WR-03 of the D-15 review).
		expect(invalidate).toHaveBeenCalledTimes(1);
		expect(invalidate).toHaveBeenCalledWith('app:adresse-documents');
		expect(invalidateAll).not.toHaveBeenCalled();
	});

	it('a list served without a readable count is an outage of the section, never « 0 » (IN-01)', () => {
		const { container } = rendre(ok({ total: null }));

		expect(container.querySelector('.doclist-panne')?.textContent).toContain("Les documents DPE n'ont pas pu être lus.");
		expect(container.textContent).not.toContain('Aucun DPE déclaré');
		expect(container.querySelector('ol.doclist-cards')).toBeNull();
	});

	it('a typed refusal (429) keeps its own message, never worded as an outage', () => {
		const { container } = rendre({ kind: 'panne', panne: { statut: 429, titre: 'Trop de demandes', message: 'Réessayez dans 30 secondes.' } });

		const encart = container.querySelector('.doclist-panne');
		expect(encart?.textContent).toContain('Réessayez dans 30 secondes.');
		expect(encart?.textContent).not.toContain("n'ont pas pu être lus");
	});

	it('DPE out of coverage: the warn badge of the reason, never a zero', () => {
		const { container } = rendre(ok({ etat: 'out_of_coverage', motif: 'territoire_hors_perimetre', total: null, documents: [], selection: null }));

		expect(container.querySelector('[data-state="out_of_coverage"] .state-badge')?.textContent).toBe('Hors couverture · département non hébergé');
		expect(container.querySelector('.doclist-phrase')).toBeNull();
	});
});

// --- 04-19 Task 1 — line 4: the declared address, published or masked (P-ADR-01, LIEU-03) --------

const NNB = ' ';

describe('DpeDocumentList — line 4: declared address served by the API (P-ADR-01)', () => {
	const PUBLIEE = carte('2485G0000001P', { adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null } });
	const COMPLEMENT = carte('2485G0000004C', { adresse_declaree: { etat: 'masquee', texte: null, motif: 'complement_present' } });
	const NON_VERIFIABLE = carte('2485G0000003S', { adresse_declaree: { etat: 'masquee', texte: null, motif: 'non_verifiable_sans_complement' } });
	const CONCORDANTE = carte('2485G0000005K');
	const ABSENTE = carte('2485G0000008A', { adresse_declaree: { etat: 'absente', texte: null, motif: null } });

	function lignes4(container: HTMLElement) {
		return [...container.querySelectorAll('ol.doclist-cards > li')].map((li) => li.querySelector('.doccard-l4')?.textContent ?? null);
	}

	it('published: « Adresse déclarée : « {text as typed} » »; masked: the short line « Adresse déclarée non affichée » (2026-10-04)', () => {
		const { container } = rendre(ok({ total: 5, documents: [PUBLIEE, COMPLEMENT, NON_VERIFIABLE, CONCORDANTE, ABSENTE] }));

		expect(lignes4(container)).toEqual([
			`Adresse déclarée${NB}: «${NNB}14 rue des Lilas${NNB}»`,
			'Adresse déclarée non affichée',
			'Adresse déclarée non affichée',
			null,
			null
		]);
		expect(container.textContent).not.toContain('Secret statistique');
		// The reason is not on the card any more: it is read in the panel of the section.
		expect(container.querySelector('ol.doclist-cards')?.textContent).not.toContain('complément (étage');
	});

	it('a masked address puts its served reasons and the conservation sentence in the section panel, once', async () => {
		const { getByRole, container } = rendre(ok({ documents: [COMPLEMENT, NON_VERIFIABLE, CONCORDANTE] }));

		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));

		const entrees = [...container.querySelectorAll('.source-panel-list > div')].map((div) => [
			div.querySelector('dt')?.textContent,
			div.querySelector('dd')?.textContent
		]);
		expect(entrees.filter(([terme]) => terme === 'Adresse déclarée non affichée')).toEqual([
			[
				'Adresse déclarée non affichée',
				'Le document y joint un complément (étage, porte, bâtiment ou nom). Elle ne se vérifie pas comme une adresse sans complément. ' +
					"Le texte saisi par le diagnostiqueur est conservé par DataCommune et n'est pas publié."
			]
		]);
	});

	it('no masked address: no conservation sentence in the panel; line 4 adds no actionable element', async () => {
		const { getByRole, container } = rendre(ok({ documents: [PUBLIEE, CONCORDANTE] }));

		for (const li of container.querySelectorAll('ol.doclist-cards > li')) {
			expect(li.querySelectorAll('a, button, select, [tabindex]')).toHaveLength(1);
		}
		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));
		expect(container.textContent).not.toContain('est conservé par DataCommune');
	});
});

// --- 04-19 Task 2 — D-110 finding, rendered only from the served field (DPE-01, P-D110-NULL) ------

const CONSTAT = {
	criteres: ['maison', 'document_unique_cle', 'concordance_stricte', 'batiment_unique_parcelle_du_point', 'annexes_exclues_usage_inconnu_compte'],
	sources: [SOURCE_DPE, { cle: 'batiments', nom: 'BD TOPO — Bâtiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo' }]
};
const TITRE_D110 = `Une adresse, un bâtiment, un DPE${NB}: les éléments concordent`;

describe('DpeDocumentList — D-110 finding (DPE-01, D-110, P-D110-NULL)', () => {
	it('served: neutral notice titled h3 under the section h2, served criteria joined by « · », `i` button with the limit', async () => {
		const unique = [carte('2585H0000000D', { badges: ['plus_recent'] })];
		const { container, getByRole } = rendre(ok({ total: 1, documents: unique, constat_d110: CONSTAT }));

		const encart = container.querySelector('[data-constat-d110]');
		expect(encart).not.toBeNull();
		// 2026-10-05, rows 8 and 9: h3 under the h2 of the section; no column name in the public text.
		expect(encart!.querySelector('.notice h2')).toBeNull();
		expect(encart!.querySelector('.notice h3')?.textContent).toBe(TITRE_D110);
		// The served criteria, without the word of the source action that closes them (text button since 04.8-02, E4).
		const criteres = encart!.querySelector('.constat-criteres')!.cloneNode(true) as Element;
		criteres.querySelectorAll('.source-button').forEach((action) => action.remove());
		expect(espaces(criteres.textContent).trim()).toBe(
			"maison · document unique à la clé BAN · concordance stricte de l'adresse brute · bâtiment unique sur la parcelle du point BAN · annexes déclarées exclues, bâtiment d'usage inconnu compté"
		);
		expect(encart!.querySelector('.constat-criteres')?.textContent).not.toMatch(/BD TOPO|RNB|cad_parcelles/);
		// No internal decision key in the accessible name (IN-03 of the D-15 review).
		await fireEvent.click(getByRole('button', { name: 'Source et définition : constat de concordance' }));
		const dialogue = getByRole('dialog', { name: TITRE_D110 });
		expect(dialogue.textContent).toContain("Ce constat ne prouve pas l'identité d'un logement.");
		expect(dialogue.textContent).toContain('BD TOPO — Bâtiments');
	});

	it('not served (null): no notice and no sentence, whatever the cards say', () => {
		const unique = [carte('2585H0000000D', { type_batiment: 'maison', badges: ['plus_recent'] })];
		const { container } = rendre(ok({ total: 1, documents: unique, constat_d110: null }));

		expect(container.querySelector('[data-constat-d110]')).toBeNull();
		expect(container.textContent).not.toContain('les éléments concordent');
	});

	it('an unknown served criterion is kept, rendered by the neutral label and marked provisional', () => {
		const { container } = rendre(ok({ constat_d110: { ...CONSTAT, criteres: [...CONSTAT.criteres, 'critere_futur'] } }));

		const provisoires = container.querySelectorAll('[data-constat-d110] [data-provisoire="true"]');
		expect([...provisoires].map((el) => el.textContent)).toEqual(['Valeur non affichable']);
	});
});

// --- C39: check the documents of the address, then compare them ---------------------------------

describe('DpeDocumentList — comparer des documents cochés (C39)', () => {
	const CINQ = [
		...TROIS,
		carte('2085E0000004F', { date_etablissement: '2020-01-10' }),
		carte('1985E0000005G', { date_etablissement: '2019-01-10' })
	];

	afterEach(() => {
		documentComparisonSelection.replace([]);
		sessionStorage.clear();
	});

	it('one labelled checkbox per card, none checked; no button before two documents are checked', async () => {
		const { getByRole, queryByRole } = rendre();

		const case1 = getByRole('checkbox', { name: 'Sélectionner le DPE n° 2485E0000003C' }) as HTMLInputElement;
		expect(case1.checked).toBe(false);
		expect(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2185E0000001E' })).toBeTruthy();
		await fireEvent.click(case1);
		expect(case1.checked).toBe(true);
		expect(queryByRole('link', { name: /^Comparer les documents/ })).toBeNull();
	});

	it('two checked: « Comparer les documents (2) » opens the document comparator with exactly these two', async () => {
		const { getByRole } = rendre();

		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2485E0000003C' }));
		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2185E0000001E' }));
		const bouton = getByRole('link', { name: 'Comparer les documents (2)' });
		expect(bouton.getAttribute('href')).toBe('/comparer?dpe=2485E0000003C,2185E0000001E');
		await fireEvent.click(bouton);
		expect(goto).toHaveBeenCalledWith('/comparer?dpe=2485E0000003C,2185E0000001E');
		expect(documentComparisonSelection.codes).toEqual(['2485E0000003C', '2185E0000001E']);
	});

	it('four checked at most: the other boxes are disabled and a message says why', async () => {
		const { getByRole, container } = rendre(ok({ total: 5, documents: CINQ }));

		for (const numero of ['2485E0000003C', '2285E0000002D', '2185E0000001E', '2085E0000004F']) {
			await fireEvent.click(getByRole('checkbox', { name: `Sélectionner le DPE n° ${numero}` }));
		}
		expect((getByRole('checkbox', { name: 'Sélectionner le DPE n° 1985E0000005G' }) as HTMLInputElement).disabled).toBe(true);
		expect((getByRole('checkbox', { name: 'Sélectionner le DPE n° 2485E0000003C' }) as HTMLInputElement).disabled).toBe(false);
		expect(container.querySelector('.doclist-comparer')?.textContent).toContain(
			`Quatre documents DPE au maximum${NB}: décochez-en un pour en choisir un autre.`
		);
		expect(getByRole('link', { name: 'Comparer les documents (4)' })).toBeTruthy();
	});

	it('a document selection already in the comparator is never replaced without a message (WR-03)', async () => {
		documentComparisonSelection.replace(['2385E0000009Z']);
		const { getByRole, container } = rendre();

		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2485E0000003C' }));
		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2285E0000002D' }));
		expect(container.querySelector('.doclist-comparer')?.textContent).toContain(
			'La comparaison de documents en cours (1 DPE) sera remplacée par ces documents.'
		);
	});

	it('a single document at the address: nothing to compare, no checkbox', () => {
		const { queryAllByRole, container } = rendre(ok({ total: 1, documents: [TROIS[0]] }));
		expect(queryAllByRole('checkbox')).toHaveLength(0);
		expect(container.querySelector('.doclist-consigne')).toBeNull();
	});

	// B3 (UI-008, plan 04.5-05): the purpose of the boxes is said before the first one, with the count
	// of the boxes the visitor checked (not a figure of the list).
	it('the instruction and the counter come before the first checkbox; two checked set the counter to 2', async () => {
		const { getByRole, getAllByRole, container } = rendre();

		const consigne = container.querySelector('.doclist-consigne');
		expect(espaces(consigne?.textContent).trim()).toBe(`${CONSIGNE_COMPARAISON_DOCUMENTS} ${libelleCompteurSelection(0)}`);
		const [premiere] = getAllByRole('checkbox');
		expect(consigne!.compareDocumentPosition(premiere) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		const compteur = () => container.querySelector('.doclist-compteur')?.textContent;
		expect(compteur()).toBe('0 sélectionné');

		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2485E0000003C' }));
		expect(compteur()).toBe('1 sélectionné');
		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2185E0000001E' }));
		expect(compteur()).toBe('2 sélectionnés');
		expect(getByRole('link', { name: 'Comparer les documents (2)' })).toBeTruthy();
		await fireEvent.click(getByRole('checkbox', { name: 'Sélectionner le DPE n° 2185E0000001E' }));
		expect(compteur()).toBe('1 sélectionné');
	});

	// Review 05 (MINOR): the counter is announced in a status region from the first box; the compare
	// action is no live region any more (its count would be said twice), its messages still are.
	it('the counter is the status region of the count; the compare link is in no live region, its messages are', async () => {
		const { getByRole, container } = rendre(ok({ total: 5, documents: CINQ }));

		expect(container.querySelector('.doclist-compteur')?.getAttribute('role')).toBe('status');
		for (const numero of ['2485E0000003C', '2285E0000002D', '2185E0000001E', '2085E0000004F']) {
			await fireEvent.click(getByRole('checkbox', { name: `Sélectionner le DPE n° ${numero}` }));
		}
		const lien = getByRole('link', { name: 'Comparer les documents (4)' });
		const VIVANTE = '[aria-live]:not([aria-live="off"]), [role="status"], [role="alert"], [role="log"]';
		expect(lien.closest(VIVANTE)).toBeNull();
		const note = container.querySelector('.doclist-comparer-note');
		expect(note?.closest('[aria-live="polite"]')).not.toBeNull();
		// The count is said by one region only.
		const regions = [...container.querySelectorAll(VIVANTE)].filter((region) => /sélectionnés?|Comparer les documents/.test(region.textContent ?? ''));
		expect(regions).toHaveLength(1);
		expect(regions[0].classList.contains('doclist-compteur')).toBe(true);
	});

	it('four checked: the comparator opens with exactly these four, in the checked order', async () => {
		const { getByRole, container } = rendre(ok({ total: 5, documents: CINQ }));

		const ordre = ['2085E0000004F', '2485E0000003C', '1985E0000005G', '2285E0000002D'];
		for (const numero of ordre) await fireEvent.click(getByRole('checkbox', { name: `Sélectionner le DPE n° ${numero}` }));
		expect(container.querySelector('.doclist-compteur')?.textContent).toBe('4 sélectionnés');
		await fireEvent.click(getByRole('link', { name: 'Comparer les documents (4)' }));
		expect(goto).toHaveBeenCalledWith(`/comparer?dpe=${ordre.join(',')}`);
		expect(documentComparisonSelection.codes).toEqual(ordre);
	});

	it('the cards, links, badges and pagination are unchanged by the instruction (D45-11)', () => {
		const { container } = rendre(ok({ total: 23, pages: 3, page: 2, documents: TROIS }));
		const enfants = [...container.querySelector('section.doclist')!.children].map((noeud) => noeud.className.split(' ')[0]);
		expect(enfants.indexOf('doclist-consigne')).toBe(enfants.indexOf('doclist-cards') - 1);
		expect([...container.querySelectorAll('ol.doclist-cards > li a.doccard-lien')].map((a) => a.getAttribute('href'))).toEqual(
			TROIS.map((document) => `/dpe/${document.numero}`)
		);
		expect(container.querySelector('.doclist-range')?.textContent).toBe('Documents 11 à 13 sur 23');
	});
});

// --- C2 (04.1-13, D-127; plan 04.5-05): the documents attached by their exact raw address --------
// A second titled block after the list of the ADEME key: its served total, its limit, its cards with
// the badge of 4.1-13 and their link, its own panel. Nothing of the ADEME key changes (D45-06).
describe('DpeDocumentList — groupe rapproché par l’adresse brute, à part (C2)', () => {
	const brute = (numero: string, partiel: Partial<DocumentDpeCarteAdresseBrute> = {}): DocumentDpeCarteAdresseBrute => ({
		...carte(numero, { date_etablissement: '2025-06-02', date_fin_validite: '2035-06-01' }),
		rattachement: 'adresse_brute_exacte',
		...partiel
	});
	const TROIS_BRUTES = [brute('2544E9000010S'), brute('2544E9000009S', { badges: ['expire'] }), brute('2444E9000008S')];
	const groupe = (partiel: Partial<GroupeAdresseBrute> = {}): GroupeAdresseBrute => ({ etat: 'value', total: 3, documents: TROIS_BRUTES, ...partiel });
	const bloc = (container: HTMLElement) => container.querySelector('[data-groupe-dpe="adresse_brute"]');

	afterEach(() => {
		documentComparisonSelection.replace([]);
		sessionStorage.clear();
	});

	it('an answer without the group (adresseBrute null) renders no block', () => {
		const { container } = rendre(ok({ adresseBrute: null }));
		expect(bloc(container)).toBeNull();
	});

	it('served: a titled block after the ADEME list, its served total, its cards in the served order with their link and the badge', () => {
		const { container } = rendre(ok({ adresseBrute: groupe() }));
		const groupeRendu = bloc(container)!;
		expect(groupeRendu).not.toBeNull();
		expect(groupeRendu.querySelector('h4')?.textContent).toBe(TITRE_GROUPE_ADRESSE_BRUTE);
		const listeAdeme = container.querySelector('ol.doclist-cards')!;
		expect(listeAdeme.compareDocumentPosition(groupeRendu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(groupeRendu.querySelector('.doclist-brute-phrase')?.textContent).toBe(
			'3 DPE rapprochés de cette adresse par leur adresse saisie. Ils ne sont pas comptés parmi les DPE déclarés à cette adresse.'
		);
		const cartes = [...groupeRendu.querySelectorAll('ol.doclist-brute-cards > li')];
		expect(cartes.map((li) => li.querySelector('a.doccard-lien')?.getAttribute('href'))).toEqual(TROIS_BRUTES.map((doc) => `/dpe/${doc.numero}`));
		for (const li of cartes) expect(li.querySelector('.doccard-rattachement')?.textContent).toBe(BADGE_ADRESSE_BRUTE);
		expect(cartes[1].querySelector('.doccard-badge[data-badge="expire"]')?.textContent).toBe('Expiré');
		expect(groupeRendu.querySelector('.doclist-brute-limite')).toBeNull();
	});

	it('the badge is on the cards of the group only; the ADEME head sentence, distribution and cards are those without the group', () => {
		const sans = rendre(ok());
		const ademeSans = {
			phrase: sans.container.querySelector('.doclist-phrase')?.textContent,
			repartition: sans.container.querySelector('.doclist-distribution')?.textContent,
			cartes: [...sans.container.querySelectorAll('ol.doclist-cards > li')].map((li) => li.textContent)
		};
		sans.unmount();
		const { container } = rendre(ok({ adresseBrute: groupe() }));
		expect(container.querySelector('.doclist-phrase')?.textContent).toBe(ademeSans.phrase);
		expect(container.querySelector('.doclist-distribution')?.textContent).toBe(ademeSans.repartition);
		expect([...container.querySelectorAll('ol.doclist-cards > li')].map((li) => li.textContent)).toEqual(ademeSans.cartes);
		expect(container.querySelectorAll('ol.doclist-cards .doccard-rattachement')).toHaveLength(0);
		expect(container.querySelectorAll('.doccard-rattachement')).toHaveLength(3);
	});

	it('the cards of the group are not offered to the comparison: no checkbox, the instruction counts the ADEME boxes only', () => {
		const { container, getAllByRole } = rendre(ok({ adresseBrute: groupe() }));
		expect(bloc(container)!.querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
		expect(getAllByRole('checkbox')).toHaveLength(TROIS.length);
	});

	it('R6 « dix sur N »: twelve served, ten cards, the limit said with the served total', () => {
		const dix = Array.from({ length: 10 }, (_, rang) => brute(`25${String(rang).padStart(2, '0')}E9000000S`));
		const { container } = rendre(ok({ adresseBrute: groupe({ total: 12, documents: dix }) }));
		expect(bloc(container)!.querySelectorAll('ol.doclist-brute-cards > li')).toHaveLength(10);
		expect(bloc(container)!.querySelector('.doclist-brute-limite')?.textContent).toBe('Les 10 plus récents sur 12 sont affichés.');
	});

	it('not computed: the cause is said, never a zero, no card', () => {
		const { container } = rendre(ok({ adresseBrute: groupe({ etat: 'not_available', total: null, documents: [] }) }));
		const texte = bloc(container)!.textContent ?? '';
		expect(bloc(container)!.querySelector('.doclist-brute-phrase')?.textContent).toBe(
			"Le rapprochement par l'adresse saisie n'est pas calculé pour cette importation des données DPE."
		);
		expect(texte).not.toMatch(/\b0\b/);
		expect(bloc(container)!.querySelectorAll('li')).toHaveLength(0);
	});

	it('computed and empty: the zero sentence of the group, no card', () => {
		const { container } = rendre(ok({ adresseBrute: groupe({ total: 0, documents: [] }) }));
		expect(bloc(container)!.querySelector('.doclist-brute-phrase')?.textContent).toBe('Aucun DPE rapproché de cette adresse par son adresse saisie.');
		expect(bloc(container)!.querySelectorAll('li')).toHaveLength(0);
	});

	it('no document at the ADEME key: its zero sentence, then the group with its cards', () => {
		const { container } = rendre(
			ok({
				total: 0,
				documents: [],
				distribution: { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 },
				selection: null,
				adresseBrute: groupe({ total: 2, documents: TROIS_BRUTES.slice(0, 2) })
			})
		);
		expect(container.querySelector('.doclist-phrase')?.textContent).toMatch(/^Aucun DPE déclaré à cette adresse/);
		expect(bloc(container)!.querySelectorAll('ol.doclist-brute-cards > li')).toHaveLength(2);
	});

	it('the `i` button of the group opens its own panel: definition, attachment and the badge explained', async () => {
		const { getByRole } = rendre(ok({ adresseBrute: groupe() }));
		await fireEvent.click(getByRole('button', { name: "Source et définition : documents DPE rapprochés par l'adresse saisie" }));
		const volet = getByRole('dialog', { name: TITRE_GROUPE_ADRESSE_BRUTE });
		expect(volet.textContent).toContain('Adresse saisie, rapprochée exactement de la BAN');
		expect(espaces(volet.textContent)).toContain(espaces(EXPLICATION_ADRESSE_BRUTE));
	});

	it('the panel of the ADEME list does not mention the group', async () => {
		const { getByRole } = rendre(ok({ adresseBrute: groupe() }));
		await fireEvent.click(getByRole('button', { name: 'Source et définition : documents DPE déclarés à cette adresse' }));
		const volet = getByRole('dialog', { name: 'Documents DPE déclarés à cette adresse' });
		expect(volet.textContent).not.toContain(BADGE_ADRESSE_BRUTE);
	});

	it('a card of an unknown attachment keeps the neutral badge, marked provisional, never the badge of 4.1-13', () => {
		const { container } = rendre(ok({ adresseBrute: groupe({ total: 1, documents: [brute('2544E9000010S', { rattachement: 'cle_future' })] }) }));
		const badge = bloc(container)!.querySelector('.doccard-rattachement');
		expect(badge?.textContent).not.toBe(BADGE_ADRESSE_BRUTE);
		expect(badge?.getAttribute('data-provisoire')).toBe('true');
	});
});
