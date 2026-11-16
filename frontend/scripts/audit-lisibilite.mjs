#!/usr/bin/env node
// Readability audit of the public site (phase 4.9): the same measures on the same pages, so that a
// before and an after can be compared. Read-only, headless Chromium in an isolated context.
//
//   node scripts/audit-lisibilite.mjs [--base <origin>] [--sortie <file>] [--reference <file>]
//
// Rendered text only: the content of a closed <details> and of a closed dialog is not counted, on
// purpose, since the question is what the reader has to go through. Heights depend on the served
// data: they are objectives, not thresholds, and the script exits non-zero only on a failed page.
import { readFileSync, writeFileSync } from 'node:fs';
import { chromium } from 'playwright';

const PAGES = [
	{ id: 'accueil', chemin: '/' },
	{ id: 'adresse', chemin: '/adresse/44109_zz04_00004' },
	{ id: 'commune', chemin: '/commune/44109' },
	{ id: 'departement', chemin: '/departement/44' },
	{ id: 'annuaire', chemin: '/communes' },
	{ id: 'comparateur', chemin: '/comparer?communes=44109,85191,85262' },
	{ id: 'datasets', chemin: '/datasets' }
];
const LARGEURS = [
	{ largeur: 1440, hauteur: 900 },
	{ largeur: 375, hauteur: 812 }
];
// Three addresses for the « no information lost » comparison (`data-field-id`, before and after).
const ADRESSES_CHAMPS = ['44109_zz04_00004', '44109_zz02_00031', '85191_zz05_00011'];

function option(nom, defaut) {
	const index = process.argv.indexOf(`--${nom}`);
	return index > -1 ? process.argv[index + 1] : defaut;
}

const base = option('base', 'https://datacommune.fr').replace(/\/$/, '');
const sortie = option('sortie', null);
const reference = option('reference', null);

/** What is measured in the page; runs in the browser. */
function mesurer() {
	const texte = document.body.innerText;
	const mots = texte.split(/\s+/).filter(Boolean);
	const titres = [...document.querySelectorAll('h1, h2, h3')];
	const enBref = titres.find((titre) => titre.textContent.trim() === 'En bref');
	const longues = [...document.querySelectorAll('.cle-note')]
		.filter((note) => note.checkVisibility())
		.filter((note) => note.textContent.trim().split(/\s+/).length > 25);
	return {
		hauteur: document.documentElement.scrollHeight,
		mots: mots.length,
		source: (texte.match(/\bSource\b/g) ?? []).length,
		sigles: new Set(texte.match(/\b[A-ZÉÈ]{2,}\b/g) ?? []).size,
		enBref: enBref ? Math.round(enBref.getBoundingClientRect().top + window.scrollY) : null,
		notesLongues: longues.length
	};
}

async function champsDe(contexte, id) {
	const page = await contexte.newPage();
	await page.goto(`${base}/adresse/${id}`, { waitUntil: 'networkidle', timeout: 60_000 });
	const champs = await page.evaluate(() =>
		[...new Set([...document.querySelectorAll('[data-field-id]')].map((n) => n.getAttribute('data-field-id')))].sort()
	);
	await page.close();
	return champs;
}

const navigateur = await chromium.launch({ headless: true });
const resultat = { base, date: new Date().toISOString(), version: null, pages: {}, champs: {} };
let echecs = 0;
try {
	for (const { largeur, hauteur } of LARGEURS) {
		const contexte = await navigateur.newContext({
			viewport: { width: largeur, height: hauteur },
			locale: 'fr-FR',
			timezoneId: 'Europe/Paris'
		});
		for (const { id, chemin } of PAGES) {
			const page = await contexte.newPage();
			const reponse = await page.goto(base + chemin, { waitUntil: 'networkidle', timeout: 60_000 });
			if (!reponse?.ok()) {
				echecs += 1;
				console.error(`ECHEC ${id} ${largeur} : ${reponse?.status()}`);
				await page.close();
				continue;
			}
			(resultat.pages[id] ??= {})[largeur] = await page.evaluate(mesurer);
			await page.close();
		}
		if (largeur === 1440) {
			for (const id of ADRESSES_CHAMPS) resultat.champs[id] = await champsDe(contexte, id);
			const version = await contexte.request.get(`${base}/__version`).catch(() => null);
			if (version?.ok()) resultat.version = (await version.json()).deployment_sha ?? null;
		}
		await contexte.close();
	}
} finally {
	await navigateur.close();
}

const avant = reference ? JSON.parse(readFileSync(reference, 'utf8')) : null;
const ecart = (id, largeur, cle) => {
	const ancien = avant?.pages?.[id]?.[largeur]?.[cle];
	const nouveau = resultat.pages[id]?.[largeur]?.[cle];
	return ancien === undefined || ancien === null || nouveau === ancien ? `${nouveau}` : `${ancien} -> ${nouveau}`;
};
for (const { id } of PAGES) {
	const colonnes = LARGEURS.map(({ largeur }) => `h${largeur}=${ecart(id, largeur, 'hauteur')}`);
	const le = ({ cle, nom }) => `${nom}=${ecart(id, 1440, cle)}`;
	console.log(
		[id, ...colonnes, ...[{ cle: 'mots', nom: 'mots' }, { cle: 'source', nom: 'Source' }, { cle: 'sigles', nom: 'sigles' }, { cle: 'notesLongues', nom: 'notes>25' }].map(le), `enBref375=${ecart(id, 375, 'enBref')}`].join(' | ')
	);
}
if (avant) {
	for (const [id, champs] of Object.entries(resultat.champs)) {
		const anciens = avant.champs?.[id] ?? [];
		const perdus = anciens.filter((champ) => !champs.includes(champ));
		console.log(`champs ${id} : ${champs.length} (référence ${anciens.length}), perdus : ${perdus.join(', ') || 'aucun'}`);
	}
}
if (sortie) writeFileSync(sortie, `${JSON.stringify(resultat, null, '\t')}\n`);
process.exit(echecs ? 1 : 0);
