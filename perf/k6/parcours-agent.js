// Load journey of an estate agent on the DataCommune API (Grafana k6 >= 2.3, single binary on the PC).
//
// One iteration = one agent looking an address up, as the served frontend (c64ab822) does it:
//   1. search        GET /v1/adresses/recherche?q=<number street commune>
//   2. think time    THINK_S seconds (the agent picks a result)
//   3. address fiche GET /v1/adresse/{ban_id}                      (layout loader of /adresse/[id])
//   4. in parallel, like the page loader's Promise.all:
//        commune     GET /v1/commune?code_insee=<code>             (commune fiche)
//        DVF         GET /v1/adresse/{ban_id}/ventes               (sales of the parcel)
//                    GET /v1/adresse/{ban_id}/ventes-declarees     (sales declared at the address)
//        DPE         GET /v1/adresse/{ban_id}/dpe                  (documents of the address)
// 6 requests per iteration: a rate in iterations/s ("parcours/s") is 6x that many requests/s.
//
// The address fiche calls two public upstreams live, without cache (Géorisques for the communal
// risks, IGN GPU for the planning at the point): about 5 outbound calls per fiche, and a floor of
// about 2 s with no load. FICHE=fond (rampe only) takes it out of the ramped journey (5 requests
// per parcours, all served from the base) and requests it in a separate constant-rate scenario
// (fiche_fond: a search then a fiche, FICHE_PAR_S per second, at most MAX_FICHE_PAR_S), so the
// upstreams see a fixed, small load whatever the plateau; its samples stay out of the p95 verdict.
//
// Profiles (PROFILE):
//   smoke     1 VU, 3 iterations, bodies read and their envelope checked. Exit 0 = smoke green.
//   rampe     ramping-arrival-rate, plateaus PALIERS (parcours/s), up to the break. Loopback only.
//   controle  constant 0.25 parcours/s for CONTROLE_S seconds, light enough for one real IP
//             (90 requests/min under the 120/min per-IP ceiling), meant for https://api.datacommune.fr.
//
// Environment (nothing secret is ever written in this file, printed or logged):
//   BASE_URL     required. http://127.0.0.1:18080 (SSH tunnel to the Caddy loopback of the public container)
//                or https://api.datacommune.fr.
//   HOST_HEADER  Host sent on the loopback path (api.datacommune.fr), so Caddy picks the API vhost.
//   DC_API_KEY   required. The public dc_anon_ key, read at run time from the CT's .env into the
//                shell environment. Sent in X-API-Key only.
//   SIM_IP=1     loopback only: each iteration sends CF-Connecting-IP from a pool of NB_PROS
//                simulated visitors in 198.18.0.0/16 (RFC 2544 benchmarking range). The API counts
//                its per-IP allowances on that header behind Caddy, so every simulated agent has its
//                own 120 req/min, like a real one; nothing is changed on the server.
//   NB_PROS (2000), PALIERS ("1,2,4,6,8,12,16,24,32"), PALIER_S (120), RAMPE_S (30),
//   MAX_VUS (300), THINK_S (1), P95_MS (1000), ABORT_MS (5000), ABORT_FENETRE (30),
//   ABORT_MIN (12), ABORT_TAUX (0.3), CONTROLE_S (120), COMMUNES (comma list overriding the
//   default 44/85 list), FICHE (parcours | fond), FICHE_PAR_S (1).
//
// Stop criterion inside the test: each VU keeps its last ABORT_FENETRE requests; once it holds
// at least ABORT_MIN of them (an arrival-rate executor spreads iterations over its whole VU
// pool, so a VU may see only a few) and ABORT_TAUX of them are refused (5xx, 429, network error)
// or slower than ABORT_MS, the whole test is aborted (exec.test.abort). Backstop:
// http_req_failed above 20 % aborts after 30 s. The watchdog (surveille.sh) stops it too.
// The SLO thresholds (p95 < P95_MS per route, failures < 1 %, checks > 99 %) are a verdict: a
// rampe that goes past capacity is expected to cross them (exit code 99), a smoke is not.

import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter } from 'k6/metrics';

const env = (name, fallback) => (__ENV[name] === undefined || __ENV[name] === '' ? fallback : __ENV[name]);
const num = (name, fallback) => {
	const value = Number(env(name, fallback));
	if (!Number.isFinite(value)) throw new Error(`${name} is not a number`);
	return value;
};

