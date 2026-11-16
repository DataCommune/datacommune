import { api } from './client';
import type { OutOfCoverageReason } from '$lib/types/fiche';

/** Hosting of a dataset by the instance, read from the persisted scope (D-24). */
export interface DatasetHosting {
	etat: 'heberge' | 'hors_couverture';
	motif: OutOfCoverageReason | null;
	departements: string[];
}

export interface DatasetInfo {
	nom: string;
	/**
	 * Row status, or for a catalogue key without an import: `hors_couverture` (not hosted),
	 * `proxy_amont` (hosted upstream proxy) or `non_importe` (hosted, not imported yet).
	 */
	statut: string;
	derniere_maj: string | null;
	nombre_lignes: number | null;
	duree_import_secondes: number | null;
	cron_schedule: string | null;
	fraicheur: string | null;
	prochaine_maj_prevue: string | null;
	derniere_run: {
		statut: string;
		debut: string | null;
		fin: string | null;
		duree_secondes: number | null;
		lignes_chargees: number | null;
		erreur: string | null;
	} | null;
	hebergement?: DatasetHosting | null;
}

interface DatasetsResponse {
	statut: string;
	donnees: DatasetInfo[];
}

export function getDatasets() {
	return api<DatasetsResponse>('/v1/datasets');
}

export interface DataGouvMeta {
	title: string;
	last_modified: string;
	frequency: string | null;
	resources: { title: string; last_modified: string; filesize: number; format: string }[];
}

/** Fetch dataset metadata from data.gouv.fr (public API, CORS OK). */
export async function getDataGouvMeta(slug: string): Promise<DataGouvMeta | null> {
	try {
		const resp = await fetch(`https://www.data.gouv.fr/api/1/datasets/${slug}/`);
		if (!resp.ok) return null;
		return resp.json();
	} catch {
		return null;
	}
}

/** Map dataset names to data.gouv.fr slugs. */
export const DATAGOUV_SLUGS: Record<string, string> = {
	dvf: 'demandes-de-valeurs-foncieres-geolocalisees',
	loyers: 'carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025',
	connectivite: 'ma-connexion-internet',
	scolaire: 'carte-scolaire-des-colleges-publics',
	encadrement_loyers: 'logement-encadrement-des-loyers',
	foncier_pm: 'fichiers-des-locaux-et-des-parcelles-des-personnes-morales',
	fiscalite: 'impots-locaux-fichier-de-recensement-des-elements-dimposition-a-la-fiscalite-directe-locale-rei-4',
	zonage_abc: 'liste-des-communes-selon-le-zonage-abc',
	copropriete: 'registre-national-dimmatriculation-des-coproprietes',
	permis: 'base-des-permis-de-construire-et-autres-autorisations-durbanisme',
	vacance: 'logements-vacants-du-parc-prive-par-commune-departement-region-france',
	antennes: 'observatoire-2g-3g-4g'
};

