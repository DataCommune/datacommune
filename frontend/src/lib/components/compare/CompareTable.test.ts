import { fireEvent, render, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import CompareTable from './CompareTable.svelte';
import { composeComparaison, composeComparaisonAdresses, composeComparaisonDocuments, factoriserMetadonnees } from '$lib/data/commune-comparison';
import { projeterComparaisonAdresses } from '$lib/api/adresses';
import { projeterComparaisonDocuments } from '$lib/api/documents-dpe';
import adressesServies from '$lib/data/__fixtures__/adresse-comparaison.json';
import documentsServis from '$lib/data/__fixtures__/dpe-comparaison.json';
import type { ReponseApiComparaison } from '$lib/api/commune';
import comparaisons from '$lib/data/__fixtures__/commune-comparaison.json';
import source from './CompareTable.svelte?raw';

// The table renders the model of the served comparison (D-48, D-49): the value or state of each
// cell, and for columns 2 to 4 the gap line recopied from the response. Frozen harness responses
// of /v1/communes/comparaison (03-09), guarded by the backend schema.

const REPONSES = comparaisons as unknown as Record<string, ReponseApiComparaison>;
const deux = () => structuredClone(REPONSES.deux_communes_hebergees);
const trois = () => structuredClone(REPONSES.trois_communes_dont_hors_couverture);

function modele(reponse: ReponseApiComparaison) {
	return composeComparaison(reponse.donnees, reponse.meta?.source ?? null);
}

const normaliser = (brut: string) => brut.replace(/\s+/g, ' ').trim();
const texte = (element: Element | null) => (element?.textContent == null ? null : normaliser(element.textContent));

function ligne(container: HTMLElement, cle: string): HTMLTableRowElement {
	const row = container.querySelector<HTMLTableRowElement>(`tr[data-cle="${cle}"]`);
	if (!row) throw new Error(`no row ${cle}`);
	return row;
}

afterEach(() => vi.restoreAllMocks());

describe('CompareTable — row header of a criterion (review of 09/11)', () => {
	// The unit is read with the value (« 11,14 % »), as on the fiche: never alone under the label.
	// DECLARED CHANGE (direction A, decision D4 of 2026-11-10, T10): in a comparison of communes the unit is
	// also written once, after the label; each cell keeps it with its value (drawn for screen readers only).
	// The addresses and the documents write none.
	it('writes no unit under any label, the cells carry it with the value', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		type ServiA = Parameters<typeof projeterComparaisonAdresses>[0];
		const adresses = (adressesServies as unknown as Record<string, { donnees: ServiA }>).deux_adresses.donnees;
		const autres = render(CompareTable, { modele: composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(adresses))) });
		expect(autres.container.querySelectorAll('th.compare-critere .compare-unite')).toHaveLength(0);
		const revenu = ligne(container, 'revenu_median');
		expect(texte(revenu.querySelector('td .state-value'))).toMatch(/€\/UC\/an$/);
	});

	// The information icon stays glued to the last word of its label: both sit in one unbreakable span.
	it('keeps the information button with the last word of the label', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const rows = [...container.querySelectorAll<HTMLTableRowElement>('tr[data-cle]')].filter((row) =>
			row.querySelector('th.compare-critere .source-button')
		);
		expect(rows.length).toBeGreaterThan(3);
		for (const row of rows) {
			const libelle = texte(row.querySelector('th.compare-critere > .compare-libelle'))!;
			const fin = row.querySelector('th.compare-critere .compare-libelle-fin');
			expect(fin, row.dataset.cle).not.toBeNull();
			expect(fin!.querySelector('.source-button')).not.toBeNull();
			expect(texte(fin)).toBe(libelle.split(' ').at(-1));
		}
	});

	it('titles the panel of a cell without a dash', async () => {
		const model = modele(deux());
		const ligneRevenu = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'revenu_median')!;
		ligneRevenu.cellules[0].naturePeriode = 'connue';
		ligneRevenu.cellules[1].naturePeriode = null;
		const { container } = render(CompareTable, { modele: model });
		await fireEvent.click(ligne(container, 'revenu_median').querySelector<HTMLButtonElement>('td .source-button')!);
		const titre = texte(container.ownerDocument.querySelector('dialog.source-panel h2'));
		expect(titre).not.toContain('—');
		expect(titre).toMatch(/^Revenu .+ · .+/);
	});
});

