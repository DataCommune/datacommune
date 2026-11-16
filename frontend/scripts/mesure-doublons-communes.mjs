#!/usr/bin/env node
// How alike two commune fiches look to a search engine (Search Console of 2026-11-16: 486 `/commune/<code>`
// pages « en double, Google a choisi une autre canonique », e.g. /commune/06128 -> /commune/83040).
//
// Runs against the built artifact (`npm run build` first), through the entry the image starts (server.js),
// with a local API stub serving frozen fixtures: nothing reaches a real API. For each pair of communes it
// reads the HTML the server sends, drops scripts and styles (JSON-LD included), keeps every other text,
// `<noscript>` and folded `<details>` included (a crawler reads them), and computes on words:
//   - ratio: difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), the measure of the diagnostic;
//   - propre: the share of the words of each page outside the blocks it shares with the other one.
//
// Communes: three real small ones read from the public site (06128, 83040 frozen on 2026-11-16, 26274 frozen
// on 2026-11-09), Paris and its 11th arrondissement (frozen exports), and one synthetic twin per real small
// commune (same served states, figures moved by +17 %, fictitious code and name): the worst case of two
// neighbours of one departement, which the fixtures of the repository do not hold. The stub serves no
// neighbour (an empty list): commune-specific links would only lower the ratios.
//
//   node scripts/mesure-doublons-communes.mjs [--seuil 0.5] [--json] [--html <dir>]
// --html keeps the served page of each commune in <dir> (an existing directory), to see what is shared.
// Exit code 1 when a ratio is above --seuil (no threshold: always 0).
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { createServer } from 'node:net';
import { fileURLToPath } from 'node:url';

const FRONTEND = fileURLToPath(new URL('..', import.meta.url));
const FIXTURES = new URL('../src/lib/data/__fixtures__/', import.meta.url);
const lire = (nom) => JSON.parse(readFileSync(new URL(nom, FIXTURES), 'utf8'));

// --- Communes -------------------------------------------------------------------------------------------

/** API answers of `/v1/commune`, by code. */
function communes() {
	const gsc = lire('commune-06128-83040-20261116.json');
	const depuisExport = (exp) => ({ statut: 'ok', donnees: [exp.donnees], meta: { source: exp.sources } });
	const reponses = {
		'06128': gsc['06128'],
		'83040': gsc['83040'],
		'26274': depuisExport(lire('revue-commune-26274.json')),
		'75056': depuisExport(lire('commune-75056-autre-maille.json')),
		'75111': depuisExport(lire('commune-75111-autre-maille.json'))
	};
	for (const [code, jumelle, nom] of [
		['06128', '06991', 'Jumelle Alpes-Maritimes'],
		['83040', '83991', 'Jumelle Var'],
		['26274', '26991', 'Jumelle Drôme']
	]) {
		reponses[jumelle] = jumelleDe(reponses[code], jumelle, nom);
	}
	return reponses;
}

// Keys whose number is not a figure of the commune: a year, a code, a class, a count of days.
const NON_CHIFFRE = /annee|millesime|code|classe|rang|ordre|jours|mois|version|seuil|position/i;

/** A fictitious commune of the same departement: same served states, every figure moved by +17 %. */
function jumelleDe(reponse, code, nom) {
	const copie = structuredClone(reponse);
	const bouger = (valeur, cle) => {
		if (Array.isArray(valeur)) return valeur.map((v) => bouger(v, cle));
		if (valeur && typeof valeur === 'object') {
			for (const [k, v] of Object.entries(valeur)) valeur[k] = bouger(v, k);
			return valeur;
		}
		if (typeof valeur !== 'number' || NON_CHIFFRE.test(cle) || valeur === 0) return valeur;
		if (Number.isInteger(valeur) && valeur >= 1900 && valeur <= 2100) return valeur;
		const decimales = (String(valeur).split('.')[1] ?? '').length;
		return Number((valeur * 1.17).toFixed(decimales));
	};
	const synthese = bouger(copie.donnees[0], '');
	Object.assign(synthese, { code_insee: code, nom_commune: nom });
	return copie;
}

