import { describe, expect, it, vi } from 'vitest';
import type { Cookies } from '@sveltejs/kit';
import {
	DEFAULT_WEEKLY_QUOTA,
	QUOTA_COOKIE,
	WEEK_MS,
	admettreFiche,
	creerCompteurFiches,
	creerVerificateurRobots,
	quotaHebdomadaire
} from './fiche-quota';

describe('quotaHebdomadaire (FICHE_QUOTA_WEEKLY)', () => {
	it('defaults to 100 fiches when unset, empty or not a whole number', () => {
		expect(DEFAULT_WEEKLY_QUOTA).toBe(100);
		for (const raw of [undefined, '', ' ', 'abc', '-1', '2.5']) expect(quotaHebdomadaire(raw), String(raw)).toBe(100);
	});

	it('reads another threshold, and 0 as « off »', () => {
		expect(quotaHebdomadaire('10')).toBe(10);
		expect(quotaHebdomadaire('0')).toBe(0);
	});
});

describe('creerCompteurFiches', () => {
	function compteur(limite: number) {
		let now = 1_000_000;
		const c = creerCompteurFiches({ limite, maintenant: () => now });
		return { c, avancer: (ms: number) => (now += ms), now: () => now };
	}

	it('counts distinct fiches: the same fiche again is free', () => {
		const { c } = compteur(2);
		expect(c.admettre('s', 'a').admise).toBe(true);
		expect(c.admettre('s', 'a').admise).toBe(true);
		expect(c.admettre('s', 'b').admise).toBe(true);
		expect(c.admettre('s', 'a').admise).toBe(true);
		expect(c.admettre('s', 'c')).toMatchObject({ admise: false, limite: 2 });
	});

	it('refuses the fiche past the threshold with the end of the window, per session', () => {
		const { c, now } = compteur(1);
		const debut = now();
		c.admettre('s1', 'a');
		const refus = c.admettre('s1', 'b');
		expect(refus).toEqual({ admise: false, limite: 1, reprise: new Date(debut + WEEK_MS) });
		expect(c.admettre('s2', 'b').admise).toBe(true);
	});

	it('opens a fresh week once seven days have passed since the first fiche', () => {
		const { c, avancer } = compteur(1);
		c.admettre('s', 'a');
		avancer(WEEK_MS - 1);
		expect(c.admettre('s', 'b').admise).toBe(false);
		avancer(1);
		expect(c.admettre('s', 'b').admise).toBe(true);
	});

	it('0 turns the quota off', () => {
		const { c } = compteur(0);
		for (let i = 0; i < 500; i++) expect(c.admettre('s', String(i)).admise).toBe(true);
		expect(c.taille).toBe(0);
	});

	it('stays bounded in memory: expired sessions go first, then the oldest', () => {
		let now = 0;
		const c = creerCompteurFiches({ limite: 5, maintenant: () => now, maxSessions: 3 });
		c.admettre('vieux', 'a');
		now = WEEK_MS;
		c.admettre('s1', 'a');
		c.admettre('s2', 'a');
		c.admettre('s3', 'a');
		expect(c.taille).toBe(3);
		c.admettre('s4', 'a');
		expect(c.taille).toBeLessThanOrEqual(3);
	});

	// /confidentialite § 5 promises the count « s'efface au plus tard 7 jours après la première fiche
	// ouverte »: a session that never comes back must not stay in memory until the table fills up.
	it('forgets a session seven days after its first fiche, even if it never comes back', () => {
		const { c, avancer } = compteur(5);
		c.admettre('parti', 'a');
		avancer(3 * 24 * 60 * 60 * 1000);
		c.admettre('reste', 'a');
		avancer(WEEK_MS - 3 * 24 * 60 * 60 * 1000 - 1);
		c.oublierExpirees();
		expect(c.taille, 'one millisecond before the end of its week').toBe(2);
		avancer(1);
		c.oublierExpirees();
		expect(c.taille).toBe(1);
	});

	it('forgets expired sessions on any admission, not only when the table is full', () => {
		const { c, avancer } = compteur(5);
		c.admettre('parti', 'a');
		avancer(WEEK_MS);
		c.admettre('nouveau', 'a');
		expect(c.taille).toBe(1);
	});
});

