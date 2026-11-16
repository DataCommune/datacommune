// What the link preview of a commune or departement fiche says (`/og/commune/{code}.png`,
// `/og/departement/{code}.png`, SEO audit of 2026-11-14). Pure module: the page reads it for its
// `og:image` and `og:image:alt`, the image route for what it draws, so the alternative text never
// describes another image than the one served.
// Only served values are written, read through the same functions as the page (`communeKpis`,
// `cartesDepartement`): an absent value is left out, never shown as a zero. A fiche without any
// served figure has no card of its own (null): the page and the route fall back to the generic image.
import { blocsPublies, type CommuneSynthese, type SourceAttribution } from '$lib/api/commune';
import type { DepartementSynthese } from '$lib/api/communes';
import { libelleArrondissement } from '$lib/data/arrondissements';
import { communeKpis, type KpiCommune } from '$lib/data/commune-kpi';
import { dateEnLettres } from '$lib/data/commune-indicators';
import { cartesDepartement, type CarteDepartement } from '$lib/data/departement-presentation';
import { DEPARTEMENTS } from '$lib/data/departements';
import { etatDeColonne } from '$lib/data/etats-colonnes';
import { formatBareNumber } from '$lib/data/formatters';

export const OG_LARGEUR = 1200;
export const OG_HAUTEUR = 630;

const NBSP = ' ';

export interface ChiffreOg {
	/** What the figure is (« Appartements »). */
	libelle: string;
	/** The figure with its unit, as the page writes it (« 3 385 €/m² »). */
	valeur: string;
	/** Where it comes from and what it rests on (« DVF · 375 ventes · au 31 décembre 2025 »). */
	source: string;
	/** The source alone, for the alternative text and a narrow column (« DVF »). */
	sigle: string;
}

export interface CarteOg {
	genre: 'commune' | 'departement';
	code: string;
	nom: string;
	/** Line above the name (« Commune · Loire-Atlantique (44) »). */
	surtitre: string;
	/** One to three served figures, in reading order. */
	chiffres: ChiffreOg[];
	/** Path of the fiche on the site, written at the foot of the image. */
	chemin: string;
	alt: string;
}

/** The acronym a source name ends with (« Demandes de valeurs foncières (DVF) » -> « DVF »), else the name. */
function sigleDe(nom: string | undefined, defaut: string): string {
	if (!nom) return defaut;
	return /\(([A-Z][A-Za-z0-9]+)\)\s*$/.exec(nom)?.[1] ?? nom;
}

function texteAlt(nom: string, surtitre: string, chiffres: ChiffreOg[]): string {
	const lus = chiffres.map((chiffre) => `${chiffre.libelle} : ${chiffre.valeur}`).join(' ; ');
	const sources = [...new Set(chiffres.map((chiffre) => chiffre.sigle))].join(', ');
	return `${nom} — ${surtitre}. ${lus}. Sources : ${sources}.`;
}

const MOIS_COURTS: Readonly<Record<string, string>> = {
	janvier: 'janv.',
	février: 'févr.',
	avril: 'avr.',
	juillet: 'juil.',
	septembre: 'sept.',
	octobre: 'oct.',
	novembre: 'nov.',
	décembre: 'déc.'
};

/** « au 31 décembre 2025 » -> « au 31 déc. 2025 »: the end of a period in the room of a column. */
function finCourte(date: string | null | undefined): string | null {
	return date ? `au ${date.replace(/\p{L}+/gu, (mot) => MOIS_COURTS[mot] ?? mot)}` : null;
}

function departementDe(code: string | null): string | null {
	if (!code) return null;
	const nom = DEPARTEMENTS.find((d) => d.code === code)?.nom;
	return nom ? `${nom} (${code})` : `Département ${code}`;
}

const LIBELLES_PRIX: Readonly<Record<'prix-appartements' | 'prix-maisons', string>> = {
	'prix-appartements': 'Appartements, prix médian',
	'prix-maisons': 'Maisons, prix médian'
};

/** The price of a commune KPI when served: the first line holding a value (`communeKpis` order). */
function prixCommune(kpi: KpiCommune | undefined): ChiffreOg | null {
	if (!kpi || (kpi.id !== 'prix-appartements' && kpi.id !== 'prix-maisons')) return null;
	const ligne = kpi.lignes.find((l) => l.etat.kind === 'valeur');
	if (!ligne || ligne.etat.kind !== 'valeur') return null;
	const sigle = sigleDe(kpi.source?.nom, 'DVF');
	// « du 31 décembre 2024 au 31 décembre 2025 » -> « au 31 décembre 2025 »: the end of the served window.
	const fin = ligne.fenetre ? / au (.+)$/.exec(ligne.fenetre)?.[1] : undefined;
	return {
		libelle: LIBELLES_PRIX[kpi.id],
		valeur: ligne.etat.unite ? `${ligne.etat.texte}${NBSP}${ligne.etat.unite}` : ligne.etat.texte,
		source: [sigle, ligne.effectif, finCourte(fin)].filter(Boolean).join(' · '),
		sigle
	};
}