const PAIRES = [
	['06128', '83040', 'réelles, deux départements (cas Search Console)'],
	['06128', '26274', 'réelles, deux départements'],
	['83040', '26274', 'réelles, deux départements'],
	['75056', '75111', 'réelles, même département (Paris, 11e)'],
	['75111', '06128', 'réelles, deux départements'],
	['06128', '06991', 'réelle et jumelle, même département'],
	['83040', '83991', 'réelle et jumelle, même département'],
	['26274', '26991', 'réelle et jumelle, même département'],
	['06991', '83991', 'jumelles, deux départements'],
	['83991', '26274', 'jumelle et réelle, deux départements'],
	['06991', '26991', 'jumelles, deux départements']
];

// --- Text and similarity --------------------------------------------------------------------------------

const ENTITES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'" };

/** The words of the served HTML a crawler reads as text: tags, scripts and styles out. */
export function motsServis(html) {
	const texte = html
		.replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ')
		.replace(/<!--[\s\S]*?-->/g, ' ')
		.replace(/<[^>]+>/g, ' ')
		.replace(/&(#x[0-9a-f]+|#\d+|[a-z0-9]+);/gi, (tout, e) => {
			if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : Number(e.slice(1)));
			return ENTITES[e] ?? tout;
		});
	return texte.split(/\s+/).filter(Boolean);
}

/** Matching blocks of difflib.SequenceMatcher(None, a, b, autojunk=False), sizes only. */
export function blocsCommuns(a, b) {
	const b2j = new Map();
	b.forEach((mot, j) => {
		const liste = b2j.get(mot);
		if (liste) liste.push(j);
		else b2j.set(mot, [j]);
	});
	const plusLong = (alo, ahi, blo, bhi) => {
		let besti = alo;
		let bestj = blo;
		let taille = 0;
		let j2len = new Map();
		for (let i = alo; i < ahi; i++) {
			const suivant = new Map();
			for (const j of b2j.get(a[i]) ?? []) {
				if (j < blo) continue;
				if (j >= bhi) break;
				const k = (j2len.get(j - 1) ?? 0) + 1;
				suivant.set(j, k);
				if (k > taille) {
					besti = i - k + 1;
					bestj = j - k + 1;
					taille = k;
				}
			}
			j2len = suivant;
		}
		return [besti, bestj, taille];
	};
	const blocs = [];
	const file = [[0, a.length, 0, b.length]];
	while (file.length) {
		const [alo, ahi, blo, bhi] = file.pop();
		const [i, j, k] = plusLong(alo, ahi, blo, bhi);
		if (!k) continue;
		blocs.push(k);
		if (alo < i && blo < j) file.push([alo, i, blo, j]);
		if (i + k < ahi && j + k < bhi) file.push([i + k, ahi, j + k, bhi]);
	}
	return blocs;
}

export function comparer(a, b) {
	const communs = blocsCommuns(a, b).reduce((s, k) => s + k, 0);
	return {
		ratio: a.length + b.length ? (2 * communs) / (a.length + b.length) : 1,
		propreA: a.length ? 1 - communs / a.length : 0,
		propreB: b.length ? 1 - communs / b.length : 0
	};
}

// --- Server ---------------------------------------------------------------------------------------------

function portLibre() {
	return new Promise((resolve, reject) => {
		const srv = createServer();
		srv.unref();
		srv.on('error', reject);
		srv.listen(0, '127.0.0.1', () => {
			const { port } = srv.address();
			srv.close(() => resolve(port));
		});
	});
}