describe('creerVerificateurRobots', () => {
	const GOOGLEBOT = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)';

	function verificateur(ptr: Record<string, string[]>, a: Record<string, string[]>) {
		const reverse = vi.fn(async (ip: string) => {
			if (!ptr[ip]) throw Object.assign(new Error('ENOTFOUND'), { code: 'ENOTFOUND' });
			return ptr[ip];
		});
		const lookup = vi.fn(async (host: string) => (a[host] ?? []).map((address) => ({ address })));
		return { estRobotVerifie: creerVerificateurRobots({ resolveurs: { reverse, lookup } }), reverse, lookup };
	}

	it('admits a crawler whose address resolves back into its published domain', async () => {
		const { estRobotVerifie } = verificateur(
			{ '66.249.66.1': ['crawl-66-249-66-1.googlebot.com'] },
			{ 'crawl-66-249-66-1.googlebot.com': ['66.249.66.1'] }
		);
		expect(await estRobotVerifie(GOOGLEBOT, () => '66.249.66.1')).toBe(true);
	});

	it('refuses a claimed crawler whose PTR is outside the domain, or does not resolve back', async () => {
		const { estRobotVerifie } = verificateur(
			{ '203.0.113.5': ['googlebot.com.example.net'], '203.0.113.6': ['fake.googlebot.com'] },
			{ 'fake.googlebot.com': ['198.51.100.1'] }
		);
		expect(await estRobotVerifie(GOOGLEBOT, () => '203.0.113.5')).toBe(false);
		expect(await estRobotVerifie(GOOGLEBOT, () => '203.0.113.6')).toBe(false);
		expect(await estRobotVerifie(GOOGLEBOT, () => '203.0.113.7')).toBe(false);
	});

	it('never resolves anything for an ordinary browser, and caches a verdict', async () => {
		const { estRobotVerifie, reverse } = verificateur(
			{ '66.249.66.1': ['crawl.googlebot.com'] },
			{ 'crawl.googlebot.com': ['66.249.66.1'] }
		);
		const ip = vi.fn(() => '66.249.66.1');
		expect(await estRobotVerifie('Mozilla/5.0 (Windows NT 10.0) Firefox/140.0', ip)).toBe(false);
		expect(await estRobotVerifie(null, ip)).toBe(false);
		expect(ip).not.toHaveBeenCalled();
		await estRobotVerifie(GOOGLEBOT, ip);
		await estRobotVerifie(GOOGLEBOT, ip);
		expect(reverse).toHaveBeenCalledTimes(1);
	});
});

