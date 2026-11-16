import type { FicheCriticalSummary, FicheGrid, FicheGridCell } from '$lib/types/fiche-grid';
import type { CommuneSynthese, FenetreIndicateur, IndicateursDpe, IndicateursDvf } from '$lib/api/commune';

/** Pure presentation projection for additive Commune indicators. */
export function enrichCommuneIndicatorPresentation(grid: FicheGrid, commune?: CommuneSynthese): FicheGrid {
	const communeColumns = new Set(grid.columns.filter((column) => column.entity === 'commune').map((column) => column.id));
	let changed = false;
	const rows = grid.rows.map((row) => {
		if (!isAdditiveCommuneIndicator(row.concept.id)) return row;
		let rowChanged = false;
		const cells = Object.fromEntries(Object.entries(row.cells).map(([columnId, cell]) => {
			if (!communeColumns.has(columnId)) return [columnId, cell];
			rowChanged = true;
			return [columnId, { ...cell, ...typedMetadata(row.concept.id, commune), criticalSummary: criticalSummaryFor(cell) }];
		}));
		if (!rowChanged) return row;
		changed = true;
		return { ...row, cells };
	});
	return changed ? { ...grid, rows } : grid;
}

function isAdditiveCommuneIndicator(conceptId: string): boolean {
	return /:(?:context:)?(?:dvf|dpe)-/.test(conceptId);
}

function windowLabel(window: FenetreIndicateur | null): string | null {
	return window ? `${window.debut_inclus} → ${window.fin_exclue} (fin exclue)` : null;
}

function sourceMetadata(block: IndicateursDvf | IndicateursDpe) {
	const names = [...new Set(block.provenance.map(source => source.nom).filter(Boolean))];
	const reference = block.provenance[0]?.reference;
	const commonUrl = reference && block.provenance.every(source => source.reference === reference) ? reference : null;
	return names.length ? { source: { nom: names.join(' · '), url: commonUrl } } : {};
}

function typedMetadata(conceptId: string, commune?: CommuneSynthese): Partial<FicheGridCell> {
	if (!commune) return {};
	if (/:dvf-/.test(conceptId)) {
		const block = commune.indicateurs_dvf;
		if (!block || block.version_contrat !== '1.1') return {};
		if (conceptId.endsWith('dvf-ancrage')) {
			const anchor = block.ancrage_temporel;
			const millesime = anchor.etat === 'delivery_received_and_stock_bound'
				? windowLabel({ debut_inclus: anchor.periode_debut_inclus, fin_exclue: anchor.periode_fin_exclue })
				: null;
			return { ...sourceMetadata(block), millesime, estDatable: millesime !== null };
		}
		const property = conceptId.includes('appartement') ? block.residentiel_appartement : block.residentiel_maison;
		if (!property) return { ...sourceMetadata(block), millesime: null, estDatable: false };
		const current = windowLabel(property.courante.fenetre);
		const previous = windowLabel(property.precedente.fenetre);
		const millesime = conceptId.includes('variation')
			? previous && current ? `${previous} / ${current}` : null
			: /-precedent(?:e)?$/.test(conceptId) ? previous : current;
		return { ...sourceMetadata(block), millesime, estDatable: millesime !== null };
	}
	if (/:dpe-/.test(conceptId)) {
		const block = commune.indicateurs_dpe;
		if (!block || block.version_contrat !== '1.0') return {};
		const millesime = block.date_evaluation ? `Évaluation : ${block.date_evaluation}` : null;
		return { ...sourceMetadata(block), millesime, estDatable: millesime !== null };
	}
	return {};
}

function normalized(value: string): string {
	return value.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLocaleLowerCase('fr-FR').trim();
}

function isTechnicalMetadata(value: string): boolean {
	const text = normalized(value);
	const label = text.split(':', 1)[0].trim();
	return /^(?:calcul|generation(?: fournisseur| stock)?|version(?: du)? contrat|source(?:\(s\))?|producteur|licence|reference|(?:publication source|source publication|date publication source)|snapshot|import|couverture|methode|unite)$/.test(label)
		|| /(?:^|[_ -])(?:sha(?:-?256)?|hash|empreinte|digest)(?:$|[_ -])/.test(label);
}

