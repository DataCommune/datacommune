<script lang="ts">
	// Entry to the comparison of arrondissements (Paris, Lyon, Marseille; asked by the owner on 2026-11-10).
	// The comparator already compares two arrondissements and its search finds them, but no page led there.
	// This is a folded form on the fiche of the commune and on the fiche of each of its arrondissements:
	// one box per arrondissement, two to four of them, then the comparator.
	//
	// A native GET form of `/comparer`: without JavaScript the checked boxes travel as repeated `communes`
	// parameters, which `lireSelection` reads, and a surplus is said by the comparator (« Lien altéré »).
	// With JavaScript the link is written in its usual form (`communes=a,b`), the arrondissement of the
	// fiche first: it is the reference the gaps are read against. Only arrondissements are offered, so a
	// commune is never set against its own part (the 422 of the API).
	//
	// Every visible text here was validated by Ianis on 2026-11-10 (relayed by the orchestrator).
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { arrondissementsDe, rangArrondissement, type CommuneParente } from '$lib/data/arrondissements';
	import { MAX_COMMUNES, lienSelection } from '$lib/data/commune-comparison-selection';

	interface Props {
		/** The commune whose arrondissements are offered. */
		ville: CommuneParente;
		/** The arrondissement of the fiche, checked on arrival; none on the fiche of the commune. */
		courant?: string | null;
	}

	let { ville, courant = null }: Props = $props();

	const id = $props.id();
	const arrondissements = $derived(arrondissementsDe(ville.code));
	const rang = (code: string) => {
		const n = rangArrondissement(code);
		return n === 1 ? '1er' : `${n}e`;
	};

	// The boxes the reader checked while the scripts were loading are kept: `bind:group` would write the
	// initial choice over them at the hydration (the family of R-06). Nothing exists on a client navigation.
	function dejaCoches(): string[] | null {
		if (typeof document === 'undefined') return null;
		const servi = document.getElementById(id);
		return servi ? [...servi.querySelectorAll<HTMLInputElement>('input[name="communes"]:checked')].map((element) => element.value) : null;
	}
	// Read once: the fiche mounts this form again for each commune or arrondissement (`{#key}`).
	let choisis = $state<string[]>(untrack(() => dejaCoches() ?? (courant ? [courant] : [])));
	const plein = $derived(choisis.length >= MAX_COMMUNES);

	// The button is held back only once the page can count the boxes: served enabled, it works without JavaScript.
	let hydrate = $state(false);
	onMount(() => {
		hydrate = true;
	});

	function comparer(event: SubmitEvent) {
		event.preventDefault();
		const ordre = arrondissements.map((arrondissement) => arrondissement.code).filter((code) => choisis.includes(code));
		const codes = courant && ordre.includes(courant) ? [courant, ...ordre.filter((code) => code !== courant)] : ordre;
		if (codes.length < 2) return;
		void goto(lienSelection(codes, false));
	}
</script>

{#if arrondissements.length}
	<details class="depliant depliant-accent choix">
		<summary>Comparer des arrondissements de {ville.nom}</summary>
		<form {id} class="choix-corps" method="GET" action="/comparer" onsubmit={comparer}>
			<p id={`${id}-aide`} class="choix-aide">Choisissez de deux à quatre arrondissements.</p>
			<div class="choix-liste" role="group" aria-label={`Arrondissements de ${ville.nom}`} aria-describedby={`${id}-aide`}>
				{#each arrondissements as arrondissement (arrondissement.code)}
					<label class="choix-case">
						<input
							type="checkbox"
							name="communes"
							value={arrondissement.code}
							bind:group={choisis}
							disabled={plein && !choisis.includes(arrondissement.code)}
						/>
						<span>{rang(arrondissement.code)} <span class="sr-only">arrondissement</span></span>
					</label>
				{/each}
			</div>
			<button type="submit" class="bouton-secondaire" disabled={hydrate && choisis.length < 2}>Comparer</button>
		</form>
	</details>
{/if}

<style>
	.choix {
		margin-top: var(--s2);
	}

	.choix-corps {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s2);
		max-width: 620px;
		margin-top: var(--s1);
		padding: var(--s4);
		border-radius: var(--radius-ligne);
		background: var(--color-panneau);
	}

	.choix-aide {
		margin: 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.choix-liste {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
		gap: 0 var(--s2);
		width: 100%;
	}

	.choix-case {
		display: inline-flex;
		align-items: center;
		gap: var(--s2);
		min-height: 44px;
		font-size: 16px;
		line-height: 1.2;
		font-variant-numeric: tabular-nums;
		cursor: pointer;
	}

	.choix-case input {
		flex: none;
		width: 20px;
		height: 20px;
		margin: 0;
		accent-color: var(--color-accent);
	}

	.choix-case:has(input:disabled) {
		color: var(--color-muted);
		cursor: not-allowed;
	}

	@media print {
		.choix {
			display: none;
		}
	}
</style>
