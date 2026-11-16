// Production entry (Dockerfile CMD), in place of adapter-node's `build/index.js`.
//
// adapter-node's handler serves `build/client` and `build/prerendered` with sirv
// BEFORE SvelteKit runs, so `hooks.server.ts` never sees those responses and the
// marketing pages, robots.txt and every asset went out without the S4 headers
// (KI-P3). Setting the canonical list on `res` first gives them to every answer:
// sirv and SvelteKit both write their own headers through `writeHead`, which
// merges with these and wins on a clash, so a value a route sets on purpose stays.
//
// Kept from build/index.js: HOST, PORT, SHUTDOWN_TIMEOUT and the graceful stop on
// SIGTERM/SIGINT. Dropped: SOCKET_PATH, systemd socket activation and
// IDLE_TIMEOUT, which no deployment of this image uses. Every other adapter-node
// variable (ORIGIN, ADDRESS_HEADER, BODY_SIZE_LIMIT...) is read by the handler.
import http from 'node:http';
import process from 'node:process';
import { handler } from './build/handler.js';
import { SECURITY_HEADERS } from './src/lib/utils/security-headers.js';

const host = process.env.HOST ?? '0.0.0.0';
const port = Number(process.env.PORT ?? '3000');
const shutdownTimeout = Number(process.env.SHUTDOWN_TIMEOUT ?? '30');

const server = http.createServer((req, res) => {
	for (const [name, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(name, value);
	// Audit P-4: the self-hosted fonts had no Cache-Control, so the CDN kept them four hours. Their
	// names carry no hash: thirty days, and a new file name whenever a font changes.
	if (req.url?.startsWith('/fonts/')) res.setHeader('Cache-Control', 'public, max-age=2592000');
	handler(req, res, () => {
		res.statusCode = 404;
		res.end('Not found');
	});
});

server.listen(port, host, () => console.log(`Listening on http://${host}:${port}`));

let stopping = false;
function shutdown() {
	if (stopping) return;
	stopping = true;
	server.closeIdleConnections();
	server.close(() => process.exit(0));
	setTimeout(() => server.closeAllConnections(), shutdownTimeout * 1000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
