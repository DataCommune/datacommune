import { createServer } from 'node:http';

const HOST = '127.0.0.1';
const PORT = Number(process.env.DPE_SOURCE_PORT ?? '14174');
const TOTAL = 137;
const PAGE_SIZE = 20;
const PUBLIC_API_KEY = 'dc_anon_e2e_fixture';

const PRIVATE_SENTINELS = {
	root: 'PRIVATE-SSR-ROOT-SENTINEL',
	ademe: 'PRIVATE-ADEME-NUMBER-SENTINEL',
	ban: 'PRIVATE-BAN-KEY-SENTINEL',
	address: 'PRIVATE-RAW-ADDRESS-SENTINEL',
	coordinates: 'PRIVATE-COORDINATES-SENTINEL',
	replacement: 'PRIVATE-REPLACEMENT-ID-SENTINEL',
	legacyCandidate: 'PRIVATE-LEGACY-CANDIDATE-SENTINEL',
	otherBan: 'PRIVATE-OTHER-BAN-DOCUMENT-SENTINEL',
	error: 'PRIVATE-UPSTREAM-ERROR-SENTINEL'
} as const;

const CLASSES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', null] as const;
const TYPES = ['maison', 'appartement', 'immeuble', null] as const;
const CURSORS = new Map<string | null, number>([
	[null, 0],
	['opaque-page-b', 20],
	['opaque-page-c', 40],
	['opaque-page-d', 60],
	['opaque-page-e', 80],
	['opaque-page-f', 100],
	['opaque-page-g', 120]
]);
const NEXT_CURSOR = new Map<number, string>([
	[0, 'opaque-page-b'],
	[20, 'opaque-page-c'],
	[40, 'opaque-page-d'],
	[60, 'opaque-page-e'],
	[80, 'opaque-page-f'],
	[100, 'opaque-page-g']
]);

interface RecordedRequest {
	method: string;
	pathname: string;
	search: string;
	query: Record<string, string>;
	auth: {
		mode: 'public' | 'authenticated' | 'invalid';
		hasApiKey: boolean;
		hasAccessTokenCookie: boolean;
		hasUnexpectedCookies: boolean;
	};
}

interface ResponseOverride {
	status: number;
	body: unknown;
	/** Opt-in deterministic response barrier for navigation-race scenarios. */
	gate?: string;
	/** Extra response headers (04.7-10: the `Retry-After` of a 429 or a 503). */
	headers?: Record<string, string>;
}

interface ExpectedRequest {
	method: string;
	pathname: string;
	query: Record<string, string>;
	minCalls: number;
	maxCalls: number | null;
	calls: number;
	responses: Array<ResponseOverride | null>;
}

interface RequestViolation {
	reason: 'invalid_auth' | 'unconfigured_request' | 'too_many_calls';
	request: RecordedRequest;
	expected: Array<Pick<ExpectedRequest, 'method' | 'pathname' | 'query' | 'maxCalls' | 'calls'>>;
}

const requests: RecordedRequest[] = [];
const expectations: ExpectedRequest[] = [];
const violations: RequestViolation[] = [];
const responseGates = new Map<string, Array<() => void>>();
const releasedGates = new Set<string>();
const configuredResolve = new Map<string, { status: number; body: unknown }>();
const configuredLogements = new Map<string, { status: number; body: unknown }>();
let lifecycleDate = '2026-09-08';
let lifecyclePresent = false;
let configuredProxy: { status: number; body: unknown } | null = null;

function queryRecord(url: URL): Record<string, string> {
	return Object.fromEntries(
		[...url.searchParams.entries()].sort(([left], [right]) => left.localeCompare(right))
	);
}

function sameQuery(left: Record<string, string>, right: Record<string, string>): boolean {
	const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
	const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
	return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
}

function expectationKey(
	expectation: Pick<ExpectedRequest, 'method' | 'pathname' | 'query'>
): string {
	return JSON.stringify([
		expectation.method,
		expectation.pathname,
		Object.entries(expectation.query).sort(([a], [b]) => a.localeCompare(b))
	]);
}

