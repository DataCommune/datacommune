<script lang="ts" module>
	import { flushSync, getContext, setContext } from 'svelte';

	const VOLETS_DIFFERES = Symbol('volets-differes');

	/**
	 * Called by a page while it initialises: the panels under it render their content when first opened,
	 * never in the server HTML. The commune fiche carries some forty panels whose explanations are the same
	 * on every commune; served, they made two fiches look alike to a search engine (Search Console of
	 * 2026-11-16). Opening still makes no network call: the content is rendered from what the page read.
	 */
	export function differerLesVolets() {
		setContext(VOLETS_DIFFERES, true);
	}
</script>

<script lang="ts">
	// Source panel of a value (UI-SPEC « Bouton i et volet », E6): a modal <dialog>, never a
	// floating tooltip. Side panel of at most 552px from 721px, full screen below, scrolled
	// inside. Focus goes to its title on opening and back to the `i` button that opened it on
	// closing (Escape, « Fermer » or a click on the backdrop). Opening makes no network call: everything shown
	// was read with the page, and an entry the API did not serve is not rendered at all.
	//
	// Every string is rendered through Svelte text interpolation (escaped); the only links are the
	// served URLs (the source, or the URL an entry carries such as « Référence », 04-18), and only
	// when they are https. « Rattachement » (04-18, SEM-02) is an ordinary entry placed by the model
	// after « Objet »; like every entry, it is rendered only when it is served.
	import type { EntreeVolet, SourceKpi, ValeurServie } from '$lib/data/commune-kpi';
	import { LIBELLES_MOTIFS, libelleEtat, libelleMotifAbsence } from '$lib/data/etats-libelles';
	import { urlHttpsSeulement } from '$lib/utils/source-url';
	import { DEPLOYMENT_SHA } from '$lib/config';
	import { lienSignalement } from '$lib/signalement/contexte';
	import { LIBELLES } from '$lib/signalement/libelles';
	import { SIGNALEMENT } from '$lib/signalement/reglage';
	import StateValue from './StateValue.svelte';

	interface Props {
		titre: string;
		entrees: EntreeVolet[];
		/** `objet`: names the row object of a raw value when the panel covers several (comparator row). */
		brut: (ValeurServie & { objet?: string })[];
		source?: SourceKpi | null;
	}

	let { titre, entrees, brut, source = null }: Props = $props();

	const titreId = $props.id();
	let dialog = $state<HTMLDialogElement>();
	// Under `differerLesVolets`, nothing is rendered inside the dialog before its first opening.
	let rendu = $state(getContext(VOLETS_DIFFERES) !== true);
	let titreElement = $state<HTMLHeadingElement>();
	let declencheur: HTMLElement | null = null;
	// What had the focus when opening: a native close gives it back there, and a click does not
	// always focus the trigger (Safari, Firefox on macOS).
	let focusAvant: Element | null = null;
	// « Signaler cette valeur » (framing of 2026-11-10): built when the panel opens, from what it
	// already shows. Null while the report is switched off: nothing more is rendered.
	let lienSignaler = $state<string | null>(null);

	const urlSource = $derived(urlHttpsSeulement(source?.url));
	const affichees = $derived(
		entrees.filter((entree) => ('absences' in entree ? entree.absences.length > 0 : entree.valeur.trim() !== ''))
	);

	/** Opens the panel; `origine` gets the focus back when it closes. */
	export function ouvrir(origine: HTMLElement) {
		if (!dialog || dialog.open) return;
		if (!rendu) {
			rendu = true;
			// The title must exist before the focus goes to it.
			flushSync();
		}
		declencheur = origine;
		focusAvant = document.activeElement;
		if (SIGNALEMENT) {
			lienSignaler = lienSignalement({ page: window.location.pathname, titre, brut, source: source?.nom, version: DEPLOYMENT_SHA });
		}
		if (typeof dialog.showModal === 'function') dialog.showModal();
		else dialog.setAttribute('open', '');
		titreElement?.focus();
	}

	export function fermer() {
		if (!dialog) return;
		if (typeof dialog.close === 'function') dialog.close();
		else {
			dialog.removeAttribute('open');
			dialog.dispatchEvent(new Event('close'));
		}
	}

	/** A click on the backdrop closes the panel (owner acceptance, 2026-09-28); a click inside it
	 * never does: the panel content fills the dialog box, so only the backdrop targets the dialog. */
	function surClic(event: MouseEvent) {
		if (event.target === dialog) fermer();
	}

	function surFermeture() {
		// `close` is fired in a later task: a native modal dialog has already given the focus back,
		// and it may have moved on since (another button, another panel). Only a focus still left in
		// the panel or dropped to the body is brought back, never one taken by another element.
		// A panel reopened before this late event keeps its new trigger.
		if (dialog?.open) return;
		const actif = document.activeElement;
		if (!actif || actif === document.body || dialog?.contains(actif) || actif === focusAvant) declencheur?.focus();
		declencheur = null;
		focusAvant = null;
	}

	function surTouche(event: KeyboardEvent) {
		// A native modal dialog closes itself on Escape; this covers the environments without one.
		if (event.key === 'Escape' && typeof dialog?.showModal !== 'function') {
			event.preventDefault();
			fermer();
			return;
		}
		// Focus stays in the panel: Tab from its last control comes back to the first one (a modal
		// dialog makes the page inert, but lets the focus leave for the browser chrome).
		if (event.key !== 'Tab' || !dialog) return;
		const focusables = [
			...dialog.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), summary')
		].filter((element) => element.getClientRects().length > 0);
		if (focusables.length === 0) return;
		const premier = focusables[0];
		const dernier = focusables[focusables.length - 1];
		const actif = document.activeElement;
		if (event.shiftKey && (actif === premier || actif === titreElement)) {
			event.preventDefault();
			dernier.focus();
		} else if (!event.shiftKey && actif === dernier) {
			event.preventDefault();
			premier.focus();
		}
	}

	// A served reason, worded (03-17 F57): an out-of-coverage reason is already worded by its badge,
	// a technical key is never shown, and a reason that repeats the help sentence is not repeated.
	function motifLisible(etat: { motif: string; aide?: string | null }): string | null {
		if (Object.hasOwn(LIBELLES_MOTIFS, etat.motif)) return null;
		const libelle = libelleMotifAbsence(etat.motif.trim());
		return libelle && libelle !== etat.aide ? libelle : null;
	}

	function libelleServi(etat: ValeurServie['etat']): string {
		return etat.kind === 'valeur' ? 'Valeur' : libelleEtat(etat.etat, etat.motif);
	}
