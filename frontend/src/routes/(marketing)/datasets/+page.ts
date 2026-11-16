// Universal load of « Données & méthode » (UI-SPEC « /datasets », D-40). Server-rendered from
// `GET /v1/datasets` on every request: what this instance hosts is read in its database (D-23),
// so the page is never prerendered and never writes a territory (D-34, D-35). A failed call is
// an outage, never an empty catalogue. The failure is typed: a rate limit or a refused access is
// not an outage of the service and the page says so (WR-01).
import { listDatasets, type DatasetPublic } from '$lib/api/datasets';
import { ApiError } from '$lib/api/client';
import { DEPARTEMENTS } from '$lib/data/departements';
import { detailPanne, type DetailPanne } from '$lib/data/panne';
import { markOutage } from '$lib/utils/etat-page';
import type { PageLoad } from './$types';

export const prerender = false;

export type DatasetsPageData = {
	jeux: DatasetPublic[];
	/** Local presentation index; aliases are editorial themes, not API metadata. */
	indexRecherche: Record<string, string>;
	/** Departements served by at least one hosted dataset, in the referential order. */
	departementsHeberges: string[];
	/** The failed call, typed (rate limit, refused access, outage); null when it answered. */
	panne: DetailPanne | null;
};

const ORDRE = new Map(DEPARTEMENTS.map((d, index) => [d.code, index]));

// Cautious aliases from the five existing themes of the commune fiche. A catalogue key absent
// here stays searchable by its served name and producer; no classification is inferred.
const THEMES_RECHERCHE: Readonly<Record<string, readonly string[]>> = {
	antennes: ['Services & transports'],
	batiments: ['Énergie & bâti'],
	bdnb: ['Énergie & bâti'],
	connectivite: ['Services & transports'],
	copropriete: ['Immobilier'],
	demographie: ['Contexte communal'],
	dpe: ['Énergie & bâti'],
	dvf: ['Immobilier'],
	encadrement_loyers: ['Immobilier'],
	equipements: ['Services & transports'],
	errial: ['Risques & urbanisme'],
	fiscalite: ['Contexte communal'],
	foncier_pm: ['Contexte communal'],
	gares: ['Services & transports'],
	georisques: ['Risques & urbanisme'],
	loyers: ['Immobilier'],
	monuments: ['Risques & urbanisme'],
	permis: ['Risques & urbanisme'],
	revenus: ['Contexte communal'],
	scolaire: ['Services & transports'],
	sirene: ['Services & transports'],
	transports: ['Services & transports'],
	urbanisme: ['Risques & urbanisme'],
	vacance: ['Immobilier'],
	zonage_abc: ['Risques & urbanisme']
};

export function _normaliserRechercheCatalogue(value: string): string {
	return value
		.normalize('NFD')
		.replace(/[\u0300-\u036f]/g, '')
		.replace(/&/g, ' et ')
		.toLocaleLowerCase('fr')
		.trim()
		.replace(/\s+/g, ' ');
}

function indexerRecherche(jeux: DatasetPublic[]): DatasetsPageData['indexRecherche'] {
	return Object.fromEntries(
		jeux.map((jeu) => {
			const themes = THEMES_RECHERCHE[jeu.nom] ?? [];
			const termes = [jeu.nom, jeu.provenance?.nom, jeu.provenance?.producteur, ...themes]
				.filter((value): value is string => Boolean(value))
				.join(' ');
			return [jeu.nom, _normaliserRechercheCatalogue(termes)];
		})
	);
}

/** Union of the `hebergement.departements` served for the hosted datasets. */
function departementsHeberges(jeux: DatasetPublic[]): string[] {
	const codes = new Set<string>();
	for (const jeu of jeux) {
		if (jeu.hebergement?.etat === 'heberge') {
			for (const code of jeu.hebergement.departements) codes.add(code);
		}
	}
	return [...codes].sort(
		(a, b) => (ORDRE.get(a) ?? Number.MAX_SAFE_INTEGER) - (ORDRE.get(b) ?? Number.MAX_SAFE_INTEGER)
	);
}

/**
 * Datasets of the catalogue only: the ones the API gives a `hebergement`. `/v1/datasets` also
 * serves, for the administration, metadata rows outside the catalogue (`scoring`, a derived score
 * seeded by migration 039): the public page neither shows nor counts them. A display selection,
 * not a computation; the API is unchanged (decision of 03-11 after the D-15 review).
 */
function jeuxDuCatalogue(jeux: DatasetPublic[]): DatasetPublic[] {
	return jeux.filter((jeu) => jeu.hebergement != null);
}

// `setHeaders` marks an outage for the 503 of hooks.server.ts (04.7-10, decision 11; review R-21); it does
// nothing in the browser.
export const load: PageLoad = async ({ fetch, setHeaders }): Promise<DatasetsPageData> => {
	try {
		const servis = await listDatasets(fetch);
		if (!Array.isArray(servis)) throw new Error('/v1/datasets: donnees is not a list');
		const jeux = jeuxDuCatalogue(servis);
		return {
			jeux,
			indexRecherche: indexerRecherche(jeux),
			departementsHeberges: departementsHeberges(jeux),
			panne: null
		};
	} catch (err) {
		markOutage(setHeaders, err instanceof ApiError ? err.retryAfter : null);
		return { jeux: [], indexRecherche: {}, departementsHeberges: [], panne: detailPanne(err) };
	}
};