const PROFILE = env('PROFILE', 'smoke');
const BASE_URL = env('BASE_URL', '').replace(/\/+$/, '');
const HOST_HEADER = env('HOST_HEADER', '');
const API_KEY = env('DC_API_KEY', '');
const SIM_IP = env('SIM_IP', '0') === '1';
const NB_PROS = num('NB_PROS', 2000);
const PALIERS = env('PALIERS', '1,2,4,6,8,12,16,24,32').split(',').map(Number);
const PALIER_S = num('PALIER_S', 120);
const RAMPE_S = num('RAMPE_S', 30);
const MAX_VUS = num('MAX_VUS', 300);
const THINK_S = num('THINK_S', 1);
const P95_MS = num('P95_MS', 1000);
const ABORT_MS = num('ABORT_MS', 5000);
const ABORT_FENETRE = num('ABORT_FENETRE', 30);
const ABORT_MIN = num('ABORT_MIN', 12);
const ABORT_TAUX = num('ABORT_TAUX', 0.3);
const CONTROLE_S = num('CONTROLE_S', 120);
const CONTROLE_RATE_PER_MIN = 15; // 0.25 parcours/s = 90 requests/min from one real IP
const FICHE = env('FICHE', 'parcours');
const FICHE_PAR_S = num('FICHE_PAR_S', 1);

// Hard ceilings of this script, whatever the environment says (PLAN-TIR.md, "Débit max").
const MAX_PALIER = 32;
const MAX_FICHE_PAR_S = 2; // about 10 outbound calls/s to Géorisques and IGN, not more
const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/;

if (!BASE_URL) throw new Error('BASE_URL is required');
if (!API_KEY) throw new Error('DC_API_KEY is required (public dc_anon_ key, from the environment only)');
if (!['smoke', 'rampe', 'controle'].includes(PROFILE)) throw new Error(`unknown PROFILE ${PROFILE}`);
if (SIM_IP && !LOOPBACK.test(BASE_URL)) throw new Error('SIM_IP=1 is only allowed on a loopback BASE_URL');
if (PROFILE === 'rampe' && !LOOPBACK.test(BASE_URL)) throw new Error('rampe is only allowed on a loopback BASE_URL');
if (PROFILE === 'rampe' && PALIERS.some((p) => !(p > 0) || p > MAX_PALIER)) {
	throw new Error(`PALIERS must be in ]0, ${MAX_PALIER}] parcours/s`);
}
if (NB_PROS < 1 || NB_PROS > 64000) throw new Error('NB_PROS must be in [1, 64000]');
if (!['parcours', 'fond'].includes(FICHE)) throw new Error(`unknown FICHE ${FICHE}`);
if (FICHE === 'fond' && PROFILE !== 'rampe') throw new Error('FICHE=fond is a rampe option');
if (FICHE === 'fond' && !(FICHE_PAR_S > 0 && FICHE_PAR_S <= MAX_FICHE_PAR_S)) {
	throw new Error(`FICHE_PAR_S must be in ]0, ${MAX_FICHE_PAR_S}] fiches/s`);
}
const FOND = FICHE === 'fond';

// Generic street names found in most communes of the served departements, and communes of
// Loire-Atlantique (44) and Vendée (85). Public place names, no golden case, no private address.
const VOIES = [
	'rue de la mairie', "rue de l'eglise", "place de l'eglise", 'rue du stade', 'rue des ecoles',
	'avenue de la gare', 'rue nationale', 'grande rue', 'rue pasteur', 'rue victor hugo',
	'rue jean jaures', 'rue du general de gaulle', 'rue du moulin', 'rue de la poste', 'rue des lilas'
];
const COMMUNES = env(
	'COMMUNES',
	'nantes,saint-nazaire,reze,saint-herblain,orvault,vertou,carquefou,clisson,guerande,pornic,' +
		'chateaubriant,ancenis,la roche-sur-yon,challans,les sables-d olonne,fontenay-le-comte,lucon,' +
		'les herbiers,montaigu-vendee,saint-gilles-croix-de-vie'
).split(',');

function rampeStages() {
	const stages = [];
	for (const target of PALIERS) {
		stages.push({ target, duration: `${RAMPE_S}s` });
		stages.push({ target, duration: `${PALIER_S}s` });
	}
	return stages;
}

