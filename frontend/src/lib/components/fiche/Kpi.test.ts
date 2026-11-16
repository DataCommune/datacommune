import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { COMMUNE_85047_CHALLANS } from '$lib/data/__fixtures__/commune-85';
import { communeKpis, type KpiCommune, type KpiLigne } from '$lib/data/commune-kpi';
import type { KpiDocument } from '$lib/data/document-dpe';
import Kpi from './Kpi.svelte';

// A KPI card of the fiche on a phone (03-17 R-1, V28-V31, SEM-01): title, value or badge of the
// served state with its `i` button, ONE short line `effectif · période`; the source, the sentence
// of the state and the qualification live in the source panel, never on the card.

const brut = (fieldId: string): KpiLigne['brut'] => ({ fieldId, valeur: 'null', etat: { kind: 'valeur' } });

function carte(libelle: string, lignes: KpiLigne[], id: KpiCommune['id'] = 'prix-maisons'): KpiCommune {
	return {
		id,
		rang: 1,
		libelle,
		definition: 'Définition de test du repère.',
		lignes,
		source: { nom: 'Demandes de valeurs foncières (DVF)', url: null },
		volet: [{ terme: 'Source', valeur: 'Demandes de valeurs foncières (DVF)' }]
	};
}

const PRIX: KpiLigne = {
	id: 'dvf-maison-mediane-courante',
	fieldId: 'dvf.indicateurs.maison.mediane_courante',
	libelle: null,
	etat: { kind: 'valeur', texte: '2 000', unite: '€/m²' },
	effectif: '12 ventes',
	fenetre: '2025',
	qualification: 'synthetic',
	brut: brut('dvf.indicateurs.maison.mediane_courante')
};

const texte = (element: Element | null) => element?.textContent?.replace(/\s+/g, ' ').trim() ?? null;

function repereDocument(id: 'energie' | 'climat'): KpiDocument {
	const unite = id === 'energie' ? 'kWhEP/an' : 'kg CO₂ éq/an';
	return {
		id,
		rang: id === 'energie' ? 1 : 2,
		libelle: id === 'energie' ? 'Étiquette énergie' : 'Étiquette climat',
		lignes: [
			{
				id: `${id}-mesure`,
				fieldId: `dpe.document.${id}`,
				libelle: null,
				etat: { kind: 'valeur', texte: '17 830', unite },
				effectif: null,
				fenetre: 'total annuel · 5 usages',
				etiquette: 'D',
				brut: brut(`dpe.document.${id}`)
			}
		],
		source: { nom: 'ADEME', url: null },
		volet: [{ terme: 'Source', valeur: 'ADEME' }]
	};
}

// 4.9, lot 3: a commune marker reads « value + base » on its tile; its definition and its source are in
// the panel its one button opens.
describe('Kpi — définition d’un repère communal au niveau 2 (4.9)', () => {
	const item = communeKpis({ ...COMMUNE_85047_CHALLANS, indicateurs_dvf: null, indicateurs_dpe: null, fenetre_mediane_dvf: '2024-12-31 → 2025-12-31', prix_m2_median_maison: 2480, nb_mutations_12m_maison: 96 } as CommuneSynthese)[0];

	it('the definition and the source are in the panel, not on the tile', () => {
		const repere = { ...carte('Prix médian des maisons', [PRIX]), definition: item.definition };
		const { container } = render(Kpi, { kpi: repere });
		const volet = container.querySelector('dialog')!;
		expect(container.querySelector('.cle-note')).toBeNull();
		const face = texte(container.querySelector('.cle'))!.replace(texte(volet)!, '');
		expect(face).not.toContain(item.definition);
		expect(face).not.toContain(repere.source!.nom);
		expect(texte(volet)).toContain(repere.source!.nom);
	});

	it('the form « detail » shows one button, named after the tile', () => {
		const { getAllByRole, container } = render(Kpi, { kpi: item, forme: 'detail' });
		expect(getAllByRole('button', { name: `Détail : ${item.libelle}` })).toHaveLength(1);
		expect(container.querySelector('.source-button svg')).not.toBeNull();
	});

	// P4 of the DPE document (2026-11-10): the source name was written on the face of the four tiles; it
	// is read in the panel of the « i », as on a commune tile.
	it('a document landmark has no definition line and no note: its source is in the panel only', () => {
		const item = repereDocument('energie');
		const { container } = render(Kpi, { kpi: item });
		expect(container.querySelector('.kpi-definition')).toBeNull();
		expect(container.querySelector('.cle-note')).toBeNull();
		const volet = container.querySelector('dialog')!;
		const face = texte(container.querySelector('.cle'))!.replace(texte(volet)!, '');
		expect(face).not.toContain(item.source!.nom);
		expect(texte(volet)).toContain(item.source!.nom);
	});
});

