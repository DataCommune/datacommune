import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type Locator, type Page, type Request } from '@playwright/test';
import { ouvrir } from '../e2e/fixtures/recette-adresses';

// Measurement walk of the parcel map on REAL data (plan 04.10-12), outside the hermetic gate. Played
// by the orchestrator through `infra/pve02/workbase-recette-replay.sh carte-parcelles` (window opened
// and closed by that script), twice: the second run reads the base tiles from the API cache.
//
// At 375 then 1 440 px: open the map of the rich address of the private keys file, wait for the
// parcels, zoom to both bounds, move one screen in four directions, choose three parcels from the
// list, show a group when a sale carries one, close. Every `/carte/` request is recorded (path,
// status, bytes, duration, start time, width) into a JSON file OUTSIDE the repository, with one
// capture per zoom level. No header, key or cookie is recorded. This spec judges no threshold
// (04.10-mesure-verdict.mjs does); it fails only if the walk cannot be made or a request leaves the
// local origin.
//
//   DC_RELEVE_CARTE   output directory, outside the repository (default C:/tmp/dc-0410-12/releves)
//   DC_RELEVE_PASSAGE run number written into the file name and the record (default 1)

const LARGEURS = [375, 1440] as const;
const ZOOM_OUVERTURE = 18;
const ZOOM_MIN = 16;
const ZOOM_MAX = 19;
const ATTENTE_MS = 30_000;
const FICHIER_PRIVE = new URL('./private/adresses-reel.json', import.meta.url);
const RACINE_DEPOT = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const SORTIE = resolve(process.env.DC_RELEVE_CARTE ?? 'C:/tmp/dc-0410-12/releves');
const PASSAGE = Number(process.env.DC_RELEVE_PASSAGE ?? '1');

/** A gesture that changes or leaves the view: the only causes of a cancelled tile (04.10-mesure-verdict.mjs). */
interface Geste {
	t: number;
	largeur: number;
	geste: 'zoom' | 'deplacement' | 'groupe' | 'fermeture';
}

interface Requete {
	chemin: string;
	annulee?: true;
	statut: number;
	octets: number | null;
	duree_ms: number | null;
	t: number;
	/** Time of the cancellation, for a cancelled request: a gesture must come between `t` and it. */
	t_fin?: number;
	largeur: number;
}

function adresseRiche(): string {
	if (!existsSync(FICHIER_PRIVE)) throw new Error('Fichier privé absent : frontend/tests/recette/private/adresses-reel.json.');
	const cle = (JSON.parse(readFileSync(FICHIER_PRIVE, 'utf8')) as { adresse_riche?: string }).adresse_riche;
	if (!cle) throw new Error('Champ « adresse_riche » absent du fichier privé.');
	return cle;
}

function dossierDeSortie(): string {
	const rel = relative(RACINE_DEPOT, SORTIE);
	if (!rel.startsWith('..') && !isAbsolute(rel)) throw new Error(`DC_RELEVE_CARTE doit être hors du dépôt : ${SORTIE}`);
	mkdirSync(join(SORTIE, 'captures'), { recursive: true });
	return SORTIE;
}

/** One record per finished or failed request under `/carte/`; foreign origins are kept apart. */
function relever(page: Page, largeur: () => number, origine: string) {
	const requetes: Requete[] = [];
	const etrangeres: string[] = [];
	const noter = async (requete: Request, echec: boolean) => {
		const fin = Date.now();
		const url = new URL(requete.url());
		if (url.protocol === 'data:' || url.protocol === 'blob:') return;
		if (url.origin !== origine) {
			etrangeres.push(url.origin);
			return;
		}
		if (!url.pathname.includes('/carte/')) return;
		const timing = requete.timing();
		const reponse = echec ? null : await requete.response();
		const tailles = echec ? null : await requete.sizes().catch(() => null);
		// Leaflet cancels the tiles a zoom or a move made useless: not a failure of the service.
		const annulee = echec && /ABORTED|cancel/i.test(requete.failure()?.errorText ?? '');
		requetes.push({
			chemin: url.pathname,
			...(annulee ? { annulee: true } : {}),
			statut: reponse?.status() ?? 0,
			octets: tailles?.responseBodySize ?? null,
			duree_ms: timing.responseEnd > 0 ? Math.round(timing.responseEnd * 10) / 10 : null,
			t: Math.round(timing.startTime),
			...(annulee ? { t_fin: fin } : {}),
			largeur: largeur()
		});
	};
	const enCours: Promise<void>[] = [];
	page.on('requestfinished', (r) => void enCours.push(noter(r, false)));
	page.on('requestfailed', (r) => void enCours.push(noter(r, true)));
	return { requetes, etrangeres, toutNote: () => Promise.all(enCours) };
}

async function capturer(dialogue: Locator, largeur: number, zoom: number) {
	await dialogue.locator('.carte-cadre').screenshot({ path: join(SORTIE, 'captures', `p${PASSAGE}-${largeur}-z${zoom}.png`) });
}

