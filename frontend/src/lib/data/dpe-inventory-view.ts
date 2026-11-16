import type {
	DpeConsultedDocument,
	DpeInventoryDistribution,
	DpeInventoryItem,
	DpeInventoryPage
} from '$lib/api/dpe-inventory';

const FR_LOCALE = 'fr-FR';
const DPE_CLASSES = ['A', 'B', 'C', 'D', 'E', 'F', 'G'] as const;

export type DpeClass = (typeof DPE_CLASSES)[number];
export type DpeInventoryPageUiStatus = 'ready' | 'loading' | 'error' | 'refresh_required';
export type DpeDocumentUiStatus = 'idle' | 'loading' | 'ready' | 'error' | 'refresh_required';

export interface DpeInventoryPageState {
	status: DpeInventoryPageUiStatus;
	currentPage: number;
	hasPrevious: boolean;
	errorMessage?: string | null;
}

export interface DpeDistributionView {
	label: DpeClass | 'Classes non disponibles';
	count: number;
}

export interface DpeInventoryRowView {
	documentRef: string;
	ordinal: number;
	documentLabel: string;
	typeLabel: string;
	surfaceLabel: string;
	dpeClass: DpeClass | null;
	dpeLabel: string;
	gesLabel: string;
	establishedLabel: string;
	validUntilLabel: string;
	showValidUntil: boolean;
	lifecycleRows: DpeDocumentRowView[];
}

export interface DpeInventoryView {
	title: 'Diagnostics connus à cette adresse';
	scopeNote: string;
	addressCheckLabel: string;
	totalLabel: string;
	distribution: DpeDistributionView[];
	rows: DpeInventoryRowView[];
	pageLabel: string;
	rangeLabel: string;
	hasMore: boolean;
	nextCursor: string | null;
	notes: string[];
	sourceLabel: string;
	importedAtLabel: string;
	emptyLabel: string | null;
}

export interface DpeDocumentRowView {
	label: string;
	value: string;
	energyClass?: DpeClass | null;
}

export interface DpeDocumentView {
	title: 'Diagnostic consulté';
	reserve: "Ce document n'est pas attribué au logement recherché";
	rows: DpeDocumentRowView[];
	validityNote: string;
	sourceLabel: string;
	importedAtLabel: string;
	addressCheckLabel: string;
	graphNote: string;
}

function formatNumber(value: number, maximumFractionDigits = 1): string {
	return new Intl.NumberFormat(FR_LOCALE, { maximumFractionDigits }).format(value);
}

function absent(gender: 'masculine' | 'feminine'): string {
	return gender === 'feminine' ? 'Non renseignée' : 'Non renseigné';
}

export function formatDpeAnnualValue(
	value: number | null,
	unit: 'kWhEP/an' | 'kgCO₂eq/an' | '€/an'
): string {
	return value == null ? 'Non disponible' : `${formatNumber(value)} ${unit}`;
}

function formatDate(value: string | null, gender: 'masculine' | 'feminine'): string {
	if (value == null || value.trim() === '') return absent(gender);
	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) return absent(gender);
	return new Intl.DateTimeFormat(FR_LOCALE, {
		day: '2-digit',
		month: '2-digit',
		year: 'numeric',
		timeZone: 'UTC'
	}).format(parsed);
}

function formatImportDate(
	value: string | null,
	absentLabel: 'non renseignée' | 'non disponible' = 'non renseignée'
): string {
	if (value == null || value.trim() === '') {
		return `Fraîcheur de l’import : ${absentLabel}`;
	}
	const parsed = new Date(value);
	if (Number.isNaN(parsed.getTime())) {
		return `Fraîcheur de l’import : ${absentLabel}`;
	}
	const formatted = new Intl.DateTimeFormat(FR_LOCALE, {
		day: '2-digit',
		month: '2-digit',
		year: 'numeric',
		timeZone: 'UTC'
	}).format(parsed);
	return `Dernier import disponible : ${formatted}`;
}

function energyClass(value: string | null): DpeClass | null {
	const normalized = value?.trim().toUpperCase();
	return DPE_CLASSES.find((candidate) => candidate === normalized) ?? null;
}

function formatType(value: string | null): string {
	return value == null || value.trim() === '' ? 'Type non renseigné' : value.trim();
}

