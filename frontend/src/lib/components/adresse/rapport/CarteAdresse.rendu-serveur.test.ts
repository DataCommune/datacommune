// @vitest-environment node
// What the server renders for the header map of the address fiche (04.10 UI contract § 2.1): the row of
// the button is there, empty, so that the button that comes with hydration moves nothing; without
// JavaScript there is no button, and nothing of the interactive map is in the HTML.
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';

// The test setup resolves `svelte` to its browser build; a server render needs the server build.
// @ts-expect-error the server entry of svelte has no declaration file of its own
vi.mock('svelte', () => import('../../../../../node_modules/svelte/src/index-server.js'));
vi.mock('$app/environment', () => ({ browser: false }));
// The dialog of the map imports the router of the client (C8, 04.10-09); under this setup its browser
// build would read `window` at load. The server never calls it: the dialog is mounted on a click.
vi.mock('$app/navigation', () => ({ pushState: vi.fn() }));

import CarteAdresse from './CarteAdresse.svelte';

const CARTE = {
	plan: '/carte/44109_9999_00002/plan.png',
	parcelles: '/carte/44109_9999_00002/parcelles.png',
	empriseM: 300,
	attribution: '© IGN (Plan IGN, Parcellaire Express)',
	licence: 'Licence Ouverte 2.0',
	geoportail: null
};

describe('CarteAdresse — rendered by the server', () => {
	const html = render(CarteAdresse, { props: { carte: CARTE, adresse: '12 rue des Lilas, Nantes', banId: '44109_9999_00002' } }).body;

	it('keeps the row of the button between the picture and its caption, without the button', () => {
		expect(html).toMatch(/<div class="carte-image[^"]*"[\s\S]*<div class="carte-action[^"]*">[\s\S]*<\/div>[\s\S]*<figcaption/);
		expect(html).not.toContain('<button');
		expect(html).not.toContain('Ouvrir la carte des parcelles');
	});

	it('renders no dialog and nothing of the interactive map', () => {
		expect(html).not.toContain('<dialog');
		expect(html).not.toMatch(/contexte|\/carte\/parcelles\//);
		// Non-vacuity: the two pictures of the fiche are there.
		expect(html.match(/<img/g)).toHaveLength(2);
	});
});
