// @vitest-environment node
// What the server renders for `/signaler`, as a visitor without JavaScript receives it (e2e pass of
// 2026-11-10: after a refusal the message typed was no longer in its field). The page is rendered
// here as SvelteKit renders it for the request that follows a plain post.
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';

// The test setup resolves `svelte` to its browser build; a server render needs the server build.
// @ts-expect-error the server entry of svelte has no declaration file of its own
vi.mock('svelte', () => import('../../../../node_modules/svelte/src/index-server.js'));
vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$app/forms', () => ({ enhance: () => ({ destroy() {} }) }));

import Page from './+page.svelte';

const CONTEXTE = { page: '/commune/44109', titre: 'Prix médian des maisons', lignes: 'a=1', source: 'DVF', version: '81d3926f' };
const DATA = { contexte: CONTEXTE, navigateur: 'Mozilla/5.0 (test)' };

const rendu = (form?: unknown) => render(Page, { props: { data: DATA, form } as never }).body;

describe('/signaler — rendered by the server', () => {
	it('before any answer the two fields carry no value: nothing to erase what is typed while the page loads', () => {
		const html = rendu();

		expect(html).toMatch(/<textarea[^>]*name="commentaire"[^>]*><\/textarea>/);
		expect(html.match(/<input[^>]*name="email"[^>]*>/)?.[0]).not.toContain('value=');
	});

	it('after a refusal the message and the address typed are given back in their fields, escaped', () => {
		const html = rendu({
			erreurs: { email: 'invalide' },
			valeurs: { commentaire: 'Le prix est <b>faux</b>.', email: 'pas-un-email' },
			contexte: CONTEXTE
		});

		expect(html).toMatch(/<textarea[^>]*name="commentaire"[^>]*>Le prix est &lt;b>faux&lt;\/b>\.<\/textarea>/);
		expect(html.match(/<input[^>]*name="email"[^>]*>/)?.[0]).toContain('value="pas-un-email"');
		expect(html).not.toContain('<b>faux</b>');
	});

	it('the context is shown and sent back without JavaScript, but for the width of the screen', () => {
		const html = rendu();

		expect(html).toContain('<input type="hidden" name="page" value="/commune/44109"');
		expect(html).toContain('<input type="hidden" name="largeur" value=""');
		expect(html).not.toContain(' px</dd>');
	});
});
