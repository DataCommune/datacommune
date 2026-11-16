// Phase 46-05 (T46-05-NET/T46-05-LOG) — fail-closed quality guard fixture.
//
// Attached BEFORE any navigation, for every test that imports `test` from this module
// (auto fixture): any console.error, pageerror, HTTP >=500 response, request to a
// production/BAN host, or an API-shaped request left unmocked by the spec turns into a
// blocking teardown failure. Nothing here ever prints a raw URL query string or a
// credential-shaped value — `redactUrl`/`redactMessage` strip them first (D-11 pattern,
// same redaction discipline as scripts/scan-public-bundle.mjs).
//
// D-12/D-13 (46-05 must_haves): "toute requête API inconnue avorte" + "aucun fallback
// production" — enforced by the single catch-all route below: a spec-registered
// `page.route()` for the SAME pattern always wins (Playwright dispatches routes in
// reverse registration order), so this fixture's catch-all only ever fires for a request
// the spec forgot to mock. `route.fallback()` is never used anywhere in this file or in
// v35-critical.spec.ts (grep-verified in code review, not just by convention).

import { test as base, expect } from '@playwright/test';
import type { ConsoleMessage, Page, Request, Route } from '@playwright/test';

export { expect };

export type ViolationKind =
	| 'console.error'
	| 'pageerror'
	| 'http-5xx'
	| 'unmocked-api'
	| 'forbidden-host';

export interface Violation {
	kind: ViolationKind;
	detail: string;
}

// Production + BAN hosts are NEVER a legitimate destination for this suite: every dataset
// call goes through a mocked `/v1|/auth|/billing` route, and no test navigates the real
// address-search widget. If a request ever reaches one of these hosts (regression, typo
// in a mock pattern, absolute-URL bug), it must abort instead of silently succeeding.
export const FORBIDDEN_HOST_PATTERN = /(^|[./])datacommune\.fr\b|api-adresse\.data\.gouv\.fr/i;

// Our own API surface: every request under these path prefixes MUST be explicitly mocked
// by the spec. Matched independently of scheme/host so this holds regardless of whatever
// VITE_API_URL resolves to locally or in CI.
const API_PATH_PATTERN = /\/(v1|auth|billing)\//;

const MAX_MESSAGE_LEN = 200;

