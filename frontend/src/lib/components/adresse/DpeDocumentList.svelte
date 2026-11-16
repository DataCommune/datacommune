<script lang="ts">
	// Section « Documents DPE déclarés à cette adresse » (UI-SPEC « /adresse/[id] » point 4, D-109,
	// D-111, LIEU-02, LIEU-03). It renders the served list as served: the head sentence of the
	// contract (zero, one, many, tie, undetermined), ONE common line with ONE `i` button for the
	// section (VN12), the served signal of several dwellings, the served A→G distribution when N ≥ 2,
	// then an ordered list of cards in the served order (date descending). A card says the classes,
	// the type and surface, the dates and the served badges; its source number is a link to
	// `/dpe/{numero}`. Nothing is picked, compared or recomputed here: the most recent document, a tie
	// and the badges are the API's (D-111).
	// C39 (amends VN11): from two documents at the address, each card also carries a checkbox
	// « Sélectionner le DPE n° … » (with JavaScript only: it does nothing without); from two checked,
	// « Comparer les documents (n) » opens the document comparator with exactly these documents, four
	// at most (the comparator's ceiling: the other boxes are disabled and a message says why). A
	// document selection already kept by the tab is never replaced without a message (WR-03 of the
	// D-15 review of the comparators). The checked documents stay checked across the pages.
	// « Déclarés » means the address typed by the diagnostician; no document is attached to a
	// dwelling. Line 4 of a card is the served declared address (P-ADR-01): the text as typed when
	// the API publishes it, else the short line « Adresse déclarée non affichée », its served reason
	// being read in the panel of the section (2026-10-05); never built here. The D-110
	// notice is rendered from the served `constat_d110` only: no criterion is evaluated here.
	//
	// Pagination: links `?page={n}&g={generation}` that work without JavaScript (the loader reads
	// them and goes back to page 1 on a stale generation). With JavaScript the link navigates in
	// place: the cards stay with `aria-busy` until the answer, then the new range is announced and the
	// focus goes to the h2. A failure of the list is a state of this section only. A page change and
	// « Recharger les documents DPE » read the list only, never the identity nor Géorisques; the
	// links of the cards and of the pagination are preloaded on tap, not on hover (FRONT WR-03 of the
	// D-15 review). The pagination is the shared `Pagination` (04-05); its links keep the parameters
	// of the other lists of the page (`autres`: sales, similar sales), after `page` and `g`.
	import { onMount, tick } from 'svelte';
	import { goto, invalidate } from '$app/navigation';
	import { MAX_COMPARAISON, documentComparisonSelection } from '$lib/stores/comparison-selection.svelte';
	import { lienSelectionNature } from '$lib/data/commune-comparison-selection';
	import { DEPENDANCE_DOCUMENTS, type DocumentDpeCarte, type IssueDocumentsDpe } from '$lib/api/adresses';
	import DpeTag from '$lib/components/fiche/DpeTag.svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import Pagination from './Pagination.svelte';
	import SourceButton from '$lib/components/fiche/SourceButton.svelte';
	import SourcePanel from '$lib/components/fiche/SourcePanel.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import {
		CLASSES_REPARTITION,
		CONSIGNE_COMPARAISON_DOCUMENTS,
		LIBELLE_REPARTITION,
		MESSAGE_PANNE_DOCUMENTS,
		PHRASE_SIGNAL_PLUSIEURS_LOGEMENTS,
		NON_RENSEIGNE as NON_RENSEIGNE_PHRASE,
		TITRE_CONSTAT_D110,
		TITRE_DOCUMENTS,
		TITRE_GROUPE_ADRESSE_BRUTE,
		badgeProvisoire,
		badgeRattachement,
		criteresConstat,
		estClasse,
		libelleBadge,
		libelleCompteurSelection,
		libelleRattachement,
		libelleSurface,
		libelleType,
		ligneAdresseDeclaree,
		ligneDates,
		ligneCommuneDocuments,
		limiteGroupeAdresseBrute,
		phraseEnTeteDpe,
		phraseGroupeAdresseBrute,
		plageDocuments,
		voletAdresseBrute,
		voletConstat,
		voletDocuments
	} from '$lib/data/adresse-fiche';
	import { LIBELLES_ETATS } from '$lib/data/etats-libelles';

	interface Props {
		issue: IssueDocumentsDpe;
		banId: string;
		/** Other parameters of the page, kept by the page links (serialized, without `page` and `g`). */
		autres?: string;
	}

	let { issue, banId, autres = '' }: Props = $props();

	const titreId = $props.id();
	let panneau = $state<ReturnType<typeof SourcePanel>>();
	let panneauConstat = $state<ReturnType<typeof SourcePanel>>();
	let titre = $state<HTMLHeadingElement>();
	let enCours = $state(false);
	let annonce = $state('');

	const NON_RENSEIGNE = LIBELLES_ETATS.not_reported;
	// A missing type or surface names its field: « Immeuble · Non renseigné » left it to be guessed (UI-009).
	const TYPE_NON_RENSEIGNE = 'Type non renseigné';
	const SURFACE_NON_RENSEIGNEE = 'surface non renseignée';
	const servie = $derived(issue.kind === 'ok' && issue.documents.etat === 'value' ? issue.documents : null);
	// A list without a readable count is an outage of the section, never « 0 » (IN-01, SEM-01).
	const liste = $derived(servie && typeof servie.total === 'number' ? servie : null);
	const horsCouverture = $derived(issue.kind === 'ok' && issue.documents.etat === 'out_of_coverage' ? issue.documents : null);
	const panne = $derived(
		issue.kind === 'panne' ? issue.panne : servie && !liste ? { statut: null, titre: null, message: null } : null
	);
	const volet = $derived(liste ? voletDocuments(liste) : []);
	const total = $derived(liste?.total ?? 0);
	const plage = $derived(liste ? plageDocuments(liste) : null);
	const pages = $derived(liste?.pages ?? 1);
	const signal = $derived(liste?.signal_plusieurs_logements ?? null);
	const constat = $derived(liste?.constat_d110 ?? null);
	// C2: the raw-address group, rendered apart; nothing of it is read by the derivations above.
	let panneauBrute = $state<ReturnType<typeof SourcePanel>>();
	const brute = $derived(liste?.adresseBrute ?? null);
	const limiteBrute = $derived(brute ? limiteGroupeAdresseBrute(brute) : null);
	const voletBrute = $derived(liste && brute ? voletAdresseBrute(liste) : []);
	const LIBELLE_SOURCE_BRUTE = TITRE_GROUPE_ADRESSE_BRUTE.charAt(0).toLowerCase() + TITRE_GROUPE_ADRESSE_BRUTE.slice(1);

	const lienDocument = (numero: string) => `/dpe/${encodeURIComponent(numero)}`;

	// --- C39: documents checked to be compared (provisional texts, 04-DECISIONS-TEXTES row 71) ------
	const NB = '\u00a0';
	let hydrate = $state(false);
	onMount(() => {
		hydrate = true;
	});
	let coches = $state<string[]>([]);
	const comparable = $derived(hydrate && total >= 2);
	const pleine = $derived(coches.length >= MAX_COMPARAISON);
	const lienComparerDocuments = $derived(lienSelectionNature('dpe', coches, false));
	// The documents the tab already keeps for the comparator, when they are not exactly these.
	const selectionOnglet = $derived(documentComparisonSelection.codes);
	const remplacee = $derived(
		coches.length >= 2 &&
			selectionOnglet.length > 0 &&
			(selectionOnglet.length !== coches.length || selectionOnglet.some((numero) => !coches.includes(numero)))
	);

	function basculer(numero: string, coche: boolean) {
		coches = coche ? (coches.includes(numero) || pleine ? coches : [...coches, numero]) : coches.filter((n) => n !== numero);
	}

	function comparerDocuments(event: MouseEvent) {
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		documentComparisonSelection.replace(coches);
		void goto(lienComparerDocuments);
	}

	function lienPage(numero: number): string {
		const sp = new URLSearchParams({ page: String(numero) });
		if (liste?.generation) sp.set('g', liste.generation);
		return `?${sp.toString()}${autres ? `&${autres}` : ''}`;
	}

	/** With JavaScript: same URL as the link, in place; the cards stay until the answer. */
	async function changerDePage(event: MouseEvent, href: string) {
		if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
		event.preventDefault();
		if (enCours) return;
		enCours = true;
		annonce = '';
		try {
			await goto(href, { noScroll: true, keepFocus: true });
		} finally {
			enCours = false;
		}
		await tick();
		annonce = plage ?? '';
		titre?.focus();
	}

	// « Recharger les documents DPE »: one reload in flight at most, announced by aria-busy.
	async function recharger() {
		if (enCours) return;
		enCours = true;
		try {
			await invalidate(DEPENDANCE_DOCUMENTS);
		} finally {
			enCours = false;
		}
	}
