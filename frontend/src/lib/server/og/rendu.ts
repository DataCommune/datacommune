// Drawing of a link preview (1200 x 630 PNG) from a card of `$lib/seo/og-carte`: an SVG written here,
// rasterised by resvg compiled to WebAssembly (`@resvg/resvg-wasm`, MPL-2.0). WebAssembly rather than
// the native `@resvg/resvg-js`: the runtime image copies `build/` alone, with no node_modules, so the
// renderer and its fonts must be bundled (`?hex`, vite.config.ts); a `.node` binary cannot be.
// The fonts are the charter's (Newsreader for the name, Inter for the rest), as static TrueType
// instances written by `scripts/og-polices.py` (resvg does not read WOFF2), with the advance widths
// of their characters: a long name is sized and cut here, never left to overflow.
import { initWasm, Resvg } from '@resvg/resvg-wasm';
import wasm from '@resvg/resvg-wasm/index_bg.wasm?hex';
import inter400 from './polices/inter-400.ttf?hex';
import inter400ext from './polices/inter-400-ext.ttf?hex';
import inter600 from './polices/inter-600.ttf?hex';
import inter600ext from './polices/inter-600-ext.ttf?hex';
import newsreader500 from './polices/newsreader-500.ttf?hex';
import metriques from './polices/metriques.json';
import { OG_HAUTEUR, OG_LARGEUR, type CarteOg } from '$lib/seo/og-carte';

type Police = keyof typeof metriques;

/** Bytes of a file bundled as hex (`?hex`, vite.config.ts). */
function octets(hex: string): Uint8Array {
	return Buffer.from(hex, 'hex');
}

let pret: Promise<void> | undefined;
let polices: Uint8Array[] | undefined;

/** The WebAssembly module is instantiated once per process (initWasm refuses a second call). */
function initialiser(): Promise<void> {
	pret ??= initWasm(octets(wasm)).catch((erreur) => {
		pret = undefined;
		throw erreur;
	});
	return pret;
}

const COULEURS = {
	fond: '#1f3d33',
	anneau: '#27453c',
	pointille: '#798b82',
	encre: '#f4f6f2',
	doux: '#cfd8d4',
	pale: '#a9bcb2',
	filet: '#46645a',
	marque: '#d8ecdb'
} as const;

const MARGE = 80;
const UTILE = OG_LARGEUR - 2 * MARGE;
const ECART_COLONNES = 48;

/**
 * The text as the fonts can draw it: the narrow no-break space of French figures (U+202F) is not in
 * the latin subsets, a no-break space takes its place; an arrow of a served period becomes a dash.
 */
export function lisible(texte: string): string {
	return texte.replace(/ /g, ' ').replace(/\s*→\s*/g, ' – ');
}