const scenarios = {
	smoke: { smoke: { executor: 'per-vu-iterations', vus: 1, iterations: 3, maxDuration: '2m' } },
	rampe: {
		rampe: {
			executor: 'ramping-arrival-rate',
			startRate: 0,
			timeUnit: '1s',
			preAllocatedVUs: Math.min(20, MAX_VUS),
			maxVUs: MAX_VUS,
			stages: rampeStages()
		},
		...(FOND && {
			fiche_fond: {
				executor: 'constant-arrival-rate',
				exec: 'ficheFond',
				rate: FICHE_PAR_S,
				timeUnit: '1s',
				duration: `${PALIERS.length * (RAMPE_S + PALIER_S)}s`,
				preAllocatedVUs: 5,
				maxVUs: 20
			}
		})
	},
	controle: {
		controle: {
			executor: 'constant-arrival-rate',
			rate: CONTROLE_RATE_PER_MIN,
			timeUnit: '1m',
			duration: `${CONTROLE_S}s`,
			preAllocatedVUs: 5,
			maxVUs: 10
		}
	}
}[PROFILE];

const ROUTES = ['recherche', 'fiche_adresse', 'commune', 'dvf_ventes', 'dvf_ventes_declarees', 'dpe_adresse'];
const thresholds = {
	// Verdict (SLO). Per route, so the summary prints each route's p95. With FICHE=fond, the
	// background fiches are out of it: their latency is the upstreams', reported apart.
	[FOND ? 'http_req_duration{scenario:rampe}' : 'http_req_duration']: [`p(95)<${P95_MS}`],
	checks: ['rate>0.99'],
	// Verdict and backstop abort.
	http_req_failed: ['rate<0.01', { threshold: 'rate<0.2', abortOnFail: true, delayAbortEval: '30s' }]
};
for (const route of ROUTES) {
	if (FOND && route === 'fiche_adresse') continue;
	thresholds[`http_req_duration{route:${route}}`] = [`p(95)<${P95_MS}`];
}

export const options = {
	scenarios,
	thresholds,
	// No url tag: the URLs carry BAN keys of real addresses, kept out of every output file.
	systemTags: ['status', 'method', 'name', 'scenario', 'expected_response', 'error_code'],
	summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
	userAgent: 'datacommune-perf-k6/1 (+perf/k6)',
	setupTimeout: '60s'
};

// 404 (unknown key) and 409 (ambiguous address) are served answers, not failures.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 404, 409));

// Refusals seen by the stop criterion (5xx, 429, network error), by route.
export const refus = new Counter('refus');

// Plateau of the rampe the iteration started in (its target, in parcours/s), or 'montee' during
// a ramp: the analysis groups the samples by this tag instead of guessing it from timestamps.
function currentPalier() {
	if (PROFILE !== 'rampe') return PROFILE;
	let t = (Date.now() - exec.scenario.startTime) / 1000;
	for (const target of PALIERS) {
		if (t < RAMPE_S) return 'montee';
		t -= RAMPE_S;
		if (t < PALIER_S) return String(target);
		t -= PALIER_S;
	}
	return 'fin';
}

function simulatedIp() {
	const n = exec.scenario.iterationInTest % NB_PROS;
	return `198.18.${Math.floor(n / 250)}.${(n % 250) + 1}`;
}

function headers(ip) {
	const h = { 'X-API-Key': API_KEY, Accept: 'application/json' };
	if (HOST_HEADER) h.Host = HOST_HEADER;
	if (ip) h['CF-Connecting-IP'] = ip;
	return h;
}

function params(route, name, ip) {
	return {
		headers: headers(ip),
		tags: { route, name },
		timeout: '30s',
		// Bodies are read only where the journey needs them (search results) or checks them (smoke).
		responseType: ['recherche', 'recherche_fond', 'setup'].includes(route) || PROFILE === 'smoke' ? 'text' : 'none'
	};
}

const pick = (list) => list[Math.floor(Math.random() * list.length)];

function addressQuery() {
	return `${1 + Math.floor(Math.random() * 40)} ${pick(VOIES)} ${pick(COMMUNES)}`;
}

function addressesOf(res) {
	if (res.status !== 200) return [];
	try {
		return (res.json('donnees.resultats') || [])
			.filter((r) => r.type_resultat === 'adresse' && r.ban_id && r.code_commune)
			.map((r) => ({ ban_id: r.ban_id, code_commune: r.code_commune }));
	} catch (_) {
		return [];
	}
}

export function setup() {
	// Fallback pool, for the searches that return no numbered address.
	const ip = SIM_IP ? '198.18.255.1' : null;
	const pool = [];
	for (const commune of COMMUNES) {
		const q = `1 ${VOIES[0]} ${commune}`;
		const res = http.get(
			`${BASE_URL}/v1/adresses/recherche?q=${encodeURIComponent(q)}&limite=8`,
			params('setup', '/v1/adresses/recherche', ip)
		);
		if (res.status === 429 || res.status >= 500 || res.status === 0) {
			throw new Error(`setup: search refused with status ${res.status}`);
		}
		pool.push(...addressesOf(res));
	}
	if (pool.length === 0) throw new Error('setup: no numbered address found, check BASE_URL, HOST_HEADER and coverage');
	console.log(`setup: ${pool.length} addresses in the fallback pool, profile ${PROFILE}`);
	return { pool };
}

