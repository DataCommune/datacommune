import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

const getDpeDocumentMock = vi.hoisted(() => vi.fn());
const getDpeInventoryMock = vi.hoisted(() => vi.fn());
const searchAdressesMock = vi.hoisted(() => vi.fn());
const resolveMock = vi.hoisted(() => vi.fn());
const gotoMock = vi.hoisted(() => vi.fn());
vi.mock('$lib/api/dpe-inventory', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/dpe-inventory')>('$lib/api/dpe-inventory');
	return {
		...actual,
		getDpeDocument: (...args: unknown[]) => getDpeDocumentMock(...args),
		getDpeInventory: (...args: unknown[]) => getDpeInventoryMock(...args)
	};
});
vi.mock('$lib/api/adresses', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/adresses')>('$lib/api/adresses');
	return { ...actual, searchAdresses: (...args: unknown[]) => searchAdressesMock(...args) };
});
vi.mock('$lib/api/resolve', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/resolve')>('$lib/api/resolve');
	return { ...actual, resolveBienParAdresse: (...args: unknown[]) => resolveMock(...args) };
});
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => gotoMock(...args) }));

import DpeMatchingPreview from './DpeMatchingPreview.svelte';

const result = {
	inventory: {
		version: 2, state: 'ready', context_ref: 'ctx', items: [
			{ document_ref: 'doc-a', type_batiment: 'Appartement', surface_habitable: 48, etiquette_dpe: 'C', etiquette_ges: 'B', date_etablissement_dpe: null, date_fin_validite_dpe: null },
			{ document_ref: 'doc-b', type_batiment: null, surface_habitable: null, etiquette_dpe: null, etiquette_ges: null, date_etablissement_dpe: null, date_fin_validite_dpe: null }
		], total: 3, distribution: { A: 0, B: 0, C: 1, D: 0, E: 0, F: 0, G: 0, unknown: 2 }, page_size: 2, has_more: true, next_cursor: 'next', read_state: 'partial', association: 'source_ban_equality', address_check: 'unverified', source: 'ADEME', source_coverage: 'unknown', graph_state: 'not_checked', imported_at: null
	},
	attribution: { version: 2, status: 'unassigned', reason: 'dwelling_identity_unproven', diagnostic: null, candidates: [], building_context: { count: 3, distribution: { C: 1 }, truncated: true }, graph_state: 'not_checked' },
	rule_version: 'dpe-factual-comparison-v1', comparisons: [
		{ document_ref: 'doc-a', criteria: [{ criterion: 'type_bien', state: 'matching', target: 'appartement', source: 'appartement' }, { criterion: 'surface', state: 'known', target: 50, source: 48, target_nature: 'habitable_dpe', source_nature: 'habitable_dpe', delta: -2 }], blockers: [] },
		{ document_ref: 'doc-b', criteria: [{ criterion: 'type_bien', state: 'unknown', target: 'appartement', source: null }, { criterion: 'surface', state: 'unknown', target: 50, source: null, target_nature: 'habitable_dpe', source_nature: 'habitable_dpe', delta: null }], blockers: ['validity_unknown'] }
	], limits: ['inventory_partial', 'candidates_outside_page']
} as const;