function parseExpectation(value: unknown): ExpectedRequest {
	if (value == null || typeof value !== 'object') throw new Error('Expectation must be an object.');
	const input = value as Record<string, unknown>;
	if (typeof input.method !== 'string' || input.method.trim() === '') {
		throw new Error('Expectation method is required.');
	}
	if (typeof input.pathname !== 'string' || !input.pathname.startsWith('/')) {
		throw new Error('Expectation pathname is required.');
	}
	if (input.query == null || typeof input.query !== 'object' || Array.isArray(input.query)) {
		throw new Error('Expectation query must be an object.');
	}
	const query = Object.fromEntries(
		Object.entries(input.query as Record<string, unknown>).map(([key, entry]) => {
			if (typeof entry !== 'string') throw new Error(`Expectation query ${key} must be a string.`);
			return [key, entry];
		})
	);
	const minCalls = input.minCalls === undefined ? 0 : Number(input.minCalls);
	const maxCalls =
		input.maxCalls === undefined ? 1 : input.maxCalls === null ? null : Number(input.maxCalls);
	if (!Number.isInteger(minCalls) || minCalls < 0)
		throw new Error('Expectation minCalls is invalid.');
	if (maxCalls !== null && (!Number.isInteger(maxCalls) || maxCalls < minCalls)) {
		throw new Error('Expectation maxCalls is invalid.');
	}
	const responseInputs = input.responses === undefined ? [] : input.responses;
	if (!Array.isArray(responseInputs)) throw new Error('Expectation responses must be an array.');
	const responses = responseInputs.map((entry): ResponseOverride | null => {
		if (entry === null) return null;
		if (entry == null || typeof entry !== 'object')
			throw new Error('Response override is invalid.');
		const response = entry as Record<string, unknown>;
		if (typeof response.status !== 'number')
			throw new Error('Response override status is required.');
		if (response.gate !== undefined && (typeof response.gate !== 'string' || !/^[a-z0-9-]+$/.test(response.gate))) {
			throw new Error('Response gate is invalid.');
		}
		let headers: Record<string, string> | undefined;
		if (response.headers !== undefined) {
			if (response.headers === null || typeof response.headers !== 'object') {
				throw new Error('Response headers must be an object of strings.');
			}
			const entries = Object.entries(response.headers as Record<string, unknown>);
			if (entries.some(([, value]) => typeof value !== 'string')) {
				throw new Error('Response headers must be an object of strings.');
			}
			headers = Object.fromEntries(entries) as Record<string, string>;
		}
		return {
			status: response.status,
			body: response.body,
			...(response.gate ? { gate: response.gate as string } : {}),
			...(headers ? { headers } : {})
		};
	});
	return {
		method: input.method.toUpperCase(),
		pathname: input.pathname,
		query,
		minCalls,
		maxCalls,
		calls: 0,
		responses
	};
}

function upsertExpectations(values: unknown): void {
	if (values === undefined) return;
	if (!Array.isArray(values)) throw new Error('Expectations must be an array.');
	for (const value of values) {
		const next = parseExpectation(value);
		const key = expectationKey(next);
		const current = expectations.find((entry) => expectationKey(entry) === key);
		if (current === undefined) {
			expectations.push(next);
			continue;
		}
		current.minCalls = Math.max(current.minCalls, next.minCalls);
		current.maxCalls = next.maxCalls;
		if (next.responses.length > 0) current.responses = next.responses;
	}
}

function resetContract(): void {
	for (const waiting of responseGates.values()) for (const release of waiting) release();
	responseGates.clear();
	releasedGates.clear();
	lifecycleDate = '2026-09-08';
	lifecyclePresent = false;
	requests.length = 0;
	expectations.length = 0;
	violations.length = 0;
	configuredResolve.clear();
	configuredLogements.clear();
	configuredProxy = null;
}

/**
 * Ambient reads of the internal links (SEO audit 2026-11-14): the commune page reads its neighbours
 * and the departement page its pages beside their own data. A spec that configures neither gets empty
 * lists, which render no link, instead of a violation on every page; a spec that configures the path
 * keeps its exact, fail-closed expectation.
 */
function ambientLinks(method: string, url: URL): unknown | null {
	if (method !== 'GET' || expectations.some((expectation) => expectation.pathname === url.pathname)) return null;
	if (url.pathname === '/v1/communes/voisines') {
		return { statut: 'ok', donnees: { code_insee: url.searchParams.get('code_insee') ?? '', voisines: [] } };
	}
	const fiches = /^\/v1\/departements\/([^/]+)\/fiches$/.exec(url.pathname);
	if (fiches) return { statut: 'ok', donnees: { code: fiches[1], derniere_importation: null, fiches: [] } };
	return null;
}