describe('CompareTable — values and gaps served by the API', () => {
	it('recopies the served gap, even when it is not the subtraction of the two values', () => {
		const reponse = deux();
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		const [a, b] = revenu.cellules;
		revenu.cellules[1] = { ...b, ecart: { valeur: 999, unite: 'euro_per_uc_year' } };
		expect((b.valeur as number) - (a.valeur as number)).not.toBe(999);
		const { container } = render(CompareTable, { modele: modele(reponse) });
		const cellules = ligne(container, 'revenu_median').querySelectorAll('td');
		expect(texte(cellules[1].querySelector('.compare-ecart'))).toBe('+999 €/UC/an par rapport à Ancienne');
		// Column 1 is the reference: it carries no gap line.
		expect(cellules[0].querySelector('.compare-ecart')).toBeNull();
	});

	// C3 (arbitrated by Ianis on 2026-11-10): the period and the source left the row header for its panel. The
	// row keeps what it showed otherwise, and its one explanation control (lot 6).
	it('a row of exact common metadata: nothing under its label, one explanation control whose panel says the period and the source', async () => {
		const model = modele(deux());
		const ligneRevenu = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'revenu_median')!;
		for (const cellule of ligneRevenu.cellules) cellule.naturePeriode = 'connue';
		const { container } = render(CompareTable, { modele: model });
		const row = ligne(container, 'revenu_median');

		expect(row.querySelector('th .compare-meta-commune')).toBeNull();
		// The row still says its metadata are common: the e2e measure of lot 6 reads it there.
		expect(row.dataset.metaCommunes).toBe('true');
		expect(row.querySelectorAll('td .compare-meta')).toHaveLength(0);
		expect(row.querySelectorAll('td .state-value')).toHaveLength(2);
		expect(row.querySelectorAll('td .compare-ecart')).toHaveLength(1);
		// Lot 6 (phase 4.9): one explanation control for the row, in its header, none in the cells.
		expect(row.querySelectorAll('td button')).toHaveLength(0);
		const boutons = row.querySelectorAll<HTMLButtonElement>('th.compare-critere .source-button');
		expect(boutons).toHaveLength(1);
		await fireEvent.click(boutons[0]);
		const volet = texte(container.ownerDocument.querySelector('dialog.source-panel'))!;
		expect(volet).toContain('Millésime 2021');
		expect(volet).toContain('Filosofi (revenus et pauvreté)');
	});

	// C3: divergent or unproved metadata are no longer written beside each cell; each cell keeps its own
	// explanation control, whose panel says them.
	it('divergent or unproved metadata: one explanation control per cell, nothing on the face', () => {
		const model = modele(deux());
		const ligneRevenu = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'revenu_median')!;
		ligneRevenu.cellules[0].naturePeriode = 'connue';
		ligneRevenu.cellules[1].naturePeriode = null;
		const { container } = render(CompareTable, { modele: model });
		const row = ligne(container, 'revenu_median');

		expect(row.querySelector('.compare-meta-commune')).toBeNull();
		expect(row.querySelectorAll('td .compare-meta')).toHaveLength(0);
		expect(row.querySelectorAll('td .source-button')).toHaveLength(2);
		expect(row.querySelectorAll('th.compare-critere .source-button')).toHaveLength(0);
		expect(row.dataset.metaCommunes).toBeUndefined();
	});

	// B6 (plan 04.5-10, UI-013, UI-027), amended by C3 (arbitrated by Ianis on 2026-11-10): a row without unit
	// whose metadata is strictly identical has one explanation control under its label; its panel says the
	// period and the source the label no longer carries.
	it('B6: a row without unit, identical metadata: one control under its label, whose panel says period and source', async () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const row = ligne(container, 'nb_mutations_12m_maison');
		const tete = row.querySelector('th.compare-critere');
		expect(tete?.querySelector('.compare-meta-commune') ?? null).toBeNull();
		expect(tete?.querySelector('.compare-unite')).toBeNull();
		expect(row.querySelectorAll('td .compare-meta')).toHaveLength(0);
		expect(row.querySelectorAll('td .state-value')).toHaveLength(2);
		expect(row.querySelectorAll('td .source-button')).toHaveLength(0);
		const boutons = [...row.querySelectorAll<HTMLButtonElement>('th .source-button')];
		expect(boutons).toHaveLength(1);
		await fireEvent.click(boutons[0]);
		const volet = container.ownerDocument.querySelector('dialog.source-panel')!;
		expect(texte(volet.querySelector('h2'))).toBe(texte(tete?.querySelector('span') ?? null));
		const termes = [...volet.querySelectorAll('dt')].map((dt) => texte(dt));
		expect(termes).toEqual(expect.arrayContaining(['Définition', 'Période', 'Source']));
		expect(texte(volet)).toContain('Demandes de valeurs foncières (DVF)');
		expect(texte(volet)).toContain('2024-06-01 → 2025-06-01');
	});

	// The cell buttons go only if the row panel loses nothing: what a cell panel said, the row panel says.
	describe('the panel of a row covers the panels of its cells (lot 6)', () => {
		const ouvrirLigne = async (model: ReturnType<typeof modele>, cle: string) => {
			const { container } = render(CompareTable, { modele: model });
			await fireEvent.click(ligne(container, cle).querySelector<HTMLButtonElement>('th .source-button')!);
			return container.ownerDocument.querySelector<HTMLDialogElement>('dialog.source-panel')!;
		};
		const trouver = (model: ReturnType<typeof modele>, cle: string) =>
			model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === cle)!;

		it('gives the raw value of every commune, each under the name and code of its commune', async () => {
			const model = modele(deux());
			const servie = trouver(model, 'nb_mutations_12m_maison');
			const volet = await ouvrirLigne(model, 'nb_mutations_12m_maison');
			const blocs = [...volet.querySelectorAll('.source-panel-raw-bloc')];
			expect(blocs.map((bloc) => texte(bloc.querySelector('.source-panel-sub')))).toEqual(['Ancienne (44991)', 'Courante (44992)']);
			servie.cellules.forEach((cellule, index) => {
				expect(texte(blocs[index]), cellule.code).toContain(cellule.brut[0].valeur);
				expect(texte(blocs[index]), cellule.code).toContain(cellule.brut[0].fieldId);
			});
		});

		it('says once what every cell shares, and per commune what differs (Effectif)', async () => {
			const model = modele(deux());
			const [premiere, seconde] = trouver(model, 'nb_mutations_12m_maison').cellules;
			premiere.volet.push({ terme: 'Effectif', valeur: '12 ventes' });
			seconde.volet.push({ terme: 'Effectif', valeur: '48 ventes' });
			const volet = await ouvrirLigne(model, 'nb_mutations_12m_maison');
			const entrees = [...volet.querySelectorAll('.source-panel-list > div')].map((div) => [
				texte(div.querySelector('dt')),
				texte(div.querySelector('dd'))
			]);
			expect(entrees.filter(([terme]) => terme === 'Définition')).toHaveLength(1);
			expect(entrees.filter(([terme]) => terme === 'Source')).toEqual([['Source', 'Demandes de valeurs foncières (DVF)']]);
			expect(entrees).toEqual(
				expect.arrayContaining([
					['Effectif · Ancienne (44991)', '12 ventes'],
					['Effectif · Courante (44992)', '48 ventes']
				])
			);
		});

		it('keeps an entry only one commune has, under the name of that commune', async () => {
			const model = modele(deux());
			trouver(model, 'nb_mutations_12m_maison').cellules[1].volet.push({ terme: 'Limites', valeur: 'Réserve propre à Courante.' });
			const volet = await ouvrirLigne(model, 'nb_mutations_12m_maison');
			const dt = [...volet.querySelectorAll('dt')].find((item) => texte(item) === 'Limites · Courante (44992)');
			expect(texte(dt?.nextElementSibling ?? null)).toBe('Réserve propre à Courante.');
		});

		it('every entry and raw value of every cell panel is found in the row panel', async () => {
			const model = modele(deux());
			const lignes = model.groupes.flatMap((groupe) => groupe.lignes).filter((item) => factoriserMetadonnees(item));
			expect(lignes.length).toBeGreaterThan(1);
			for (const item of lignes) {
				const { container, unmount } = render(CompareTable, { modele: model });
				await fireEvent.click(ligne(container, item.cle).querySelector<HTMLButtonElement>('th .source-button')!);
				const volet = texte(container.ownerDocument.querySelector('dialog.source-panel'))!;
				for (const cellule of item.cellules) {
					for (const entree of cellule.volet) {
						if (!('absences' in entree)) expect(volet, `${item.cle} ${cellule.code} ${entree.terme}`).toContain(normaliser(entree.valeur));
					}
					for (const servie of cellule.brut) expect(volet, `${item.cle} ${cellule.code}`).toContain(normaliser(servie.valeur));
				}
				unmount();
			}
		});
	});

	// Plan § 4: what is measured is the controls, not the word « Source ».
	it('counts one explanation control per criterion of identical provenance, one per cell otherwise', () => {
		for (const reponse of [deux(), trois()]) {
			const model = modele(reponse);
			const { container, unmount } = render(CompareTable, { modele: model });
			for (const item of model.groupes.flatMap((groupe) => groupe.lignes)) {
				const row = ligne(container, item.cle);
				const attendu = factoriserMetadonnees(item) ? 1 : row.querySelectorAll('td .compare-valeur:has(.state-value, .state-badge)').length;
				expect(row.querySelectorAll('.source-button').length, item.cle).toBe(attendu);
			}
			unmount();
		}
	});

	// C3: the communes no longer write a period on the face. Where one is still written (the addresses), an ISO
	// date it carries stays whole on one line, the served wording unchanged.
	it('keeps every ISO date whole in the metadata still written on the face, without changing the wording', () => {
		type Servi = Parameters<typeof projeterComparaisonAdresses>[0];
		const servi = (adressesServies as unknown as Record<string, { donnees: Servi }>).deux_adresses.donnees;
		const model = composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(servi)));
		const fibre = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'fibre')!;
		const periode = 'Évaluation : 2024-01-01 au 2025-12-31';
		for (const cellule of fibre.cellules) cellule.periodeAffichee = periode;
		const { container } = render(CompareTable, { modele: model });
		const metas = [...ligne(container, 'fibre').querySelectorAll('td .compare-meta')];
		expect(metas).toHaveLength(2);
		for (const meta of metas) {
			expect(texte(meta)).toContain(periode);
			expect([...meta.querySelectorAll('.compare-date')].map(texte)).toEqual(['2024-01-01', '2025-12-31']);
		}
	});

	it('shows the served value of each quantile, never the dispersion of the fiche cell', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const servies = deux().donnees.criteres.filter((critere) => /courante\.quantiles\.p(10|50|90)$/.test(critere.cle) && critere.cle.includes('maison'));
		for (const critere of servies) {
			const row = ligne(container, critere.cle);
			expect(texte(row.querySelector('th'))).toMatch(/^Intervalle P10–P90 des maisons \(fenêtre courante\) · (1er décile \(P10\)|médiane \(P50\)|9e décile \(P90\))/);
			for (const [index, cellule] of critere.cellules.entries()) {
				const td = row.querySelectorAll('td')[index];
				expect(td.getAttribute('data-state')).toBe(cellule.etat === 'value' ? 'value' : cellule.etat);
			}
		}
	});

	it('writes the served reasons in warn, and a non-numeric verdict without warn', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		const periode = [...container.querySelectorAll('.compare-ecart')].find((p) => texte(p) === 'Écart non calculé · périodes différentes');
		expect(periode?.classList.contains('compare-ecart-warn')).toBe(true);
		// A column out of coverage repeats no gap line: the reason is said once, in its header (03-18 C-03).
		expect(container.querySelectorAll('td[data-code="85991"]').length).toBeGreaterThan(10);
		expect(container.querySelectorAll('td[data-code="85991"] .compare-ecart')).toHaveLength(0);
		const verdict = ligne(container, 'indicateurs_dpe.resultat.modes').querySelectorAll('td')[1].querySelector('.compare-ecart');
		expect(texte(verdict)).toBe('Même valeur que Ancienne');
		expect(verdict?.classList.contains('compare-ecart-warn')).toBe(false);
	});

	it('keeps each word of a state badge whole inside narrow comparison cells', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		const badge = within(ligne(container, 'fibre_pct')).getAllByText('Non renseigné')[0];

		expect(texte(badge)).toBe('Non renseigné');
		expect(source).toMatch(
			/\.compare-valeur\s+:global\(\.state-badge\)\s*\{[^}]*padding-inline:\s*var\(--s1\);[^}]*overflow-wrap:\s*normal;[^}]*word-break:\s*normal;/s
		);
	});

	it('keeps the out-of-coverage column: badge, reason and « ne vaut pas zéro » once in the header, « Hors couverture » in each typed cell', () => {
		const { container, getByRole } = render(CompareTable, { modele: modele(trois()) });
		const entete = container.querySelector('th[data-code="85991"]')!;
		expect(texte(entete.querySelector('.compare-nom'))).toBe('Couverture inconnue');
		expect(entete.querySelectorAll('.state-badge')).toHaveLength(1);
		expect(texte(entete.querySelector('.state-badge'))).toBe('Hors couverture · département non hébergé');
		expect(entete.querySelector('.state-badge')?.classList.contains('state-badge-warn')).toBe(true);
		expect(texte(entete.querySelector('.compare-hc-note'))).toBe('Aucune valeur : ne vaut pas zéro.');
		expect(texte(entete.querySelector('.compare-identite'))).toBe('85991 · Vendée (85)');
		expect(getByRole('link', { name: 'Couverture inconnue' })).toHaveAttribute('href', '/commune/85991');
		// Hosted columns carry no such note.
		expect(container.querySelector('th[data-code="44991"] .compare-hc-note')).toBeNull();
		const cellules = [...container.querySelectorAll('td[data-code="85991"]')];
		expect(cellules.length).toBeGreaterThan(10);
		for (const td of cellules) {
			// The state stays typed and named in every cell (SEM-01): no badge, no button, no gap repeated.
			expect(td.getAttribute('data-state')).toBe('out_of_coverage');
			expect(texte(td)).toBe('Hors couverture');
			expect(td.querySelector('.compare-hors-couverture')).not.toBeNull();
			expect(td.querySelector('.state-badge, button, .compare-ecart, .compare-meta')).toBeNull();
		}
	});

	it('gives the gaps no colour of judgement: the sign and the text carry them (Ianis, 2026-09-29, status quo)', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const chiffres = [...container.querySelectorAll('p.compare-ecart[data-ecart="ecart"]')];
		const positif = chiffres.find((p) => texte(p)?.startsWith('+'));
		const negatif = chiffres.find((p) => /^[−-]/.test(texte(p) ?? ''));
		expect(positif, 'a positive served gap in the fixture').toBeDefined();
		expect(negatif, 'a negative served gap in the fixture').toBeDefined();
		const classes = (p: Element) => [...p.classList].filter((classe) => !/^svelte-/.test(classe));
		for (const p of [positif!, negatif!]) expect(classes(p)).toEqual(['compare-ecart']);
		for (const p of container.querySelectorAll('p.compare-ecart')) {
			const autres = classes(p);
			expect(autres.every((classe) => classe === 'compare-ecart' || classe === 'compare-ecart-warn'), autres.join(' ')).toBe(true);
			if (p.classList.contains('compare-ecart-warn')) expect(p.getAttribute('data-ecart')).not.toBe('ecart');
		}
		// A colour by sign added later is caught in the source: no judgement token, text colours from
		// the contract only.
		expect(source).not.toMatch(/danger|success|green|\bred\b/i);
		const couleursDeTexte = [...source.matchAll(/(?<![\w-])color:\s*var\(--color-([\w-]+)\)/g)].map((m) => m[1]);
		expect(couleursDeTexte.length).toBeGreaterThan(0);
		for (const jeton of couleursDeTexte) expect(['ink', 'muted', 'accent', 'accent-hover', 'warn']).toContain(jeton);
	});

	it('renders every absence through the glossary: no technical key, no « non disponible »', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		const rendu = texte(container)!;
		expect(rendu).not.toMatch(/non disponible/i);
		expect(rendu).not.toMatch(/out_of_coverage|not_reported|insufficient_sample|not_available|not_qualified|territoire_hors_perimetre|donnee_absente|periodes_differentes/);
		expect(container.querySelectorAll('td[data-state="not_qualified"] .state-badge').length).toBeGreaterThan(0);
		for (const badge of container.querySelectorAll('td[data-state="not_qualified"] .state-badge')) {
			expect(texte(badge)).toBe('Non qualifié');
		}
	});

	it('refuses and names a state the glossary does not know, instead of disguising it', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		const reponse = deux();
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		revenu.cellules[1] = { ...revenu.cellules[1], etat: 'etat_inconnu' as never, valeur: null };
		const { container } = render(CompareTable, { modele: modele(reponse) });
		const td = ligne(container, 'revenu_median').querySelectorAll('td')[1];
		expect(td.getAttribute('data-state')).toBe('non-affiche');
		expect(td.querySelector('.state-badge, .state-value')).toBeNull();
		// Not a blank: a neutral marker that blames no one, never a glossary state.
		expect(texte(td.querySelector('.compare-non-affichable'))).toBe('Valeur non affichable');
		expect(erreur).toHaveBeenCalledWith(expect.stringContaining('etat_inconnu'));
	});

	it('marks an absence the API did not declare as « Valeur non affichable », neutral, and names it on the console (03-17 C6)', () => {
		const erreur = vi.spyOn(console, 'error').mockImplementation(() => {});
		const reponse = deux();
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		revenu.cellules[0] = { ...revenu.cellules[0], etat: null, valeur: null };
		const { container } = render(CompareTable, { modele: modele(reponse) });
		const td = ligne(container, 'revenu_median').querySelectorAll('td')[0];
		expect(td.getAttribute('data-state')).toBe('non-affiche');
		const marqueur = td.querySelector('.compare-non-affichable');
		expect(texte(marqueur)).toBe('Valeur non affichable');
		expect(texte(td)).not.toContain('Non servi');
		expect(marqueur?.classList.contains('state-badge')).toBe(false);
		expect(texte(td)).not.toMatch(/Non renseigné|Non publiée|non disponible|0/);
		expect(td.querySelector('.source-button')).toBeNull();
		expect(erreur).toHaveBeenCalledWith(expect.stringContaining('revenu_median'));
	});
});

