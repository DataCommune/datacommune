import { describe, expect, it } from 'vitest';
import { ACCOUNTS_DISABLED_PATH, rerouteAccounts } from './hooks';

const ACCOUNT_PATHS = [
	'/login',
	'/register',
	'/forgot-password',
	'/reset-password',
	'/verify',
	'/settings',
	'/billing',
	'/mon-espace',
	'/admin',
	'/admin/users',
	'/billing/'
];
const PUBLIC_PATHS = ['/', '/couverture', '/adresse/abc', '/commune/44109', '/developpeurs', '/administration', '/loginx'];

describe('rerouteAccounts', () => {
	it('sends every account route to a path no route matches when accounts are disabled', () => {
		for (const path of ACCOUNT_PATHS) expect(rerouteAccounts(path, false), path).toBe(ACCOUNTS_DISABLED_PATH);
	});

	it('leaves the public routes untouched when accounts are disabled', () => {
		for (const path of PUBLIC_PATHS) expect(rerouteAccounts(path, false), path).toBeUndefined();
	});

	it('leaves every route untouched when accounts are enabled', () => {
		for (const path of [...ACCOUNT_PATHS, ...PUBLIC_PATHS]) expect(rerouteAccounts(path, true), path).toBeUndefined();
	});

	// Ianis, 2026-11-12: accounts with an API key, without any paid plan (D-133 amended).
	it('closes only the billing route when accounts are on and billing is off', () => {
		for (const path of ['/billing', '/billing/']) expect(rerouteAccounts(path, true, false), path).toBe(ACCOUNTS_DISABLED_PATH);
		for (const path of [...ACCOUNT_PATHS.filter((p) => !p.startsWith('/billing')), ...PUBLIC_PATHS])
			expect(rerouteAccounts(path, true, false), path).toBeUndefined();
	});
});
