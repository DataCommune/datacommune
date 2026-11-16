// Weekly allowance of address fiches per anonymous session (H-3, PROJECT.md).
//
// The unit is the fiche: one distinct `/adresse/{ban_id}` per session, so a reload,
// a page change of a list or a return to a fiche already seen is free. The session
// is a random id in a first-party cookie, and the allowance is a fixed window of
// seven days opened by the first fiche of the session.
//
// Why not the API's per-IP ceiling alone: every visitor sends the same public
// `dc_anon_` key, and an IP is shared behind CGNAT and VPNs, while a visitor
// understands « 100 fiches » rather than « 2 000 requests ». The per-IP ceilings
// of the API stay in place underneath as the backstop: a client that drops the
// cookie gets a new session each time, and only those ceilings bound it.
//
// Verified crawlers are outside the allowance: a user agent that claims to be one
// is admitted only if its address resolves back (reverse then forward DNS) into
// the crawler's published domain. A claim that does not verify counts like anyone.
//
// The threshold is `FICHE_QUOTA_WEEKLY` (default 100, `0` turns the quota off):
// the value is Ianis's to set, nothing here freezes it. The counters live in this
// Node process: a restart gives every session a fresh week, which errs on the
// visitor's side. One instance serves the site (docker-compose.public.yml).
import { lookup as dnsLookup, reverse as dnsReverse } from 'node:dns/promises';
import type { Cookies } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';

export const QUOTA_COOKIE = 'dc_session';
export const DEFAULT_WEEKLY_QUOTA = 100;
export const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_SESSIONS = 200_000;
const BOT_CACHE_MS = 24 * 60 * 60 * 1000;
const SESSION_ID = /^[0-9a-f]{32}$/;
const SWEEP_MS = 60 * 1000;

/** `FICHE_QUOTA_WEEKLY` read as a whole number of fiches; anything else is the default. */
export function quotaHebdomadaire(raw: string | undefined): number {
	if (raw === undefined || raw.trim() === '') return DEFAULT_WEEKLY_QUOTA;
	const n = Number(raw);
	return Number.isInteger(n) && n >= 0 ? n : DEFAULT_WEEKLY_QUOTA;
}

/**
 * `rendre` is set only when this admission counted a fiche new to the session: it gives the place back,
 * once, if the fiche turns out not to be one (unknown key, street, outage of ours: R-16, option B).
 */
export type Admission = { admise: true; rendre?: () => void } | { admise: false; limite: number; reprise: Date };

interface Session {
	debut: number;
	fiches: Set<string>;
}

export function creerCompteurFiches({
	limite,
	maintenant = Date.now,
	maxSessions = MAX_SESSIONS
}: {
	limite: number;
	maintenant?: () => number;
	maxSessions?: number;
}) {
	const sessions = new Map<string, Session>();

	// A session is inserted once, at its first fiche, so the Map holds them by start date: the
	// expired ones are at the front. The privacy policy promises the count is gone seven days after
	// the first fiche, so they are dropped at the next admission or sweep, not when the table fills.
	function oublierExpirees(now: number) {
		for (const [id, s] of sessions) {
			if (now - s.debut < WEEK_MS) break;
			sessions.delete(id);
		}
	}

	function purger(now: number) {
		oublierExpirees(now);
		// Still full of live sessions: the oldest windows go first (Map keeps insertion
		// order). Their owners get a fresh week - the failure mode favours the visitor.
		for (const id of sessions.keys()) {
			if (sessions.size < maxSessions) break;
			sessions.delete(id);
		}
	}

	return {
		admettre(sessionId: string, fiche: string): Admission {
			if (limite === 0) return { admise: true };
			const now = maintenant();
			oublierExpirees(now);
			let session = sessions.get(sessionId);
			if (session && now - session.debut >= WEEK_MS) {
				// Out of start order only if the clock went back; drop it all the same.
				sessions.delete(sessionId);
				session = undefined;
			}
			if (!session) {
				if (sessions.size >= maxSessions) purger(now);
				session = { debut: now, fiches: new Set() };
				sessions.set(sessionId, session);
			}
			if (session.fiches.has(fiche)) return { admise: true };
			if (session.fiches.size >= limite) {
				return { admise: false, limite, reprise: new Date(session.debut + WEEK_MS) };
			}
			session.fiches.add(fiche);
			// Bound to the set this admission wrote in: a later week of the same session is never touched.
			const fiches = session.fiches;
			let rendue = false;
			return {
				admise: true,
				rendre() {
					if (rendue) return;
					rendue = true;
					fiches.delete(fiche);
				}
			};
		},
		/** Drops the sessions whose week is over; the server calls it on a timer, traffic or not. */
		oublierExpirees() {
			oublierExpirees(maintenant());
		},
		get taille() {
			return sessions.size;
		}
	};
}

