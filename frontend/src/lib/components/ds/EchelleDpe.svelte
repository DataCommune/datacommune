<script lang="ts" module>
	export type ClasseDpe = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G';
	export interface CaseDpe {
		classe: ClasseDpe;
		/** Served count of documents of the class; null when the source does not serve it. */
		n?: number | null;
	}
</script>

<script lang="ts">
	// A to G scale of the DPE (UI-SPEC v4 § 3.13, `dc-dpe` of the mockup): one box per class in its
	// colour, the letter then the served count. The current class, when there is one, is marked by
	// `aria-current` and an inner ink outline; the colour never carries it alone, the list is named
	// after it. Counts are documents, never dwellings (§ 5, rule 7).
	interface Props {
		cases: readonly CaseDpe[];
		/** Class of the document or of the address, if any. */
		active?: ClasseDpe | null;
		/** 38 px boxes on the DPE page, 34 px elsewhere. */
		grande?: boolean;
	}

	let { cases, active = null, grande = false }: Props = $props();

	const nom = $derived(`Classes A à G${active ? `, classe ${active}` : ''}`);
</script>

<ol class="echelle-dpe" class:echelle-dpe-grande={grande} aria-label={nom}>
	{#each cases as c (c.classe)}
		<li class="dpe-{c.classe}" class:active={c.classe === active} aria-current={c.classe === active ? 'true' : undefined}>
			<!-- Read « Classe A : 3 DPE » (or « non servie »); the boxes show the letter and the count. -->
			<span class="lettre"><span class="sr">Classe </span>{c.classe}</span>
			{#if c.n !== undefined}<span class="n"
					><span class="sr">&nbsp;: </span>{#if c.n === null}<span aria-hidden="true">—</span><span class="sr">non servie</span
						>{:else}{c.n}<span class="sr">&nbsp;DPE</span>{/if}</span
				>{/if}
		</li>
	{/each}
</ol>

<style>
	.echelle-dpe {
		display: flex;
		gap: var(--s-fin);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	/* Relative: the box of the visually hidden words stays inside its class. */
	li {
		position: relative;
		display: flex;
		flex-direction: column;
		align-items: center;
		min-width: 34px;
		padding: var(--s1) var(--s-demi);
		color: var(--color-dpe-ink);
		font-size: 14px;
		line-height: 1.2;
	}

	.echelle-dpe-grande li {
		min-width: 38px;
	}

	li.active {
		outline: 2px solid var(--color-ink);
		outline-offset: -2px;
	}

	.lettre {
		font-weight: 600;
	}

	.sr {
		position: absolute;
		width: 1px;
		height: 1px;
		overflow: hidden;
		clip: rect(0 0 0 0);
		white-space: nowrap;
	}

	.dpe-A {
		background: var(--color-dpe-a);
		color: var(--color-dpe-sur-a);
	}
	.dpe-B {
		background: var(--color-dpe-b);
	}
	.dpe-C {
		background: var(--color-dpe-c);
	}
	.dpe-D {
		background: var(--color-dpe-d);
	}
	.dpe-E {
		background: var(--color-dpe-e);
	}
	.dpe-F {
		background: var(--color-dpe-f);
	}
	.dpe-G {
		background: var(--color-dpe-g);
		color: var(--color-dpe-sur-g);
	}

	@media (max-width: 720px) {
		li {
			flex: 1;
			min-width: 0;
		}
	}

	@media print {
		li {
			print-color-adjust: exact;
			-webkit-print-color-adjust: exact;
		}
	}
</style>