describe('CompareTable — layout, filter and column actions', () => {
	it('puts the table in a focusable, named scroll region, headers and criteria as table headers', () => {
		const { getByRole, container } = render(CompareTable, { modele: modele(trois()) });
		const region = getByRole('region', { name: 'Tableau de comparaison' });
		expect(region).toHaveAttribute('tabindex', '0');
		expect([...region.querySelectorAll('thead th')].map((th) => th.getAttribute('scope'))).toEqual(['col', 'col', 'col', 'col']);
		expect(within(region).getAllByRole('rowheader').length).toBeGreaterThan(30);
		expect(container.querySelector('tr[data-cle="revenu_median"] th')).toHaveAttribute('scope', 'row');
		// Declared change (direction A, T10): the row header says its unit once, after the label.
		expect(texte(container.querySelector('tr[data-cle="revenu_median"] th .compare-libelle'))).toBe('Revenu déclaré médian');
		expect(texte(container.querySelector('tr[data-cle="revenu_median"] th'))).toBe('Revenu déclaré médian €/UC/an');
		expect(container.querySelector('table')?.getAttribute('data-colonnes')).toBe('3');
	});

	it('mounts without IntersectionObserver nor ResizeObserver (jsdom as is): the band stays hidden', () => {
		expect(typeof IntersectionObserver).toBe('undefined');
		expect(typeof ResizeObserver).toBe('undefined');
		const { container } = render(CompareTable, { modele: modele(trois()) });
		expect(container.querySelector('table')).not.toBeNull();
		expect(container.querySelector<HTMLElement>('.compare-bande')?.style.visibility).toBe('hidden');
	});

	it('duplicates the column names in a hidden, decorative header band; the thead stays the only semantics', () => {
		const { container, getByRole } = render(CompareTable, { modele: modele(trois()) });
		const ancre = container.querySelector('.compare-cadre > .compare-bande-ancre')!;
		expect(ancre).not.toBeNull();
		// The anchor comes before the region, inside the non-scrolling frame.
		expect(ancre.nextElementSibling?.getAttribute('role')).toBe('region');
		const bande = ancre.querySelector<HTMLElement>(':scope > .compare-bande')!;
		expect(bande.getAttribute('aria-hidden')).toBe('true');
		expect(bande.style.visibility).toBe('hidden');
		expect(texte(bande.querySelector('.compare-bande-coin'))).toBe('Critère');
		const cellules = [...bande.querySelectorAll('.compare-bande-piste > .compare-bande-cellule')];
		expect(cellules.map((cellule) => cellule.getAttribute('data-code'))).toEqual(['44991', '44992', '85991']);
		expect(cellules.map((cellule) => texte(cellule.querySelector('.compare-bande-nom')))).toEqual(['Ancienne', 'Courante', 'Couverture inconnue']);
		// After the cut name, what tells the columns apart (D-15 CR-01): the departement number, or the
		// INSEE code when two columns share their departement (44991 and 44992 are both in 44).
		expect(cellules.map((cellule) => texte(cellule.querySelector('.compare-bande-discriminant')))).toEqual(['(44991)', '(44992)', '(85)']);
		expect(bande.querySelector('.compare-bande-piste')?.getAttribute('style')).toContain('translateX(');
		// Decorative only: no link, no button, no state badge; not in the accessibility tree.
		expect(bande.querySelectorAll('a, button, .state-badge')).toHaveLength(0);
		const zone = getByRole('region', { name: 'Tableau de comparaison' });
		expect(within(zone.querySelector('thead')!).getAllByRole('columnheader')).toHaveLength(4);
		expect(within(container).getAllByRole('columnheader')).toHaveLength(within(zone).getAllByRole('columnheader').length);
		expect(zone.contains(bande)).toBe(false);
		// The real header cells carry no sticky marker of their own (the band takes over).
		for (const th of container.querySelectorAll('th.compare-commune')) {
			expect([...th.classList].filter((classe) => !/^svelte-/.test(classe))).toEqual(['compare-commune']);
			expect(th.hasAttribute('style')).toBe(false);
		}
		// Hidden columns are announced only when the region scrolls (never measured in jsdom).
		expect(container.querySelector<HTMLElement>('.compare-defilement')?.hidden).toBe(true);
		expect(container.querySelector('.compare-cadre')?.getAttribute('data-defilable')).toBe('false');
	});

	// C1 (arbitrated by Ianis on 2026-11-10): the three categories of the commune fiche, no longer its five former themes.
	it('orders rows like the fiche: the synthesis cards, then its three categories', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const groupes = [...container.querySelectorAll('tbody')].map((tbody) => tbody.getAttribute('data-groupe'));
		expect(groupes).toEqual(['reperes', 'q-prix', 'q-logement', 'q-quotidien']);
		const reperes = [...container.querySelectorAll('tbody[data-groupe="reperes"] tr[data-ligne]')].map((tr) => tr.getAttribute('data-ligne'));
		// C5: the two rent rows are landmarks, after the prices and their trend, before the DPE.
		expect(reperes).toEqual([
			'dvf-maison-mediane-courante', 'dvf-appartement-mediane-courante', 'dvf-maison-variation', 'dvf-appartement-variation',
			'loyers.loyer_m2_appartement', 'loyers.loyer_m2_maison',
			'dpe-modes', 'dpe-mediane'
		]);
	});

	it('gives each displayed group a stable focus target', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		for (const groupe of container.querySelectorAll('tbody[data-groupe]')) {
			const cle = groupe.getAttribute('data-groupe');
			const titre = groupe.querySelector('th[scope="rowgroup"]');
			expect(titre?.id).toBe(`comparaison-groupe-${cle}`);
			expect(titre).toHaveAttribute('tabindex', '-1');
		}
	});

	it('« Différences seulement » hides exactly the criteria served as identical', () => {
		const reponse = deux();
		const vue = render(CompareTable, { modele: modele(reponse), differencesSeulement: true });
		const affichees = [...vue.container.querySelectorAll('tr[data-cle]')];
		expect(affichees.length).toBeGreaterThan(0);
		for (const tr of affichees) expect(tr.getAttribute('data-identiques')).toBe('false');
		const tous = modele(reponse).groupes.flatMap((groupe) => groupe.lignes);
		const identiques = tous.filter((l) => l.identiques).length;
		expect(identiques).toBeGreaterThan(0);
		expect(affichees).toHaveLength(tous.length - identiques);
		expect(texte(vue.getByRole('status'))).toBe(`${identiques} critères identiques dans toutes les colonnes sont masqués.`);
	});

	it('says why the filter hides nothing when a commune is out of coverage', () => {
		const reponse = trois();
		const { container, getByRole } = render(CompareTable, { modele: modele(reponse), differencesSeulement: true });
		expect(container.querySelectorAll('tr[data-cle]')).toHaveLength(modele(reponse).groupes.flatMap((g) => g.lignes).length);
		expect(texte(getByRole('status'))).toBe(
			'Aucun critère n’est masqué : Couverture inconnue est hors couverture, donc aucun critère n’est identique dans toutes les colonnes.'
		);
	});

	it('names « Retirer » and « Placer en premier » per commune; the first column cannot be placed first', async () => {
		const onretirer = vi.fn();
		const onplacerpremier = vi.fn();
		const { getByRole, queryByRole } = render(CompareTable, { modele: modele(trois()), onretirer, onplacerpremier });
		expect(queryByRole('button', { name: 'Placer Ancienne en premier' })).toBeNull();
		await fireEvent.click(getByRole('button', { name: 'Placer Couverture inconnue en premier' }));
		expect(onplacerpremier).toHaveBeenCalledWith('85991');
		await fireEvent.click(getByRole('button', { name: 'Retirer Ancienne' }));
		expect(onretirer).toHaveBeenCalledWith('44991');
	});

	it('disables the column actions and marks the region busy while a comparison is read', () => {
		const { getByRole } = render(CompareTable, { modele: modele(deux()), occupe: true });
		expect(getByRole('region', { name: 'Tableau de comparaison' })).toHaveAttribute('aria-busy', 'true');
		expect(getByRole('button', { name: 'Retirer Courante' })).toBeDisabled();
	});

	it('opens one panel per control, titled by the criterion alone for a row, with its commune for a cell, without a native tooltip', async () => {
		const model = modele(deux());
		const { container, getByRole } = render(CompareTable, { modele: model });
		const bouton = getByRole('button', { name: /^Détail : Revenu déclaré médian$/ });
		expect(bouton.hasAttribute('title')).toBe(false);
		expect(container.querySelectorAll('dialog')).toHaveLength(1);
		await fireEvent.click(bouton);
		expect(texte(container.querySelector('dialog h2'))).toBe('Revenu déclaré médian');
		const divergente = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => !factoriserMetadonnees(item))!;
		const cellule = ligne(container, divergente.cle).querySelector<HTMLButtonElement>('td .source-button')!;
		await fireEvent.click(container.querySelector('.source-panel-close')!);
		await fireEvent.click(cellule);
		expect(texte(container.querySelector('dialog h2'))).toMatch(new RegExp(`^${divergente.libelle} · `));
	});
});

// --- 04-07: addresses in the same table (D-102, CMP-01, D-116) --------------------------------

