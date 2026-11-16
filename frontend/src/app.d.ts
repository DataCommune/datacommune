// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
declare global {
	namespace App {
		// `@sveltejs/adapter-node` does not populate `event.platform`; every read
		// site already guards with `platform?.`. The shape stays declared so the
		// preproduction Cloudflare Access path keeps a real type instead of `any`
		// if a future host supplies one (hosting target decided in phase 5).
		interface Platform {
			env?: {
				CF_ACCESS_CLIENT_ID?: string;
				CF_ACCESS_CLIENT_SECRET?: string;
			};
		}

		/**
		 * `departements`: the served coverage of a 404 « DPE inconnu », the only extra member a page
		 * error carries (`/dpe/[numero]`, 2026-10-05); public codes, never the input.
		 */
		interface Error {
			message: string;
			departements?: string[];
		}
		// interface Locals {}
		// interface PageData {}
		/**
		 * `carteParcelles`: the entry of history the parcel map of the address fiche adds at its opening
		 * (shallow routing, URL unchanged), so that the back button closes the map (04.10-09, C8).
		 */
		interface PageState {
			carteParcelles?: true;
		}
	}
}

export {};
