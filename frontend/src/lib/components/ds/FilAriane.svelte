<script lang="ts" module>
	export interface EtapeAriane {
		nom: string;
		/** Link of a step; the last step is the current page and is never a link. */
		href?: string | null;
	}
</script>

<script lang="ts">
	// Breadcrumb of the public pages (UI-SPEC v4 § 3.11): an ordered list in a named navigation, the
	// links in muted ink and underlined, the current page last as a `span aria-current="page"`. The
	// separator « › » is drawn by CSS with an empty alternative, so it is neither text nor read. Only the
	// form changes: the steps, and the BreadcrumbList JSON-LD the pages derive from the same list, are
	// the ones of each page (04.7-10).
	interface Props {
		etapes: readonly EtapeAriane[];
	}

	let { etapes }: Props = $props();
</script>

<nav class="ariane" aria-label="Fil d'Ariane">
	<ol>
		{#each etapes as etape, index (index)}
			<li>
				{#if index === etapes.length - 1}<span aria-current="page">{etape.nom}</span
					>{:else if etape.href}<a href={etape.href}>{etape.nom}</a>{:else}<span>{etape.nom}</span>{/if}
			</li>
		{/each}
	</ol>
</nav>

<style>
	.ariane {
		margin-bottom: var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	ol {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s-demi);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	li {
		min-width: 0;
		overflow-wrap: anywhere;
	}

	/* The plain value first: an engine without alternative text for CSS content keeps a separator. */
	li + li::before {
		content: '›';
		content: '›' / '';
		margin-right: var(--s-demi);
	}

	/* Underlined on purpose: the public reset takes the line off the links. Targets of 24 px (44 px up
	   to 720 px, UI-SPEC § 8) drawn by padding pulled back by the same negative margin, so the line
	   keeps its height. */
	a {
		display: inline-block;
		box-sizing: border-box;
		min-width: 24px;
		min-height: 24px;
		padding: var(--s1) 0;
		margin: calc(-1 * var(--s1)) 0;
		color: var(--color-accent);
		text-decoration-line: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}

	a:hover {
		text-decoration-thickness: 2px;
	}

	@media (max-width: 720px) {
		a {
			min-width: 44px;
			min-height: 44px;
			padding: var(--s3) 0;
			margin: calc(-1 * var(--s3)) 0;
		}
	}

	/* Site chrome: not printed, as the breadcrumbs of the pages were not before it. */
	@media print {
		.ariane {
			display: none;
		}
	}
</style>