describe('CompareTable — addresses compared (04-07)', () => {
	type Brut = Parameters<typeof projeterComparaisonAdresses>[0];
	const adresses = (cle: 'deux_adresses' | 'trois_dont_hors_couverture' | 'deux_designations') =>
		composeComparaisonAdresses(
			projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: Brut }>)[cle].donnees))
		);

	it('one tbody per served group with a rowgroup header, the commune of the address last with its sentence', () => {
		const { container } = render(CompareTable, { modele: adresses('deux_adresses') });
		const tbodies = [...container.querySelectorAll('tbody')];
		expect(tbodies.map((tbody) => tbody.getAttribute('data-groupe'))).toEqual(['documents', 'ventes', 'lieu', 'commune_reference']);
		for (const tbody of tbodies) {
			const entete = tbody.querySelector('tr.compare-groupe th')!;
			expect(entete.getAttribute('scope')).toBe('rowgroup');
		}
		const reference = tbodies.at(-1)!;
		expect(reference.getAttribute('data-reference')).toBe('true');
		expect(reference.querySelector('tr.compare-groupe')?.classList.contains('compare-groupe-reference')).toBe(true);
		expect(texte(reference.querySelector('.compare-reference-phrase'))).toBe("Ces valeurs décrivent la commune, pas l'adresse.");
		expect(container.querySelectorAll('.compare-reference-phrase')).toHaveLength(1);
		expect(source).toMatch(/\.compare-groupe-reference th \{\s*background: var\(--color-soft\);/);
	});

	// B6 (plan 04.5-10, counter-review I4): the Source button and the panel title name the address by
	// `nomAccessible`, street then « {CP} {Commune} »: two « 12 Rue des Lilas » (85000, 85100) never share a name.
	it('B6, I4: two « 12 Rue des Lilas » (85000, 85100) give distinct Source buttons and panel titles', async () => {
		const { container, getByRole } = render(CompareTable, { modele: adresses('trois_dont_hors_couverture') });
		const libelle = texte(container.querySelector('tr[data-cle="dpe_declares"] th.compare-critere span'));
		expect(libelle).toBe('DPE déclarés');
		const noms = [...container.querySelectorAll('tr[data-cle="dpe_declares"] td .source-button')].map((bouton) => bouton.getAttribute('aria-label'));
		expect(noms).toEqual([
			'Détail : DPE déclarés · 12 Rue des Lilas, 85000 La Roche-sur-Yon',
			'Détail : DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon'
		]);
		await fireEvent.click(getByRole('button', { name: 'Détail : DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon' }));
		expect(texte(container.ownerDocument.querySelector('dialog.source-panel h2'))).toBe('DPE déclarés · 12 Rue des Lilas, 85100 La Roche-sur-Yon');
		// Every Source button of the table has a name of its own.
		const tous = [...container.querySelectorAll('td .source-button')].map((bouton) => bouton.getAttribute('aria-label'));
		expect(new Set(tous).size).toBe(tous.length);
	});

	it('D-15 FRONT WR-05: an absence with a decided reason says its sentence in the cell, not only « Non publiée »', () => {
		const servie = projeterComparaisonAdresses(
			structuredClone((adressesServies as Record<string, { donnees: Brut }>).deux_adresses.donnees)
		);
		const plusRecent = servie.groupes[0].lignes.find((ligne) => ligne.cle === 'dpe_plus_recent')!;
		Object.assign(plusRecent.cellules[1], { etat: 'not_available', motif: 'aucun_document', valeur: null, selection: null });
		const { container } = render(CompareTable, { modele: composeComparaisonAdresses(servie) });

		const cellule = container.querySelectorAll('tr[data-cle="dpe_plus_recent"] td')[1];
		expect(texte(cellule.querySelector('.compare-valeur'))).toContain('Aucun DPE depuis juillet 2021 à cette adresse ; les diagnostics plus anciens ne sont plus valables.');
		// A reason without a decided sentence keeps the badge alone (the glossary, no invented text).
		const ventes = container.querySelectorAll('tr[data-cle="ventes_parcelle"] td')[1];
		expect(ventes.querySelector('.state-help')).toBeNull();
	});

	it('D-15 FRONT WR-06: two homonymous streets of one postal code are told apart by their commune in the band', () => {
		const brut = structuredClone((adressesServies as Record<string, { donnees: Brut }>).deux_adresses.donnees) as {
			colonnes: { numero_voie: string; code_postal: string; commune: { nom: string } }[];
		};
		brut.colonnes.forEach((colonne, index) => {
			colonne.numero_voie = '1 rue de la Gare';
			colonne.code_postal = '85000';
			colonne.commune.nom = index ? 'Mouilleron-le-Captif' : 'La Roche-sur-Yon';
		});
		const { container } = render(CompareTable, { modele: composeComparaisonAdresses(projeterComparaisonAdresses(brut as Brut)) });
		const discriminants = [...container.querySelectorAll('.compare-bande-discriminant')].map((element) => texte(element));
		expect(discriminants).toEqual(['(La Roche-sur-Yon)', '(Mouilleron-le-Captif)']);
	});

	it('names each address column by its street, links its page and writes « {CP} {Commune} »', () => {
		const { container, getByRole } = render(CompareTable, { modele: adresses('deux_adresses') });
		const entetes = [...container.querySelectorAll('thead th.compare-commune')];
		expect(entetes.map((th) => th.querySelector('a.compare-nom')?.getAttribute('href'))).toEqual([
			'/adresse/85191_0420_00012',
			'/adresse/85191_0601_00001'
		]);
		expect(entetes.map((th) => texte(th.querySelector('.compare-identite')))).toEqual(['85000 La Roche-sur-Yon', '85000 La Roche-sur-Yon']);
		expect(getByRole('button', { name: 'Retirer 1 Rue du Lieu, 85000 La Roche-sur-Yon' })).toBeInTheDocument();
		expect(getByRole('button', { name: 'Placer 1 Rue du Lieu, 85000 La Roche-sur-Yon en premier' })).toBeInTheDocument();
		// Same postal code: no discriminant is invented in the band.
		expect(container.querySelectorAll('.compare-bande-discriminant')).toHaveLength(0);
		expect(container.querySelector('.compare-bande')?.classList.contains('compare-bande-adresses')).toBe(true);
	});

	it('writes the served verdict without warn and the served gap without a colour of judgement', () => {
		const { container } = render(CompareTable, { modele: adresses('deux_adresses') });
		const argiles = ligne(container, 'argiles').querySelectorAll('td')[1].querySelector('.compare-ecart')!;
		expect(texte(argiles)).toBe('Différent');
		expect(argiles.getAttribute('data-ecart')).toBe('verdict');
		expect(argiles.classList.contains('compare-ecart-warn')).toBe(false);
		const contenance = ligne(container, 'contenance').querySelectorAll('td')[1].querySelector('.compare-ecart')!;
		expect(texte(contenance)).toBe('−412 m² par rapport à 12 Rue des Lilas');
		expect([...contenance.classList].filter((classe) => !/^svelte-/.test(classe))).toEqual(['compare-ecart']);
		const ventes = ligne(container, 'ventes_parcelle').querySelectorAll('td')[1];
		expect(ventes.getAttribute('data-state')).toBe('not_available');
		expect(texte(ventes.querySelector('.state-badge'))).toBe('Point hors parcelle');
	});

	it('keeps an address out of coverage as a column, says why the filter hides nothing', () => {
		const { container } = render(CompareTable, { modele: adresses('trois_dont_hors_couverture'), differencesSeulement: true });
		// Two « 12 Rue des Lilas » of different postal codes: the band keeps the code, never cut.
		const bande = [...container.querySelectorAll('.compare-bande-cellule')];
		expect(bande.map((cellule) => texte(cellule.querySelector('.compare-bande-nom')))).toEqual(['12 Rue des Lilas', '12 Rue des Lilas', 'Adresse à Rennes (35)']);
		expect(bande.map((cellule) => texte(cellule.querySelector('.compare-bande-discriminant')))).toEqual(['(85000)', '(85100)', null]);
		const hors = container.querySelectorAll('thead th.compare-commune')[2];
		expect(texte(hors.querySelector('a.compare-nom'))).toBe('Adresse à Rennes (35)');
		expect(texte(hors.querySelector('.compare-hc-note'))).toBe('Aucune valeur : ne vaut pas zéro.');
		expect(texte(container.querySelector('.compare-filtre'))).toBe(
			'Aucun critère n’est masqué : Adresse à Rennes (35) est hors couverture, donc aucun critère n’est identique dans toutes les colonnes.'
		);
	});

	it('C40, C41: the declared sales row says its count, its suffix reason as the page, « Identique » for two equal counts', () => {
		const deux = render(CompareTable, { modele: adresses('deux_adresses') }).container;
		const ligne = deux.querySelector('tr[data-cle="ventes_declarees"]')!;
		expect(texte(ligne.querySelector('th.compare-critere span'))).toBe('Ventes déclarées à cette adresse');
		expect(texte(ligne.querySelectorAll('td')[1].querySelector('.compare-ecart'))).toBe('Identique');
		const trois = render(CompareTable, { modele: adresses('trois_dont_hors_couverture') }).container;
		const suffixe = trois.querySelectorAll('tr[data-cle="ventes_declarees"] td')[1];
		expect(suffixe.getAttribute('data-state')).toBe('not_available');
		expect(texte(suffixe.querySelector('.state-badge'))).toBe('Suffixe non distingué');
		// C44: no sale attached is its state, never « 0 » nor « Identique ».
		const aucune = render(CompareTable, { modele: adresses('deux_designations') }).container
			.querySelectorAll('tr[data-cle="ventes_declarees"] td')[1];
		expect(aucune.getAttribute('data-state')).toBe('not_available');
		expect(texte(aucune.querySelector('.state-badge'))).toBe('Aucune vente rattachée');
		expect(texte(aucune.querySelector('.compare-ecart'))).not.toBe('Identique');
	});

	// Second Codex review of 04.5-08 (D45-01, D45-08): a served reason without wording changes the label
	// only; the cell keeps its typed absence and its Source button, with the raw served data.
	it('C40: a served reason without wording keeps its state and its Source button, « Valeur non affichable » without a sentence', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const servie = projeterComparaisonAdresses(
			structuredClone((adressesServies as Record<string, { donnees: Brut }>).trois_dont_hors_couverture.donnees)
		);
		const ligneServie = servie.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'ventes_declarees')!;
		Object.assign(ligneServie.cellules[1], { etat: 'not_available', motif: 'suffixe_futur', valeur: null, periode: null });
		const { container } = render(CompareTable, { modele: composeComparaisonAdresses(servie) });
		const cellule = container.querySelectorAll('tr[data-cle="ventes_declarees"] td')[1];
		expect(cellule.getAttribute('data-state')).toBe('not_available');
		const badge = cellule.querySelector('.state-badge');
		expect(texte(badge)).toBe('Valeur non affichable');
		expect(badge?.getAttribute('data-provisoire')).toBe('true');
		expect(cellule.querySelector('.state-help')).toBeNull();
		expect(cellule.querySelector('.compare-non-affichable')).toBeNull();
		const bouton = cellule.querySelector<HTMLButtonElement>('.source-button');
		expect(bouton).not.toBeNull();
		await fireEvent.click(bouton!);
		const volet = container.ownerDocument.querySelector('dialog.source-panel')!;
		// B6, I4: the panel title names the address with its postal code and commune (homonymous streets).
		expect(texte(volet.querySelector('h2'))).toBe('Ventes déclarées à cette adresse · 12 Rue des Lilas, 85100 La Roche-sur-Yon');
		expect(texte(volet)).toContain('Valeur non affichable');
		expect(texte(volet)).not.toMatch(/La source ne publie pas|suffixe_futur|Suffixe/);
		// The raw served data: the field, its null value (an absence) and its served state.
		const brut = [...volet.querySelectorAll('.source-panel-raw dd')].map((dd) => texte(dd));
		expect(brut).toEqual([ligneServie.cellules[1].field_id, 'null', 'Non publiée']);
	});

	it('« Différences seulement » hides exactly the rows served as identical (D-116: two same absences)', () => {
		const modele = adresses('deux_adresses');
		const identiques = modele.groupes.flatMap((groupe) => groupe.lignes).filter((l) => l.identiques).map((l) => l.cle);
		expect(identiques).toContain('prix_median_appartement');
		const { container } = render(CompareTable, { modele, differencesSeulement: true });
		const affichees = [...container.querySelectorAll('tr[data-cle]')].map((tr) => tr.getAttribute('data-cle'));
		expect(affichees.some((cle) => identiques.includes(cle!))).toBe(false);
		expect(affichees.length + identiques.length).toBe(modele.groupes.flatMap((groupe) => groupe.lignes).length);
	});
});

