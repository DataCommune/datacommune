import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { registreAllegePlugin } from './src/lib/data/registre-client';
import { defineConfig } from 'vitest/config';
import type { Plugin } from 'vite';
import type { IncomingMessage } from 'node:http';
import { readFileSync } from 'node:fs';

// Target of the dev relay below. Default: the local Docker API. The owner acceptance on real data
// (plan 03-13) points it at the work base API opened by `workbase-recette.sh up` (private hypervisor script).
const apiProxyTarget = process.env.DEV_API_PROXY_TARGET || 'http://127.0.0.1:8000';

// Dev server only (`configureServer` never runs in a build): the audience counting endpoint, which
// Caddy routes to GoatCounter in the public stack (04.7-14b), answers 204 here, so a dev or e2e run
// with VITE_MESURE_AUDIENCE=true counts against nothing and never shows a 404.
const pointDeCollecteDeDev: Plugin = {
	name: 'dc-point-de-collecte-dev',
	configureServer(server) {
		server.middlewares.use('/count', (_req, res) => {
			res.statusCode = 204;
			res.end();
		});
	}
};

// `import x from './fichier.ttf?hex'`: the bytes of a file as a lowercase hex string, bundled in the server
// code. The renderer of the link previews (`$lib/server/og/rendu.ts`) takes its WebAssembly module and its
// fonts this way, the runtime image having no node_modules to read them from. Hex, not base64: base64 of
// binary bytes can spell a credential pattern of the bundle scan (`scripts/scan-public-bundle.mjs`, a
// false « AKIA… » on 2026-11-14), and that gate must stay as strict as it is; hex digits match none.
const fichiersEnHex = new Map<string, string>();
const binaireEnHex: Plugin = {
	name: 'dc-binaire-hex',
	enforce: 'pre',
	// A virtual id (`\0`) that does not carry the path: Vitest loads natively any id that names node_modules.
	async resolveId(source, importer) {
		if (!source.endsWith('?hex')) return null;
		const resolu = await this.resolve(source.slice(0, -'?hex'.length), importer, { skipSelf: true });
		if (!resolu) return null;
		const id = `\0dc-hex:${resolu.id.split(/[\\/]/).pop()}`;
		if ((fichiersEnHex.get(id) ?? resolu.id) !== resolu.id) this.error(`?hex: two files named ${id.slice(8)}`);
		fichiersEnHex.set(id, resolu.id);
		return id;
	},
	load(id) {
		const fichier = fichiersEnHex.get(id);
		if (!fichier) return null;
		this.addWatchFile(fichier);
		return `export default ${JSON.stringify(readFileSync(fichier).toString('hex'))};`;
	}
};

export default defineConfig({
	// svelteTesting() only activates under VITEST: resolves the browser Svelte
	// build (so @testing-library/svelte can mount components in jsdom) and adds
	// per-test DOM auto-cleanup. No-op for the production build.
	plugins: [pointDeCollecteDeDev, registreAllegePlugin, binaireEnHex, tailwindcss(), sveltekit(), svelteTesting()],

	// The public API is currently offline; local development targets Docker.
	server: {
		proxy: {
			'/v1': {
				target: apiProxyTarget,
				changeOrigin: true,
				secure: false,
				cookieDomainRewrite: { '.datacommune.fr': '' },
				cookiePathRewrite: { '*': '/' },
				configure: (proxy) => {
					proxy.on('proxyRes', (proxyRes: IncomingMessage) => {
						const sc = proxyRes.headers['set-cookie'];
						if (sc) {
							proxyRes.headers['set-cookie'] = sc.map((c: string) =>
								c.replace(/;\s*Secure/gi, '').replace(/;\s*SameSite=\w+/gi, '; SameSite=Lax')
							);
						}
					});
				}
			},
			'/auth': {
				target: apiProxyTarget,
				changeOrigin: true,
				secure: false,
				cookieDomainRewrite: { '.datacommune.fr': '' },
				cookiePathRewrite: { '*': '/' },
				configure: (proxy) => {
					proxy.on('proxyRes', (proxyRes: IncomingMessage) => {
						const sc = proxyRes.headers['set-cookie'];
						if (sc) {
							proxyRes.headers['set-cookie'] = sc.map((c: string) =>
								c.replace(/;\s*Secure/gi, '').replace(/;\s*SameSite=\w+/gi, '; SameSite=Lax')
							);
						}
					});
				}
			},
			'/billing': {
				target: apiProxyTarget,
				changeOrigin: true,
				secure: false,
				cookieDomainRewrite: { '.datacommune.fr': '' },
				cookiePathRewrite: { '*': '/' },
				configure: (proxy) => {
					proxy.on('proxyRes', (proxyRes: IncomingMessage) => {
						const sc = proxyRes.headers['set-cookie'];
						if (sc) {
							proxyRes.headers['set-cookie'] = sc.map((c: string) =>
								c.replace(/;\s*Secure/gi, '').replace(/;\s*SameSite=\w+/gi, '; SameSite=Lax')
							);
						}
					});
				}
			},
			// Header map of the address fiche (04.7-17): Caddy relays `/carte/*` to `/v1/carte/*` on the
			// site's own origin; the dev server does the same.
			'/carte': {
				target: apiProxyTarget,
				changeOrigin: true,
				secure: false,
				rewrite: (chemin) => `/v1${chemin}`
			}
		}
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['src/tests/setup.ts']
	}
});
