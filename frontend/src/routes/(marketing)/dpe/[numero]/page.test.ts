// `/dpe/[numero]` (UI-SPEC « /dpe/[numero] — page document », LIEU-03, D-102, D-111, SEM-01): the
// page renders what the API serves about one document, by its ADEME number, and attributes it to
// nothing. The server loader projects a public model (the only thing serialized in the HTML), marks
// the page `noindex, follow` and never calls the API for a malformed number. Since 2026-10-05
// (04-DECISIONS-TEXTES, 04-04 rows 13 and 14) an unknown or malformed number answers 404 and an
// outage 503, rendered by `+error.svelte` with the same content.
import { fireEvent, render, within } from '@testing-library/svelte';
import type { Writable } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		page: writable({ status: 200, error: null, params: {}, url: new URL('https://site.invalid/') }),
		navigating: writable(null)
	};
});

import { invalidateAll } from '$app/navigation';
import { goto } from '$app/navigation';
import { documentComparisonSelection } from '$lib/stores/comparison-selection.svelte';
import { navigating, page } from '$app/stores';
import type { DocumentDpePublic } from '$lib/api/documents-dpe';
import type { ValeurLieu } from '$lib/api/adresses';
import { DEPARTEMENTS } from '$lib/data/departements';
import { TITRE_LIMITE, messageLimite } from '$lib/data/panne';
import { SITE_URL } from '$lib/config';
import { load, type DocumentPageData } from './+page.server';
import ErrorPage from './+error.svelte';
import Page from './+page.svelte';