function structuredCount(value: string): number | null {
	const match = normalized(value).match(/:\s*(-?\d[\d\s]*)\s*$/);
	return match ? Number(match[1].replace(/\s/g, '')) : null;
}

const structuredAxes: Readonly<Record<string, readonly string[]>> = {
	type: ['maison', 'appartement', 'autre', 'inconnu'],
	validite: ['unknown', 'expired', 'not_expired_by_date', 'inconsistent_dates', 'future_establishment'],
	remplacement: ['not_checked', 'no_successor_known', 'replaced', 'ambiguous'],
	regime: ['existant', 'neuf', 'inconnu']
};

function isStructuredDetail(value: string): boolean {
	const text = normalized(value);
	const axis = text.match(/\[(type|validite|remplacement|regime) ([a-z_]+)\s*:/)
		?? text.match(/^(type|validite|remplacement|regime) ([a-z_]+)\s*:/);
	return Boolean(axis && structuredCount(value) !== null && structuredAxes[axis[1]]?.includes(axis[2]));
}

function isCriticalUnknown(value: string): boolean {
	const text = normalized(value);
	const count = structuredCount(value);
	if (count === 0 && /(?:inconn|unknown|non verifie|ambigu|incoher|future|expire|remplace)/.test(text)) return false;
	return /(?:inconn|unknown|partiel|partial|non renseigne|non verifie|ambigu|incoher|future|expire|remplace)/.test(text);
}

function isCriticalStructuredUnknown(value: string): boolean {
	const count = structuredCount(value);
	return count !== null && count > 0
		&& /(?:inconnu|unknown|inconsistent_dates|future_establishment|ambiguous|not_checked|remplacement replaced\s*:|validite expired\s*:)/.test(normalized(value));
}

function summaryLabel(value: string): string {
	return value.replace(/\s*\[(?:Type|Validité|Remplacement|Régime) [a-z_]+ : -?\d[\d\s]*\](?=\s:\s)/u, '');
}

function isFact(value: string): boolean {
	const text = normalized(value);
	return /^(?:periode|fenetre precedente\s*—\s*periode|evaluation|population\s*:|grain\s*:)/.test(text)
		|| /^\d[\d\s ]*(?:mutations?|diagnostics? disponibles?|classes? connues?)/.test(text)
		|| /^qualification\s*:/.test(text);
}

function isKnownWarning(value: string): boolean {
	const text = normalized(value);
	return /^(?:etat source|motif source|population statistique)\s*:/.test(text)
		|| /(?:insuffis|supprim|confidential|hors |exclu|ne representent pas|aucune |zero denominator|non qualifi)/.test(text);
}

function criticalSummaryFor(cell: FicheGridCell): FicheCriticalSummary {
	const facts: string[] = [];
	const warnings: string[] = [];
	const add = (target: string[], value: string) => {
		if (!target.includes(value)) target.push(value);
	};
	for (const reserve of cell.reserves) {
		// Preserve the window label in the output, but classify the underlying metadata.
		const content = reserve.replace(/^Fenêtre précédente — /, '');
		if (isFact(content)) add(facts, reserve);
		else if (isStructuredDetail(content)) {
			if (isCriticalStructuredUnknown(content)) add(warnings, summaryLabel(reserve));
		}
		else if (isCriticalUnknown(content) || isKnownWarning(content)) add(warnings, reserve);
		else if (isTechnicalMetadata(content)) continue;
		else add(warnings, reserve);
	}
	if (cell.kind === 'absent') {
		const explanation = cell.reason?.explanation ?? cell.explanation;
		if (explanation && !cell.reserves.some((reserve) => reserve.includes(explanation))) add(warnings, explanation);
	}
	return { facts, warnings };
}
