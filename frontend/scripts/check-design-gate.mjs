#!/usr/bin/env node
// Design gate of the phase 3 design contract (internal tracking,
// 03-UI-SPEC.md, « Garde de design »). Rewritten in 03-03 on the contract tokens.
//
// Ten rules, scanned on `src/**/*.svelte` and `src/**/*.css` (`src/app.css` is the token
// source and the only file allowed to write a colour in hexadecimal: it is not scanned):
//   site    hex-en-dur            hexadecimal colour literal
//   site    police-hors-token     font-family that is not a `--font-*` token declared in app.css
//   public  couleur-hors-palette  colour utility or var(--color-*) outside the contract tokens
//                                 (transition aliases included), rgb()/hsl() literals
//   public  typo-hors-echelle     font size outside the closed v4 scale (14 px floor), relative
//                                 size, or any `font:` shorthand (04.8, D-03, D-03c, D-28)
//   public  emoji-ui              emoji code point in markup
//   public  infobulle-native      `title` attribute on <button>, <a>, <summary>, [role=button]
//   public  perimetre-en-dur      hosted territory written in a screen (D-34): department
//                                 codes 44/85, their names, INSEE codes starting with them
//   public  graisse-hors-contrat  font weight other than 400, 500, 600 or inherit, Tailwind
//                                 utilities and style: directives included (04.8, D-03b)
//   public  rayon-hors-jeton      border radius other than 0, 50 %, inherit or a contract radius
//                                 token, Tailwind radii included (04.8, D-04, D-28)
//   public  espacement-hors-jeton padding, margin or gap other than 0, auto or a token of the
//                                 closed spacing list, Tailwind spacing included (04.8, D-04, D-28)
//
// Built on the pattern of `check-e2e-manifest.mjs`: pure functions exported for
// `qa-gates.test.mjs`, `isMain()` CLI guard, fail-closed (`process.exit(1)`) on any
// read/parse failure — never a silent `ok: true`.
//
// Scope note: `scripts/*.mjs` and the internal tracking are not scanned, so the patterns this file
// matches may appear here in clear text without self-tripping the gate.
//
// `DETTE` is NEVER hand-edited: `--maj-dette` regenerates it from the live repo and rewrites
// this file between the GENERATED-DETTE markers below.

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, relative } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';

const SCRIPT_DIR = fileURLToPath(new URL('.', import.meta.url));
const REPO_ROOT = join(SCRIPT_DIR, '..');
const SELF_PATH = fileURLToPath(import.meta.url);

// --- Public scope -----------------------------------------------------------------------
// The screens of the public site and the components they are built from. `(app)/`,
// `(auth)/` and `components/admin/` are deliberately outside: they keep the transition
// aliases until they are redesigned.
export const PORTEE_PUBLIQUE = [
	'src/routes/(marketing)/',
	'src/routes/+layout.svelte',
	'src/routes/+error.svelte',
	'src/lib/components/SiteHeader.svelte',
	'src/lib/components/SiteFooter.svelte',
	'src/lib/components/CommuneSearch.svelte',
	'src/lib/components/SearchResult.svelte',
	'src/lib/components/DatasetCard.svelte',
	// 04.8-01 amendment 6: the header search and the dataset detail are public screens too. The list
	// only grows (sentinel in qa-gates.test.mjs).
	'src/lib/components/RechercheEnTete.svelte',
	'src/lib/components/DatasetDetail.svelte',
	'src/lib/components/fiche/',
	'src/lib/components/adresse/',
	'src/lib/components/compare/',
	'src/lib/components/ds/'
];

// Token source: defines the palette, so it is the one file the gate does not scan.
export const SOURCE_DES_JETONS = 'src/app.css';
// Read once at load: the declared fonts come from it. Unreadable → throw (fail-closed).
const TEXTE_SOURCE_DES_JETONS = readFileSync(join(REPO_ROOT, SOURCE_DES_JETONS), 'utf8');

export function estDansPortee(cheminRelatif) {
	return PORTEE_PUBLIQUE.some(
		(prefixe) => cheminRelatif === prefixe || cheminRelatif.startsWith(prefixe)
	);
}

// --- Contract palette -------------------------------------------------------------------

// Colour tokens of the UI-SPEC table (`--color-<name>` in the @theme of app.css).
export const JETONS_CONTRAT = [
	'bg',
	'paper',
	'soft',
	'ink',
	'muted',
	'line',
	'line-strong',
	'accent',
	'accent-hover',
	'warn',
	'warn-bg',
	'focus',
	'danger',
	'chart',
	'dpe-a',
	'dpe-b',
	'dpe-c',
	'dpe-d',
	'dpe-e',
	'dpe-f',
	'dpe-g',
	'dpe-ink',
	'dpe-sur-a',
	'dpe-sur-g',
	// 04.8 (D-02, D-27): three new colours, same value everywhere, never overridden by .public-site.
	'panneau',
	'filet-pale',
	'vigilance-bg',
	// Charter of 06/11/2026: same value everywhere too.
	'accent-doux',
	'panne-fond',
	'neutre',
	'neutre-fond',
	'prix-encre',
	'prix-fond',
	'logement-encre',
	'logement-fond',
	'quotidien-encre',
	'quotidien-fond',
	'risques-encre',
	'risques-fond',
	'hero-fond',
	'hero-encre',
	'hero-encre-2',
	'hero-anneau',
	'hero-anneau-2',
	'hero-trait',
	'hero-action',
	'hero-sur-action',
	'hero-lien',
	'bande',
	'pied',
	'pied-encre',
	'pied-lien'
];

// Former names kept in app.css for the screens outside the public scope. Forbidden in it.
export const ALIAS_DE_TRANSITION = [
	'bronze',
	'bronze-dark',
	'bronze-light',
	'parchment',
	'marble',
	'marble-dark',
	'ink-muted',
	'terracotta',
	'gold',
	'stone-border',
	'column',
	'hairline',
	'success'
];

// Former tokens removed in 03-03: a class using them renders no colour at all.
const JETONS_RETIRES = [
	'sand',
	'night-soft',
	'gold-deep',
	'dev-bg',
	'dev-surface',
	'dev-border',
	'risk-none',
	'risk-low',
	'risk-mid',
	'risk-high',
	'chart-1',
	'chart-2',
	'chart-3',
	'chart-4',
	'chart-5',
	'chart-6',
	// Listed as an alias until 04.8 but never declared in app.css: it renders no colour either.
	'error'
];

const FAMILLES_TAILWIND = [
	'slate',
	'gray',
	'zinc',
	'neutral',
	'stone',
	'red',
	'orange',
	'amber',
	'yellow',
	'lime',
	'green',
	'emerald',
	'teal',
	'cyan',
	'sky',
	'blue',
	'indigo',
	'violet',
	'purple',
	'fuchsia',
	'pink',
	'rose'
];

const PREFIXES_COULEUR = [
	'text',
	'bg',
	'border',
	'border-[xytrblse]',
	'ring',
	'ring-offset',
	'inset-ring',
	'outline',
	'decoration',
	'divide',
	'fill',
	'stroke',
	'from',
	'via',
	'to',
	'accent',
	'caret',
	'placeholder',
	'shadow',
	'inset-shadow'
];

function echapper(nom) {
	return nom.replace(/[-]/g, '\\-');
}

