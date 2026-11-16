import type { ThemeModel } from '$lib/types/fiche';

/** Keep values and qualified absences visible in the exported review. */
export function bienThemeCsvRows(theme: ThemeModel): string[][] {
	const scopedRows = [
		...(theme.rowsBien ?? theme.rows).map(row => ({ row, scope: undefined })),
		...(theme.rowsCommune ?? []).map(row => ({ row, scope: 'Commune' }))
	];
	const rows = scopedRows.map(({ row }) => row);
	const values = rows.map(row => [row.label, row.value]);
	// Add public presentation context without reconstructing any hidden value or threshold.
	const contexts = scopedRows.flatMap(({ row, scope }) => {
		// The explicit Commune compartment supplies only a fallback, never a Bien attribution.
		const grain = row.presentation?.grain ?? scope;
		return [
			...(grain ? [[`Portée — ${row.label}`, grain]] : []),
			...(row.presentation?.state && row.presentation.state !== 'value'
				? [[`État — ${row.label}`, row.presentation.state]] : []),
			...(row.presentation?.state !== 'value' && row.presentation?.reason
				? [[`Motif — ${row.label}`, [
					row.presentation.reason.label,
					row.presentation.reason.explanation,
					row.presentation.reason.evidence
				].filter(Boolean).join(' · ')]] : []),
			...(row.presentation?.reserves ?? []).map(reserve => [`Réserve — ${row.label}`, reserve])
		];
	});
	const seen = new Set(values.map(row => JSON.stringify(row)));
	return [...values, ...contexts.filter(row => {
		const key = JSON.stringify(row);
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	})];
}
