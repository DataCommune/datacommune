// The neighbours of a commune page (SEO audit 2026-11-14): read beside the fiche, a failure drops
// the links only; the links name an arrondissement by its title and a commune of another
// departement with its code.
import { describe, expect, it, vi } from 'vitest';
import { liensVoisines, lireVoisines } from './communes-voisines';

const voisine = (code_insee: string, nom: string, departement_code: string, type_zone: 'commune' | 'arrondissement' = 'commune') => ({
	code_insee,
	nom,
	departement_code,
	type_zone,
	limitrophe: true
});

describe('lireVoisines', () => {
	it('asks the API for the code upper case and returns the served list', async () => {
		const liste = [voisine('2A041', 'Bastelicaccia', '2A')];
		const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ statut: 'ok', donnees: { code_insee: '2A004', voisines: liste } }), { status: 200 }));
		expect(await lireVoisines('2a004', fetchImpl as unknown as typeof fetch)).toEqual(liste);
		const url = new URL(String((fetchImpl.mock.calls[0] as unknown[])[0]), 'http://api.test');
		expect(url.pathname).toBe('/v1/communes/voisines');
		expect(url.searchParams.get('code_insee')).toBe('2A004');
	});

	it('is null, never an exception, for a failure or an answer outside the contract', async () => {
		for (const fetchImpl of [
			vi.fn(async () => new Response('{}', { status: 503 })),
			vi.fn(async () => new Response(JSON.stringify({ statut: 'ok', donnees: { code_insee: '44109' } }), { status: 200 })),
			vi.fn(async () => {
				throw new TypeError('network');
			})
		]) {
			expect(await lireVoisines('44109', fetchImpl as unknown as typeof fetch)).toBeNull();
		}
	});
});

describe('liensVoisines', () => {
	it('keeps the served order, titles an arrondissement and gives the code of another departement', () => {
		expect(
			liensVoisines(
				[voisine('94080', 'Vincennes', '94'), voisine('75111', 'Paris 11e Arrondissement', '75', 'arrondissement'), voisine('75056', 'Paris', '75')],
				'75'
			)
		).toEqual([
			{ code: '94080', href: '/commune/94080', nom: `Vincennes${String.fromCharCode(0xa0)}(94)` },
			{ code: '75111', href: '/commune/75111', nom: '11e arrondissement de Paris' },
			{ code: '75056', href: '/commune/75056', nom: 'Paris' }
		]);
	});

	it('gives no code when the departement of the page is unknown', () => {
		expect(liensVoisines([voisine('85194', 'Sallertaine', '85')], null)[0].nom).toBe('Sallertaine');
	});
});