const navigatingStore = navigating as unknown as Writable<unknown>;
const pageStore = page as unknown as Writable<{
	status: number;
	error: { message: string; departements?: string[] } | null;
	params: Record<string, string>;
	url: URL;
}>;
const NB = String.fromCharCode(0x00a0);
const NNB = String.fromCharCode(0x202f);
const NUMERO = '2485E0000003C';
const SOURCE_DPE = { cle: 'dpe', nom: 'Diagnostics de performance énergétique (DPE)', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant' };

function valeur(partiel: Partial<ValeurLieu>): ValeurLieu {
	return { field_id: 'x', etat: 'value', valeur: null, unite: null, periode: null, motif: null, source: SOURCE_DPE, rattachement: 'adresse_saisie_diagnostiqueur', reference: null, ...partiel };
}

function document(partiel: Partial<DocumentDpePublic> = {}): DocumentDpePublic {
	return {
		numero: NUMERO,
		source_identity: { reference: NUMERO, sources: [{ dataset: 'dpe03existant', url: 'https://data.ademe.fr/datasets/dpe03existant' }] },
		disponibilite: 'disponible',
		methode: 'actuelle',
		statut: { code: 'en_vigueur', validite: 'not_expired_by_date', date: null, remplace_par: null, remplace: null },
		dates: { etablissement: '2024-03-12', fin_validite: '2034-03-11' },
		reperes: {
			etiquette_energie: {
				classe: valeur({ field_id: 'dpe.document.etiquette_dpe', valeur: 'C' }),
				mesure: valeur({ field_id: 'dpe.document.conso_ep', valeur: 9120 }),
				// Published by the ADEME, never 9 120 / 81,9 (about 111): plan 04.5-12, D45-05.
				mesure_m2: valeur({ field_id: 'dpe.document.conso_ep_m2', valeur: 104 })
			},
			etiquette_climat: {
				classe: valeur({ field_id: 'dpe.document.etiquette_ges', valeur: 'B' }),
				mesure: valeur({ field_id: 'dpe.document.emissions_ges', valeur: 1004.8 }),
				mesure_m2: valeur({ field_id: 'dpe.document.emissions_ges_m2', valeur: 11 })
			},
			logement_decrit: {
				type_batiment: valeur({ field_id: 'dpe.document.type_batiment', valeur: 'maison' }),
				surface: valeur({ field_id: 'dpe.document.surface_habitable', valeur: 81.9 })
			},
			cout_annuel: valeur({ field_id: 'dpe.document.cout_annuel', valeur: 1240 })
		},
		caracteristiques: [
			valeur({ field_id: 'dpe.document.type_batiment', valeur: 'maison' }),
			valeur({ field_id: 'dpe.document.isolation', etat: 'not_reported', valeur: null })
		],
		adresse_declaree: { etat: 'publiee', texte: '14 rue des Lilas', motif: null },
		adresse_ban: {
			etat: 'retenue',
			ban_id: '85191_0420_00012',
			libelle: '12 Rue des Lilas, 85000 La Roche-sur-Yon',
			numero: '12',
			rep: null,
			nom_voie: 'Rue des Lilas',
			commune: { code_insee: '85191', nom: 'La Roche-sur-Yon' },
			motif: null
		},
		adresse_divergente: true,
		provenance: { source: 'ADEME', date_import: '2026-09-30' },
		...partiel
	};
}

function afficher(issue: DocumentPageData['issue'], numero = NUMERO) {
	return render(Page, { props: { data: { numero, issue } } });
}

afterEach(() => {
	navigatingStore.set(null);
	vi.mocked(invalidateAll).mockClear();
});

describe('/dpe/[numero] — document servi', () => {
	// UI-SPEC v4 § 6.7 (04.8-04): the h1 names the document by its served type and surface; the number
	// moves to the line under it (« Document DPE · N° {numero} · Établi le … »), still in tabular figures.
	// T6 (decided by Ianis on 2026-11-10): « Accueil » is the root of the breadcrumb, « Recherche » before.
	it('fil d’Ariane long, h1 « Diagnostic … d’une maison de 81,9 m² », sur-titre puis « N° {numero} » en chiffres tabulaires, ligne d’identité', () => {
		const { container, getByRole } = afficher({ kind: 'ok', document: document() });

		const fil = getByRole('navigation', { name: "Fil d'Ariane" });
		expect(within(fil).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['Accueil', '/'],
			['La Roche-sur-Yon', '/commune/85191'],
			['12 Rue des Lilas', '/adresse/85191_0420_00012']
		]);
		expect(container.querySelector('.document-overline')?.textContent).toBe('Document DPE');
		const h1 = getByRole('heading', { level: 1 });
		expect(h1.textContent).toBe(`Diagnostic de performance énergétique d’une maison de 81,9${NB}m²`);
		const numero = container.querySelector('.document-sous .document-numero')!;
		expect(numero.textContent).toBe(`N°${NB}${NUMERO}`);
		expect(numero.querySelector('.chiffre')?.textContent).toBe(NUMERO);
		expect(container.querySelector('.document-sous')?.textContent).toContain('Document DPE');
		expect(container.querySelector('.document-identity')?.textContent).toContain('Établi le 12 mars 2024');
		expect(container.querySelector('.document-identity')?.textContent).toContain('valable jusqu');
		// P4 (2026-11-10): the method is said by « Provenance et validité » only, no longer in this line.
		expect(container.querySelector('.document-identity')?.textContent).not.toContain('méthode');
		expect(container.querySelector('[data-provisoire]')).toBeNull();
		expect(container.textContent).not.toContain('Valeur non affichable');
	});

	it('lignes courtes des repères (ligne 12) : ① ② « par m² · 5 usages » (C1), ③ aucune, ④ « estimation à occupation standard »', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const lignes = [...container.querySelectorAll('dl.kpi-strip > .cle')].map((li) => li.querySelector('.kpi-meta')?.textContent?.trim() ?? null);
		expect(lignes).toEqual(['par m² · 5 usages', 'par m² · 5 usages', null, 'estimation à occupation standard']);
		expect(container.textContent).not.toContain('total annuel');
	});

	it('repère ② : les émissions par m² servies ; le total annuel dans le détail (C1, plan 04.5-12)', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const ligne = container.querySelector('[data-kpi="climat"] [data-field-id]');
		expect(ligne?.getAttribute('data-field-id')).toBe('dpe.document.emissions_ges_m2');
		expect(ligne?.querySelector('.state-value')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('11 kg CO₂ éq/m²/an');
		const total = container.querySelector('dl.tab [data-field-id="dpe.document.emissions_ges"]');
		expect(total?.querySelector('dt')?.textContent).toBe('Émissions annuelles de gaz à effet de serre');
		expect(total?.querySelector('.state-value')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(`${new Intl.NumberFormat('fr-FR').format(1005)} kg CO₂ éq/an`.replace(/\s+/g, ' '));
	});

	it('« Ce que déclare le document » : chaque valeur ouvre son volet Source en une action (D45-08)', async () => {
		const { container, getByRole } = afficher({ kind: 'ok', document: document() });

		const lignes = [...container.querySelectorAll('dl.tab > .tab-l[data-field-id]')];
		expect(lignes.map((ligne) => ligne.getAttribute('data-field-id'))).toEqual([
			'dpe.document.type_batiment',
			'dpe.document.isolation',
			'dpe.document.conso_ep',
			'dpe.document.emissions_ges'
		]);
		for (const ligne of lignes) {
			const libelle = ligne.querySelector('dt')?.textContent ?? '';
			expect(within(ligne as HTMLElement).getAllByRole('button', { name: `Source et définition : ${libelle}` })).toHaveLength(1);
		}
		await fireEvent.click(getByRole('button', { name: "Source et définition : Consommation annuelle d'énergie primaire" }));
		const volet = getByRole('dialog', { name: "Consommation annuelle d'énergie primaire" });
		expect(volet.textContent).toContain('Consommation annuelle totale');
		expect(volet.textContent).toContain('kWhEP/an');
		expect(volet.textContent).toContain('Diagnostics de performance énergétique (DPE)');
	});

	it('repère ① : la consommation par m² servie ; le total annuel dans « Ce que déclare le document » (C1, plan 04.5-12)', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const energie = container.querySelector('[data-kpi="energie"]');
		const ligne = energie?.querySelector('[data-field-id]');
		expect(ligne?.getAttribute('data-field-id')).toBe('dpe.document.conso_ep_m2');
		expect(ligne?.querySelector('.state-value .value')?.textContent).toBe('104');
		expect(ligne?.querySelector('.state-value .unit')?.textContent?.trim()).toBe('kWhEP/m²/an');
		expect(energie?.querySelector('[data-field-id="dpe.document.conso_ep"]')).toBeNull();

		const total = container.querySelector('dl.tab [data-field-id="dpe.document.conso_ep"]');
		expect(total?.querySelector('dt')?.textContent).toBe("Consommation annuelle d'énergie primaire");
		expect(total?.querySelector('.state-value')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(`${new Intl.NumberFormat('fr-FR').format(9120)} kWhEP/an`.replace(/\s+/g, ' '));
	});

	// P2 (inventory of 2026-11-09): « Adresse et rattachement » and « Provenance et validité » are folds of the
	// socle (`depliant depliant-cadre`), closed while the status only says « En vigueur »; their h2 is in the
	// summary. « Ce que déclare le document » stays an open panel. T2: the buttons of the socle.
	it('quatre repères, deux panneaux ouverts puis deux replis fermés, « Voir l’adresse » secondaire', () => {
		const { container, getByRole } = afficher({ kind: 'ok', document: document() });

		expect([...container.querySelectorAll('dl.kpi-strip > .cle > dt')].map((h) => h.textContent)).toEqual([
			'Étiquette énergie',
			'Étiquette climat',
			'Type et surface déclarés',
			'Coût annuel estimé des énergies'
		]);
		// UI-SPEC v4 § 6.7: the landmarks live in the panel « Classes déclarées ».
		expect([...container.querySelectorAll('section.panneau > h2')].map((h) => h.textContent)).toEqual([
			'Classes déclarées',
			'Ce que déclare le document'
		]);
		const replis = [...container.querySelectorAll<HTMLDetailsElement>('details.depliant.depliant-cadre')];
		expect(replis.map((d) => d.querySelector(':scope > summary > h2')?.textContent)).toEqual(['Adresse et rattachement', 'Provenance et validité']);
		expect(replis.map((d) => d.id)).toEqual(['rattachement', 'provenance']);
		expect(replis.map((d) => d.open)).toEqual([false, false]);
		// The headings of the page keep the outline: h1, then four h2 (the source panels have their own).
		expect([...container.querySelectorAll('h2:not(dialog h2)')].map((h) => h.textContent)).toEqual([
			'Classes déclarées',
			'Ce que déclare le document',
			'Adresse et rattachement',
			'Provenance et validité'
		]);
		const voir = getByRole('link', { name: "Voir l'adresse" });
		expect(voir.getAttribute('href')).toBe('/adresse/85191_0420_00012');
		expect(voir.classList.contains('bouton-secondaire')).toBe(true);
		expect(container.querySelector('.document-secondary, .document-primary')).toBeNull();
	});

	it('« Adresse et rattachement » : adresse publiée entre guillemets, BAN en lien, divergence, phrase fixe', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const declaree = container.querySelector('[data-ligne="adresse-declaree"] dd');
		expect(declaree?.textContent?.trim()).toBe(`«${NNB}14 rue des Lilas${NNB}»`);
		const ban = container.querySelector('[data-ligne="adresse-ban"] dd');
		expect(ban?.querySelector('a')?.getAttribute('href')).toBe('/adresse/85191_0420_00012');
		expect(ban?.textContent).toContain('clé BAN déclarée par la source, non vérifiée');
		const texte = container.textContent ?? '';
		expect(texte).toContain(`L'adresse déclarée diffère de l'adresse retenue${NNB}; les deux sont conservées.`);
		expect(texte.match(/Ce document n'est rattaché à aucun logement précis\./g)).toHaveLength(1);
	});

	it('« Ce que déclare le document » : champ vide « Non renseigné », jamais « Non disponible »', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const isolation = container.querySelector('[data-field-id="dpe.document.isolation"]');
		expect(isolation?.querySelector('dt')?.textContent).toBe('Isolation');
		expect(isolation?.querySelector('.state-badge')?.textContent).toBe('Non renseigné');
		expect(container.textContent).not.toMatch(/non disponible/i);
	});

	it('« Provenance et validité » : ADEME, jeu servi en lien https, importation, statut', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const lien = container.querySelector('[data-ligne="source"] a');
		expect(lien?.textContent).toBe('DPE des logements existants');
		expect(lien?.getAttribute('href')).toBe('https://data.ademe.fr/datasets/dpe03existant');
		// P7: the row is a row of ListeValeurs, whose markup leaves spaces around the value.
		expect(container.querySelector('[data-ligne="importation"] dd')?.textContent?.trim()).toBe('30 septembre 2026');
		expect(container.querySelector('[data-ligne="methode"] dd')?.textContent?.trim()).toBe('Méthode actuelle (depuis juillet 2021)');
		expect(container.querySelector('[data-ligne="statut"] dd')?.textContent?.trim()).toBe('En vigueur');
		// « Comparer avec un autre DPE » (row 15: absent until 04-10, added then): the primary action,
		// a link to the comparator with this document, which works without JavaScript.
		const comparer = [...container.querySelectorAll('a')].filter((a) => a.textContent?.trim() === 'Comparer avec un autre DPE');
		expect(comparer).toHaveLength(1);
		expect(comparer[0].getAttribute('href')).toBe('/comparer?dpe=2485E0000003C');
		expect(comparer[0].classList.contains('bouton-primaire')).toBe(true);
		expect(comparer[0].getAttribute('data-sveltekit-preload-data')).toBe('tap');
	});

	it.each([
		['ancienne_methode', "Établi selon l'ancienne méthode (avant juillet 2021)"],
		['validite_inconnue', 'Validité non déterminée'],
		['dates_incoherentes', `Dates incohérentes${NB}: validité non déterminée`],
		['etablissement_futur', `Date d'établissement postérieure à aujourd'hui${NB}: validité non déterminée`]
	])('statut %s : « %s », sans marque provisoire', (code, texte) => {
		const statut = { code, validite: null, date: null, remplace_par: null, remplace: null };
		const { container } = afficher({ kind: 'ok', document: document({ statut }) });

		expect(container.querySelector('[data-ligne="statut"] dd')?.textContent?.trim()).toBe(texte);
		expect(container.querySelector('[data-provisoire]')).toBeNull();
	});

	it('méthode inconnue : « Méthode non déterminée » dans la provenance seulement (P4)', () => {
		const { container } = afficher({ kind: 'ok', document: document({ methode: 'inconnue' }) });

		expect(container.querySelector('.document-identity')?.textContent).not.toContain('méthode');
		expect(container.querySelector('[data-ligne="methode"] dd')?.textContent?.trim()).toBe('Méthode non déterminée');
	});

	it('adresse BAN : clé introuvable (ligne 9) et libellé non servi (ligne 10)', () => {
		const cleInconnue = { etat: 'cle_inconnue' as const, ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null };
		const inconnue = afficher({ kind: 'ok', document: document({ adresse_ban: cleInconnue }) });
		expect(inconnue.container.querySelector('[data-ligne="adresse-ban"] dd')?.textContent?.trim()).toBe(
			'Clé BAN déclarée par la source, introuvable dans la BAN importée'
		);
		inconnue.unmount();

		const sansLibelle = { ...document().adresse_ban!, libelle: null };
		const { container } = afficher({ kind: 'ok', document: document({ adresse_ban: sansLibelle }) });
		const lien = container.querySelector('[data-ligne="adresse-ban"] dd a');
		expect(lien?.textContent).toBe(`Adresse de la BAN non déterminée${NB}: les sources ne s'accordent pas`);
		expect(lien?.getAttribute('href')).toBe('/adresse/85191_0420_00012');
		expect(container.querySelector('[data-provisoire]')).toBeNull();
	});

	it('remplacé selon l’ADEME : le remplaçant en lien ; document remplacé en lien', () => {
		const statut = { code: 'remplace', validite: 'expired', date: null, remplace_par: '2585E0000004D', remplace: '2185E0000001A' };
		const { container } = afficher({ kind: 'ok', document: document({ statut }) });

		const ligne = container.querySelector('[data-ligne="statut"] dd');
		expect(ligne?.textContent?.trim()).toBe(`Remplacé par le DPE n°${NB}2585E0000004D selon l'ADEME`);
		expect(ligne?.querySelector('a')?.getAttribute('href')).toBe('/dpe/2585E0000004D');
		expect(container.querySelector('[data-ligne="document-remplace"] a')?.getAttribute('href')).toBe('/dpe/2185E0000001A');
		expect([...container.querySelectorAll('.document-badge')].map((b) => b.textContent)).toEqual(['Expiré']);
	});

	it('adresse masquée : « non affichée : {motif} », pas de phrase de divergence', () => {
		const { container } = afficher({
			kind: 'ok',
			document: document({ adresse_declaree: { etat: 'masquee', texte: null, motif: 'non_verifiable_sans_complement' } })
		});

		expect(container.querySelector('[data-ligne="adresse-declaree"] dd')?.textContent?.trim()).toBe(
			`non affichée${NB}: elle ne se vérifie pas comme une adresse sans complément.`
		);
		expect(container.textContent).not.toContain("L'adresse déclarée diffère");
	});

	it('sans adresse BAN servie : fil court, pas de « Voir l’adresse »', () => {
		const sansCle = { etat: 'sans_cle' as const, ban_id: null, libelle: null, numero: null, rep: null, nom_voie: null, commune: null, motif: null };
		const { getByRole, queryByRole } = afficher({ kind: 'ok', document: document({ adresse_ban: sansCle }) });

		const fil = getByRole('navigation', { name: "Fil d'Ariane" });
		expect(within(fil).getAllByRole('link').map((a) => a.textContent)).toEqual(['Accueil']);
		expect(queryByRole('link', { name: "Voir l'adresse" })).toBeNull();
	});

	it('document retiré : aucun descriptif, seule la provenance', async () => {
		const retire = document({
			disponibilite: 'retire',
			methode: 'inconnue',
			statut: { code: 'retire', validite: null, date: null, remplace_par: null, remplace: null },
			dates: null,
			reperes: null,
			caracteristiques: [],
			adresse_declaree: null,
			adresse_ban: null,
			adresse_divergente: null
		});
		const { container, getByRole } = afficher({ kind: 'ok', document: retire });

		// P2: its only section is the fold of the provenance, open (its status is not « En vigueur »).
		expect(container.querySelectorAll('section.panneau')).toHaveLength(0);
		const provenance = container.querySelector<HTMLDetailsElement>('details#provenance')!;
		expect(provenance.querySelector(':scope > summary > h2')?.textContent).toBe('Provenance et validité');
		expect(provenance.open).toBe(true);
		// Plan 04.5-12: no landmark, no per-m² value, no yearly total of a retired document.
		expect(container.querySelector('.kpi-strip')).toBeNull();
		expect(container.querySelector('.echelle-dpe')).toBeNull();
		expect(container.querySelectorAll('[data-field-id]')).toHaveLength(0);
		expect(container.textContent).not.toMatch(/kWhEP|CO₂|par m²|total annuel|Consommation annuelle|Émissions annuelles/);
		// 2026-10-05, row 7: « Plus publié par l'ADEME », its sentence in the panel of the status.
		// The served status, without the word of its source action (text button since 04.8-02, E4).
		const statut = container.querySelector('[data-ligne="statut"] dd')!.cloneNode(true) as Element;
		statut.querySelectorAll('.source-button').forEach((action) => action.remove());
		expect(statut.textContent?.trim()).toBe("Plus publié par l'ADEME");
		expect(container.querySelector('[data-provisoire]')).toBeNull();
		await fireEvent.click(getByRole('button', { name: 'Source et définition : statut du document' }));
		const volet = getByRole('dialog', { name: 'Statut' });
		expect(volet.textContent).toContain(`La source ne publie plus ce document${NNB}; DataCommune conserve son numéro et son statut.`);
	});

	// P1 (inventory of 2026-11-09): the classes come before the rest. The header keeps the breadcrumb, the h1
	// and the short identity line; the actions follow « Classes déclarées »; « Ce que nous ne montrons pas »
	// ends the page, as on the address fiche.
	it('P1 : les classes avant les actions, le lien des limites en fin de page', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		const header = container.querySelector('.document-header')!;
		expect(header.querySelectorAll('a, button')).toHaveLength(0);
		const classes = container.querySelector('section.panneau-essentiel')!;
		const actions = container.querySelector('.document-actions')!;
		const declare = container.querySelector('section#declare')!;
		const limites = container.querySelector('.lien-limites')!;
		const suit = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
		expect(suit(header, classes)).toBe(true);
		expect(suit(classes, actions)).toBe(true);
		expect(suit(actions, declare)).toBe(true);
		expect(suit(container.querySelector('details#provenance')!, limites)).toBe(true);
	});

	// P2: a status that is not « En vigueur » is a mention of validity; its fold opens by itself.
	it.each([
		['expire', 'expired'],
		['remplace', 'expired'],
		['remplacement_non_verifie', 'not_expired_by_date'],
		['validite_inconnue', null]
	])('P2 : statut %s → « Provenance et validité » ouvert, « Adresse et rattachement » fermé', (code, validite) => {
		const statut = { code, validite, date: null, remplace_par: null, remplace: null };
		const { container } = afficher({ kind: 'ok', document: document({ statut }) });

		expect(container.querySelector<HTMLDetailsElement>('details#provenance')?.open).toBe(true);
		expect(container.querySelector<HTMLDetailsElement>('details#rattachement')?.open).toBe(false);
	});

	// P7 (inventory of 2026-11-09): the rows of the two folds are rows of the shared `ListeValeurs` (`dl.tab`),
	// no longer a copy of it (`.document-dl`); the values that carry links are given as page content.
	it('P7 : les lignes des deux replis sont des lignes de ListeValeurs, liens compris', () => {
		const statut = { code: 'remplace', validite: 'expired', date: null, remplace_par: '2585E0000004D', remplace: '2185E0000001A' };
		const { container } = afficher({ kind: 'ok', document: document({ statut }) });

		expect(container.querySelector('.document-dl, .document-dl-row')).toBeNull();
		const lignes = (repli: string) => [...container.querySelectorAll(`details#${repli} dl.tab > .tab-l`)].map((l) => l.getAttribute('data-ligne'));
		expect(lignes('rattachement')).toEqual(['adresse-declaree', 'adresse-ban']);
		expect(lignes('provenance')).toEqual(['source', 'importation', 'methode', 'statut', 'document-remplace']);
		expect(container.querySelector('[data-ligne="adresse-ban"] .tab-v a.document-lien')?.getAttribute('href')).toBe('/adresse/85191_0420_00012');
		expect(container.querySelector('[data-ligne="source"] .tab-v a.document-lien')?.getAttribute('href')).toBe('https://data.ademe.fr/datasets/dpe03existant');
		expect(container.querySelector('[data-ligne="statut"] .tab-v a.document-lien')?.getAttribute('href')).toBe('/dpe/2585E0000004D');
		expect(container.querySelector('[data-ligne="document-remplace"] .tab-v a.document-lien')?.getAttribute('href')).toBe('/dpe/2185E0000001A');
		expect(container.querySelector('[data-ligne="importation"] .tab-v .state-value')?.textContent?.trim()).toBe('30 septembre 2026');
	});

	// P2: printing opens the two folds and gives each its state back afterwards (as the commune fiche).
	it('P2 : l’impression ouvre les deux replis, puis rend leur état', async () => {
		const { container } = afficher({ kind: 'ok', document: document() });
		const etats = () => [...container.querySelectorAll<HTMLDetailsElement>('details.document-repli')].map((d) => d.open);

		expect(etats()).toEqual([false, false]);
		window.dispatchEvent(new Event('beforeprint'));
		expect(etats()).toEqual([true, true]);
		window.dispatchEvent(new Event('afterprint'));
		expect(etats()).toEqual([false, false]);
	});

	// P6: the panels of the document wear the tint of the « logement » category of the fiches.
	it('P6 : « Classes déclarées » et « Ce que déclare le document » à la teinte logement', () => {
		const { container } = afficher({ kind: 'ok', document: document() });

		expect([...container.querySelectorAll('section.panneau.teinte-q-logement > h2')].map((h) => h.textContent)).toEqual([
			'Classes déclarées',
			'Ce que déclare le document'
		]);
	});

	// T1: the sharing card of the socle; the page stays out of the index, without a canonical.
	it('T1 : carte de partage par MetaPartage, noindex, follow, sans canonique', () => {
		afficher({ kind: 'ok', document: document() });

		// `document` names the fixture of this file: the DOM is read through `globalThis`.
		const head = globalThis.document.head;
		const meta = (selecteur: string) => head.querySelector(selecteur)?.getAttribute('content');
		expect(head.querySelector('title')?.textContent).toBe(`DPE n°${NB}${NUMERO} — DataCommune`);
		expect(meta('meta[name="robots"]')).toBe('noindex, follow');
		expect(head.querySelectorAll('meta[name="robots"]')).toHaveLength(1);
		expect(head.querySelector('link[rel="canonical"]')).toBeNull();
		expect(meta('meta[property="og:title"]')).toBe(`DPE n°${NB}${NUMERO} — DataCommune`);
		expect(meta('meta[property="og:url"]')).toBe(`${SITE_URL}/dpe/${NUMERO}`);
		expect(meta('meta[name="twitter:card"]')).toBe('summary_large_image');
		expect(meta('meta[name="description"]')).toBe(
			`Document DPE n°${NB}${NUMERO} : classes énergie et climat, dates de validité, adresse déclarée et provenance, avec leur source.`
		);
		expect(meta('meta[property="og:description"]')).toBe(meta('meta[name="description"]'));
	});

	it('expiré sans date servie : « Expiré » (ligne 8)', () => {
		const statut = { code: 'expire', validite: 'expired', date: null, remplace_par: null, remplace: null };
		const { container } = afficher({ kind: 'ok', document: document({ statut }) });

		expect(container.querySelector('[data-ligne="statut"] dd')?.textContent?.trim()).toBe('Expiré');
	});
});