function echapper(texte: string): string {
	return texte.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Width of a line in pixels, from the advance widths of the font (no kerning: a slight overestimate). */
export function largeur(texte: string, police: Police, taille: number): number {
	const avances = metriques[police] as Record<string, number>;
	let em = 0;
	for (const caractere of texte) em += avances[String(caractere.codePointAt(0))] ?? 0.6;
	return em * taille;
}

/** The text cut to the width, ending with « … » when cut. */
function tronquer(texte: string, police: Police, taille: number, max: number): string {
	if (largeur(texte, police, taille) <= max) return texte;
	let garde = texte;
	while (garde.length > 1 && largeur(`${garde}…`, police, taille) > max) garde = garde.slice(0, -1);
	return `${garde.trimEnd()}…`;
}

/** The largest size, from `max` down to `min`, at which the text holds in the width; `min` when none. */
function taillePour(texte: string, police: Police, max: number, min: number, largeurMax: number): number {
	for (let taille = max; taille > min; taille -= 2) if (largeur(texte, police, taille) <= largeurMax) return taille;
	return min;
}

/** The name on one line, else on two cut at a space or after a hyphen, as large as the room allows. */
export function placerNom(nom: string): { taille: number; lignes: string[] } {
	const police: Police = 'newsreader-500';
	if (largeur(nom, police, 64) <= UTILE) return { taille: taillePour(nom, police, 96, 64, UTILE), lignes: [nom] };
	const coupures: number[] = [];
	for (let i = 1; i < nom.length; i++) {
		if (nom[i] === ' ') coupures.push(i);
		else if (nom[i - 1] === '-') coupures.push(i);
	}
	let meilleure: { taille: number; lignes: string[] } | null = null;
	for (const i of coupures) {
		const lignes = [nom.slice(0, i).trimEnd(), nom.slice(i).trimStart()];
		const plusLongue = lignes.reduce((a, b) => (largeur(a, police, 1) >= largeur(b, police, 1) ? a : b));
		const taille = taillePour(plusLongue, police, 72, 48, UTILE);
		if (!meilleure || taille > meilleure.taille) meilleure = { taille, lignes };
	}
	if (!meilleure) return { taille: 64, lignes: [tronquer(nom, police, 64, UTILE)] };
	return { taille: meilleure.taille, lignes: meilleure.lignes.map((ligne) => tronquer(ligne, police, meilleure.taille, UTILE)) };
}

/**
 * The source lines of the columns: whole when they all fit, else all without their last clauses
 * (« · au … ») alike, so that no column says a period the others leave out; a line still too long is cut.
 */
export function placerSources(sources: string[], max: number): string[] {
	const morceaux = sources.map((source) => source.split(' · '));
	const tient = (n: number) => morceaux.every((m) => largeur(m.slice(0, n).join(' · '), 'inter-400', 19) <= max);
	let n = Math.max(...morceaux.map((m) => m.length));
	while (n > 1 && !tient(n)) n--;
	return morceaux.map((m) => tronquer(m.slice(0, n).join(' · '), 'inter-400', 19, max));
}

function texte(x: number, y: number, contenu: string, police: Police, taille: number, couleur: string): string {
	const famille = police.startsWith('newsreader') ? 'Newsreader' : 'Inter';
	const poids = police.split('-')[1];
	return `<text x="${x}" y="${y}" font-family="${famille}" font-weight="${poids}" font-size="${taille}" fill="${couleur}">${echapper(contenu)}</text>`;
}

/** The SVG of a card. Exported for the tests, which read what is written without rasterising it. */
export function svgDeCarte(carte: CarteOg): string {
	const nom = placerNom(lisible(carte.nom));
	const surtitre = tronquer(lisible(carte.surtitre), 'inter-400', 28, UTILE);
	const parties: string[] = [];

	// The rings of scale of the charter, top right, behind the text.
	parties.push(
		`<g transform="translate(1110 120)">` +
			[380, 260, 150]
				.map((r) => `<circle r="${r}" fill="${COULEURS.anneau}" opacity=".6"/><circle r="${r}" fill="none" stroke="${COULEURS.pointille}" stroke-width="1.6" stroke-dasharray="2 7" stroke-linecap="round"/>`)
				.join('') +
			`</g>`
	);
	// The mark and the name of the site.
	parties.push(
		`<rect x="${MARGE}" y="66" width="14" height="14" fill="${COULEURS.marque}"/><rect x="${MARGE + 18}" y="66" width="14" height="14" fill="${COULEURS.marque}"/>` +
			`<rect x="${MARGE}" y="84" width="14" height="14" fill="${COULEURS.marque}"/><rect x="${MARGE + 18}" y="84" width="14" height="14" fill="${COULEURS.pointille}"/>`,
		texte(MARGE + 48, 93, 'DataCommune', 'inter-600', 28, COULEURS.encre)
	);
	parties.push(texte(MARGE, 196, surtitre, 'inter-400', 28, COULEURS.doux));
	let ligneDeBase = 196 + 20 + nom.taille * 0.9;
	for (const ligne of nom.lignes) {
		parties.push(texte(MARGE, Math.round(ligneDeBase), ligne, 'newsreader-500', nom.taille, COULEURS.encre));
		ligneDeBase += nom.taille * 1.04;
	}

	// The figures, one column each, under a rule.
	const n = carte.chiffres.length;
	const colonne = (UTILE - (n - 1) * ECART_COLONNES) / n;
	parties.push(`<rect x="${MARGE}" y="418" width="${UTILE}" height="1.5" fill="${COULEURS.filet}"/>`);
	const sources = placerSources(
		carte.chiffres.map((chiffre) => lisible(chiffre.source)),
		colonne
	);
	carte.chiffres.forEach((chiffre, i) => {
		const x = Math.round(MARGE + i * (colonne + ECART_COLONNES));
		const valeur = lisible(chiffre.valeur);
		if (i > 0) parties.push(`<rect x="${x - ECART_COLONNES / 2}" y="440" width="1.5" height="118" fill="${COULEURS.filet}"/>`);
		parties.push(
			texte(x, 466, tronquer(lisible(chiffre.libelle), 'inter-400', 23, colonne), 'inter-400', 23, COULEURS.doux),
			texte(x, 526, valeur, 'inter-600', taillePour(valeur, 'inter-600', 54, 30, colonne), COULEURS.encre),
			texte(x, 562, sources[i], 'inter-400', 19, COULEURS.pale)
		);
	});
	parties.push(texte(MARGE, 606, `datacommune.fr${carte.chemin}`, 'inter-400', 21, COULEURS.doux));

	return (
		`<svg xmlns="http://www.w3.org/2000/svg" width="${OG_LARGEUR}" height="${OG_HAUTEUR}" viewBox="0 0 ${OG_LARGEUR} ${OG_HAUTEUR}">` +
		`<rect width="${OG_LARGEUR}" height="${OG_HAUTEUR}" fill="${COULEURS.fond}"/>` +
		`<svg x="0" y="0" width="${OG_LARGEUR}" height="${OG_HAUTEUR}" overflow="hidden">${parties.join('')}</svg>` +
		`</svg>`
	);
}

/** The PNG of a card, and the time the rasterisation took (milliseconds, the WebAssembly start excluded). */
export async function rendreCarte(carte: CarteOg): Promise<{ png: Uint8Array; ms: number }> {
	await initialiser();
	polices ??= [inter400, inter400ext, inter600, inter600ext, newsreader500].map(octets);
	const debut = performance.now();
	const resvg = new Resvg(svgDeCarte(carte), {
		font: { fontBuffers: polices, loadSystemFonts: false, defaultFontFamily: 'Inter' },
		fitTo: { mode: 'original' }
	});
	try {
		const image = resvg.render();
		const png = image.asPng();
		image.free();
		return { png, ms: performance.now() - debut };
	} finally {
		resvg.free();
	}
}
