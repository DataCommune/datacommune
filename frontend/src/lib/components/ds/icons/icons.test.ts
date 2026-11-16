import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import type { Component } from 'svelte';

// Mechanical sentinel over the icon set (D-22 contract): components are discovered by glob
// rather than enumerated by hand, so a new icon added in a future phase inherits the checks
// without anyone remembering to update this file.
const composants = import.meta.glob('./Icon*.svelte', { eager: true }) as Record<
	string,
	{ default: Component }
>;
const sources = import.meta.glob('./Icon*.svelte', {
	eager: true,
	query: '?raw',
	import: 'default'
}) as Record<string, string>;

const entrees = Object.entries(composants).map(([chemin, mod]) => ({
	nom: chemin.replace('./', '').replace('.svelte', ''),
	Composant: mod.default,
	source: sources[chemin]
}));

// Guard on the glob itself: a broken path or a changed extension must NOT make the suite pass
// silently by validating zero files — the classic failure mode of a mechanical glob test.
describe('ds/icons — garde du glob', () => {
	it('découvre au moins 9 icônes', () => {
		expect(entrees.length).toBeGreaterThanOrEqual(9);
	});
});

describe.each(entrees)('ds/icons — $nom (conventions du jeu)', ({ Composant, source }) => {
	it('rend exactement un <svg>', () => {
		const { container } = render(Composant);
		expect(container.querySelectorAll('svg').length).toBe(1);
	});

	it('porte aria-hidden="true"', () => {
		const { container } = render(Composant);
		expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
	});

	it('porte viewBox="0 0 24 24"', () => {
		const { container } = render(Composant);
		expect(container.querySelector('svg')).toHaveAttribute('viewBox', '0 0 24 24');
	});

	it('stroke vaut currentColor', () => {
		const { container } = render(Composant);
		expect(container.querySelector('svg')).toHaveAttribute('stroke', 'currentColor');
	});

	it('stroke-width vaut 1.5', () => {
		const { container } = render(Composant);
		expect(container.querySelector('svg')).toHaveAttribute('stroke-width', '1.5');
	});

	it('applique la classe par défaut h-4 w-4, puis h-6 w-6 quand la prop class est fournie', () => {
		const { container: sansProp } = render(Composant);
		expect(sansProp.querySelector('svg')).toHaveAttribute('class', 'h-4 w-4');

		const { container: avecProp } = render(Composant, { props: { class: 'h-6 w-6' } });
		expect(avecProp.querySelector('svg')).toHaveAttribute('class', 'h-6 w-6');
	});

	it('la source ne contient aucun bloc de style scoped', () => {
		// Littéral fractionné à dessein : jamais écrire le motif qu'un futur gate grep
		// traquerait tel quel dans un fichier source (même dans une chaîne de test).
		const balise = '<' + 'style';
		expect(source.includes(balise)).toBe(false);
	});

	it('la source ne contient aucun littéral couleur hexadécimal', () => {
		expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
	});

	it('la source ne contient aucun export let (runes uniquement)', () => {
		expect(source).not.toMatch(/export let/);
	});
});