</script>

<!-- One card of a list: classes, type and surface, dates and served badges, the number link, the
     comparison box (ADEME key only) and the declared address. `rattachement`: the attachment badge of
     a card of the raw-address group, said before its served badges; null for the ADEME key. -->
{#snippet carteDocument(doc: DocumentDpeCarte, rattachement: { texte: string; provisoire: boolean } | null, choix: boolean)}
	<li class="doccard">
		<p class="doccard-l1">
			{#if estClasse(doc.etiquette_dpe)}<DpeTag classe={doc.etiquette_dpe} />{:else}<span class="doccard-badge">{NON_RENSEIGNE}</span>{/if}
			<span>GES {doc.etiquette_ges ?? NON_RENSEIGNE_PHRASE}</span>
			<span aria-hidden="true">·</span>
			<span>{libelleType(doc.type_batiment) ?? TYPE_NON_RENSEIGNE} · {libelleSurface(doc.surface_habitable) ?? SURFACE_NON_RENSEIGNEE}</span>
		</p>
		<p class="doccard-l2">
			<span>{ligneDates(doc.date_etablissement, doc.date_fin_validite)}</span>
			{#if rattachement || doc.badges.length}
				<span class="doccard-badges">
					{#if rattachement}
						<span class="doccard-badge doccard-rattachement" data-provisoire={rattachement.provisoire ? 'true' : undefined}>{rattachement.texte}</span>
					{/if}
					{#each doc.badges as badge, index (index)}
						<span class="doccard-badge" data-badge={badge} data-provisoire={badgeProvisoire(badge) ? 'true' : undefined}>{libelleBadge(badge)}</span>
					{/each}
				</span>
			{/if}
		</p>
		<p class="doccard-l3">
			<a class="doccard-lien" href={lienDocument(doc.numero)}>DPE n°&nbsp;<span class="chiffre">{doc.numero}</span></a>
			{#if choix}
				<label class="doccard-choix">
					<input
						type="checkbox"
						checked={coches.includes(doc.numero)}
						disabled={pleine && !coches.includes(doc.numero)}
						onchange={(event) => basculer(doc.numero, event.currentTarget.checked)}
					/>
					<span>Sélectionner <span class="sr-only">le DPE n° {doc.numero}</span></span>
				</label>
			{/if}
		</p>
		{#if ligneAdresseDeclaree(doc.adresse_declaree)}
			<p class="doccard-l4" data-adresse-declaree={doc.adresse_declaree?.etat}>{ligneAdresseDeclaree(doc.adresse_declaree)}</p>
		{/if}
	</li>
{/snippet}

<section class="doclist" aria-labelledby={titreId} aria-busy={enCours} data-ban-id={banId}>
	<h3 id={titreId} class="doclist-h2" tabindex="-1" bind:this={titre}>{TITRE_DOCUMENTS}</h3>
	<p class="doclist-annonce sr-only" aria-live="polite">{annonce}</p>

	{#if liste}
		<p class="doclist-phrase">{phraseEnTeteDpe(liste)}</p>
		<p class="doclist-commune">
			<span>{ligneCommuneDocuments(liste)}</span>
			<SourceButton libelle="documents DPE déclarés à cette adresse" onouvrir={(declencheur) => panneau?.ouvrir(declencheur)} />
		</p>

		{#if signal?.etat === 'value'}
			<p class="doclist-signal">{PHRASE_SIGNAL_PLUSIEURS_LOGEMENTS}</p>
		{:else if signal}
			<!-- The registry is down: an outage of the signal, never an absence of signal (I-3). -->
			<div class="doclist-signal doclist-signal-panne">
				{#if signal.source}<span>{signal.source.nom}</span>{/if}
				<StateValue etat={{ kind: 'absence', etat: 'temporary_error', motif: '', effectif: null }} aide={false} />
			</div>
		{/if}

		{#if constat}
			<div class="doclist-constat" data-constat-d110>
				<!-- h3 under the h2 of the section (04-DECISIONS-TEXTES, 2026-10-05, row 9). -->
				<Notice titre={TITRE_CONSTAT_D110} niveau={3}>
					<p class="constat-criteres">
						{#each criteresConstat(constat) as critere, rang (rang)}{#if rang > 0}{' · '}{/if}<span data-provisoire={critere.provisoire ? 'true' : undefined}>{critere.texte}</span>{/each}
						<SourceButton libelle="constat de concordance" onouvrir={(declencheur) => panneauConstat?.ouvrir(declencheur)} />
					</p>
				</Notice>
				<SourcePanel bind:this={panneauConstat} titre={TITRE_CONSTAT_D110} entrees={voletConstat(constat)} brut={[]} />
			</div>
		{/if}

		{#if total >= 2 && liste.distribution}
			<ul class="doclist-distribution" aria-label={LIBELLE_REPARTITION}>
				{#each CLASSES_REPARTITION as classe (classe)}
					<li>
						<DpeTag {classe} />
						<span class="doclist-effectif" class:doclist-zero={liste.distribution[classe] === 0}>{liste.distribution[classe]}</span>
					</li>
				{/each}
				{#if liste.distribution.unknown > 0}
					<li>
						<span class="doclist-sans-classe">{NON_RENSEIGNE}</span>
						<span class="doclist-effectif">{liste.distribution.unknown}</span>
					</li>
				{/if}
			</ul>
		{/if}

		{#if liste.documents.length}
			{#if comparable}
				<!-- B3 (UI-008): the purpose of the boxes, said before the first one, and the count of the
				     boxes checked by the visitor (not a figure of the list). The counter is the status region
				     of the count from the first box (review 05); the compare link below is not live, so the
				     count is said once. -->
				<p class="doclist-consigne">
					<span>{CONSIGNE_COMPARAISON_DOCUMENTS}</span>
					<span class="doclist-compteur" role="status">{libelleCompteurSelection(coches.length)}</span>
				</p>
			{/if}
			<ol class="doclist-cards" data-sveltekit-preload-data="tap">
				<!-- Keyed by position: the served order is the order, and a repeated number served by mistake
				     must not break the page. -->
				{#each liste.documents as doc, rang (rang)}
					{@render carteDocument(doc, null, comparable)}
				{/each}
			</ol>
		{/if}

		{#if comparable}
			<!-- C39: the action, then its messages, announced politely as boxes are checked; the count of the
			     action is said by the counter above (review 05: never twice). -->
			<div class="doclist-comparer">
				{#if coches.length >= 2}
					<a class="doclist-secondaire" href={lienComparerDocuments} onclick={comparerDocuments} data-sveltekit-preload-data="tap"
						>Comparer les documents ({coches.length})</a
					>
				{/if}
				<div class="doclist-comparer-notes" aria-live="polite">
					{#if pleine}
						<p class="doclist-comparer-note">Quatre documents DPE au maximum{NB}: décochez-en un pour en choisir un autre.</p>
					{/if}
					{#if remplacee}
						<p class="doclist-comparer-note">La comparaison de documents en cours ({selectionOnglet.length} DPE) sera remplacée par ces documents.</p>
					{/if}
				</div>
			</div>
		{/if}

		<Pagination
			{plage}
			{pages}
			lienPrecedent={liste.page > 1 ? lienPage(liste.page - 1) : null}
			lienSuivant={liste.page < pages ? lienPage(liste.page + 1) : null}
			etiquette="Pages des documents DPE"
			prefixe="doclist"
			onchanger={changerDePage}
		/>

		{#if brute}
			<!-- C2 (04.1-13, D-127): the documents attached by their exact raw address, a group of their
			     own after the list of the ADEME key, outside its count, distribution, most recent document,
			     finding, selection and pagination. Its total is served; its cards are the most recent ones
			     served, the same on every page; its panel is its own. -->
			<div class="doclist-brute" data-groupe-dpe="adresse_brute">
				<h4 class="doclist-brute-titre">{TITRE_GROUPE_ADRESSE_BRUTE}</h4>
				<p class="doclist-brute-phrase">{phraseGroupeAdresseBrute(brute)}</p>
				<p class="doclist-brute-source">
					<span>{libelleRattachement('adresse_brute_exacte')}</span>
					<SourceButton libelle={LIBELLE_SOURCE_BRUTE} onouvrir={(declencheur) => panneauBrute?.ouvrir(declencheur)} />
				</p>
				{#if limiteBrute}<p class="doclist-brute-limite">{limiteBrute}</p>{/if}
				{#if brute.documents.length}
					<ol class="doclist-brute-cards" data-sveltekit-preload-data="tap">
						{#each brute.documents as doc, rang (rang)}
							{@render carteDocument(doc, badgeRattachement(doc.rattachement), false)}
						{/each}
					</ol>
				{/if}
				<SourcePanel bind:this={panneauBrute} titre={TITRE_GROUPE_ADRESSE_BRUTE} entrees={voletBrute} brut={[]} source={liste.source} />
			</div>
		{/if}

		<SourcePanel bind:this={panneau} titre={TITRE_DOCUMENTS} entrees={volet} brut={[]} source={liste.source} />
	{:else if horsCouverture}
		<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: horsCouverture.motif ?? '', effectif: null }} />
	{:else if panne}
		<div class="doclist-panne" role="alert">
			<!-- 429, 401, 403 and a 409 other than a stale generation are typed by panne.ts (never an outage). -->
			<p>{panne.message ?? MESSAGE_PANNE_DOCUMENTS}</p>
			<button type="button" class="doclist-secondaire" onclick={recharger} disabled={enCours} aria-busy={enCours}>Recharger les documents DPE</button>
		</div>
	{/if}
</section>

<style>
	.doclist {
		margin-top: var(--s6);
	}

	/* Title of the list: the h3 of a group (UI-SPEC v4 § 2.2, Newsreader 22 px, 20 px under 720 px, 500). */
	.doclist-h2 {
		margin: 0 0 var(--s2);
		font-family: var(--font-display);
		font-size: 22px;
		font-weight: 500;
		line-height: 1.25;
	}

	.doclist-h2:focus {
		outline: none;
	}

	.doclist-phrase {
		max-width: var(--mesure);
		margin: 0 0 var(--s1);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.doclist-commune {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0 var(--s2);
		margin: 0 0 var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.doclist-signal {
		max-width: var(--mesure);
		margin: 0 0 var(--s4);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.doclist-signal-panne {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		font-size: 14px;
	}

	.doclist-constat {
		margin: 0 0 var(--s4);
	}

	.constat-criteres {
		overflow-wrap: anywhere;
	}

	/* Seven tags A→G and their served counts on one line at 375px (UI-SPEC point 4): the tags are
	   drawn at the body size here, the zeros stay visible in muted (VN8). A plain row, without the band
	   of the former theme: the v4 grammar has no container for it (UI-SPEC v4 § 3.0). */
	.doclist-distribution {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		margin: 0 0 var(--s4);
		padding: 0;
		list-style: none;
	}

	.doclist-distribution li {
		display: inline-flex;
		align-items: center;
		gap: var(--s1);
	}

	.doclist-distribution :global(.dpe-tag) {
		min-width: 24px;
		height: 24px;
		padding: 0 var(--s1);
		font-size: 16px;
	}

	.doclist-effectif {
		font-size: 16px;
		font-variant-numeric: tabular-nums;
	}

	.doclist-zero {
		color: var(--color-muted);
	}

	.doclist-sans-classe {
		font-size: 14px;
		font-weight: 600;
	}

	/* B3: the instruction and its counter on one line when it fits, the counter in muted after it. */
	.doclist-consigne {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0 var(--s2);
		max-width: var(--mesure);
		margin: 0 0 var(--s2);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.doclist-compteur {
		color: var(--color-muted);
		font-size: 14px;
		font-variant-numeric: tabular-nums;
	}

	/* C2: the raw-address group, a block of its own under the list of the ADEME key. The pale rule is the
	   one separator between two blocks of a list (UI-SPEC v4 § 3.0). */
	.doclist-brute {
		margin-top: var(--s6);
		padding-top: var(--s4);
		border-top: 1px solid var(--color-filet-pale);
	}

	.doclist-brute-titre {
		margin: 0 0 var(--s2);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
	}

	.doclist-brute-phrase,
	.doclist-brute-limite {
		max-width: var(--mesure);
		margin: 0 0 var(--s1);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.doclist-brute-limite {
		margin: 0 0 var(--s4);
		color: var(--color-muted);
		font-size: 14px;
	}

	.doclist-brute-source {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0 var(--s2);
		margin: 0 0 var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.doclist-cards,
	.doclist-brute-cards {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* 12px vertical / 16px horizontal, sm between lines (UI-SPEC « Espacement », ≤ 160px at 375px). v4
	   form: a document is one datum on its own, so a tile (UI-SPEC v4 § 3.0): page background, line
	   outline, tile radius. */
	.doccard {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
		box-sizing: border-box;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-bg);
	}

	.doccard p {
		margin: 0;
	}

	.doccard-l1,
	.doccard-l2 {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.doccard-badges {
		display: inline-flex;
		flex-wrap: wrap;
		gap: var(--s1);
	}

	/* Neutral badges: facts on a document, never a judgement (UI-SPEC « Couleur »). */
	.doccard-badge {
		display: inline-block;
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	/* The number link keeps a 44px hit zone (UI-SPEC point 4, WCAG 2.5.5), pulled back into the card
	   padding and the gap above by a negative margin (same technique as SourceButton): the layout
	   keeps a 20px line, so the densest card stays ≤ 160px at 375px. */
	.doccard-l3 {
		display: flex;
	}

	.doccard-lien {
		display: inline-flex;
		align-items: center;
		min-width: 0;
		min-height: 44px;
		margin: calc(-1 * var(--s3)) 0;
		color: var(--color-ink);
		font-size: 16px;
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.chiffre {
		font-variant-numeric: tabular-nums;
	}

	/* C39: the checkbox shares line 3 with the number link, at its right; a 44px hit zone pulled back
	   into the card padding like the link, so a card keeps its UI-SPEC height at 375px. */
	.doccard-l3 {
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0 var(--s4);
	}

	.doccard-choix {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		margin: calc(-1 * var(--s3)) 0;
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
		cursor: pointer;
	}

	.doccard-choix input {
		width: 20px;
		height: 20px;
		margin: 0;
	}

	.doclist-comparer {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s2) var(--s4);
		margin: var(--s4) 0 0;
	}

	/* Nothing to show (fewer than two boxes, no message): the block takes no room. */
	.doclist-comparer:not(:has(a, p)) {
		display: none;
	}

	.doclist-comparer-notes {
		display: flex;
		flex-direction: column;
		gap: var(--s2);
	}

	.doclist-comparer-notes:empty {
		display: none;
	}

	.doclist-comparer-note {
		max-width: var(--mesure);
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* The number is never cut; it wraps only when the window is narrower than it (a real 200 % zoom
	   of a 320px window leaves 160 CSS px, 04-08 fiche-grid): never a horizontal scroll. */
	.doccard-lien .chiffre {
		min-width: 0;
		overflow-wrap: anywhere;
	}

	/* Declared address (UI-SPEC point 4, line 4): 14px muted, a long typed text wraps at 375px. */
	.doccard-l4 {
		max-width: var(--mesure);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* Secondary button (UI-SPEC v4 § 4.6): line-strong outline, ink text, control radius, 16 px / 600. */
	.doclist-secondaire {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		font-weight: 600;
		text-decoration: none;
		cursor: pointer;
	}

	.doclist-secondaire:hover {
		background: var(--color-soft);
	}

	/* State panel (UI-SPEC v4 § 3.13): a panel, ink text, no ochre fill (ochre belongs to the Vigilance);
	   the words say the outage, the colour never does. The alert role is kept. */
	.doclist-panne {
		box-sizing: border-box;
		max-width: 100%;
		padding: var(--s5);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-panneau);
		background: var(--color-panneau);
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.doclist-panne p {
		max-width: var(--mesure);
		margin: 0 0 var(--s2);
	}

	@media (max-width: 720px) {
		.doclist-h2 {
			font-size: 20px;
		}

		.doclist-panne {
			padding: var(--s4);
		}

		/* The nested fiche leaves 275 px inside a 375 px card. Keep the controls beside each other;
		   the link may put its number below its prefix, without breaking it to fit the checkbox. */
		.doccard-l3 {
			flex-wrap: nowrap;
			gap: 0 var(--s1);
		}

		.doccard-lien {
			flex-wrap: wrap;
		}

		.doccard-lien .chiffre {
			flex-shrink: 0;
			max-width: 100%;
		}

		/* All seven served counts stay beside their class within the nested mobile fiche. */
		.doclist-distribution {
			gap: var(--s1);
		}

		.doclist-distribution :global(.dpe-tag) {
			min-width: 20px;
		}

		.doccard-choix {
			gap: var(--s1);
		}
	}
	@media print {
		.doclist-consigne,
		.doclist-comparer,
		.doccard-choix {
			display: none;
		}
	}
</style>
