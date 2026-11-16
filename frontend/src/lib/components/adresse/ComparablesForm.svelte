<script lang="ts">
	// Criteria of the similar sales (UI-SPEC « /adresse/[id] » point 5, « Espacement », D-114, Q6): a
	// GET form whose parameters are the URL (shareable, without JavaScript too), submitted to the
	// anchor of the section. « Type » (radios « Maison » / « Appartement », none checked unless the
	// URL says so), « Surface (m²) » (optional) and « Tolérance », « Rayon », « Période » with the
	// UI-SPEC defaults, then the secondary button « Afficher les ventes ressemblantes ». The other
	// lists of the page keep their place through hidden fields. Fields are 44 px high, one column at
	// ≤ 720 px, four fields on one line at ≥ 1 000 px; the form is hidden when printed. The form
	// filters nothing: the API applies the criteria and serves them.
	import { ORIGINES_PREREMPLISSAGE, PERIODES, RAYONS, TOLERANCES, TYPES, provenanceSurface, type CriteresLus } from '$lib/data/adresse-ventes';

	interface Props {
		criteres: CriteresLus;
		/** Parameters of the other lists kept by a submission (`page`, `g`, `ventes_page`, `vg`). */
		caches?: [string, string][];
		/** C42: where the prefilled criteria come from (the documents of the address), said visibly. */
		origine?: string | null;
		/**
		 * UI-007 (04.5-06): the served document of a prefilled surface, its number and the served
		 * qualification of its selection; `type` when the type is prefilled too. Replaces `origine`.
		 */
		provenance?: { numero: string; qualification: string | null; type: boolean } | null;
	}

	let { criteres, caches = [], origine = null, provenance = null }: Props = $props();

	const surfaceIssue = $derived(provenance ? provenanceSurface(provenance.numero, provenance.qualification) : null);

	const id = $props.id();
</script>

<form class="comparables-form" method="get" action="#ventes-ressemblantes">
	{#each caches as [nom, valeur] (nom)}
		<input type="hidden" name={nom} value={valeur} />
	{/each}
	<div class="comparables-champs">
		<fieldset class="comparables-champ comparables-type">
			<legend>Type</legend>
			<div class="comparables-radios">
				{#each TYPES as [valeur, libelle] (valeur)}
					<label class="comparables-radio">
						<input type="radio" name="type" value={valeur} checked={criteres.type === valeur} />
						<span>{libelle}</span>
					</label>
				{/each}
			</div>
		</fieldset>
		<div class="comparables-champ comparables-surface">
			<div class="comparables-paire">
				<label for={`${id}-surface`}>Surface (m²)</label>
				<input id={`${id}-surface`} type="number" name="surface" inputmode="numeric" min="9" max="1000" step="1" value={criteres.surface} />
			</div>
			<div class="comparables-paire">
				<label for={`${id}-tolerance`}>Tolérance</label>
				<select id={`${id}-tolerance`} name="tolerance">
					{#each TOLERANCES as [valeur, libelle] (valeur)}
						<option value={valeur} selected={criteres.tolerance === valeur}>{libelle}</option>
					{/each}
				</select>
			</div>
		</div>
		<div class="comparables-champ comparables-paire">
			<label for={`${id}-rayon`}>Rayon</label>
			<select id={`${id}-rayon`} name="rayon">
				{#each RAYONS as [valeur, libelle] (valeur)}
					<option value={valeur} selected={criteres.rayon === valeur}>{libelle}</option>
				{/each}
			</select>
		</div>
		<div class="comparables-champ comparables-paire">
			<label for={`${id}-periode`}>Période</label>
			<select id={`${id}-periode`} name="periode">
				{#each PERIODES as [valeur, libelle] (valeur)}
					<option value={valeur} selected={criteres.periode === valeur}>{libelle}</option>
				{/each}
			</select>
		</div>
	</div>
	{#if provenance && surfaceIssue}
		<p class="comparables-origine">
			{#if provenance.type}{ORIGINES_PREREMPLISSAGE.type}{' '}{/if}<a href={surfaceIssue.lien}>{surfaceIssue.texte}</a>{surfaceIssue.suite}
		</p>
	{:else if origine}<p class="comparables-origine">{origine}</p>{/if}
	<button type="submit" class="comparables-bouton">Afficher les ventes ressemblantes</button>
</form>

<style>
	.comparables-form {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s4);
		margin: 0 0 var(--s4);
	}

	/* One column at ≤ 720 px, two up to 999 px, four fields on one line at ≥ 1 000 px. */
	.comparables-champs {
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--s4);
		width: 100%;
	}

	.comparables-champ {
		min-width: 0;
		margin: 0;
		padding: 0;
		border: 0;
	}

	.comparables-champ legend,
	.comparables-paire label {
		display: block;
		margin: 0 0 var(--s2);
		padding: 0;
		font-size: 16px;
		font-weight: 600;
		line-height: 1.5;
	}

	.comparables-radios {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--s4);
	}

	.comparables-radio {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		font-size: 16px;
		line-height: 1.5;
		cursor: pointer;
	}

	.comparables-radio input {
		width: 20px;
		height: 20px;
		margin: 0;
	}

	.comparables-surface {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s4);
	}

	.comparables-surface .comparables-paire {
		flex: 1 1 120px;
		min-width: 0;
	}

	/* Large field (UI-SPEC v4 § 3.13, 4.6): 18 px text, padding --s3 --s4, paper fill, line-strong outline. */
	.comparables-paire input,
	.comparables-paire select {
		box-sizing: border-box;
		width: 100%;
		min-height: 44px;
		padding: var(--s3) var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-weight: 400;
		font-style: inherit;
		font-size: 18px;
		line-height: 1.4;
	}

	.comparables-origine {
		margin: 0;
		max-width: var(--mesure);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.comparables-origine a {
		color: var(--color-ink);
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	/* Secondary button (UI-SPEC v4 § 4.6): line-strong outline, ink text, control radius, 16 px / 600. */
	.comparables-bouton {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-sizing: border-box;
		min-height: 44px;
		padding: 0 var(--s4);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-control);
		background: var(--color-paper);
		color: var(--color-ink);
		font-family: inherit;
		font-style: inherit;
		line-height: inherit;
		font-size: 16px;
		font-weight: 600;
		cursor: pointer;
	}

	.comparables-bouton:hover {
		background: var(--color-soft);
	}

	@media (min-width: 721px) {
		.comparables-champs {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (min-width: 1000px) {
		.comparables-champs {
			grid-template-columns: repeat(4, minmax(0, 1fr));
		}
	}

	@media print {
		.comparables-form {
			display: none;
		}
	}
</style>