function authorizeRequest(record: RecordedRequest): ResponseOverride | null | false {
	if (record.auth.mode === 'invalid') {
		violations.push({
			reason: 'invalid_auth',
			request: record,
			expected: []
		});
		return false;
	}
	const candidates = expectations.filter(
		(expectation) =>
			expectation.method === record.method &&
			expectation.pathname === record.pathname &&
			sameQuery(expectation.query, record.query)
	);
	const expectation = candidates[0];
	if (expectation === undefined) {
		violations.push({
			reason: 'unconfigured_request',
			request: record,
			expected: expectations.map(({ method, pathname, query, maxCalls, calls }) => ({
				method,
				pathname,
				query,
				maxCalls,
				calls
			}))
		});
		return false;
	}
	if (expectation.maxCalls !== null && expectation.calls >= expectation.maxCalls) {
		violations.push({
			reason: 'too_many_calls',
			request: record,
			expected: candidates.map(({ method, pathname, query, maxCalls, calls }) => ({
				method,
				pathname,
				query,
				maxCalls,
				calls
			}))
		});
		return false;
	}
	const response = expectation.responses[expectation.calls] ?? null;
	expectation.calls += 1;
	return response;
}

function scenarioFromText(value: string | null): string {
	const text = (value ?? '').toLowerCase();
	if (text.includes('cycle vie')) return 'lifecycle';
	if (text.includes('confidentialite hostile')) return 'privacy-hostile';
	if (text.includes('panne logements')) return 'logements-outage';
	if (text.includes('panne resolve')) return 'resolve-outage';
	if (text.includes('panne page')) return 'page-outage';
	if (text.includes('panne detail')) return 'detail-outage';
	if (text.includes('generation')) return 'generation-mismatch';
	if (text.includes('nouvelle cible')) return 'new-target';
	return 'inventory-137';
}

function contextFor(scenario: string): string {
	return `opaque-context-${scenario}${scenario === 'lifecycle' ? `-${lifecycleDate}-${lifecyclePresent ? 'returned' : 'absent'}` : ''}`;
}

function parcelIdFor(scenario: string): string {
	return `85999000ZZ${scenario === 'new-target' ? '0043' : '0042'}`;
}

function parcelNumberFor(scenario: string): string {
	return scenario === 'new-target' ? '0043' : '0042';
}

function isPrivacyHostile(scenario: string): boolean {
	return scenario === 'privacy-hostile';
}

function distribution(scenario = '') {
	const result = { A: 0, B: 0, C: 0, D: 0, E: 0, F: 0, G: 0, unknown: 0 };
	for (let index = 0; index < TOTAL; index += 1) {
		const value = scenario === 'lifecycle' && index === 1 && !lifecyclePresent ? null : CLASSES[index % CLASSES.length];
		if (value === null) result.unknown += 1;
		else result[value] += 1;
	}
	return result;
}

/** Synthetic lifecycle facts; the control endpoint simulates a new server day/publication. */
function lifecycleFields(index: number) {
 const absent = index === 1 && !lifecyclePresent;
 const established = absent ? null : index === 2 ? '2027-01-01' : '2020-01-01';
 const end = absent || index === 5 ? null : index === 0 ? '2025-02-03' : index === 3 ? '2019-01-01' : '2030-01-01';
 return {
  date_etablissement_dpe: established, date_fin_validite_dpe: end,
  replacement_id:PRIVATE_SENTINELS.replacement,
  ...(absent ? {type_batiment:'PRIVATE-TOMBSTONE-HISTORY',surface_habitable:999.5,etiquette_dpe:'G',etiquette_ges:'G'} : {}),
  lifecycle: {
   source_state: absent ? 'absent_from_complete_scope' : index === 4 ? 'PRIVATE-UNKNOWN-LIFECYCLE' : 'present',
   source_snapshot_date:lifecycleDate,source_scope:'44/85',regimes:index === 0 ? ['existant','neuf'] : ['existant'],
   replacement_state:index === 0 ? 'replaced' : 'no_successor_known',
   graph_issues:index === 0 ? ['cycle','fork','missing_target','truncated','ambiguous_target'] : [],
   validity: absent || index === 5 ? 'unknown' : index === 0 ? 'expired' : index === 2 ? 'future_establishment' : index === 3 ? 'inconsistent_dates' : 'not_expired_by_date',
   as_of_date:lifecycleDate,revision_status:'unverified',revision_kind:null,revision_date:null,
   detail_availability:absent ? 'source_reconciled' : 'available'
  }
 };
}