describe('Kpi — repère principal énergie du document', () => {
	it('places the served energy class before the unchanged annual total and its source action', () => {
		const { container, getByRole } = render(Kpi, { kpi: repereDocument('energie') });
		const valeur = container.querySelector('.kpi-etiquette')!;
		const classe = valeur.querySelector('.dpe-tag')!;
		const total = valeur.querySelector('.state-value')!;

		expect(classe.compareDocumentPosition(total) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(classe.classList.contains('dpe-tag-principal')).toBe(true);
		expect(texte(valeur.querySelector('.state-value'))).toBe('17 830 kWhEP/an');
		expect(texte(container.querySelector('.kpi-meta'))).toBe('total annuel · 5 usages');
		expect(getByRole('button', { name: 'Source et définition : Étiquette énergie' })).toBeTruthy();
	});

	it('gives the climate the same layout with its default-size class, so its total is never cut (UI-010)', () => {
		const { container, getByRole } = render(Kpi, { kpi: repereDocument('climat') });
		const valeur = container.querySelector('.kpi-etiquette')!;
		const classe = valeur.querySelector('.dpe-tag')!;
		const total = valeur.querySelector('.state-value')!;

		expect(classe.compareDocumentPosition(total) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(classe.classList.contains('dpe-tag-principal')).toBe(false);
		expect(texte(total)).toBe('17 830 kg CO₂ éq/an');
		expect(container.querySelector('.kpi-avec-classe')).toBeNull();
		expect(getByRole('button', { name: 'Source et définition : Étiquette climat' })).toBeTruthy();
	});
});

describe('Kpi — une ligne courte sous la valeur (03-17 R-1)', () => {
	it('renders one short meta line, the source only in the note, no state sentence on the card', () => {
		const { container } = render(Kpi, { kpi: carte('Prix médian des maisons', [PRIX]) });
		const metas = container.querySelectorAll('.kpi-meta');
		expect(metas).toHaveLength(1);
		expect(texte(metas[0])).toBe('12 ventes · 2025');
		expect(container.querySelector('.kpi-source')).toBeNull();
		expect(container.querySelector('.state-help')).toBeNull();
		// The qualification is in the panel only; the source name is the end of the note of the tile
		// (UI-SPEC v4 § 6.2, « note = définition servie · source »), nowhere else on the tile.
		const carteSeule = texte(container.querySelector('.cle'))!.replace(texte(container.querySelector('dialog'))!, '');
		const note = texte(container.querySelector('.cle-note')) ?? '';
		expect(carteSeule.replace(note, '')).not.toContain('Demandes de valeurs foncières');
		expect(carteSeule).not.toContain('synthetic');
	});

	it('an insufficient sample keeps its badge, without its sentence under it (SEM-01)', () => {
		const absente: KpiLigne = {
			...PRIX,
			etat: { kind: 'absence', etat: 'insufficient_sample', motif: 'Aucune vente.', effectif: 0 },
			effectif: '0 vente'
		};
		const { container } = render(Kpi, { kpi: carte('Prix médian des appartements', [absente]) });
		const ligne = container.querySelector('.kpi-line')!;
		expect(texte(ligne.querySelector('.state-badge'))).toBe('Effectif insuffisant');
		expect(ligne.querySelector('.state-help')).toBeNull();
		expect(texte(ligne)).not.toContain("Trop peu d'observations");
	});

	it('keeps two lines when the two states differ', () => {
		const lignes: KpiLigne[] = [
			{ ...PRIX, id: 'dvf-maison-variation', libelle: 'Maisons', etat: { kind: 'valeur', texte: '0', unite: '%' }, brut: brut('dvf.indicateurs.maison.variation_pct') },
			{ ...PRIX, id: 'dvf-appartement-variation', libelle: 'Appartements', etat: { kind: 'absence', etat: 'insufficient_sample', motif: 'x', effectif: 3 }, brut: brut('dvf.indicateurs.appartement.variation_pct') }
		];
		const { container } = render(Kpi, { kpi: carte('Tendance du prix médian', lignes, 'tendance') });
		expect([...container.querySelectorAll('.kpi-sub')].map(texte)).toEqual(['Maisons', 'Appartements']);
	});

	it('names its i button after the KPI, and the title carries the whole label', () => {
		const { container, getAllByRole } = render(Kpi, { kpi: carte('Prix médian des appartements', [PRIX]) });
		const boutons = getAllByRole('button', { name: 'Source et définition : Prix médian des appartements' });
		expect(boutons).toHaveLength(1);
		// One source action per tile, after its note (UI-SPEC v4 § 3.2): never in the label.
		expect(boutons[0].closest('.cle-n')).not.toBeNull();
		expect(texte(container.querySelector('dt.kpi-label'))).toBe('Prix médian des appartements');
		expect(container.querySelector('dt')?.querySelector('button')).toBeNull();
	});
});

describe('Kpi — sous-libellés d’une tendance publiée', () => {
	it('keeps its sub-labels above their values', () => {
		const lignes: KpiLigne[] = [
			{ ...PRIX, id: 'dvf-maison-variation', fieldId: 'dvf.indicateurs.maison.variation_pct', libelle: 'Maisons', libelleChamp: 'Tendance du prix médian des maisons', etat: { kind: 'valeur', texte: '+1,2', unite: '%' }, brut: brut('dvf.indicateurs.maison.variation_pct') },
			{ ...PRIX, id: 'dvf-appartement-variation', fieldId: 'dvf.indicateurs.appartement.variation_pct', libelle: 'Appartements', libelleChamp: 'Tendance du prix médian des appartements', etat: { kind: 'valeur', texte: '+3,2', unite: '%' }, brut: brut('dvf.indicateurs.appartement.variation_pct') }
		];
		const { container } = render(Kpi, { kpi: carte('Tendance du prix médian', lignes, 'tendance') });
		const article = container.querySelector('.cle')!;
		for (const ligne of article.querySelectorAll('.kpi-line')) {
			const sous = ligne.querySelector('.kpi-sub')!;
			expect(sous.compareDocumentPosition(ligne.querySelector('.kpi-value')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		}
		// One field, one label: the line says the label of its theme row.
		expect([...article.querySelectorAll('.kpi-line')].map((ligne) => ligne.getAttribute('data-libelle'))).toEqual(['Tendance du prix médian des maisons', 'Tendance du prix médian des appartements']);
	});
});

// 03-17 D-15 G4: a marker whose lines are all undeclared absences (an API defect: they are not in
// the model) still opens its source panel. Its source and definition stay reachable (SEM-02).
describe('Kpi — un repère sans ligne affichable garde son bouton i (03-17 G4)', () => {
	const nonDeclaree: KpiLigne = { ...PRIX, etat: { kind: 'absence-non-declaree' }, effectif: null, fenetre: null };

	it.each([
		['no line at all', [] as KpiLigne[]],
		['an undeclared absence only', [nonDeclaree]]
	])('%s: the i button is rendered once and opens the panel', async (_cas, lignes) => {
		const { container, getAllByRole } = render(Kpi, { kpi: carte('DPE dominant', lignes, 'dpe-dominant') });
		const boutons = getAllByRole('button', { name: 'Source et définition : DPE dominant' });
		expect(boutons).toHaveLength(1);
		expect(container.querySelectorAll('.kpi-line')).toHaveLength(0);
		boutons[0].click();
		expect(container.querySelector('dialog')?.hasAttribute('open')).toBe(true);
	});
});

// P4 of the DPE document (2026-11-10): the source name left the face of the document tiles. The tiles of the
// commune fiche (the other page that mounts Kpi; the departement page has its own `Tuile`) must render
// exactly as before: their markup is frozen here, recorded before the change (Svelte's empty anchors
// `<!---->` render nothing and are left out: the removed `{#if note}` left one).
describe('Kpi — une tuile de la fiche commune rend comme avant (P4 du document DPE)', () => {
	it('keeps the markup of every commune tile', () => {
		const balisage = communeKpis(COMMUNE_85047_CHALLANS as CommuneSynthese).map((kpi) => {
			const { container, unmount } = render(Kpi, { kpi, forme: 'detail' });
			const html = container.innerHTML.replace(/<!---->/g, '').replace(/svelte-[a-z0-9]+/g, 'svelte-x').replace(/(id|aria-labelledby|aria-controls)="[^"]*"/g, '$1="…"');
			unmount();
			return html;
		});
		expect(balisage).toMatchSnapshot();
	});
});
