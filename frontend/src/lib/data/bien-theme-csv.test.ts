import { describe, expect, it } from 'vitest';
import { bienThemeCsvRows } from './bien-theme-csv';
import { mapBienToGrid } from './bien-grid';
import { mockResolve85 } from './__fixtures__/resolve-85';
import { COMMUNE_85047_CHALLANS } from './__fixtures__/commune-85';
import type { ThemeModel } from '$lib/types/fiche';

describe('Bien CSV scope', () => {
	it('exports the explicit Commune compartment without assigning a grain to Bien rows', () => {
		const theme: ThemeModel = {
			key: 'environnement', emoji: '', name: 'Synthetic', hint: '', sources: '', rows: [],
			rowsBien: [{ label: 'Local', value: '21 340' }],
			rowsCommune: [
				{ label: 'Communal', value: '21 516', presentation: { state: 'value', reserves: ['Declared income'] } },
				{ label: 'Vacancy', value: '0 %', presentation: { state: 'value', grain: 'Parc privé communal' } },
				{ label: 'Unavailable', value: 'n.d.', available: false, state: 'not_reported',
					presentation: { state: 'not_reported', grain: 'Commune', reserves: ['Source non publiée'],
						reason: { code: 'source_reason', label: 'Non publié', explanation: 'Valeur absente de la source.', evidence: 'Réponse qualifiée.' } } }
			]
		};
		expect(bienThemeCsvRows(theme)).toEqual([
			['Local', '21 340'], ['Communal', '21 516'], ['Vacancy', '0 %'], ['Unavailable', 'n.d.'],
			['Portée — Communal', 'Commune'], ['Réserve — Communal', 'Declared income'],
			['Portée — Vacancy', 'Parc privé communal'], ['Portée — Unavailable', 'Commune'],
			['État — Unavailable', 'not_reported'],
			['Motif — Unavailable', 'Non publié · Valeur absente de la source. · Réponse qualifiée.'],
			['Réserve — Unavailable', 'Source non publiée']
		]);
	});
	it('exports the real grid income scope and keeps the more specific LOVAC grain', () => {
		const result = mapBienToGrid(mockResolve85, COMMUNE_85047_CHALLANS);
		const environment = result.fiche.themes.find(theme => theme.key === 'environnement')!;
		const property = result.fiche.themes.find(theme => theme.key === 'propriete')!;
		expect(bienThemeCsvRows(environment)).toContainEqual(['Portée — Revenu déclaré médian', 'Commune']);
		expect(bienThemeCsvRows(property)).toContainEqual(['Portée — Taux de vacance', 'Parc privé communal']);
		expect(bienThemeCsvRows(property)).not.toContainEqual(['Portée — Taux de vacance', 'Commune']);
	});
});