describe('admettreFiche', () => {
	function cookies(initial?: string) {
		const jar = new Map<string, string>(initial ? [[QUOTA_COOKIE, initial]] : []);
		const set = vi.fn((name: string, value: string) => void jar.set(name, value));
		return { cookies: { get: (name: string) => jar.get(name), set } as unknown as Cookies, set, jar };
	}

	function evenement(c: Cookies) {
		return {
			cookies: c,
			request: new Request('https://site.invalid/adresse/x', { headers: { 'user-agent': 'Mozilla/5.0 Firefox/140.0' } }),
			getClientAddress: () => '192.0.2.1'
		};
	}

	it('creates an httpOnly first-party session cookie for a week, then reuses it', async () => {
		const { cookies: c, set, jar } = cookies();
		expect((await admettreFiche(evenement(c), '85191_0420_00012')).admise).toBe(true);
		expect(set).toHaveBeenCalledTimes(1);
		const [name, value, options] = set.mock.calls[0] as unknown as [string, string, Record<string, unknown>];
		expect(name).toBe(QUOTA_COOKIE);
		expect(value).toMatch(/^[0-9a-f]{32}$/);
		expect(options).toMatchObject({ path: '/adresse', httpOnly: true, sameSite: 'lax', maxAge: WEEK_MS / 1000 });
		expect(jar.get(QUOTA_COOKIE)).toBe(value);

		await admettreFiche(evenement(c), '85191_0420_00013');
		expect(set).toHaveBeenCalledTimes(1);
	});

	it('replaces a malformed session id instead of trusting it', async () => {
		const { cookies: c, set } = cookies('<script>');
		await admettreFiche(evenement(c), '85191_0420_00012');
		expect(set).toHaveBeenCalledTimes(1);
	});

	it('applies the default threshold of 100 distinct fiches to one session', async () => {
		const { cookies: c } = cookies('0123456789abcdef0123456789abcdef');
		for (let i = 0; i < 100; i++) {
			expect((await admettreFiche(evenement(c), `85191_0420_${String(i).padStart(5, '0')}`)).admise).toBe(true);
		}
		expect(await admettreFiche(evenement(c), '85191_0421_00001')).toMatchObject({ admise: false, limite: 100 });
		expect((await admettreFiche(evenement(c), '85191_0420_00000')).admise).toBe(true);
	});
});

// R-16, option B (orchestrator, 2026-11-10): a fiche whose identity turns out unknown, a street or an
// outage of ours is given back, but only the admission this request made, and never twice.
describe('rendre (R-16, option B)', () => {
	type Rendable = { admise: true; rendre?: () => void };

	it('gives back a fiche added by this admission: the place is free again', () => {
		const c = creerCompteurFiches({ limite: 1, maintenant: () => 1_000_000 });
		const a = c.admettre('s', 'a') as Rendable;
		expect(typeof a.rendre).toBe('function');
		a.rendre!();
		expect(c.admettre('s', 'b').admise).toBe(true);
		expect(c.admettre('s', 'c').admise).toBe(false);
	});

	it('a fiche counted before this admission has nothing to give back', () => {
		const c = creerCompteurFiches({ limite: 1, maintenant: () => 1_000_000 });
		c.admettre('s', 'a');
		expect((c.admettre('s', 'a') as Rendable).rendre).toBeUndefined();
		expect(c.admettre('s', 'b').admise).toBe(false);
	});

	it('gives back once: a second call after the fiche was counted again removes nothing', () => {
		const c = creerCompteurFiches({ limite: 1, maintenant: () => 1_000_000 });
		const premiere = c.admettre('s', 'a') as Rendable;
		premiere.rendre!();
		expect(c.admettre('s', 'a').admise).toBe(true);
		premiere.rendre!();
		expect(c.admettre('s', 'b').admise).toBe(false);
	});

	it('the two loaders of one request share one admission: a give-back is never undone by the second', async () => {
		const jar = new Map<string, string>([[QUOTA_COOKIE, 'cd'.repeat(16)]]);
		const cookies = { get: (n: string) => jar.get(n), set: vi.fn() } as unknown as Cookies;
		const nouvelleRequete = () => ({ cookies, request: new Request('https://site.invalid/adresse/x'), getClientAddress: () => '192.0.2.1' });
		for (let i = 0; i < 99; i++) await admettreFiche(nouvelleRequete(), `44109_0002_${String(i).padStart(5, '0')}`);
		const requete = nouvelleRequete();
		const identite = (await admettreFiche(requete, '44109_0003_00001')) as Rendable;
		identite.rendre!();
		expect((await admettreFiche(requete, '44109_0003_00001')).admise).toBe(true);
		expect((await admettreFiche(nouvelleRequete(), '44109_0003_00002')).admise).toBe(true);
		expect((await admettreFiche(nouvelleRequete(), '44109_0003_00003')).admise).toBe(false);
	});
});
