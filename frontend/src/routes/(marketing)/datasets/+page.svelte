<script lang="ts">
	// « Données & méthode » (UI-SPEC « /datasets », D-40; phase 04.5 plan 11, B7; review of 2026-11-11, the
	// six proposals of the internal note 2026-11-11-page-donnees-methode). Under the title, one
	// sentence that says what the page holds and three links of one style; then three panels:
	//   1. « Les sources » — the search (a GET form) at the head, chips that filter by group (a chip at 0 is
	//      hidden unless it is active), then the datasets group by group in the reading order of the address
	//      fiche (`catalogue-groupes.ts`), one DatasetCard per served entry; the source read (`?jeu=`) is
	//      detailed in place, under its row, at every width (UI-029, UI-030, lot 8 of 4.9);
	//   2. « Lire une fiche » — the one glossary of the page: the scales and the states of the fiche
	//      (LireUneFiche), then « Quand une valeur manque », the four states of etats-libelles.ts, their
	//      labels folded;
	//   3. « Couverture » — the departements served by `/v1/datasets`, and the link to the page that says
	//      the limits.
	// Everything this page says about a dataset — licence, restrictions, quarantine, coverage —
	// is read from `GET /v1/datasets`; the frontend holds no copy of the catalogue (LIC-01). No
	// territory is written here (D-34, D-35). An outage keeps the glossary, which does not depend on the
	// API, and shows its notice first, in the coverage panel.
	import { browser } from '$app/environment';
	import { afterNavigate, goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/stores'; // Project convention: stores, never the state module.
	import { onMount } from 'svelte';
	import { etatDuJeu } from '$lib/api/datasets';
	import DatasetCard from '$lib/components/DatasetCard.svelte';
	import LireUneFiche from '$lib/components/ds/LireUneFiche.svelte';
	import MetaPartage from '$lib/components/ds/MetaPartage.svelte';
	import PageSimple from '$lib/components/ds/PageSimple.svelte';
	import Panneau from '$lib/components/ds/Panneau.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import { DEPARTEMENTS } from '$lib/data/departements';
	import { GROUPES_CATALOGUE, groupeDuJeu, grouperJeux, type IdGroupeCatalogue } from '$lib/data/catalogue-groupes';
	import { LIBELLES_ETATS, LIBELLES_MOTIFS, phraseEtat } from '$lib/data/etats-libelles';
	import type { OutOfCoverageReason } from '$lib/types/fiche';
	import { developperHorsCitations } from '$lib/utils/sigles';
	import type { PageData } from './$types';
	import { _normaliserRechercheCatalogue } from './+page';

	let { data }: { data: PageData } = $props();
	let rechercheDisponible = $state(false);
	let champRecherche = $state<HTMLInputElement | null>(null);
	// The field shows `q` of the URL; typing filters at once and writes `q` into the URL behind it.
	let saisie = $state($page.url.searchParams.get('q') ?? '');
	// A typing session: its first key creates a history entry, the next ones replace it.
	let saisieEnCours = false;
	// The server paints the field before the page is hydrated, and hydration writes `saisie` over it: what
	// the reader typed in between (a slow phone) is read here, first. No field on a client navigation.
	const saisieAvantHydratation = browser
		? document.querySelector<HTMLInputElement>('#recherche-catalogue')?.value
		: undefined;

	// Back, Forward, a link or a form followed by the browser: the field takes `q` of the new URL. A
	// navigation of this page (`goto`) carries the field as it is.
	afterNavigate((navigation) => {
		if (navigation.type === 'goto') return;
		saisieEnCours = false;
		// Once hydrated, not before: another list than the server's would not match its HTML.
		if (navigation.type === 'enter' && saisieAvantHydratation !== undefined && saisieAvantHydratation !== saisie) {
			saisir(saisieAvantHydratation);
			return;
		}
		saisie = $page.url.searchParams.get('q') ?? '';
		// The detail opens in place: closing the one above lifts the chosen row, which must stay on screen.
		if (navigation.type === 'link' && jeuChoisi) {
			document.getElementById(jeuChoisi.nom)?.scrollIntoView({ block: 'nearest' });
		}
	});

	onMount(() => {
		rechercheDisponible = true;
		// An old link `/datasets#<key>` (D45-11) opens the detail of its source; the fragment is never
		// seen by the server. A replaced entry: the reader chose nothing. Only when the URL carries no
		// `jeu` at all: an unknown `jeu` is not an absent one, and the fragment is then the row target.
		let cible = '';
		try {
			cible = decodeURIComponent(location.hash.slice(1));
		} catch {
			return;
		}
		if (!$page.url.searchParams.has('jeu') && data.jeux.some((jeu) => jeu.nom === cible)) {
			void goto(lienJeu(cible), { replaceState: true, keepFocus: true });
		}
	});

	function nomDepartement(code: string): string {
		const nom = DEPARTEMENTS.find((d) => d.code === code)?.nom;
		return nom ? `${nom} (${code})` : `Département ${code}`;
	}

	const nombreFr = new Intl.NumberFormat('fr-FR');
	/** French agreement: 0 and 1 take the singular. */
	function compteSources(n: number): string {
		return `${nombreFr.format(n)} ${n <= 1 ? 'source' : 'sources'}`;
	}

	// Under the title: what the page holds. The number is the one the API served; none in an outage.
	const CE_QUE_DIT_CHAQUE_SOURCE = 'son producteur, sa licence, sa date et ses limites.';
	const chapo = $derived.by(() => {
		if (data.panne || data.jeux.length === 0) return `Des sources publiques. Pour chacune : ${CE_QUE_DIT_CHAQUE_SOURCE}`;
		if (data.jeux.length === 1) return `1 source publique. Son producteur, sa licence, sa date et ses limites.`;
		return `${nombreFr.format(data.jeux.length)} sources publiques. Pour chacune : ${CE_QUE_DIT_CHAQUE_SOURCE}`;
	});

	// -- Glossary (same source as the fiche: etats-libelles.ts) ---------------------------------
	const SOUS_ETATS_MANQUE = [
		'not_reported',
		'insufficient_sample',
		'suppressed',
		'not_available',
		'zero_denominator',
		'not_applicable',
		'not_qualified'
	] as const;
	const MOTIFS = Object.keys(LIBELLES_MOTIFS) as OutOfCoverageReason[];
	// « Non qualifié » is worded in `warn`, like the badge of a quarantined dataset (UI-SPEC).
	const manques = SOUS_ETATS_MANQUE.map((etat) => ({
		libelle: LIBELLES_ETATS[etat],
		phrase: phraseEtat(etat, '', null),
		warn: etat === 'not_qualified'
	}));
	const horsCouverture = MOTIFS.map((motif) => LIBELLES_MOTIFS[motif]);
	const panne = {
		libelle: LIBELLES_ETATS.temporary_error,
		phrase: phraseEtat('temporary_error', '', null),
		warn: true
	};
	// One row per state: its lead, then its labels, folded (review of 2026-11-11). « Zéro » has no label.
	const ETATS: Array<{ nom: string; phrase: string; libelles: Array<{ libelle: string; phrase: string | null; warn: boolean }> }> = [
		{ nom: 'Zéro', phrase: 'La source recense zéro : « 0 » s’affiche avec son unité, comme toute valeur.', libelles: [] },
		{ nom: 'Manque', phrase: 'La valeur n’est pas publiée. Le libellé affiché à sa place dit pourquoi.', libelles: manques },
		{
			nom: 'Hors couverture',
			phrase: 'Le territoire n’est pas couvert ; le motif est affiché. Une absence de couverture ne vaut pas zéro.',
			libelles: horsCouverture.map((entree) => ({ ...entree, warn: true }))
		},
		{ nom: 'Panne', phrase: 'Rien n’est affiché plutôt qu’une valeur incomplète.', libelles: [panne] }
	];

	// -- Catalogue chips ------------------------------------------------------------------------
	// Review of 2026-11-11: the chips filter by group. The former state chips (« Disponibles », « Hors
	// couverture », « Non qualifiés ») left: a link that still carries one of them reads as « Tout ».
	type Filtre = 'tous' | IdGroupeCatalogue;
	const FILTRES: Array<{ id: Filtre; libelle: string }> = [
		{ id: 'tous', libelle: 'Tout' },
		...GROUPES_CATALOGUE.map(({ id, titre }) => ({ id, libelle: titre }))
	];

	// `filtre` is read in the URL: a closed list, an unknown value is « Tout » and never written back.
	const filtre = $derived.by((): Filtre => {
		const valeur = $page.url.searchParams.get('filtre');
		return FILTRES.find(({ id }) => id === valeur)?.id ?? 'tous';
	});
	const rechercheNormalisee = $derived(_normaliserRechercheCatalogue(saisie));
	const jeuxRecherches = $derived(
		rechercheNormalisee
			? data.jeux.filter((jeu) =>
					data.indexRecherche[jeu.nom]?.includes(rechercheNormalisee)
				)
			: data.jeux
	);
	const comptes = $derived(
		Object.fromEntries(
			FILTRES.map(({ id }) => [
				id,
				id === 'tous'
					? jeuxRecherches.length
					: jeuxRecherches.filter((jeu) => groupeDuJeu(jeu.nom) === id).length
			])
		) as Record<Filtre, number>
	);
	// A chip at 0 leads nowhere and is hidden, on the server too. « Tous » stays, the way back from a search
	// that finds nothing, and so does the active chip: it says what empties the list.
	const filtresVisibles = $derived(FILTRES.filter(({ id }) => id === 'tous' || id === filtre || comptes[id] > 0));
	const affiches = $derived(
		filtre === 'tous' ? jeuxRecherches : jeuxRecherches.filter((jeu) => groupeDuJeu(jeu.nom) === filtre)
	);
	const groupesAffiches = $derived(grouperJeux(affiches));
	const libelleFiltre = $derived(FILTRES.find((f) => f.id === filtre)!.libelle);

	// -- Source read (B7, UI-030) ------------------------------------------------------------------
	// `jeu` is read in the URL by the page, never by the loader (changing it reads nothing from the
	// API), and checked against the served keys: an unknown key renders no detail and is never written
	// into the page.
	const jeuDemande = $derived.by(() => {
		const cle = $page.url.searchParams.get('jeu');
		return cle !== null && data.jeux.some((jeu) => jeu.nom === cle) ? cle : null;
	});
	// No source is chosen arbitrarily (UI-017): the detail is that of `jeu` when it is listed, or that of
	// the only dataset the search and the filter leave. An unknown key is not an absent one: it opens
	// nothing (review M1), even when a single dataset remains.
	const cleInconnue = $derived($page.url.searchParams.has('jeu') && jeuDemande === null);
	const jeuChoisi = $derived(
		affiches.find((jeu) => jeu.nom === jeuDemande) ??
			(cleInconnue || affiches.length !== 1 ? null : affiches[0])
	);

	/** URL of the catalogue for these parameters (the current ones by default; « Tout » is not written). */
	function adresse({
		q = $page.url.searchParams.get('q') ?? '',
		f = filtre,
		cle = jeuDemande
	}: { q?: string; f?: Filtre; cle?: string | null } = {}): string {
		const parametres = new URLSearchParams();
		if (q) parametres.set('q', q);
		if (f !== 'tous') parametres.set('filtre', f);
		if (cle) parametres.set('jeu', cle);
		const recherche = parametres.toString();
		return recherche ? `/datasets?${recherche}` : '/datasets';
	}

	/** Link to a source: the parameters of the current URL, `jeu` changed only, its row targeted. */
	function lienJeu(cle: string): string {
		return `${adresse({ cle })}#${encodeURIComponent(cle)}`;
	}

	// UI-030, Back: a chip, a submitted search, « Effacer » and the first key of a typing session each
	// create a history entry; the page reads nothing from the API on any of them (the loader does not
	// read the URL). Focus stays where the reader is.
	function naviguer(cible: string, remplacer = false) {
		return goto(cible, { keepFocus: true, noScroll: true, replaceState: remplacer });
	}

	function saisir(valeur: string) {
		saisie = valeur;
		void naviguer(adresse({ q: saisie }), saisieEnCours);
		saisieEnCours = true;
	}

	function soumettre(event: SubmitEvent) {
		event.preventDefault();
		saisieEnCours = false;
		void naviguer(adresse({ q: saisie }));
	}

	function choisirFiltre(event: MouseEvent, id: Filtre) {
		event.preventDefault();
		saisieEnCours = false;
		void naviguer(adresse({ q: saisie, f: id }));
	}

	let boutonTous = $state<HTMLButtonElement | null>(null);

	function retourATous(event: MouseEvent) {
		choisirFiltre(event, 'tous');
		boutonTous?.focus();
	}

	function effacerRecherche() {
		saisie = '';
		saisieEnCours = false;
		void naviguer(adresse({ q: '' }));
		champRecherche?.focus();
	}

	// -- Acronyms (04.8-08, D-17, D-31) -----------------------------------------------------------
	// Spelled out at their first occurrence in the prose of the main, in reading order: the served
	// sentences of the source read (quarantine reason, limits, reuse conditions, restrictions), then
	// the reminder under the scales of « Lire une fiche ». Never the name,
	// producer, badge, licence, period or coverage of a dataset, nor a URL. A quotation « … » (a
	// mention the producer imposes word for word) is kept as served: only the text around it is passed.
	// What « Portée des objets » said and the scales did not (review of 2026-11-11).
	const RAPPEL_ECHELLES =
		'Une valeur ne se transpose pas d’une échelle à une autre : ce qui vaut pour une commune ne vaut pas pour une adresse. Un DPE est un document, pas un logement : un même logement peut en avoir connu plusieurs.';
	const textes = $derived.by(() => {
		const jeu = jeuChoisi;
		const cle = jeu?.nom ?? '';
		const etatChoisi = jeu ? etatDuJeu(jeu) : null;
		return developperHorsCitations([
			[`${cle}:motif`, etatChoisi?.kind === 'non_qualifie' ? etatChoisi.motif : null],
			[`${cle}:chapeau`, jeu?.limites_source?.chapeau],
			...(jeu?.limites_source?.puces ?? []).map((p, i) => [`${cle}:puce:${i}`, p] as const),
			[`${cle}:conditions`, jeu?.reutilisation?.conditions],
			...(jeu?.reutilisation?.restrictions ?? []).map((r, i) => [`${cle}:restriction:${i}`, r] as const),
			['rappel', RAPPEL_ECHELLES]
		]);
	});

	// -- Outage ---------------------------------------------------------------------------------
	const nombre = $derived(data.departementsHeberges.length);
	let relance = $state(false);

	async function recharger(event: SubmitEvent) {
		event.preventDefault();
		if (relance) return;
		relance = true;
		try {
			await invalidateAll();
		} finally {
			relance = false;
		}
	}
</script>

<MetaPartage titre="Données & méthode — DataCommune" description="Ce que couvre ce site, le sens des états affichés, la portée des objets, et pour chaque jeu de données sa licence, sa période, sa cadence et ses limites." chemin="/datasets" />

<!-- Declared outside PageSimple: inside its tag, a snippet would be handed to it as a prop. -->
{#snippet panneauCouverture()}
	<Panneau titre="Couverture" titreId="couverture-titre">
		{#if data.panne}
			<!-- A 4xx is typed by detailPanne (title and body); only a 5xx or a network failure is an outage (G2). -->
			<Notice
				titre={data.panne?.titre ?? 'La liste des jeux de données est momentanément indisponible'}
				variante="warn"
				role="alert"
			>
				<p>
					{data.panne?.message ??
						"Le service n'a pas répondu. Aucun jeu n'est affiché plutôt qu'une liste incomplète."}
				</p>
				<form action="/datasets" method="get" onsubmit={recharger}>
					<button type="submit" class="methode-button" aria-busy={relance ? 'true' : undefined}
						>Recharger la page</button
					>
				</form>
			</Notice>
		{:else if nombre === 0}
			<p>Ce site n'héberge encore aucun département.</p>
		{:else}
			<p>
				{nombre === 1
					? 'Ce site sert les données d’un département.'
					: `Ce site sert les données de ${nombreFr.format(nombre)} départements.`}
			</p>
			{#snippet listeDepartements()}
				<ul class="methode-departements">
					{#each data.departementsHeberges as code (code)}
						<li>{nomDepartement(code)}</li>
					{/each}
				</ul>
			{/snippet}
			<details class="methode-repli">
				<summary>Voir la liste des départements</summary>
				{@render listeDepartements()}
			</details>
			<!-- Paper does not open a fold: the list is written out for it. -->
			<div class="methode-impression">{@render listeDepartements()}</div>
		{/if}
		<!-- What each dataset covers and what the site does not: said once, on its own page. -->
		<p class="methode-suite">
			<a href="/couverture">Couverture et limites</a><span class="methode-suite-fleche" aria-hidden="true">→</span>
		</p>
	</Panneau>
{/snippet}

<PageSimple titre="Données & méthode" ariane="Données & méthode" sousTitre={chapo}>
	<nav class="methode-sommaire" aria-label="Accès directs aux données et à la méthode">
		<ul>
			{#if !data.panne}
				<li><a class="methode-entree" href="#catalogue-titre">Les sources</a></li>
			{/if}
			<li><a class="methode-entree" href="#lire-titre">Lire une fiche</a></li>
			<li><a class="methode-entree" href="#couverture-titre">Couverture</a></li>
		</ul>
	</nav>

	{#if !data.panne}
		<Panneau titre="Les sources" titreId="catalogue-titre">
			<!-- A GET form (UI-030): without JavaScript the server renders `q`, `filtre` and `jeu`; its first
			     submit button (Enter in the field) keeps the current filter, a chip sends its own. With
			     JavaScript the same parameters are written into the URL by the page, typing filters at once
			     and that button is hidden. -->
			<form
				id="catalogue-recherche"
				class="methode-formulaire"
				role="search"
				method="get"
				action="/datasets"
				onsubmit={soumettre}
			>
				<div class="methode-recherche">
					<label for="recherche-catalogue">Rechercher une source ou un producteur</label>
					<div class="methode-recherche-ligne">
						<input
							id="recherche-catalogue"
							bind:this={champRecherche}
							name="q"
							value={saisie}
							oninput={(event) => saisir(event.currentTarget.value)}
							onblur={() => (saisieEnCours = false)}
							type="search"
							autocomplete="off"
							placeholder="Ex. DVF, INSEE, Géorisques"
						/>
						<button
							type="submit"
							class="methode-button"
							hidden={rechercheDisponible}
							name={filtre === 'tous' ? undefined : 'filtre'}
							value={filtre === 'tous' ? undefined : filtre}>Rechercher</button
						>
						{#if rechercheDisponible && saisie}
							<button type="button" class="methode-button" onclick={effacerRecherche}
								>Effacer la recherche</button
							>
						{/if}
					</div>
				</div>
				<div class="methode-filtres" role="group" aria-label="Filtrer les sources par thème">
					{#each filtresVisibles as f (f.id)}
						{#if f.id === 'tous'}
							<button
								type="submit"
								name="filtre"
								value={f.id}
								class="methode-chip"
								aria-pressed={filtre === f.id}
								bind:this={boutonTous}
								onclick={(event) => choisirFiltre(event, f.id)}>{f.libelle} · {nombreFr.format(comptes[f.id])}</button
							>
						{:else}
							<button
								type="submit"
								name="filtre"
								value={f.id}
								class="methode-chip"
								aria-pressed={filtre === f.id}
								onclick={(event) => choisirFiltre(event, f.id)}
								><span class="methode-pastille pastille-{f.id}" aria-hidden="true"></span>{f.libelle} · {nombreFr.format(
									comptes[f.id]
								)}</button
							>
						{/if}
					{/each}
				</div>
				{#if jeuDemande}<input type="hidden" name="jeu" value={jeuDemande} />{/if}
			</form>
			<div class="catalogue-liste">
				<!-- Said to a screen reader when the list changes; the chips and the group counts show it. -->
				<p class="methode-annonce" role="status">
					{libelleFiltre} : {compteSources(affiches.length)}
				</p>
				{#if affiches.length === 0}
					<div class="methode-vide">
						<p>{rechercheNormalisee ? 'Aucune source ne correspond à cette recherche.' : 'Aucune source dans ce thème.'}</p>
						{#if !rechercheNormalisee}
							<button
								type="submit"
								form="catalogue-recherche"
								name="filtre"
								value="tous"
								class="methode-button"
								onclick={retourATous}>Afficher toutes les sources</button
							>
						{/if}
					</div>
				{:else}
					{#if !jeuChoisi}
						<p class="catalogue-invite">
							Choisissez une source pour lire sa licence, ses conditions de réutilisation, sa période, sa
							dernière importation et ses limites.
						</p>
					{/if}
					<!-- One mount per dataset: the detail of the source read follows its row, at every width. -->
					{#each groupesAffiches as { groupe, jeux } (groupe.id)}
						<div class="catalogue-groupe">
							<div class="catalogue-groupe-tete">
								<h3 class="catalogue-groupe-titre">
									<span class="methode-pastille pastille-{groupe.id}" aria-hidden="true"></span>{groupe.titre}
								</h3>
								<p class="catalogue-groupe-compte">{compteSources(jeux.length)}</p>
							</div>
							<ul class="methode-cards">
								{#each jeux as jeu (jeu.nom)}
									<li>
										<DatasetCard
											{jeu}
											departementsInstance={data.departementsHeberges}
											href={lienJeu(jeu.nom)}
											choisi={jeu.nom === jeuChoisi?.nom}
											niveau={4}
											{textes}
										/>
									</li>
								{/each}
							</ul>
						</div>
					{/each}
				{/if}
			</div>
		</Panneau>
	{/if}

	<!-- The notice of an outage is read first; otherwise the coverage closes the page. -->
	{#if data.panne}{@render panneauCouverture()}{/if}

	<LireUneFiche titreId="lire-titre">
		{#snippet apresEchelles()}
			<p class="methode-rappel">{textes.get('rappel') ?? RAPPEL_ECHELLES}</p>
		{/snippet}
		<div class="methode-manques">
			<!-- `etats-titre`: the target of the former entry « Comprendre les chiffres », kept for the links that carry it. -->
			<h3 id="etats-titre" class="methode-manques-titre">Quand une valeur manque</h3>
			<p class="methode-manques-chapo">
				Une valeur absente n’est jamais affichée comme un zéro. Quatre cas, toujours distingués :
			</p>
			<!-- A table to the eye, one row per state, kept in headings and definition lists: a heading is not
			     allowed in a `th`, and « Zéro » has no label of its own. -->
			<div class="methode-etats">
				{#each ETATS as etat (etat.nom)}
					{#snippet libelles()}
						<dl class="methode-glossaire">
							{#each etat.libelles as entree (entree.libelle)}
								<div>
									<dt><span class="methode-badge" class:methode-badge-warn={entree.warn}>{entree.libelle}</span></dt>
									<dd>{entree.phrase}</dd>
								</div>
							{/each}
						</dl>
					{/snippet}
					<div class="methode-etat">
						<h4 class="methode-h3">{etat.nom}</h4>
						<div class="methode-etat-corps">
							<p>{etat.phrase}</p>
							{#if etat.libelles.length > 0}
								<details class="methode-repli">
									<summary>{etat.libelles.length === 1 ? 'Voir le libellé' : `Voir les ${etat.libelles.length} libellés`}</summary>
									{@render libelles()}
								</details>
								<!-- Paper does not open a fold: the labels are written out for it. -->
								<div class="methode-impression">{@render libelles()}</div>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		</div>
	</LireUneFiche>

	{#if !data.panne}{@render panneauCouverture()}{/if}
</PageSimple>

<style>
	.methode-sommaire {
		margin-top: var(--s3);
	}

	.methode-sommaire ul {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.methode-sommaire ul {
		align-items: center;
	}

	/* The three entries: one style, none set apart (lot 8 of 4.9); links on one row since 2026-11-11, at
	   375 px too, where three stacked buttons pushed the first source out of the first screen. */
	.methode-sommaire a.methode-entree {
		display: inline-flex;
		align-items: center;
		box-sizing: border-box;
		min-height: 44px;
		color: var(--color-accent);
		font-weight: 600;
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 4px;
	}

	.methode-sommaire a.methode-entree:hover {
		color: var(--color-accent-hover);
	}

	.methode-h3 {
		margin: 0 0 var(--s2);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.5;
	}

	.methode-repli {
		margin: 0 0 var(--s3);
	}

	.methode-repli > summary {
		display: list-item;
		box-sizing: border-box;
		min-height: 44px;
		padding-block: var(--s2);
		color: var(--color-accent);
		font-weight: 600;
		cursor: pointer;
	}

	.methode-impression {
		display: none;
	}

	.methode-departements {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
		margin: var(--s2) 0 var(--s4);
		padding: 0;
		list-style: none;
	}

	.methode-departements li {
		padding: var(--s1) var(--s2);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-control);
		background: var(--color-paper);
	}

	/* A compact table (lot 8 of 4.9): the state, then its lead and its labels, each with its sentence. */
	.methode-etats {
		margin-top: var(--s4);
		border-top: 1px solid var(--color-line);
	}

	.methode-etat {
		display: grid;
		grid-template-columns: 9.5rem minmax(0, 1fr);
		column-gap: var(--s4);
		/* The name of the state keeps its height when the labels beside it unfold (forme-publique.spec.ts). */
		align-items: start;
		padding: var(--s3) 0;
		border-bottom: 1px solid var(--color-line);
	}

	.methode-etat .methode-h3 {
		margin: 0;
	}

	.methode-etat p {
		max-width: var(--mesure);
		margin: 0;
	}

	.methode-glossaire {
		display: flex;
		flex-direction: column;
		margin: 0;
	}

	.methode-glossaire > div {
		display: grid;
		grid-template-columns: minmax(0, 19.5rem) minmax(0, 1fr);
		column-gap: var(--s3);
		align-items: baseline;
		padding: var(--s2) 0;
		border-top: 1px solid var(--color-filet-pale);
	}

	.methode-glossaire dd {
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
	}

	/* The four states, under the two lists of the legend and in the same panel. */
	.methode-manques {
		margin-top: var(--s5);
	}

	.methode-manques-titre {
		margin: 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 20px;
		font-weight: 500;
		line-height: 1.25;
		scroll-margin-top: 96px;
	}

	.methode-manques-chapo {
		max-width: var(--mesure);
		margin: var(--s1) 0 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.methode-manques .methode-etats {
		margin-top: var(--s3);
	}

	.methode-etat .methode-repli {
		margin: 0;
	}

	.methode-rappel {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.methode-suite {
		display: flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		margin-bottom: 0;
		font-weight: 600;
	}

	.methode-suite a {
		color: var(--color-accent);
		text-underline-offset: 4px;
	}

	.methode-suite-fleche {
		color: var(--color-accent);
	}

	.methode-badge {
		display: inline-block;
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s1) var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.methode-badge-warn {
		border-color: var(--color-warn);
		background: var(--color-warn-bg);
		color: var(--color-warn);
	}

	.methode-filtres {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	.methode-recherche {
		max-width: 720px;
		margin-bottom: var(--s4);
	}

	.methode-recherche label {
		display: block;
		margin-bottom: var(--s1);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.methode-recherche-ligne {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s2);
	}

	.methode-recherche input {
		box-sizing: border-box;
		flex: 1 1 320px;
		min-width: 0;
		min-height: 44px;
		padding: 0 var(--s3);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-size: inherit;
		font-weight: inherit;
		font-style: inherit;
		line-height: inherit;
	}

	.methode-recherche input::placeholder {
		color: var(--color-muted);
	}

	.methode-chip {
		display: inline-flex;
		/* Never wider than its row: with a wider font, the longest title wraps inside its chip (D-04). */
		box-sizing: border-box;
		max-width: 100%;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		padding: 0 var(--s3);
		text-align: left;
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

	.methode-chip:hover {
		background: var(--color-soft);
	}

	.methode-chip[aria-pressed='true'] {
		border-color: var(--color-accent);
		background: var(--color-soft);
		font-weight: 600;
	}

	/* The dot of a group, in the tint of its part of the fiche (app.css); the territory stays neutral. */
	.methode-pastille {
		flex: none;
		width: 10px;
		height: 10px;
		border-radius: 50%;
		background: var(--color-neutre);
	}

	.pastille-prix {
		background: var(--color-prix-encre);
	}

	.pastille-logement {
		background: var(--color-logement-encre);
	}

	.pastille-quotidien {
		background: var(--color-quotidien-encre);
	}

	.pastille-risques {
		background: var(--color-risques-encre);
	}

	/* The clipped status is placed against this box: left in the flow of the page, it would widen it. */
	.catalogue-liste {
		position: relative;
	}

	.methode-annonce {
		position: absolute;
		width: 1px;
		height: 1px;
		margin: 0;
		padding: 0;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
		border: 0;
	}

	.catalogue-groupe {
		margin-top: var(--s5);
	}

	.catalogue-groupe-tete {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s3);
		padding-bottom: var(--s1);
		border-bottom: 1px solid var(--color-line);
	}

	.catalogue-groupe-titre {
		display: flex;
		align-items: baseline;
		gap: var(--s2);
		margin: 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 22px;
		font-weight: 500;
		line-height: 1.25;
	}

	.catalogue-groupe-compte {
		flex: none;
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.methode-vide {
		padding: var(--s5);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-paper);
	}

	.methode-cards {
		display: flex;
		flex-direction: column;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Rows of a list: a pale line between two rows, never after the last (UI-SPEC v4 § 3.0). */
	.methode-cards > li + li {
		border-top: 1px solid var(--color-filet-pale);
	}

	.catalogue-invite {
		margin: var(--s4) 0 0;
		color: var(--color-muted);
	}

	/* Kept for the form without JavaScript; with it, typing filters and the attribute hides the button. */
	.methode-button[hidden] {
		display: none;
	}

	.methode-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
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

	.methode-button:hover {
		background: var(--color-soft);
	}

	@media (max-width: 720px) {
		.methode-etat,
		.methode-glossaire > div {
			grid-template-columns: minmax(0, 1fr);
			row-gap: var(--s1);
		}

		.methode-recherche .methode-button {
			flex: 1 1 100%;
		}

		/* The chips wrap, as everywhere on the site: no sideways scroll outside the comparator (UI-01). Tighter
		   and at the small size, two of them hold on a row of a phone. */
		.methode-chip {
			gap: var(--s-demi);
			padding: 0 var(--s2);
			font-size: 14px;
		}
	}
	@media screen {
		.methode-recherche input { font-size: 16px; min-height: 48px; }
		.methode-recherche .methode-button { min-height: 48px; }
	}

	@media print {
		.methode-repli {
			display: none;
		}

		.methode-impression {
			display: block;
		}
	}
</style>
