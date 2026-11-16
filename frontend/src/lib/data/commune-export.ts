// CSV export of the commune fiche by the three categories of the page (C1, inventory of 2026-11-10,
// arbitrated by Ianis on 2026-11-10: « oui aux trois catégories de la fiche commune, export CSV compris »).
// This amends D-35, which kept the five former themes in the export.
//
// Pure module. It only moves the rows `themesPourExport` already builds: each exported row and note keeps
// its label, value, state, grain and caveats, and is read under the category and the group the page shows
// it in (`categoriesCommune`). Nothing is computed, nothing is dropped.
import { categoriesCommune } from './commune-categories';
import { themesPourExport, type GroupeExport, type LigneExport, type ThemeFiche } from './commune-grid';

/** The categories of the fiche in their order, for the CSV export: the rows of each group, then its notes. */
export function categoriesPourExport(themes: ThemeFiche[]): GroupeExport[] {
	const parId = new Map<string, LigneExport>();
	for (const theme of themesPourExport(themes)) {
		for (const ligne of theme.rows) if (ligne.id) parId.set(ligne.id, ligne);
	}
	return categoriesCommune(themes)
		.map((categorie): GroupeExport => ({
			key: categorie.id,
			name: categorie.titre,
			rows: categorie.groupes
				.flatMap((groupe) => [...groupe.lignes.map((ligne) => ligne.id), ...groupe.notes.map((note) => note.id)])
				.flatMap((id) => (parId.has(id) ? [parId.get(id)!] : []))
		}))
		.filter((groupe) => groupe.rows.length > 0);
}
