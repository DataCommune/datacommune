import { api } from './client';
import type { DatasetHosting } from './admin';
import type { DatasetProvenance } from './resolve';
import type { OutOfCoverageReason } from '$lib/types/fiche';

// Public read of `GET /v1/datasets` for « Données & méthode » (D-40). Every licence, restriction,
// quarantine and coverage shown on that page comes from this response: the frontend holds no
// copy of the catalogue (LIC-01). Types mirror `app/datasets/schemas.py`.

/** Reuse terms read on the producer's own pages, served from the canonical catalogue (LIC-01). */
export interface ReutilisationSource {
	licence: string;
	licence_url: string;
	conditions: string;
	/** Reuse restrictions of the producer (DVF: R*112 A-3 LPF); empty when it states none. */
	restrictions: string[];
	/** ISO date the producer's pages were consulted. */
	verifiee_le: string;
}

/** Limits of the source itself (territory, market, cadence): arbitrated copy, never reworded. */
export interface LimitesSource {
	chapeau: string;
	puces: string[];
}

/** One entry of `GET /v1/datasets`, restricted to what the public page reads. */
export interface DatasetPublic {
	/** Catalogue key (`dvf`, `encadrement_loyers`…): the anchor of its card. */
	nom: string;
	/**
	 * `disponible` (imported row), `quarantined` (declared by the catalogue), or for a catalogue
	 * key without an import `hors_couverture`, `proxy_amont` or `non_importe`.
	 */
	statut: string;
	provenance: DatasetProvenance | null;
	hebergement: DatasetHosting | null;
	reutilisation: ReutilisationSource | null;
	/** Published reason of a catalogue quarantine, null otherwise. */
	quarantaine: string | null;
	limites_source: LimitesSource | null;
}

interface ReponseApi<T> {
	statut: string;
	donnees: T;
}

/** `GET /v1/datasets`, through SvelteKit's `fetch` during SSR. */
export async function listDatasets(fetchImpl: typeof fetch = fetch): Promise<DatasetPublic[]> {
	const res = await api<ReponseApi<DatasetPublic[]>>('/v1/datasets', {}, fetchImpl);
	return res.donnees;
}

/** Statuses of a hosted dataset the page knows how to word. */
export type StatutHeberge = 'disponible' | 'proxy_amont' | 'non_importe';
const STATUTS_HEBERGES: ReadonlySet<string> = new Set(['disponible', 'proxy_amont', 'non_importe']);

/**
 * State of a dataset as served, read in a fixed order: a catalogue quarantine first (a
 * quarantined dataset is never presented as available), then the hosting of the instance. A
 * status or hosting the page cannot word is `inconnu`: refused and reported, never guessed.
 * Pure reading of served fields; nothing is computed.
 */
export type EtatJeu =
	| { kind: 'non_qualifie'; motif: string | null }
	| { kind: 'hors_couverture'; motif: OutOfCoverageReason }
	| { kind: 'heberge'; statut: StatutHeberge; departements: string[] }
	| { kind: 'inconnu'; statut: string };

export function etatDuJeu(jeu: DatasetPublic): EtatJeu {
	if (jeu.statut === 'quarantined') return { kind: 'non_qualifie', motif: jeu.quarantaine };
	const hebergement = jeu.hebergement;
	if (hebergement?.etat === 'hors_couverture' && hebergement.motif) {
		return { kind: 'hors_couverture', motif: hebergement.motif };
	}
	if (hebergement?.etat === 'heberge' && STATUTS_HEBERGES.has(jeu.statut)) {
		return {
			kind: 'heberge',
			statut: jeu.statut as StatutHeberge,
			departements: hebergement.departements
		};
	}
	return { kind: 'inconnu', statut: jeu.statut };
}
