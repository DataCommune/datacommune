import { fireEvent, render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { communeKpis, type EntreeVolet, type ValeurServie } from '$lib/data/commune-kpi';
import { COMMUNE_85191_OBSERVED, COMMUNE_85191_SOURCES } from '$lib/data/__fixtures__/commune-85191-20261012';
import { lectureCommune } from '$lib/data/lecture';
import SourcePanel from './SourcePanel.svelte';

// Entries as the model builds them (commune-kpi.ts): UI-SPEC order, an entry the API does not
// serve is simply not in the list.
const ENTREES: EntreeVolet[] = [
	{ terme: 'Définition', valeur: 'Médiane communale du prix au mètre carré des maisons.' },
	{ terme: 'Objet', valeur: 'Ventes de maisons (mutations DVF) de la commune' },
	{ terme: 'Période', valeur: '2025-01-01 → 2026-01-01 (fin exclue)' },
	{ terme: 'Unité', valeur: '€/m²' },
	{ terme: 'Source', valeur: 'DVF fixture' },
	{ terme: 'Dernière importation réussie', valeur: '02/08/2026' },
	{ terme: 'Méthode', valeur: 'Médiane des prix au mètre carré' },
	{ terme: 'Effectif', valeur: '4 ventes' },
	{
		terme: 'État et motif',
		absences: [{ libelle: null, etat: { kind: 'absence', etat: 'insufficient_sample', motif: 'Moins de 5 ventes.', effectif: 4 } }]
	},
	{ terme: 'Limites', valeur: 'Révisions de la source possibles.' }
];

const BRUT: ValeurServie[] = [
	{ fieldId: 'dvf.indicateurs.maison.mediane_courante', valeur: 'null', etat: { kind: 'absence', etat: 'insufficient_sample', motif: 'Moins de 5 ventes.' } }
];

function termes(container: HTMLElement): string[] {
	return [...container.querySelectorAll('.source-panel-list > div > dt')].map((dt) => dt.textContent ?? '');
}

describe('SourcePanel — volet source en <dialog> (E6)', () => {
	it('renders the definition list in the UI-SPEC order', () => {
		const { container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		expect(termes(container)).toEqual([
			'Définition', 'Objet', 'Période', 'Unité', 'Source', 'Dernière importation réussie',
			'Méthode', 'Effectif', 'État et motif', 'Limites'
		]);
		expect(container.querySelector('dialog')).not.toBeNull();
	});

	it('omits an entry that is not served, never a 0 nor a dash', () => {
		const sansEffectif = ENTREES.filter((entree) => entree.terme !== 'Effectif');
		const vide: EntreeVolet[] = [...sansEffectif, { terme: 'Méthode', valeur: '  ' }].filter(
			(entree, index, all) => all.findIndex((other) => other.terme === entree.terme) === index
		);
		const { container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: [...vide, { terme: 'Objet bis', valeur: '' }], brut: BRUT });
		expect(termes(container)).not.toContain('Effectif');
		expect(termes(container)).not.toContain('Objet bis');
		const texte = container.querySelector('.source-panel-list')?.textContent ?? '';
		expect(texte).not.toMatch(/(^|\s)(0|—|-)(\s|$)/);
	});

	it('shows the served raw value with its field_id and a glossary state, never the state key', () => {
		const { container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const raw = container.querySelector('.source-panel-raw');
		expect(raw?.querySelector('summary')?.textContent).toBe("Voir la donnée telle que l'API la renvoie");
		expect(raw?.querySelector('dt')?.textContent).toBe('Identifiant du champ (field_id)');
		expect(raw?.textContent).toContain('dvf.indicateurs.maison.mediane_courante');
		expect(raw?.textContent).toContain('Effectif insuffisant');
		expect(container.textContent).not.toContain('insufficient_sample');
	});

	it('renders several raw values of one field, each under its object (comparator row, lot 6)', () => {
		const brut = [
			{ fieldId: 'dvf.nb', valeur: '12', etat: { kind: 'valeur' }, objet: 'Ancienne (44991)' },
			{ fieldId: 'dvf.nb', valeur: '48', etat: { kind: 'valeur' }, objet: 'Courante (44992)' }
		] satisfies (ValeurServie & { objet: string })[];
		const { container } = render(SourcePanel, { titre: 'Ventes', entrees: ENTREES, brut });
		const blocs = [...container.querySelectorAll('.source-panel-raw-bloc')];
		expect(blocs.map((bloc) => bloc.querySelector('.source-panel-sub')?.textContent)).toEqual(['Ancienne (44991)', 'Courante (44992)']);
		expect(blocs[1].textContent).toContain('48');
	});

	it('words a served reason: a sentence is shown, a technical key never (03-17 F57)', () => {
		const avecMotif = (motif: string): EntreeVolet[] => [
			{ terme: 'État et motif', absences: [{ libelle: null, etat: { kind: 'absence', etat: 'not_qualified', motif, effectif: null } }] }
		];
		const phrase = render(SourcePanel, { titre: 'Phrase', entrees: avecMotif('Moins de 5 ventes.'), brut: [] });
		expect(phrase.container.querySelector('.source-panel-reason')?.textContent).toBe('Motif : Moins de 5 ventes.');
		const table = render(SourcePanel, { titre: 'Table', entrees: avecMotif('empty_sample'), brut: [] });
		expect(table.container.querySelector('.source-panel-reason')?.textContent).toBe('Motif : Aucune vente retenue sur la période');
		const cle = render(SourcePanel, { titre: 'Clé', entrees: avecMotif('some_unknown_key'), brut: [] });
		expect(cle.container.querySelector('.source-panel-reason')).toBeNull();
		expect(cle.container.textContent).not.toContain('some_unknown_key');
	});

	it('opens on the title and gives the focus back to the button that opened it', async () => {
		const declencheur = document.createElement('button');
		declencheur.textContent = 'i';
		document.body.append(declencheur);
		const { component, container, getByRole } = render(SourcePanel, {
			titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT
		});
		const dialog = container.querySelector('dialog')!;
		expect(dialog.hasAttribute('open')).toBe(false);

		component.ouvrir(declencheur);
		expect(dialog.hasAttribute('open')).toBe(true);
		expect(document.activeElement?.textContent).toBe('Prix médian des maisons');

		await fireEvent.click(getByRole('button', { name: 'Fermer' }));
		expect(dialog.hasAttribute('open')).toBe(false);
		expect(document.activeElement).toBe(declencheur);
		declencheur.remove();
	});

	it('closes on a click on the backdrop and returns the focus, never on a click inside', async () => {
		const declencheur = document.createElement('button');
		document.body.append(declencheur);
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(declencheur);

		await fireEvent.click(container.querySelector('.source-panel-list')!);
		await fireEvent.click(container.querySelector('.source-panel-inner')!);
		expect(dialog.hasAttribute('open')).toBe(true);

		// A click on the backdrop of a modal dialog targets the dialog element itself.
		await fireEvent.click(dialog);
		expect(dialog.hasAttribute('open')).toBe(false);
		expect(document.activeElement).toBe(declencheur);
		declencheur.remove();
	});

	it('closes on Escape and returns the focus', async () => {
		const declencheur = document.createElement('button');
		document.body.append(declencheur);
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(declencheur);
		await fireEvent.keyDown(dialog, { key: 'Escape' });
		expect(dialog.hasAttribute('open')).toBe(false);
		expect(document.activeElement).toBe(declencheur);
		declencheur.remove();
	});

	it('a late close event never takes back a focus that moved on (flaky 85191 e2e, 2026-10-20)', async () => {
		// A native modal dialog gives the focus back when it closes, but fires `close` in a later
		// task: by then the focus may be on another button, whose Enter must open its own panel.
		const declencheur = document.createElement('button');
		const suivant = document.createElement('button');
		document.body.append(declencheur, suivant);
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(declencheur);

		dialog.removeAttribute('open');
		suivant.focus();
		dialog.dispatchEvent(new Event('close'));
		expect(document.activeElement).toBe(suivant);
		declencheur.remove();
		suivant.remove();
	});

	it('gives the focus back to the trigger when it dropped to the body (IN-07)', () => {
		const declencheur = document.createElement('button');
		document.body.append(declencheur);
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(declencheur);

		dialog.removeAttribute('open');
		(document.activeElement as HTMLElement).blur();
		expect(document.activeElement).toBe(document.body);
		dialog.dispatchEvent(new Event('close'));
		expect(document.activeElement).toBe(declencheur);
		declencheur.remove();
	});

	it('takes the focus back from the element focused before opening when the click did not focus the trigger (WR-01)', () => {
		// Safari/Firefox on macOS: a click does not focus the button, so a field focused earlier keeps
		// the focus; showModal() remembers it and the native close gives it back to that field.
		const champ = document.createElement('input');
		const declencheur = document.createElement('button');
		document.body.append(champ, declencheur);
		champ.focus();
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(declencheur);

		dialog.removeAttribute('open');
		champ.focus(); // what the native close does
		dialog.dispatchEvent(new Event('close'));
		expect(document.activeElement).toBe(declencheur);
		champ.remove();
		declencheur.remove();
	});

	it('keeps the new trigger when the panel is reopened before the late close of the previous one (IN-06)', () => {
		const premier = document.createElement('button');
		const second = document.createElement('button');
		document.body.append(premier, second);
		const { component, container } = render(SourcePanel, { titre: 'Prix médian des maisons', entrees: ENTREES, brut: BRUT });
		const dialog = container.querySelector('dialog')!;
		component.ouvrir(premier);
		dialog.removeAttribute('open');
		component.ouvrir(second);

		dialog.dispatchEvent(new Event('close')); // the late close of the first opening
		(document.activeElement as HTMLElement).blur(); // the real close drops the focus
		dialog.removeAttribute('open');
		dialog.dispatchEvent(new Event('close')); // the close of the second one
		expect(document.activeElement).toBe(second);
		premier.remove();
		second.remove();
	});
});

// 04-18 (SEM-02, UI-SPEC « Volet ⓘ — deltas »): « Rattachement » after « Objet » and « Référence »
// with its served https link, both rendered only when the API serves them.
describe('SourcePanel — entrées « Rattachement » et « Référence » (04-18)', () => {
	const REFERENCE = '2 500 €/m² · La Roche-sur-Yon · Demandes de valeurs foncières (DVF), 2021-2025';

	it('renders « Rattachement » where the model puts it, right after « Objet »', () => {
		const { container } = render(SourcePanel, {
			titre: 'DPE déclarés à cette adresse',
			entrees: [
				{ terme: 'Définition', valeur: 'Nombre de DPE.' },
				{ terme: 'Objet', valeur: 'Document DPE' },
				{ terme: 'Rattachement', valeur: 'Adresse saisie par le diagnostiqueur (non vérifiée)' },
				{ terme: 'Source', valeur: 'ADEME' }
			],
			brut: []
		});

		expect(termes(container)).toEqual(['Définition', 'Objet', 'Rattachement', 'Source']);
		expect(container.querySelector('.source-panel-list > div:nth-child(3) dd')?.textContent).toBe(
			'Adresse saisie par le diagnostiqueur (non vérifiée)'
		);
	});

	it('« Référence » carries its served https link', () => {
		const { container } = render(SourcePanel, {
			titre: 'Maisons',
			entrees: [{ terme: 'Référence', valeur: REFERENCE, url: 'https://www.data.gouv.fr/datasets/dvf' }],
			brut: []
		});

		const lien = container.querySelector('.source-panel-list a');
		expect(lien?.textContent).toBe(REFERENCE);
		expect(lien?.getAttribute('href')).toBe('https://www.data.gouv.fr/datasets/dvf');
	});

	it('a non-https reference URL is rendered as text, never as a link (T-04-18-02)', () => {
		const { container } = render(SourcePanel, {
			titre: 'Maisons',
			entrees: [{ terme: 'Référence', valeur: REFERENCE, url: 'javascript:alert(1)' }],
			brut: []
		});

		expect(container.querySelector('.source-panel-list a')).toBeNull();
		expect(container.querySelector('.source-panel-list dd')?.textContent).toBe(REFERENCE);
	});

	it('neither entry is rendered when it is not served (empty value)', () => {
		const { container } = render(SourcePanel, {
			titre: 'Repère',
			entrees: [
				{ terme: 'Objet', valeur: 'Point' },
				{ terme: 'Rattachement', valeur: '' },
				{ terme: 'Référence', valeur: '', url: 'https://example.org' }
			],
			brut: []
		});

		expect(termes(container)).toEqual(['Objet']);
	});
});

// 04.5-01 (D45-08, UI-027): a reading line hands the panel the public volet and raw values as they
// are; the panel mounted from it is the panel mounted from the commune landmark.
describe('SourcePanel — monté depuis une ligne de lecture (04.5-01)', () => {
	function paires(container: HTMLElement): string[] {
		return [...container.querySelectorAll('dt, dd')].map((element) => `${element.tagName}:${element.textContent}`);
	}

	it('renders the same dt/dd from a LigneLecture as from communeKpis', () => {
		const commune = COMMUNE_85191_OBSERVED as unknown as CommuneSynthese;
		const sources = COMMUNE_85191_SOURCES as unknown as SourceAttribution[];
		const kpis = communeKpis(commune, sources);
		const [essentiel] = lectureCommune(commune, sources);
		expect(essentiel.lignes).toHaveLength(kpis.length);
		essentiel.lignes.forEach((ligne, index) => {
			const kpi = kpis[index];
			const depuisKpi = render(SourcePanel, { titre: kpi.libelle, entrees: kpi.volet, brut: kpi.lignes.map((item) => item.brut), source: kpi.source });
			const depuisLecture = render(SourcePanel, { titre: ligne.libelle, entrees: ligne.volet, brut: ligne.brut, source: ligne.source });
			const attendu = paires(depuisKpi.container);
			expect(attendu.length).toBeGreaterThan(0);
			expect(paires(depuisLecture.container)).toEqual(attendu);
			depuisKpi.unmount();
			depuisLecture.unmount();
		});
	});
});
