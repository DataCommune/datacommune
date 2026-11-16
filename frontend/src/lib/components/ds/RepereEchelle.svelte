<script lang="ts" module>
	/** The seven A2 scale pictograms of the mockup (`a/pictos/*.svg`, original DataCommune drawings). */
	export type Pictogramme = 'adresse' | 'batiment' | 'parcelle' | 'rayon' | 'carreau' | 'commune' | 'departement';
</script>

<script lang="ts">
	// Scale mark (UI-SPEC v4 § 3.5, D-07): a pictogram drawn inline, always followed by its word, so the
	// drawing never carries the meaning alone. The word is visible, or read by screen readers only in a
	// commune tile whose group title already says it. No native tooltip: it is reachable neither by
	// keyboard nor by touch; the long form of the scale is said in the detail. Presentation only: the
	// table of the served levels is `lib/data/echelles.ts` (04.8-03).
	interface Props {
		/** Absent for a level unknown to this front: the word alone. */
		pictogramme?: Pictogramme;
		mot: string;
		motVisible?: boolean;
		/** 20 px by default; 18 px in a key figure and in the title « Pour toute la commune ». */
		taille?: 18 | 20;
	}

	let { pictogramme, mot, motVisible = true, taille = 20 }: Props = $props();
</script>

<span class="ech">
	{#if pictogramme}
		<!-- Copied from a/pictos/ as static markup: no raw HTML is inserted (T-04.8-06). -->
		<svg
			class="ech-svg"
			width={taille}
			height={taille}
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="1.6"
			stroke-linecap="round"
			stroke-linejoin={pictogramme === 'departement' ? 'miter' : 'round'}
			aria-hidden="true"
			focusable="false"
		>
			{#if pictogramme === 'adresse'}
				<path d="M12 21.5C12 21.5 19 15.3 19 9.8a7 7 0 0 0-14 0c0 5.5 7 11.7 7 11.7z" />
				<circle cx="12" cy="9.8" r="2.6" fill="currentColor" stroke="none" />
			{:else if pictogramme === 'batiment'}
				<rect x="5" y="2.5" width="14" height="19" rx="1" />
				<rect x="9.5" y="14.5" width="5" height="7" fill="currentColor" stroke="none" />
			{:else if pictogramme === 'parcelle'}
				<path d="M5 7.5 19 5.5 18 18.5 6.5 17.5Z" />
				<circle cx="5" cy="7.5" r="1.6" fill="currentColor" stroke="none" />
				<circle cx="19" cy="5.5" r="1.6" fill="currentColor" stroke="none" />
				<circle cx="18" cy="18.5" r="1.6" fill="currentColor" stroke="none" />
				<circle cx="6.5" cy="17.5" r="1.6" fill="currentColor" stroke="none" />
			{:else if pictogramme === 'rayon'}
				<circle cx="12" cy="12" r="9" stroke-dasharray="3.5 2.5" stroke-linecap="butt" />
				<circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
				<path d="M12 12 18.36 5.64" />
			{:else if pictogramme === 'carreau'}
				<rect x="3" y="3" width="18" height="18" rx="1" />
				<path d="M12 3v18M3 12h18" />
				<rect x="4" y="4" width="8" height="8" fill="currentColor" stroke="none" />
			{:else if pictogramme === 'commune'}
				<path d="M2.5 9.5 12 3.5l9.5 6z" />
				<path d="M7.5 9.5v9M12 9.5v9M16.5 9.5v9M3 21h18" />
			{:else}
				<path d="M2.5 6 8 2.5 14.5 4 21.5 2.5 20 10 22 15 18.5 21.5 11.5 19.5 5 22 3 15 4.5 10.5Z" />
			{/if}
		</svg>
	{/if}
	<span class="ech-mot" class:sr={!motVisible}>{mot}</span>
</span>

<style>
	.ech {
		display: inline-flex;
		align-items: center;
		gap: var(--s-demi);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.2;
		/* A long word (« Intercommunalité », « 10 km autour ») wraps in the 120 px column of a row
		   instead of pushing the value (UI-SPEC v4, UI Considerations, long-text). */
		white-space: normal;
		font-family: var(--font-sans);
	}

	/* Never green, which is what clicks: the ink of its category when it stands in one. */
	.ech-svg {
		flex: none;
		color: var(--c-encre, var(--color-muted));
	}

	/* Read by screen readers, not shown (never display: none). */
	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}
</style>
