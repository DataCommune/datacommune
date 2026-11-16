<script lang="ts">
	// Designation of a DPE on a compared address (UI-SPEC « Désignation d'un DPE sur une adresse
	// comparée », D-102, 04-11). In the header of an address column: « Désigner un DPE » opens, under
	// the header, a group of radio buttons listing the documents the API serves for this address
	// (`getDocumentsDpe`, 10 per page, « Documents suivants »), then « Désigner ce DPE ». Once a DPE
	// is designated, the header says it and offers « Retirer la désignation ».
	//
	// The designation lives in the link only: this component keeps no state of it, in no storage and
	// in no database; it hands the chosen number to the page, which writes the link (`designes`) and
	// reads the comparison again. Nothing is chosen for the visitor: no radio is checked when the
	// group opens, a tie of dates designates nothing. Focus goes back to « Désigner un DPE » when the
	// group closes (UI-SPEC « Réactivité, impression, accessibilité »).
	//
	// D-15 FRONT WR-02: the open group is carried into `cible`, a full-width row the table puts under
	// its header, never inside the column header (124 px at 375 px): each number stays on one line
	// and « Désigner ce DPE » within a screen of « Désigner un DPE ». Without `cible` it stays here.
	import { tick, untrack } from 'svelte';
	import { getDocumentsDpe, type DocumentDpeCarte } from '$lib/api/adresses';
	import { MESSAGE_PANNE_DOCUMENTS, estClasse, libelleBadge, moisAnnee } from '$lib/data/adresse-fiche';
	import { titreDocument } from '$lib/data/document-dpe';
	import { PHRASES_MOTIFS_NON_PUBLIES } from '$lib/data/etats-libelles';
	import type { DesignationColonne } from '$lib/data/commune-comparison';
	import StateValue from '$lib/components/fiche/StateValue.svelte';

	interface Props {
		banId: string;
		/** Accessible name of the address: « {numéro} {voie}, {CP} {Commune} ». */
		adresse: string;
		designation: DesignationColonne | null;
		occupe?: boolean;
		/** Where the open group is shown (a full-width row of the table); here when absent. */
		cible?: HTMLElement | null;
		/** Told whenever the group opens or closes, so the table shows or removes its row. */
		onbasculer?: (ouvert: boolean) => void;
		ondesigner?: (numero: string) => void;
		onretirer?: () => void;
	}

	let { banId, adresse, designation, occupe = false, cible = null, onbasculer, ondesigner, onretirer }: Props = $props();

	const id = $props.id();
	/** UI-SPEC « Panne d'une section » of the documents: the rest of the table stays shown. */
	const PANNE = MESSAGE_PANNE_DOCUMENTS;
	/** UI-SPEC motif `aucun_document` (04-DECISIONS-TEXTES row 50). */
	const AUCUN = PHRASES_MOTIFS_NON_PUBLIES.aucun_document;
	/** Derived from « Chargement du document… », kept (04-DECISIONS-TEXTES row 64). */
	const CHARGEMENT = 'Chargement des documents DPE…';
	/** Legend of the radio buttons (04-DECISIONS-TEXTES row 66); the group itself names the address. */
	const LEGENDE = 'Documents DPE déclarés à cette adresse';

	let ouvert = $state(false);
	let chargement = $state(false);
	let panne = $state(false);
	/** Message of a typed refusal (429, 401, 403: `panne.ts`), never worded as an outage. */
	let messagePanne = $state<string | null>(null);
	/** The documents of the address are out of coverage: a state, never an outage (D-15 FRONT WR-04). */
	let horsCouverture = $state<{ motif: string } | null>(null);
	let documents = $state<DocumentDpeCarte[]>([]);
	let total = $state<number | null>(null);
	let page = $state(0);
	let pages = $state(0);
	let generation = $state<string | null>(null);
	let choix = $state<string | null>(null);
	let boutonDesigner = $state<HTMLButtonElement>();
	let boutonRetirer = $state<HTMLButtonElement>();
	let groupe = $state<HTMLDivElement>();

	/** « classe {X} · {mois année} », the parts the API served after « DPE n° {numéro} ». */
	function details(document: DocumentDpeCarte): string {
		const classe = estClasse(document.etiquette_dpe) ? `classe ${document.etiquette_dpe}` : null;
		return [classe, moisAnnee(document.date_etablissement)].filter(Boolean).join(' · ');
	}

	/** Moves the open group into `cible` (the row of the table); it is removed when it closes. */
	function porter(noeud: HTMLElement, vers: HTMLElement | null) {
		const deplacer = (destination: HTMLElement | null) => {
			if (destination && noeud.parentElement !== destination) destination.appendChild(noeud);
		};
		deplacer(vers);
		return {
			update: deplacer,
			destroy: () => noeud.remove()
		};
	}

	$effect(() => {
		const etat = ouvert;
		untrack(() => onbasculer?.(etat));
	});

	// « Désigner un DPE » is inert without JavaScript: masked until the page is hydrated
	// (04-DECISIONS-TEXTES row 67); the server HTML carries no such button.
	let hydratee = $state(false);
	$effect(() => {
		hydratee = true;
	});

	/** Reads page `suivante` of the list, under the snapshot of the first page (`g`). */
	async function lire(suivante: number) {
		chargement = true;
		panne = false;
		messagePanne = null;
		horsCouverture = null;
		const issue = await getDocumentsDpe(fetch, banId, String(suivante), suivante > 1 ? generation : null);
		chargement = false;
		if (issue.kind === 'page_invalide') {
			// The documents changed between two pages: the list is read again from page 1.
			documents = [];
			choix = null;
			if (suivante > 1) await lire(1);
			else panne = true;
			return;
		}
		if (issue.kind === 'panne') {
			panne = true;
			messagePanne = issue.panne.message;
			return;
		}
		if (issue.documents.etat === 'out_of_coverage') {
			horsCouverture = { motif: issue.documents.motif ?? '' };
			return;
		}
		if (issue.documents.etat !== 'value') {
			panne = true;
			return;
		}
		const lus = issue.documents;
		documents = suivante === 1 ? lus.documents : [...documents, ...lus.documents];
		total = lus.total;
		page = lus.page;
		pages = lus.pages ?? lus.page;
		generation = lus.generation;
	}

	async function basculer() {
		if (ouvert) {
			fermer();
			return;
		}
		ouvert = true;
		if (!documents.length) await lire(1);
	}

	/** Closes the group; the focus goes back to « Désigner un DPE ». */
	async function fermer() {
		ouvert = false;
		choix = null;
		await tick();
		boutonDesigner?.focus();
	}

	function surTouche(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		event.preventDefault();
		void fermer();
	}

	async function suivants() {
		const premierNouveau = documents.length;
		await lire(page + 1);
		await tick();
		// The focus goes to the first document read, so the keyboard does not start over; when the
		// list was read again from page 1, to its first document (D-15 FRONT IN-09).
		const radios = groupe?.querySelectorAll<HTMLInputElement>('input[type="radio"]');
		(radios?.[premierNouveau] ?? radios?.[0])?.focus();
	}

	/** Empties the group: closed, nothing chosen, the list read again at the next opening. */
	function vider() {
		ouvert = false;
		choix = null;
		documents = [];
		total = null;
		page = 0;
		pages = 0;
		generation = null;
		panne = false;
		messagePanne = null;
		horsCouverture = null;
	}

	function designer() {
		if (!choix) return;
		const numero = choix;
		// The group never outlives the designation it made (D-15 FRONT WR-01).
		vider();
		ondesigner?.(numero);
	}

	// A designation set, removed or set aside by the API (the link changed): the group is closed and
	// emptied, so it never reopens with the previous document checked (D-15 FRONT WR-01).
	$effect(() => {
		void designation?.numero;
		untrack(vider);
	});

	/** Focus on the visible control: « Retirer la désignation » once designated, else « Désigner un DPE ». */
	export function focaliser(): boolean {
		const bouton = designation ? boutonRetirer : boutonDesigner;
		if (!bouton) return false;
		bouton.focus();
		return true;
	}
