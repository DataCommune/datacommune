// The <title> of the territory pages, the line a search engine shows for each of them (2026-11-12: every
// commune came out under the same « DataCommune »). One rule per kind of page, used by the page itself and
// by its og:title / twitter:title, so the three never drift apart.
//
// A title only names what the page serves: the commune fiche shows prices, rents, DPE and the services of
// daily life, never the risks (they are read at the address), so its title does not promise them. A page
// that serves no figure (outage, out of coverage) keeps its plain name.

const SITE = 'DataCommune';
// Between a name and its code, as on every list of the site: « Loire-Atlantique (44) » never breaks.
const INSECABLE = ' ';

/** `<sujet> — DataCommune`, the form every public page ends with. */
export const titrePage = (sujet: string): string => `${sujet} — ${SITE}`;

/**
 * Commune or arrondissement fiche: « Nantes (44) : prix immobilier, loyers, DPE et services — DataCommune »
 * when the fiche serves its figures, « Nantes — DataCommune » otherwise.
 */
export function titreCommune(nom: string, codeDepartement: string | null, ficheServie: boolean): string {
	if (!ficheServie) return titrePage(nom);
	const territoire = codeDepartement ? `${nom}${INSECABLE}(${codeDepartement})` : nom;
	return titrePage(`${territoire} : prix immobilier, loyers, DPE et services`);
}

/** Departement page: « Loire-Atlantique (44), chiffres clés du département — DataCommune ». */
export const titreDepartement = (nom: string, code: string): string =>
	titrePage(`${nom}${INSECABLE}(${code}), chiffres clés du département`);