</script>

<!-- Keyboard closing: Escape (native modal dialog, or surTouche) and « Fermer ». -->
<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
<dialog
	bind:this={dialog}
	class="source-panel"
	aria-labelledby={rendu ? titreId : undefined}
	onclose={surFermeture}
	onkeydown={surTouche}
	onclick={surClic}
>
	{#if rendu}<div class="source-panel-inner">
		<div class="source-panel-head">
			<h2 id={titreId} class="source-panel-title" tabindex="-1" bind:this={titreElement}>{titre}</h2>
			<button type="button" class="source-panel-close" onclick={fermer}>Fermer</button>
		</div>
		<dl class="source-panel-list">
			{#each affichees as entree (entree.terme)}
				<div class="source-panel-entry">
					<dt>{entree.terme}</dt>
					{#if 'absences' in entree}
						<dd>
							{#each entree.absences as absence, index (index)}
								<div class="source-panel-absence">
									{#if absence.libelle}<p class="source-panel-sub">{absence.libelle}</p>{/if}
									<StateValue etat={absence.etat} />
									{#if motifLisible(absence.etat)}<p class="source-panel-reason">Motif : {motifLisible(absence.etat)}</p>{/if}
								</div>
							{/each}
						</dd>
					{:else if urlHttpsSeulement(entree.url)}
						<dd><a href={urlHttpsSeulement(entree.url)} target="_blank" rel="noopener noreferrer">{entree.valeur}</a></dd>
					{:else if entree.terme === 'Source' && urlSource}
						<dd><a href={urlSource} target="_blank" rel="noopener noreferrer">{entree.valeur}</a></dd>
					{:else}
						<dd>{entree.valeur}</dd>
					{/if}
				</div>
			{/each}
		</dl>
		{#if brut.length}
			<details class="source-panel-raw">
				<summary>Voir la donnée telle que l'API la renvoie</summary>
				{#each brut as servie, index (index)}
					<div class="source-panel-raw-bloc">
						{#if servie.objet}<p class="source-panel-sub">{servie.objet}</p>{/if}
						<dl class="source-panel-raw-list">
							<div><dt>Identifiant du champ (field_id)</dt><dd><code>{servie.fieldId}</code></dd></div>
							<div><dt>Valeur brute</dt><dd><code>{servie.valeur}</code></dd></div>
							<div><dt>État</dt><dd>{libelleServi(servie.etat)}</dd></div>
						</dl>
					</div>
				{/each}
			</details>
		{/if}
		{#if lienSignaler}<p class="source-panel-signaler"><a href={lienSignaler}>{LIBELLES.volet}</a></p>{/if}
	</div>{/if}
</dialog>

<style>
	.source-panel {
		box-sizing: border-box;
		/* Pinned to the edges of the viewport, scrollbar excluded (100dvw would include it and
		   push the panel 16px past the right edge): a side panel of 552px at most. */
		inset: 0 0 0 auto;
		width: min(552px, 100%);
		max-width: 100%;
		height: auto;
		max-height: none;
		margin: 0;
		padding: 0;
		border: 0;
		border-left: 1px solid var(--color-line);
		background: var(--color-panneau);
		color: var(--color-ink);
	}

	/* The one motion of the contract (150ms), cut by prefers-reduced-motion below. */
	.source-panel[open] {
		animation: source-panel-entree 150ms ease-out;
	}

	@keyframes source-panel-entree {
		from {
			opacity: 0;
			transform: translateX(var(--s4));
		}
	}

	.source-panel::backdrop {
		background: color-mix(in srgb, var(--color-ink) 40%, transparent);
	}

	.source-panel-inner {
		box-sizing: border-box;
		height: 100%;
		overflow-y: auto;
		overscroll-behavior: contain;
		padding: var(--s5);
	}

	.source-panel-head {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		justify-content: space-between;
		gap: var(--s2) var(--s4);
		margin-bottom: var(--s4);
	}

	.source-panel-title {
		flex: 1 1 240px;
		margin: 0;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
		overflow-wrap: anywhere;
	}

	.source-panel-title:focus {
		outline: none;
	}

	/* Secondary button (UI-SPEC v4 § 4.6): line-strong outline, ink text, control radius, 16 px / 600. */
	.source-panel-close {
		display: inline-flex;
		align-items: center;
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
		cursor: pointer;
	}

	.source-panel-close:hover {
		background: var(--color-soft);
	}

	.source-panel-list,
	.source-panel-raw-list {
		margin: 0;
	}

	/* A row of the list (UI-SPEC v4 § 3.0, § 3.13 `dl.tab`): padding --s3 0, the pale line only between two
	   rows, never under the title or after the last one. */
	.source-panel-entry {
		padding: var(--s3) 0;
	}

	.source-panel-entry + .source-panel-entry {
		border-top: 1px solid var(--color-filet-pale);
	}

	dt {
		margin: 0 0 var(--s1);
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	dd {
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	dd a {
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 4px;
	}

	.source-panel-absence + .source-panel-absence {
		margin-top: var(--s2);
	}

	.source-panel-sub,
	.source-panel-reason {
		max-width: var(--mesure);
		margin: var(--s1) 0 0;
		font-size: 14px;
		line-height: 1.4;
	}

	.source-panel-raw {
		margin-top: var(--s4);
		padding-top: var(--s3);
		border-top: 1px solid var(--color-filet-pale);
	}

	.source-panel-raw summary {
		display: flex;
		align-items: center;
		min-height: 44px;
		font-size: 16px;
		cursor: pointer;
	}

	.source-panel-raw-list {
		margin-top: var(--s2);
	}

	.source-panel-raw-list > div {
		padding: var(--s1) 0;
	}

	.source-panel-signaler {
		margin: var(--s4) 0 0;
		padding-top: var(--s3);
		border-top: 1px solid var(--color-filet-pale);
		font-size: 16px;
	}

	.source-panel-signaler a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}

	code {
		font-family: var(--font-sans);
		font-size: 14px;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	@media (max-width: 720px) {
		/* Full screen: the whole visible viewport, whatever its dynamic height. */
		.source-panel {
			inset: 0;
			width: auto;
			max-width: none;
			border-left: 0;
		}

		.source-panel-inner {
			padding: var(--s4);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.source-panel[open] {
			animation: none;
		}
	}

	@media print {
		.source-panel {
			display: none;
		}
	}
</style>