describe('DpeMatchingPreview', () => {
	it('keeps criteria disabled until one address context is resolved', () => {
		const { getByRole } = render(DpeMatchingPreview, { props: { isAdmin: true, isProduction: false } });
		expect(getByRole('combobox', { name: 'Type de bien' })).toBeDisabled();
		expect(getByRole('spinbutton', { name: 'Surface' })).toBeDisabled();
		expect(getByRole('combobox', { name: 'Nature de la surface' })).toBeDisabled();
		expect(getByRole('button', { name: 'Comparer les documents' })).toBeDisabled();
	});
	it('keeps every candidate, limitations and pagination visible', async () => {
		const compare = vi.fn().mockResolvedValue(result);
		const { getByRole, getByText } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare, isAdmin: true, isProduction: false } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await waitFor(() => expect(getByRole('table')).toBeVisible());
		expect(within(getByRole('table')).getAllByRole('row')).toHaveLength(3);
		const scrollRegion = getByRole('region', { name: 'Tableau de comparaison, défilement horizontal' });
		expect(scrollRegion).toHaveAttribute('tabindex', '0');
		expect(within(scrollRegion).getByRole('table')).toHaveClass('comparison-table');
		expect(getByText(/inventaire partiel/i)).toBeVisible();
		expect(getByRole('button', { name: 'Page suivante' })).toBeVisible();
	});
	it('sends a numeric surface without failing in the browser binding', async () => {
		const compare = vi.fn().mockResolvedValue(result);
		const { getByRole } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare, isAdmin: true, isProduction: false } });
		await fireEvent.input(getByRole('spinbutton', { name: 'Surface' }), { target: { value: '50' } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await waitFor(() => expect(compare).toHaveBeenCalled());
		expect(compare.mock.calls[0][1]).toEqual({ surface: { value: 50, nature: 'habitable_dpe' } });
	});
	it('clears an obsolete result on correction and keeps inventory return keyboard-accessible', async () => {
		const compare = vi.fn().mockResolvedValue(result);
		const onReturnInventory = vi.fn();
		const { getByRole, queryByRole } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare, onReturnInventory, isAdmin: true, isProduction: false } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await waitFor(() => expect(getByRole('table')).toBeVisible());
		await fireEvent.change(getByRole('combobox', { name: 'Type de bien' }), { target: { value: 'maison' } });
		expect(queryByRole('table', { name: 'Comparaison de tous les documents de la page' })).toBeNull();
		const back = getByRole('button', { name: 'Retour à l’inventaire' });
		back.focus(); await fireEvent.keyDown(back, { key: 'Enter' }); await fireEvent.click(back);
		expect(onReturnInventory).toHaveBeenCalled();
	});
	it('shows a request error without claiming absence', async () => {
		const { getByRole, getByText, queryByText } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare: vi.fn().mockRejectedValue(new Error('offline')), isAdmin: true, isProduction: false } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await waitFor(() => expect(getByText(/momentanément indisponible/i)).toBeVisible());
		expect(queryByText(/aucun diagnostic/i)).toBeNull();
	});
	it('does not restore a late comparison after the target changes', async () => {
		let release!: (value: typeof result) => void;
		const compare = vi.fn().mockReturnValue(new Promise<typeof result>((resolve) => (release = resolve)));
		const { getByRole, queryByText } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare, isAdmin: true, isProduction: false } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await fireEvent.change(getByRole('combobox', { name: 'Type de bien' }), { target: { value: 'maison' } });
		release(result);
		await Promise.resolve();
		expect(queryByText('dpe-factual-comparison-v1')).toBeNull();
		expect(getByRole('button', { name: 'Comparer les documents' })).not.toBeDisabled();
	});
	it('ignores an old document response after another document is requested', async () => {
		let releaseOld!: (value: unknown) => void;
		getDpeDocumentMock
			.mockReturnValueOnce(new Promise((resolve) => (releaseOld = resolve)))
			.mockReturnValueOnce(new Promise(() => {}));
		const { getAllByRole, getByRole, getByText } = render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', compare: vi.fn().mockResolvedValue(result), isAdmin: true, isProduction: false } });
		await fireEvent.click(getByRole('button', { name: 'Comparer les documents' }));
		await waitFor(() => expect(getAllByRole('button', { name: 'Consulter' })).toHaveLength(2));
		await fireEvent.click(getAllByRole('button', { name: 'Consulter' })[0]);
		await fireEvent.click(getAllByRole('button', { name: 'Consulter' })[1]);
		releaseOld({ document_ref: 'doc-a' });
		await Promise.resolve();
		expect(getByText('Chargement du document…')).toBeVisible();
	});
	// 04-12: the address is chosen in this instance's search field (`CommuneSearch`, addresses only,
	// `/v1/adresses/recherche`), never by the former BAN client; the label sent to the resolver keeps
	// the « {numéro}{indice} {voie}, {CP} {Commune} » form of the former field.
	it('selects the address in the instance search field and resolves the same label as before', async () => {
		searchAdressesMock.mockReset().mockResolvedValue({
			ordre_types: ['adresse', 'commune'],
			resultats: [
				{ type_resultat: 'adresse', ban_id: '44109_1234_00007', numero: '7', rep: 'bis', nom_voie: 'rue Synthétique', code_postal: '44000', code_commune: '44109', nom_commune: 'Nantes', departement: { code: '44', nom: 'Loire-Atlantique' } }
			],
			hebergement: { ban: { etat: 'heberge', motif: null, departements: ['44', '85'] } }
		});
		resolveMock.mockReset().mockResolvedValue({ statut: 'ok', donnees: [{ dpe_inventory: { context_ref: 'ctx-embedded' } }] });
		getDpeInventoryMock.mockReset().mockResolvedValue({ ...result.inventory, context_ref: 'ctx-fresh' });
		gotoMock.mockReset();
		const { getByRole, getByText } = render(DpeMatchingPreview, { props: { isAdmin: true, isProduction: false } });
		const field = getByRole('combobox', { name: 'Adresse' });
		await fireEvent.change(getByRole('combobox', { name: 'Département' }), { target: { value: '44' } });
		await fireEvent.input(field, { target: { value: '7 rue Synth' } });
		await waitFor(() => expect(within(getByRole('listbox')).getAllByRole('option')[0]).toHaveTextContent('7bis rue Synthétique'));
		expect(searchAdressesMock.mock.calls[0][0]).toBe('7 rue Synth');
		expect(searchAdressesMock.mock.calls[0][1]).toMatchObject({ departement: '44' });
		await fireEvent.click(within(getByRole('listbox')).getAllByRole('option')[0]);
		await waitFor(() => expect(getByText('Adresse sélectionnée : 7bis rue Synthétique, 44000 Nantes')).toBeVisible());
		expect(resolveMock).toHaveBeenCalledWith('7bis rue Synthétique, 44000 Nantes');
		await waitFor(() => expect(getDpeInventoryMock).toHaveBeenCalledWith('ctx-embedded', null, 20));
		await waitFor(() => expect(getByRole('button', { name: 'Comparer les documents' })).toBeEnabled());
		expect(getByRole('combobox', { name: 'Type de bien' })).toBeEnabled();
		expect(gotoMock).not.toHaveBeenCalled();
	});
	it('blocks preview content outside admin or in production', () => {
		expect(render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', isAdmin: false, isProduction: false } }).queryByRole('form')).toBeNull();
		expect(render(DpeMatchingPreview, { props: { initialContextRef: 'ctx', isAdmin: true, isProduction: true } }).queryByRole('form')).toBeNull();
	});
});