function formatSurface(value: number | null): string {
	if (value == null) return 'Surface non renseignée';
	return `${formatNumber(value)} m² habitables DPE`;
}

function formatClass(value: string | null): { className: DpeClass | null; label: string } {
	const className = energyClass(value);
	return {
		className,
		label: className == null ? 'Classe non renseignée' : `Classe ${className}`
	};
}

function distributionView(distribution: DpeInventoryDistribution): DpeDistributionView[] {
	return [
		...DPE_CLASSES.map((label) => ({ label, count: distribution[label] })),
		{ label: 'Classes non disponibles' as const, count: distribution.unknown }
	];
}

/** Presentation only: the API projection validates all lifecycle facts and their server date. */
export function deriveDpeLifecycleRows(item: DpeInventoryItem): DpeDocumentRowView[] {
 const cycle = item.lifecycle;
 const unavailable = 'Cycle de vie non disponible';
 if (cycle == null) return [{ label: 'Cycle de vie', value: unavailable }];
 const end = formatDate(item.date_fin_validite_dpe, 'feminine');
 const validity = {
  unknown: item.date_fin_validite_dpe == null
   ? 'Fin de validité non renseignée'
   : 'Date d’établissement non renseignée — validité indéterminée',
  expired: `Validité expirée depuis le ${end}`,
  not_expired_by_date: `Fin de validité déclarée : ${end}`,
  inconsistent_dates: 'Dates incohérentes — validité indéterminée',
  future_establishment: 'Date d’établissement future — validité indéterminée'
 }[cycle.validity] ?? unavailable;
 const presence = {
  present: `Présent dans l’instantané source du ${formatDate(cycle.source_snapshot_date, 'feminine')}`,
  absent_from_complete_scope: 'Absent du dernier instantané complet du périmètre — motif inconnu',
  explicitly_deactivated: 'Désactivation signalée par la source',
  unknown: 'Présence dans la source non déterminée'
 }[cycle.source_state] ?? unavailable;
 const replacement = {
  not_checked: unavailable,
  no_successor_known: 'Aucun remplacement connu dans les données disponibles',
  replaced: 'Remplacé selon la source',
  ambiguous: 'Cible de remplacement ambiguë'
 }[cycle.replacement_state] ?? unavailable;
 const issues = {
  cycle: 'Cycle de remplacement détecté', fork: 'Plusieurs remplacements signalés',
  missing_target: 'Lien de remplacement hors des documents consultables',
  truncated: 'Chaîne de remplacement incomplète', ambiguous_target: 'Cible de remplacement ambiguë'
 };
 const regimes = cycle.regimes.map(regime => ({existant:'Logements existants',neuf:'Logements neufs'})[regime]).filter(Boolean);
 const rows: DpeDocumentRowView[] = [
  {label:'Validité',value:validity}, {label:'Présence dans la source',value:presence},
  {label:'Périmètre source',value:cycle.source_scope === '44/85' ? 'Périmètre 44/85' : unavailable},
  {label:'Remplacement',value:replacement},
  ...cycle.graph_issues.map(issue => ({label:`Réserve — ${issues[issue] ?? unavailable}`,value:issues[issue] ?? unavailable})),
  {label:'Régime déclaré',value:regimes.length ? regimes.join(' · ') : 'Régime non renseigné'},
  {label:'Date de référence',value:`Date de référence : ${formatDate(cycle.as_of_date, 'feminine')}`}
 ];
 if (cycle.source_state !== 'present' && cycle.source_snapshot_date != null) {
  rows.push({label:'Date du constat',value:`Date du constat : ${formatDate(cycle.source_snapshot_date, 'feminine')}`});
 }
 if (cycle.detail_availability === 'source_reconciled') {
  rows.push({label:'Disponibilité du détail',value:'Détail non disponible après réconciliation de la source'});
 }
 return rows;
}

