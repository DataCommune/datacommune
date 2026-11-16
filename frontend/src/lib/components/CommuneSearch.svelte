<script lang="ts">
	// Commune search field (UI-SPEC « CommuneSearch », D-42, D-43, D-45): an ARIA 1.2 combobox
	// over this instance's API only (`searchCommunes`), never a third-party origin.
	//   - from 2 characters, 200 ms after the last keystroke; each request aborts the previous
	//     one and only the latest answer is rendered;
	//   - at most 8 communes, then « Voir tous les résultats pour « {q} » » (results page);
	//   - ↑/↓ move `aria-activedescendant`, Enter opens the active option, Escape closes then
	//     clears, Tab leaves without choosing;
	//   - the departement filter (static referential of every French departement, not the
	//     hosted perimeter: D-34) is kept while typing and carried to the results page;
	//   - « Aucun résultat » never means « does not exist », and an outage is never shown as an
	//     empty result;
	//   - a postal-code search out of coverage is rendered as such, never as a zero (CR-01): the
	//     state is read in the served metadata (`couvertureCodePostal`), and a zero names the
	//     departements where the postal code was looked up, as served (D-34). A listed commune
	//     always wins over that metadata.
	// The option model is a discriminated union on `kind` (`SearchSuggestion`). Since 04-17 the
	// field is the single search of the site (D-105..D-107): with `types` = communes and addresses
	// (default), both searches run in parallel on this instance's API (`searchCommunes`,
	// `searchAdresses`), debounced and aborted together. Each option carries its type before its
	// name; the order of the types is the one served by the address search (`ordre_types`), never
	// recomputed here (`composerOptions`: head type up to 5, the other up to 3, remainder filled).
	//   - an address option opens `/adresse/{ban_id}` (D-112); a street without number does not
	//     navigate: it comes back in the field without a number, the caret at the start (D-107);
	//   - addresses out of coverage are said by a titled warn notice under the field, outside the
	//     listbox, with the served territory; the commune options stay listed (D-105);
	//   - a partial outage keeps the served options and says which half failed in a head option that
	//     reruns the search; a 429 or a refused access stays typed by `panne.ts` (SEM-01).
	// `types: ['commune']` keeps the communes-only field (the comparator of communes).
	// Without JavaScript the surrounding GET form reaches `/communes` with `q` and `departement`.
	// `prefillFromUrl` (home page, 04-08): the `q` of the URL — a legacy `/bien` link redirected to
	// `/?q=` — prefills the field once mounted, without searching nor navigating; without JavaScript
	// the field stays empty.
	// `variante` (04.8-02, E6, UI-SPEC v4 § 3.13): `compacte` (default) is the field of the comparator, of
	// the connected space and of the administration; `grand` is the large field of the public pages, its
	// visible label « Adresse, commune ou code postal » (communes and addresses), an 18 px field and its
	// plain button « Rechercher » attached to it, 620 px at most. Same combobox, same GET form.
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		searchCommunes,
		type CommuneSearchResult,
		type CommunesSearchResponse,
		type SearchSuggestion
	} from '$lib/api/communes';
	import {
		searchAdresses,
		type CorrespondanceNumeroRecherche,
		type HebergementBanRecherche,
		type RechercheAdresses,
		type ResultatRechercheAdresse,
		type TypeResultat
	} from '$lib/api/adresses';
	import { EXEMPLE_SAISIE } from '$lib/data/accueil';
	import { mentionArrondissement } from '$lib/data/arrondissements';
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { LIBELLE_HORS_COUVERTURE, MOTIFS_HORS_COUVERTURE } from '$lib/data/etats-libelles';
	import { detailPanne, type DetailPanne } from '$lib/data/panne';
	import {
		ANNONCE_VOIE,
		CORPS_AUCUN_RESULTAT,
		MAX_OPTIONS,
		PANNE_ADRESSES,
		PANNE_COMMUNES,
		PAS_ZERO,
		PRECISEZ_LE_NUMERO,
		RECHERCHE_TROP_LARGE,
		RELANCER,
		annonceResultats,
		composerOptions,
		libelleVoie,
		lieuAdresse,
		messageCorrespondanceNumero,
		saisieVoie,
		titreAdressesHorsCouverture,
		titreAucunResultat,
		variantesOrthographe
	} from '$lib/data/recherche-lieux';
	import {
		ANNONCE_CODE_POSTAL_HORS_COUVERTURE,
		CONSEIL_RECHERCHE_PAR_NOM,
		LIEN_COUVERTURE,
		TITRE_CODE_POSTAL_HORS_COUVERTURE,
		couvertureCodePostal,
		phraseRechercheBornee,
		conseilCodePostal,
		type CouvertureCodePostal
	} from '$lib/data/recherche-code-postal';

	interface Props {
		initialQuery?: string;
		initialDepartement?: string;
		/** Lists the municipal arrondissements of Paris, Lyon and Marseille too (default: yes). */
		inclureArrondissements?: boolean;
		/** Selection mode (comparator): receives the chosen commune instead of opening its fiche. */
		onselect?: (result: CommuneSearchResult) => void;
		/** Selection mode for addresses (comparator, 04-07): receives the chosen address instead of opening its page. */
		onselectadresse?: (result: ResultatRechercheAdresse) => void;
		submitLabel?: string;
		/** Result types of the field (UI-SPEC « CommuneSearch » généralisé): both by default. */
		types?: TypeResultat[];
		/** Prefill the field with the `q` of the URL once mounted (home page, legacy `/bien` links). */
		prefillFromUrl?: boolean;
		/** Optional public wording when the static department referential needs clarification. */
		departementLabel?: string;
		departementHint?: string;
		/** Home page: the filter folds under a summary; a chosen department stays in sight. */
		departementReplie?: boolean;
		/**
		 * No departement filter at all (home page, 09/11): a postal code already bounds the search and each
		 * option names its departement; refining stays on the results page, which keeps the filter.
		 */
		sansDepartement?: boolean;
		/** Form of the field: compact (default) or the large field of the public pages. */
		variante?: 'compacte' | 'grand';
		/** Visible label of the field, when a page words it its own way (root 404, 04.8-07). */
		etiquette?: string;
		/** Large field: the field and the departement on one row, filling the column (the directory). */
		enLigne?: boolean;
		/** Large field on the solid head of the home page: the field and its button in one white bar. */
		surAplat?: boolean;
	}

	let {
		initialQuery = '',
		initialDepartement = '',
		inclureArrondissements = true,
		onselect,
		onselectadresse,
		submitLabel,
		types = ['commune', 'adresse'],
		prefillFromUrl = false,
		departementLabel = 'Département',
		departementHint,
		departementReplie = false,
		sansDepartement = false,
		variante = 'compacte',
		etiquette,
		enLigne = false,
		surAplat = false
	}: Props = $props();

	const chercheCommunes = $derived(types.includes('commune'));
	const chercheAdresses = $derived(types.includes('adresse'));
	const lieux = $derived(chercheCommunes && chercheAdresses);
	// The communes-only field keeps its phase 3 wording; the single field has the UI-SPEC one.
	const libelles = $derived(
		lieux
			? {
					formulaire: 'Rechercher un lieu',
					champ: 'Commune, code postal ou adresse',
					placeholder: EXEMPLE_SAISIE,
					liste: 'Lieux proposés',
					bouton: 'Rechercher un lieu'
				}
			: chercheAdresses
				? {
						formulaire: 'Rechercher une adresse',
						champ: 'Adresse',
						placeholder: EXEMPLE_SAISIE,
						liste: 'Adresses proposées',
						bouton: 'Rechercher une adresse'
					}
				: {
						formulaire: 'Rechercher une commune',
						champ: 'Rechercher une commune',
						placeholder: 'Nom de commune ou code postal',
						liste: 'Communes proposées',
						bouton: 'Rechercher une commune'
					}
	);

	const grand = $derived(variante === 'grand');
	// The large label names an address search: a communes-only field keeps its own.
	const libelleChamp = $derived(etiquette ?? (grand && lieux ? 'Adresse, commune ou code postal' : libelles.champ));
	const libelleBouton = $derived(submitLabel ?? (grand ? 'Rechercher' : libelles.bouton));

	const MIN_LENGTH = 2;
	const DEBOUNCE_MS = 200;
	const TYPES_PAR_DEFAUT: TypeResultat[] = ['commune', 'adresse'];

	const uid = $props.id();
	const inputId = `${uid}-q`;
	const listId = `${uid}-list`;
	const selectId = `${uid}-departement`;
	const selectHintId = `${uid}-departement-hint`;
	const optionId = (index: number) => `${uid}-option-${index}`;

	type Status = 'idle' | 'loading' | 'ready' | 'error';

	let query = $state(untrack(() => initialQuery));

	onMount(() => {
		if (!prefillFromUrl || query !== '') return;
		const q = new URL(window.location.href).searchParams.get('q');
		if (q) query = q;
	});
	let departement = $state(untrack(() => initialDepartement));
	let filtreOuvert = $state(untrack(() => initialDepartement !== ''));
	let status = $state<Status>('idle');
	// The failed search, typed (WR-01): a rate limit or a refused access is worded by the client,
	// a null message keeps the outage text.
	let erreur = $state<DetailPanne | null>(null);
	let suggestions = $state<SearchSuggestion[]>([]);
	// The query the displayed suggestions answer: the « see all » label names it.
	let answered = $state('');
	// Coverage of the answered postal-code search (null for a name or a hosted answer).
	let couverture = $state<CouvertureCodePostal>(null);
	// The half of the single field that failed while the other answered (partial outage).
	let panneAdresses = $state<DetailPanne | null>(null);
	let panneCommunes = $state<DetailPanne | null>(null);
	// Address search served `etat: 'trop_large'` (R-08): said apart, neither an outage nor a zero.
	let adressesTropLarges = $state(false);
	// BAN hosting served with the address search: out of coverage is said under the field (D-105).
	let hebergementAdresses = $state<HebergementBanRecherche | null>(null);
	// API qualification for the answered query; cleared with every cancellation guard.
	let correspondanceNumero = $state<CorrespondanceNumeroRecherche | null>(null);
	// Options answered per type, for the announcement; null for a type not searched or failed.
	let compte = $state<{ communes: number | null; adresses: number | null }>({ communes: null, adresses: null });
	// One-off announcement of a street put back in the field, cleared by the next keystroke.
	let annonceVoie = $state('');
	let open = $state(false);
	let activeIndex = $state(-1);
	let inputEl: HTMLInputElement | undefined = $state(undefined);

	let timer: ReturnType<typeof setTimeout> | undefined;
	let controller: AbortController | null = null;
	let sequence = 0;

	const trimmed = $derived(query.trim());
	const listed = $derived(open && status === 'ready');
	// Partial outage: a head option says which half failed and reruns the search.
	const lignePanne = $derived(
		panneAdresses
			? (panneAdresses.message ?? PANNE_ADRESSES)
			: panneCommunes
				? (panneCommunes.message ?? PANNE_COMMUNES)
				: null
	);
	const decalage = $derived(lignePanne ? 1 : 0);
	// The outage line, the options, then the « see all » entry: one index space for the keyboard.
	const optionCount = $derived(listed ? decalage + suggestions.length + 1 : 0);
	const seeAllIndex = $derived(decalage + suggestions.length);
	// A postal code out of coverage with nothing listed keeps the phase 3 contract (C22): its own
	// notice and title, never the address one, even when the address search answers out of coverage.
	const codePostalHorsCouverture = $derived(
		status === 'ready' && suggestions.length === 0 && couverture?.kind === 'hors-couverture'
	);
	const adressesHorsCouverture = $derived(
		status === 'ready' && !codePostalHorsCouverture && hebergementAdresses?.etat === 'hors_couverture'
			? hebergementAdresses
			: null
	);
	const messageNumero = $derived(
		status === 'ready' && !adressesHorsCouverture && !panneAdresses
			? messageCorrespondanceNumero(correspondanceNumero)
			: null
	);
	const activeId = $derived(
		listed && activeIndex >= 0 && activeIndex < optionCount ? optionId(activeIndex) : undefined
	);
	// The focus stays in the input, so the browser never scrolls the list for the active option: the
	// list scrolls itself to keep it in view, and only the list, never the page (UI-003).
	$effect(() => {
		if (!activeId) return;
		const liste = document.getElementById(listId);
		const option = document.getElementById(activeId);
		if (!liste || !option) return;
		const boite = liste.getBoundingClientRect();
		const cible = option.getBoundingClientRect();
		if (cible.top < boite.top) liste.scrollTop -= boite.top - cible.top;
		else if (cible.bottom > boite.bottom) liste.scrollTop += cible.bottom - boite.bottom;
	});
	const announcement = $derived.by(() => {
		if (annonceVoie) return annonceVoie;
		if (status === 'loading') return 'Recherche en cours…';
		if (status === 'error') return erreur?.message ?? 'La recherche est momentanément indisponible.';
		if (status !== 'ready') return '';
		if (suggestions.length === 0 && adressesHorsCouverture) {
			return titreAdressesHorsCouverture(adressesHorsCouverture);
		}
		if (suggestions.length === 0 && couverture?.kind === 'hors-couverture') {
			return ANNONCE_CODE_POSTAL_HORS_COUVERTURE;
		}
		const tropLarge = adressesTropLarges ? `${RECHERCHE_TROP_LARGE} ` : '';
		const adressesAnnoncees = suggestions
			.filter((suggestion) => suggestion.kind === 'adresse')
			.map((suggestion) => suggestion.result);
		const arrondissements = suggestions.filter(
			(suggestion) => suggestion.kind === 'commune' && suggestion.result.type_zone === 'arrondissement'
		).length;
		const resultat = annonceResultats(compte.communes, compte.adresses, answered, adressesAnnoncees, arrondissements);
		return `${tropLarge}${messageNumero ? `${messageNumero} ${resultat}` : resultat}`;
	});

	function resultsHref(q: string, dept: string): string {
		const params = new URLSearchParams();
		if (q) params.set('q', q);
		if (dept) params.set('departement', dept);
		const qs = params.toString();
		return `/communes${qs ? '?' + qs : ''}`;
	}

	function nomDepartement(code: string): string {
		const nom = DEPARTEMENTS.find((d) => d.code === code)?.nom;
		return nom ? `${nom} (${code})` : `Département ${code}`;
	}

	/** « Département (code) · CP »: homonyms are told apart here, never merged. */
	function lieu(result: CommuneSearchResult): string {
		const departementLibelle = nomDepartement(result.departement);
		const base = result.code_postal ? `${departementLibelle} · ${result.code_postal}` : departementLibelle;
		const arrondissement = mentionArrondissement(result);
		return arrondissement ? `${arrondissement} · ${base}` : base;
	}

	function cle(suggestion: SearchSuggestion, index: number): string {
		if (suggestion.kind === 'commune') return `commune-${suggestion.result.code_insee}`;
		const result = suggestion.result;
		return result.ban_id ? `adresse-${result.ban_id}` : `voie-${index}-${result.nom_voie}-${result.code_postal}`;
	}

	/** Second line of an address option; a street ends with « · Précisez le numéro » (D-107). */
	function lieuOption(result: ResultatRechercheAdresse): string {
		const lieu = lieuAdresse(result);
		return result.type_resultat === 'voie' ? [lieu, PRECISEZ_LE_NUMERO].filter(Boolean).join(' · ') : lieu;
	}

	/** Phrase of the served out-of-coverage reason, or the generic state. */
	function phraseMotif(motif: string | null): string {
		return motif && Object.hasOwn(MOTIFS_HORS_COUVERTURE, motif)
			? MOTIFS_HORS_COUVERTURE[motif as keyof typeof MOTIFS_HORS_COUVERTURE].phrase
			: `${LIBELLE_HORS_COUVERTURE}.`;
	}

	type Issue<T> = { ok: true; valeur: T } | { ok: false; panne: DetailPanne } | null;

	/** Settles a request: its answer, or its failure typed by `detailPanne`. */
	async function issue<T>(promesse: Promise<T> | null): Promise<Issue<T>> {
		if (promesse === null) return null;
		try {
			return { ok: true, valeur: await promesse };
		} catch (err) {
			return { ok: false, panne: detailPanne(err) };
		}
	}

	function badge(result: CommuneSearchResult): string | null {
		if (result.hebergement.etat !== 'hors_couverture') return null;
		const motif = result.hebergement.motif;
		return motif
			? (MOTIFS_HORS_COUVERTURE[motif]?.libelle ?? LIBELLE_HORS_COUVERTURE)
			: LIBELLE_HORS_COUVERTURE;
	}

	/** Drops the pending and in-flight requests: their answer can no longer be rendered. */
	function cancel() {
		clearTimeout(timer);
		timer = undefined;
		controller?.abort();
		controller = null;
		sequence += 1;
		couverture = null;
		panneAdresses = null;
		panneCommunes = null;
		adressesTropLarges = false;
		hebergementAdresses = null;
		correspondanceNumero = null;
	}

	async function run() {
		cancel();
		const q = trimmed;
		if (q.length < MIN_LENGTH) {
			status = 'idle';
			suggestions = [];
			open = false;
			return;
		}
		const current = sequence;
		const request = new AbortController();
		controller = request;
		status = 'loading';
		suggestions = [];
		open = false;
		activeIndex = -1;
		const filtre = departement || undefined;
		try {
			// Both searches in parallel, aborted together; each answer or failure is kept typed.
			const [communes, adresses] = await Promise.all([
				issue<CommunesSearchResponse>(
					chercheCommunes
						? searchCommunes(q, { departement: filtre, limit: MAX_OPTIONS, inclureArrondissements }, fetch, request.signal)
						: null
				),
				issue<RechercheAdresses>(
					chercheAdresses
						? searchAdresses(q, { departement: filtre, limite: MAX_OPTIONS, signal: request.signal })
						: null
				)
			]);
			if (current !== sequence) return;
			const echecs = [communes, adresses].filter((reponse) => reponse !== null && !reponse.ok);
			if (echecs.length === [communes, adresses].filter((reponse) => reponse !== null).length) {
				const premier = echecs[0];
				erreur = premier && !premier.ok ? premier.panne : detailPanne(null);
				status = 'error';
				suggestions = [];
				couverture = null;
				return;
			}
			const ordre = adresses?.ok ? adresses.valeur.ordre_types : TYPES_PAR_DEFAUT;
			const tropLarge = adresses?.ok === true && adresses.valeur.etat === 'trop_large';
			suggestions = composerOptions(
				communes?.ok ? communes.valeur.donnees : [],
				adresses?.ok ? adresses.valeur.resultats : [],
				ordre
			);
			compte = {
				communes: communes?.ok ? suggestions.filter((s) => s.kind === 'commune').length : null,
				// Not searched to the end (R-08): not counted, never a zero.
				adresses: adresses?.ok && !tropLarge ? suggestions.filter((s) => s.kind === 'adresse').length : null
			};
			adressesTropLarges = tropLarge;
			couverture = communes?.ok ? couvertureCodePostal(q, communes.valeur.meta) : null;
			panneCommunes = communes && !communes.ok ? communes.panne : null;
			panneAdresses = adresses && !adresses.ok ? adresses.panne : null;
			hebergementAdresses = adresses?.ok ? adresses.valeur.hebergement.ban : null;
			correspondanceNumero = adresses?.ok ? (adresses.valeur.correspondance_numero ?? null) : null;
			answered = q;
			status = 'ready';
			open = true;
		} finally {
			if (controller === request) controller = null;
		}
	}

	function schedule() {
		cancel();
		// The typed text changed: the listed suggestions answer another query and must not be
		// chosen (nor reopened on focus) while the next request waits for its debounce.
		status = 'idle';
		erreur = null;
		annonceVoie = '';
		suggestions = [];
		couverture = null;
		open = false;
		activeIndex = -1;
		if (trimmed.length >= MIN_LENGTH) timer = setTimeout(run, DEBOUNCE_MS);
	}

	function close() {
		open = false;
		activeIndex = -1;
	}

	function closeWhenLeavingAnchor(event: FocusEvent) {
		const anchor = event.currentTarget;
		const next = event.relatedTarget;
		if (anchor instanceof HTMLElement && next instanceof Node && anchor.contains(next)) return;
		close();
	}

	// A pending debounce or request must not outlive the field (navigation, keyed remount).
	onDestroy(cancel);

	function choose(index: number) {
		if (lignePanne && index === 0) {
			retry();
			return;
		}
		if (index === seeAllIndex) {
			close();
			goto(resultsHref(answered, departement));
			return;
		}
		const suggestion = suggestions[index - decalage];
		if (!suggestion) return;
		close();
		if (suggestion.kind === 'commune') {
			if (onselect) onselect(suggestion.result);
			else goto(`/commune/${encodeURIComponent(suggestion.result.code_insee)}`);
			return;
		}
		if (suggestion.result.type_resultat === 'voie') {
			choisirVoie(suggestion.result);
			return;
		}
		if (onselectadresse && suggestion.result.ban_id) {
			onselectadresse(suggestion.result);
			return;
		}
		// An address opens its page by the served BAN key, never by a coordinate (D-112).
		if (suggestion.result.ban_id) goto(`/adresse/${encodeURIComponent(suggestion.result.ban_id)}`);
	}

	/**
	 * A street has no page (D-107): it comes back in the field as « {voie}, {CP} {Commune} », the
	 * caret before it so that the number is typed first, and the live region says so. The listed
	 * options answered another text: they are dropped, no search is sent until the next keystroke.
	 */
	async function choisirVoie(result: ResultatRechercheAdresse) {
		cancel();
		query = saisieVoie(result);
		status = 'idle';
		suggestions = [];
		open = false;
		activeIndex = -1;
		annonceVoie = ANNONCE_VOIE;
		await tick();
		inputEl?.focus();
		inputEl?.setSelectionRange(0, 0);
	}

	function handleKeydown(event: KeyboardEvent) {
		switch (event.key) {
			case 'ArrowDown':
				if (status !== 'ready') return;
				event.preventDefault();
				if (!open) {
					open = true;
					activeIndex = 0;
					return;
				}
				activeIndex = activeIndex < optionCount - 1 ? activeIndex + 1 : optionCount - 1;
				return;
			case 'ArrowUp':
				if (status !== 'ready') return;
				event.preventDefault();
				if (!open) open = true;
				activeIndex = activeIndex <= 0 ? optionCount - 1 : activeIndex - 1;
				return;
			case 'Enter':
				if (listed && activeIndex >= 0) {
					event.preventDefault();
					choose(activeIndex);
				}
				// Otherwise the form submits: the results page for the typed query.
				return;
			case 'Tab':
				close();
				return;
			case 'Escape':
				// Nothing left to close or clear: the Escape belongs to what holds the field (a menu, the
				// comparator's add zone, audit C-3), so it is not consumed.
				if (!open && query === '') return;
				event.preventDefault();
				if (open) {
					close();
					return;
				}
				cancel();
				query = '';
				status = 'idle';
				annonceVoie = '';
				suggestions = [];
				return;
		}
	}

	function handleDepartementChange() {
		if (trimmed.length >= MIN_LENGTH) run();
	}

	function removeDepartementFilter() {
		departement = '';
		run();
		inputEl?.focus();
	}

	function retry() {
		run();
		inputEl?.focus();
	}

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		cancel();
		close();
		goto(resultsHref(trimmed, departement));
	}
