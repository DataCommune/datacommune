import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import OverviewCards from './OverviewCards.svelte';

afterEach(cleanup);

describe('Admin overview revenue evidence', () => {
	it('renders missing MRR without a false zero or a catalog price', () => {
		const { container } = render(OverviewCards, {
			data: {
				users_count: 42,
				active_keys_count: 7,
				requests_24h: 1234,
				mrr_eur_estimated: null,
				mrr_status: 'unavailable',
				mrr_reason: 'billing_amounts_not_persisted'
			}
		});
		expect(screen.getByText('Indisponible')).toBeInTheDocument();
		expect(screen.getByText(/montants et la cadence de facturation/)).toBeInTheDocument();
		expect(screen.getByText(/ne mesure pas les encaissements/)).toBeInTheDocument();
		expect(container.textContent).not.toMatch(/0\s*€|39\s*€|MRR estimé/);
		expect(screen.getByText('42')).toBeInTheDocument();
		expect(screen.getByText('7')).toBeInTheDocument();
		expect(screen.getByText(/1\s*234/)).toBeInTheDocument();
	});
});