// Crawlers whose operators publish a reverse-DNS verification. A user agent
// matching `ua` is verified when the PTR name of its address ends with one of
// `domaines` and that name resolves back to the same address.
const ROBOTS: { ua: RegExp; domaines: string[] }[] = [
	{ ua: /googlebot|google-inspectiontool|googleother|storebot-google/i, domaines: ['.googlebot.com', '.google.com', '.googleusercontent.com'] },
	{ ua: /bingbot|msnbot|adidxbot|bingpreview/i, domaines: ['.search.msn.com'] },
	{ ua: /applebot/i, domaines: ['.applebot.apple.com'] },
	{ ua: /qwantbot|qwant/i, domaines: ['.qwant.com'] },
	{ ua: /yandex(bot|images|mobilebot)/i, domaines: ['.yandex.ru', '.yandex.net', '.yandex.com'] }
];

type Resolveurs = {
	reverse: (ip: string) => Promise<string[]>;
	lookup: (host: string) => Promise<{ address: string }[]>;
};

export function creerVerificateurRobots({
	resolveurs = {
		reverse: dnsReverse,
		lookup: (host: string) => dnsLookup(host, { all: true })
	},
	maintenant = Date.now
}: { resolveurs?: Resolveurs; maintenant?: () => number } = {}) {
	const cache = new Map<string, { verifie: boolean; expire: number }>();

	async function verifier(ip: string, domaines: string[]): Promise<boolean> {
		try {
			for (const nom of await resolveurs.reverse(ip)) {
				const host = nom.toLowerCase().replace(/\.$/, '');
				if (!domaines.some((d) => host.endsWith(d))) continue;
				const adresses = await resolveurs.lookup(host);
				if (adresses.some((a) => a.address === ip)) return true;
			}
		} catch {
			// No PTR record, a timeout or a resolver error: not verified, the quota applies.
		}
		return false;
	}

	return async function estRobotVerifie(userAgent: string | null, ip: () => string): Promise<boolean> {
		const robot = userAgent ? ROBOTS.find((r) => r.ua.test(userAgent)) : undefined;
		if (!robot) return false;
		const adresse = ip();
		const cle = `${robot.domaines[0]}|${adresse}`;
		const now = maintenant();
		const connu = cache.get(cle);
		if (connu && connu.expire > now) return connu.verifie;
		const verifie = await verifier(adresse, robot.domaines);
		if (cache.size >= MAX_SESSIONS) cache.clear();
		cache.set(cle, { verifie, expire: now + BOT_CACHE_MS });
		return verifie;
	};
}

export interface EvenementFiche {
	cookies: Cookies;
	request: Request;
	getClientAddress: () => string;
}

let compteur: ReturnType<typeof creerCompteurFiches> | undefined;
let estRobotVerifie: ReturnType<typeof creerVerificateurRobots> | undefined;

// The identity and the lists of a fiche are two loaders of one request: they share one admission, so
// that the lists never count again a fiche the identity has just given back (R-16, option B).
const parRequete = new WeakMap<Request, { fiche: string; admission: Promise<Admission> }>();

/** Admits the fiche *fiche* for the session of *event*, creating the session cookie if needed. */
export function admettreFiche(event: EvenementFiche, fiche: string): Promise<Admission> {
	const deja = parRequete.get(event.request);
	if (deja && deja.fiche === fiche) return deja.admission;
	const admission = admettre(event, fiche);
	parRequete.set(event.request, { fiche, admission });
	return admission;
}

async function admettre(event: EvenementFiche, fiche: string): Promise<Admission> {
	if (!compteur) {
		const c = creerCompteurFiches({ limite: quotaHebdomadaire(env.FICHE_QUOTA_WEEKLY) });
		// Without traffic no admission runs: the sweep keeps « gone 7 days after the first fiche »
		// true to the minute. `unref` so the timer never keeps the process alive on its own.
		setInterval(() => c.oublierExpirees(), SWEEP_MS).unref?.();
		compteur = c;
	}
	estRobotVerifie ??= creerVerificateurRobots();
	if (await estRobotVerifie(event.request.headers.get('user-agent'), event.getClientAddress)) {
		return { admise: true };
	}
	let sessionId = event.cookies.get(QUOTA_COOKIE);
	if (!sessionId || !SESSION_ID.test(sessionId)) {
		sessionId = crypto.randomUUID().replaceAll('-', '');
		// Strictly necessary for the service (it carries the free allowance), never read
		// by a third party: exempt from consent under the CNIL guidelines on trackers.
		event.cookies.set(QUOTA_COOKIE, sessionId, {
			// Only the fiche pages read it: never sent to the rest of the site, nor to an API that
			// shares the host (cookies are not scoped by port, e.g. the e2e fixture servers).
			path: '/adresse',
			httpOnly: true,
			sameSite: 'lax',
			maxAge: WEEK_MS / 1000
		});
	}
	return compteur.admettre(sessionId, fiche);
}