// « Classes déclarées » (04.8-04, UI-SPEC v4 § 6.7): two rows « Énergie : classe X » and « Climat : classe Y »,
// each with the A to G scale whose served class is current, then the landmarks as tiles, then the note.
describe('/dpe/[numero] — Classes déclarées (UI-SPEC v4 § 6.7)', () => {
	it('two rows, each with its A to G scale, the served class current; then the tiles and the note', () => {
		const { container } = afficher({ kind: 'ok', document: document() });
		const panneau = container.querySelector('section.panneau-essentiel')!;
		expect(panneau.querySelector('h2')?.textContent).toBe('Classes déclarées');
		const lignes = [...panneau.querySelectorAll('.dpe-l')];
		expect(lignes.map((l) => l.querySelector('.dpe-lt')?.textContent?.replace(/[ \t\r\n]+/g, ' ').trim())).toEqual([
			`Énergie${NB}: classe C`,
			`Climat${NB}: classe B`
		]);
		for (const [index, classe] of ['C', 'B'].entries()) {
			const echelle = lignes[index].querySelector('ol.echelle-dpe')!;
			expect([...echelle.querySelectorAll('li')].map((li) => li.querySelector('.lettre')?.textContent?.replace(/^Classe\s*/, ''))).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G']);
			const actives = [...echelle.querySelectorAll('li[aria-current="true"]')];
			expect(actives).toHaveLength(1);
			expect(actives[0].className).toContain(`dpe-${classe}`);
			expect(echelle.getAttribute('aria-label')).toBe(`Classes A à G, classe ${classe}`);
		}
		// P4 (inventory of 2026-11-09): the per-m² value is read once, in its tile with its « i », no longer
		// repeated under the scale.
		expect(panneau.querySelector('.dpe-lv')).toBeNull();
		expect(panneau.textContent?.match(/\b104\b/g)).toHaveLength(1);
		const tuiles = panneau.querySelector('dl.cles.kpi-strip')!;
		expect(lignes[1].compareDocumentPosition(tuiles) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		const note = panneau.querySelector('.dc-note')!;
		expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'Ce document décrit un logement à une date, tel que l’a saisi le diagnostiqueur. Ce n’est pas une mesure du bâtiment entier.'
		);
		expect(tuiles.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('a class the document does not serve keeps its served state and no current class', () => {
		const base = document();
		const sansClasse = document({
			reperes: { ...base.reperes!, etiquette_energie: { ...base.reperes!.etiquette_energie, classe: valeur({ field_id: 'dpe.document.etiquette_dpe', etat: 'not_reported', valeur: null }) } }
		});
		const { container } = afficher({ kind: 'ok', document: sansClasse });
		const energie = container.querySelector('[data-classe-declaree="energie"]')!;
		expect(energie.querySelector('.dpe-lt .state-badge')?.textContent).toBe('Non renseigné');
		expect(energie.querySelector('li[aria-current]')).toBeNull();
		expect(energie.querySelector('ol.echelle-dpe')?.getAttribute('aria-label')).toBe('Classes A à G');
	});

	it('names no object when the type is not served (no « logement » guessed), and the served type otherwise', () => {
		const base = document();
		const sansType = document({
			caracteristiques: [],
			reperes: { ...base.reperes!, logement_decrit: { type_batiment: valeur({ field_id: 'dpe.document.type_batiment', etat: 'not_reported', valeur: null }), surface: valeur({ field_id: 'dpe.document.surface_habitable', valeur: 45 }) } }
		});
		expect(afficher({ kind: 'ok', document: sansType }).getByRole('heading', { level: 1 }).textContent).toBe('Diagnostic de performance énergétique');
	});

	it('a building DPE is named and noted as a building, never as a dwelling (Opus 04-1, P1)', () => {
		const base = document();
		const immeuble = document({
			caracteristiques: [valeur({ field_id: 'dpe.document.type_batiment', valeur: 'immeuble' })],
			reperes: { ...base.reperes!, logement_decrit: { type_batiment: valeur({ field_id: 'dpe.document.type_batiment', valeur: 'immeuble' }), surface: valeur({ field_id: 'dpe.document.surface_habitable', etat: 'not_reported', valeur: null }) } }
		});
		const { container, getByRole } = afficher({ kind: 'ok', document: immeuble });
		expect(getByRole('heading', { level: 1 }).textContent).toBe('Diagnostic de performance énergétique d’un immeuble');
		const note = container.querySelector('section.panneau-essentiel .dc-note')?.textContent ?? '';
		expect(note).toBe('Ce document décrit un immeuble à une date, tel que l’a saisi le diagnostiqueur.');
		expect(note).not.toContain('logement');
	});

	it('a served type without an article is written as served, never replaced by a guessed noun', () => {
		const base = document();
		const autre = document({
			caracteristiques: [valeur({ field_id: 'dpe.document.type_batiment', valeur: 'autre' })],
			reperes: { ...base.reperes!, logement_decrit: { type_batiment: valeur({ field_id: 'dpe.document.type_batiment', valeur: 'autre' }), surface: valeur({ field_id: 'dpe.document.surface_habitable', valeur: 45 }) } }
		});
		const { container, getByRole } = afficher({ kind: 'ok', document: autre });
		expect(getByRole('heading', { level: 1 }).textContent).toBe(`Diagnostic de performance énergétique${NB}(Autre, 45${NB}m²)`);
		expect(container.querySelector('section.panneau-essentiel .dc-note')?.textContent).not.toContain('logement');
	});
});

describe('/dpe/[numero] — page d’erreur : inconnu, mal formé, panne (SEM-01, 2026-10-05)', () => {
	function rendreErreur(status: number, error: { message: string; departements?: string[] }, numero = NUMERO) {
		pageStore.set({ status, error, params: { numero }, url: new URL(`https://site.invalid/dpe/${numero}`) });
		return render(ErrorPage);
	}

	afterEach(() => {
		pageStore.set({ status: 200, error: null, params: {}, url: new URL('https://site.invalid/') });
	});

	// Owner decision of 2026-11-09: with the whole country served, the page says how many departements,
	// it no longer lists them in a sentence.
	it('DPE inconnu (404), France entière : le nombre de départements, jamais leur liste', () => {
		// The served set of production: Saint-Pierre-et-Miquelon (975) is in the list of names, not served.
		const tous = DEPARTEMENTS.map((departement) => departement.code).filter((code) => code !== '975');
		const { container } = rendreErreur(404, { message: 'DPE inconnu', departements: tous });

		expect(container.textContent).toContain(
			`dans la base importée par ce site (101${NB}départements). Cela ne veut pas dire qu'il n'existe pas.`
		);
		expect(container.textContent).not.toContain('Ain');
		expect(container.textContent).not.toContain('Mayotte');
	});

	it('DPE inconnu (404) : h1 « DPE n° {numero} », la couverture nommée, deux liens, jamais une panne', () => {
		const { container, getByRole } = rendreErreur(404, { message: 'DPE inconnu', departements: ['44', '85'] });

		expect(getByRole('heading', { level: 1 }).textContent).toBe(`DPE n°${NB}${NUMERO}`);
		expect(container.textContent).toContain(`qui couvre Loire-Atlantique${NB}(44) et Vendée${NB}(85). Cela ne veut pas dire qu'il n'existe pas.`);
		expect(getByRole('link', { name: 'Revenir à la recherche' }).getAttribute('href')).toBe('/');
		expect(getByRole('link', { name: 'Voir la couverture des données' }).getAttribute('href')).toBe('/datasets');
		expect(container.textContent).not.toMatch(/momentanément indisponible/);
	});

	it('DPE inconnu sans DPE hébergé (couverture vide) : aucun document importé par ce site (ligne 11)', () => {
		const { container } = rendreErreur(404, { message: 'DPE inconnu', departements: [] });

		expect(container.querySelector('.notice p')?.textContent).toBe("Aucun document DPE n'est importé par ce site.");
		expect(container.querySelector('[data-provisoire]')).toBeNull();
	});

	it('numéro mal formé (404) : h1 « Numéro DPE non valide », la saisie entre guillemets dans la phrase seulement', () => {
		const { container, getByRole } = rendreErreur(404, { message: 'Numéro DPE non valide' }, 'abc');

		expect(getByRole('heading', { level: 1 }).textContent).toBe('Numéro DPE non valide');
		expect(container.textContent).toContain(`«${NNB}abc${NNB}» n'a pas la forme d'un numéro DPE de l'ADEME.`);
		expect(container.querySelector('nav')?.textContent).not.toContain('abc');
		// `document` is the fixture builder of this file: the DOM document is read on `window`.
		expect(window.document.title).toBe('Numéro DPE non valide — DataCommune');
		expect(container.querySelector('.kpi-strip')).toBeNull();
	});

	it('T6 (2026-11-10) : le fil d’Ariane d’une page d’erreur part de « Accueil »', () => {
		const cas: [number, { message: string; departements?: string[] }][] = [
			[404, { message: 'DPE inconnu', departements: ['44'] }],
			[404, { message: 'Numéro DPE non valide' }],
			[503, { message: 'Ce document est momentanément indisponible' }]
		];
		for (const [status, error] of cas) {
			const { getByRole, unmount } = rendreErreur(status, error);
			const fil = getByRole('navigation', { name: "Fil d'Ariane" });
			expect([...fil.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['Accueil', '/']]);
			unmount();
		}
	});

	it('panne (503) : un encart warn, aucune valeur, « Recharger le document » relance le chargement', async () => {
		const { container, getByRole } = rendreErreur(503, { message: 'Ce document est momentanément indisponible' });

		expect(getByRole('heading', { level: 1 }).textContent).toBe(`DPE n°${NB}${NUMERO}`);
		const encart = getByRole('alert');
		expect(within(encart).getByRole('heading', { level: 2 }).textContent).toBe('Ce document est momentanément indisponible');
		expect(encart.textContent).toContain("Le service n'a pas répondu. Aucune valeur n'est affichée plutôt qu'une valeur incomplète.");
		expect(container.querySelector('.kpi-strip')).toBeNull();
		await fireEvent.click(getByRole('button', { name: 'Recharger le document' }));
		expect(invalidateAll).toHaveBeenCalledTimes(1);
	});
});

describe('/dpe/[numero] — états rendus par la page (SEM-01)', () => {
	it('429 : titré par panne.ts, jamais « momentanément indisponible »', () => {
		const { getByRole, container } = afficher({ kind: 'panne', panne: { statut: 429, titre: TITRE_LIMITE, message: messageLimite(30) } });

		expect(within(getByRole('alert')).getByRole('heading', { level: 2 }).textContent).toBe(TITRE_LIMITE);
		expect(container.textContent).not.toMatch(/momentanément indisponible/);
		// T2: the reload is the secondary button of the socle.
		expect(getByRole('button', { name: 'Recharger le document' }).classList.contains('bouton-secondaire')).toBe(true);
	});

	it('navigation client vers un autre document : aria-busy, « Chargement du document… », aucun chiffre', () => {
		navigatingStore.set({ to: { route: { id: '/(marketing)/dpe/[numero]' }, params: { numero: '2585E0000004D' } } });
		const { container, getByRole } = afficher({ kind: 'ok', document: document() });

		const section = container.querySelector('[aria-busy="true"]');
		expect(section).not.toBeNull();
		expect(getByRole('heading', { level: 2 }).textContent).toBe('Chargement du document…');
		expect(section!.querySelectorAll('[data-squelette] .cle-squelette')).toHaveLength(4);
		expect(container.querySelector('.echelle-dpe')).toBeNull();
		expect(container.querySelector('.state-value')).toBeNull();
	});
});

describe('/dpe/[numero] — loader serveur', () => {
	function evenement(numero: string, setHeaders = vi.fn()) {
		return {
			params: { numero },
			request: new Request(`https://site.invalid/dpe/${numero}`),
			platform: undefined,
			setHeaders
		} as unknown as Parameters<typeof load>[0];
	}

	it('numéro mal formé : 404 « Numéro DPE non valide », aucun appel à l’API', async () => {
		const fetchSpy = vi.spyOn(globalThis, 'fetch');
		const setHeaders = vi.fn();

		await expect(load(evenement('2485e0000003c', setHeaders))).rejects.toMatchObject({
			status: 404,
			body: { message: 'Numéro DPE non valide' }
		});
		expect(fetchSpy).not.toHaveBeenCalled();
		// Never stored by a cache (IN-10 of the D-15 review).
		expect(setHeaders).toHaveBeenCalledWith({ 'x-robots-tag': 'noindex, follow', 'cache-control': 'private, no-store' });
		fetchSpy.mockRestore();
	});

	function repondre(status: number, body: unknown) {
		return vi
			.spyOn(globalThis, 'fetch')
			.mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
	}

	it('DPE inconnu : 404 avec la couverture servie, jamais la saisie', async () => {
		const fetchSpy = repondre(404, { title: 'DPE inconnu', status: 404, code: 'dpe_inconnu', couverture: { departements: ['44', '85'] } });

		await expect(load(evenement(NUMERO))).rejects.toMatchObject({
			status: 404,
			body: { message: 'DPE inconnu', departements: ['44', '85'] }
		});
		fetchSpy.mockRestore();
	});

	it.each([
		[503, { title: 'x', status: 503, code: 'document_dpe_indisponible' }],
		[503, { detail: 'Données temporairement indisponibles : publication non validée.' }],
		[500, { title: 'x', status: 500 }]
	])('%i → 503, le contrat de panne', async (status, body) => {
		const fetchSpy = repondre(status, body);

		await expect(load(evenement(NUMERO))).rejects.toMatchObject({ status: 503, body: { message: 'Ce document est momentanément indisponible' } });
		fetchSpy.mockRestore();
	});

	it('réseau en échec → 503', async () => {
		const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'));

		await expect(load(evenement(NUMERO))).rejects.toMatchObject({ status: 503 });
		fetchSpy.mockRestore();
	});

	it.each([409, 429, 401, 403])('%i → le refus typé est rendu par la page (200), jamais un 503', async (status) => {
		const fetchSpy = repondre(status, { title: 'x', status });

		const data = await load(evenement(NUMERO));

		expect(data?.issue.kind).toBe('panne');
		fetchSpy.mockRestore();
	});

	it('document servi : noindex, follow et le seul modèle projeté', async () => {
		const reponse = { statut: 'ok', donnees: { ...document(), adresse_brut: 'SENTINELLE', id: 7 } };
		const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
			new Response(JSON.stringify(reponse), { status: 200, headers: { 'Content-Type': 'application/json' } })
		);
		const setHeaders = vi.fn();

		const data = await load(evenement(NUMERO, setHeaders));

		expect(new URL(String(fetchSpy.mock.calls[0][0]), 'http://o.invalid').pathname).toBe(`/v1/dpe/${NUMERO}`);
		expect(setHeaders).toHaveBeenCalledWith({ 'x-robots-tag': 'noindex, follow', 'cache-control': 'private, no-store' });
		// Bounded in time (FRONT WR-03 of the D-15 review).
		expect((fetchSpy.mock.calls[0][1] as RequestInit).signal).toBeInstanceOf(AbortSignal);
		expect(JSON.stringify(data)).not.toContain('SENTINELLE');
		expect(data?.issue.kind).toBe('ok');
		fetchSpy.mockRestore();
	});
});

describe('/dpe/[numero] — « Comparer avec un autre DPE » with a full selection (D-15 FRONT WR-03)', () => {
	it('opens the four documents kept in the tab, never replaces them with this one', async () => {
		const gardes = ['2385E0123456A', '2185E0000001A', '2485E0000004C', '2485E0000005K'];
		documentComparisonSelection.replace(gardes);
		vi.mocked(goto).mockClear();
		const { container } = afficher({ kind: 'ok', document: document() });

		const lien = [...container.querySelectorAll('a')].find((a) => a.textContent?.trim() === 'Comparer avec un autre DPE')!;
		await fireEvent.click(lien);

		expect(goto).toHaveBeenCalledWith(`/comparer?dpe=${gardes.join(',')}`);
		expect(documentComparisonSelection.codes).toEqual(gardes);
		documentComparisonSelection.replace([]);
	});
});
