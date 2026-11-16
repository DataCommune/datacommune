import adapter from '@sveltejs/adapter-node';

// The API the browser calls (same variable and default as src/lib/config.ts): its origin is the one
// third-party source of the page CSP.
const API_ORIGIN = /** @type {`${string}.${string}`} host source of the CSP types */ (
	new URL(process.env.VITE_API_URL || 'http://127.0.0.1:8000').origin
);
// The one inline event handler Svelte writes in server-rendered HTML (`onload` / `onerror` of an
// element with such a listener, replayed at hydration): allowed by its hash alone ('unsafe-hashes'
// applies to the listed hashes only, never to any other handler or inline script).
export const SVELTE_EVENT_HANDLER = 'this.__e=event';
const SVELTE_EVENT_HANDLER_HASH = 'sha256-7dQwUgLau1NFCCGjfn9FsYptB6ZtWxJin6VohGIu20I=';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter(),
		// Page CSP (05-07 lot 5), written by SvelteKit: the hash of each inline script on a prerendered
		// page (in a <meta> tag), a nonce on the others (in the header), so no 'unsafe-inline' for
		// scripts. Styles keep it: Svelte writes style attributes. The proxy sets no CSP of its own on
		// the site (it would replace this one); the fallback of security-headers.js covers the rest.
		csp: {
			mode: 'auto',
			directives: {
				'default-src': ['self'],
				'script-src': ['self', 'unsafe-hashes', SVELTE_EVENT_HANDLER_HASH],
				'style-src': ['self', 'unsafe-inline'],
				'img-src': ['self', 'data:'],
				'font-src': ['self'],
				'connect-src': ['self', API_ORIGIN],
				'object-src': ['none'],
				'frame-ancestors': ['none'],
				'base-uri': ['self'],
				'form-action': ['self']
			}
		}
	},
	vitePlugin: {
		dynamicCompileOptions: ({ filename }) => {
			if (filename.includes('node_modules')) return undefined;
			return { runes: true };
		}
	}
};

export default config;
