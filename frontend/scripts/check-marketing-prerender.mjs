#!/usr/bin/env node
// Verify that the prerendered home page is the direct commune search (D-38, UI-SPEC « / »):
// a <main> landmark, exactly one <h1>, and a search form that works without JavaScript
// (`<form action="/communes" method="get">` carrying the `q` field; since 09/11 no departement filter
// on the home page, the results page keeps it).
// This replaces the former 10 000-byte threshold: the page is short by design, what matters is
// that the visitor can reach the results page from the static HTML alone. Since 04.7-10 it also
// checks the canonical link and the WebSite JSON-LD (with its SearchAction) of the page.
//
// The pure check `verifierAccueil` is exported and exercised by `qa-gates.test.mjs`; the script
// itself only reads the generated page and reports.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = fileURLToPath(new URL(".", import.meta.url));
const homepage = join(
	scriptDir,
	"..",
	".svelte-kit",
	"output",
	"prerendered",
	"pages",
	"index.html"
);

/** Value of `name` in an opening tag, or null (quotes as emitted by the SSR renderer). */
function attribute(tag, name) {
	const match = tag.match(new RegExp(`\\s${name}\\s*=\\s*"([^"]*)"`, "i"));
	return match ? match[1] : null;
}

/**
 * What the prerendered home page lacks, as a list of short descriptions (empty when it is the
 * direct commune search).
 * @param {string} html
 * @returns {string[]}
 */
export function verifierAccueil(html) {
	const missing = [];
	if (!/<main[\s>]/i.test(html)) missing.push("<main>");

	const h1Count = (html.match(/<h1[\s>]/gi) ?? []).length;
	if (h1Count !== 1) missing.push(`exactly one <h1> (found ${h1Count})`);

	// Every <form>…</form> block with its opening tag; the search form is the one that targets
	// /communes with GET.
	const forms = [...html.matchAll(/(<form\b[^>]*>)([\s\S]*?)<\/form>/gi)];
	const searchForm = forms.find(
		([, open]) =>
			attribute(open, "action") === "/communes" &&
			(attribute(open, "method") ?? "").toLowerCase() === "get"
	);
	if (!searchForm) {
		missing.push('<form action="/communes" method="get">');
	} else {
		const body = searchForm[2];
		if (!/\sname\s*=\s*"q"/i.test(body)) missing.push('search field name="q"');
	}

	// 04.7-10, decision 8: the page is its own canonical, and it declares the WebSite with the search
	// action of the form above. Checked in the prerendered HTML, so that a build which loses them fails.
	const origin = verifierCanonique(html, missing);
	verifierWebSite(html, origin, missing);

	return missing;
}

/** Origins the site is built for (`SITE_URL` in `src/lib/config.ts`: production, preproduction). */
const ORIGIN_DU_SITE = /^(https:\/\/(?:dev\.)?datacommune\.fr)\/$/;

/**
 * The canonical link: exactly one, equal to the root of the site, absolute. Returns the origin it
 * names (the other URLs of the page must agree on it), or null.
 * @param {string} html
 * @param {string[]} missing
 * @returns {string | null}
 */
function verifierCanonique(html, missing) {
	const canoniques = [...html.matchAll(/<link\b[^>]*>/gi)]
		.map(([tag]) => tag)
		.filter((tag) => (attribute(tag, "rel") ?? "").toLowerCase() === "canonical");
	if (canoniques.length !== 1) {
		missing.push(`exactly one <link rel="canonical"> (found ${canoniques.length})`);
		return null;
	}
	const href = attribute(canoniques[0], "href");
	const racine = href === null ? null : ORIGIN_DU_SITE.exec(href);
	if (!racine) {
		missing.push(
			`canonical link equal to the site root, https://datacommune.fr/ or https://dev.datacommune.fr/ (found ${JSON.stringify(href)})`
		);
		return null;
	}
	return racine[1];
}

/**
 * The WebSite JSON-LD: exactly one, named DataCommune, in fr-FR, at the root of the site, with the
 * absolute SearchAction of the search form (`/communes`, field `q`).
 * @param {string} html
 * @param {string | null} origin origin of the canonical link, when it is valid
 * @param {string[]} missing
 */
function verifierWebSite(html, origin, missing) {
	const sites = [];
	let illisibles = 0;
	for (const [, attributs, corps] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
		if ((attribute(`<script${attributs}>`, "type") ?? "").toLowerCase() !== "application/ld+json") continue;
		try {
			const lu = JSON.parse(corps);
			for (const objet of Array.isArray(lu) ? lu : [lu]) {
				if (objet?.["@type"] === "WebSite") sites.push(objet);
			}
		} catch {
			illisibles++;
		}
	}
	if (illisibles > 0) missing.push(`valid JSON in every ld+json script (${illisibles} unreadable)`);
	if (sites.length !== 1) {
		missing.push(`exactly one WebSite JSON-LD (found ${sites.length})`);
		return;
	}
	const [site] = sites;
	const racine = ORIGIN_DU_SITE.exec(typeof site.url === "string" ? site.url : "");
	const base = origin ?? racine?.[1] ?? null;
	if (site["@context"] !== "https://schema.org") missing.push('WebSite @context "https://schema.org"');
	if (site.name !== "DataCommune") missing.push('WebSite name "DataCommune"');
	if (site.inLanguage !== "fr-FR") missing.push('WebSite inLanguage "fr-FR"');
	if (!racine || (origin !== null && racine[1] !== origin)) {
		missing.push(`WebSite url equal to the canonical site root (found ${JSON.stringify(site.url)})`);
	}
	const action = site.potentialAction;
	const cible = base ? `${base}/communes?q={search_term_string}` : null;
	if (
		action?.["@type"] !== "SearchAction" ||
		cible === null ||
		action.target !== cible ||
		action["query-input"] !== "required name=search_term_string"
	) {
		missing.push(
			`WebSite SearchAction with the absolute target ${cible ?? "<site>/communes?q={search_term_string}"} and query-input "required name=search_term_string"`
		);
	}
}

function isMain() {
	if (!process.argv[1]) return false;
	try {
		return (
			import.meta.url === new URL(`file://${process.argv[1]}`).href ||
			import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/"))
		);
	} catch {
		return false;
	}
}

if (isMain()) {
	let html;
	try {
		html = readFileSync(homepage, "utf8");
	} catch (error) {
		console.error(`[prerender] Missing generated homepage: ${homepage}`);
		console.error(error);
		process.exit(1);
	}

	const missing = verifierAccueil(html);
	if (missing.length > 0) {
		console.error(`[prerender] Homepage is not the prerendered commune search; missing ${missing.join(", ")}.`);
		process.exit(1);
	}

	console.log(
		`[prerender] OK - homepage has <main>, one <h1>, the GET /communes form (q), its canonical and the WebSite JSON-LD; ${html.length} bytes.`
	);
}
