import { describe, expect, it } from 'vitest';
import type { CommuneSynthese } from '$lib/api/commune';
import { panneauDeTheme } from './__fixtures__/fiche-commune-rendue';
import fixture from './__fixtures__/dvf-numeric-chain.json';

// http_projection is a snapshot captured from the application output and checked
// by the native SQL/API test; only price and count come from the independent oracle.
// The DOM contract is a separate consumer proof, not a live browser-to-database chain.
function renderProjection(document: CommuneSynthese) {
	return { container: panneauDeTheme(document, 'immobilier') };
}

function text(value: string | null | undefined): string {
	return (value ?? '').replace(/\s/g, ' ').replace(/(?<=\d)\.(?=\d{3}\b)/g, ' ').trim();
}

function assertLegacyOracle(container: HTMLElement) {
	const price = container.querySelector('[data-field-id="dvf.prix_m2_median_maison"] .state-value');
	const count = container.querySelector('[data-field-id="dvf.nb_mutations_12m_maison"] .state-value');
	expect(price, 'Missing rendered house price').not.toBeNull();
	expect(count, 'Missing rendered house count').not.toBeNull();
	expect(text(price?.textContent), 'Rendered DVF price differs from independent oracle').toBe('4 000 €/m²');
	expect(text(count?.textContent), 'Rendered DVF count differs from independent oracle').toBe('5');
}

// The indicator rows are read on the theme panel of the commune page, each row
// keyed by its line, since the two windows share a field id for their count.
function assertIndicatorOracle(container: HTMLElement) {
	const price = container.querySelector('[data-ligne="dvf-maison-mediane-courante"]');
	const count = container.querySelector('[data-ligne="dvf-maison-effectif-courant"]');
	expect(price, 'Missing rendered indicator price').not.toBeNull();
	expect(count, 'Missing rendered indicator count').not.toBeNull();
	expect(text(price?.querySelector('.state-value')?.textContent), 'Rendered indicator price differs from independent oracle').toBe('4 000 €/m²');
	expect(text(count?.querySelector('.state-value')?.textContent), 'Rendered indicator count differs from independent oracle').toBe('5');
	expect(price?.querySelector('.tab-note-texte')).toHaveTextContent('2025-01-01 → 2026-01-01 (fin exclue)');
	expect(count?.querySelector('.tab-note-texte')).toHaveTextContent('2025-01-01 → 2026-01-01 (fin exclue)');
}

describe('DVF raw-to-HTTP oracle consumed by the existing fiche renderer', () => {
	it('renders the verified legacy HTTP projection with its price, count and unit', () => {
		const document = fixture.http_projection.legacy as CommuneSynthese;
		const { container } = renderProjection(document);
		assertLegacyOracle(container);
	});


	it('renders the complete verified indicators HTTP projection and its bounded period', () => {
		const document = fixture.http_projection.indicators as CommuneSynthese;
		const { container } = renderProjection(document);
		assertIndicatorOracle(container);
	});
});
