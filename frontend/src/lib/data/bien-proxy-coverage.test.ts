import { describe, it, expect } from 'vitest';
import { mapBienToFiche } from './bien-fiche';
import { mockResolve85 } from './__fixtures__/resolve-85';

function fiche(dataset: string, data: object) {
	return mapBienToFiche({ ...mockResolve85, datasets: { ...mockResolve85.datasets, [dataset]: data } }, null);
}
function row(model: ReturnType<typeof fiche>, id: string) {
	return model.themes.flatMap(t => [...(t.rowsBien ?? []), ...(t.rowsCommune ?? [])]).find(r => r.fieldId === id)!;
}
const complete = { statut: 'vide', complet: true };

describe('COR11/12 proxy coverage', () => {
	it('does not turn a failed GASPAR branch into out of coverage', () => {
		const model = fiche('risques', { risques_naturels: null, argiles: { exposition: 'Faible' }, sources: { risques_naturels: { statut: 'indisponible' }, argiles: { statut: 'disponible', complet: true } } });
		expect(row(model, 'bien.risques.inondation').state).toBe('temporary_error');
		expect(row(model, 'bien.risques.expo_argiles').value).toBe('Faible');
	});
	it('preserves officially empty risk lists as a known absence in the queried list', () => {
		const model = fiche('risques', { risques_naturels: [], sources: { risques_naturels: complete } });
		expect(row(model, 'bien.risques.inondation').state).toBe('value');
		expect(row(model, 'bien.risques.inondation').value).toBe('Non');
	});
	it('cannot infer no flood from an incomplete first page', () => {
		const model = fiche('risques', { risques_naturels: [{ risques_detail: [{ libelle_risque_long: 'Séisme' }] }], sources: { risques_naturels: { statut: 'partiel', complet: false, total: 51, nombre_retournes: 50 } } });
		expect(row(model, 'bien.risques.inondation').state).toBe('not_reported');
		expect(row(model, 'bien.risques.sismique').state).toBe('value');
	});
	it('keeps every PLU candidate and a prescription outage visible', () => {
		const model = fiche('urbanisme', { zones_plu: [{ properties: { libelle: 'UA', typezone: 'U' } }, { properties: { libelle: 'NB', typezone: 'N' } }], prescriptions: null, sources: { zones_plu: { statut: 'disponible', complet: true }, prescriptions: { statut: 'indisponible' } } });
		expect(row(model, 'bien.urbanisme.zonage_libelle').value).toContain('UA');
		expect(row(model, 'bien.urbanisme.zonage_libelle').value).toContain('NB');
		expect(model.themes.flatMap(t => t.rowsBien ?? []).some(r => r.kind === 'context' && r.value.includes('indisponible'))).toBe(true);
	});
});
