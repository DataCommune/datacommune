// CSV export of the address fiche (04.7-03, UI-SPEC v3 § 4.1). Pure module, zero Svelte imports.
//
// WHAT IS EXPORTED: one row per criterion of the report, grouped by part in the order of the page,
// with what the closed row shows (headline, status, scale) and, in the last column, the meaning
// sentence, the other figures with their millésime, the attachment and the sources. The rows go to
// `ExportDialog`, hence the columns of the phase 3 export (« Thème », « Indicateur », « Valeur ou
// motif », « État », « Maille », « Période, population, source et réserves »).
//
// WHAT NEVER IS: an individual sale or price (the report serves none, and the lists of documents and
// sales stay on the page), a technical key (BAN key, RNB or parcel identifier of an attachment), a
// coordinate. The export reads the projected report, the same objects the rows render (A1).

import type { ThemeRapport } from '$lib/api/rapport';
import type { GroupeExport, LigneExport } from './commune-grid';
import {
	PARTIES,
	alerteDe,
	dateFr,
	lecturePartielle,
	libelleEchelle,
	motConfiance,
	statutDe,
	valeurChiffre
} from './adresse-rapport';

const NB = String.fromCharCode(0xa0); // no-break space
const NNB = String.fromCharCode(0x202f); // narrow no-break space

/** Base of the downloaded file: `ExportDialog` appends « -export.csv ». */
export function baseFichierExport(banId: string): string {
	return `adresse-${banId}`;
}

function ligneCritere(t: ThemeRapport, commune: string | null): LigneExport {
	const accroche = t.accroche;
	const statut = statutDe(t);
	const autres = t.chiffres.slice(accroche && t.chiffres.length ? 1 : 0).map(
		(c) => `${c.libelle}${NB}: ${valeurChiffre(c)}${c.millesime ? ` (${dateFr(c.millesime)})` : ''}`
	);
	const sources = t.sources.map((s) => [s.nom, s.producteur, s.millesime ? dateFr(s.millesime) : null].filter(Boolean).join(', '));
	const reserves = [
		accroche ? t.verdict : null,
		...autres,
		alerteDe(t),
		lecturePartielle(t),
		t.rattachement ? `Rattachement ${motConfiance(t.rattachement.confiance)}` : null,
		sources.length ? `${sources.length > 1 ? 'Sources' : 'Source'}${NB}: ${sources.join(`${NNB}; `)}` : null
	].filter((x): x is string => Boolean(x));
	return {
		label: t.titre,
		value: accroche ?? t.verdict,
		state: statut?.texte ?? 'Valeur lue',
		presentation: { grain: libelleEchelle(t.granularite, commune), reserves }
	};
}

/** What the file says of the address it describes: served labels only, never the BAN key nor the parcel. */
export interface IdentiteExport {
	adresse: string | null;
	commune: string;
	departement: string;
}

/** Group « Identité », first in the file, as in the export of the published fiche (04.7-13 parity). */
function groupeIdentite(id: IdentiteExport): GroupeExport {
	const ligne = (label: string, value: string, grain: string): LigneExport => ({
		label,
		value,
		state: 'Valeur lue',
		presentation: { grain, reserves: [] }
	});
	const rows = [
		...(id.adresse ? [ligne('Adresse', id.adresse, 'Adresse')] : []),
		ligne('Commune', id.commune, 'Commune'),
		ligne('Département', id.departement, 'Département')
	];
	return { key: 'identite', name: 'Identité', rows };
}

/**
 * The criteria of the report, one group per part in the order of the page (empty parts left out),
 * preceded by the identity of the address when it is given and the report serves a criterion.
 */
export function lignesExportRapport(
	themes: readonly ThemeRapport[],
	commune: string | null,
	identite: IdentiteExport | null = null
): GroupeExport[] {
	const parties = PARTIES.map((p) => ({
		key: p.id,
		name: p.titre,
		rows: themes.filter((t) => t.section === p.id).map((t) => ligneCritere(t, commune))
	})).filter((g) => g.rows.length > 0);
	return identite && parties.length > 0 ? [groupeIdentite(identite), ...parties] : parties;
}