async function demarrer(reponses) {
	const inconnues = new Set();
	const api = createHttpServer((req, res) => {
		const url = new URL(req.url ?? '/', 'http://stub.invalid');
		const code = url.searchParams.get('code_insee') ?? '';
		const json = (statut, corps) => {
			// SvelteKit checks CORS on a server-side fetch of another origin, as a browser would.
			res.writeHead(statut, { 'content-type': 'application/json', 'access-control-allow-origin': '*' });
			res.end(JSON.stringify(corps));
		};
		if (url.pathname === '/v1/commune' && reponses[code]) return json(200, reponses[code]);
		if (url.pathname === '/v1/communes/voisines') return json(200, { statut: 'ok', donnees: { code_insee: code, voisines: [] } });
		inconnues.add(url.pathname);
		return json(503, { detail: 'Service indisponible (stub of mesure-doublons-communes.mjs).' });
	});
	const portApi = await portLibre();
	await new Promise((resolve) => api.listen(portApi, '127.0.0.1', resolve));
	const port = await portLibre();
	const origine = `http://127.0.0.1:${port}`;
	const serveur = spawn(process.execPath, ['server.js'], {
		cwd: FRONTEND,
		env: { ...process.env, HOST: '127.0.0.1', PORT: String(port), ORIGIN: origine, API_INTERNAL_URL: `http://127.0.0.1:${portApi}` },
		stdio: ['ignore', 'ignore', 'inherit']
	});
	const limite = Date.now() + 15_000;
	for (;;) {
		try {
			await fetch(`${origine}/robots.txt`);
			break;
		} catch {
			if (serveur.exitCode !== null) throw new Error(`server.js exited with ${serveur.exitCode}`);
			if (Date.now() > limite) throw new Error(`server.js did not listen on ${origine}`);
			await new Promise((r) => setTimeout(r, 100));
		}
	}
	return { origine, inconnues, arreter: () => (serveur.kill(), api.close()) };
}

// --- Main -----------------------------------------------------------------------------------------------

async function principal() {
	const args = process.argv.slice(2);
	const iSeuil = args.indexOf('--seuil');
	const seuil = iSeuil >= 0 ? Number(args[iSeuil + 1]) : null;
	const iHtml = args.indexOf('--html');
	const dossierHtml = iHtml >= 0 ? args[iHtml + 1] : null;
	if (!existsSync(new URL('../build/handler.js', import.meta.url))) throw new Error('build/ missing: run `npm run build` first');

	const reponses = communes();
	const { origine, inconnues, arreter } = await demarrer(reponses);
	const mots = {};
	try {
		for (const code of [...new Set(PAIRES.flatMap(([a, b]) => [a, b]))]) {
			const reponse = await fetch(`${origine}/commune/${code}`);
			const html = await reponse.text();
			if (reponse.status !== 200) throw new Error(`/commune/${code}: HTTP ${reponse.status}`);
			if (!html.includes('id="essentiel"')) throw new Error(`/commune/${code}: the fiche state was not rendered`);
			mots[code] = motsServis(html);
			if (dossierHtml) writeFileSync(`${dossierHtml}/${code}.html`, html);
		}
	} finally {
		arreter();
	}

	const lignes = PAIRES.map(([a, b, nature]) => ({ a, b, nature, motsA: mots[a].length, motsB: mots[b].length, ...comparer(mots[a], mots[b]) }));
	if (args.includes('--json')) {
		console.log(JSON.stringify({ lignes, appelsNonServis: [...inconnues] }, null, 2));
	} else {
		const pc = (x) => `${(x * 100).toFixed(0)} %`;
		console.log('| Paire | Nature | Mots servis | Ratio | Part propre (A / B) |');
		console.log('|---|---|---|---|---|');
		for (const l of lignes) {
			console.log(`| ${l.a} / ${l.b} | ${l.nature} | ${l.motsA} / ${l.motsB} | ${l.ratio.toFixed(2)} | ${pc(l.propreA)} / ${pc(l.propreB)} |`);
		}
		const ratios = lignes.map((l) => l.ratio);
		console.log(`\nRatio : min ${Math.min(...ratios).toFixed(2)}, max ${Math.max(...ratios).toFixed(2)}.`);
		if (inconnues.size) console.log(`Appels de l'API non servis par le stub (503) : ${[...inconnues].join(', ')}.`);
	}
	const audessus = seuil === null ? [] : lignes.filter((l) => l.ratio > seuil);
	if (audessus.length) {
		console.error(`${audessus.length} paire(s) au-dessus du seuil ${seuil} : ${audessus.map((l) => `${l.a}/${l.b} ${l.ratio.toFixed(2)}`).join(', ')}`);
		process.exitCode = 1;
	}
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
	principal().catch((err) => {
		console.error(err);
		process.exitCode = 2;
	});
}