/** Source info for all datasets. */
export const SOURCE_INFO: Record<string, { nom: string; producteur: string; url: string; type: 'etl' | 'proxy' }> = {
	ban: { nom: 'Base Adresse Nationale (BAN)', producteur: 'DINUM / IGN', url: 'https://adresse.data.gouv.fr', type: 'etl' },
	cadastre: { nom: 'Cadastre Etalab', producteur: 'DGFiP / Etalab', url: 'https://cadastre.data.gouv.fr', type: 'etl' },
	dvf: { nom: 'Demandes de Valeurs Foncieres (DVF)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/datasets/demandes-de-valeurs-foncieres-geolocalisees', type: 'etl' },
	dpe: { nom: 'Diagnostics de Performance Energetique', producteur: 'ADEME', url: 'https://data.ademe.fr/datasets/dpe03existant', type: 'etl' },
	loyers: { nom: 'Carte des Loyers', producteur: 'ANIL / MTE', url: 'https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025', type: 'etl' },
	batiments: { nom: 'BD TOPO Batiments', producteur: 'IGN', url: 'https://geoservices.ign.fr/bdtopo', type: 'etl' },
	monuments: { nom: 'Monuments Historiques', producteur: 'Min. Culture', url: 'https://www.data.gouv.fr/datasets/immeubles-proteges-au-titre-des-monuments-historiques-2', type: 'etl' },
	connectivite: { nom: 'Ma Connexion Internet (Arcep)', producteur: 'Arcep', url: 'https://www.data.gouv.fr/datasets/ma-connexion-internet', type: 'etl' },
	scolaire: { nom: 'Carte Scolaire Colleges', producteur: 'DGESCO / MEN', url: 'https://www.data.gouv.fr/datasets/carte-scolaire-des-colleges-publics', type: 'etl' },
	transports: { nom: 'Arrets de Transport', producteur: 'PAN transport', url: 'https://transport.data.gouv.fr', type: 'etl' },
	gares: { nom: 'Gares SNCF', producteur: 'SNCF', url: 'https://ressources.data.sncf.com', type: 'etl' },
	encadrement_loyers: { nom: 'Encadrement des Loyers', producteur: 'DRIHL', url: 'https://www.data.gouv.fr/datasets/logement-encadrement-des-loyers', type: 'etl' },
	foncier_pm: { nom: 'Fichiers Personnes Morales (MAJIC)', producteur: 'DGFiP / Etalab', url: 'https://www.data.gouv.fr/datasets/fichiers-des-locaux-et-des-parcelles-des-personnes-morales', type: 'etl' },
	fiscalite: { nom: 'Fiscalite Locale des Particuliers', producteur: 'DGFiP', url: 'https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers', type: 'etl' },
	zonage_abc: { nom: 'Zonage ABC', producteur: 'MTE', url: 'https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc', type: 'etl' },
	revenus: { nom: 'Filosofi (Revenus)', producteur: 'INSEE', url: 'https://www.insee.fr/fr/statistiques/7756855', type: 'etl' },
	demographie: { nom: 'Recensement Population', producteur: 'INSEE', url: 'https://www.insee.fr/fr/statistiques/8290591', type: 'etl' },
	copropriete: { nom: 'Registre Coproprietes (RNIC)', producteur: 'ANAH', url: 'https://www.data.gouv.fr/datasets/registre-national-dimmatriculation-des-coproprietes', type: 'etl' },
	permis: { nom: 'Permis - source locale en quarantaine', producteur: 'Attribution a requalifier', url: 'https://www.data.gouv.fr/datasets/base-des-permis-de-construire-et-autres-autorisations-durbanisme', type: 'etl' },
	antennes: { nom: 'Antennes Relais (ANFR)', producteur: 'ANFR', url: 'https://data.anfr.fr', type: 'etl' },
	equipements: { nom: 'Base Permanente Equipements (BPE)', producteur: 'INSEE', url: 'https://www.insee.fr/fr/statistiques/8217537', type: 'etl' },
	vacance: { nom: 'Logements Vacants (LOVAC)', producteur: 'CEREMA', url: 'https://www.data.gouv.fr/datasets/logements-vacants-du-parc-prive-par-commune-departement-region-france', type: 'etl' },
	bdnb: { nom: 'BDNB', producteur: 'CSTB', url: 'https://bdnb.io', type: 'proxy' },
	errial: { nom: 'ERRIAL (Risques)', producteur: 'BRGM / MTE', url: 'https://errial.georisques.gouv.fr', type: 'proxy' },
	georisques: { nom: 'Georisques', producteur: 'BRGM / MTE', url: 'https://georisques.gouv.fr', type: 'proxy' },
	rnb: { nom: 'RNB (Batiments)', producteur: 'beta.gouv.fr', url: 'https://rnb.beta.gouv.fr', type: 'proxy' },
	sirene: { nom: 'API Sirene', producteur: 'INSEE / Etalab', url: 'https://recherche-entreprises.api.gouv.fr', type: 'proxy' },
	urbanisme: { nom: 'API Carto GPU', producteur: 'IGN', url: 'https://apicarto.ign.fr', type: 'proxy' }
};