// VU-local window of the last ABORT_FENETRE outcomes (true = refused or too slow).
const recent = [];
function observe(res, route) {
	const refused = res.status === 0 || res.status === 429 || res.status >= 500;
	const bad = refused || res.timings.duration > ABORT_MS;
	if (refused) refus.add(1, { route, status: String(res.status) });
	recent.push(bad);
	if (recent.length > ABORT_FENETRE) recent.shift();
	if (recent.length >= ABORT_MIN) {
		const ratio = recent.filter(Boolean).length / recent.length;
		if (ratio >= ABORT_TAUX) {
			exec.test.abort(
				`rupture: ${Math.round(ratio * 100)} % of the last ${recent.length} requests of VU ` +
					`${exec.vu.idInTest} refused or slower than ${ABORT_MS} ms (last status ${res.status})`
			);
		}
	}
}

// abort=false for the background fiches: an upstream slower than ABORT_MS is not a break of the API.
function checkResponse(res, route, abort = true) {
	const ok = { [`${route} 2xx`]: (r) => r.status >= 200 && r.status < 300 };
	if (PROFILE === 'smoke') {
		ok[`${route} envelope`] = (r) => {
			try {
				return r.json('donnees') !== undefined && r.json('donnees') !== null;
			} catch (_) {
				return false;
			}
		};
	}
	check(res, ok, { route });
	if (abort) observe(res, route);
	else if (res.status === 0 || res.status === 429 || res.status >= 500) refus.add(1, { route, status: String(res.status) });
}

export default function (data) {
	const ip = SIM_IP ? simulatedIp() : null;
	// VU tag: every sample of the iteration carries it, the iterations metric included.
	exec.vu.metrics.tags.palier = currentPalier();

	const search = http.get(
		`${BASE_URL}/v1/adresses/recherche?q=${encodeURIComponent(addressQuery())}&limite=8`,
		params('recherche', '/v1/adresses/recherche', ip)
	);
	checkResponse(search, 'recherche');
	const found = addressesOf(search);
	const target = found.length ? pick(found) : pick(data.pool);

	if (THINK_S > 0) sleep(THINK_S);

	const id = encodeURIComponent(target.ban_id);
	if (!FOND) {
		const fiche = http.get(`${BASE_URL}/v1/adresse/${id}`, params('fiche_adresse', '/v1/adresse/{ban_id}', ip));
		checkResponse(fiche, 'fiche_adresse');
	}

	const batch = [
		['commune', '/v1/commune', `${BASE_URL}/v1/commune?code_insee=${encodeURIComponent(target.code_commune)}`],
		['dvf_ventes', '/v1/adresse/{ban_id}/ventes', `${BASE_URL}/v1/adresse/${id}/ventes`],
		['dvf_ventes_declarees', '/v1/adresse/{ban_id}/ventes-declarees', `${BASE_URL}/v1/adresse/${id}/ventes-declarees`],
		['dpe_adresse', '/v1/adresse/{ban_id}/dpe', `${BASE_URL}/v1/adresse/${id}/dpe`]
	];
	const responses = http.batch(batch.map(([route, name, url]) => ['GET', url, null, params(route, name, ip)]));
	responses.forEach((res, i) => checkResponse(res, batch[i][0]));
}

// FICHE=fond: one fiche of a freshly searched address, from its own block of simulated visitors.
export function ficheFond(data) {
	const ip = SIM_IP ? `198.18.254.${(exec.scenario.iterationInTest % 250) + 1}` : null;
	exec.vu.metrics.tags.palier = currentPalier();

	const search = http.get(
		`${BASE_URL}/v1/adresses/recherche?q=${encodeURIComponent(addressQuery())}&limite=8`,
		params('recherche_fond', '/v1/adresses/recherche', ip)
	);
	checkResponse(search, 'recherche_fond', false);
	const found = addressesOf(search);
	const target = found.length ? pick(found) : pick(data.pool);
	const fiche = http.get(
		`${BASE_URL}/v1/adresse/${encodeURIComponent(target.ban_id)}`,
		params('fiche_adresse', '/v1/adresse/{ban_id}', ip)
	);
	checkResponse(fiche, 'fiche_adresse', false);
}
