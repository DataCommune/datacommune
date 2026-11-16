<script lang="ts">
	// List of the parcels shown (04.10 UI contract § 2.6): the path without the map. A map cannot be read
	// by a screen reader and a parcel can be smaller than a 44 px target: this list is the equivalent of
	// the map (WCAG 2.5.8), not an extra. It says what the map shows (reference, surface, sales when there
	// are some) and a line chooses its parcel, as a touch on the map does.
	//
	// The parcel of the address comes first and says so by a word (T-32); the others follow from the
	// nearest to the farthest from the point of the address (`parcel-view`, no distance shown). The
	// selected line is current (`aria-current`) and checked, never told by a colour alone.
	//
	// Three displays are never merged (SEM-01): no parcel in the view (T-37), the cadastre out of
	// coverage (the state of the glossary in place of the list), and a reading still in flight or a part
	// of the view not read (no « Aucune parcelle », no count that would read as fewer parcels).
	// Counts come from the parcels read, never added up from a guess; a parcel whose sales were not
	// counted makes the count unknown, so neither the count nor the box show.
	import { tick } from 'svelte';
	import type { ContexteCarte, ParcelleCarte } from '$lib/api/carte-parcelles';
	import IconValide from '$lib/components/ds/icons/IconValide.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { LIBELLES_ETATS } from '$lib/data/etats-libelles';
	import { GABARITS, LOT_LISTE, afficherPlus, compteParcelles, compteSansVentes, nombreVentes, referenceLigne, surfaceCadastrale } from '$lib/data/parcel-map';
	import { ordonnerParcelles, ventesComptees } from '$lib/utils/parcel-view';

	interface Props {
		/** The parcels of the view (or, without the map, of the opening bounds), in any order. */
		parcelles: readonly ParcelleCarte[];
		/** Reference of the parcel that contains the point of the address; null: none is. */
		idAdresse: string | null;
		/** Reference of the selected parcel; null: none chosen. */
		selection: string | null;
		/** References of the parcels of the shown group (« Vendue ensemble »). */
		groupe?: readonly string[];
		/** Served context of the map: the point of the address, the period, the state of the cadastre. */
		contexte: ContexteCarte | null;
		/** The parcels of the view are being read. */
		occupe: boolean;
		/** A part of the view was not read (an outage, a rate limit): the list is not the whole view. */
		partielle?: boolean;
		/** The map is not there: the list stands for it, over the opening bounds. */
		sansCarte?: boolean;
		onchoisir: (parcelle: ParcelleCarte) => void;
	}

	let { parcelles, idAdresse, selection, groupe = [], contexte, occupe, partielle = false, sansCarte = false, onchoisir }: Props = $props();

	const titreId = $props.id();
	let seulementVendues = $state(false);
	let affichees = $state(LOT_LISTE);
	let liste = $state<HTMLUListElement>();

	const horsCouverture = $derived(contexte?.parcelles.etat === 'out_of_coverage');
	// Without a served point (no context yet), the parcel of the address first, then by reference.
	const ordonnees = $derived(ordonnerParcelles(parcelles, idAdresse, contexte?.centre ?? { lon: 0, lat: 0 }));
	const vendues = $derived(ventesComptees(parcelles));
	const compte = $derived(
		parcelles.length === 0 || occupe || partielle
			? null
			: vendues === null
				? compteSansVentes(parcelles.length, sansCarte)
				: compteParcelles(parcelles.length, vendues, contexte?.ventes.periode ?? LIBELLES_ETATS.not_reported, sansCarte)
	);
	// The box filters only when every sale of the view was counted.
	const filtrees = $derived(seulementVendues && vendues !== null ? ordonnees.filter((p) => (p.properties.ventes ?? 0) > 0) : ordonnees);
	const visibles = $derived(filtrees.slice(0, affichees));
	const reste = $derived(Math.max(0, filtrees.length - affichees));
	const vide = $derived(parcelles.length === 0 && !occupe && !partielle);
	const dansLeGroupe = $derived(new Set(groupe));

	/** The next batch; the focus goes to its first line, where the reading goes on. */
	async function afficherLaSuite() {
		const premiere = affichees;
		affichees += Math.min(LOT_LISTE, reste);
		await tick();
		liste?.querySelectorAll<HTMLButtonElement>(':scope > li > button')[premiere]?.focus();
	}
</script>

