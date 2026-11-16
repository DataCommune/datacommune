<script lang="ts">
	// Table of contents of a long page (I5, 2026-11-10): the titles of its sections as links to their anchors. The
	// titles already carry their number, so the list shows no marker. The name « Sommaire » is the `aria-label`
	// of the navigation, not a word of the page.
	interface Props {
		/** The sections in reading order: the `id` of the panel and its title as written. */
		sections: readonly { id: string; titre: string }[];
	}

	let { sections }: Props = $props();
</script>

<nav class="sommaire" aria-label="Sommaire">
	<ul>
		{#each sections as section (section.id)}
			<li><a href={`#${section.id}`}>{section.titre}</a></li>
		{/each}
	</ul>
</nav>

<style>
	.sommaire {
		margin-top: var(--s5);
	}

	.sommaire ul {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.sommaire a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		color: var(--color-ink);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 3px;
	}
</style>
