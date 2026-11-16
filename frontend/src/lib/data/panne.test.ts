import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import {
	MESSAGE_ACCES_REFUSE,
	MESSAGE_QUOTA_CLE_PARTAGEE,
	MESSAGE_QUOTA_OFFRE,
	TITRE_ACCES_REFUSE,
	TITRE_DEMANDE_REFUSEE,
	TITRE_LIMITE,
	TITRE_QUOTA,
	detailPanne,
	messageDemandeRefusee,
	messageLimite
} from './panne';

// WR-01, 03-17 B C1/C2: every 4xx is titled and worded by the client; 5xx and network failures stay
// a generic outage, and no text of the server body ever reaches the notice.
//
// 03-17 D-15 G1: a 429 is worded after its real cause on the server. The problem titles below are
// the ones of api/backend (rate_limit.py « Trop de requetes », auth/dependencies.py « Limite de
// debit atteinte » and « Quota mensuel epuise », auth/router.py « Trop de demandes »).
const refus429 = (titre: string, contexte: { retryAfter?: number | null; anonyme?: boolean }) =>
	new ApiError(429, 'texte serveur', Boolean(contexte.anonyme && contexte.retryAfter != null), titre, contexte);

describe('detailPanne — 429 after its cause (03-17 G1)', () => {
	it('the per-IP ceiling (Retry-After) of an anonymous visitor: a request limit with the served delay', () => {
		expect(detailPanne(refus429('Trop de requetes', { retryAfter: 60, anonyme: true }))).toEqual({
			statut: 429,
			titre: TITRE_LIMITE,
			message: 'La limite de requêtes est atteinte. Réessayez dans 1\u00a0minute.'
		});
	});

	it('the per-IP ceiling also hits a signed-in visitor: a request limit, never a quota of the offer', () => {
		const detail = detailPanne(refus429('Trop de requetes', { retryAfter: 30, anonyme: false }));
		expect(detail).toEqual({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(30) });
		expect(detail.message).toContain('dans 30\u00a0secondes');
		expect(detail.message).not.toMatch(/quota|offre/);
	});

	it.each([
		['anonymous (shared public key)', true],
		['signed in', false]
	])('the per-minute limit of the key, without Retry-After, %s: a request limit, no quota', (_cas, anonyme) => {
		const detail = detailPanne(refus429('Limite de debit atteinte', { anonyme }));
		expect(detail).toEqual({ statut: 429, titre: TITRE_LIMITE, message: messageLimite(null) });
		expect(detail.message).toBe('La limite de requêtes est atteinte. Réessayez dans quelques instants.');
		expect(detail.message).not.toMatch(/quota|offre/);
	});

	it('the monthly quota named by the server, signed in: the quota of the offer', () => {
		expect(detailPanne(refus429('Quota mensuel epuise', { anonyme: false }))).toEqual({
			statut: 429,
			titre: TITRE_QUOTA,
			message: MESSAGE_QUOTA_OFFRE
		});
	});

	it('the monthly quota of the shared public key (anonymous, accented title): never « votre offre »', () => {
		const detail = detailPanne(refus429('Quota mensuel épuisé', { anonyme: true }));
		expect(detail).toEqual({ statut: 429, titre: TITRE_QUOTA, message: MESSAGE_QUOTA_CLE_PARTAGEE });
		expect(detail.message).not.toContain('votre offre');
	});

	it('another 429 (account route ceiling, no title): a request limit', () => {
		expect(detailPanne(refus429('Trop de demandes', {})).message).toBe(messageLimite(null));
		expect(detailPanne(new ApiError(429, 'texte serveur')).message).toBe(messageLimite(null));
	});

	it.each([
		[1, 'dans 1\u00a0seconde'],
		[59, 'dans 59\u00a0secondes'],
		[61, 'dans 2\u00a0minutes'],
		[0, 'dans quelques instants']
	])('Retry-After %i is worded « %s »', (secondes, attendu) => {
		expect(messageLimite(secondes)).toBe(`La limite de requêtes est atteinte. Réessayez ${attendu}.`);
	});
});

describe('detailPanne', () => {

	it.each([401, 403])('HTTP %i is a refused access, worded by the client', (status) => {
		expect(detailPanne(new ApiError(status, 'texte serveur'))).toEqual({
			statut: status,
			titre: TITRE_ACCES_REFUSE,
			message: MESSAGE_ACCES_REFUSE
		});
	});

	it.each([400, 404, 409, 422])('HTTP %i is a refused request carrying its status', (status) => {
		const detail = detailPanne(new ApiError(status, 'texte serveur'));
		expect(detail).toEqual({ statut: status, titre: TITRE_DEMANDE_REFUSEE, message: messageDemandeRefusee(status) });
		expect(detail.message).toContain(`erreur ${status}`);
	});

	it.each([500, 502, 503])('HTTP %i stays a generic outage: no title, no message', (status) => {
		expect(detailPanne(new ApiError(status, 'texte serveur'))).toEqual({ statut: status, titre: null, message: null });
	});

	it('a failure before any response has no status', () => {
		expect(detailPanne(new TypeError('réseau'))).toEqual({ statut: null, titre: null, message: null });
	});

	it('counter-proof: the server text of a 409 is never rendered', () => {
		const detail = detailPanne(new ApiError(409, 'texte serveur'));
		expect(`${detail.titre} ${detail.message}`).not.toContain('texte serveur');
	});

	it('the typed wording never uses the outage vocabulary', () => {
		for (const texte of [TITRE_LIMITE, messageLimite(60), messageLimite(null), TITRE_QUOTA, MESSAGE_QUOTA_OFFRE, MESSAGE_QUOTA_CLE_PARTAGEE, TITRE_ACCES_REFUSE, MESSAGE_ACCES_REFUSE, TITRE_DEMANDE_REFUSEE, messageDemandeRefusee(409)]) {
			expect(texte).not.toMatch(/indisponible|n'a pas répondu/);
		}
	});
});

// UI-025 of the 2026-10-24 UI review: the public site has no accounts, so a refused access never asks
// the visitor to reconnect. `ACCOUNTS_ENABLED` is a build-time constant: the module is reloaded without it.
describe('detailPanne — site public sans comptes', () => {
	it('words a 401/403 without any reconnection', async () => {
		vi.resetModules();
		vi.doMock('$lib/config', async (importOriginal) => ({
			...(await importOriginal<typeof import('$lib/config')>()),
			ACCOUNTS_ENABLED: false
		}));
		try {
			const { detailPanne: detail, MESSAGE_ACCES_REFUSE: message } = await import('./panne');
			const { ApiError: ErreurApi } = await import('$lib/api/client');
			expect(message).toBe("L'accès aux données a été refusé. Rechargez la page dans quelques minutes.");
			for (const status of [401, 403]) {
				expect(detail(new ErreurApi(status, 'texte serveur')).message).toBe(message);
			}
			expect(message).not.toMatch(/connect/i);
		} finally {
			vi.doUnmock('$lib/config');
			vi.resetModules();
		}
	});
});