function inventoryRow(item: DpeInventoryItem, ordinal: number): DpeInventoryRowView {
	const dpe = formatClass(item.etiquette_dpe);
	const ges = formatClass(item.etiquette_ges);
	const establishedDate = formatDate(item.date_etablissement_dpe, 'feminine');
	const validUntilDate = formatDate(item.date_fin_validite_dpe, 'feminine');
	return {
		documentRef: item.document_ref,
		ordinal,
		documentLabel: `Document ${String(ordinal).padStart(2, '0')}`,
		typeLabel: formatType(item.type_batiment),
		surfaceLabel: formatSurface(item.surface_habitable),
		dpeClass: dpe.className,
		dpeLabel: dpe.label,
		gesLabel: ges.label,
		establishedLabel:
			establishedDate === 'Non renseignée' ? 'Date non renseignée' : `Établi le ${establishedDate}`,
		validUntilLabel:
			validUntilDate === 'Non renseignée'
				? 'Fin de validité non renseignée'
				: `Fin de validité déclarée : ${validUntilDate}`,
		showValidUntil: item.lifecycle == null ||
			(item.lifecycle.validity === 'unknown' && item.date_fin_validite_dpe != null),
		lifecycleRows: deriveDpeLifecycleRows(item)
	};
}

function totalLabel(total: number | null): string {
	if (total == null) return 'Effectif documentaire non disponible';
	if (total === 1) return '1 document connu';
	return `${formatNumber(total, 0)} documents connus`;
}

function pageLabel(total: number | null, pageSize: number, currentPage: number): string {
	if (total == null) return `Page ${currentPage} · total inconnu`;
	const pages = Math.max(1, Math.ceil(total / pageSize));
	return `Page ${currentPage} sur ${pages}`;
}

function rangeLabel(
	total: number | null,
	pageSize: number,
	currentPage: number,
	rowCount: number
): string {
	if (rowCount === 0) return total == null ? 'Aucun document chargé' : `0 document sur ${total}`;
	const first = (currentPage - 1) * pageSize + 1;
	const last = first + rowCount - 1;
	const prefix = rowCount === 1 ? 'Document' : 'Documents';
	return total == null
		? `${prefix} ${first}${rowCount === 1 ? '' : ` à ${last}`} · total inconnu`
		: `${prefix} ${first}${rowCount === 1 ? '' : ` à ${last}`} sur ${total}`;
}

function inventoryNotes(inventory: DpeInventoryPage): string[] {
	const notes = [
		inventory.read_state === 'partial'
			? 'Lecture partielle du périmètre importé.'
			: 'Lecture complète du périmètre importé ; la couverture de tous les diagnostics existants n’est pas garantie.',
		'Couverture de la source ADEME : inconnue.'
	];
	if (inventory.as_of_date != null) notes.push(`Date de référence : ${formatDate(inventory.as_of_date, 'feminine')}`);
	if (inventory.items.some(item => item.lifecycle != null)) notes.push('Périmètre source : 44/85.');
	if (inventory.graph_state === 'incoherent') {
		notes.push(
			'Graphe de remplacement incohérent : aucun document n’est écarté silencieusement.'
		);
	} else if (inventory.items.some(item => item.lifecycle == null) || inventory.items.length === 0) {
		notes.push('Graphe de remplacement : non vérifié dans cet inventaire.');
	}
	return notes;
}

function emptyLabel(inventory: DpeInventoryPage): string | null {
	if (inventory.state === 'identity_unavailable') {
		return "L’identité de l’adresse ne permet pas de constituer cet inventaire.";
	}
	if (inventory.state === 'unavailable') {
		return 'Les diagnostics sont momentanément indisponibles.';
	}
	if (inventory.state === 'ready' && inventory.total != null && inventory.total > 0 && inventory.items.length === 0) {
		return 'Aucun document chargé sur cette page.';
	}
	if (inventory.state === 'absent' || inventory.items.length === 0) {
		return 'Aucun document disponible à cette adresse';
	}
	return null;
}

export function deriveDpeInventoryView(
	inventory: DpeInventoryPage,
	currentPage = 1
): DpeInventoryView {
	const safePage = Math.max(1, Math.trunc(currentPage));
	const firstOrdinal = (safePage - 1) * inventory.page_size + 1;
	return {
		title: 'Diagnostics connus à cette adresse',
		scopeNote:
			'Association déclarée par la source. L’appartenance au logement reste à établir.',
		addressCheckLabel: 'Adresse brute ADEME : non vérifiée',
		totalLabel: totalLabel(inventory.total),
		distribution: distributionView(inventory.distribution),
		rows: inventory.items.map((item, index) => inventoryRow(item, firstOrdinal + index)),
		pageLabel: pageLabel(inventory.total, inventory.page_size, safePage),
		rangeLabel: rangeLabel(
			inventory.total,
			inventory.page_size,
			safePage,
			inventory.items.length
		),
		hasMore: inventory.has_more,
		nextCursor: inventory.next_cursor,
		notes: inventoryNotes(inventory),
		sourceLabel: 'Source : ADEME',
		importedAtLabel: formatImportDate(inventory.imported_at),
		emptyLabel: emptyLabel(inventory)
	};
}