function inventoryItem(index: number, privacyHostile = false, scenario = '') {
	const ordinal = index + 1;
	const dpeClass = CLASSES[index % CLASSES.length];
	return {
		document_ref: `opaque-document-${String(ordinal).padStart(3, '0')}`,
		type_batiment: TYPES[index % TYPES.length],
		surface_habitable: index % 11 === 0 ? null : 40 + (index % 67),
		etiquette_dpe: dpeClass,
		etiquette_ges: index % 9 === 0 ? null : CLASSES[(index + 2) % 7],
		date_etablissement_dpe: index % 10 === 0 ? null : `202${index % 5}-01-15`,
		date_fin_validite_dpe: index % 10 === 0 ? null : `203${index % 5}-01-14`,
		...(scenario === 'lifecycle' ? lifecycleFields(index) : {}),
		...(privacyHostile
			? {
					numero_dpe: PRIVATE_SENTINELS.ademe,
					ban_id: PRIVATE_SENTINELS.ban,
					adresse_brute: PRIVATE_SENTINELS.address,
					coordonnees: PRIVATE_SENTINELS.coordinates,
					remplacement: { id: PRIVATE_SENTINELS.replacement }
				}
			: {})
	};
}

function inventoryPage(scenario: string, cursor: string | null = null) {
	const offset = CURSORS.get(cursor);
	if (offset === undefined) return null;
	const items = Array.from({ length: Math.min(PAGE_SIZE, TOTAL - offset) }, (_, index) =>
		inventoryItem(offset + index, isPrivacyHostile(scenario), scenario)
	);
	return {
		version: 2,
		state: 'ready',
		context_ref: contextFor(scenario),
		items,
		total: TOTAL,
		distribution: distribution(scenario),
		...(scenario === 'lifecycle' ? {as_of_date:lifecycleDate} : {}),
		page_size: PAGE_SIZE,
		has_more: offset + items.length < TOTAL,
		next_cursor: NEXT_CURSOR.get(offset) ?? null,
		read_state: 'complete',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'incoherent',
		imported_at: '2026-09-04T09:00:00Z',
		...(isPrivacyHostile(scenario)
			? {
					private_root: PRIVATE_SENTINELS.root,
					other_ban_documents: [{ numero_dpe: PRIVATE_SENTINELS.otherBan }]
				}
			: {})
	};
}

function attribution(privacyHostile = false) {
	return {
		version: 2,
		status: 'unassigned',
		reason: 'dwelling_identity_unproven',
		diagnostic: null,
		candidates: [],
		building_context: null,
		graph_state: 'incoherent',
		...(privacyHostile
			? {
					legacy_candidates: [{ numero_dpe: PRIVATE_SENTINELS.legacyCandidate }]
				}
			: {})
	};
}

function commune() {
	return {
		code_insee: '85999',
		nom_commune: 'Commune Test 85',
		code_departement: '85',
		code_region: '52',
		population: 12_345,
		nb_menages: 5_100,
		nb_logements: 5_800,
		nb_logements_vacants_recensement: 410,
		densite: 250,
		superficie: 49,
		annee_demographie: 2023,
		revenu_median: 22_100,
		taux_pauvrete: 11.2,
		indice_gini: 0.26,
		part_imposee: 57,
		annee_revenus: 2023,
		zone_abc: 'B2',
		zone_abc_reclassement: false,
		taux_vacance: 7.1,
		nb_vacants_plus_2ans: 95,
		annee_vacance: 2023,
		taux_tfpb: 39.4,
		taux_th_rs: 17.2,
		taux_teom: 10.8,
		nom_epci: 'EPCI synthétique',
		annee_fiscalite: 2024,
		loyer_m2_appartement: 10.2,
		loyer_m2_maison: 9.4,
		annee_loyers: 2024
	};
}

