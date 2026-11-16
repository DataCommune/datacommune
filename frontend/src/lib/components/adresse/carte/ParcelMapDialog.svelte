<script lang="ts">
	// Interactive parcel map of the address fiche (04.10 UI contract, §§ 2.2 to 2.5, § 3): a native modal
	// `<dialog>`, full screen under 720 px. Leaflet is loaded when the dialog opens (`chargerLeaflet`): the
	// fiche never loads it before the visitor asks for the map.
	//
	// The ground (Plan IGN) and the parcels come from our own origin: the ground through the relay of our
	// API (`GABARIT_FOND`), the parcels one read per tile (`lireTuileParcelles`); a parcel across two tiles
	// is drawn once. The layers are SVG paths styled by classes and tokens (`.parcel`, `.parcel-address`,
	// `.parcel-selected`, `.parcel-sold-mark`, `.tile-failed`), never by colours handed to Leaflet. Nothing
	// is written on the map: no popup, no tooltip, no price; the lines and the panel under it speak.
	//
	// A tile that was not read is never an empty tile: it is marked, said in the status line and can be
	// asked again (SEM-01). The status line changes at most once a second, so a series of moves does not
	// flood a screen reader.
	//
	// The dialog exists only while open (the fiche mounts it on the click), so it carries no `open` in its
	// markup: a re-render would set it back and close the modal without any `close` event.
	//
	// Leaflet comes from `chargerLeaflet` (a classic script, asked again by T-39 after a failure); this file
	// takes only its types from the package.
	//
	// The list under the panel (04.10-08, `ParcelList`) says the parcels of the view, computed at each end of
	// a move and at each tile read (`parcel-view`); a line chooses its parcel as a touch does, centres the
	// map on it, brings the body back to the panel and gives the focus to its title. The count of the view
	// (T-29, or T-37 when it is empty) is said in the status line after a move, never at the opening.
	//
	// A sale of several parcels shown by the panel (04.10-09, `ongroupe`) is drawn from the geometries the
	// sale served, never from the tiles (a tile in outage leaves no hole in it): dashed and filled, under the
	// address and the selection. The map is framed on it between the zooms 16 and 18; what stays out of the
	// view, and what the service left out of the bounds, is counted at each end of a move (`horsVue`, T-26).
	// The legend says it (T-12) and the list marks its lines (T-31). Nothing is summed here.
	//
	// When the module of Leaflet cannot be loaded (T-38), the list and the panel still work, over the frame
	// of the opening (centred on the address at zoom 18, the size of the map frame): its tiles of parcels are
	// read directly (`tuilesDuCadre`, the same tiles Leaflet would have asked), the parcel of the address is
	// selected, and the list, the panel and their texts take their form « sans carte ». The legend goes, the
	// line of limits stays. T-39 asks the module again; once the map is mounted, the choice is kept.
	//
	// C8: the opening adds one entry of history without changing the URL (shallow routing, `pushState` of
	// SvelteKit): the back button closes the map and the fiche stays, not reloaded; no address of a map is
	// created, so none can be shared nor indexed. A closing from the dialog (« Fermer », Escape, backdrop)
	// consumes that entry, so the next back leaves the fiche as before. The back is heard on `popstate`:
	// the page store does not follow the state of a shallow entry the browser goes back from.
	import { onMount, tick, untrack } from 'svelte';
	import { pushState } from '$app/navigation';
	import type {
		CircleMarker,
		Coords,
		DoneCallback,
		GridLayer,
		GridLayerOptions,
		LatLngBounds,
		LatLngExpression,
		Map as CarteLeaflet,
		Polygon,
		TileEvent
	} from 'leaflet';
	import {
		GABARIT_FOND,
		ZOOM_MAX,
		ZOOM_MIN,
		ZOOM_OUVERTURE,
		lireContexte,
		lireTuileParcelles,
		type ContexteCarte,
		type GroupeVendu,
		type ParcelleCarte
	} from '$lib/api/carte-parcelles';
	import IconFermer from '$lib/components/ds/icons/IconFermer.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { MESSAGE_PANNE_COMPARABLES, PHRASE_VENTE_ISOLEE } from '$lib/data/adresse-ventes';
	import { LIBELLES_ETATS, phraseEtat } from '$lib/data/etats-libelles';
	import { messageLimite } from '$lib/data/panne';
	import {
		GABARITS,
		LIBELLE_DETAIL_LIMITES,
		annonceSelection,
		compteParcelles,
		compteSansVentes,
		descriptionCarte,
		legendeVentes,
		ligneLimites,
		nomCarte,
		voletCarte
	} from '$lib/data/parcel-map';
	import {
		bornesDuCadre,
		horsVueDuGroupe,
		parcellesDeLaVue,
		tuilesDuCadre,
		ventesComptees,
		type BornesVue
	} from '$lib/utils/parcel-view';
	import { developperSigles } from '$lib/utils/sigles';
	import { chargerLeaflet, type Leaflet } from './chargeur-carte';
	import ParcelList from './ParcelList.svelte';
	import ParcelPanel from './ParcelPanel.svelte';

	interface Props {
		banId: string;
		/** Label of the address of the fiche: the accessible name of the map (T-03). */
		adresse: string;
		onclose: () => void;
	}

	let { banId, adresse, onclose }: Props = $props();

	const titreId = $props.id();
	const descriptionId = `${titreId}-description`;
	let dialog = $state<HTMLDialogElement>();
	let titreElement = $state<HTMLHeadingElement>();
	let conteneur = $state<HTMLDivElement>();
	let corps = $state<HTMLDivElement>();
	let blocPanneau = $state<HTMLDivElement>();
	/** Keeps the body on the panel chosen from the list while its sales are read. */
	let suiviPanneau: ResizeObserver | null = null;
	let volet = $state<ReturnType<typeof SourcePanel>>();
	// What had the focus when opening: the closing gives it back there.
	let focusAvant: Element | null = null;
	const abandon = new AbortController();
	/** The entry of history added at the opening is still the current one (C8). */
	let entreeHistorique = false;

	// Leaflet objects and served objects are never proxied: they are compared by identity.
	let carte = $state.raw<CarteLeaflet | null>(null);
	let contexte = $state.raw<ContexteCarte | null>(null);
	let selection = $state.raw<ParcelleCarte | null>(null);
	/** The parcels drawn whose outline meets the view, in no order (the list orders them). */
	let parcellesVue = $state.raw<ParcelleCarte[]>([]);
	/** The group of the sale the panel shows; null: none. One at a time. */
	let groupe = $state.raw<GroupeVendu | null>(null);
	/** Parcels of the shown group out of the view, counted by the map (T-26); null: no group counted. */
	let horsVue = $state<number | null>(null);
	const groupeMontre = $derived((groupe?.parcelles?.length ?? 0) > 0);
	const idsGroupe = $derived(groupe?.parcelles?.map((p) => p.id_parcelle) ?? []);
	/** Bounds of the frame of the opening, read without the map; null until the module of Leaflet failed. */
	let cadreOuverture = $state.raw<BornesVue | null>(null);
	/** Parcels of the frame of the opening read without the map, by tile key `z/x/y`. */
	const lusSansCarte = new Map<string, ParcelleCarte[]>();

	/**
	 * The map as a whole: still loading, mounted, refused by a rate limit, the context in outage, or the
	 * module of Leaflet that could not be loaded (T-38, relaunched by T-39).
	 */
	let phase = $state<'chargement' | 'prete' | 'limite' | 'panne' | 'module'>('chargement');
	/** Served delay of the last rate limit; undefined when none is in force. */
	let limite = $state<number | null | undefined>(undefined);
	let tuilesEnCours = $state(false);
	// Registers of the tiles in outage, by key `z/x/y`: ground, parcels, and parcels read whose sales were not.
	let fondsEnEchec = $state.raw<ReadonlySet<string>>(new Set());
	let parcellesEnEchec = $state.raw<ReadonlySet<string>>(new Set());
	let ventesEnPanne = $state.raw<ReadonlySet<string>>(new Set());
	let annonce = $state('');
	/** The panel waits for the opening selection: no « choose a parcel » while the parcels are read. */
	let panneauPret = $state(false);
	let zoom = $state(ZOOM_OUVERTURE);
	/** Zoom asked by the buttons and not reached yet: Leaflet drops a zoom asked during its animation. */
	let zoomVise: number | null = null;
	/** The point of the address left the view: the return button shows (T-09). */
	let adresseHorsVue = $state(false);
	/** The visitor moved the map: from then on, the status line says the count of the view. */
	let vueDeplacee = $state(false);
	/** Commands bound to the mounted map; null until it exists. */
	let commandes = $state.raw<{
		revenir: () => void;
		rechargerFond: () => void;
		rechargerParcelles: () => void;
		choisirDansLaListe: (p: ParcelleCarte) => void;
		montrerGroupe: () => void;
	} | null>(null);

	const PANNE = { kind: 'absence', etat: 'temporary_error', motif: '', effectif: null } as const;
	// The drawing of the point of the address on the picture of the fiche (`.carte-point`), as is.
	const POINT =
		'<svg viewBox="0 0 48 48" aria-hidden="true" focusable="false"><circle class="halo" cx="24" cy="24" r="22" /><circle class="halo" cx="24" cy="24" r="14" /><circle class="coeur" cx="24" cy="24" r="6" /></svg>';
	/** Diameter of a sold mark: 10 px, 6 px at the widest zoom where the parcels are the smallest. */
	const rayonMarque = (z: number) => (z <= ZOOM_MIN ? 3 : 5);

	const avecAjout = (registre: ReadonlySet<string>, cle: string) => (registre.has(cle) ? registre : new Set([...registre, cle]));
	const sansCle = (registre: ReadonlySet<string>, cle: string) => {
		if (!registre.has(cle)) return registre;
		const reste = new Set(registre);
		reste.delete(cle);
		return reste;
	};

	/**
	 * The count of the view (T-29), or T-37 when no parcel is in it; nothing when the cadastre is not
	 * covered here or when a parcel of the view has its sales not counted (no text says that count).
	 */
	const compteVue = $derived.by(() => {
		if (!contexte || contexte.parcelles.etat === 'out_of_coverage') return '';
		if (parcellesVue.length === 0) return GABARITS['T-37'];
		const vendues = ventesComptees(parcellesVue);
		// S5 (2026-11-15): sales not counted, the parcels are still said, never a smaller count of sales.
		return vendues === null
			? compteSansVentes(parcellesVue.length)
			: compteParcelles(parcellesVue.length, vendues, contexte.ventes.periode ?? LIBELLES_ETATS.not_reported);
	});

	/**
	 * What the status line should say now: a limit, then the outages, then the reading, then the last
	 * choice, then, once the visitor moved the map, the count of the view. A tile not read is said before
	 * any count: an empty view is never said over a part that was not read.
	 */
	const ligneVoulue = $derived.by(() => {
		if (limite !== undefined) return messageLimite(limite);
		const pannes: string[] = [];
		if (parcellesEnEchec.size > 0) pannes.push(GABARITS['T-34']);
		if (ventesEnPanne.size > 0) pannes.push(MESSAGE_PANNE_COMPARABLES);
		if (fondsEnEchec.size > 0) pannes.push(GABARITS['T-33']);
		if (pannes.length > 0) return pannes.join(' ');
		if (tuilesEnCours) return GABARITS['T-05'];
		return annonce || (vueDeplacee ? compteVue : '');
	});

	// The line said, at most once a second: a change inside the second waits for its end, and only the
	// last wanted text of that second is said.
	let ligneEtat = $state('');
	let dernierChangement = -Infinity;
	let attenteLigne: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const voulue = ligneVoulue;
		clearTimeout(attenteLigne);
		if (voulue === untrack(() => ligneEtat)) return;
		const dire = () => {
			ligneEtat = voulue;
			dernierChangement = performance.now();
		};
		const reste = dernierChangement + 1000 - performance.now();
		if (reste <= 0) dire();
		else attenteLigne = setTimeout(dire, reste);
	});

	const idAdresse = $derived(contexte?.parcelle_adresse.etat === 'value' ? contexte.parcelle_adresse.id_parcelle : null);
	/** The parcel of the address is not one: the served reason, said under the legend. */
	const motifParcelle = $derived.by(() => {
		const pa = contexte?.parcelle_adresse;
		if (!pa || pa.etat === 'value' || !pa.motif) return null;
		return phraseEtat(pa.etat, pa.motif, pa.nombre_parcelles);
	});
	const ventesHorsCouverture = $derived(contexte?.ventes.etat === 'out_of_coverage');
	/** The map is not there: the list and the panel stand for it, over the frame of the opening. */
	const sansCarte = $derived(phase !== 'prete' && cadreOuverture !== null);
	const entreesVolet = $derived.by(() => {
		if (!contexte) return [];
		const entrees = voletCarte({ periode: contexte.ventes.periode, millesime: contexte.cadastre_millesime }).map((entree) =>
			entree.terme === 'Prix' ? { ...entree, valeur: `${entree.valeur} ${PHRASE_VENTE_ISOLEE}` } : entree
		);
		// Acronyms spelled out at their first occurrence, in the reading order of the panel.
		const valeurs = developperSigles(entrees.map((entree) => entree.valeur));
		// « Fond » keeps its link to the source page (R1, 2026-11-15).
		return entrees.map((entree, i) => ({ ...entree, valeur: valeurs[i] }));
	});
	const titreVolet = LIBELLE_DETAIL_LIMITES.charAt(0).toUpperCase() + LIBELLE_DETAIL_LIMITES.slice(1);

	onMount(() => {
		focusAvant = document.activeElement;
		if (dialog) {
			if (typeof dialog.showModal === 'function') dialog.showModal();
			else dialog.setAttribute('open', '');
		}
		titreElement?.focus();
		ajouterEntreeHistorique();
		window.addEventListener('popstate', surRetour);
		void charger();
		return () => {
			window.removeEventListener('popstate', surRetour);
			abandon.abort();
			clearTimeout(attenteLigne);
			suiviPanneau?.disconnect();
			carte?.remove();
		};
	});

	/** C8: one entry of history, same URL; the page state says it belongs to the map. */
	function ajouterEntreeHistorique() {
		try {
			pushState('', { carteParcelles: true });
			entreeHistorique = true;
		} catch {
			// No router (a component test): no entry, and the dialog closes as before C8.
		}
	}

	/** The browser went back from the entry of the map: the map closes, the fiche stays. */
	function surRetour() {
		if (!entreeHistorique) return;
		entreeHistorique = false;
		fermer();
	}

	async function charger() {
		const issue = await lireContexte(banId, { signal: abandon.signal });
		if (abandon.signal.aborted) return;
		if (issue.kind === 'limite') {
			limite = issue.retryAfter;
			phase = 'limite';
			return;
		}
		if (issue.kind === 'panne') {
			phase = 'panne';
			return;
		}
		contexte = issue.donnees;
		await chargerModule(issue.donnees);
	}

	/** Leaflet and its style sheet, asked now; a failure is said in the frame and relaunched by T-39. */
	async function chargerModule(ctx: ContexteCarte) {
		phase = 'chargement';
		let leaflet: Leaflet;
		try {
			leaflet = await chargerLeaflet();
		} catch {
			if (abandon.signal.aborted) return;
			phase = 'module';
			void lireLeCadre(ctx);
			return;
		}
		if (abandon.signal.aborted || !conteneur) return;
		monter(leaflet, ctx, conteneur);
	}

	/**
	 * Without the map: the tiles of parcels of the frame of the opening, read once. The size is that of the
	 * frame of the map, which stays in the dialog when Leaflet is missing.
	 */
	async function lireLeCadre(ctx: ContexteCarte) {
		if (!conteneur || cadreOuverture) return;
		const taille = { largeur: conteneur.clientWidth, hauteur: conteneur.clientHeight };
		cadreOuverture = bornesDuCadre(ctx.centre, ZOOM_OUVERTURE, taille);
		await lireTuilesDuCadre(tuilesDuCadre(ctx.centre, ZOOM_OUVERTURE, taille));
	}

	async function lireTuilesDuCadre(cles: string[]) {
		tuilesEnCours = true;
		await Promise.all(cles.map(lireTuileDuCadre));
		if (abandon.signal.aborted || phase === 'prete') return;
		tuilesEnCours = false;
		// Every tile of the frame answered: without a parcel of the address to select, the panel says so.
		panneauPret = true;
	}

	/** One tile of the frame; as on the map, a tile not read is marked, said, and can be asked again (T-41). */
	async function lireTuileDuCadre(cle: string) {
		const [z, x, y] = cle.split('/').map(Number);
		const issue = await lireTuileParcelles(z, x, y, { signal: abandon.signal });
		// The map was mounted meanwhile (T-39): its own tiles speak now.
		if (abandon.signal.aborted || phase === 'prete') return;
		if (issue.kind === 'ok') {
			limite = undefined;
			parcellesEnEchec = sansCle(parcellesEnEchec, cle);
			ventesEnPanne = issue.donnees.ventes.etat === 'temporary_error' ? avecAjout(ventesEnPanne, cle) : sansCle(ventesEnPanne, cle);
			lusSansCarte.set(cle, issue.donnees.collection.features);
			verserSansCarte();
			return;
		}
		if (issue.kind === 'limite') limite = issue.retryAfter;
		else parcellesEnEchec = avecAjout(parcellesEnEchec, cle);
	}

	/** The parcels of the frame, a parcel across two tiles once; the parcel of the address selected (C3). */
	function verserSansCarte() {
		if (!cadreOuverture) return;
		const uniques = new Map<string, ParcelleCarte>();
		for (const parcelles of lusSansCarte.values()) for (const p of parcelles) if (!uniques.has(p.id)) uniques.set(p.id, p);
		parcellesVue = parcellesDeLaVue(uniques.values(), cadreOuverture);
		const adresse = idAdresse ? uniques.get(idAdresse) : undefined;
		// Unless the visitor chose first.
		if (adresse && !selection) {
			selection = adresse;
			panneauPret = true;
		}
	}

	/** T-41: only the tiles in outage are asked again, on the map or in the frame of the opening. */
	function rechargerParcelles() {
		if (commandes) return commandes.rechargerParcelles();
		void lireTuilesDuCadre([...new Set([...parcellesEnEchec, ...ventesEnPanne])]);
	}

	/** One step per click, however fast the clicks: the buttons follow the zoom asked, not the one drawn. */
	function zoomer(pas: 1 | -1) {
		if (!carte) return;
		const cible = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, (zoomVise ?? carte.getZoom()) + pas));
		zoomVise = cible;
		zoom = cible;
		carte.setZoom(cible);
	}

	function monter(L: Leaflet, ctx: ContexteCarte, element: HTMLElement) {
		// The registers of a frame read without the map give way to those of the map.
		parcellesEnEchec = new Set();
		ventesEnPanne = new Set();
		tuilesEnCours = false;
		lusSansCarte.clear();
		const reduit = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
		const centre: LatLngExpression = [ctx.centre.lat, ctx.centre.lon];
		const c = L.map(element, {
			center: centre,
			zoom: ZOOM_OUVERTURE,
			minZoom: ZOOM_MIN,
			maxZoom: ZOOM_MAX,
			zoomSnap: 1,
			zoomDelta: 1,
			maxBounds: L.latLngBounds([ctx.emprise.sud, ctx.emprise.ouest], [ctx.emprise.nord, ctx.emprise.est]),
			maxBoundsViscosity: 1,
			zoomControl: false,
			attributionControl: false,
			dragging: true,
			touchZoom: true,
			doubleClickZoom: true,
			scrollWheelZoom: true,
			keyboard: true,
			boxZoom: false,
			bounceAtZoomLimits: false,
			zoomAnimation: !reduit,
			fadeAnimation: !reduit,
			markerZoomAnimation: !reduit,
			inertia: !reduit,
			preferCanvas: false
		});

		// Stacking, bottom to top: tiles (failed ones included), parcels, group sold together, sold marks,
		// outlines of the address and of the selection, point of the address. The drawing is for the eye
		// only: what it shows is said by the lines and the panel under it.
		for (const [nom, rang] of [['parcelles', '410'], ['groupe', '412'], ['pastilles', '415'], ['surcouche', '420'], ['adresse', '440']] as const) {
			const pane = c.createPane(nom);
			pane.style.zIndex = rang;
			pane.setAttribute('aria-hidden', 'true');
		}
		c.getPane('tilePane')?.setAttribute('aria-hidden', 'true');
		const rendu = L.svg({ pane: 'parcelles' });
		const renduGroupe = L.svg({ pane: 'groupe' });
		const renduPastilles = L.svg({ pane: 'pastilles' });
		const renduSurcouche = L.svg({ pane: 'surcouche' });
		const surcouche = L.layerGroup().addTo(c);
		const coucheGroupe = L.layerGroup().addTo(c);
		const cleDe = (coords: Coords) => `${coords.z}/${coords.x}/${coords.y}`;

		// Ground: same origin, no Retina doubling and one ring of kept tiles (traffic of the relay). A tile
		// in outage keeps its box, marked; Leaflet already gives each tile an empty `alt`.
		const fond = L.tileLayer(GABARIT_FOND, {
			minZoom: ZOOM_MIN,
			maxZoom: ZOOM_MAX,
			detectRetina: false,
			keepBuffer: 1,
			className: 'carte-fond'
		});
		fond.on('tileerror', (evenement: TileEvent) => {
			evenement.tile.classList.add('tile-failed');
			fondsEnEchec = avecAjout(fondsEnEchec, cleDe(evenement.coords));
		});
		fond.on('tileload', (evenement: TileEvent) => {
			evenement.tile.classList.remove('tile-failed');
			fondsEnEchec = sansCle(fondsEnEchec, cleDe(evenement.coords));
		});
		fond.on('tileunload', (evenement: TileEvent) => (fondsEnEchec = sansCle(fondsEnEchec, cleDe(evenement.coords))));
		fond.addTo(c);

		const sommets = (geometrie: ParcelleCarte['geometry']) =>
			L.GeoJSON.coordsToLatLngs(geometrie.coordinates, geometrie.type === 'Polygon' ? 1 : 2) as LatLngExpression[][];
		const bornesDeLaVue = (): BornesVue => {
			const b = c.getBounds();
			return { ouest: b.getWest(), sud: b.getSouth(), est: b.getEast(), nord: b.getNorth() };
		};

		/** The parcel of the address once a tile served it: its outline stays when its tile leaves. */
		let parcelleAdresse: ParcelleCarte | null = null;
		// A parcel chosen without the map (before T-39) is kept: the opening selection is already made.
		let ouvertureResolue = selection !== null;

		function tracerSurcouche() {
			surcouche.clearLayers();
			const tracer = (p: ParcelleCarte, classes: string, lisere: string) => {
				// A paper edging under the stroke keeps it readable on any ground.
				for (const className of [`parcel-casing ${lisere}`, classes]) {
					L.polygon(sommets(p.geometry), { renderer: renduSurcouche, pane: 'surcouche', interactive: false, className }).addTo(surcouche);
				}
			};
			if (parcelleAdresse) tracer(parcelleAdresse, 'parcel-address', 'parcel-casing-address');
			if (selection) {
				const estAdresse = selection.id === parcelleAdresse?.id;
				tracer(selection, estAdresse ? 'parcel-selected parcel-address' : 'parcel-selected', estAdresse ? 'parcel-casing-address' : 'parcel-casing-selected');
			}
		}

		function choisir(p: ParcelleCarte, parGeste: boolean) {
			selection = p;
			ouvertureResolue = true;
			panneauPret = true;
			if (parGeste) annonce = annonceSelection(p.id);
			tracerSurcouche();
		}

		// A parcel across several tiles is drawn once and leaves with the last tile that carries it.
		const dessinees = new Map<string, { parcelle: ParcelleCarte; trace: Polygon; tuiles: Set<string>; marque: CircleMarker | null }>();

		/** The parcels drawn that meet the view now: what the list shows. */
		function suivreLaVue() {
			parcellesVue = parcellesDeLaVue(
				[...dessinees.values()].map((entree) => entree.parcelle),
				bornesDeLaVue()
			);
		}

		/** What of the shown group the view leaves out, counted again at each end of a move. */
		const compterHorsVue = () => (horsVue = groupe ? horsVueDuGroupe(groupe, bornesDeLaVue()) : null);

		/**
		 * Draws the shown group from the geometries its sale served (a paper edging under each dashed
		 * outline), then frames the map on it: zoom 18 at most, so that a small group is never enlarged past
		 * the opening; 16 at least, the floor of the map. Without a group, the drawing goes.
		 */
		function tracerGroupe() {
			coucheGroupe.clearLayers();
			let cadre: LatLngBounds | null = null;
			for (const p of groupe?.parcelles ?? []) {
				for (const className of ['parcel-casing parcel-casing-group', 'parcel-group']) {
					const trace = L.polygon(sommets(p.geometry), { renderer: renduGroupe, pane: 'groupe', interactive: false, className });
					trace.addTo(coucheGroupe);
					const bornes = trace.getBounds();
					cadre = cadre ? cadre.extend(bornes) : L.latLngBounds(bornes.getSouthWest(), bornes.getNorthEast());
				}
			}
			if (cadre) c.fitBounds(cadre, { maxZoom: ZOOM_OUVERTURE, animate: !reduit });
			compterHorsVue();
		}
		const parTuile = new Map<string, string[]>();
		/** Reads a tile again (T-41), without the done callback of Leaflet, already called. */
		const relire = new Map<string, () => void>();

		/** The sold mark of a parcel, at its served label point; only from a tile whose sales were read. */
		function marquer(p: ParcelleCarte, entree: { marque: CircleMarker | null }) {
			if (entree.marque || (p.properties.ventes ?? 0) < 1) return;
			const [lon, lat] = p.properties.etiquette;
			entree.marque = L.circleMarker([lat, lon], {
				renderer: renduPastilles,
				pane: 'pastilles',
				interactive: false,
				radius: rayonMarque(c.getZoom()),
				className: 'parcel-sold-mark'
			}).addTo(c);
		}

		function verser(cle: string, parcelles: ParcelleCarte[], ventesLues: boolean) {
			parTuile.set(cle, parcelles.map((p) => p.id));
			for (const p of parcelles) {
				const connue = dessinees.get(p.id);
				if (connue) {
					connue.tuiles.add(cle);
					if (ventesLues) marquer(p, connue);
					continue;
				}
				const trace = L.polygon(sommets(p.geometry), { renderer: rendu, pane: 'parcelles', className: 'parcel' });
				trace.on('click', (evenement) => {
					L.DomEvent.stopPropagation(evenement);
					choisir(p, true);
				});
				trace.addTo(c);
				const entree = { parcelle: p, trace, tuiles: new Set([cle]), marque: null };
				dessinees.set(p.id, entree);
				if (ventesLues) marquer(p, entree);
				if (p.id !== idAdresse) continue;
				parcelleAdresse = p;
				// C3: the parcel of the address is selected at the opening, unless the visitor chose first.
				if (ouvertureResolue) tracerSurcouche();
				else choisir(p, false);
			}
			suivreLaVue();
		}

		function retirer(cle: string) {
			parcellesEnEchec = sansCle(parcellesEnEchec, cle);
			ventesEnPanne = sansCle(ventesEnPanne, cle);
			relire.delete(cle);
			for (const id of parTuile.get(cle) ?? []) {
				const connue = dessinees.get(id);
				if (!connue) continue;
				connue.tuiles.delete(cle);
				if (connue.tuiles.size > 0) continue;
				connue.trace.remove();
				connue.marque?.remove();
				dessinees.delete(id);
			}
			parTuile.delete(cle);
		}

		const Grille: new (options: GridLayerOptions) => GridLayer = L.GridLayer.extend({
			createTile(coords: Coords, fini: DoneCallback) {
				const tuile = document.createElement('div');
				const cle = cleDe(coords);
				parTuile.set(cle, []);
				const lire = (premiere: boolean) =>
					void lireTuileParcelles(coords.z, coords.x, coords.y, { signal: abandon.signal }).then((issue) => {
						// The tile left the view (or the dialog closed) before its answer: nothing to draw.
						if (abandon.signal.aborted || !parTuile.has(cle)) return;
						if (issue.kind === 'ok') {
							limite = undefined;
							tuile.classList.remove('tile-failed');
							parcellesEnEchec = sansCle(parcellesEnEchec, cle);
							// Parcels read, sales not: the parcels are drawn without a mark, and the outage is said.
							const ventesEnErreur = issue.donnees.ventes.etat === 'temporary_error';
							ventesEnPanne = ventesEnErreur ? avecAjout(ventesEnPanne, cle) : sansCle(ventesEnPanne, cle);
							verser(cle, issue.donnees.collection.features, issue.donnees.ventes.etat === 'value');
							if (premiere) fini(undefined, tuile);
							return;
						}
						// A tile that was not read is never an empty tile: it is marked, counted and said.
						if (issue.kind === 'limite') limite = issue.retryAfter;
						else {
							tuile.classList.add('tile-failed');
							parcellesEnEchec = avecAjout(parcellesEnEchec, cle);
						}
						if (premiere) fini(new Error(issue.kind), tuile);
					});
				relire.set(cle, () => lire(false));
				lire(true);
				return tuile;
			}
		});
		const grille = new Grille({ minZoom: ZOOM_MIN, maxZoom: ZOOM_MAX, updateWhenIdle: true, className: 'couche-parcelles' });
		grille.on('loading', () => (tuilesEnCours = true));
		grille.on('load', () => {
			tuilesEnCours = false;
			// Every tile of the view answered: without a parcel of the address to select, the panel says so.
			panneauPret = true;
		});
		grille.on('tileunload', (evenement: TileEvent) => retirer(cleDe(evenement.coords)));
		grille.addTo(c);

		L.marker(centre, {
			pane: 'adresse',
			interactive: false,
			keyboard: false,
			icon: L.divIcon({ className: 'point-adresse', iconSize: [48, 48], iconAnchor: [24, 24], html: POINT })
		}).addTo(c);

		// A move drops the announcement of the last choice: the line then speaks of what is being read, then
		// of the parcels of the new view.
		c.on('movestart', () => {
			annonce = '';
			vueDeplacee = true;
		});
		// A gesture on the map itself (wheel, double click, pinch, keys) supersedes the zoom the buttons
		// queued: without this, the end of the queue took the map back and undid a wheel that came between
		// two steps (seen in Firefox and WebKit, matrix of 04.10-10).
		for (const geste of ['wheel', 'dblclick', 'pointerdown', 'keydown']) {
			element.addEventListener(geste, () => (zoomVise = null), { capture: true, passive: true });
		}
		c.on('zoomend', () => {
			// A click during the animation was dropped by Leaflet: the zoom asked last is reached now.
			if (zoomVise !== null && c.getZoom() !== zoomVise) {
				c.setZoom(zoomVise);
				return;
			}
			zoomVise = null;
			zoom = c.getZoom();
			for (const { marque } of dessinees.values()) marque?.setRadius(rayonMarque(zoom));
		});
		c.on('moveend', () => {
			adresseHorsVue = !c.getBounds().contains(centre);
			suivreLaVue();
			compterHorsVue();
		});

		commandes = {
			revenir: () => c.setView(centre, c.getZoom(), { animate: !reduit }),
			rechargerFond: () => {
				fondsEnEchec = new Set();
				fond.redraw();
			},
			// Only the tiles in outage are asked again; their answer was not kept (`no-store`, 04.10-02).
			rechargerParcelles: () => {
				for (const cle of new Set([...parcellesEnEchec, ...ventesEnPanne])) relire.get(cle)?.();
			},
			// The map moves first: its move drops the former announcement, the choice is then announced.
			choisirDansLaListe: (p) => {
				const [lon, lat] = p.properties.etiquette;
				c.panTo([lat, lon], { animate: !reduit });
				choisir(p, true);
			},
			montrerGroupe: tracerGroupe
		};
		carte = c;
		phase = 'prete';
		// Mounted after T-39: the choice and the group shown without the map are drawn now.
		if (selection) tracerSurcouche();
		if (groupe) tracerGroupe();
	}

	/** The panel shows the group of a sale, or none any more (turned off, another parcel or page). */
	function surGroupe(g: GroupeVendu | null) {
		groupe = g;
		if (commandes) commandes.montrerGroupe();
		// Without the map, what the frame of the opening leaves out is counted.
		else horsVue = g && cadreOuverture ? horsVueDuGroupe(g, cadreOuverture) : null;
	}

	/**
	 * A line of the list: the parcel is chosen and the map centred on it, then the body comes back up to
	 * the panel and its title takes the focus. The scroll is set at once, never animated by script.
	 */
	async function choisirDansLaListe(p: ParcelleCarte) {
		if (commandes) commandes.choisirDansLaListe(p);
		else {
			selection = p;
			panneauPret = true;
			annonce = annonceSelection(p.id);
		}
		await tick();
		const [zone, bloc] = [corps, blocPanneau];
		if (!zone || !bloc) return;
		const aligner = () => (zone.scrollTop += bloc.getBoundingClientRect().top - zone.getBoundingClientRect().top);
		aligner();
		const titre = bloc.querySelector<HTMLElement>('section.parcelle h3');
		titre?.focus({ preventScroll: true });
		// While its sales are read the panel is short, and the body cannot scroll as far as its top: it is
		// aligned again as it grows, until its sales are there or the focus left its title.
		suiviPanneau?.disconnect();
		if (!titre || typeof ResizeObserver !== 'function') return;
		let lecture = false;
		const suivi = new ResizeObserver(() => {
			if (document.activeElement !== titre) return suivi.disconnect();
			aligner();
			const occupee = bloc.querySelector('.parcelle-ventes')?.getAttribute('aria-busy') === 'true';
			if (lecture && !occupee) suivi.disconnect();
			lecture ||= occupee;
		});
		suiviPanneau = suivi;
		suivi.observe(bloc);
	}

	/** Every way out (« Fermer », Escape, the backdrop) goes through the native close. */
	function fermer() {
		if (dialog?.open && typeof dialog.close === 'function') dialog.close();
		else surFermeture();
	}

	function surFermeture() {
		// A native modal has already given the focus back; only a focus still left in the dialog or
		// dropped to the body is brought back (a click does not always focus its button: Safari).
		const actif = document.activeElement;
		const perdu = !actif || actif === document.body || dialog?.contains(actif);
		if (perdu && focusAvant instanceof HTMLElement && focusAvant !== document.body) focusAvant.focus();
		focusAvant = null;
		// Closed from the dialog: its entry of history is consumed, so the next back leaves the fiche.
		if (entreeHistorique) {
			entreeHistorique = false;
			history.back();
		}
		onclose();
	}

	function surClic(event: MouseEvent) {
		// The content fills the box of the dialog: only the backdrop targets the dialog itself.
		if (event.target === dialog) fermer();
	}

	function surTouche(event: KeyboardEvent) {
		// A native modal dialog closes itself on Escape; this covers the environments without one.
		if (event.key === 'Escape' && typeof dialog?.showModal !== 'function') {
			event.preventDefault();
			fermer();
			return;
		}
		// The focus stays in the dialog: Tab from its last stop comes back to the first one (a modal
		// dialog makes the page inert, but lets the focus leave for the browser chrome). The map is one
		// stop (`tabindex="0"`, set by Leaflet); the box of the list is one too.
		if (event.key !== 'Tab' || !dialog) return;
		const arrets = [...dialog.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex="0"]')];
		if (arrets.length === 0) return;
		const premier = arrets[0];
		const dernier = arrets[arrets.length - 1];
		const actif = document.activeElement;
		if (event.shiftKey && (actif === premier || actif === titreElement)) {
			event.preventDefault();
			dernier.focus();
		} else if (!event.shiftKey && actif === dernier) {
			event.preventDefault();
			premier.focus();
		}
	}