// --- 04-10: DPE documents in the same table (D-102, CMP-02, D-116) ----------------------------

describe('CompareTable — DPE documents compared (04-10)', () => {
	type Brut = Parameters<typeof projeterComparaisonDocuments>[0];
	const documents = (cle: 'ancien_et_actuel' | 'meme_methode_trois') =>
		composeComparaisonDocuments(
			projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: Brut }>)[cle].donnees))
		);

	it('one tbody per served group, each column linked to its document with its address line and badges', () => {
		const { container, getByRole } = render(CompareTable, { modele: documents('ancien_et_actuel') });
		expect([...container.querySelectorAll('tbody')].map((tbody) => tbody.getAttribute('data-groupe'))).toEqual([
			'etiquettes',
			'logement',
			'cout_validite',
			'adresse'
		]);
		const entetes = [...container.querySelectorAll('thead th.compare-commune')];
		expect(entetes.map((th) => th.querySelector('a.compare-nom')?.getAttribute('href'))).toEqual(['/dpe/2185E0000001A', '/dpe/2385E0123456A']);
		expect(entetes.map((th) => texte(th.querySelector('.compare-identite')))).toEqual([
			'Adresse déclarée : « 12 rue des Lilas »',
			'Adresse déclarée non affichée'
		]);
		expect(entetes.map((th) => [...th.querySelectorAll('.compare-badge')].map(texte))).toEqual([['Ancienne méthode'], []]);
		expect(getByRole('button', { name: 'Retirer DPE n° 2385E0123456A' })).toBeInTheDocument();
		// The band shows the number whole, without a discriminant.
		expect([...container.querySelectorAll('.compare-bande-nom')].map(texte)).toEqual(['2185E0000001A', '2385E0123456A']);
		expect(container.querySelector('.compare-bande')?.classList.contains('compare-bande-documents')).toBe(true);
		expect(container.querySelectorAll('.compare-bande-discriminant')).toHaveLength(0);
	});

	it('writes « méthodes de calcul différentes » in warn, a verdict and a gap without warn', () => {
		const { container } = render(CompareTable, { modele: documents('ancien_et_actuel') });
		const ecart = (cle: string) => ligne(container, cle).querySelectorAll('td')[1].querySelector('.compare-ecart')!;
		expect(texte(ecart('classe_energie'))).toBe('Écart non calculé · méthodes de calcul différentes');
		expect(ecart('classe_energie').classList.contains('compare-ecart-warn')).toBe(true);
		expect(texte(ecart('type_batiment'))).toBe('Identique');
		expect(ecart('type_batiment').classList.contains('compare-ecart-warn')).toBe(false);
		expect([...ecart('cout_annuel').classList].filter((classe) => !/^svelte-/.test(classe))).toEqual(['compare-ecart']);
	});

	it('« Différences seulement » hides exactly the rows served as identical (D-116 for documents)', () => {
		const modele = documents('ancien_et_actuel');
		const identiques = modele.groupes.flatMap((groupe) => groupe.lignes).filter((l) => l.identiques).map((l) => l.cle);
		// Two absences of the same state stay identical; a real gap stays shown.
		expect(identiques).toContain('adresse_ban');
		expect(identiques).not.toContain('cout_annuel');
		const { container } = render(CompareTable, { modele, differencesSeulement: true });
		const affichees = [...container.querySelectorAll('tr[data-cle]')].map((tr) => tr.getAttribute('data-cle'));
		expect(affichees.some((cle) => identiques.includes(cle!))).toBe(false);
		expect(affichees).toContain('cout_annuel');
		expect(affichees.length + identiques.length).toBe(modele.groupes.flatMap((groupe) => groupe.lignes).length);
	});

	it('gives the gaps no colour of judgement for the three natures: only a reason is in warn', () => {
		type Brut2 = Parameters<typeof projeterComparaisonAdresses>[0];
		const modeles = [
			modele(trois()),
			composeComparaisonAdresses(
				projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: Brut2 }>).deux_adresses.donnees))
			),
			documents('meme_methode_trois')
		];
		for (const courant of modeles) {
			const { container, unmount } = render(CompareTable, { modele: courant });
			const lignes = [...container.querySelectorAll('.compare-ecart')];
			expect(lignes.length, courant.nature ?? 'commune').toBeGreaterThan(0);
			for (const p of lignes) {
				const classes = [...p.classList].filter((classe) => !/^svelte-/.test(classe));
				expect(classes, `${courant.nature ?? 'commune'} ${texte(p)}`).toEqual(
					p.getAttribute('data-ecart') === 'motif' ? ['compare-ecart', 'compare-ecart-warn'] : ['compare-ecart']
				);
			}
			unmount();
		}
	});
});

// --- 04-11 (D-102, R06-09/7): a DPE designated on a compared address ---------------------------

describe('CompareTable — DPE designated on compared addresses (04-11)', () => {
	type BrutA = Parameters<typeof projeterComparaisonAdresses>[0];
	type BrutD = Parameters<typeof projeterComparaisonDocuments>[0];
	type CasAdresses = 'deux_adresses' | 'deux_designations' | 'une_designation_une_ecartee' | 'designation_en_panne';
	const adresses = (cle: CasAdresses) =>
		composeComparaisonAdresses(
			projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutA }>)[cle].donnees))
		);
	const documents = () =>
		composeComparaisonDocuments(
			projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: BrutD }>).ancien_et_actuel.donnees))
		);
	const PHRASE = "Désigné par vous pour comparer ; ce document n'est rattaché à aucun logement.";
	// The rows of the document comparator, in the served order (R5, plan 04.5-13: per m² first, totals after).
	const LIGNES_DOCUMENT = [
		'classe_energie', 'consommation_m2', 'classe_climat', 'emissions_m2', 'consommation', 'emissions',
		'type_batiment', 'surface', 'periode_construction', 'energie_chauffage',
		'cout_annuel', 'etabli_le', 'valable_jusqu_au', 'methode',
		'adresse_declaree', 'adresse_ban'
	];

	it('the same couple reads the same in both comparators: values, gaps and reasons (R06-09/7)', () => {
		const designes = render(CompareTable, { modele: adresses('deux_designations') });
		const directs = render(CompareTable, { modele: documents() });
		const lignesDirectes = [...directs.container.querySelectorAll<HTMLTableRowElement>('tr[data-cle]')];
		expect(lignesDirectes.map((tr) => tr.getAttribute('data-cle'))).toEqual(LIGNES_DOCUMENT);
		for (const directe of lignesDirectes) {
			const cle = directe.getAttribute('data-cle')!;
			const designee = ligne(designes.container, `designe_${cle}`);
			expect(texte(designee.querySelector('th')), cle).toBe(texte(directe.querySelector('th')));
			const cellules = (tr: Element) => [...tr.querySelectorAll('td')].map((td) => [td.getAttribute('data-state'), texte(td)]);
			expect(cellules(designee), cle).toEqual(cellules(directe));
		}
		// The method mismatch is said the same way, the gap read against the same document.
		const classe = ligne(designes.container, 'designe_classe_energie').querySelectorAll('td')[1];
		expect(texte(classe.querySelector('.compare-ecart'))).toBe('Écart non calculé · méthodes de calcul différentes');
		expect(texte(ligne(designes.container, 'designe_cout_annuel').querySelectorAll('td')[1].querySelector('.compare-ecart'))).toBe(
			'+200 €/an par rapport à DPE n° 2185E0000001A'
		);
	});

	it("follows the documents rows, preceded once by the sentence of the contract, in « Documents à l'adresse »", () => {
		const { container } = render(CompareTable, { modele: adresses('deux_designations') });
		const documentsGroupe = container.querySelector('tbody[data-groupe="documents"]')!;
		const cles = [...documentsGroupe.querySelectorAll('tr[data-cle]')].map((tr) => tr.getAttribute('data-cle'));
		expect(cles.slice(0, 2)).toEqual(['dpe_declares', 'dpe_plus_recent']);
		expect(cles.slice(2).every((cle) => cle?.startsWith('designe_'))).toBe(true);
		const notes = [...container.querySelectorAll('tr.compare-designation-note')];
		expect(notes).toHaveLength(1);
		expect(texte(notes[0])).toBe(PHRASE);
		expect(notes[0].nextElementSibling?.getAttribute('data-cle')).toBe('designe_classe_energie');
		expect(source).toContain("Désigné par vous pour comparer ; ce document n'est rattaché à aucun logement.");
		// No designation, no designated row and no sentence.
		const sans = render(CompareTable, { modele: adresses('deux_adresses') });
		expect(sans.container.querySelectorAll('tr[data-designe]')).toHaveLength(0);
		expect(sans.container.querySelectorAll('tr.compare-designation-note')).toHaveLength(0);
	});

	it('names the designated DPE in its header, « Retirer la désignation » per address', async () => {
		const ondesigner = vi.fn();
		const onretirerdesignation = vi.fn();
		const { container, getByRole } = render(CompareTable, {
			modele: adresses('une_designation_une_ecartee'),
			ondesigner,
			onretirerdesignation
		});
		const [premiere, seconde] = [...container.querySelectorAll('thead th.compare-commune')];
		expect(texte(premiere.querySelector('.designation-texte'))).toBe('DPE désigné : n° 2185E0000001A · classe D · mai 2020');
		await fireEvent.click(getByRole('button', { name: 'Retirer la désignation du DPE pour 12 Rue des Lilas, 85000 La Roche-sur-Yon' }));
		expect(onretirerdesignation).toHaveBeenCalledWith('85191_0420_00012');
		const designer = within(seconde as HTMLElement).getByRole('button', { name: 'Désigner un DPE pour 1 Rue du Lieu, 85000 La Roche-sur-Yon' });
		expect(designer).toHaveAttribute('aria-expanded', 'false');
		expect(ondesigner).not.toHaveBeenCalled();
	});

	it('a column without designation says « Aucun DPE désigné », muted, without a state nor a gap', () => {
		const { container } = render(CompareTable, { modele: adresses('une_designation_une_ecartee') });
		const designees = [...container.querySelectorAll('tr[data-designe="true"]')];
		expect(designees.map((tr) => tr.getAttribute('data-cle'))).toEqual(LIGNES_DOCUMENT.map((cle) => `designe_${cle}`));
		for (const tr of designees) {
			const td = tr.querySelectorAll('td')[1];
			expect(td.getAttribute('data-state')).toBe('sans-designation');
			expect(texte(td)).toBe('Aucun DPE désigné');
			expect(td.querySelector('.compare-ecart')).toBeNull();
			expect(td.querySelector('button')).toBeNull();
		}
		expect(source).toMatch(/\.compare-sans-designation,\s*\.compare-hors-couverture \{[^}]*color: var\(--color-muted\);/);
	});

	it('a designation that could not be read keeps its number, its rows say the outage', () => {
		const { container } = render(CompareTable, { modele: adresses('designation_en_panne') });
		const seconde = container.querySelectorAll('thead th.compare-commune')[1];
		// 04-DECISIONS-TEXTES row 65.
		expect(texte(seconde.querySelector('.designation-texte'))).toBe('DPE désigné : n° 2385E0123456A (détails indisponibles)');
		const td = ligne(container, 'designe_classe_energie').querySelectorAll('td');
		expect(td[0].getAttribute('data-state')).toBe('sans-designation');
		expect(td[1].getAttribute('data-state')).toBe('temporary_error');
	});

	it('« Différences seulement » keeps the sentence only while a designated row is shown', () => {
		const modele = adresses('deux_designations');
		const { container } = render(CompareTable, { modele, differencesSeulement: true });
		const restantes = [...container.querySelectorAll('tr[data-designe="true"]')];
		expect(restantes.length).toBeGreaterThan(0);
		expect(restantes.every((tr) => tr.getAttribute('data-identiques') === 'false')).toBe(true);
		expect(container.querySelectorAll('tr.compare-designation-note')).toHaveLength(1);
	});
});

