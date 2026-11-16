<script lang="ts">
	// Key figure tile (UI-SPEC v4 § 3.0, § 3.2): one datum on its own, `div.cle` inside a `dl.cles`
	// (four columns, two under 1080 px, one under 400 px) or `dl.cles.cles-3` (three columns, two under
	// 720 px with the last one full width, one under 400 px). The label is the `dt`; the `dd` holds the
	// value the caller renders (served value, DPE class or state word), then the scale mark at 18 px with
	// its word and the note on its own line.
	import type { Snippet } from 'svelte';
	import RepereEchelle, { type Pictogramme } from './RepereEchelle.svelte';

	interface Props {
		libelle: string;
		repere?: { pictogramme?: Pictogramme; mot: string; motVisible?: boolean };
		note?: string;
		/** The control that explains the value (4.9, A9-8), after its note. */
		action?: Snippet;
		children: Snippet;
	}

	let { libelle, repere, note, action, children }: Props = $props();
</script>

<div class="cle">
	<dt>{libelle}</dt>
	<dd>
		{@render children()}
		{#if repere || note || action}
			<span class="cle-n"
				>{#if repere}<RepereEchelle
						pictogramme={repere.pictogramme}
						mot={repere.mot}
						motVisible={repere.motVisible ?? true}
						taille={18}
					/>{/if}{#if note}<span class="cle-note">{note}</span>{/if}{#if action}{@render action()}{/if}</span
			>
		{/if}
	</dd>
</div>

<style>
	.cle {
		min-width: 0;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-bg);
	}

	dt {
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	dd {
		display: grid;
		gap: var(--s-demi);
		margin: var(--s-demi) 0 0;
	}

	.cle-n {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s-demi);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	/* The note reads on its own line under the scale mark, never squeezed into a narrow column. */
	.cle-note {
		flex-basis: 100%;
		overflow-wrap: anywhere;
	}

	/* The grids of the tiles belong with the tile: they only exist around it. */
	:global(dl.cles) {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: var(--s2);
		margin: var(--s5) 0;
	}

	:global(dl.cles.cles-3) {
		grid-template-columns: repeat(3, minmax(0, 1fr));
	}

	/* Two or three tiles fill their panel: four columns left a part of it empty. */
	@media (min-width: 1081px) {
		:global(dl.cles:has(> :nth-child(2):last-child)) {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		:global(dl.cles:has(> :nth-child(3):last-child)) {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	@media (max-width: 1080px) {
		:global(dl.cles:not(.cles-3)) {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (max-width: 720px) {
		:global(dl.cles.cles-3) {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		:global(dl.cles.cles-3 > .cle:last-child) {
			grid-column: 1 / -1;
		}
	}

	@media (max-width: 400px) {
		/* `:not(.cles-3)` repeated: the rule under 1080 px is that specific and would keep two columns. */
		:global(dl.cles:not(.cles-3)),
		:global(dl.cles.cles-3) {
			grid-template-columns: minmax(0, 1fr);
		}

		:global(dl.cles.cles-3 > .cle:last-child) {
			grid-column: auto;
		}
	}

	@media print {
		.cle {
			break-inside: avoid;
		}
	}
</style>