function textValue(value: string | null): string {
	return value == null || value.trim() === '' ? 'Non disponible' : value.trim();
}

function yearValue(value: number | null): string {
	return value == null ? 'Non disponible' : String(Math.trunc(value));
}

function detailDate(value: string | null): string {
	if (value == null || value.trim() === '') return 'Non disponible';
	const formatted = formatDate(value, 'feminine');
	return formatted === 'Non renseignée' ? 'Non disponible' : formatted;
}

function validityNote(document: DpeConsultedDocument): string {
	if (document.validity_by_date === 'unknown') {
		return 'La validité par date est inconnue ; aucun statut de remplacement ou d’annulation n’est déduit.';
	}
	if (document.validity_by_date === 'expired') {
		return 'Selon la date déclarée, la fin de validité est dépassée ; le statut de remplacement ou d’annulation reste inconnu.';
	}
	return 'La fin de validité n’est pas dépassée selon la date déclarée ; cela ne garantit pas l’absence de remplacement ou d’annulation.';
}

function graphNote(document: DpeConsultedDocument): string {
	return document.graph_state === 'incoherent'
		? 'Le graphe de remplacement connu est incohérent ; ce document reste présenté sans conclusion de cycle de vie.'
		: 'Le graphe de remplacement n’est pas vérifié par cette consultation.';
}

export function deriveDpeDocumentView(document: DpeConsultedDocument): DpeDocumentView {
	const dpe = formatClass(document.etiquette_dpe);
	const ges = formatClass(document.etiquette_ges);
	return {
		title: 'Diagnostic consulté',
		reserve: "Ce document n'est pas attribué au logement recherché",
		rows: [
			{ label: 'Type de bâtiment', value: textValue(document.type_batiment) },
			{
				label: 'Surface habitable',
				value:
					document.surface_habitable == null
						? 'Non disponible'
						: formatSurface(document.surface_habitable)
			},
			{
				label: 'Étiquette DPE',
				value: dpe.className == null ? 'Non disponible' : dpe.label,
				energyClass: dpe.className
			},
			{
				label: 'Étiquette GES',
				value: ges.className == null ? 'Non disponible' : ges.label,
				energyClass: ges.className
			},
			{
				label: 'Date d’établissement',
				value: detailDate(document.date_etablissement_dpe)
			},
			{
				label: 'Fin de validité déclarée',
				value: detailDate(document.date_fin_validite_dpe)
			},
			{ label: 'Année de construction', value: yearValue(document.annee_construction) },
			{
				label: 'Période de construction',
				value: textValue(document.periode_construction)
			},
			{
				label: 'Installation de chauffage',
				value: textValue(document.type_installation_chauffage)
			},
			{
				label: 'Énergie principale de chauffage',
				value: textValue(document.type_energie_principale_chauffage)
			},
			{
				label: 'Isolation de l’enveloppe',
				value: textValue(document.qualite_isolation_enveloppe)
			},
			{
				label: 'Consommation primaire (5 usages)',
				value: formatDpeAnnualValue(document.conso_5_usages_ep, 'kWhEP/an')
			},
			{
				label: 'Émissions (5 usages)',
				value: formatDpeAnnualValue(document.emission_ges_5_usages, 'kgCO₂eq/an')
			},
			{
				label: 'Coût estimé (5 usages)',
				value: formatDpeAnnualValue(document.cout_total_5_usages, '€/an')
			}
		],
		validityNote: validityNote(document),
		sourceLabel: 'Source : ADEME',
		importedAtLabel: formatImportDate(document.imported_at, 'non disponible'),
		addressCheckLabel: 'Adresse brute ADEME : non vérifiée',
		graphNote: graphNote(document)
	};
}