// --- B6 (plan 04.5-10): one continuous table per nature, 2 to 4 columns ---------------------------

describe('CompareTable — B6, one continuous table for the three natures (UI-013)', () => {
	type BrutAdresses = Parameters<typeof projeterComparaisonAdresses>[0];
	type BrutDocuments = Parameters<typeof projeterComparaisonDocuments>[0];
	const CAS = {
		'communes, 2': () => modele(deux()),
		'communes, 3': () => modele(trois()),
		'adresses, 2': () =>
			composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutAdresses }>).deux_adresses.donnees))),
		'adresses, désignations': () =>
			composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutAdresses }>).deux_designations.donnees))),
		'documents, 2': () =>
			composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: BrutDocuments }>).ancien_et_actuel.donnees))),
		'documents, 3': () =>
			composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: BrutDocuments }>).meme_methode_trois.donnees)))
	};

	it.each(Object.keys(CAS) as (keyof typeof CAS)[])('%s: one table, its groups consecutive bodies of it, row and column headers', (cas) => {
		const model = CAS[cas]();
		const { container } = render(CompareTable, { modele: model });
		const tables = container.querySelectorAll('table.compare-table');
		expect(tables).toHaveLength(1);
		const [table] = tables;
		const groupes = [...container.querySelectorAll('tbody[data-groupe]')];
		expect(groupes.map((tbody) => tbody.getAttribute('data-groupe'))).toEqual(model.groupes.map((groupe) => groupe.cle));
		expect(groupes.every((tbody) => tbody.parentElement === table)).toBe(true);
		// Nothing between two groups: each group body is followed by the next one.
		groupes.slice(1).forEach((tbody, index) => expect(groupes[index].nextElementSibling).toBe(tbody));
		const colonnes = [...table.querySelectorAll('thead th[scope="col"][data-code]')];
		expect(colonnes.map((th) => th.getAttribute('data-code'))).toEqual(model.colonnes.map((colonne) => colonne.code));
		colonnes.forEach((th, index) => expect(texte(th.querySelector('.compare-nom'))).toBe(model.colonnes[index].nom.replace(/\s+/g, ' ')));
		expect(table.querySelectorAll('tbody th[scope="row"]')).toHaveLength(model.groupes.flatMap((groupe) => groupe.lignes).length);
	});
});

// --- R5 (plan 04.5-13, Codex review): the yearly totals of a document in a detail level ----------
//
// The per-m² measures are read at first level; the two yearly totals (`dpe.document.conso_ep`,
// `dpe.document.emissions_ges`) follow them in a closed <details> of the table (« Totaux annuels »),
// direct and designated alike: opened by its summary, opened for printing, each value keeping its Source.

describe('CompareTable — R5: the yearly totals of the documents in a detail level', () => {
	type BrutA = Parameters<typeof projeterComparaisonAdresses>[0];
	type BrutD = Parameters<typeof projeterComparaisonDocuments>[0];
	const CAS = {
		directe: {
			modele: () =>
				composeComparaisonDocuments(
					projeterComparaisonDocuments(structuredClone((documentsServis as Record<string, { donnees: BrutD }>).meme_methode_trois.donnees))
				),
			prefixe: ''
		},
		designee: {
			modele: () =>
				composeComparaisonAdresses(
					projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutA }>).deux_designations.donnees))
				),
			prefixe: 'designe_'
		}
	};

	it.each(Object.keys(CAS) as (keyof typeof CAS)[])('%s: per m² at first level, the totals after them in a closed detail', async (nom) => {
		const { modele, prefixe } = CAS[nom];
		const { container } = render(CompareTable, { modele: modele() });
		const lignes = (cles: string[]) => cles.map((cle) => ligne(container, `${prefixe}${cle}`));
		const parM2 = lignes(['consommation_m2', 'emissions_m2']);
		const totaux = lignes(['consommation', 'emissions']);
		for (const tr of parM2) {
			expect(tr.hidden, tr.dataset.cle).toBe(false);
			expect(tr.closest('[data-detail]')).toBeNull();
			expect(tr.hasAttribute('data-detail')).toBe(false);
		}
		const details = container.querySelectorAll<HTMLDetailsElement>('details.compare-detail');
		expect(details).toHaveLength(1);
		const [detail] = details;
		expect(detail.open).toBe(false);
		expect(texte(detail.querySelector('summary'))).toBe('Totaux annuels');
		// The detail row comes after the per-m² rows, right before the totals it opens.
		const bascule = detail.closest('tr')!;
		const rangees = [...container.querySelectorAll('tr')];
		expect(rangees.indexOf(bascule)).toBeGreaterThan(rangees.indexOf(parM2[1]));
		expect(bascule.nextElementSibling).toBe(totaux[0]);
		expect(totaux[0].nextElementSibling).toBe(totaux[1]);
		// Their visibility follows the native [open] state in CSS, never a `hidden` attribute set by
		// script (Codex review of the fix: without JavaScript, `hidden` never left the rows).
		for (const tr of totaux) {
			expect(tr.getAttribute('data-detail'), tr.dataset.cle).toBe('true');
			expect(tr.hasAttribute('hidden'), tr.dataset.cle).toBe(false);
		}
		expect(detail.querySelector('summary')!.getAttribute('aria-controls')!.split(' ')).toEqual(totaux.map((tr) => tr.id));

		// Opened natively: the rows are untouched (no state of their own), each value with its Source.
		detail.open = true;
		await fireEvent(detail, new Event('toggle'));
		for (const tr of totaux) {
			expect(tr.hasAttribute('hidden'), tr.dataset.cle).toBe(false);
			const cellules = [...tr.querySelectorAll('td')];
			expect(cellules.length).toBeGreaterThan(0);
			for (const td of cellules) expect(td.querySelector('.source-button'), td.dataset.code).not.toBeNull();
		}
		expect(texte(totaux[0].querySelector('th'))).toContain("Consommation annuelle d'énergie primaire");
	});

	it('the detail rows are folded by CSS on the native state only (no `hidden`, no toggle state)', () => {
		// The rendered behaviour (closed, opened and printed, without JavaScript) is checked in a browser
		// (comparer-natures.spec.ts); here, the component carries no script state for these rows.
		expect(source).not.toMatch(/hidden=\{ligne\.detail/);
		expect(source).not.toMatch(/ontoggle=/);
	});

	it('no detail level for the communes and the addresses without designation', () => {
		const communes = render(CompareTable, { modele: modele(trois()) });
		expect(communes.container.querySelector('details.compare-detail')).toBeNull();
		expect(communes.container.querySelector('tr[hidden]')).toBeNull();
		communes.unmount();
		const sans = render(CompareTable, {
			modele: composeComparaisonAdresses(
				projeterComparaisonAdresses(structuredClone((adressesServies as Record<string, { donnees: BrutA }>).deux_adresses.donnees))
			)
		});
		expect(sans.container.querySelector('details.compare-detail')).toBeNull();
	});
});

// 04.8-05 (UI-SPEC v4 § 6.4, § 8): the `.cmp` form of the mock-up around the same served model.
describe('CompareTable — forme .cmp (04.8-05)', () => {
	it('a focusable region named « Tableau de comparaison », a hidden caption, one rowgroup per group, row headers', () => {
		const reponse = trois();
		const { container, getByRole } = render(CompareTable, { modele: modele(reponse) });
		const region = getByRole('region', { name: 'Tableau de comparaison' });
		expect(region).toHaveAttribute('tabindex', '0');
		const table = region.querySelector('table')!;
		expect(table.classList.contains('cmp')).toBe(true);
		expect(table.firstElementChild?.tagName).toBe('CAPTION');
		expect(table.querySelector('caption')?.classList.contains('compare-caption')).toBe(true);
		expect(source).toMatch(/\.compare-caption \{[^}]*position: absolute;[^}]*clip-path: inset\(50%\);/);
		const colonnes = reponse.donnees.communes.length;
		const groupes = [...container.querySelectorAll('tbody[data-groupe]')];
		expect(groupes.length).toBeGreaterThan(1);
		for (const groupe of groupes) {
			const titre = groupe.querySelector('tr:first-child > th')!;
			expect(titre.getAttribute('scope')).toBe('rowgroup');
			expect(titre.getAttribute('colspan')).toBe(String(colonnes + 1));
		}
		for (const rangee of container.querySelectorAll('tr[data-cle]')) {
			expect(rangee.querySelector(':scope > th')?.getAttribute('scope')).toBe('row');
			expect(rangee.querySelectorAll(':scope > td')).toHaveLength(colonnes);
		}
	});

	it('an absent value is its served state on a badge, never a value and never zero', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		const absences = [...container.querySelectorAll('td[data-state]')].filter(
			(td) => !['value', 'sans-designation', 'non-affiche'].includes(td.getAttribute('data-state')!)
		);
		expect(absences.length).toBeGreaterThan(0);
		for (const td of absences) {
			expect(td.querySelector('.state-value'), td.getAttribute('data-state')!).toBeNull();
			expect(td.querySelector('.state-badge, .compare-hors-couverture'), td.getAttribute('data-state')!).not.toBeNull();
			expect(texte(td.querySelector('.state-badge, .compare-hors-couverture'))).not.toMatch(/^0/);
		}
	});
});

