// The real commune page mounted on a served answer: the rules a test reads on a theme are read on the
// page the reader sees, with the projection of the page, never a copy of it. Since 4.9 the rows of a theme
// are spread over the categories of the address fiche: a theme is read in the block of the categories.
import { render } from '@testing-library/svelte';
import { vi } from 'vitest';
import type { CommuneSynthese, SourceAttribution } from '$lib/api/commune';
import { themesCommune } from '$lib/data/commune-grid';
import Page from '../../../routes/(marketing)/commune/[code]/+page.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(), invalidateAll: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/stores', async () => {
	const { writable } = await import('svelte/store');
	return {
		navigating: writable(null),
		page: writable({ url: new URL('https://site.invalid/commune/00000'), state: {} })
	};
});

export function renderFicheCommune(synthese: CommuneSynthese, sources: SourceAttribution[] | null = null) {
	return render(Page, {
		props: { data: { etat: 'fiche', code: synthese.code_insee ?? '00000', synthese, sources, consulteLe: '24 octobre 2026' } }
	});
}

/**
 * The block of the categories, where the rows of a theme are read on the mounted page; a theme without a
 * served row has none, so it throws. Rows are unique on the page: a test finds a row of the theme by its key.
 */
export function panneauDeTheme(synthese: CommuneSynthese, ancre: string, sources: SourceAttribution[] | null = null): HTMLElement {
	if (!themesCommune(synthese, sources).some((theme) => theme.ancre === ancre)) throw new Error(`Missing theme ${ancre}`);
	const lecture = renderFicheCommune(synthese, sources).container.querySelector<HTMLElement>('section.lecture');
	if (!lecture) throw new Error('Missing the categories of the fiche');
	return lecture;
}
