// Leaflet and its style sheet, loaded when the map dialog opens (04.10-06), as a classic script and a
// style sheet of the site origin, not by `import()`: a browser keeps the failure of a dynamic import of
// a URL for the life of the page, so « Recharger la carte » (T-39) would never ask the network again.
// A classic script or link element fetches again each time one is added. Both files are the minified
// ones of the package, emitted by Vite with a hashed name: the CSP (`script-src 'self'`) admits them.
import type * as LeafletEspace from 'leaflet';
import urlScript from 'leaflet/dist/leaflet.js?url';
import urlFeuille from 'leaflet/dist/leaflet.css?url';

export type Leaflet = typeof LeafletEspace;

// A load that succeeded is kept for the life of the page; a failed one is forgotten, so the next call
// adds a new element.
let script: Promise<void> | null = null;
let feuille: Promise<void> | null = null;

function ajouter(element: HTMLScriptElement | HTMLLinkElement): Promise<void> {
	element.dataset.leaflet = '';
	return new Promise((lu, echoue) => {
		element.addEventListener('load', () => lu(), { once: true });
		element.addEventListener(
			'error',
			() => {
				element.remove();
				echoue(new Error('Leaflet non chargé'));
			},
			{ once: true }
		);
		document.head.append(element);
	});
}

function oublierSiEchec(lecture: Promise<void>, oublier: () => void): Promise<void> {
	lecture.catch(oublier);
	return lecture;
}

export async function chargerLeaflet(): Promise<Leaflet> {
	feuille ??= oublierSiEchec(
		ajouter(Object.assign(document.createElement('link'), { rel: 'stylesheet', href: urlFeuille })),
		() => (feuille = null)
	);
	script ??= oublierSiEchec(
		ajouter(Object.assign(document.createElement('script'), { src: urlScript })),
		() => (script = null)
	);
	await Promise.all([feuille, script]);
	const L = (window as unknown as { L?: Leaflet }).L;
	if (!L) throw new Error('Leaflet lu sans window.L');
	return L;
}