</script>

{#snippet filtreDepartement()}
	<div class="cs-field cs-field-select">
		<label class="cs-label" for={selectId}>{departementLabel}</label>
		{#if departementHint}
			<p id={selectHintId} class="cs-field-hint">{departementHint}</p>
		{/if}
		<select
			id={selectId}
			class="cs-input"
			name="departement"
			aria-describedby={departementHint ? selectHintId : undefined}
			bind:value={departement}
			onchange={handleDepartementChange}
		>
			<option value="">Tous les départements</option>
			{#each DEPARTEMENTS as d (d.code)}
				<option value={d.code}>{d.nom} ({d.code})</option>
			{/each}
		</select>
	</div>
{/snippet}

{#snippet noticeAdresses(ban: HebergementBanRecherche)}
	<div class="cs-notice cs-notice-warn cs-notice-adresses">
		<p class="cs-notice-title">{titreAdressesHorsCouverture(ban)}</p>
		<p>{phraseMotif(ban.motif)} {PAS_ZERO}</p>
		<p><a href="/datasets">{LIEN_COUVERTURE}</a></p>
		{#if departement}
			<button type="button" class="cs-button" onclick={removeDepartementFilter}>
				Retirer le filtre département
			</button>
		{/if}
	</div>
{/snippet}

{#snippet noticeNumero(message: string, inFlow = false)}
	<p class="cs-number-match" class:cs-notice={inFlow} data-number-match="proches">{message}</p>
{/snippet}

<!-- The anchored field: the combobox and its popup, alone or with the attached button of the large field. -->
{#snippet champ()}
	<div class="cs-anchor" onfocusout={closeWhenLeavingAnchor}>
		<input
			bind:this={inputEl}
			id={inputId}
			class="cs-input"
			type="text"
			name="q"
			role="combobox"
			autocomplete="off"
			spellcheck="false"
			placeholder={libelles.placeholder}
			aria-autocomplete="list"
			aria-expanded={listed}
			aria-controls={listId}
			aria-activedescendant={activeId}
			bind:value={query}
			oninput={schedule}
			onkeydown={handleKeydown}
			onfocus={() => {
				if (status === 'ready') open = true;
			}}
		/>
		{#if listed}
			<div class="cs-popup">
				{#if messageNumero}
					{@render noticeNumero(messageNumero)}
				{/if}
				{#if adressesHorsCouverture}
					<!-- The notice shares the anchored surface with the options, but stays outside the ARIA
					     listbox so its explanation and actions are never options. -->
					{@render noticeAdresses(adressesHorsCouverture)}
				{:else if adressesTropLarges}
					<!-- R-08: outside the listbox as the notice above; no « Relancer », the same input meets
					     the same budget again. -->
					<div class="cs-notice cs-notice-adresses">
						<p class="cs-notice-title">{RECHERCHE_TROP_LARGE}</p>
					</div>
				{/if}
				{#if listed}
					<ul id={listId} class="cs-listbox" role="listbox" aria-label={libelles.liste}>
				{#if lignePanne}
					<!-- Partial outage (SEM-01): the half that failed, said at the head of the list; the
					     served options stay below. Choosing it reruns both searches. -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<li
						id={optionId(0)}
						class="cs-option cs-option-panne"
						class:cs-option-active={activeIndex === 0}
						role="option"
						aria-selected={activeIndex === 0}
						onmousedown={(event) => event.preventDefault()}
						onclick={() => choose(0)}
					>
						<span class="cs-option-panne-texte">{lignePanne}</span>
						<span class="cs-option-relancer">{RELANCER}</span>
					</li>
				{/if}
				{#each suggestions as suggestion, rang (cle(suggestion, rang))}
					{@const index = rang + decalage}
					<!-- Options are never focused: the keyboard stays on the combobox input, which moves
					     aria-activedescendant (ARIA 1.2 combobox). The click is the pointer path only. -->
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<li
						id={optionId(index)}
						class="cs-option"
						class:cs-option-active={index === activeIndex}
						class:cs-option-adresse={suggestion.kind === 'adresse'}
						role="option"
						aria-selected={index === activeIndex}
						onmousedown={(event) => event.preventDefault()}
						onclick={() => choose(index)}
					>
						{#if suggestion.kind === 'commune'}
							{@const result = suggestion.result}
							{@const coverage = badge(result)}
							{#if lieux}<span class="cs-type">{result.type_zone === 'arrondissement' ? 'Arrondissement' : 'Commune'}</span>{/if}
							<span class="cs-option-name">{result.nom}</span>
							<span class="cs-option-meta">{lieu(result)}</span>
							{#if coverage}<span class="cs-badge">{coverage}</span>{/if}
						{:else}
							{@const result = suggestion.result}
							{#if lieux}<span class="cs-type">{result.type_resultat === 'voie' ? 'Voie' : 'Adresse'}</span>{/if}
							<span class="cs-option-name">{libelleVoie(result.numero, result.rep, result.nom_voie)}</span>
							<span class="cs-option-meta">{lieuOption(result)}</span>
						{/if}
					</li>
				{/each}
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<li
					id={optionId(seeAllIndex)}
					class="cs-option cs-option-all"
					class:cs-option-active={seeAllIndex === activeIndex}
					role="option"
					aria-selected={seeAllIndex === activeIndex}
					onmousedown={(event) => event.preventDefault()}
					onclick={() => choose(seeAllIndex)}
				>
					Voir tous les résultats pour « {answered} »
				</li>
					</ul>
				{/if}
			</div>
		{/if}
	</div>
{/snippet}

<form
	class="cs"
	class:cs-grand={grand}
	class:cs-en-ligne={grand && enLigne}
	class:cs-sur-aplat={grand && surAplat}
	action="/communes"
	method="get"
	role="search"
	aria-label={libelles.formulaire}
	onsubmit={handleSubmit}
>
	<div class="cs-row">
		<div class="cs-field">
			<label class="cs-label" for={inputId}>{libelleChamp}</label>
			{#if grand}
				<div class="cs-saisie">{@render champ()}<button type="submit" class="cs-submit">{libelleBouton}</button></div>
			{:else}
				{@render champ()}
			{/if}
		</div>

		{#if sansDepartement}
			<!-- No filter: the form carries `q` only. -->
		{:else if departementReplie}
			<details class="cs-repli" bind:open={filtreOuvert}>
				<summary>Affiner par département</summary>
				{@render filtreDepartement()}
			</details>
		{:else}
			{@render filtreDepartement()}
		{/if}

		{#if !grand}<button type="submit" class="cs-submit">{libelleBouton}</button>{/if}
	</div>
	{#if messageNumero && !listed}
		{@render noticeNumero(messageNumero, true)}
	{/if}
	{#if adressesHorsCouverture && !listed}
		{@render noticeAdresses(adressesHorsCouverture)}
	{/if}

	<p class="cs-sr-only" role="status" aria-live="polite">{announcement}</p>

	{#if status === 'error'}
		<div class="cs-notice cs-notice-warn">
			{#if erreur?.titre}<p class="cs-notice-title">{erreur.titre}</p>{/if}
			<p>{erreur?.message ?? 'La recherche est momentanément indisponible. Réessayez dans un instant.'}</p>
			<button type="button" class="cs-button" onclick={retry}>Relancer la recherche</button>
		</div>
	{:else if status === 'ready' && suggestions.length === 0 && couverture?.kind === 'hors-couverture'}
		<div class="cs-notice cs-notice-warn">
			<p class="cs-notice-title">{TITRE_CODE_POSTAL_HORS_COUVERTURE}</p>
			<p>{couverture.phrase ?? `${LIBELLE_HORS_COUVERTURE}.`} {CONSEIL_RECHERCHE_PAR_NOM}</p>
			<p><a href="/datasets">{LIEN_COUVERTURE}</a> <a href="/couverture">Limites connues des données</a></p>
			{#if departement}
				<button type="button" class="cs-button" onclick={removeDepartementFilter}>
					Retirer le filtre département
				</button>
			{/if}
		</div>
	{:else if status === 'ready' && suggestions.length === 0 && !adressesHorsCouverture}
		<div class="cs-notice">
			<p class="cs-notice-title">{titreAucunResultat(compte.communes, compte.adresses, answered)}</p>
			{#if couverture?.kind === 'bornee'}
				<p>
					Cela ne veut pas dire qu'elle n'existe pas. {phraseRechercheBornee(
						couverture.departements,
						nomDepartement
					)}
					{conseilCodePostal(answered)}
				</p>
			{:else if compte.adresses !== null}
				<p>{CORPS_AUCUN_RESULTAT}</p>
			{:else}
				<p>
					Cela ne veut pas dire qu'elle n'existe pas. Vérifiez l'orthographe, essayez le code postal
					ou retirez le filtre département.
				</p>
			{/if}
			<!-- Next step of an empty search (04.7-14, R16): another spelling; « Voir tous les résultats »
			     leads to the results page, which adds the communes au nom proche and examples. -->
			{#each variantesOrthographe(answered) as variante (variante)}
				<p><a href={`/communes?${new URLSearchParams({ q: variante, ...(departement ? { departement } : {}) })}`}>Essayer « {variante} »</a></p>
			{/each}
			{#if departement}
				<button type="button" class="cs-button" onclick={removeDepartementFilter}>
					Retirer le filtre département
				</button>
			{/if}
		</div>
	{/if}
</form>

<style>
	.cs {
		color: var(--color-ink);
	}

	.cs-row {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: var(--s4);
	}

	.cs-field {
		display: flex;
		flex: 1 1 320px;
		flex-direction: column;
		gap: var(--s1);
		min-width: 0;
	}

	.cs-field-select {
		flex: 0 1 256px;
	}

	.cs-label {
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.cs-field-hint {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.cs-anchor {
		position: relative;
	}

	.cs-input {
		box-sizing: border-box;
		min-height: 44px;
		width: 100%;
		padding: 0 var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
	}

	.cs-input::placeholder {
		color: var(--color-muted);
	}

	/* One anchored surface: the explanation precedes the options instead of being covered by them. */
	.cs-popup {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		right: 0;
		z-index: 30;
		margin: 0;
		background: var(--color-paper);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-tuile);
	}

	.cs-number-match {
		margin: 0;
		padding: var(--s2) var(--s4);
		color: var(--color-muted);
		font-size: 16px;
		line-height: 1.5;
	}

	/* The options keep their own vertical scroll and ARIA listbox identity. */
	.cs-listbox {
		position: static;
		max-height: 360px;
		margin: 0;
		padding: var(--s1) 0;
		overflow-y: auto;
		list-style: none;
	}

	/* The scrolled options stay inside the rounded corners of the popup. */
	.cs-popup > .cs-listbox {
		border-radius: inherit;
	}

	.cs-number-match + .cs-listbox {
		border-top: 1px solid var(--color-line);
	}

	.cs-popup > .cs-notice {
		margin: 0;
		border: 0;
		border-radius: inherit;
	}

	.cs-notice-adresses + .cs-listbox {
		border-top: 1px solid var(--color-line);
	}

	.cs-option {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--s1) var(--s2);
		min-height: 44px;
		padding: var(--s2) var(--s3);
		font-size: 16px;
		line-height: 1.5;
		cursor: pointer;
	}

	.cs-option:hover,
	.cs-option-active {
		background: var(--color-soft);
	}

	.cs-option-active {
		outline: 2px solid var(--color-focus);
		outline-offset: -2px;
	}

	.cs-option-name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	/* An address option: « {numéro} {voie} » on its own line, then the place in `muted` 13px. */
	.cs-option-adresse .cs-option-meta {
		flex-basis: 100%;
	}

	/* Type label before the name (D-106): neutral `soft` badge, 13px / 600, never a judgement. */
	.cs-type {
		padding: 0 var(--s1);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.cs-option-meta {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* Partial outage line: the warn wording of an outage, and the rerun in the « see all » style. */
	.cs-option-panne {
		border-bottom: 1px solid var(--color-line);
		background: var(--color-warn-bg);
	}

	.cs-option-panne-texte {
		overflow-wrap: anywhere;
	}

	.cs-option-relancer {
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.cs-option-all {
		border-top: 1px solid var(--color-line);
		text-decoration: underline;
		text-underline-offset: 4px;
		overflow-wrap: anywhere;
	}

	.cs-badge {
		padding: 0 var(--s1);
		border: 1px solid var(--color-warn);
		border-radius: var(--radius-badge);
		background: var(--color-warn-bg);
		color: var(--color-warn);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.cs-submit {
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-accent);
		border-radius: var(--radius-control);
		background: var(--color-accent);
		color: var(--color-paper);
		font-family: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		font-weight: 600;
		cursor: pointer;
	}

	.cs-submit:hover {
		background: var(--color-accent-hover);
	}

	.cs-notice {
		margin-top: var(--s4);
		padding: var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-paper);
		font-size: 16px;
		line-height: 1.5;
	}

	.cs-notice p {
		margin: 0;
	}

	.cs-notice p + p {
		margin-top: var(--s2);
	}

	.cs-notice a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}

	.cs-notice-warn {
		border-color: var(--color-warn);
		background: var(--color-warn-bg);
	}

	.cs-notice-title {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.cs-button {
		margin-top: var(--s3);
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		cursor: pointer;
	}

	.cs-button:hover {
		background: var(--color-soft);
	}

	.cs-sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
		border: 0;
	}

	/* A search form is a screen control: the printed results keep their cards only. */
	@media print {
		.cs {
			display: none;
		}
	}

	@media (max-width: 720px) {
		.cs-field,
		.cs-field-select,
		.cs-submit {
			flex: 1 1 100%;
		}
	}

  :global(.public-site) .cs-field-select { flex-basis: 226px; }
  :global(.public-site) .cs-input { min-height: 48px; font-size: 16px; }
  :global(.public-site) .cs-submit { min-height: 48px; }
  @media (max-width: 720px) {
    :global(.public-site) .cs-field-select { flex-basis: 100%; }
  }

	/* Large field of the public pages (UI-SPEC v4 § 3.13): the label above, the 18 px field and its plain
	   button attached, 620 px at most; the departement filter below, at its compact size. */
	.cs-grand .cs-row {
		display: grid;
		gap: var(--s3);
	}

	.cs-grand .cs-field:not(.cs-field-select) {
		width: 100%;
		max-width: 620px;
		gap: var(--s-demi);
	}

	.cs-grand .cs-field:not(.cs-field-select) .cs-label {
		font-size: 16px;
	}

	.cs-grand .cs-field-select {
		width: 100%;
		max-width: 256px;
	}

	/* In line (the directory): same label, same height, and both edges of the blocks under them. */
	.cs-en-ligne .cs-row {
		grid-template-columns: minmax(0, 1fr) 256px;
		align-items: stretch;
		gap: var(--s4);
	}

	.cs-grand.cs-en-ligne .cs-field:not(.cs-field-select),
	.cs-grand.cs-en-ligne .cs-field-select {
		gap: var(--s-demi);
		max-width: none;
	}

	.cs-grand.cs-en-ligne .cs-field-select .cs-label {
		font-size: 16px;
	}

	.cs-grand.cs-en-ligne .cs-field-select .cs-input {
		flex: 1 1 auto;
	}

	@media (max-width: 720px) {
		.cs-en-ligne .cs-row {
			grid-template-columns: minmax(0, 1fr);
			gap: var(--s3);
		}

		/* Stacked, the departement has the width of the field above it. */
		.cs-grand .cs-field-select {
			max-width: none;
		}
	}

	/* The example of the large field stays whole at 320 px. */
	@media (max-width: 359px) {
		.cs-grand .cs-saisie .cs-input {
			padding-inline: var(--s2);
		}

		.cs-grand .cs-submit {
			padding: 0 var(--s3);
		}
	}

	/* The folded filter keeps a 44 px target (UI-SPEC § 8). */
	.cs-repli summary {
		display: flex;
		align-items: center;
		width: fit-content;
		min-height: 44px;
		color: var(--color-muted);
		font-size: 14px;
		cursor: pointer;
	}

	.cs-repli[open] summary {
		margin-bottom: var(--s1);
	}

	.cs-grand .cs-saisie {
		display: flex;
		align-items: stretch;
		min-width: 0;
	}

	.cs-grand .cs-anchor {
		flex: 1 1 auto;
		min-width: 0;
	}

	.cs-grand .cs-input {
		font-size: 18px;
		line-height: 1.4;
	}

	.cs-grand .cs-saisie .cs-input {
		padding: var(--s3) var(--s4);
		border-right: 0;
		border-radius: var(--radius-control) 0 0 var(--radius-control);
		background: var(--color-paper);
	}

	.cs-grand .cs-field-select .cs-input {
		font-size: 16px;
		line-height: inherit;
	}

	.cs-grand .cs-submit {
		flex: none;
		border-radius: 0 var(--radius-control) var(--radius-control) 0;
	}

	/* On a solid head: the words in the light of the head, the field and its button in one white bar, where the
	   ring of focus takes the ink again, as does whatever opens on paper under it. */
	.cs-sur-aplat {
		color: var(--color-hero-encre);
	}

	.cs-sur-aplat .cs-field:not(.cs-field-select) {
		max-width: none;
	}

	.cs-sur-aplat .cs-field-hint,
	.cs-sur-aplat .cs-repli summary {
		color: var(--color-hero-encre-2);
	}

	.cs-sur-aplat .cs-saisie {
		gap: var(--s2);
		padding: var(--s2);
		border-radius: var(--radius-panneau);
		background: var(--color-paper);
	}

	.cs-sur-aplat .cs-saisie,
	.cs-sur-aplat .cs-popup,
	.cs-sur-aplat .cs-notice {
		color: var(--color-ink);
		--color-focus: var(--color-accent);
	}

	.cs-sur-aplat .cs-saisie .cs-input {
		padding: var(--s2) var(--s3);
		border: 0;
		border-radius: var(--radius-control);
	}

	.cs-sur-aplat .cs-submit {
		padding: 0 var(--s5);
		border-color: var(--color-hero-action);
		border-radius: var(--radius-control);
		background: var(--color-hero-action);
		color: var(--color-hero-sur-action);
	}

	.cs-sur-aplat .cs-submit:hover {
		border-color: var(--color-accent-doux);
		background: var(--color-accent-doux);
	}

	/* The example of the field stays whole, as on the large field: the bar gives it the room, then a line. */
	@media (max-width: 720px) {
		.cs-sur-aplat .cs-submit {
			padding: 0 var(--s3);
		}
	}

	@media (max-width: 359px) {
		.cs-sur-aplat .cs-saisie {
			flex-wrap: wrap;
		}

		.cs-sur-aplat .cs-submit {
			flex: 1 1 100%;
		}
	}
</style>