// C3 (inventory of 2026-11-10, arbitrated by Ianis on 2026-11-10: « oui à la période et à la source derrière
// le bouton d'info »; this amends the rule of B6 and lot 6 that wrote them under the row label). The face of
// the comparator of communes and of DPE documents shows the label, the value and the gap; the period and the
// source are read in the panel, which already said both. The addresses keep their dated wording on the face:
// their panel does not say it in the same words yet.
describe('CompareTable — period and source behind the information button (C3)', () => {
	const panneau = (container: HTMLElement) => texte(container.ownerDocument.querySelector('dialog.source-panel'))!;

	it('communes: no period nor source on the face, in a row header or in a cell', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		expect(container.querySelectorAll('.compare-meta-commune')).toHaveLength(0);
		expect(container.querySelectorAll('.compare-meta')).toHaveLength(0);
		expect(texte(container.querySelector('table'))).not.toMatch(/Source\s:/);
	});

	it('communes: the row panel still says the period and the source the header wrote', async () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const row = ligne(container, 'nb_mutations_12m_maison');
		await fireEvent.click(row.querySelector<HTMLButtonElement>('th.compare-critere .source-button')!);
		expect(panneau(container)).toContain('2024-06-01 → 2025-06-01');
		expect(panneau(container)).toContain('Demandes de valeurs foncières (DVF)');
	});

	it('communes: every cell that showed a period or a source says them in its own panel', () => {
		for (const reponse of [deux(), trois()]) {
			for (const item of modele(reponse).groupes.flatMap((groupe) => groupe.lignes)) {
				for (const cellule of item.cellules) {
					const volet = JSON.stringify(cellule.volet);
					if (cellule.periode) expect(volet, `${item.cle} ${cellule.code} period`).toContain(JSON.stringify(cellule.periode).slice(1, -1));
					if (cellule.source) expect(volet, `${item.cle} ${cellule.code} source`).toContain(JSON.stringify(cellule.source.nom).slice(1, -1));
				}
			}
		}
	});

	it('DPE documents: no period nor source on the face, both in the panel of each cell', () => {
		type Servi = Parameters<typeof projeterComparaisonDocuments>[0];
		const servi = (documentsServis as unknown as Record<string, { donnees: Servi }>).meme_methode_trois.donnees;
		const model = composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(servi)));
		const { container } = render(CompareTable, { modele: model });
		expect(container.querySelectorAll('.compare-meta, .compare-meta-commune')).toHaveLength(0);
		let lues = 0;
		for (const cellule of model.groupes.flatMap((groupe) => groupe.lignes).flatMap((item) => item.cellules)) {
			const volet = JSON.stringify(cellule.volet);
			const periode = cellule.periodeAffichee !== undefined ? cellule.periodeAffichee : cellule.periode;
			if (periode) expect(volet).toContain(JSON.stringify(periode.replace(/^Période\s:\s/, '')).slice(1, -1));
			if (cellule.source) expect(volet).toContain(JSON.stringify(cellule.source.nom).slice(1, -1));
			if (periode || cellule.source) lues += 1;
		}
		expect(lues).toBeGreaterThan(0);
	});

	it('addresses keep their dated wording on the face (their panel words the date differently)', () => {
		type Servi = Parameters<typeof projeterComparaisonAdresses>[0];
		const servi = (adressesServies as unknown as Record<string, { donnees: Servi }>).deux_adresses.donnees;
		const { container } = render(CompareTable, { modele: composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(servi))) });
		expect(container.querySelectorAll('td .compare-meta').length).toBeGreaterThan(0);
	});

	it('a DPE designated on an address reads like a document: nothing on the face, period and source in its panel', () => {
		type Servi = Parameters<typeof projeterComparaisonAdresses>[0];
		const servi = (adressesServies as unknown as Record<string, { donnees: Servi }>).deux_designations.donnees;
		const model = composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(servi)));
		const { container } = render(CompareTable, { modele: model });
		expect(container.querySelectorAll('tr[data-designe="true"]').length).toBeGreaterThan(0);
		expect(container.querySelectorAll('tr[data-designe="true"] .compare-meta')).toHaveLength(0);
		for (const cellule of model.groupes.flatMap((groupe) => groupe.lignes).filter((item) => item.designe).flatMap((item) => item.cellules)) {
			const volet = JSON.stringify(cellule.volet);
			const periode = cellule.periodeAffichee !== undefined ? cellule.periodeAffichee : cellule.periode;
			if (periode) expect(volet).toContain(JSON.stringify(periode.replace(/^Période\s:\s/, '')).slice(1, -1));
			if (cellule.source) expect(volet).toContain(JSON.stringify(cellule.source.nom).slice(1, -1));
		}
	});
});

// C4 (inventory of 2026-11-10): « par rapport à Nantes » was written under every gap of every column.
// The reference is said once, in the corner of the table; each gap keeps the words for a screen reader,
// which reads a cell without the corner.
describe('CompareTable — the reference of the gaps is written once (C4)', () => {
	function avecEcart() {
		const reponse = deux();
		const revenu = reponse.donnees.criteres.find((critere) => critere.cle === 'revenu_median')!;
		revenu.cellules[1] = { ...revenu.cellules[1], ecart: { valeur: 999, unite: 'euro_per_uc_year' } };
		return render(CompareTable, { modele: modele(reponse) });
	}

	it('a gap shows its figure; the reference stays in the cell for a screen reader only', () => {
		const { container } = avecEcart();
		const ecart = ligne(container, 'revenu_median').querySelectorAll('td')[1].querySelector('.compare-ecart')!;
		// The whole served sentence stays in the cell, word for word.
		expect(texte(ecart)).toBe('+999 €/UC/an par rapport à Ancienne');
		const cache = ecart.querySelector('.sr-only');
		// Declared change (direction A, T10): the row says « €/UC/an » once after its label, so the eye reads
		// the figure alone; the unit joins the words kept for a screen reader.
		expect(texte(cache)).toBe('€/UC/an par rapport à Ancienne');
		const visible = [...ecart.childNodes].filter((noeud) => noeud !== cache).map((noeud) => noeud.textContent).join('');
		expect(normaliser(visible)).toBe('+999');
	});

	it('the corner of the table names the reference once, for the eye', () => {
		const { container } = avecEcart();
		const legende = container.querySelector('thead th.compare-coin .compare-reference-ecarts');
		expect(texte(legende)).toBe('Écarts par rapport à Ancienne');
		expect(legende?.getAttribute('aria-hidden')).toBe('true');
		expect(container.querySelectorAll('.compare-reference-ecarts')).toHaveLength(1);
	});

	// Declared change (direction A, T7 to T9): a verdict of the communes is drawn as a sign and a reason under
	// an absent value is not drawn; both keep their whole sentence in the cell. The rule this test holds is
	// now read on the text of the cell, against the model: no word of a verdict or of a reason is cut.
	it('a verdict or a reason keeps its whole sentence', () => {
		const model = modele(deux());
		const { container } = render(CompareTable, { modele: model });
		const attendus = model.groupes
			.flatMap((groupe) => groupe.lignes)
			.flatMap((item) => item.cellules)
			.flatMap((cellule) => (cellule.ecart && cellule.ecart.kind !== 'ecart' ? [normaliser(cellule.ecart.texte)] : []));
		const autres = [...container.querySelectorAll('.compare-ecart:not([data-ecart="ecart"])')];
		expect(autres.length).toBeGreaterThan(0);
		expect(autres.map((ligneEcart) => texte(ligneEcart))).toEqual(attendus);
		// What is not drawn is exactly a sign or a reason under an absence; any other sentence is in sight.
		for (const ligneEcart of autres) {
			const dessinee = !(ligneEcart as HTMLElement).dataset.signe && !(ligneEcart as HTMLElement).dataset.tu;
			expect(ligneEcart.querySelector('.sr-only') === null, texte(ligneEcart)!).toBe(dessinee);
		}
	});
});

// R-09: a value an arrondissement holds from its commune (`synthese.heritages`) shows the mark of the
// fiche, « Commune », in its cell; on paper the mark gives way to its object (« Commune de Paris »).
describe('CompareTable — the mark of a value inherited from the commune (R-09)', () => {
	function avecHeritage() {
		const model = modele(deux());
		const zone = model.groupes.flatMap((groupe) => groupe.lignes).find((item) => item.cle === 'zone_abc')!;
		zone.cellules[0] = {
			...zone.cellules[0],
			etat: { kind: 'valeur', texte: 'Abis', unite: null },
			echelle: { pictogramme: 'commune', mot: 'Commune', enClair: 'Commune de Paris' }
		};
		zone.cellules[1] = { ...zone.cellules[1], etat: { kind: 'valeur', texte: 'B1', unite: null } };
		return model;
	}

	it('marks the inherited cell, and only that one', () => {
		const { container } = render(CompareTable, { modele: avecHeritage() });
		const [heritee, propre] = ligne(container, 'zone_abc').querySelectorAll('td');
		expect(texte(heritee.querySelector('.state-value'))).toBe('Abis');
		expect(texte(heritee.querySelector('.compare-echelle-ecran .ech'))).toBe('Commune');
		expect(heritee.querySelector('.compare-echelle-ecran svg')).not.toBeNull();
		expect(texte(heritee.querySelector('.compare-echelle-clair'))).toBe('Commune de Paris');
		expect(propre.querySelector('.compare-echelle')).toBeNull();
		expect(container.querySelectorAll('.compare-echelle')).toHaveLength(1);
	});

	it('a comparison without an inherited value draws no mark', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		expect(container.querySelector('.compare-echelle')).toBeNull();
	});
});

// C11 (inventory of 2026-11-10): the toggle of a detail level is the fold of the socle, a 44 px target, and
// the name of a cell's explanation button carries no dash.
describe('CompareTable — detail toggle and button names (C11)', () => {
	it('the toggle « Totaux annuels » is the shared fold, not a fold of its own', () => {
		type Servi = Parameters<typeof projeterComparaisonDocuments>[0];
		const servi = (documentsServis as unknown as Record<string, { donnees: Servi }>).meme_methode_trois.donnees;
		const { container } = render(CompareTable, { modele: composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(servi))) });
		const detail = container.querySelector('details.compare-detail')!;
		expect(detail.classList.contains('depliant')).toBe(true);
		expect(detail.classList.contains('depliant-accent')).toBe(true);
		// The 24 px summary and its chevron drawn here are gone: the socle draws both (44 px).
		expect(source).not.toMatch(/\.compare-detail-resume::before/);
		expect(source).not.toMatch(/\.compare-detail-resume \{[^}]*min-height/);
	});

	it('no explanation button is named with a dash', () => {
		const { container } = render(CompareTable, { modele: modele(trois()) });
		const noms = [...container.querySelectorAll('button.source-button')].map((bouton) => bouton.getAttribute('aria-label') ?? '');
		expect(noms.length).toBeGreaterThan(0);
		for (const nom of noms) expect(nom).not.toContain('—');
	});
});

