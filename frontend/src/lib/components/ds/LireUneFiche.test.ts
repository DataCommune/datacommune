// « Lire une fiche » (lot 8 of 4.9, arbitrage A7): the whole legend by default, as `/datasets` shows it; a
// folded form for the address fiche, opened by the anchor `#lire`, written out for paper.
import { render, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import LireUneFiche from './LireUneFiche.svelte';
import { lignesEchelles, lignesEtats } from '$lib/data/lire-une-fiche';

const LIGNES = lignesEchelles().length + lignesEtats().length;

afterEach(() => history.replaceState(null, '', location.pathname));

describe('LireUneFiche', () => {
	it('shows the whole legend by default, nothing folded, once', () => {
		const { container } = render(LireUneFiche);
		expect(container.querySelector('section#lire')).not.toBeNull();
		expect(container.querySelector('details')).toBeNull();
		expect(container.querySelectorAll('.ligne')).toHaveLength(LIGNES);
	});

	it('folded: closed by default, the title, its lead and every line kept', () => {
		const { container, getByRole } = render(LireUneFiche, { props: { repliable: true } });
		expect(getByRole('heading', { level: 2 }).textContent).toBe('Lire une fiche');
		expect(container.textContent).toContain('Chaque valeur porte son échelle');
		const repli = container.querySelector('details')!;
		expect(repli).not.toHaveAttribute('open');
		expect(repli.querySelector('summary')?.textContent).toBe('Voir les échelles et les états');
		expect(repli.querySelectorAll('.ligne')).toHaveLength(LIGNES);
	});

	it('folded: the legend is written out for paper, outside the fold', () => {
		const { container } = render(LireUneFiche, { props: { repliable: true } });
		const papier = container.querySelector('.lire-impression')!;
		expect(papier.closest('details')).toBeNull();
		expect(papier.querySelectorAll('.ligne')).toHaveLength(LIGNES);
	});

	it('folded: arriving on `#lire` opens it', () => {
		history.replaceState(null, '', '#lire');
		const { container } = render(LireUneFiche, { props: { repliable: true } });
		expect(container.querySelector('details')).toHaveAttribute('open');
	});

	it('folded: a link to `#lire` followed on the page opens it, another anchor does not', async () => {
		const { container } = render(LireUneFiche, { props: { repliable: true } });
		history.replaceState(null, '', '#sources');
		window.dispatchEvent(new HashChangeEvent('hashchange'));
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(container.querySelector('details')).not.toHaveAttribute('open');
		history.replaceState(null, '', '#lire');
		window.dispatchEvent(new HashChangeEvent('hashchange'));
		await waitFor(() => expect(container.querySelector('details')).toHaveAttribute('open'));
	});
});