</script>

<div class="designation" data-designation={designation ? designation.numero : undefined}>
	{#if designation}
		<p class="designation-texte">{designation.texte}</p>
		<button
			bind:this={boutonRetirer}
			type="button"
			class="designation-action"
			aria-label={`Retirer la désignation du DPE pour ${adresse}`}
			disabled={occupe}
			onclick={() => onretirer?.()}>Retirer la désignation</button
		>
	{:else if hydratee}
		<button
			bind:this={boutonDesigner}
			type="button"
			class="designation-action"
			aria-label={`Désigner un DPE pour ${adresse}`}
			aria-expanded={ouvert}
			aria-controls={`${id}-groupe`}
			disabled={occupe}
			onclick={basculer}>Désigner un DPE</button
		>
		{#if ouvert}
			<!-- svelte-ignore a11y_no_noninteractive_element_interactions (Escape closes the group, focus returns) -->
			<div
				id={`${id}-groupe`}
				class="designation-groupe"
				role="group"
				aria-label={`Désigner un DPE pour ${adresse}`}
				bind:this={groupe}
				use:porter={cible}
				onkeydown={surTouche}
				aria-busy={chargement}
			>
				{#if horsCouverture}
					<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: horsCouverture.motif, effectif: null }} />
				{:else if panne}
					<p class="designation-panne" role="alert">{messagePanne ?? PANNE}</p>
					<button type="button" class="designation-secondaire" onclick={() => lire(1)}>Recharger les documents DPE</button>
				{:else if chargement && !documents.length}
					<p class="designation-note" role="status">{CHARGEMENT}</p>
				{:else if total === 0}
					<p class="designation-note">{AUCUN}</p>
				{:else}
					<fieldset class="designation-liste">
						<legend class="designation-legende">{LEGENDE}</legend>
						{#each documents as document (document.numero)}
							<label class="designation-option">
								<input type="radio" name={`${id}-numero`} value={document.numero} bind:group={choix} />
								<span class="designation-libelle">
									<!-- The number never cut, its details on the next line (D-15 FRONT WR-02). -->
									<span
										><span class="designation-numero">{titreDocument(document.numero)}</span
										>{#if details(document)}<span class="designation-separateur">{' · '}</span><span class="designation-details"
												>{details(document)}</span
											>{/if}</span
									>
									{#if document.badges.length}
										<span class="designation-badges">
											{#each document.badges as badge (badge)}<span class="designation-badge">{libelleBadge(badge)}</span>{/each}
										</span>
									{/if}
								</span>
							</label>
						{/each}
					</fieldset>
					{#if page < pages}
						<button type="button" class="designation-secondaire" disabled={chargement} aria-busy={chargement} onclick={suivants}
							>Documents suivants</button
						>
					{/if}
					<button type="button" class="designation-secondaire" disabled={!choix || occupe} onclick={designer}>Désigner ce DPE</button>
				{/if}
			</div>
		{/if}
	{/if}
</div>

<style>
	.designation {
		margin-top: var(--s1);
	}

	.designation-texte {
		margin: 0;
		color: var(--color-ink);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	/* The secondary link-button of the column actions (« Retirer », « Placer en premier »). */
	.designation-action {
		display: inline-flex;
		align-items: center;
		min-width: 44px;
		min-height: 44px;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--color-accent);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		font-size: 14px;
		line-height: 1.2;
		text-align: left;
		text-decoration: underline;
		text-underline-offset: 4px;
		cursor: pointer;
	}

	.designation-action:hover {
		color: var(--color-accent-hover);
	}

	.designation-action:disabled {
		cursor: wait;
	}

	.designation-groupe {
		display: grid;
		gap: var(--s2);
		margin-top: var(--s1);
		padding: var(--s2);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-control);
		background: var(--color-paper);
	}

	.designation-liste {
		display: grid;
		gap: var(--s1);
		min-width: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}

	/* The legend names the radio buttons for assistive technologies; the button above already says it. */
	.designation-legende {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	.designation-option {
		display: flex;
		align-items: flex-start;
		gap: var(--s2);
		min-height: 44px;
		font-size: 14px;
		line-height: 1.4;
		cursor: pointer;
	}

	.designation-option input {
		flex: none;
		width: 20px;
		height: 20px;
		margin: var(--s-fin) 0 0;
		accent-color: var(--color-ink);
	}

	.designation-libelle {
		display: grid;
		gap: var(--s1);
		min-width: 0;
		overflow-wrap: anywhere;
	}

	.designation-numero {
		display: block;
		white-space: nowrap;
	}

	/* Kept in the text (one label for every reader), not drawn: the details start their own line. */
	.designation-separateur {
		display: none;
	}

	.designation-details {
		display: block;
	}

	.designation-badges {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s1);
	}

	.designation-badge {
		display: inline-block;
		max-width: 100%;
		box-sizing: border-box;
		padding: var(--s-fin) var(--s-demi);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.designation-secondaire {
		min-height: 44px;
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: inherit;
		font-style: inherit;
		font-size: 14px;
		line-height: 1.2;
		cursor: pointer;
	}

	.designation-secondaire:hover:not(:disabled) {
		background: var(--color-soft);
	}

	.designation-secondaire:disabled {
		color: var(--color-muted);
		cursor: not-allowed;
	}

	.designation-note,
	.designation-panne {
		margin: 0;
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.designation-note {
		color: var(--color-muted);
	}

	.designation-panne {
		color: var(--color-warn);
	}

	@media print {
		.designation-action,
		.designation-groupe {
			display: none;
		}
	}
</style>