<section class="parcelles" aria-labelledby={titreId} aria-busy={occupe}>
	<div class="parcelles-tete">
		<h3 id={titreId} class="parcelles-titre">{GABARITS['T-28']}</h3>
		{#if !horsCouverture && vendues !== null && !vide}
			<label class="parcelles-filtre">
				<input type="checkbox" bind:checked={seulementVendues} />
				{GABARITS['T-30']}
			</label>
		{/if}
	</div>
	{#if horsCouverture && contexte}
		<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: contexte.parcelles.motif ?? '', effectif: null }} />
	{:else if vide}
		<p class="parcelles-vide">{GABARITS[sansCarte ? 'T-37-v1' : 'T-37']}</p>
	{:else}
		{#if compte}<p class="parcelles-compte">{compte}</p>{/if}
		{#if visibles.length > 0}
			<ul class="parcelles-lignes" bind:this={liste}>
				{#each visibles as p (p.id)}
					{@const choisie = p.id === selection}
					{@const ventes = p.properties.ventes ?? 0}
					<li>
						<button type="button" class="ligne" aria-current={choisie ? 'true' : undefined} onclick={() => onchoisir(p)}>
							<span class="ligne-texte">
								<span class="ligne-l1">
									<span class="ligne-ref">{referenceLigne(p.id)}</span>
									{#if p.id === idAdresse}<span class="ligne-badge">{GABARITS['T-32']}</span>{/if}
									{#if dansLeGroupe.has(p.id)}<span class="ligne-badge">{GABARITS['T-31']}</span>{/if}
								</span>
								<span class="ligne-l2">
									<span>{p.properties.contenance === null ? LIBELLES_ETATS.not_reported : surfaceCadastrale(p.properties.contenance)}</span>
									{#if ventes > 0}
										<span aria-hidden="true">·</span>
										<span>{nombreVentes(ventes)}</span>
									{/if}
								</span>
							</span>
							{#if choisie}<IconValide class="ligne-coche" />{/if}
						</button>
					</li>
				{/each}
			</ul>
		{/if}
		{#if reste > 0}
			<button type="button" class="bouton-secondaire parcelles-suite" onclick={afficherLaSuite}>{afficherPlus(Math.min(LOT_LISTE, reste))}</button>
		{/if}
	{/if}
</section>

<style>
	.parcelles-tete {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--s1) var(--s4);
	}

	.parcelles-titre {
		margin: 0;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
	}

	/* A native box in a row of 44 px: the whole label is the target. */
	.parcelles-filtre {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		font-size: 16px;
		line-height: 1.5;
		cursor: pointer;
	}

	.parcelles-filtre input {
		width: 20px;
		height: 20px;
		margin: 0;
	}

	.parcelles-compte,
	.parcelles-vide {
		max-width: var(--mesure);
		margin: var(--s2) 0 0;
		font-size: 16px;
		font-variant-numeric: tabular-nums;
		line-height: 1.5;
	}

	.parcelles-lignes {
		margin: var(--s2) 0 0;
		padding: 0;
		list-style: none;
	}

	/* A pale rule between two lines, never after the last one. */
	.parcelles-lignes > li + li {
		border-top: 1px solid var(--color-filet-pale);
	}

	/* The whole width, 44 px at least. The radius of a button is never reset (forme-publique.spec.ts). */
	.ligne {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--s2);
		box-sizing: border-box;
		width: 100%;
		min-height: 44px;
		margin: 0;
		padding: var(--s3) var(--s2);
		border: 0;
		background: transparent;
		color: var(--color-ink);
		font-size: 16px;
		line-height: 1.5;
		text-align: left;
		cursor: pointer;
	}

	.ligne:hover {
		background: var(--color-soft);
	}

	.ligne[aria-current='true'] {
		background: var(--color-accent-doux);
	}

	.ligne-texte {
		display: flex;
		flex-direction: column;
		gap: var(--s1);
		min-width: 0;
	}

	.ligne-l1,
	.ligne-l2 {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
		overflow-wrap: anywhere;
	}

	.ligne-ref {
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}

	.ligne-l2 {
		font-size: 14px;
		font-variant-numeric: tabular-nums;
		line-height: 1.4;
	}

	/* The neutral badge of the sale cards: a fact, never a judgement. */
	.ligne-badge {
		display: inline-block;
		padding: 0 var(--s2);
		border: 1px solid var(--color-line-strong);
		border-radius: var(--radius-badge);
		background: var(--color-soft);
		color: var(--color-ink);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.ligne :global(.ligne-coche) {
		flex: none;
		width: 16px;
		height: 16px;
		color: var(--color-accent);
	}

	.parcelles-suite {
		margin-top: var(--s2);
	}
</style>