/** The card of a commune fiche that rendered (`etat: 'fiche'`), or null when no figure is served. */
export function carteCommune(fiche: {
	code: string;
	parent?: { code: string; nom: string } | null;
	synthese: CommuneSynthese;
	sources: SourceAttribution[] | null;
}): CarteOg | null {
	const synthese = blocsPublies(fiche.synthese);
	const kpis = communeKpis(synthese, fiche.sources);
	const chiffres: ChiffreOg[] = [];
	for (const id of ['prix-appartements', 'prix-maisons'] as const) {
		const prix = prixCommune(kpis.find((kpi) => kpi.id === id));
		if (prix) chiffres.push(prix);
	}
	const population = etatDeColonne(synthese, 'population');
	if (population.kind === 'valeur' && typeof population.valeur === 'number') {
		chiffres.push({
			libelle: 'Habitants',
			valeur: formatBareNumber('demographie.population', population.valeur),
			source: synthese.annee_demographie != null ? `INSEE · recensement ${synthese.annee_demographie}` : 'INSEE',
			sigle: 'INSEE'
		});
	}
	if (!chiffres.length) return null;

	const nomServi = synthese.nom_commune ?? `Commune INSEE ${fiche.code}`;
	const nom = fiche.parent ? (libelleArrondissement(fiche.code) ?? nomServi) : nomServi;
	const departement = departementDe(synthese.code_departement);
	const surtitre = [fiche.parent ? 'Arrondissement' : 'Commune', departement].filter(Boolean).join(' · ');
	return { genre: 'commune', code: fiche.code, nom, surtitre, chiffres, chemin: `/commune/${fiche.code}`, alt: texteAlt(nom, surtitre, chiffres) };
}

const LIBELLES_DEPARTEMENT: Readonly<Record<'appartement' | 'maison' | 'population', string>> = {
	appartement: 'Appartements, prix médian',
	maison: 'Maisons, prix médian',
	population: 'Habitants'
};

function chiffreDepartement(carte: CarteDepartement, synthese: DepartementSynthese, sources: readonly SourceAttribution[]): ChiffreOg | null {
	if (carte.etat.kind !== 'valeur' || carte.id === 'dpe') return null;
	if (carte.id === 'population') {
		// The label says « Habitants »: the unit of the tile (« hab. ») would say it twice.
		const valeur = carte.etat.texte;
		const annee = synthese.population.annee;
		// A partial sum is said on the image too, as on the tile (A9-7): « 207 communes sur 212 ».
		const partielle = carte.portee && /\bsur\b/.test(carte.portee) ? carte.portee : null;
		const source = ['INSEE', annee !== null ? `recensement ${annee}` : null, partielle].filter(Boolean).join(' · ');
		return { libelle: LIBELLES_DEPARTEMENT.population, valeur, source, sigle: 'INSEE' };
	}
	const valeur = carte.etat.unite ? `${carte.etat.texte}${NBSP}${carte.etat.unite}` : carte.etat.texte;
	const sigle = sigleDe(sources.find((source) => source.nom && /dvf/i.test(source.nom))?.nom, 'DVF');
	const fin = synthese.prix.fenetre?.fin ? dateEnLettres(synthese.prix.fenetre.fin) : null;
	return {
		libelle: LIBELLES_DEPARTEMENT[carte.id],
		valeur,
		source: [sigle, carte.portee, finCourte(fin)].filter(Boolean).join(' · '),
		sigle
	};
}

/** The card of a departement whose figures answered, or null when none is served. */
export function carteDepartement(data: { code: string; synthese: DepartementSynthese; sources: readonly SourceAttribution[] }): CarteOg | null {
	const cartes = cartesDepartement(data.synthese);
	const chiffres = (['appartement', 'maison', 'population'] as const)
		.map((id) => cartes.find((carte) => carte.id === id))
		.map((carte) => (carte ? chiffreDepartement(carte, data.synthese, data.sources) : null))
		.filter((chiffre): chiffre is ChiffreOg => chiffre !== null);
	if (!chiffres.length) return null;
	const nom = data.synthese.nom;
	const surtitre = [`Département ${data.code}`, data.synthese.region].filter(Boolean).join(' · ');
	return { genre: 'departement', code: data.code, nom, surtitre, chiffres, chemin: `/departement/${data.code}`, alt: texteAlt(nom, surtitre, chiffres) };
}

/** Address of the image of a card, on the site that serves the page. */
export function urlImageOg(siteUrl: string, carte: Pick<CarteOg, 'genre' | 'code'>): string {
	return `${siteUrl}/og/${carte.genre}/${encodeURIComponent(carte.code)}.png`;
}