// C2 (inventory of 2026-11-10; frame of the orchestrator on the reading rule of Ianis): the comparison of
// communes was one table of 62 rows, 7 898 px high at 375 px. « Repères » stays as it was, always shown; each
// category after it is a fold, closed on arrival, opened by its title. A native `details` in the title row:
// the rows follow its state in CSS, so it works without JavaScript, and they are always printed. The
// addresses and the DPE documents keep their groups as they were.
describe('CompareTable — the categories of a comparison of communes fold (C2)', () => {
	const pli = (container: HTMLElement, groupe: string) =>
		container.querySelector<HTMLDetailsElement>(`tbody[data-groupe="${groupe}"] > tr.compare-groupe details.compare-groupe-pli`);

	it('« Repères » is not a fold; each category is one, closed on arrival, its title as summary', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		expect(pli(container, 'reperes')).toBeNull();
		expect(texte(container.querySelector('tbody[data-groupe="reperes"] th[scope="rowgroup"]'))).toBe('Repères');
		for (const [cle, titre] of [['q-prix', 'Prix et ventes'], ['q-logement', 'Logement et DPE'], ['q-quotidien', 'Vivre dans la commune']]) {
			const details = pli(container, cle)!;
			expect(details, cle).not.toBeNull();
			expect(details.open, cle).toBe(false);
			expect(details.hasAttribute('open'), cle).toBe(false);
			expect(texte(details.querySelector('summary')), cle).toBe(titre);
			expect(details.classList.contains('depliant'), cle).toBe(true);
			// The rows stay in the table, after the title row: only the stylesheet folds them.
			expect(container.querySelectorAll(`tbody[data-groupe="${cle}"] > tr[data-cle]`).length, cle).toBeGreaterThan(0);
		}
	});

	it('the rows follow the native state of the fold on screen only, never a `hidden` attribute', () => {
		expect(source).toMatch(/@media screen \{[^}]*tbody:has\(> \.compare-groupe \.compare-groupe-pli:not\(\[open\]\)\) > tr:not\(\.compare-groupe\)/);
		const { container } = render(CompareTable, { modele: modele(deux()) });
		expect(container.querySelectorAll('tr[hidden]')).toHaveLength(0);
	});

	it('reaching a group from the page summary opens it before the focus moves', async () => {
		const vue = render(CompareTable, { modele: modele(deux()) });
		const details = pli(vue.container, 'q-logement')!;
		expect(vue.component.focaliserGroupe('q-logement')).toBe(true);
		expect(details.open).toBe(true);
		expect(document.activeElement).toBe(vue.container.querySelector('#comparaison-groupe-q-logement'));
	});

	it('the addresses and the DPE documents keep their groups unfolded, without a fold', () => {
		type ServiA = Parameters<typeof projeterComparaisonAdresses>[0];
		type ServiD = Parameters<typeof projeterComparaisonDocuments>[0];
		const adresses = (adressesServies as unknown as Record<string, { donnees: ServiA }>).deux_adresses.donnees;
		const documents = (documentsServis as unknown as Record<string, { donnees: ServiD }>).meme_methode_trois.donnees;
		for (const model of [
			composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(adresses))),
			composeComparaisonDocuments(projeterComparaisonDocuments(structuredClone(documents)))
		]) {
			const { container, unmount } = render(CompareTable, { modele: model });
			expect(container.querySelectorAll('details.compare-groupe-pli')).toHaveLength(0);
			unmount();
		}
	});
});

// C10 (inventory of 2026-11-10): the categories of the comparator wore no tint. Each one takes the tint and
// the mark of its category on the commune fiche (`teinte-q-…`, `IconeCategorie`); « Repères » and the other
// natures stay neutral.
describe('CompareTable — the tint of each category (C10)', () => {
	it('a category wears its tint and its mark on its title row; « Repères » wears none', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		for (const cle of ['q-prix', 'q-logement', 'q-quotidien']) {
			const titre = container.querySelector(`tbody[data-groupe="${cle}"] > tr.compare-groupe`)!;
			expect(titre.classList.contains(`teinte-${cle}`), cle).toBe(true);
			expect(titre.querySelector('summary svg.icone-categorie'), cle).not.toBeNull();
			expect(titre.querySelector('summary svg')?.getAttribute('aria-hidden'), cle).toBe('true');
		}
		const reperes = container.querySelector('tbody[data-groupe="reperes"] > tr.compare-groupe')!;
		expect([...reperes.classList].some((classe) => classe.startsWith('teinte-'))).toBe(false);
		expect(reperes.querySelector('svg')).toBeNull();
	});

	it('the addresses and the DPE documents wear no tint', () => {
		type ServiA = Parameters<typeof projeterComparaisonAdresses>[0];
		const adresses = (adressesServies as unknown as Record<string, { donnees: ServiA }>).deux_adresses.donnees;
		const { container } = render(CompareTable, { modele: composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone(adresses))) });
		expect(container.querySelectorAll('tr.compare-groupe[class*="teinte-"]')).toHaveLength(0);
	});
});

// Direction A (Ianis, 2026-11-10: « A est mieux, on y va »; D4 and T7 to T11 carried on the orchestrator's
// reading, wordings to be reread by Ianis). A comparison of communes reads one unit per row, a sign for a
// verdict and a badge alone for an absence. Nothing a cell said is removed from the page: the words stay in
// the cell for a screen reader and for the frozen references, which read `textContent`.
describe('CompareTable — direction A: one unit per row, signs, badge alone', () => {
	type ServiA = Parameters<typeof projeterComparaisonAdresses>[0];
	const adresses = () =>
		composeComparaisonAdresses(projeterComparaisonAdresses(structuredClone((adressesServies as unknown as Record<string, { donnees: ServiA }>).deux_adresses.donnees)));

	it('T10: the unit of a row is written once after its label; cell and gap keep it out of sight', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const revenu = ligne(container, 'revenu_median');
		expect(texte(revenu.querySelector('th.compare-critere .compare-unite'))).toBe('€/UC/an');
		expect(revenu.getAttribute('data-unite-dite')).toBe('true');
		// The cell still says its unit (reference, screen reader).
		expect(texte(revenu.querySelector('td .state-value'))).toMatch(/€\/UC\/an$/);
		const ecart = revenu.querySelector('td[data-code="44992"] .compare-ecart[data-ecart="ecart"]')!;
		expect(texte(ecart)).toMatch(/^[+−]\d[\d\s]* €\/UC\/an par rapport à Ancienne$/);
		expect(texte(ecart.querySelector('.sr-only'))).toBe('€/UC/an par rapport à Ancienne');
		// Every row that names a unit names the one its cells carry.
		const dites = [...container.querySelectorAll<HTMLTableRowElement>('tr[data-unite-dite="true"]')];
		expect(dites.length).toBeGreaterThan(5);
		for (const row of dites) {
			const unite = texte(row.querySelector('th .compare-unite'));
			const servies = [...row.querySelectorAll('td .state-value .unit')].map((u) => texte(u));
			expect(servies.length, row.dataset.cle).toBeGreaterThan(0);
			expect(new Set(servies), row.dataset.cle).toEqual(new Set([unite]));
		}
		// A row without unit (a count) names none.
		const sans = [...container.querySelectorAll<HTMLTableRowElement>('tr[data-ligne]:not([data-unite-dite])')];
		expect(sans.length).toBeGreaterThan(0);
		for (const row of sans) expect(row.querySelector('th .compare-unite'), row.dataset.cle).toBeNull();
	});

	it('T7, T8: a verdict is drawn as a sign, its sentence kept in the cell', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const verdicts = [...container.querySelectorAll<HTMLElement>('.compare-ecart[data-ecart="verdict"]')];
		expect(verdicts.length).toBeGreaterThan(0);
		for (const verdict of verdicts) {
			const phrase = texte(verdict)!;
			expect(verdict.dataset.signe, phrase).toBe(/^Valeur différente/.test(phrase) ? '≠' : '=');
			expect(texte(verdict.querySelector('.sr-only')), phrase).toBe(phrase);
		}
		expect(verdicts.some((verdict) => texte(verdict) === 'Identique')).toBe(true);
		expect(source).toMatch(/\.compare-ecart\[data-signe\]::before \{\s*content: attr\(data-signe\);/);
		expect(source).toMatch(/\.compare-groupe-compte::after \{\s*content: attr\(data-compte\);/);
	});

	it('T9: under an absent value the badge speaks alone; a reason under a served value stays in sight', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		const motifs = [...container.querySelectorAll<HTMLElement>('.compare-ecart[data-ecart="motif"]')];
		const sousAbsence = motifs.filter((motif) => motif.closest('td')!.dataset.state !== 'value');
		const sousValeur = motifs.filter((motif) => motif.closest('td')!.dataset.state === 'value');
		expect(sousAbsence.length).toBeGreaterThan(0);
		expect(sousValeur.length).toBeGreaterThan(0);
		for (const motif of sousAbsence) {
			expect(motif.dataset.tu, texte(motif)!).toBe('true');
			expect(texte(motif.querySelector(':scope > .sr-only')), texte(motif)!).toBe(texte(motif));
		}
		for (const motif of sousValeur) {
			expect(motif.dataset.tu, texte(motif)!).toBeUndefined();
			expect(motif.querySelector('.sr-only'), texte(motif)!).toBeNull();
		}
	});

	it('T11: a category says how many rows it folds, beside its title and outside its summary', () => {
		const { container } = render(CompareTable, { modele: modele(deux()) });
		for (const cle of ['q-prix', 'q-logement', 'q-quotidien']) {
			const corps = container.querySelector(`tbody[data-groupe="${cle}"]`)!;
			const lignes = corps.querySelectorAll('tr[data-ligne]').length;
			const compte = corps.querySelector('tr.compare-groupe th .compare-groupe-compte');
			// Drawn from the attribute: the text of the title cell stays the title.
			expect(compte?.getAttribute('data-compte'), cle).toBe(`${lignes} lignes`);
			expect(texte(compte), cle).toBe('');
			expect(compte?.closest('summary'), cle).toBeNull();
		}
		expect(container.querySelector('tbody[data-groupe="reperes"] .compare-groupe-compte')).toBeNull();
	});

	it('witness: the addresses are read as before (no unit after a label, no sign, no reason out of sight)', () => {
		const { container } = render(CompareTable, { modele: adresses() });
		expect(container.querySelectorAll('.compare-unite, [data-unite-dite], .compare-ecart[data-signe], .compare-ecart[data-tu], .compare-groupe-compte')).toHaveLength(0);
	});
});