</script>

<!-- Keyboard closing: Escape (native modal dialog, or surTouche) and « Fermer ». -->
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
<dialog
	bind:this={dialog}
	class="carte-dialogue"
	aria-labelledby={titreId}
	onclose={surFermeture}
	onkeydown={surTouche}
	onclick={surClic}
>
	<div class="carte-boite">
		<div class="carte-tete">
			<h2 id={titreId} class="carte-titre" tabindex="-1" bind:this={titreElement}>{GABARITS['T-02']}</h2>
			<button type="button" class="bouton-secondaire carte-fermer" onclick={fermer}>
				<IconFermer class="carte-fermer-icone" />
				Fermer
			</button>
		</div>
		<div class="carte-corps" bind:this={corps}>
			<div class="carte-cadre">
				<!-- One tab stop, a named group; no text lives in the container of Leaflet. -->
				<div class="carte-vue" role="group" aria-label={nomCarte(adresse)} aria-describedby={descriptionId} bind:this={conteneur}></div>
				<p id={descriptionId} class="sr-only">{descriptionCarte()}</p>
				{#if phase === 'prete' && commandes}
					<div class="carte-zoom">
						<button type="button" class="carte-commande" aria-label={GABARITS['T-07']} disabled={zoom >= ZOOM_MAX} onclick={() => zoomer(1)}>
							<span aria-hidden="true">+</span>
						</button>
						<button type="button" class="carte-commande" aria-label={GABARITS['T-08']} disabled={zoom <= ZOOM_MIN} onclick={() => zoomer(-1)}>
							<span aria-hidden="true">−</span>
						</button>
					</div>
					{#if adresseHorsVue}
						<button type="button" class="carte-commande carte-retour" onclick={commandes.revenir}>{GABARITS['T-09']}</button>
					{/if}
				{/if}
				<!-- The frame speaks only while the map does not exist: it leaves once the map is mounted. -->
				{#if phase === 'chargement'}
					<p class="carte-avis" role="status">{GABARITS['T-04']}</p>
				{:else if phase === 'panne'}
					<div class="carte-avis" role="status"><StateValue etat={PANNE} /></div>
				{:else if phase === 'module'}
					<div class="carte-avis">
						<p class="carte-avis-texte" role="status">{GABARITS['T-38']}</p>
						<button type="button" class="bouton-secondaire" onclick={() => contexte && chargerModule(contexte)}>{GABARITS['T-39']}</button>
					</div>
				{/if}
			</div>
			<p class="carte-etat" aria-live="polite">{ligneEtat}</p>
			{#if ((phase === 'prete' && commandes) || sansCarte) && (fondsEnEchec.size > 0 || parcellesEnEchec.size > 0 || ventesEnPanne.size > 0)}
				<div class="carte-bloc carte-relances">
					{#if fondsEnEchec.size > 0 && commandes}
						<button type="button" class="bouton-secondaire" onclick={commandes.rechargerFond}>{GABARITS['T-39']}</button>
					{/if}
					{#if parcellesEnEchec.size > 0 || ventesEnPanne.size > 0}
						<button type="button" class="bouton-secondaire" onclick={rechargerParcelles}>{GABARITS['T-41']}</button>
					{/if}
				</div>
			{/if}
			{#if contexte}
				<div class="carte-bloc carte-legende-bloc">
					{#if !sansCarte}
						<ul class="carte-legende">
							<li>
								<svg class="echantillon" viewBox="0 0 24 16" aria-hidden="true" focusable="false"><rect class="echantillon-adresse" x="3" y="3" width="18" height="10" /></svg>
								{GABARITS['T-10']}
							</li>
							{#if !ventesHorsCouverture}
								<li>
									<svg class="echantillon" viewBox="0 0 24 16" aria-hidden="true" focusable="false"><circle class="echantillon-vendue" cx="12" cy="8" r="5" /></svg>
									{legendeVentes(contexte.ventes.periode ?? LIBELLES_ETATS.not_reported)}
								</li>
							{/if}
							{#if groupeMontre}
								<li>
									<svg class="echantillon" viewBox="0 0 24 16" aria-hidden="true" focusable="false"><rect class="echantillon-groupe" x="3" y="3" width="18" height="10" /></svg>
									{GABARITS['T-12']}
								</li>
							{/if}
						</ul>
					{/if}
					{#if motifParcelle}
						<p class="carte-motif-parcelle">{motifParcelle}</p>
					{/if}
					{#if ventesHorsCouverture}
						<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: contexte.ventes.motif ?? '', effectif: null }} />
					{/if}
					<p class="carte-limites">
						{ligneLimites({
							periode: contexte.ventes.periode,
							millesime: contexte.cadastre_millesime,
							attribution: contexte.fond.attribution,
							licence: contexte.fond.licence
						})}
						<SourceButton forme="detail" libelle={LIBELLE_DETAIL_LIMITES} onouvrir={(declencheur) => volet?.ouvrir(declencheur)} />
					</p>
				</div>
				<SourcePanel bind:this={volet} titre={titreVolet} entrees={entreesVolet} brut={[]} />
			{/if}
			{#if panneauPret}
				<div class="carte-bloc" bind:this={blocPanneau}>
					<ParcelPanel {banId} parcelle={selection} estAdresse={selection !== null && selection.id === idAdresse} {contexte} ongroupe={surGroupe} {horsVue} {sansCarte} />
				</div>
			{/if}
			{#if contexte && (phase === 'prete' || sansCarte)}
				<div class="carte-bloc">
					<ParcelList
						parcelles={parcellesVue}
						{idAdresse}
						selection={selection?.id ?? null}
						groupe={idsGroupe}
						{contexte}
						occupe={tuilesEnCours}
						partielle={parcellesEnEchec.size > 0 || limite !== undefined}
						{sansCarte}
						onchoisir={choisirDansLaListe}
					/>
				</div>
			{/if}
		</div>
	</div>
</dialog>

<style>
	/* Full screen up to 720 px; above, a box centred by the browser (a modal dialog: `inset: 0` and
	   automatic margins). The box inside takes the whole dialog, so a click on the dialog itself is a
	   click on the backdrop. */
	.carte-dialogue {
		box-sizing: border-box;
		inset: 0;
		width: 100%;
		max-width: none;
		height: 100dvh;
		max-height: none;
		margin: 0;
		padding: 0;
		overflow: hidden;
		border: 0;
		background: var(--color-panneau);
		color: var(--color-ink);
	}

	.carte-dialogue::backdrop {
		background: color-mix(in srgb, var(--color-ink) 40%, transparent);
	}

	/* The page behind is inert and does not scroll: a modal dialog does not stop a wheel over its head,
	   or over the backdrop, from scrolling the document. No gutter is kept: it would narrow the dialog. */
	:global(html:has(dialog.carte-dialogue[open])) {
		overflow: hidden;
	}

	.carte-boite {
		display: flex;
		flex-direction: column;
		height: 100%;
	}

	/* Outside the scrolling body: the head never covers a focused element. */
	.carte-tete {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--s2);
		padding: var(--s3) var(--s4);
		border-bottom: 1px solid var(--color-line);
	}

	.carte-titre {
		margin: 0;
		font-size: 22px;
		font-weight: 600;
		line-height: 1.2;
	}

	/* The title takes the focus on opening: a landing point, not a control. */
	.carte-titre:focus {
		outline: none;
	}

	.carte-fermer {
		flex: none;
		gap: var(--s2);
	}

	.carte-fermer :global(.carte-fermer-icone) {
		width: 16px;
		height: 16px;
	}

	.carte-corps {
		flex: 1;
		min-height: 0;
		padding-bottom: var(--s6);
		overflow-y: auto;
		overscroll-behavior: contain;
	}

	/* Never more than half the height of the window: under the map there is always a place to put a
	   finger and scroll. `isolation` keeps the panes of Leaflet (z-index up to 1000) under the head. */
	.carte-cadre {
		position: relative;
		isolation: isolate;
		height: min(50dvh, 420px);
		background: var(--color-soft);
	}

	/* Its own stacking context: the controls, its siblings, stay above every pane of Leaflet. */
	.carte-vue {
		position: relative;
		z-index: 0;
		height: 100%;
	}

	.carte-avis {
		position: absolute;
		inset: 0;
		display: flex;
		flex-direction: column;
		gap: var(--s4);
		align-items: center;
		justify-content: center;
		margin: 0;
		padding: var(--s4);
		text-align: center;
	}

	.carte-avis-texte {
		max-width: var(--mesure);
		margin: 0;
	}

	/* Controls over the map, skin of the site (§ 2.3): nothing of the skin of Leaflet. */
	.carte-zoom {
		position: absolute;
		top: var(--s2);
		right: var(--s2);
		z-index: 1;
		display: flex;
		flex-direction: column;
		gap: var(--s2);
	}

	.carte-commande {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-width: 44px;
		height: 44px;
		padding: 0;
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-accent);
		font-size: 20px;
		font-weight: 600;
		line-height: 1;
		cursor: pointer;
	}

	.carte-commande:hover:not(:disabled) {
		background: var(--color-accent-doux);
	}

	.carte-commande:disabled {
		background: var(--color-soft);
		color: var(--color-muted);
		cursor: default;
	}

	.carte-retour {
		position: absolute;
		bottom: var(--s2);
		left: var(--s2);
		z-index: 1;
		padding: 0 var(--s3);
		font-size: 16px;
		font-weight: 400;
	}

	.carte-etat,
	.carte-bloc {
		margin: var(--s4) var(--s4) 0;
	}

	/* One line is kept for the announcements: the panel under it does not jump when one comes. */
	.carte-etat {
		min-height: 1.5em;
		line-height: 1.5;
	}

	.carte-relances {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	.carte-legende {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2) var(--s4);
		margin: 0;
		padding: 0;
		font-size: 14px;
		line-height: 1.4;
		list-style: none;
	}

	.carte-legende li {
		display: inline-flex;
		align-items: center;
		gap: var(--s1);
	}

	.echantillon {
		flex: none;
		width: 24px;
		height: 16px;
	}

	.echantillon-adresse {
		fill: none;
		stroke: var(--color-accent);
		stroke-width: 4px;
	}

	.echantillon-vendue {
		fill: var(--color-prix-encre);
		stroke: var(--color-paper);
		stroke-width: 2px;
	}

	.echantillon-groupe {
		fill: var(--color-prix-fond);
		fill-opacity: 0.7;
		stroke: var(--color-prix-encre);
		stroke-width: 3px;
		stroke-dasharray: 8 6;
	}

	.carte-motif-parcelle,
	.carte-limites {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.carte-limites {
		color: var(--color-muted);
		overflow-wrap: anywhere;
	}

	/* Leaflet: its skin is not rendered. The container takes the ground and the type of the site, and
	   its focus ring is drawn inside the frame. */
	.carte-cadre :global(.leaflet-container) {
		background: var(--color-soft);
		font-family: inherit;
		font-size: inherit;
		line-height: inherit;
		outline-offset: -3px;
	}

	/* Ground desaturated like the picture of the fiche (C10): colour only says what we add. */
	.carte-cadre :global(.carte-fond .leaflet-tile:not(.tile-failed)) {
		filter: grayscale(1) contrast(0.92);
	}

	/* A tile in outage keeps its box: a dashed edge on the outage ground, never a mute grey area. */
	.carte-cadre :global(.tile-failed) {
		background: var(--color-panne-fond);
		outline: 1px dashed var(--color-danger);
		outline-offset: -1px;
	}

	/* Layers: classes on the SVG paths of Leaflet, coloured by tokens. A style declaration wins over the
	   presentation attributes Leaflet writes on each path. */
	.carte-cadre :global(.parcel) {
		fill: var(--color-paper);
		fill-opacity: 0;
		stroke: var(--color-neutre);
		stroke-opacity: 1;
		stroke-width: 1px;
		cursor: pointer;
	}

	@media (hover: hover) and (pointer: fine) {
		.carte-cadre :global(.parcel:hover) {
			stroke: var(--color-ink);
			stroke-width: 2px;
		}
	}

	.carte-cadre :global(.parcel-sold-mark) {
		fill: var(--color-prix-encre);
		fill-opacity: 1;
		stroke: var(--color-paper);
		stroke-opacity: 1;
		stroke-width: 2px;
		pointer-events: none;
	}

	.carte-cadre :global(.parcel-casing) {
		fill: none;
		stroke: var(--color-paper);
		stroke-opacity: 1;
	}

	.carte-cadre :global(.parcel-casing-address) {
		stroke-width: 7px;
	}

	.carte-cadre :global(.parcel-casing-selected) {
		stroke-width: 6px;
	}

	.carte-cadre :global(.parcel-casing-group) {
		stroke-width: 6px;
		pointer-events: none;
	}

	/* Sold together: a dashed outline and a pale fill of the Price category, never the colour alone; a
	   touch goes through to the parcel under it. */
	.carte-cadre :global(.parcel-group) {
		fill: var(--color-prix-fond);
		fill-opacity: 0.7;
		stroke: var(--color-prix-encre);
		stroke-opacity: 1;
		stroke-width: 3px;
		stroke-dasharray: 8 6;
		pointer-events: none;
	}

	.carte-cadre :global(.parcel-address) {
		fill: none;
		stroke: var(--color-accent);
		stroke-opacity: 1;
		stroke-width: 4px;
	}

	.carte-cadre :global(.parcel-selected) {
		fill: var(--color-accent-doux);
		fill-opacity: 0.75;
		stroke: var(--color-ink);
		stroke-opacity: 1;
		stroke-width: 3px;
	}

	/* The parcel of the address, selected: its thick accent stroke and the fill of the selection. */
	.carte-cadre :global(.parcel-selected.parcel-address) {
		stroke: var(--color-accent);
		stroke-width: 4px;
	}

	.carte-cadre :global(.point-adresse) {
		pointer-events: none;
	}

	.carte-cadre :global(.point-adresse svg) {
		display: block;
		width: 48px;
		height: 48px;
		overflow: visible;
	}

	.carte-cadre :global(.point-adresse .halo) {
		fill: none;
		stroke: var(--color-accent);
		stroke-width: 2px;
	}

	.carte-cadre :global(.point-adresse .coeur) {
		fill: var(--color-accent);
		stroke: var(--color-paper);
		stroke-width: 3px;
	}

	@media (min-width: 721px) {
		.carte-dialogue {
			width: min(960px, calc(100% - 2 * var(--s5)));
			height: min(880px, calc(100dvh - 2 * var(--s5)));
			margin: auto;
			border: 1px solid var(--color-line);
			border-radius: var(--radius-panneau);
		}

		.carte-tete {
			padding: var(--s4) var(--s5);
		}

		.carte-cadre {
			height: min(50dvh, 520px);
			margin: var(--s5) var(--s5) 0;
			overflow: hidden;
			border: 1px solid var(--color-line);
			border-radius: var(--radius-tuile);
		}

		.carte-etat,
		.carte-bloc {
			margin: var(--s5) var(--s5) 0;
		}
	}

	@media print {
		.carte-dialogue {
			display: none;
		}
	}
</style>
