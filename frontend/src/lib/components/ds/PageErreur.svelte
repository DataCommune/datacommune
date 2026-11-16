<script lang="ts">
	// Shared error page (socle of 2026-11-09, T5): the body of an error boundary, on the simple template. It
	// can stand for the three boundaries of the public site without losing a state:
	//   - 404: the title and the message as a lead, the ways out;
	//   - 429: the served sentence (threshold, date the allowance comes back) in a state panel;
	//   - 503: the outage announced (`panne`), its reload busy while it runs.
	// With `etat`, the message sits in a state panel titled by it (`fiche/Notice`); without it, the message is
	// the lead under the h1. The reload is the shared secondary button. An acronym of the message is spelled
	// out once, never in a title nor inside a quotation « … »; a page whose title already says the full form
	// turns it off (`developper={false}`). The `<title>` and `meta robots` stay with the boundary, and so do
	// the header and the footer of the root one, which renders outside the layouts.
	import type { Snippet } from 'svelte';
	import Notice from '$lib/components/fiche/Notice.svelte';
	import { developperHorsCitations } from '$lib/utils/sigles';
	import ListeLiens, { type LienDeListe } from './ListeLiens.svelte';
	import PageSimple from './PageSimple.svelte';
	import Panneau from './Panneau.svelte';

	interface Props {
		titre: string;
		/** HTTP status, said above the title (« Erreur 503 »); a 404 says it by its title. */
		statut?: number;
		/** The line above the title when the page words it its own way (« Adresse »); replaces « Erreur {statut} ». */
		surtitre?: string;
		/** Name of the page in the breadcrumb; none without it. */
		ariane?: string;
		/** First step of the breadcrumb (« Accueil » by default; it always links to `/`). */
		racine?: string;
		/** Line under the h1 (the kind of document, for instance). */
		sousTitre?: string;
		/** Title of the state panel that holds the message; without it the message is the lead. */
		etat?: string;
		/** An outage: announced to assistive technologies, the panel in its `warn` form. */
		panne?: boolean;
		message: string;
		developper?: boolean;
		/** Reload: the button is disabled and busy while `action` runs. */
		relance?: { libelle: string; action: () => void | Promise<unknown> };
		/** Ways out, as whole-row links under « Pour repartir ». */
		liens?: readonly LienDeListe[];
		/** What the page adds between the message and the ways out (a search field, a second sentence). */
		children?: Snippet;
	}

	let { titre, statut, surtitre, ariane, racine, sousTitre, etat, panne = false, message, developper = true, relance, liens = [], children }: Props = $props();

	const messageLu = $derived(developper ? (developperHorsCitations([['message', message]]).get('message') ?? message) : message);

	let enCours = $state(false);

	async function relancer() {
		if (!relance || enCours) return;
		enCours = true;
		try {
			await relance.action();
		} catch {
			// A reload that fails leaves the page on the error it already shows.
		} finally {
			enCours = false;
		}
	}
</script>

{#snippet boutonRelance()}
	{#if relance}
		<button type="button" class="bouton-secondaire" onclick={relancer} disabled={enCours} aria-busy={enCours}>{relance.libelle}</button>
	{/if}
{/snippet}

<PageSimple {titre} {ariane} {racine} {sousTitre}>
	{#snippet avantTitre()}
		{#if surtitre}<p class="erreur-statut">{surtitre}</p>{:else if statut}<p class="erreur-statut">Erreur {statut}</p>{/if}
	{/snippet}
	{#if etat}
		<div class="erreur-etat">
			<Notice titre={etat} variante={panne ? 'warn' : 'info'} role={panne ? 'alert' : 'status'}>
				<p>{messageLu}</p>
				{@render boutonRelance()}
			</Notice>
		</div>
	{:else}
		<p class="erreur-chapo" role={panne ? 'alert' : undefined}>{messageLu}</p>
		{#if relance}<div class="erreur-relance">{@render boutonRelance()}</div>{/if}
	{/if}
	{@render children?.()}
	{#if liens.length > 0}
		<Panneau titre="Pour repartir">
			<ListeLiens {liens} />
		</Panneau>
	{/if}
</PageSimple>

<style>
	.erreur-statut {
		margin: 0 0 var(--s2);
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
	}

	.erreur-chapo {
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 20px;
		line-height: 1.5;
	}

	.erreur-etat {
		margin-top: var(--s6);
	}

	.erreur-relance {
		margin-top: var(--s4);
	}
</style>
