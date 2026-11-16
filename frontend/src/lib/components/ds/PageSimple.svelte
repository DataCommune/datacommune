<script lang="ts">
	// Template of the simple pages (UI-SPEC v4 § 6.0, § 6.9; `style.css:238` of the mockup): a container of
	// the one `.gabarit`, the breadcrumb « Accueil › {page} », the h1 at 44 px (32 px up to 720 px), an
	// optional subtitle at the measure, then the panels of the page. The texts stay those of each page (D-13).
	// Without `ariane` there is no breadcrumb (the root 404, § 6.10); `avantTitre` is a line above the h1
	// (the status of an error other than 404).
	import type { Snippet } from 'svelte';
	import FilAriane from './FilAriane.svelte';

	interface Props {
		titre: string;
		/** Name of the page in the breadcrumb; none without it. */
		ariane?: string;
		/** First step of the breadcrumb (it always links to `/`); « Accueil » unless the page words it otherwise. */
		racine?: string;
		/** Above the h1: the status of an error page. */
		avantTitre?: Snippet;
		/** Line under the title (date of the page, for instance). */
		sousTitre?: string;
		children: Snippet;
	}

	let { titre, ariane, racine = 'Accueil', avantTitre, sousTitre, children }: Props = $props();
</script>

<div class="simple gabarit">
	{#if ariane}<FilAriane etapes={[{ nom: racine, href: '/' }, { nom: ariane }]} />{/if}
	{@render avantTitre?.()}
	<h1 class="simple-titre">{titre}</h1>
	{#if sousTitre}<p class="fiche-sous">{sousTitre}</p>{/if}
	{@render children()}
</div>

<style>
	.simple {
		padding-block: var(--s6) var(--s8);
		color: var(--color-ink);
		overflow-wrap: anywhere;
	}

	.simple-titre {
		margin: 0;
		font-size: 44px;
		line-height: 1.12;
		text-wrap: balance;
	}

	.fiche-sous {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		color: var(--color-muted);
		font-size: 16px;
	}

	/* Fallback measure of the running text of a simple page; a component may set its own. */
	:where(.simple) :global(p),
	:where(.simple) :global(ul.puces > li),
	:where(.simple) :global(dd) {
		max-width: var(--mesure);
		line-height: 1.6;
	}

	/* Links in a sentence: underlined, in accent (UI-SPEC § 3.0); a list of links styles its own rows. */
	:where(.simple) :global(:is(p, li, dd) a) {
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	:where(.simple) :global(:is(p, li, dd) a:hover) {
		color: var(--color-accent-hover);
		text-decoration-thickness: 2px;
	}

	:where(.simple) :global(ul.puces) {
		padding-left: var(--s5);
		list-style: disc;
	}

	:where(.simple) :global(ul.puces > li + li) {
		margin-top: var(--s1);
	}

	@media (max-width: 720px) {
		.simple {
			padding-block: var(--s5) var(--s7);
		}

		.simple-titre {
			font-size: 32px;
		}
	}

	@media print {
		.simple {
			padding: 0;
		}
	}
</style>
