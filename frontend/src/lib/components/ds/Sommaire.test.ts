// Table of contents of a long legal page (I5, 2026-11-10): the section titles as links to their anchors, nothing else.
import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import Sommaire from './Sommaire.svelte';

const SECTIONS = [
	{ id: 'section-1', titre: '1. Objet et définitions' },
	{ id: 'section-2', titre: '2. Conditions d’accès' }
];

describe('Sommaire', () => {
	it('is a named navigation whose links point to the anchors of the sections, in order', () => {
		const { container, getByRole } = render(Sommaire, { sections: SECTIONS });
		const nav = getByRole('navigation', { name: 'Sommaire' });
		const liens = [...nav.querySelectorAll('a')];
		expect(liens.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
			['1. Objet et définitions', '#section-1'],
			['2. Conditions d’accès', '#section-2']
		]);
		// No word is added to the page: the name « Sommaire » lives in aria-label only.
		expect(nav.textContent).toBe('1. Objet et définitions2. Conditions d’accès');
		expect(container.querySelectorAll('nav :not(ul):not(li):not(a)')).toHaveLength(0);
	});

	it('is a list, so that a screen reader announces the number of sections', () => {
		const { container } = render(Sommaire, { sections: SECTIONS });
		expect(container.querySelectorAll('nav ul > li')).toHaveLength(2);
	});
});