/** Strip the query string and hash from a URL — never let a query leak into a report. */
export function redactUrl(rawUrl: string): string {
	try {
		const u = new URL(rawUrl);
		return `${u.origin}${u.pathname}`;
	} catch {
		return rawUrl.split(/[?#]/)[0];
	}
}

/**
 * Redact anything credential- or query-shaped out of a free-form message (console text,
 * error message) before it can be embedded in a thrown Error. Defense-in-depth: this app
 * never intentionally logs secrets, but a leaked query string or key prefix must never
 * survive into a CI failure log even by accident.
 */
export function redactMessage(raw: string): string {
	let msg = raw
		.replace(/([?&][^\s'"]*=)[^\s'"&]*/g, '$1[redacted]')
		.replace(/dc_(live|anon)_[A-Za-z0-9]+/g, 'dc_$1_[redacted]')
		.replace(/Bearer\s+[A-Za-z0-9._-]+/gi, 'Bearer [redacted]');
	if (msg.length > MAX_MESSAGE_LEN) msg = `${msg.slice(0, MAX_MESSAGE_LEN)}…`;
	return msg;
}

export function isForbiddenHost(url: string): boolean {
	return FORBIDDEN_HOST_PATTERN.test(url);
}

export function isApiPath(url: string): boolean {
	try {
		return API_PATH_PATTERN.test(new URL(url).pathname);
	} catch {
		return API_PATH_PATTERN.test(url);
	}
}

/** Pure classifier — used both by the live fixture and by qa-gates.test.mjs (no browser). */
export function classifyRequest(url: string): ViolationKind | null {
	if (isForbiddenHost(url)) return 'forbidden-host';
	if (isApiPath(url)) return 'unmocked-api';
	return null;
}

function formatViolations(violations: Violation[]): string {
	return violations.map((v) => `  - [${v.kind}] ${v.detail}`).join('\n');
}

export { formatViolations };

// ---------------------------------------------------------------------------
// 04.8-01 (amendment 8, UI-SPEC § 2.2 and § 2.6; D-03, D-03b, D-35) — rendered checks of the v4
// type contract. Each returns its findings (text excerpts cut at 40 characters), never throws:
// the caller asserts `toEqual([])`. A finding is fixed in the component, never by relaxing these
// thresholds.
// ---------------------------------------------------------------------------

/** Floor of the public type scale, in CSS px (D-03). */
export const PLANCHER_TYPO_PX = 14;

/** Locked weights per family (D-03b); any other family keeps the three contract weights. */
export const GRAISSES_PAR_FAMILLE: Readonly<Record<string, readonly number[]>> = {
	Inter: [400, 600],
	Newsreader: [400, 500]
};
export const GRAISSES_CONTRAT: readonly number[] = [400, 500, 600];

const LONGUEUR_EXTRAIT = 40;

/**
 * Every visible text under `portee` whose computed font size is below 14 px: text nodes, the value
 * of a field (or its placeholder while the field is empty) and the text of a `::before` or `::after`.
 * A node is visible when its parent passes `checkVisibility` (display, visibility, content-visibility
 * of a closed <details>) and its own boxes are not empty; a field or a pseudo-element, when its
 * element does.
 */
export function verifierPlancherTypo(page: Page, portee: string): Promise<string[]> {
	return page.evaluate(
		({ portee, plancher, longueur }) => {
			const infractions: string[] = [];
			const normaliser = (texte: string) => texte.replace(/\s+/g, ' ').trim();
			const lire = (style: CSSStyleDeclaration, texte: string) => {
				const taille = Number.parseFloat(style.fontSize);
				if (taille < plancher) infractions.push(`${taille}px « ${texte.slice(0, longueur)} »`);
			};
			const visible = (el: Element) =>
				el.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true }) &&
				[...el.getClientRects()].some((r) => r.width > 0 && r.height > 0);
			// Input types that render no text of their own.
			const SANS_TEXTE = new Set(['hidden', 'checkbox', 'radio', 'range', 'color', 'file', 'image']);
			// Text carried by `content`: a non-blank string, a counter or an attribute (not an image).
			const texteDeContenu = (contenu: string) => {
				if (contenu === 'none' || contenu === 'normal') return '';
				const chaines = [...contenu.replace(/url\([^)]*\)/g, '').matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]);
				const texte = normaliser(chaines.join(''));
				if (texte) return texte;
				return /\b(?:counters?|attr)\(/.test(contenu) ? contenu : '';
			};
			for (const racine of document.querySelectorAll(portee)) {
				const marcheur = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT);
				for (let noeud = marcheur.nextNode(); noeud; noeud = marcheur.nextNode()) {
					const texte = normaliser(noeud.textContent ?? '');
					const parent = noeud.parentElement;
					if (!texte || !parent) continue;
					// The text of a textarea is its value, read below.
					if (parent instanceof HTMLTextAreaElement) continue;
					if (!parent.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) continue;
					const plage = document.createRange();
					plage.selectNodeContents(noeud);
					if (![...plage.getClientRects()].some((r) => r.width > 0 && r.height > 0)) continue;
					lire(getComputedStyle(parent), texte);
				}
				for (const champ of racine.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea')) {
					if (champ instanceof HTMLInputElement && SANS_TEXTE.has(champ.type)) continue;
					if (!visible(champ)) continue;
					const valeur = normaliser(champ.value);
					if (valeur) lire(getComputedStyle(champ), valeur);
					else if (normaliser(champ.placeholder)) lire(getComputedStyle(champ, '::placeholder'), normaliser(champ.placeholder));
				}
				for (const element of [racine, ...racine.querySelectorAll('*')]) {
					for (const pseudo of ['::before', '::after']) {
						const style = getComputedStyle(element, pseudo);
						const texte = texteDeContenu(style.content);
						if (!texte || style.display === 'none' || !visible(element)) continue;
						lire(style, `${pseudo} ${texte}`);
					}
				}
			}
			return infractions;
		},
		{ portee, plancher: PLANCHER_TYPO_PX, longueur: LONGUEUR_EXTRAIT }
	);
}

/**
 * Every visible text-bearing element under `portee` whose computed weight is outside its family's
 * locked weights. The family is the first name of the computed `font-family`.
 */
export function verifierGraisses(page: Page, portee: string): Promise<string[]> {
	return page.evaluate(
		({ portee, parFamille, contrat, longueur }) => {
			const infractions: string[] = [];
			const vus = new Set<Element>();
			for (const racine of document.querySelectorAll(portee)) {
				const marcheur = document.createTreeWalker(racine, NodeFilter.SHOW_TEXT);
				for (let noeud = marcheur.nextNode(); noeud; noeud = marcheur.nextNode()) {
					const parent = noeud.parentElement;
					if (!parent || vus.has(parent) || !(noeud.textContent ?? '').trim()) continue;
					vus.add(parent);
					if (!parent.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) continue;
					const style = getComputedStyle(parent);
					const famille = style.fontFamily.split(',')[0].replace(/["']/g, '').trim();
					const poids = Number(style.fontWeight);
					const admis = parFamille[famille] ?? contrat;
					if (admis.includes(poids)) continue;
					const texte = (parent.textContent ?? '').replace(/\s+/g, ' ').trim();
					infractions.push(`${famille} ${poids} « ${texte.slice(0, longueur)} »`);
				}
			}
			return infractions;
		},
		{ portee, parFamille: GRAISSES_PAR_FAMILLE, contrat: GRAISSES_CONTRAT, longueur: LONGUEUR_EXTRAIT }
	);
}

/**
 * Every visible element matching `selecteurs` under `portee` whose rendered content width
 * (border box minus padding and borders) exceeds 60ch in its own font — the measured substitute
 * of the 75-character rule (UI-SPEC § 2.2). Waits for `document.fonts.ready` first: before the
 * web fonts load, `ch` is the width of a fallback face.
 */
export async function verifierMesure(page: Page, portee: string, selecteurs: string[]): Promise<string[]> {
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
	return page.evaluate(
		({ portee, selecteurs, longueur }) => {
			const infractions: string[] = [];
			for (const racine of document.querySelectorAll(portee)) {
				for (const element of racine.querySelectorAll(selecteurs.join(','))) {
					if (!element.checkVisibility({ checkOpacity: false, checkVisibilityCSS: true })) continue;
					const boite = element.getBoundingClientRect();
					if (boite.width === 0) continue;
					const style = getComputedStyle(element);
					const largeur =
						boite.width -
						Number.parseFloat(style.paddingLeft) -
						Number.parseFloat(style.paddingRight) -
						Number.parseFloat(style.borderLeftWidth) -
						Number.parseFloat(style.borderRightWidth);
					// The probe inherits the element's font, so 60ch is measured in that font.
					const sonde = document.createElement('span');
					sonde.setAttribute('aria-hidden', 'true');
					sonde.style.cssText =
						'position:absolute;display:block;visibility:hidden;width:60ch;height:0;margin:0;padding:0;border:0';
					element.append(sonde);
					const mesure = sonde.getBoundingClientRect().width;
					sonde.remove();
					if (largeur > mesure + 0.5) {
						const texte = (element.textContent ?? '').replace(/\s+/g, ' ').trim();
						infractions.push(
							`${Math.round(largeur)}px > 60ch (${Math.round(mesure)}px) « ${texte.slice(0, longueur)} »`
						);
					}
				}
			}
			return infractions;
		},
		{ portee, selecteurs, longueur: LONGUEUR_EXTRAIT }
	);
}

interface QualityFixtures {
	qualityGuard: void;
}

export const test = base.extend<QualityFixtures>({
	qualityGuard: [
		async ({ page }: { page: Page }, use: () => Promise<void>) => {
			const violations: Violation[] = [];

			const onConsole = (msg: ConsoleMessage) => {
				if (msg.type() === 'error') {
					violations.push({ kind: 'console.error', detail: redactMessage(msg.text()) });
				}
			};
			const onPageError = (err: Error) => {
				violations.push({ kind: 'pageerror', detail: redactMessage(err.message) });
			};
			const onResponse = (res: { status(): number; url(): string }) => {
				if (res.status() >= 500) {
					violations.push({ kind: 'http-5xx', detail: `${redactUrl(res.url())} -> ${res.status()}` });
				}
			};

			page.on('console', onConsole);
			page.on('pageerror', onPageError);
			page.on('response', onResponse);

			// Attached BEFORE navigation (fixture setup always runs before the test body).
			// Any spec-registered `page.route()` for a matching pattern is registered AFTER
			// this one and therefore wins (last-registered-first-checked) — this only ever
			// fires for a request nobody mocked.
			await page.route('**/*', async (route: Route) => {
				const req: Request = route.request();
				const url = req.url();
				const kind = classifyRequest(url);
				if (kind) {
					violations.push({ kind, detail: redactUrl(url) });
					await route.abort('accessdenied');
					return;
				}
				await route.continue();
			});

			await use();

			page.off('console', onConsole);
			page.off('pageerror', onPageError);
			page.off('response', onResponse);

			if (violations.length > 0) {
				throw new Error(
					`[quality-guard] ${violations.length} blocking runtime violation(s):\n${formatViolations(violations)}`
				);
			}
		},
		{ auto: true }
	]
});
