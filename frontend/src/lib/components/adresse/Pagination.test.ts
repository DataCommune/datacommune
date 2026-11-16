// Shared pagination of the address page (04-05) and its button variant for the panel of the parcel map
// (04.10-07, decision (d)). Without the two new properties the markup is today's: links that work without
// JavaScript. A direction given a function is a `button` of the same class and the same words, which
// calls it; the other direction keeps its link.
import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import Pagination from './Pagination.svelte';

const BASE = {
	plage: 'Ventes 6 à 10 sur 12',
	pages: 3,
	lienPrecedent: '?ventes_page=1&vg=0123456789abcdef',
	lienSuivant: '?ventes_page=3&vg=0123456789abcdef',
	etiquette: 'Pages des ventes publiées sur la parcelle',
	prefixe: 'ventes'
};

/** Classes of an element without the scoping class Svelte adds (`svelte-<hash>`). */
const classes = (e: Element) => [...e.classList].filter((c) => !c.startsWith('svelte-')).join(' ');

/** The markup of the navigation, attributes and texts, as a stable string. */
const balisage = (conteneur: HTMLElement) =>
	[...conteneur.querySelectorAll('.pagination nav > *')].map(
		(e) => `${e.tagName.toLowerCase()}|${classes(e)}|${e.getAttribute('href') ?? ''}|${e.getAttribute('type') ?? ''}|${e.textContent}`
	);

describe('Pagination', () => {
	it('without the new properties, renders the links of today, and no button', () => {
		const { container } = render(Pagination, BASE);
		expect(balisage(container)).toEqual([
			'a|pagination-page ventes-page|?ventes_page=1&vg=0123456789abcdef||Page précédente',
			'a|pagination-page ventes-page|?ventes_page=3&vg=0123456789abcdef||Page suivante'
		]);
		expect(container.querySelector('button')).toBeNull();
		expect(container.querySelector('.ventes-range')!.textContent).toBe('Ventes 6 à 10 sur 12');
		expect(screen.getByRole('navigation', { name: 'Pages des ventes publiées sur la parcelle' })).toBeTruthy();
	});

	it('a link click still goes to `onchanger` with its address', async () => {
		const onchanger = vi.fn((event: MouseEvent, _href: string) => event.preventDefault());
		render(Pagination, { ...BASE, onchanger });
		await fireEvent.click(screen.getByRole('link', { name: 'Page suivante' }));
		expect(onchanger).toHaveBeenCalledTimes(1);
		expect(onchanger.mock.calls[0][1]).toBe('?ventes_page=3&vg=0123456789abcdef');
	});

	it('with a function for each direction, renders buttons of the same class and the same words, which call it', async () => {
		const onprecedent = vi.fn();
		const onsuivant = vi.fn();
		const { container } = render(Pagination, { ...BASE, lienPrecedent: null, lienSuivant: null, onprecedent, onsuivant });
		expect(balisage(container)).toEqual(['button|pagination-page ventes-page||button|Page précédente', 'button|pagination-page ventes-page||button|Page suivante']);
		expect(container.querySelector('a')).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Page suivante' }));
		expect(onsuivant).toHaveBeenCalledTimes(1);
		expect(onprecedent).not.toHaveBeenCalled();
		await fireEvent.click(screen.getByRole('button', { name: 'Page précédente' }));
		expect(onprecedent).toHaveBeenCalledTimes(1);
	});

	it('one direction as a button, the other one keeps its link; a direction with neither is not rendered', () => {
		const mixte = render(Pagination, { ...BASE, onsuivant: vi.fn() });
		expect(balisage(mixte.container)).toEqual([
			'a|pagination-page ventes-page|?ventes_page=1&vg=0123456789abcdef||Page précédente',
			'button|pagination-page ventes-page||button|Page suivante'
		]);
		mixte.unmount();
		const premiere = render(Pagination, { ...BASE, lienPrecedent: null, lienSuivant: null, onsuivant: vi.fn() });
		expect(balisage(premiere.container)).toEqual(['button|pagination-page ventes-page||button|Page suivante']);
	});

	it('one page: the range only, hidden on screen, with or without the functions', () => {
		const { container } = render(Pagination, { ...BASE, pages: 1, lienPrecedent: null, lienSuivant: null, onsuivant: vi.fn() });
		expect(container.querySelector('.pagination')!.classList.contains('pagination-seule')).toBe(true);
		expect(container.querySelector('nav')).toBeNull();
	});
});
