import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { render } from '@testing-library/svelte';
import SourcesPrint from './SourcesPrint.svelte';
import type { DatasetProvenance } from '$lib/api/resolve';

// D-11 / D-14 — bloc final sources/millésimes du document imprimé.
//
// Le composant n'écrit NI un nom de publication NI une année : les deux viennent des cartes de
// provenance déjà câblées au plan 49-06. Les tests montent donc des cartes, jamais des chaînes
// attendues « en dur » côté production.

function provenance(dataset: string, nom: string, periodeLabel: string | null): DatasetProvenance {
	return {
		nom,
		licence: 'Licence Ouverte',
		url: 'https://www.data.gouv.fr/',
		producteur: 'Producteur de test',
		dataset,
		periode: { statut: periodeLabel == null ? 'inconnue' : 'connue', label: periodeLabel, debut: null, fin: null },
		derniere_importation_reussie: null,
		couverture: { type: 'departements', departements: ['85'] },
		cadence: { statut: 'official', label: 'annuelle' }
	} as DatasetProvenance;
}

describe('SourcesPrint.svelte — tableau final sources/millésimes (D-11)', () => {
	it('rend une ligne par dataset, portant sa publication et son millésime', () => {
		const { container } = render(SourcesPrint, {
			props: {
				provenances: {
					dvf: provenance('dvf', 'Demandes de valeurs foncières (DVF)', '2020-2024'),
					dpe: provenance('dpe', 'Diagnostics de Performance Energetique', '2021'),
					loyers: provenance('loyers', 'Carte des loyers', '2025')
				},
				// FIDÉLITÉ (NV-01) : `loyers` est un agrégat COMMUNE, et une page qui câble une
				// provenance câble toujours aussi `millesimesFromCommune`, dont les 5 clés incluent
				// celle-ci. La carte vide d'origine était donc un câblage impossible en production —
				// et c'est précisément par là que le millésime d'une ligne commune était pris à la
				// provenance sur le papier alors que le ℹ️ de l'écran déclarait une absence.
				millesimes: { loyers: '2025' },
				sources: {}
			}
		});

		const lignes = container.querySelectorAll('tbody tr');
		expect(lignes).toHaveLength(3);
		const texte = container.textContent ?? '';
		expect(texte).toContain('Demandes de valeurs foncières (DVF)');
		expect(texte).toContain('2020-2024');
		expect(texte).toContain('Carte des loyers');
		expect(texte).toContain('2025');
	});

	it('millésime inconnu : la ligne existe et DÉCLARE l’absence, jamais l’année d’un voisin', () => {
		const { container } = render(SourcesPrint, {
			props: {
				provenances: {
					dvf: provenance('dvf', 'Demandes de valeurs foncières (DVF)', '2020-2024'),
					monuments: provenance('monuments', 'Monuments historiques', null)
				},
				millesimes: {},
				sources: {}
			}
		});

		const lignes = [...container.querySelectorAll('tbody tr')];
		expect(lignes).toHaveLength(2);
		const ligneMonuments = lignes.find((tr) => tr.textContent?.includes('Monuments historiques'));
		expect(ligneMonuments).toBeDefined();
		// L'année du voisin n'a pas été empruntée…
		expect(ligneMonuments?.textContent).not.toContain('2020-2024');
		// …et l'absence est déclarée dans le vocabulaire partagé.
		expect(ligneMonuments?.textContent).toContain('non disponible');
	});

	it('source non attribuée : la ligne n’affiche jamais « undefined »', () => {
		const { container } = render(SourcesPrint, {
			props: {
				provenances: {},
				// Un millésime commune existe, mais la carte de sources est vide (zip fail-closed).
				millesimes: { revenus: '2021' },
				sources: {}
			}
		});

		const lignes = [...container.querySelectorAll('tbody tr')];
		expect(lignes).toHaveLength(1);
		expect(lignes[0].textContent).toContain('2021');
		expect(lignes[0].textContent).not.toContain('undefined');
		expect(lignes[0].textContent).not.toContain('null');
	});

	it('cartes vides des deux côtés : AUCUN tableau, pas d’en-tête orphelin', () => {
		const { container } = render(SourcesPrint, {
			props: { provenances: {}, millesimes: {}, sources: {} }
		});

		expect(container.querySelector('table')).toBeNull();
		expect(container.textContent?.trim()).toBe('');
	});

	it('millésimes tous absents (5 clés nulles) : aucune ligne inventée', () => {
		// `millesimesFromCommune` renvoie TOUJOURS ses 5 clés, à `null` quand l'année n'est pas
		// publiée. Un dataset sans source ET sans millésime n'a rien à dire : pas de ligne.
		const { container } = render(SourcesPrint, {
			props: {
				provenances: {},
				millesimes: {
					demographie: null,
					revenus: null,
					vacance: null,
					fiscalite: null,
					loyers: null
				},
				sources: {}
			}
		});

		expect(container.querySelector('table')).toBeNull();
	});

	it('est masqué à l’écran, affiché à l’impression, et ses lignes ne sont pas coupées (D-14)', () => {
		const { container } = render(SourcesPrint, {
			props: {
				provenances: { dvf: provenance('dvf', 'Demandes de valeurs foncières (DVF)', '2024') },
				millesimes: {},
				sources: {}
			}
		});

		const bloc = container.querySelector('.bloc-impression');
		expect(bloc).not.toBeNull();
		expect(bloc?.classList.contains('hidden')).toBe(true);
		expect(bloc?.classList.contains('print:block')).toBe(true);
		// Les lignes sont DANS `.bloc-impression` : c'est ce sélecteur qui porte le
		// `break-inside: avoid` de l'app.css (règle `.bloc-impression tr`, task 1).
		const ligne = container.querySelector('tbody tr');
		expect(ligne).not.toBeNull();
		expect(bloc?.contains(ligne as Node)).toBe(true);
	});

	it('ne code en dur AUCUN nom de producteur : la colonne source vient de la provenance live', () => {
		const source = readFileSync('src/lib/components/fiche/SourcesPrint.svelte', 'utf8');
		const producteurs = [
			'DGFiP',
			'INSEE',
			'ADEME',
			'ANIL',
			'CEREMA',
			'ANAH',
			'Arcep',
			'BRGM',
			'Etalab',
			'Filosofi',
			'LOVAC',
			'SITADEL',
			'DINUM'
		];
		expect(producteurs.filter((p) => source.includes(p))).toEqual([]);
	});
});
