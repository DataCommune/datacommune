// The semantic-contract registry as the built site ships it (optimisation of 2026-11-13).
//
// `formatters.ts` imports `fiche-fields.v1.json` whole, so the bundle of the address and commune
// fiches carried every `description`: contract-grade texts about the SQL and ETL semantics, read by
// the backend and the tests, never by the site (the reader-facing texts live in `explications.ts`).
// They were 30 KB of the 83 KB of the registry, 7.4 KB of its 10.1 KB once compressed (brotli).
// The build drops them through `registreAllegePlugin`, listed in `vite.config.ts`; the source file
// stays byte-identical to `contracts/fiche-fields.v1.json`, and the dev server and vitest read it
// whole.
import type { Plugin } from 'vite';

/** The registry without its descriptions; every other key and every field kept, in order. */
export function registreAllege(registre: Record<string, unknown>): Record<string, unknown> {
	const { description: _registre, fields, ...reste } = registre;
	return {
		...reste,
		fields: (fields as Record<string, unknown>[]).map(({ description: _champ, ...champ }) => champ)
	};
}

/** Build only, before the JSON plugin of Vite: the registry module is built from the light copy. */
export const registreAllegePlugin: Plugin = {
	name: 'dc-registre-allege',
	apply: 'build',
	enforce: 'pre',
	transform(code, id) {
		if (!id.split('?')[0].endsWith('/fiche-fields.v1.json')) return null;
		return { code: JSON.stringify(registreAllege(JSON.parse(code))), map: null };
	}
};