function resolveEnvelope(address: string, scenario: string) {
	const page = inventoryPage(scenario);
	const parcelId = parcelIdFor(scenario);
	const privacyHostile = isPrivacyHostile(scenario);
	return {
		statut: 'ok',
		donnees: [
			{
				type_detecte: 'adresse',
				latitude: 46.85,
				longitude: -1.85,
				adresse: address,
				id_parcelle: parcelId,
				commune: 'Commune Test 85',
				code_postal: '85000',
				code_insee: '85999',
				score: null,
				resolution: 'exact',
				parcelles_candidates: null,
				ban_id: `${PRIVATE_SENTINELS.ban}-${scenario}`,
				dpe_inventory: page,
				dpe_attribution: attribution(privacyHostile),
				datasets: {
					cadastre: {
						id_parcelle: parcelId,
						commune: '85999',
						section: 'ZZ',
						numero: parcelNumberFor(scenario),
						contenance: 640
					},
					dpe: privacyHostile
						? [
								{
									numero_dpe: PRIVATE_SENTINELS.ademe,
									adresse_brute: PRIVATE_SENTINELS.address,
									coordonnees: PRIVATE_SENTINELS.coordinates
								}
							]
						: null,
					dvf: [
						{
							id_mutation: '2023-SYNTHETIC',
							date_mutation: '2023-09-12',
							valeur_fonciere: 168_000,
							type_local: 'Appartement',
							surface_reelle_bati: 78,
							nombre_pieces_principales: 3,
							prix_m2: 2_154
						}
					],
					commune: commune()
				},
				erreurs: [],
				avertissements: [],
				dataset_states: {
					cadastre: {
						dataset: 'cadastre',
						presence: 'value',
						absence_reason: null,
						raison: null
					},
					dpe: {
						dataset: 'dpe',
						presence: 'value',
						absence_reason: null,
						raison: null
					},
					dvf: {
						dataset: 'dvf',
						presence: 'value',
						absence_reason: null,
						raison: null
					},
					commune: {
						dataset: 'commune',
						presence: 'value',
						absence_reason: null,
						raison: null
					},
					risques: {
						dataset: 'risques',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					},
					urbanisme: {
						dataset: 'urbanisme',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					}
				}
			}
		],
		meta: {
			resolution: { issue: 'exact', raison: null, parcelles_candidates: null },
			source: []
		}
	};
}

function proxyEnvelope() {
	return {
		statut: 'ok',
		donnees: [
			{
				datasets: { risques: null, urbanisme: null },
				erreurs: [],
				avertissements: [],
				dataset_states: {
					risques: {
						dataset: 'risques',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					},
					urbanisme: {
						dataset: 'urbanisme',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					}
				}
			}
		],
		meta: { source: [] }
	};
}

function configuredProxyEnvelope(body: unknown) {
	const root = body != null && typeof body === 'object' ? (body as Record<string, unknown>) : {};
	const rows = Array.isArray(root.donnees) ? root.donnees : [];
	const first =
		rows[0] != null && typeof rows[0] === 'object' ? (rows[0] as Record<string, unknown>) : {};
	const datasets =
		first.datasets != null && typeof first.datasets === 'object'
			? (first.datasets as Record<string, unknown>)
			: {};
	const states =
		first.dataset_states != null && typeof first.dataset_states === 'object'
			? (first.dataset_states as Record<string, unknown>)
			: {};
	const errors = Array.isArray(first.erreurs)
		? first.erreurs.filter((entry) => {
				const dataset =
					entry != null && typeof entry === 'object'
						? (entry as Record<string, unknown>).dataset
						: null;
				return dataset === 'risques' || dataset === 'urbanisme' || dataset === 'errial';
			})
		: [];
	return {
		statut: typeof root.statut === 'string' ? root.statut : 'ok',
		donnees: [
			{
				datasets: {
					risques: datasets.risques ?? null,
					urbanisme: datasets.urbanisme ?? null
				},
				erreurs: errors,
				avertissements: Array.isArray(first.avertissements) ? first.avertissements : [],
				dataset_states: {
					risques: states.risques ?? {
						dataset: 'risques',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					},
					urbanisme: states.urbanisme ?? {
						dataset: 'urbanisme',
						presence: 'absent',
						absence_reason: 'not_reported',
						raison: null
					}
				}
			}
		],
		meta: root.meta ?? { source: [] }
	};
}