// A colour utility whose colour is a real colour (Tailwind default, black/white, alias or
// removed token) but not a contract token. Unknown suffixes (`text-sm`, `border-2`) are not
// colours and never match.
const COULEURS_HORS_CONTRAT = [
	...[...ALIAS_DE_TRANSITION, ...JETONS_RETIRES].sort((a, b) => b.length - a.length).map(echapper),
	`(?:${FAMILLES_TAILWIND.join('|')})-\\d{2,3}`,
	'white',
	'black'
];
const CLASSE_HORS_PALETTE_REGEX = new RegExp(
	`(?<![\\w-])(?:${PREFIXES_COULEUR.join('|')})-(?:${COULEURS_HORS_CONTRAT.join('|')})(?![\\w-])`
);
const VAR_COULEUR_REGEX = /var\(\s*--color-([\w-]+)/g;
const COULEUR_LITTERALE_REGEX = /(?<![\w-])(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\(/;
const MOT_CLE_COULEUR_REGEX =
	/(?:^|[\s;{"'])(?:color|background|background-color|border-color|fill|stroke|outline-color)\s*[:=]\s*["']?(?:white|black)\b/;

function ligneVioleCouleurHorsPalette(ligne) {
	if (CLASSE_HORS_PALETTE_REGEX.test(ligne)) return true;
	if (COULEUR_LITTERALE_REGEX.test(ligne)) return true;
	if (MOT_CLE_COULEUR_REGEX.test(ligne)) return true;
	for (const match of ligne.matchAll(VAR_COULEUR_REGEX)) {
		if (!JETONS_CONTRAT.includes(match[1])) return true;
	}
	return false;
}

// --- Type scale ---------------------------------------------------------------------------

// Closed v4 scale of 04.8 (D-03, UI-SPEC § 2.2): 14 px floor. A size is a px of this scale or
// `inherit` — never em, rem, %, vw, a keyword or a var() (D-03c). The `font:` shorthand is
// refused whatever its value (D-28): it is forbidden, not parsed.
export const ECHELLE_TYPO_PX = [14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 38, 44, 48, 52, 64];

const TAILLE_NOMMEE_TAILWIND_REGEX = /(?<![\w-])text-(?:xs|sm|base|lg|xl|[2-9]xl)(?![\w-])/;
// Arbitrary value of a Tailwind utility, `[…]` or the variable shorthand `(…)` (= `[var(…)]`), split
// into its type hint (`length:`, `number:`…) and its value (Codex review 1, n° 5).
const ARBITRAIRE_TAILWIND = String.raw`(?:\[([^\]\s]+)\]|\(([^)\s]+)\))`;
function lireArbitraire(crochets, parentheses) {
	const brut = crochets ?? parentheses;
	const indice = brut.match(/^([a-z-]+):(.*)$/);
	return {
		indice: indice ? indice[1] : null,
		valeur: indice ? indice[2] : brut,
		variable: parentheses !== undefined
	};
}
// A number, signed included: Tailwind 4.2.2 reads `+13px` as a size and `+700` as a weight (Codex review 2,
// n° 4, checked with @tailwindcss/node). The closed lists then compare it without its `+`: a negative size
// or weight is never in them.
const NOMBRE_REGEX = /^[+-]?[\d.]/;
const sansSignePlus = (valeur) => valeur.replace(/^\+(?=[\d.])/, '');
// Math functions: Tailwind 4 infers a size or a weight from them, never a colour.
const FONCTION_MATH_REGEX = /^(?:calc|min|max|clamp|round|mod|rem|abs|sign|sin|cos|tan|asin|acos|atan|atan2|pow|sqrt|hypot|log|exp)\(/;
// `text-[…]` is a size (Tailwind 4.2.2, checked with @tailwindcss/node) when it carries a size hint, or
// without hint when it starts with a digit, is a math function or a font-size keyword; otherwise it is
// a colour (`text-[var(--color-x)]`, `text-(--color-x)`), left to couleur-hors-palette.
const TAILLE_ARBITRAIRE_REGEX = new RegExp(String.raw`(?<![\w-])text-${ARBITRAIRE_TAILWIND}`, 'g');
const INDICES_TAILLE = ['length', 'size', 'absolute-size', 'relative-size', 'percentage'];
const MOTS_CLES_TAILLE = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large', 'xxx-large', 'smaller', 'larger', 'math'];

function estTailleTailwind({ indice, valeur, variable }) {
	if (indice !== null) return INDICES_TAILLE.includes(indice);
	if (variable) return false;
	return NOMBRE_REGEX.test(valeur) || FONCTION_MATH_REGEX.test(valeur) || MOTS_CLES_TAILLE.includes(valeur);
}
const TAILLE_CSS_REGEX = /(?<![\w-])font-size\s*:\s*([^;}"'\n]*)/gi;
// Value of a Svelte attribute: double or single quotes, `{…}`, or unquoted up to a space or `>` (a
// trailing `/` closes the tag). Groups 1 to 4 of the patterns built on it, read by valeurLitterale.
const VALEUR_ATTRIBUT = String.raw`(?:"([^"]*)"|'([^']*)'|\{([^}]*)\}|([^\s"'=<>${'`'}]+))`;
const TAILLE_SVG_REGEX = new RegExp(String.raw`(?<![\w:-])font-size\s*=\s*${VALEUR_ATTRIBUT}`, 'gi');
const FORME_COURTE_FONT_REGEX = /(?<![\w-])font\s*:/i;

// A value without its closed CSS comments (Codex review 1, n° 7) nor its `!important`. An unclosed
// comment is kept: it makes the value unreadable, so it is refused.
function sansImportant(valeur) {
	return valeur
		.replace(/\/\*[\s\S]*?\*\//g, ' ')
		.trim()
		.replace(/\s*!important$/, '')
		.trim();
}

function estTailleDeLEchelle(valeur, { sansUnite = false } = {}) {
	const v = sansSignePlus(sansImportant(valeur));
	if (v === 'inherit') return true;
	const match = v.match(sansUnite ? /^(\d+)(?:px)?$/ : /^(\d+)px$/);
	return Boolean(match && ECHELLE_TYPO_PX.includes(Number(match[1])));
}

// A quoted or unquoted literal, or `{…}` holding a string or number literal. Any other expression, or
// text mixed with one (`14{unite}`), cannot be read statically: it is refused (fail-closed).
function valeurLitterale(guillemetsDoubles, guillemetsSimples, expression, sansGuillemets) {
	if (guillemetsDoubles !== undefined) return guillemetsDoubles;
	if (guillemetsSimples !== undefined) return guillemetsSimples;
	if (sansGuillemets !== undefined) return sansGuillemets.replace(/\/$/, '');
	const e = expression.trim();
	const chaine = e.match(/^(["'`])([^"'`]*)\1$/);
	if (chaine) return chaine[2];
	if (/^\d+(?:\.\d+)?$/.test(e)) return e;
	return null;
}

function ligneVioleTypoHorsEchelle(ligne) {
	if (FORME_COURTE_FONT_REGEX.test(ligne)) return true;
	if (TAILLE_NOMMEE_TAILWIND_REGEX.test(ligne)) return true;
	for (const [, crochets, parentheses] of ligne.matchAll(TAILLE_ARBITRAIRE_REGEX)) {
		const arbitraire = lireArbitraire(crochets, parentheses);
		if (!estTailleTailwind(arbitraire)) continue;
		// The `(…)` shorthand is a var(): never a size of the scale.
		if (arbitraire.variable || !estTailleDeLEchelle(arbitraire.valeur)) return true;
	}
	for (const [, valeur] of ligne.matchAll(TAILLE_CSS_REGEX)) {
		if (!estTailleDeLEchelle(valeur)) return true;
	}
	for (const [, ...groupes] of ligne.matchAll(TAILLE_SVG_REGEX)) {
		const valeur = valeurLitterale(...groupes);
		if (valeur === null || !estTailleDeLEchelle(valeur, { sansUnite: true })) return true;
	}
	return false;
}

// --- Font weights (D-03b, D-28, UI-SPEC § 2.6 n° 10) -----------------------------------------

// Locked weights: Inter 400/600 and Newsreader 400/500. The family × weight pair cannot be read in
// the source; the e2e quality fixture checks it on the computed style (amendment 8).
export const GRAISSES_CONTRAT = ['400', '500', '600'];

const GRAISSE_CSS_REGEX = /(?<![\w-])font-weight\s*:\s*([^;}"'\n]*)/gi;
const GRAISSE_ATTRIBUT_REGEX = new RegExp(String.raw`(?<![\w:-])font-weight\s*=\s*${VALEUR_ATTRIBUT}`, 'gi');
const GRAISSE_TAILWIND_NOMMEE_REGEX =
	/(?<![\w-])font-(?:thin|extralight|light|bold|extrabold|black)(?![\w-])/;
// `font-[…]` is a weight (Tailwind 4.2.2) with the `number:` or `weight:` hint, or without hint when it
// starts with a digit, is a math function or a var(), and always in the `(…)` shorthand without hint;
// otherwise it is a family (`font-[family-name:…]`, `font-[Inter]`).
const GRAISSE_TAILWIND_ARBITRAIRE_REGEX = new RegExp(String.raw`(?<![\w-])font-${ARBITRAIRE_TAILWIND}`, 'g');

function estGraisseTailwind({ indice, valeur, variable }) {
	if (indice !== null) return indice === 'number' || indice === 'weight';
	if (variable) return true;
	return NOMBRE_REGEX.test(valeur) || FONCTION_MATH_REGEX.test(valeur) || /^var\(/.test(valeur);
}

function estGraisseDuContrat(valeur) {
	const v = sansSignePlus(sansImportant(valeur));
	return v === 'inherit' || GRAISSES_CONTRAT.includes(v);
}

function ligneVioleGraisseHorsContrat(ligne) {
	if (GRAISSE_TAILWIND_NOMMEE_REGEX.test(ligne)) return true;
	for (const [, crochets, parentheses] of ligne.matchAll(GRAISSE_TAILWIND_ARBITRAIRE_REGEX)) {
		const arbitraire = lireArbitraire(crochets, parentheses);
		if (!estGraisseTailwind(arbitraire)) continue;
		if (arbitraire.variable || !GRAISSES_CONTRAT.includes(sansSignePlus(arbitraire.valeur))) return true;
	}
	for (const [, valeur] of ligne.matchAll(GRAISSE_CSS_REGEX)) {
		if (!estGraisseDuContrat(valeur)) return true;
	}
	for (const [, ...groupes] of ligne.matchAll(GRAISSE_ATTRIBUT_REGEX)) {
		const valeur = valeurLitterale(...groupes);
		if (valeur === null || !estGraisseDuContrat(valeur)) return true;
	}
	return false;
}

// --- Radii and spacings (04.8, D-04, D-28, UI-SPEC § 2.3, § 2.4, § 2.6 n° 3 and 4) -----------

// Closed lists, written name by name: no pattern on token names (D-04), so `--s9` or
// `--radius-inconnu` fail. `--radius-panel`, left in @theme, has no reader any more and is refused in
// the public scope since 04.8-09 (§ 2.4).
export const ESPACEMENTS_CONTRAT = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8', '--s-fin', '--s-demi'];
export const RAYONS_CONTRAT = [
	'--radius-panneau',
	'--radius-bande',
	'--radius-tuile',
	'--radius-ligne',
	'--radius-badge',
	'--radius-control'
];

// Value suffix of a utility: arbitrary `[…]`, variable shorthand `(…)` or a scale step.
const SUFFIXE_UTILITAIRE = String.raw`(\[[^\]\s]*\]|\([^)\s]*\)|[\w./]+)`;
const RAYON_CSS_REGEX = /(?<![\w-])border(?:-[a-z]+)*-radius\s*:\s*([^;}"'\n]*)/gi;
const RAYON_PROPRIETES = String.raw`border(?:-[a-z]+)*-radius`;
// `rounded` and `rounded-<side>`, each with an optional value (`rounded-t-lg`, `rounded-[4px]`).
const RAYON_TAILWIND_REGEX = new RegExp(
	String.raw`(?<![\w-])rounded(?:-(?:tl|tr|br|bl|ss|se|es|ee|t|r|b|l|s|e))?(?:-${SUFFIXE_UTILITAIRE})?(?![\w-])`,
	'g'
);
const ESPACEMENT_PROPRIETES = String.raw`(?:(?:padding|margin)(?:-[a-z]+)*|gap|row-gap|column-gap|grid-(?:row-|column-)?gap)`;
const ESPACEMENT_CSS_REGEX = new RegExp(String.raw`(?<![\w-])${ESPACEMENT_PROPRIETES}\s*:\s*([^;}"'\n]*)`, 'gi');
// Spacing utilities, negative form included: p, px, py, pt…, m, mx…, gap, gap-x, gap-y, space-x, space-y.
const ESPACEMENT_TAILWIND_REGEX = new RegExp(
	String.raw`(?<![\w-])-?(p[xytrblse]?|m[xytrblse]?|gap(?:-[xy])?|space-[xy])-${SUFFIXE_UTILITAIRE}(?![\w-])`,
	'g'
);
// A public file that redeclares a contract token would change its value behind the gate's back: in CSS
// (`--s4:`) or by a style: directive (`style:--s4`, whatever its value or form, Codex review 1, n° 3),
// which reaches this rule as the declaration `--s4: …;` (declarationDeDirective).
const regexRedeclaration = (noms) =>
	new RegExp(String.raw`(?<![\w-])(?:${noms.map(echapper).join('|')})(?![\w-])\s*:`);
const REDECLARATION_RAYON_REGEX = regexRedeclaration(RAYONS_CONTRAT);
const REDECLARATION_ESPACEMENT_REGEX = regexRedeclaration(ESPACEMENTS_CONTRAT);

// Top-level components of a value, split on whitespace outside parentheses.
function composantes(valeur) {
	const parties = [];
	let courante = '';
	let profondeur = 0;
	for (const c of valeur) {
		if (c === '(') profondeur++;
		if (c === ')') profondeur--;
		if (profondeur === 0 && /\s/.test(c)) {
			if (courante) parties.push(courante);
			courante = '';
			continue;
		}
		courante += c;
	}
	if (courante) parties.push(courante);
	return parties;
}

// `var(--x)`, without fallback, naming a token of `liste`.
function estVarDe(partie, liste) {
	const match = partie.match(/^var\(\s*(--[\w-]+)\s*\)$/);
	return Boolean(match && liste.includes(match[1]));
}

function estRayonDuContrat(valeur) {
	const parties = composantes(sansImportant(valeur));
	return (
		parties.length > 0 &&
		parties.every((p) => p === '0' || p === '50%' || p === 'inherit' || estVarDe(p, RAYONS_CONTRAT))
	);
}

function estEspacementDuContrat(valeur) {
	const parties = composantes(sansImportant(valeur));
	return (
		parties.length > 0 &&
		parties.every((p) => {
			if (p === '0' || p === 'auto' || estVarDe(p, ESPACEMENTS_CONTRAT)) return true;
			// The one admitted calc(): the negative of a token (UI-SPEC § 2.6, D-28).
			const negatif = p.match(/^calc\(\s*-1\s*\*\s*(var\([^()]*\))\s*\)$/);
			return Boolean(negatif && estVarDe(negatif[1], ESPACEMENTS_CONTRAT));
		})
	);
}

// Arbitrary value of a utility: `[var(--x)]` naming a token of `liste`.
function estSuffixeVarDe(suffixe, liste) {
	const match = suffixe.match(/^\[(var\([^\]]*\))\]$/);
	return Boolean(match && estVarDe(match[1], liste));
}

function ligneVioleRayonHorsJeton(ligne) {
	if (REDECLARATION_RAYON_REGEX.test(ligne)) return true;
	for (const [, suffixe] of ligne.matchAll(RAYON_TAILWIND_REGEX)) {
		if (suffixe === 'none' || suffixe === 'full') continue;
		if (suffixe === undefined || !estSuffixeVarDe(suffixe, RAYONS_CONTRAT)) return true;
	}
	for (const [, valeur] of ligne.matchAll(RAYON_CSS_REGEX)) {
		if (!estRayonDuContrat(valeur)) return true;
	}
	return false;
}

function ligneVioleEspacementHorsJeton(ligne) {
	if (REDECLARATION_ESPACEMENT_REGEX.test(ligne)) return true;
	for (const [, prefixe, suffixe] of ligne.matchAll(ESPACEMENT_TAILWIND_REGEX)) {
		if (suffixe === '0' || suffixe === 'auto') continue;
		if (prefixe.startsWith('space-') && suffixe === 'reverse') continue;
		if (!estSuffixeVarDe(suffixe, ESPACEMENTS_CONTRAT)) return true;
	}
	for (const [, valeur] of ligne.matchAll(ESPACEMENT_CSS_REGEX)) {
		if (!estEspacementDuContrat(valeur)) return true;
	}
	return false;
}

// --- Declarations written on several lines (D-28) ---------------------------------------------

// A declaration of one of these properties (size, weight, radius, spacing) whose value does not end
// on its line is glued back, up to its `;` or `}`, before the line rules read it. The finding stays
// on the property's line.
const NOMS_RECOLLES = String.raw`(?:font|font-size|font-weight|${RAYON_PROPRIETES}|${ESPACEMENT_PROPRIETES}|${[
	...RAYONS_CONTRAT,
	...ESPACEMENTS_CONTRAT
]
	.map(echapper)
	.join('|')})`;
const DECLARATION_OUVERTE_REGEX = new RegExp(String.raw`(?<![\w-])${NOMS_RECOLLES}\s*:[^;{}]*$`, 'i');
// The same for a name alone at the end of its line whose colon opens a following line: a comment
// between them, once blanked, leaves exactly this (Codex review 2, n° 5).
const NOM_EN_FIN_DE_LIGNE_REGEX = new RegExp(String.raw`(?<![\w-])${NOMS_RECOLLES}\s*$`, 'i');

function deuxPointsALaLigneSuivante(lignes, i) {
	const suivante = lignes.slice(i + 1).find((ligne) => ligne.trim() !== '');
	return suivante !== undefined && suivante.trimStart().startsWith(':');
}

function recollerDeclarations(lignes) {
	return lignes.map((ligne, i) => {
		const ouverte =
			DECLARATION_OUVERTE_REGEX.test(ligne) ||
			(NOM_EN_FIN_DE_LIGNE_REGEX.test(ligne) && deuxPointsALaLigneSuivante(lignes, i));
		if (!ouverte) return ligne;
		let texte = ligne;
		for (let j = i + 1; j < lignes.length; j++) {
			const fin = lignes[j].search(/[;}]/);
			if (fin !== -1) {
				texte += ` ${lignes[j].slice(0, fin + 1)}`;
				break;
			}
			texte += ` ${lignes[j]}`;
		}
		return texte;
	});
}

// --- Normalisation of the CSS text, before any line is read (Codex review 2, n° 5) ------------

/**
 * CSS text normalised once, length and line breaks kept, so offsets and line numbers stay exact:
 * - closed comments blanked: every character but the line breaks becomes a space. A `/*` inside a
 *   string opens no comment; an unclosed comment is kept, so what it holds still reaches the rules
 *   (never a silent pass) (Codex review 2, n° 5);
 * - ASCII capitals lowered: CSS names, keywords and functions are case-insensitive (Codex review 2,
 *   n° 6). A string, a custom property name (`--Nom`) and the Tailwind classes of an `@apply` keep
 *   their case.
 * @param {string} css
 */
export function normaliserCss(css) {
	let sortie = '';
	let i = 0;
	while (i < css.length) {
		const c = css[i];
		const identifiantAvant = i > 0 && /[\w-]/.test(css[i - 1]);
		if (c === '-' && css[i + 1] === '-' && !identifiantAvant) {
			const nom = css.slice(i).match(/^--[\w-]*/)[0];
			sortie += nom;
			i += nom.length;
			continue;
		}
		if (c === '@' && /^@apply(?![\w-])/i.test(css.slice(i, i + 7))) {
			const fin = css.slice(i).search(/[;}]/);
			const longueur = fin === -1 ? css.length - i : fin;
			sortie += css.slice(i, i + longueur);
			i += longueur;
			continue;
		}
		if (c === '"' || c === "'") {
			// A string runs to its closing quote or, unclosed, to the end of its line.
			let j = i + 1;
			while (j < css.length && css[j] !== c && css[j] !== '\n') j += css[j] === '\\' ? 2 : 1;
			const fin = Math.min(css[j] === c ? j + 1 : j, css.length);
			sortie += css.slice(i, fin);
			i = fin;
			continue;
		}
		if (c === '/' && css[i + 1] === '*') {
			const fin = css.indexOf('*/', i + 2);
			if (fin === -1) {
				sortie += css.slice(i);
				break;
			}
			sortie += css.slice(i, fin + 2).replace(/[^\r\n]/g, ' ');
			i = fin + 2;
			continue;
		}
		sortie += c >= 'A' && c <= 'Z' ? c.toLowerCase() : c;
		i++;
	}
	return sortie;
}

/** `contenu` with each `[debut, fin)` range passed through `normaliserCss`. */
function normaliserPlages(contenu, plages) {
	let sortie = '';
	let position = 0;
	for (const [debut, fin] of [...plages].sort((a, b) => a[0] - b[0])) {
		if (debut < position) continue;
		sortie += contenu.slice(position, debut) + normaliserCss(contenu.slice(debut, fin));
		position = fin;
	}
	return sortie + contenu.slice(position);
}

// Presentation attributes the rules read (`<text font-size="14">`, `<rect fill="white">`): their name and
// their value are CSS too.
const ATTRIBUTS_DE_PRESENTATION = new Set([
	'font-family',
	'font-size',
	'font-weight',
	'color',
	'fill',
	'stroke',
	'background',
	'background-color',
	'border-color',
	'outline-color'
]);

/**
 * The CSS of a component: its `<style>` blocks (top-level or nested), the text of its `style`
 * attributes and its presentation attributes. Script and markup text are not CSS: they are left as
 * they are.
 */
function plagesCssDeSvelte(ast) {
	const plages = [];
	if (ast.css) plages.push([ast.css.content.start, ast.css.content.end]);
	for (const element of noeudsDeType(ast.fragment, 'RegularElement')) {
		if (element.name.toLowerCase() !== 'style') continue;
		for (const texte of element.fragment.nodes) if (texte.type === 'Text') plages.push([texte.start, texte.end]);
	}
	for (const attribut of noeudsDeType(ast.fragment, 'Attribute')) {
		const nom = attribut.name.toLowerCase();
		const presentation = ATTRIBUTS_DE_PRESENTATION.has(nom);
		if (nom !== 'style' && !presentation) continue;
		if (presentation) plages.push([attribut.start, attribut.start + attribut.name.length]);
		if (!Array.isArray(attribut.value)) continue;
		for (const partie of attribut.value) if (partie.type === 'Text') plages.push([partie.start, partie.end]);
	}
	return plages;
}

// --- style: directives, read by the Svelte parser (Codex review 2, n° 2 and 3) ------------------

// A style: directive is read by the parser of the locked Svelte, not by a pattern: its value quoted,
// unquoted, in braces or over several lines, its modifier and its shorthand form are read as the
// compiler reads them. Each directive then becomes the CSS declaration it sets (`style:padding=10px`
// → `padding: 10px;`), judged by the same line rules as any declaration, on the directive's line. A
// value that is not a literal (expression, shorthand, text mixed with an expression) cannot be read
// statically: it becomes VALEUR_NON_LITTERALE, which no closed list admits (fail-closed).
const VALEUR_NON_LITTERALE = '__valeur-non-litterale__';
// The rules a property reaches: the directive's value is not otherwise read by them.
const REGLES_DE_DIRECTIVE = new Set([
	'typo-hors-echelle',
	'graisse-hors-contrat',
	'rayon-hors-jeton',
	'espacement-hors-jeton',
	'police-hors-token',
	'couleur-hors-palette'
]);

/** Svelte AST of a component; a file the parser refuses fails closed, it is never skipped. */
function analyserSvelte(cheminRelatif, contenu) {
	try {
		return parse(contenu, { modern: true });
	} catch (err) {
		throw new Error(`${cheminRelatif}: analyse Svelte impossible (${err.message.split('\n')[0]}) — fail-closed`);
	}
}

/** Every node of `type` under `noeud` (the template fragment: elements, blocks, snippets). */
function noeudsDeType(noeud, type, sortie = []) {
	if (Array.isArray(noeud)) {
		for (const enfant of noeud) noeudsDeType(enfant, type, sortie);
	} else if (noeud && typeof noeud === 'object') {
		if (noeud.type === type) sortie.push(noeud);
		for (const [cle, valeur] of Object.entries(noeud)) {
			if (cle !== 'parent' && cle !== 'metadata') noeudsDeType(valeur, type, sortie);
		}
	}
	return sortie;
}

// A string or number literal, or a template literal without expression; anything else is `null`.
function litteralDExpression(expression) {
	if (expression.type === 'Literal' && ['string', 'number'].includes(typeof expression.value)) {
		return String(expression.value);
	}
	if (expression.type === 'TemplateLiteral' && expression.expressions.length === 0) {
		return expression.quasis[0].value.cooked;
	}
	return null;
}

function valeurDeDirective(valeur) {
	if (valeur === true) return null;
	const parties = Array.isArray(valeur) ? valeur : [valeur];
	if (parties.length === 1 && parties[0].type === 'ExpressionTag') return litteralDExpression(parties[0].expression);
	if (parties.every((partie) => partie.type === 'Text')) return parties.map((partie) => partie.data).join('');
	return null;
}

/**
 * The CSS declaration a style: directive sets. A standard property name is case-insensitive (the
 * compiler lowers it); a custom property keeps its case.
 */
function declarationDeDirective({ name, value }) {
	const nom = name.startsWith('--') ? name : name.toLowerCase();
	const valeur = valeurDeDirective(value);
	return `${nom}: ${valeur === null ? VALEUR_NON_LITTERALE : normaliserCss(valeur)};`;
}

/** Line index (0-based) of each offset, by the newlines of `contenu`. */
function indexeurDeLignes(contenu) {
	const debuts = [0];
	for (let i = 0; i < contenu.length; i++) if (contenu[i] === '\n') debuts.push(i + 1);
	return (position) => {
		let bas = 0;
		let haut = debuts.length - 1;
		while (bas < haut) {
			const milieu = (bas + haut + 1) >> 1;
			if (debuts[milieu] <= position) bas = milieu;
			else haut = milieu - 1;
		}
		return bas;
	};
}

/** `Map<index de ligne, Set<regle>>` of the style: directives of a component, for `regles`. */
function constatsDeDirectives(ast, contenu, regles) {
	const constats = new Map();
	const ligneDe = indexeurDeLignes(contenu);
	for (const directive of noeudsDeType(ast.fragment, 'StyleDirective')) {
		const declaration = declarationDeDirective(directive);
		for (const regle of regles) {
			if (!REGLES_DE_DIRECTIVE.has(regle.nom) || !regle.ligne(declaration)) continue;
			const ligne = ligneDe(directive.start);
			if (!constats.has(ligne)) constats.set(ligne, new Set());
			constats.get(ligne).add(regle.nom);
		}
	}
	return constats;
}

// --- Fonts --------------------------------------------------------------------------------

// Font tokens declared in the token source (`--font-<name>:` in app.css). A `var(--font-x)` that
// names none of them resolves to nothing: the declaration is dropped and the text falls back to
// whatever face it inherits, silently (03-03 review). Read once at load; an unreadable token
// source throws, so the gate fails closed.
export const POLICES_DECLAREES = new Set(
	[...TEXTE_SOURCE_DES_JETONS.matchAll(/--font-([\w-]+)\s*:/g)].map((match) => match[1])
);

function estValeurFontFamilleValide(valeur) {
	const v = valeur.trim().replace(/;$/, '').trim();
	// The CSS text reaches this rule lowered (normaliserCss): `currentColor` arrives as `currentcolor`.
	if (v === 'inherit' || v.toLowerCase() === 'currentcolor') return true;
	const token = v.match(/^var\(\s*--font-([\w-]+)\s*\)$/);
	return Boolean(token && POLICES_DECLAREES.has(token[1]));
}

const POLICE_ATTRIBUT_REGEX = new RegExp(String.raw`font-family\s*=\s*${VALEUR_ATTRIBUT}`);

function ligneViolePoliceHorsToken(ligne) {
	const cssMatch = ligne.match(/font-family\s*:\s*([^;]+);?/);
	if (cssMatch) return !estValeurFontFamilleValide(cssMatch[1]);
	const svgMatch = ligne.match(POLICE_ATTRIBUT_REGEX);
	// A value in braces (`font-family={police}`) is not judged by this rule, as before; a literal is,
	// quoted or not.
	if (!svgMatch || svgMatch[3] !== undefined) return false;
	return !estValeurFontFamilleValide(valeurLitterale(...svgMatch.slice(1)));
}

// --- Hexadecimal colours ------------------------------------------------------------------

// 3/4/6/8-digit literal, never preceded by an alphanumeric, a dash or `&` (an HTML entity
// such as &#9888; is not a colour).
const HEX_EN_DUR_REGEX = /(?<![0-9a-zA-Z_&-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b/;

// --- Emoji ----------------------------------------------------------------------------------

// Code point ranges, not copied glyphs. Letterlike symbols (2100-214F) and arrows
// (2190-21FF) are typographic, not pictographs.
const EMOJI_CODEPOINT_RANGES = [
	[0x1f300, 0x1f5ff],
	[0x1f600, 0x1f64f],
	[0x1f680, 0x1f6ff],
	[0x1f900, 0x1f9ff],
	[0x1fa70, 0x1faff],
	[0x1f1e6, 0x1f1ff],
	[0x2600, 0x26ff],
	[0x2700, 0x27bf]
];

function construireRegexEmoji() {
	const classe = EMOJI_CODEPOINT_RANGES.map(
		([debut, fin]) => `\\u{${debut.toString(16)}}-\\u{${fin.toString(16)}}`
	).join('');
	return new RegExp(`[${classe}]`, 'u');
}

const EMOJI_UI_REGEX = construireRegexEmoji();

// --- Hosted territory written in a screen (D-34) -------------------------------------------

const PERIMETRE_REGEXES = [
	// A department code alone in a string literal: '44', "85", `44` — but not an attribute
	// value such as width="44" (a size, not a territory).
	/(?<![\w-]=\s*)(['"`])(?:44|85)\1/,
	// Pairs: « 44 & 85 », 44/85, 44|85, 44, 85, 44 et 85.
	/(?<![\d.])(?:44|85)\s*(?:&amp;|&|\/|\||,|\bet\b)\s*(?:44|85)(?![\d.])/,
	// Prefix patterns: ^(44|85), ^44.
	/\^\(?\s*(?:44|85)/,
	// Department names.
	/Loire[-\s]Atlantique|Vend[ée]e(?![a-zà-ÿ])/i,
	// An INSEE or postal code of those departments written after a quote or a slash.
	/['"`/=](?:44|85)\d{3}(?!\d)/,
	// A department parameter set to one of them.
	/(?:departement|département|code_departement|dept?)\s*[=:]\s*['"]?(?:44|85)(?!\d)/i
];

function ligneViolePerimetreEnDur(ligne) {
	return PERIMETRE_REGEXES.some((regex) => regex.test(ligne));
}

// --- Native tooltip on an interactive element -----------------------------------------------

const BALISES_INTERACTIVES = new Set(['button', 'a', 'summary']);

// Script, style and comment blocks are not rendered markup: they are blanked (newlines kept,
// so line numbers stay exact) before the tag walk. Without this, a comparison such as `i<n`
// in a script would open a pseudo-tag and could swallow the real tags that follow.
function masquerBlocsNonRendus(contenu) {
	const blanchir = (bloc) => bloc.replace(/[^\n]/g, ' ');
	return contenu
		.replace(/<script\b[\s\S]*?<\/script>/gi, blanchir)
		.replace(/<style\b[\s\S]*?<\/style>/gi, blanchir)
		.replace(/<!--[\s\S]*?-->/g, blanchir);
}

/**
 * Walk the opening tags of a Svelte file and return the 1-based line of every `title`
 * attribute (`title=`, `title={...}` or the `{title}` shorthand) carried by <button>, <a>,
 * <summary> or an element with role="button". Braces and quotes are tracked so that an arrow
 * function (`=>`) inside an attribute does not end the tag.
 * @param {string} contenu
 * @returns {number[]}
 */
export function lignesInfobulleNative(source) {
	const contenu = masquerBlocsNonRendus(source);
	const lignes = [];
	const ouverture = /<([a-zA-Z][\w:-]*)(?=[\s/>])/g;
	let match;
	while ((match = ouverture.exec(contenu)) !== null) {
		const nom = match[1].toLowerCase();
		let i = match.index + match[0].length;
		let profondeur = 0;
		let guillemet = null;
		const attributs = [];
		let courant = '';
		let debutCourant = i;
		const pousser = () => {
			if (courant.trim()) attributs.push({ texte: courant.trim(), index: debutCourant });
			courant = '';
		};
		for (; i < contenu.length; i++) {
			const c = contenu[i];
			if (guillemet) {
				courant += c;
				if (c === guillemet) guillemet = null;
				continue;
			}
			if (c === '"' || c === "'" || c === '`') {
				guillemet = c;
				courant += c;
				continue;
			}
			if (c === '{') profondeur++;
			if (c === '}') profondeur--;
			if (profondeur === 0 && c === '>') break;
			if (profondeur === 0 && /\s/.test(c)) {
				pousser();
				debutCourant = i + 1;
				continue;
			}
			if (!courant) debutCourant = i;
			courant += c;
		}
		pousser();
		ouverture.lastIndex = i;
		const roleBouton = attributs.some((a) => /^role\s*=\s*["']?button["']?$/i.test(a.texte));
		if (!BALISES_INTERACTIVES.has(nom) && !roleBouton) continue;
		for (const attribut of attributs) {
			if (/^title\s*=/i.test(attribut.texte) || /^\{\s*title\s*\}$/.test(attribut.texte)) {
				lignes.push(contenu.slice(0, attribut.index).split('\n').length);
			}
		}
	}
	return lignes;
}

// --- Rules --------------------------------------------------------------------------------

export const REGLES = [
	{ nom: 'hex-en-dur', portee: 'site', ligne: (l) => HEX_EN_DUR_REGEX.test(l) },
	{ nom: 'police-hors-token', portee: 'site', ligne: ligneViolePoliceHorsToken },
	{ nom: 'couleur-hors-palette', portee: 'public', ligne: ligneVioleCouleurHorsPalette },
	{ nom: 'typo-hors-echelle', portee: 'public', ligne: ligneVioleTypoHorsEchelle },
	{ nom: 'emoji-ui', portee: 'public', ligne: (l) => EMOJI_UI_REGEX.test(l) },
	{ nom: 'infobulle-native', portee: 'public', fichier: lignesInfobulleNative },
	{ nom: 'perimetre-en-dur', portee: 'public', ligne: ligneViolePerimetreEnDur },
	{ nom: 'graisse-hors-contrat', portee: 'public', ligne: ligneVioleGraisseHorsContrat },
	{ nom: 'rayon-hors-jeton', portee: 'public', ligne: ligneVioleRayonHorsJeton },
	{ nom: 'espacement-hors-jeton', portee: 'public', ligne: ligneVioleEspacementHorsJeton }
];

// No permanent exception. Reserved for a future EXPLICIT and reviewed exception — never
// filled silently by this file (sentinel in qa-gates.test.mjs).
export const ALLOWLIST = [];

/**
 * Scan an already-read file. Returns `[{ regle, ligne }]` — never the offending value: the
 * rule name and the line number locate the problem. Public rules apply only when
 * `cheminRelatif` falls under `PORTEE_PUBLIQUE`.
 * @param {string} cheminRelatif path relative to the frontend root
 * @param {string} contenu file content
 * @returns {{ regle: string, ligne: number }[]}
 */
export function scannerFichier(cheminRelatif, contenu) {
	const constats = [];
	const dansPortee = estDansPortee(cheminRelatif);
	const regles = REGLES.filter((regle) => regle.portee === 'site' || dansPortee);
	// Normalised once, before the split into lines: a stylesheet is CSS as a whole; in a component, its
	// <style> blocks and style attributes are, and its style: directives are read by the parser.
	let texte = normaliserCss(contenu);
	let parDirective = new Map();
	if (cheminRelatif.endsWith('.svelte')) {
		const ast = analyserSvelte(cheminRelatif, contenu);
		texte = normaliserPlages(contenu, plagesCssDeSvelte(ast));
		parDirective = constatsDeDirectives(ast, contenu, regles);
	}
	const lignes = recollerDeclarations(texte.split(/\r?\n/));
	for (let i = 0; i < lignes.length; i++) {
		for (const regle of regles) {
			if (!regle.ligne) continue;
			if (regle.ligne(lignes[i]) || parDirective.get(i)?.has(regle.nom)) constats.push({ regle: regle.nom, ligne: i + 1 });
		}
	}
	for (const regle of regles) {
		if (!regle.fichier) continue;
		for (const ligne of regle.fichier(contenu)) constats.push({ regle: regle.nom, ligne });
	}
	return constats.sort((a, b) => a.ligne - b.ligne);
}

/**
 * Recursive walk of `racine/src`: relative paths (posix separators) of every `.svelte` and
 * `.css` file except the token source. Empty array if the folder is missing, never a throw.
 * @param {string} racine frontend root (contains `src/`)
 * @returns {string[]}
 */
export function collecterFichiers(racine) {
	const srcDir = join(racine, 'src');
	const out = [];
	const pile = [srcDir];
	while (pile.length > 0) {
		const dir = pile.pop();
		let entries;
		try {
			entries = readdirSync(dir, { withFileTypes: true });
		} catch (err) {
			if (err && err.code === 'ENOENT') continue;
			throw err;
		}
		for (const entry of entries) {
			const full = join(dir, entry.name);
			if (entry.isDirectory()) {
				pile.push(full);
				continue;
			}
			if (entry.isFile() && /\.(svelte|css)$/.test(entry.name)) {
				const rel = relative(racine, full).replace(/\\/g, '/');
				if (rel !== SOURCE_DES_JETONS) out.push(rel);
			}
		}
	}
	return out.sort();
}

/**
 * Compare the current findings (`{ [chemin]: { [regle]: compte } }`) with the recorded debt.
 * Fails if a file absent from `dette` carries a finding, or if a file of `dette` carries more
 * findings than recorded for a rule. A debt file gone from disk is reported in `soldees`.
 * @param {Record<string, Record<string, number>>} constatsParFichier
 * @param {Record<string, Record<string, number>>} dette
 * @returns {{ ok: boolean, erreurs: string[], soldees: string[] }}
 */
export function evaluerDette(constatsParFichier, dette) {
	const erreurs = [];
	for (const [chemin, comptesParRegle] of Object.entries(constatsParFichier)) {
		const detteFichier = dette[chemin];
		for (const [regle, compte] of Object.entries(comptesParRegle)) {
			const compteDette = detteFichier?.[regle] ?? 0;
			if (compte > compteDette) {
				erreurs.push(
					`${chemin}: ${compte} constat(s) "${regle}", dette enregistree = ${compteDette}`
				);
			}
		}
	}
	const soldees = Object.keys(dette).filter((chemin) => !(chemin in constatsParFichier));
	return { ok: erreurs.length === 0, erreurs, soldees };
}

function scannerEtAgreger(racine, chemins) {
	const constatsParFichier = {};
	for (const chemin of chemins) {
		let contenu;
		try {
			contenu = readFileSync(join(racine, chemin), 'utf8');
		} catch (err) {
			return { erreur: `${chemin}: lecture impossible (${err.message}) — fail-closed` };
		}
		let constats;
		try {
			constats = scannerFichier(chemin, contenu);
		} catch (err) {
			return { erreur: err.message };
		}
		if (constats.length === 0) continue;
		const comptes = {};
		for (const { regle } of constats) comptes[regle] = (comptes[regle] ?? 0) + 1;
		constatsParFichier[chemin] = comptes;
	}
	return constatsParFichier;
}

/**
 * Full gate. Without `cheminsCibles`: scans every file of `racine/src` and compares with
 * `dette`. With `cheminsCibles`: strict mode, `dette` is ignored — used by the screen plans
 * to prove their own files are clean.
 * @param {{ racine: string, dette?: Record<string, Record<string, number>>, cheminsCibles?: string[] }} opts
 * @returns {{ ok: boolean, erreurs: string[] }}
 */
export function runDesignGate({ racine, dette = DETTE, cheminsCibles } = {}) {
	const modeStrict = Array.isArray(cheminsCibles) && cheminsCibles.length > 0;
	const cibles = modeStrict ? cheminsCibles : collecterFichiers(racine);

	const resultat = scannerEtAgreger(racine, cibles);
	if (resultat.erreur) return { ok: false, erreurs: [resultat.erreur] };

	if (modeStrict) {
		const erreurs = [];
		for (const [chemin, comptes] of Object.entries(resultat)) {
			for (const [regle, compte] of Object.entries(comptes)) {
				erreurs.push(`${chemin}: ${compte} constat(s) "${regle}" (mode strict, dette ignoree)`);
			}
		}
		return { ok: erreurs.length === 0, erreurs };
	}

	const { ok, erreurs } = evaluerDette(resultat, dette);
	return { ok, erreurs };
}

// --- DETTE — public files not yet redesigned, NEVER hand-edited ---------------------------
// Regenerated by `node scripts/check-design-gate.mjs --maj-dette`. Each screen plan of phase 3
// (04 to 11) and the phase 4 plans redesign their files and regenerate this inventory, which
// only shrinks: a regression is fixed, never recorded. No file created in phase 3 may appear
// here for the seven historic rules (sentinel in qa-gates.test.mjs). 04.8-01 froze here the
// initial debt of rayon-hors-jeton and espacement-hors-jeton, measured on every public file in the
// commit that added them to REGLES; each 04.8 slice brings its own files back to zero (D-29). The
// ratchet below (detteDeReference) holds every count under its git reference, and --maj-dette
// refuses to write a rise.
// GENERATED-DETTE-START
export const DETTE = {
	"src/routes/(marketing)/couverture/+page.svelte": { "perimetre-en-dur": 3 }
};
// GENERATED-DETTE-END

export function serialiserDette(dette) {
	const chemins = Object.keys(dette).sort();
	if (chemins.length === 0) return 'export const DETTE = {};';
	const lignes = chemins.map((chemin) => {
		const comptes = dette[chemin];
		const reglesTriees = Object.keys(comptes).sort();
		const interieur = reglesTriees
			.map((regle) => `${JSON.stringify(regle)}: ${comptes[regle]}`)
			.join(', ');
		return `\t${JSON.stringify(chemin)}: { ${interieur} }`;
	});
	return `export const DETTE = {\n${lignes.join(',\n')}\n};`;
}

/**
 * Measured debt of the working tree: `{ [chemin]: { [regle]: compte } }`, every file of `racine/src`.
 * @param {string} racine frontend root
 */
export function mesurerDette(racine) {
	const resultat = scannerEtAgreger(racine, collecterFichiers(racine));
	if (resultat.erreur) throw new Error(resultat.erreur);
	return resultat;
}

// --- Debt ratchet (04.8-01, D-29, UI-SPEC § 2.6) ------------------------------------------
// The debt is compared with a reference read from git, out of reach of the commit under test: a
// count raised by hand in DETTE, even in the same commit, fails. The reference of a (file, rule)
// couple is the minimum of its count at the base and in every commit of `<base>..HEAD` that touches
// this script and carries the rule in its REGLES (absent entry = 0): a solded debt never comes back.
// A commit without the rule (before its freeze, or on an older branch merged since) is not read for it.
//   base    pull request  origin/$GITHUB_BASE_REF
//           push          the SHA before the push, passed by the workflow in DESIGN_GATE_PUSH_BEFORE
//           local         git merge-base HEAD master, or HEAD~1 when that point is HEAD (work on master)
//           root          HEAD has no parent (a repository published without history, D-135): on
//                         master locally, or on its first push (null SHA before it), the base is HEAD
//                         itself. Its committed DETTE is the ceiling of the working tree; nothing
//                         older exists to compare with. A shallow clone is refused before this point.
//   freeze  a rule in REGLES at the base: the base itself. Otherwise the first commit of
//           `git log --reverse` where it is in REGLES; its base count is then its measure on the
//           base files, read by `git cat-file <base>:<file>` without checkout (bootstrap of
//           04.8-01). The freeze is reported (`gels`); the reference does not depend on it.
// No git, no repository, a shallow clone or a missing base throws: never a silent pass.

export const VARIABLE_BASE_PUSH = 'DESIGN_GATE_PUSH_BEFORE';
const CHEMIN_SCRIPT = 'scripts/check-design-gate.mjs';
const MARQUEUR_DEBUT_REGEX = /^\/\/ GENERATED-DETTE-START\r?$/m;
const MARQUEUR_FIN_REGEX = /^\/\/ GENERATED-DETTE-END\r?$/m;
const REGLES_BLOC_REGEX = /^export const REGLES = \[([\s\S]*?)^\];/m;
const PORTEE_BLOC_REGEX = /^export const PORTEE_PUBLIQUE = \[([\s\S]*?)^\];/m;
const NUL = String.fromCharCode(0);

function executerGit(racine, env, args, entree) {
	try {
		return execFileSync('git', args, {
			cwd: racine,
			env,
			input: entree,
			// With an input (cat-file --batch) the output stays a Buffer: blob sizes are in bytes.
			...(entree === undefined ? { encoding: 'utf8' } : {}),
			maxBuffer: 1 << 29,
			stdio: ['pipe', 'pipe', 'pipe'],
			windowsHide: true
		});
	} catch (err) {
		const detail = err.code === 'ENOENT' ? 'git introuvable' : String(err.stderr ?? '').trim() || err.message;
		const erreur = new Error(`git ${args.join(' ')} : ${detail}`);
		erreur.status = err.status;
		throw erreur;
	}
}

/** `true`/`false` from a git command whose exit 1 means "no"; any other failure throws. */
function gitOuiNon(racine, env, args) {
	try {
		executerGit(racine, env, args);
		return true;
	} catch (err) {
		if (err.status === 1) return false;
		throw err;
	}
}

/**
 * Commit the ratchet compares with, by event (see above).
 * @param {{ racine?: string, env?: Record<string, string|undefined> }} opts
 * @returns {{ base: string, origine: 'pr'|'push'|'local'|'local-parent'|'racine' }}
 */
export function resoudreBaseDeReference({ racine = REPO_ROOT, env = process.env } = {}) {
	const git = (...args) => executerGit(racine, env, args).trim();
	let dansDepot;
	try {
		dansDepot = git('rev-parse', '--is-inside-work-tree') === 'true';
	} catch (err) {
		throw new Error(`pas de depot git sous ${racine} (${err.message})`);
	}
	if (!dansDepot) throw new Error(`pas de depot git sous ${racine} (hors de l'arbre de travail)`);
	if (git('rev-parse', '--is-shallow-repository') === 'true') {
		throw new Error('historique git superficiel (clone a profondeur limitee) : fetch-depth: 0 requis');
	}
	const existe = (ref) => gitOuiNon(racine, env, ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`]);
	const commit = (ref, quoi) => {
		if (!existe(ref)) throw new Error(`${quoi} introuvable : ${ref}`);
		return git('rev-parse', '--verify', `${ref}^{commit}`);
	};
	const head = commit('HEAD', 'HEAD');
	const sansParent = !existe('HEAD^');
	const evenement = env.GITHUB_EVENT_NAME ?? '';

	if (env.GITHUB_BASE_REF) {
		return { base: commit(`origin/${env.GITHUB_BASE_REF}`, 'branche de base de la PR'), origine: 'pr' };
	}
	if (evenement.startsWith('pull_request')) throw new Error(`evenement ${evenement} sans GITHUB_BASE_REF`);

	const avantPush = env[VARIABLE_BASE_PUSH];
	if (avantPush) {
		if (/^0+$/.test(avantPush)) {
			if (sansParent) return { base: head, origine: 'racine' };
			throw new Error(`${VARIABLE_BASE_PUSH} nul (push sans commit precedent) : aucune base de reference`);
		}
		const base = commit(avantPush, `commit d'avant le push (${VARIABLE_BASE_PUSH})`);
		if (!gitOuiNon(racine, env, ['merge-base', '--is-ancestor', base, head])) {
			throw new Error(`${VARIABLE_BASE_PUSH} ${base} n'est pas un ancetre de HEAD`);
		}
		return { base, origine: 'push' };
	}
	if (evenement === 'push') throw new Error(`push sans ${VARIABLE_BASE_PUSH}`);

	const principale = ['master', 'origin/master'].find(existe);
	if (!principale) throw new Error('branche master introuvable (ni master ni origin/master)');
	const divergence = git('merge-base', 'HEAD', principale);
	if (divergence !== head) return { base: divergence, origine: 'local' };
	if (sansParent) return { base: head, origine: 'racine' };
	return { base: commit('HEAD~1', 'HEAD~1 (travail sur master, merge-base = HEAD)'), origine: 'local-parent' };
}

/**
 * Read `rev:path` blobs in one `git cat-file --batch`. `null` for a missing object.
 * @returns {(string|null)[]}
 */
function lireObjets(racine, env, specs) {
	if (specs.length === 0) return [];
	const sortie = executerGit(racine, env, ['cat-file', '--batch'], `${specs.join('\n')}\n`);
	const contenus = [];
	let pos = 0;
	for (const spec of specs) {
		const finEntete = sortie.indexOf(0x0a, pos);
		if (finEntete === -1) throw new Error(`git cat-file : sortie tronquee pour ${spec}`);
		const entete = sortie.toString('utf8', pos, finEntete);
		if (entete.endsWith(' missing')) {
			contenus.push(null);
			pos = finEntete + 1;
			continue;
		}
		const [, type, taille] = entete.split(' ');
		if (type !== 'blob') throw new Error(`git cat-file : ${spec} n'est pas un fichier (${entete})`);
		const fin = finEntete + 1 + Number(taille);
		contenus.push(sortie.toString('utf8', finEntete + 1, fin));
		pos = fin + 1;
	}
	return contenus;
}

/**
 * The REGLES names, the DETTE block and the PORTEE_PUBLIQUE entries of a version of this script.
 * Throws if REGLES or DETTE is unreadable; `portee` is `null` when its block is absent.
 * @param {string} source content of check-design-gate.mjs
 * @param {string} origine label for the error message
 */
export function lireBlocsGarde(source, origine) {
	const debut = MARQUEUR_DEBUT_REGEX.exec(source);
	const fin = MARQUEUR_FIN_REGEX.exec(source);
	if (!debut || !fin || fin.index < debut.index) {
		throw new Error(`${origine} : marqueurs GENERATED-DETTE introuvables`);
	}
	const bloc = source
		.slice(debut.index + debut[0].length, fin.index)
		.trim()
		.replace(/^export const DETTE\s*=\s*/, '')
		.replace(/;$/, '');
	let dette;
	try {
		dette = JSON.parse(bloc);
	} catch (err) {
		throw new Error(`${origine} : bloc DETTE illisible (${err.message})`);
	}
	const blocRegles = REGLES_BLOC_REGEX.exec(source)?.[1] ?? '';
	const regles = [...blocRegles.matchAll(/\bnom:\s*'([^']+)'/g)].map((m) => m[1]);
	if (regles.length === 0) throw new Error(`${origine} : REGLES illisible`);
	const blocPortee = PORTEE_BLOC_REGEX.exec(source)?.[1];
	const portee =
		blocPortee === undefined
			? null
			: [...blocPortee.replace(/^\s*\/\/.*$/gm, '').matchAll(/'([^']+)'/g)].map((m) => m[1]);
	return { dette, regles, portee };
}

/** Bootstrap: measure every rule on the files of `base`, read from git without checkout. */
function mesurerDetteALaBase(racine, env, base) {
	// ls-tree prints paths relative to the frontend root; cat-file wants them from the repository root.
	const chemins = executerGit(racine, env, ['ls-tree', '-r', '-z', '--name-only', base, '--', 'src'])
		.split(NUL)
		.filter((chemin) => /\.(svelte|css)$/.test(chemin) && chemin !== SOURCE_DES_JETONS);
	const prefixe = executerGit(racine, env, ['rev-parse', '--show-prefix']).trim();
	const contenus = lireObjets(
		racine,
		env,
		chemins.map((chemin) => `${base}:${prefixe}${chemin}`)
	);
	const dette = {};
	chemins.forEach((chemin, i) => {
		if (contenus[i] === null) throw new Error(`base ${base} : ${chemin} illisible`);
		for (const { regle } of scannerFichier(chemin, contenus[i])) {
			dette[chemin] ??= {};
			dette[chemin][regle] = (dette[chemin][regle] ?? 0) + 1;
		}
	});
	return dette;
}

/**
 * Ratchet reference, read from git: `{ [chemin]: { [regle]: compte } }`, positive counts only.
 * @param {{ racine?: string, env?: Record<string, string|undefined> }} opts
 * @returns {{ base: string, origine: string, reference: Record<string, Record<string, number>>,
 *   gels: Record<string, string|null> }} `gels[regle]`: freeze commit (the base for a rule it knows,
 *   `null` for a rule frozen by no commit yet, whose reference is then its measure on the base)
 */
export function detteDeReference({ racine = REPO_ROOT, env = process.env } = {}) {
	const { base, origine } = resoudreBaseDeReference({ racine, env });
	const cheminScript = `${executerGit(racine, env, ['rev-parse', '--show-prefix']).trim()}${CHEMIN_SCRIPT}`;
	const [sourceBase] = lireObjets(racine, env, [`${base}:${cheminScript}`]);
	if (sourceBase === null) throw new Error(`${cheminScript} absent de la base ${base}`);
	const aLaBase = lireBlocsGarde(sourceBase, `base ${base}`);

	// The pathspec of git log is relative to the working directory (the frontend root), unlike the
	// `rev:path` of cat-file, which starts at the repository root.
	const commits = executerGit(racine, env, ['log', '--reverse', '--format=%H', `${base}..HEAD`, '--', CHEMIN_SCRIPT])
		.split('\n')
		.filter(Boolean);
	const sources = lireObjets(
		racine,
		env,
		commits.map((sha) => `${sha}:${cheminScript}`)
	);
	const etats = commits.map((sha, i) => {
		if (sources[i] === null) throw new Error(`commit ${sha} : ${cheminScript} supprime`);
		return { sha, ...lireBlocsGarde(sources[i], `commit ${sha}`) };
	});

	const nomsRegles = REGLES.map((regle) => regle.nom);
	const amorcage = nomsRegles.some((nom) => !aLaBase.regles.includes(nom))
		? mesurerDetteALaBase(racine, env, base)
		: {};

	const reference = {};
	const gels = {};
	for (const regle of nomsRegles) {
		const connueALaBase = aLaBase.regles.includes(regle);
		const depart = connueALaBase ? aLaBase.dette : amorcage;
		const comptes = {};
		for (const [chemin, parRegle] of Object.entries(depart)) {
			if (parRegle[regle] > 0) comptes[chemin] = parRegle[regle];
		}
		// Only the commits whose REGLES carry the rule are read for it, whatever their place in the log:
		// a commit without the rule (before its freeze, or on a branch older than the freeze and merged
		// since) says nothing about its debt. Read anyway, its absent entries would count as zero.
		const avecLaRegle = etats.filter((etat) => etat.regles.includes(regle));
		gels[regle] = connueALaBase ? base : (avecLaRegle[0]?.sha ?? null);
		for (const etat of avecLaRegle) {
			for (const chemin of Object.keys(comptes)) {
				comptes[chemin] = Math.min(comptes[chemin], etat.dette[chemin]?.[regle] ?? 0);
			}
		}
		for (const [chemin, compte] of Object.entries(comptes)) {
			if (compte > 0) (reference[chemin] ??= {})[regle] = compte;
		}
	}
	return { base, origine, reference, gels, reglesDeBase: aLaBase.regles, porteeDeBase: aLaBase.portee };
}

/**
 * Couples of `dette` above the reference: a rise, a file absent from the reference, a rule absent
 * from the reference of its file. Empty array = within the ratchet.
 * @param {Record<string, Record<string, number>>} reference
 * @param {Record<string, Record<string, number>>} dette
 * @returns {string[]}
 */
export function evaluerCliquet(reference, dette) {
	const erreurs = [];
	for (const [chemin, comptes] of Object.entries(dette)) {
		for (const [regle, compte] of Object.entries(comptes)) {
			if (!Number.isInteger(compte) || compte < 0) {
				erreurs.push(`${chemin}: compte "${regle}" invalide (${compte})`);
				continue;
			}
			const plafond = reference[chemin]?.[regle] ?? 0;
			if (compte <= plafond) continue;
			let motif = 'hausse';
			// A public file renamed (or moved) with its debt is refused here by construction: the reference
			// knows that debt under the old path only, so the new path is a file absent from the reference.
			// Clear the debt first, then rename.
			if (!(chemin in reference)) motif = 'fichier absent de la reference';
			else if (!(regle in reference[chemin])) motif = 'regle absente de la reference du fichier';
			erreurs.push(`${chemin}: ${compte} constat(s) "${regle}" > reference ${plafond} (${motif})`);
		}
	}
	return erreurs;
}

/**
 * What the working tree dropped from the base gate, read from git (never from a copy in a test file):
 * a rule of the base REGLES, or an entry of the base PORTEE_PUBLIQUE whose file or folder still exists.
 * An entry leaves the scope only with its file (a removal). Empty array = nothing dropped.
 */
function retraitsDeLaGarde({ racine, base, reglesDeBase, porteeDeBase }, garde) {
	const erreurs = reglesDeBase
		.filter((regle) => !garde.regles.includes(regle))
		.map((regle) => `REGLES : regle "${regle}" de la base ${base} retiree`);
	if (porteeDeBase === null) return erreurs;
	if (garde.portee === null) {
		return [...erreurs, `PORTEE_PUBLIQUE illisible dans l'arbre de travail (lue a la base ${base})`];
	}
	// A folder exists while it holds a file: git keeps no empty folder, a local leftover does not count.
	const existe = (entree) => {
		const chemin = join(racine, entree);
		if (!existsSync(chemin)) return false;
		if (!entree.endsWith('/')) return true;
		return readdirSync(chemin, { recursive: true, withFileTypes: true }).some((e) => e.isFile());
	};
	for (const entree of porteeDeBase) {
		if (garde.portee.includes(entree) || !existe(entree)) continue;
		erreurs.push(`PORTEE_PUBLIQUE : entree "${entree}" de la base ${base} retiree alors que son fichier existe`);
	}
	return erreurs;
}

/**
 * Sentinel of the ratchet: the DETTE block of the working tree (read from disk) and the measured debt
 * against the git reference; the rules and the scope entries of the base kept. Throws when the
 * reference cannot be read (fail-closed).
 * @param {{ racine?: string, env?: Record<string, string|undefined> }} opts
 */
export function verifierCliquet({ racine = REPO_ROOT, env = process.env } = {}) {
	const resultat = detteDeReference({ racine, env });
	const garde = lireBlocsGarde(readFileSync(join(racine, CHEMIN_SCRIPT), 'utf8'), 'arbre de travail');
	const erreurs = [
		...retraitsDeLaGarde({ racine, ...resultat }, garde),
		...evaluerCliquet(resultat.reference, garde.dette).map((e) => `DETTE ${e}`),
		...evaluerCliquet(resultat.reference, mesurerDette(racine)).map((e) => `mesure ${e}`)
	];
	return { ...resultat, erreurs };
}

function regenererFichierDette() {
	const dette = mesurerDette(REPO_ROOT);
	// --maj-dette only lowers the debt: it never writes a count above the git reference.
	const { base, reference } = detteDeReference({ racine: REPO_ROOT, env: process.env });
	const hausses = evaluerCliquet(reference, dette);
	if (hausses.length > 0) {
		const liste = hausses.map((e) => `  - ${e}`).join('\n');
		throw new Error(`refus d'ecrire une dette au-dessus de la reference (base ${base}) :\n${liste}`);
	}
	const source = readFileSync(SELF_PATH, 'utf8');
	const eol = source.includes('\r\n') ? '\r\n' : '\n';
	const marqueurDebut = '// GENERATED-DETTE-START';
	const marqueurFin = '// GENERATED-DETTE-END';
	const idxDebut = source.indexOf(marqueurDebut);
	const idxFin = source.indexOf(marqueurFin);
	if (idxDebut === -1 || idxFin === -1) {
		throw new Error('marqueurs GENERATED-DETTE introuvables dans le script');
	}
	const avant = source.slice(0, idxDebut + marqueurDebut.length);
	const apres = source.slice(idxFin);
	const bloc = serialiserDette(dette).replace(/\n/g, eol);
	writeFileSync(SELF_PATH, `${avant}${eol}${bloc}${eol}${apres}`, 'utf8');
	return dette;
}

function isMain() {
	if (!process.argv[1]) return false;
	try {
		return (
			import.meta.url === new URL(`file://${process.argv[1]}`).href ||
			import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'))
		);
	} catch {
		return false;
	}
}

if (isMain()) {
	const argv = process.argv.slice(2);

	if (argv.includes('--maj-dette')) {
		try {
			const dette = regenererFichierDette();
			const totalConstats = Object.values(dette).reduce(
				(acc, comptes) => acc + Object.values(comptes).reduce((a, b) => a + b, 0),
				0
			);
			console.log(
				`[check-design-gate] OK dette regeneree : ${Object.keys(dette).length} fichier(s), ${totalConstats} constat(s).`
			);
			process.exit(0);
		} catch (err) {
			console.error(`[check-design-gate] FAIL regeneration de la dette: ${err.message}`);
			process.exit(1);
		}
	}

	const idxPaths = argv.indexOf('--paths');
	const cheminsCibles =
		idxPaths === -1 ? undefined : argv.slice(idxPaths + 1).filter((a) => !a.startsWith('--'));

	let resultat;
	try {
		resultat = runDesignGate({ racine: REPO_ROOT, dette: DETTE, cheminsCibles });
	} catch (err) {
		console.error(`[check-design-gate] FAIL erreur inattendue: ${err.message}`);
		process.exit(1);
	}

	if (!resultat.ok) {
		console.error(`[check-design-gate] FAIL ${resultat.erreurs.length} probleme(s):`);
		for (const e of resultat.erreurs) console.error(`  - ${e}`);
		process.exit(1);
	}

	console.log('[check-design-gate] OK.');
	process.exit(0);
}
