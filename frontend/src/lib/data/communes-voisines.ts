// The neighbours of a commune page (SEO audit 2026-11-14): a few links to the communes around it, so
// that every commune fiche is reached by a link rendered by the server, not by the sitemap alone.
// Read beside the fiche, never inside it: the fiche and its JSON export do not depend on it, and a
// failed read only drops the links (null), it is never an outage of the page.
import { getCommunesVoisines, type CommunesVoisines } from '$lib/api/communes';
import { libelleArrondissement } from '$lib/data/arrondissements';

export interface LienVoisine {
	code: string;
	href: string;
	nom: string;
}

/** The neighbours as served, or null when the read failed or answered outside the contract. */
export async function lireVoisines(code: string, fetchImpl: typeof fetch): Promise<CommunesVoisines['voisines'] | null> {
	try {
		const reponse = await getCommunesVoisines(code.toUpperCase(), fetchImpl);
		return Array.isArray(reponse?.voisines) ? reponse.voisines : null;
	} catch {
		return null;
	}
}

/**
 * The links in the served order (bordering first). An arrondissement shows its readable title; a
 * commune of another departement shows its code, the only way to tell two namesakes apart.
 */
export function liensVoisines(voisines: CommunesVoisines['voisines'], departement: string | null): LienVoisine[] {
	return voisines.map((voisine) => {
		const nom = (voisine.type_zone === 'arrondissement' ? libelleArrondissement(voisine.code_insee) : null) ?? voisine.nom;
		return {
			code: voisine.code_insee,
			href: `/commune/${voisine.code_insee}`,
			nom: departement && voisine.departement_code !== departement ? `${nom}\u00a0(${voisine.departement_code})` : nom
		};
	});
}
