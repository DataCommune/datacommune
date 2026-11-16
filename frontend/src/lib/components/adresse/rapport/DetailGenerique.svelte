<script lang="ts">
	// Generic rendering of a theme's details (04.7-03; v4 form, UI-SPEC v4 § 3.3, § 3.13): labelled
	// values, lists, tables, groups and the DPE distribution, from `aplatirDetails`. A theme the front
	// does not know renders here without any change; masked prices and nulls never reach this component.
	import EchelleDpe, { type CaseDpe } from '$lib/components/ds/EchelleDpe.svelte';
	import type { NoeudDetail } from '$lib/data/adresse-rapport';
	import DetailGenerique from './DetailGenerique.svelte';

	interface Props {
		noeuds: NoeudDetail[];
	}
	let { noeuds }: Props = $props();
	// On paper a long table keeps its first rows and says so (UI-SPEC v3 § 12).
	const LIGNES_IMPRIMEES = 10;

	type Bloc = { type: 'valeurs'; items: Extract<NoeudDetail, { type: 'valeur' }>[] } | Exclude<NoeudDetail, { type: 'valeur' }>;
	// Consecutive values form one definition list.
	const blocs = $derived(
		noeuds.reduce<Bloc[]>((acc, n) => {
			const dernier = acc.at(-1);
			if (n.type === 'valeur') {
				if (dernier?.type === 'valeurs') dernier.items.push(n);
				else acc.push({ type: 'valeurs', items: [n] });
			} else acc.push(n);
			return acc;
		}, [])
	);

	// `aplatirDetails` serves the seven classes A to G in order.
	const casesDpe = (classes: { classe: string; n: number | null }[]): CaseDpe[] =>
		classes.map((c) => ({ classe: c.classe as CaseDpe['classe'], n: c.n }));
</script>

{#each blocs as bloc, i (i)}
	{#if bloc.type === 'valeurs'}
		<dl class="valeurs">
			{#each bloc.items as item, j (j)}
				<div><dt>{item.libelle}</dt><dd>{item.valeur}</dd></div>
			{/each}
		</dl>
	{:else if bloc.type === 'liste'}
		<div class="bloc">
			<p class="libelle">{bloc.libelle}</p>
			<ul class="liste">
				{#each bloc.items as item, j (j)}<li>{item}</li>{/each}
			</ul>
		</div>
	{:else if bloc.type === 'table'}
		<div class="bloc">
			<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access is required for the scroll region) -->
			<div class="defilement" role="region" tabindex="0" aria-label={bloc.libelle}>
				<table>
					<caption>{bloc.libelle}</caption>
					<thead>
						<tr>{#each bloc.colonnes as c, j (j)}<th scope="col">{c}</th>{/each}</tr>
					</thead>
					<tbody>
						{#each bloc.lignes as ligne, j (j)}
							<tr class:au-dela={j >= LIGNES_IMPRIMEES}>{#each ligne as cellule, k (k)}<td>{cellule}</td>{/each}</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if bloc.note}<p class="note">{bloc.note}</p>{/if}
			{#if bloc.lignes.length > LIGNES_IMPRIMEES}
				<p class="note note-impression">Sur papier, les {LIGNES_IMPRIMEES} premières lignes&#8239;; la liste complète est sur la fiche en ligne.</p>
			{/if}
		</div>
	{:else if bloc.type === 'repartition_dpe'}
		<div class="bloc">
			<p class="libelle">{bloc.libelle}</p>
			<EchelleDpe cases={casesDpe(bloc.classes)} />
			{#if bloc.inconnus}<p class="note">Sans classe&nbsp;: {bloc.inconnus}&nbsp;DPE.</p>{/if}
		</div>
	{:else if bloc.type === 'groupe'}
		<div class="bloc groupe">
			<p class="libelle">{bloc.libelle}</p>
			<DetailGenerique noeuds={bloc.enfants} />
		</div>
	{/if}
{/each}

<style>
	/* `dc-dl` of the mockup: label and value side by side, a pale line under each pair. */
	.valeurs {
		display: grid;
		gap: var(--s-demi);
		margin: 0;
		font-size: 14px;
	}

	.valeurs div {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		gap: var(--s3);
		padding-bottom: var(--s-demi);
		border-bottom: 1px solid var(--color-filet-pale);
	}

	dt {
		margin: 0;
		color: var(--color-muted);
	}

	dd {
		margin: 0;
		color: var(--color-ink);
		overflow-wrap: anywhere;
	}

	.libelle {
		margin: 0 0 var(--s-demi);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
	}

	.bloc {
		min-width: 0;
	}

	.groupe {
		padding-left: var(--s3);
		border-left: 1px solid var(--color-line);
	}

	.liste {
		display: grid;
		gap: var(--s-fin);
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: 14px;
		columns: 2 260px;
		column-gap: var(--s6);
	}

	.liste li {
		break-inside: avoid;
	}

	/* Detail tables (UI-SPEC v4 § 3.13, `dc-table`). */
	.defilement {
		max-width: 100%;
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 14px;
	}

	caption {
		padding-bottom: var(--s-demi);
		text-align: left;
		font-weight: 600;
	}

	th,
	td {
		padding: var(--s-demi) var(--s3) var(--s-demi) 0;
		border-bottom: 1px solid var(--color-filet-pale);
		text-align: left;
		vertical-align: top;
	}

	thead th {
		border-bottom-color: var(--color-line);
		color: var(--color-muted);
		font-weight: 600;
		white-space: nowrap;
	}

	.note {
		max-width: var(--mesure);
		margin: var(--s-demi) 0 0;
		color: var(--color-muted);
		font-size: 14px;
	}

	.note-impression {
		display: none;
	}

	@media print {
		.defilement {
			overflow: visible;
		}

		table {
			table-layout: fixed;
		}

		th,
		td {
			overflow-wrap: anywhere;
		}

		thead th {
			white-space: normal;
		}

		.au-dela {
			display: none;
		}

		.note-impression {
			display: block;
		}

		thead {
			display: table-header-group;
		}

		tr {
			break-inside: avoid;
		}
	}
</style>