function logementsEnvelope(scenario: string) {
	const embeddedScenario = scenario === 'generation-mismatch' ? 'new-target' : scenario;
	const apartmentDocuments = Array.from(
		{ length: TOTAL },
		(_, index) => TYPES[index % TYPES.length]
	).filter((type) => type === 'appartement').length;
	return {
		statut: 'ok',
		donnees: {
			id_parcelle: parcelIdFor(scenario),
			detection: {
				signaux: {
					copro_rnc: {
						positif: false,
						presence: 'absent',
						absence_reason: 'not_reported',
						compte: 0
					},
					dpe_appart: {
						positif: true,
						presence: 'value',
						absence_reason: null,
						compte: apartmentDocuments
					},
					dvf_appart: {
						positif: false,
						presence: 'absent',
						absence_reason: 'not_reported',
						compte: 0
					}
				},
				signaux_positifs: 1,
				mode: 'indice',
				preuve_identites: false
			},
			denominateur: {
				logements_connus: 0,
				lots_declares: null,
				non_distinguables: 0
			},
			distribution_dpe: {},
			logements: [],
			ventes_immeuble: [],
			diagnostics_charges: TOTAL,
			diagnostics_exclus_adresse: 0,
			rattachement: { exact: TOTAL, elargi_batiment: 0, spatial: 0 },
			tronque: false,
			dpe_attribution: attribution(isPrivacyHostile(scenario)),
			dpe_inventory: inventoryPage(embeddedScenario)
		},
		meta: { source: [] }
	};
}

function documentPayload(contextRef: string, documentRef: string) {
	const ordinal = Number(documentRef.match(/(\d+)$/)?.[1] ?? '1');
	const privacyHostile = contextRef === contextFor('privacy-hostile');
	const isLifecycle = contextRef.startsWith('opaque-context-lifecycle-');
	const item = inventoryItem(Math.max(0, ordinal - 1), privacyHostile, isLifecycle ? 'lifecycle' : '');
	return {
		version: 2,
		context_ref: contextRef,
		...item,
		surface_habitable: ordinal === 1 ? 87 : item.surface_habitable,
		annee_construction: ordinal % 3 === 0 ? null : 1972 + (ordinal % 40),
		periode_construction: ordinal % 3 === 0 ? null : '1948-1974',
		type_installation_chauffage: ordinal % 4 === 0 ? null : 'individuel',
		type_energie_principale_chauffage: ordinal % 4 === 0 ? null : 'électricité',
		qualite_isolation_enveloppe: ordinal % 5 === 0 ? null : 'moyenne',
		conso_5_usages_ep: ordinal % 6 === 0 ? null : 12_345,
		emission_ges_5_usages: ordinal % 6 === 0 ? null : 2_345,
		cout_total_5_usages: ordinal % 6 === 0 ? null : 1_456,
		validity_by_date: isLifecycle && ordinal === 1 ? 'expired' : isLifecycle && [2,3,4,6].includes(ordinal) && !(ordinal === 2 && lifecyclePresent) ? 'unknown' : item.date_fin_validite_dpe === null ? 'unknown' : 'not_expired_by_date',
		association: 'source_ban_equality',
		address_check: 'unverified',
		source: 'ADEME',
		source_coverage: 'unknown',
		graph_state: 'incoherent',
		imported_at: '2026-09-04T09:00:00Z',
		...(privacyHostile
			? {
					private_detail: PRIVATE_SENTINELS.root,
					adresse_brute: PRIVATE_SENTINELS.address,
					coordonnees: PRIVATE_SENTINELS.coordinates
				}
			: {})
	};
}

function authenticationEvidence(
	request: import('node:http').IncomingMessage
): RecordedRequest['auth'] {
	const apiKey = request.headers['x-api-key'];
	const cookieNames = (request.headers.cookie ?? '')
		.split(';')
		.map((cookie) => cookie.trim().split('=', 1)[0])
		.filter(Boolean);
	const hasAccessTokenCookie = cookieNames.includes('access_token');
	const hasUnexpectedCookies = cookieNames.some((name) => name !== 'access_token');
	const publicMode = apiKey === PUBLIC_API_KEY && cookieNames.length === 0;
	const authenticatedMode = apiKey === undefined && hasAccessTokenCookie && !hasUnexpectedCookies;
	return {
		mode: publicMode ? 'public' : authenticatedMode ? 'authenticated' : 'invalid',
		hasApiKey: apiKey !== undefined,
		hasAccessTokenCookie,
		hasUnexpectedCookies
	};
}

