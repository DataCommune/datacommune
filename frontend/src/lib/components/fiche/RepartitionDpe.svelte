<script lang="ts">
	// Distribution of the DPE classes of a commune (04.7-15 T2, T3; a tile of « L'essentiel » since
	// 04.8-04, UI-SPEC v4 § 6.2): horizontal bars A→G, each with
	// its letter and its served count, then the reading sentence. The colours are the global DPE
	// classes of the contract (`.dpe-A` … `.dpe-G`, app.css), never an inline colour: the same scale
	// on every page. The letter and the count are always written, the colour never carries the
	// meaning alone (the pale scale has close luminances). Renders the model, computes nothing.
	import type { RepartitionDpe } from '$lib/data/commune-repartition-dpe';

	let { repartition }: { repartition: RepartitionDpe } = $props();
	const titreId = $props.id();
</script>

<figure class="repartition" aria-labelledby={titreId} data-repartition-dpe>
	<h3 id={titreId} class="repartition-titre">Répartition des DPE par classe</h3>
	<!-- role="list": a list without bullets keeps its semantics in Safari/VoiceOver. -->
	<ul class="repartition-barres" role="list">
		{#each repartition.barres as barre (barre.classe)}
			<li class="repartition-ligne" data-classe={barre.classe}>
				<span class="repartition-lettre">{barre.classe}</span>
				<span class="repartition-piste" aria-hidden="true">
					<span class="repartition-barre dpe-{barre.classe}" style:width={`${barre.longueur * 100}%`}></span>
				</span>
				<span class="repartition-effectif chiffre">{barre.texte}<span class="sr-only">{barre.effectif === 1 ? ' diagnostic' : ' diagnostics'}</span></span>
			</li>
		{/each}
	</ul>
	<figcaption class="repartition-lecture">
		<p data-lecture>{repartition.lecture}</p>
		{#if repartition.sansClasse}<p class="repartition-note">{repartition.sansClasse}</p>{/if}
		{#if repartition.bornes}<p class="repartition-note">{repartition.bornes}</p>{/if}
		<p class="repartition-note">Un DPE est un document, pas un logement&nbsp;: ces diagnostics ne représentent pas un décompte de logements.</p>
	</figcaption>
</figure>

<style>
	.repartition {
		margin: 0;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-bg);
		color: var(--color-ink);
	}

	.repartition-titre {
		margin: 0 0 var(--s4);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.5;
	}

	.repartition-barres {
		display: flex;
		flex-direction: column;
		gap: var(--s1);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.repartition-ligne {
		display: grid;
		grid-template-columns: 24px minmax(0, 1fr) auto;
		align-items: center;
		gap: var(--s2);
		font-size: 16px;
		line-height: 1.5;
	}

	.repartition-lettre {
		font-weight: 600;
		text-align: center;
	}

	.repartition-piste {
		display: block;
		height: 20px;
		border-radius: var(--radius-badge);
		background: var(--color-soft);
	}

	/* The bar is the contract's class colour; a zero count keeps an empty track, never a sliver. */
	.repartition-barre {
		display: block;
		height: 100%;
		border-radius: var(--radius-badge);
	}

	.repartition-effectif {
		min-width: 4ch;
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.repartition-lecture {
		margin-top: var(--s4);
	}

	.repartition-lecture p {
		max-width: var(--mesure);
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.repartition-lecture .repartition-note {
		margin-top: var(--s1);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	@media (max-width: 720px) {
		.repartition {
			padding: var(--s3);
		}
	}

	@media print {
		.repartition-barre {
			print-color-adjust: exact;
		}
	}
</style>