/**
 * A parcel drawn in the view and no tile still loading. Leaflet keeps the paths that left the view
 * with an empty outline (`d="M0 0"`, hidden): the first path of the layer may be one of them.
 */
async function attendreParcelles(dialogue: Locator) {
	await expect(dialogue.locator('path.parcel:not([d="M0 0"])').first()).toBeVisible({ timeout: ATTENTE_MS });
	await expect(dialogue.locator('.leaflet-tile-loading')).toHaveCount(0, { timeout: ATTENTE_MS });
}

async function glisser(page: Page, vue: Locator, dx: number, dy: number) {
	const boite = await vue.boundingBox();
	if (!boite) throw new Error('carte sans boîte');
	const x = boite.x + boite.width / 2;
	const y = boite.y + boite.height / 2;
	await page.mouse.move(x, y);
	await page.mouse.down();
	await page.mouse.move(x + dx * (boite.width / 2 - 4), y + dy * (boite.height / 2 - 4), { steps: 12 });
	await page.mouse.up();
}

test('carte-parcelles — parcours de mesure sur données réelles, 375 puis 1 440 px', async ({ page, baseURL }) => {
	test.setTimeout(600_000);
	const cle = adresseRiche();
	dossierDeSortie();
	let largeurCourante: number = LARGEURS[0];
	const { requetes, etrangeres, toutNote } = relever(page, () => largeurCourante, new URL(baseURL ?? '').origin);
	const actions: Geste[] = [];
	const geste = (nom: Geste['geste']) => actions.push({ t: Date.now(), largeur: largeurCourante, geste: nom });

	for (const largeur of LARGEURS) {
		largeurCourante = largeur;
		await page.setViewportSize({ width: largeur, height: largeur < 768 ? 812 : 900 });
		await ouvrir(page, `/adresse/${cle}`);
		await page.getByRole('button', { name: 'Ouvrir la carte des parcelles' }).click();
		const dialogue = page.getByRole('dialog', { name: 'Parcelles autour de l’adresse' });
		const vue = dialogue.locator('.carte-vue.leaflet-container');
		await expect(vue).toBeVisible({ timeout: ATTENTE_MS });
		await attendreParcelles(dialogue);
		await capturer(dialogue, largeur, ZOOM_OUVERTURE);

		const avant = dialogue.getByRole('button', { name: 'Zoom avant' });
		const arriere = dialogue.getByRole('button', { name: 'Zoom arrière' });
		for (let z = ZOOM_OUVERTURE + 1; z <= ZOOM_MAX; z++) {
			geste('zoom');
			await avant.click();
			await attendreParcelles(dialogue);
			await capturer(dialogue, largeur, z);
		}
		await expect(avant).toBeDisabled();
		for (let z = ZOOM_MAX - 1; z >= ZOOM_MIN; z--) {
			geste('zoom');
			await arriere.click();
			await attendreParcelles(dialogue);
			if (z < ZOOM_OUVERTURE) await capturer(dialogue, largeur, z);
		}
		await expect(arriere).toBeDisabled();

		for (const [dx, dy] of [[-1, 0], [0, -1], [1, 0], [0, 1]] as const) {
			geste('deplacement');
			await glisser(page, vue, dx, dy);
			await attendreParcelles(dialogue);
		}

		const lignes = dialogue.locator('section.parcelles ul.parcelles-lignes > li > button');
		await expect(lignes.first()).toBeVisible({ timeout: ATTENTE_MS });
		const panneau = dialogue.locator('section.parcelle');
		const ventes = panneau.locator('section.parcelle-ventes');
		let groupeMontre = false;
		for (let i = 0; i < Math.min(3, await lignes.count()); i++) {
			await lignes.nth(i).click();
			await expect(ventes).not.toHaveAttribute('aria-busy', 'true', { timeout: ATTENTE_MS });
			const bascule = panneau.getByRole('button', { name: 'Montrer ces parcelles' }).first();
			if (!groupeMontre && (await bascule.count())) {
				geste('groupe');
				await bascule.click();
				await expect(dialogue.locator('path.parcel-group').first()).toBeVisible({ timeout: ATTENTE_MS });
				await attendreParcelles(dialogue);
				groupeMontre = true;
			}
		}
		console.log(`RELEVE largeur=${largeur} groupe=${groupeMontre ? 'montre' : 'aucun'}`);
		geste('fermeture');
		await dialogue.getByRole('button', { name: 'Fermer' }).click();
		await expect(dialogue).toBeHidden();
	}

	await toutNote();
	const fichier = join(SORTIE, `requetes-${PASSAGE}.json`);
	writeFileSync(fichier, `${JSON.stringify({ passage: PASSAGE, largeurs: LARGEURS, actions, requetes }, null, 1)}\n`);
	console.log(`RELEVE ${requetes.length} requetes ecrites dans ${fichier}`);
	expect([...new Set(etrangeres)], 'requêtes hors de l’origine locale').toEqual([]);
	expect(requetes.length).toBeGreaterThan(0);
});