function sendJson(
	response: import('node:http').ServerResponse,
	status: number,
	body: unknown,
	extraHeaders: Record<string, string> = {}
) {
	response.writeHead(status, {
		...extraHeaders,
		'content-type': 'application/json; charset=utf-8',
		'access-control-allow-origin': response.req.headers.origin ?? process.env.E2E_APP_ORIGIN ?? 'http://localhost:15173',
		'access-control-allow-credentials': 'true',
		'access-control-allow-headers': 'content-type,x-api-key',
		'access-control-allow-methods': 'GET,OPTIONS',
		'cache-control': 'no-store'
	});
	response.end(JSON.stringify(body));
}

async function readJson(
	request: import('node:http').IncomingMessage
): Promise<Record<string, unknown>> {
	const chunks: Buffer[] = [];
	for await (const chunk of request) chunks.push(Buffer.from(chunk));
	const raw = Buffer.concat(chunks).toString('utf8');
	return raw === '' ? {} : (JSON.parse(raw) as Record<string, unknown>);
}

const server = createServer(async (request, response) => {
	const url = new URL(request.url ?? '/', `http://${HOST}:${PORT}`);
	if (request.method === 'OPTIONS') {
		response.writeHead(204, {
			'access-control-allow-origin': request.headers.origin ?? process.env.E2E_APP_ORIGIN ?? 'http://localhost:15173',
			'access-control-allow-credentials': 'true',
			'access-control-allow-headers': 'content-type,x-api-key',
			'access-control-allow-methods': 'GET,OPTIONS'
		});
		response.end();
		return;
	}

	if (url.pathname === '/health') {
		sendJson(response, 200, { status: 'ready', total: TOTAL });
		return;
	}
	if (url.pathname === '/__release' && request.method === 'POST') {
		const body = await readJson(request);
		if (typeof body.gate !== 'string' || !/^[a-z0-9-]+$/.test(body.gate)) {
			sendJson(response, 400, { released: false });
			return;
		}
		releasedGates.add(body.gate);
		const waiting = responseGates.get(body.gate) ?? [];
		responseGates.delete(body.gate);
		for (const release of waiting) release();
		sendJson(response, 200, { released: true, count: waiting.length });
		return;
	}
	if (url.pathname === '/__requests') {
		const query = url.searchParams.get('q');
		const filtered = query == null ? requests : requests.filter((entry) => entry.query.q === query);
		const unmetExpectations = expectations.filter(
			(expectation) => expectation.calls < expectation.minCalls
		);
		sendJson(response, 200, {
			requests: filtered,
			violations,
			expectations,
			unmetExpectations
		});
		return;
	}
	if (url.pathname === '/__configure' && request.method === 'POST') {
		try {
			const body = await readJson(request);
			if (body.reset === true) resetContract();
			upsertExpectations(body.expectations);
			if (typeof body.lifecycleDate === 'string' && /^2026-09-0[89]$/.test(body.lifecycleDate)) lifecycleDate = body.lifecycleDate;
			if (typeof body.lifecyclePresent === 'boolean') lifecyclePresent = body.lifecyclePresent;
			const addressQuery = typeof body.addressQuery === 'string' ? body.addressQuery : null;
			const resolvedAddress =
				typeof body.resolvedAddress === 'string' ? body.resolvedAddress : null;
			const resolve = body.resolve as { status?: unknown; body?: unknown } | undefined;
			const logements = body.logements as { status?: unknown; body?: unknown } | null | undefined;
			if (addressQuery !== null && resolve !== undefined) {
				const configured = {
					status: typeof resolve.status === 'number' ? resolve.status : 200,
					body: resolve.body
				};
				configuredResolve.set(addressQuery, configured);
				configuredProxy = configured;
			}
			if (resolvedAddress !== null) {
				if (logements === null) configuredLogements.delete(resolvedAddress);
				else if (logements !== undefined) {
					configuredLogements.set(resolvedAddress, {
						status: typeof logements.status === 'number' ? logements.status : 200,
						body: logements.body
					});
				}
			}
			sendJson(response, 200, {
				configured: true,
				expectations: expectations.length
			});
		} catch (error) {
			sendJson(response, 400, {
				configured: false,
				detail: error instanceof Error ? error.message : 'Invalid fixture configuration.'
			});
		}
		return;
	}
	if (url.pathname === '/auth/me') {
		// The shared E2E source must keep public pages anonymous unless a spec explicitly
		// intercepts authentication. Returning a synthetic user here contaminated every
		// unrelated critical spec and hid the public navigation it intended to exercise.
		sendJson(response, 401, { detail: 'Non authentifié' });
		return;
	}

	const ambiant = ambientLinks(request.method ?? 'GET', url);
	if (ambiant !== null) {
		sendJson(response, 200, ambiant);
		return;
	}

	const record: RecordedRequest = {
		method: request.method ?? 'GET',
		pathname: url.pathname,
		search: url.search,
		query: queryRecord(url),
		auth: authenticationEvidence(request)
	};
	requests.push(record);
	if (requests.length > 500) requests.shift();
	const responseOverride = authorizeRequest(record);
	if (responseOverride === false) {
		sendJson(response, 599, {
			detail: 'Unconfigured synthetic source request.'
		});
		return;
	}
	if (responseOverride !== null) {
		if (responseOverride.gate && !releasedGates.has(responseOverride.gate)) {
			const gate = responseOverride.gate;
			await new Promise<void>((release) => {
				responseGates.set(gate, [...(responseGates.get(gate) ?? []), release]);
			});
		}
		sendJson(response, responseOverride.status, responseOverride.body, responseOverride.headers);
		return;
	}

	if (url.pathname === '/v1/resolve') {
		const type = url.searchParams.get('type');
		if (type === 'gps') {
			const configured = configuredProxy;
			const body =
				configured == null
					? proxyEnvelope()
					: url.searchParams.get('datasets') === 'risques,urbanisme'
						? configuredProxyEnvelope(configured.body)
						: configured.body;
			sendJson(response, configured?.status ?? 200, body);
			return;
		}
		const address = url.searchParams.get('q') ?? '';
		const configured = configuredResolve.get(address);
		if (configured !== undefined) {
			sendJson(response, configured.status, configured.body);
			return;
		}
		const scenario = scenarioFromText(address);
		if (
			address === '' ||
			address.includes('voie sans numero') ||
			address.includes('commune seule')
		) {
			sendJson(response, 422, {
				title: 'Adresse à préciser',
				detail: PRIVATE_SENTINELS.error
			});
			return;
		}
		if (scenario === 'resolve-outage') {
			sendJson(response, 503, { detail: PRIVATE_SENTINELS.error });
			return;
		}
		sendJson(response, 200, resolveEnvelope(address, scenario));
		return;
	}
	if (url.pathname === '/v1/bien/logements') {
		const address = url.searchParams.get('adresse') ?? '';
		const configured = configuredLogements.get(address);
		if (configured !== undefined) {
			sendJson(response, configured.status, configured.body);
			return;
		}
		const scenario = scenarioFromText(address);
		if (scenario === 'logements-outage') {
			sendJson(response, 503, { detail: PRIVATE_SENTINELS.error });
			return;
		}
		sendJson(response, 200, logementsEnvelope(scenario));
		return;
	}
	if (url.pathname === '/v1/bien/dpe/inventaire') {
		const contextRef = url.searchParams.get('contexte') ?? '';
		const scenario = contextRef.startsWith('opaque-context-lifecycle-') ? 'lifecycle' : contextRef.replace(/^opaque-context-/, '');
		if (scenario === 'lifecycle' && contextRef !== contextFor('lifecycle')) {
			sendJson(response, 409, {detail:{code:'refresh_required'}});
			return;
		}
		const cursor = url.searchParams.get('curseur');
		const page = inventoryPage(scenario, cursor);
		if (page == null) {
			sendJson(response, 400, { detail: 'Référence invalide' });
			return;
		}
		sendJson(response, 200, page);
		return;
	}
	if (url.pathname === '/v1/bien/dpe/document') {
		const contextRef = url.searchParams.get('contexte') ?? '';
		const documentRef = url.searchParams.get('reference') ?? '';
		if (contextRef.startsWith('opaque-context-lifecycle-') && contextRef !== contextFor('lifecycle')) {
			sendJson(response, 409, {detail:{code:'refresh_required'}});
			return;
		}
		if (!contextRef.startsWith('opaque-context-') || !/^opaque-document-\d{3}$/.test(documentRef)) {
			sendJson(response, 400, { detail: 'Référence invalide' });
			return;
		}
		sendJson(response, 200, documentPayload(contextRef, documentRef));
		return;
	}

	sendJson(response, 404, { detail: 'Route synthétique inconnue' });
});

server.listen(PORT, HOST, () => {
	process.stdout.write(`DPE source ready on http://${HOST}:${PORT}\n`);
});

function shutdown() {
	server.close(() => process.exit(0));
}

process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);
