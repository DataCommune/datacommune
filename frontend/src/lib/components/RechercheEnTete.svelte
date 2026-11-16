<script lang="ts" module>
	/** Id of the header field, focused by Ctrl+K (⌘K) when it is displayed. */
	export const ID_RECHERCHE_EN_TETE = 'recherche-en-tete';
</script>

<script lang="ts">
	// Compact search of the site header (04.7-14, Origami R17, E9): a plain GET form to the typed results
	// page, so it works without JavaScript and the query stays in the URL; 200 characters at most.
	//
	// Public form (`publique`, UI-SPEC v4 § 3.11, `site-q` of the mockup): the help text « Adresse ou
	// commune » and a plain button, « Rechercher » on screen followed by a visually hidden « une adresse
	// ou une commune », so its accessible name starts with its visible text (WCAG 2.5.3); shown at every
	// width, alone on its row up to 720 px.
	//
	// Connected form (the default, routes (app)): the form of 04.7-14, kept as it was (D-27): the
	// magnifier button, the « Ctrl K » hint, its own name; shown from 1200 px at 232 px, where its one-line
	// header has room for it.
	let { publique = false }: { publique?: boolean } = $props();

	const NOM = $derived(publique ? 'Rechercher une adresse ou une commune' : 'Rechercher une commune ou une adresse');
</script>

<form class="recherche-en-tete" class:recherche-en-tete-publique={publique} role="search" aria-label={NOM} action="/communes" method="get">
	<label for={ID_RECHERCHE_EN_TETE} class="recherche-en-tete-masque">{NOM}</label>
	<input
		id={ID_RECHERCHE_EN_TETE}
		name="q"
		type="search"
		placeholder={publique ? 'Adresse ou commune' : 'Commune ou adresse'}
		autocomplete="off"
		aria-keyshortcuts="Control+K Meta+K"
		required
		minlength="2"
		maxlength="200"
	/>
	{#if publique}
		<!-- The space stays between the two nodes: Svelte removes the whitespace at the start of a tag. -->
		<button type="submit" class="recherche-en-tete-bouton"
			>Rechercher <span class="recherche-en-tete-masque">une adresse ou une commune</span></button
		>
	{:else}
		<kbd class="recherche-en-tete-raccourci" aria-hidden="true">Ctrl K</kbd>
		<button type="submit" class="recherche-en-tete-loupe">
			<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
				><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg
			>
			<span class="recherche-en-tete-masque">Rechercher</span>
		</button>
	{/if}
</form>

<style>
	/* Connected form: the values of 04.7-14, written with the tokens of :root (D-27). Relative: the hint,
	   the button and the visually hidden words are placed inside the form. */
	.recherche-en-tete {
		position: relative;
		display: none;
		align-items: center;
		width: 232px;
	}

	.recherche-en-tete input {
		box-sizing: border-box;
		width: 100%;
		min-height: 44px;
		/* Room for the 44 px button on the right; the hint, on the paper of the field, covers what
		   runs under it (92 px of padding before, outside the closed list of tokens). */
		padding: 0 var(--s8) 0 var(--s3);
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

	.recherche-en-tete input::placeholder {
		color: var(--color-muted);
		opacity: 1;
	}

	.recherche-en-tete-raccourci {
		position: absolute;
		right: 48px;
		padding: 0 var(--s1);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-muted);
		font-family: inherit;
		font-size: 14px;
		line-height: 1.4;
		pointer-events: none;
	}

	.recherche-en-tete-loupe {
		position: absolute;
		right: 0;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 44px;
		height: 44px;
		border: 0;
		background: transparent;
		color: var(--color-ink);
		cursor: pointer;
	}

	.recherche-en-tete-loupe svg {
		fill: none;
		stroke: currentColor;
		stroke-width: 2;
	}

	.recherche-en-tete-masque {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	@media (min-width: 1200px) {
		.recherche-en-tete {
			display: flex;
		}
	}

	/* Public form (style.css:55-57 of the mockup): grows from 280 to 460 px beside the brand. */
	.recherche-en-tete-publique {
		display: flex;
		flex: 1 1 280px;
		align-items: stretch;
		width: auto;
		min-width: 0;
		max-width: 460px;
	}

	.recherche-en-tete-publique input {
		flex: 1 1 auto;
		min-width: 0;
		padding: var(--s2) var(--s3);
		border-right: 0;
		border-radius: var(--radius-control) 0 0 var(--radius-control);
		background: var(--color-bg);
	}

	.recherche-en-tete-bouton {
		flex: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 0;
		border-radius: 0 var(--radius-control) var(--radius-control) 0;
		background: var(--color-accent);
		color: var(--color-paper);
		font-family: inherit;
		font-size: 16px;
		font-weight: 600;
		line-height: 1;
		white-space: nowrap;
		cursor: pointer;
	}

	.recherche-en-tete-bouton:hover {
		background: var(--color-accent-hover);
	}

	/* Beside « Menu », up to 1120 px, it takes the rest of the row. */
	@media (max-width: 1120px) {
		.recherche-en-tete-publique {
			max-width: none;
		}
	}

	@media (max-width: 720px) {
		.recherche-en-tete-publique {
			order: 3;
			flex-basis: 100%;
		}
	}

	/* At 320 px the help text keeps its whole width beside the button (12 px of padding, not 16, and 8 in
	   the field). */
	@media (max-width: 359px) {
		.recherche-en-tete-bouton {
			padding: 0 var(--s3);
		}

		.recherche-en-tete-publique input {
			padding: var(--s2);
		}
	}

	@media (max-width: 239px) {
		.recherche-en-tete-bouton {
			padding: 0 var(--s2);
		}
	}
</style>
