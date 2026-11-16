// The meta description of the public pages, the snippet a search engine shows under the title (SEO audit of
// 2026-11-14: every commune came out with the same sentence, Google cut the departement ones). A description
// only carries what the page serves: a figure that is not served is left out, never written as a zero, and a
// served zero population is worded as such, never « 0 habitants ».
import type { CommuneSynthese } from '$lib/api/commune';
import { prixServi, type PrixServi } from '$lib/data/commune-faq';
import type { KpiCommune } from '$lib/data/commune-kpi';
import { etatDeColonne } from '$lib/data/etats-colonnes';
import { formatBareNumber } from '$lib/data/formatters';

/** Past this length the snippet is cut in the results; the served test refuses more than 160. */
export const DESCRIPTION_MAX = 155;

const NB = ' ';

/** The first candidate that fits the snippet; the last one otherwise (it is the shortest). */
export function premiereQuiTient(candidats: readonly string[], max = DESCRIPTION_MAX): string {
	const retenus = candidats.filter(Boolean);
	return retenus.find((texte) => texte.length <= max) ?? retenus.at(-1) ?? '';
}

// A name starting with a vowel takes the elided « d’ ». The H is left out: an aspirated H keeps « de »
// (« de Hautot »), and « de » before a mute one reads as an approximation, never as a mistake.
const VOYELLE = /^[AEIOUYÀÂÄÉÈÊËÎÏÔÖÙÛÜŒÆ]/i;

/**
 * « de » before a place name, contracted with its article: « de Nantes », « d’Abbaretz », « du Mans »,
 * « des Sables-d’Olonne », « de La Baule-Escoublac », « de L’Haÿ-les-Roses ». An arrondissement is named
 * without its article, which « de » restores: « du 12e arrondissement de Paris ».
 */
export function deNom(nom: string): string {
	if (/^\d+(e|er) arrondissement\b/.test(nom)) return `du ${nom}`;
	const article = nom.match(/^(Le|Les) (.+)$/);
	if (article) return `${article[1] === 'Le' ? 'du' : 'des'} ${article[2]}`;
	if (/^(La |L['’])/.test(nom)) return `de ${nom}`;
	return VOYELLE.test(nom) ? `d’${nom}` : `de ${nom}`;
}

/** « 2 052 habitants (2022, INSEE) », « aucun habitant recensé (2022, INSEE) »; null when not served. */
export function habitantsServis(commune: CommuneSynthese): string | null {
	const lu = etatDeColonne(commune, 'population');
	if (lu.kind !== 'valeur') return null;
	const annee = commune.annee_demographie != null ? ` (${commune.annee_demographie}, INSEE)` : '';
	const n = Number(lu.valeur);
	if (n === 0) return `aucun habitant recensé${annee}`;
	return `${formatBareNumber('demographie.population', n)}${NB}${n === 1 ? 'habitant' : 'habitants'}${annee}`;
}

const majuscule = (texte: string) => texte.charAt(0).toUpperCase() + texte.slice(1);

/**
 * Commune or arrondissement fiche. With served prices: « Nantes (44), prix médians : appartements
 * 3 385 €/m² (3 996 ventes), maisons 4 110 €/m² (1 011 ventes), du 31 décembre 2024 au 31 décembre 2025.
 * 325 070 habitants. » The served period is written as the card writes it, never rounded; the
 * population, the sales counts and the closing sentence give way, in that order, to stay in the snippet.
 */
export function descriptionCommune(
	nom: string,
	codeDepartement: string | null,
	commune: CommuneSynthese | null,
	kpis: KpiCommune[]
): string {
	const territoire = codeDepartement ? `${nom}${NB}(${codeDepartement})` : nom;
	if (!commune) return `Fiche publique DataCommune ${deNom(nom)}.`;
	const population = habitantsServis(commune);
	const prix: [string, PrixServi][] = [];
	for (const [objet, type] of [['appartements', 'appartement'], ['maisons', 'maison']] as const) {
		const servi = prixServi(commune, kpis, type);
		if (servi) prix.push([objet, servi]);
	}
	const suite = 'Loyers, DPE et services, avec leurs sources.';

	if (prix.length === 0) {
		const tete = population ? `${territoire}${NB}: ${population}.` : `Fiche publique ${deNom(territoire)}.`;
		return premiereQuiTient([
			`${tete} DPE, loyers, revenus et services, avec leurs sources et leurs limites.`,
			`${tete} DPE, loyers, revenus et services, avec leurs sources.`,
			tete
		]);
	}
	// One period when both prices share it, else each price with its own.
	const memePeriode = prix.every(([, p]) => p.fenetre === prix[0][1].fenetre);
	const phrasePrix = (avecEffectif: boolean) => {
		const parties = prix.map(([objet, p]) => {
			const entre = [avecEffectif ? p.effectif : null, memePeriode ? null : p.fenetre].filter(Boolean).join(', ');
			return `${objet} ${p.valeur}${entre ? ` (${entre})` : ''}`;
		});
		const libelle = prix.length > 1 ? 'prix médians' : 'prix médian';
		return `${territoire}, ${libelle}${NB}: ${parties.join(', ')}${memePeriode ? `, ${prix[0][1].fenetre}` : ''}.`;
	};
	const habitants = population ? ` ${majuscule(population)}.` : '';
	const habitantsCourt = population ? ` ${majuscule(population.replace(/ \(.*\)$/, ''))}.` : '';
	return premiereQuiTient([
		`${phrasePrix(true)}${habitants} ${suite}`,
		`${phrasePrix(true)}${habitants}`,
		`${phrasePrix(true)}${habitantsCourt}`,
		phrasePrix(true),
		`${phrasePrix(false)}${habitantsCourt}`,
		phrasePrix(false)
	]);
}
