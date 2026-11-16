<script lang="ts">
	// One category of the address fiche (4.9, decisions of 06/11), in the shell shared with the commune fiche
	// (Categorie): its closed row announces a served result and the counts of the served lists, never two scales
	// added. Inside: the rows by group, then every other row of the category, all in sight (review of 06/11),
	// nothing is nested. The page renders each criterion (`critere`), to bind the state and the lists of the
	// criteria it injects data into.
	import type { Snippet } from 'svelte';
	import type { ResumeServi } from '$lib/api/rapport';
	import Categorie from '$lib/components/fiche/Categorie.svelte';
	import RepereEchelle from '$lib/components/ds/RepereEchelle.svelte';
	import {
		annoncePartie,
		estToutCommunal,
		plusLargesQueLAdresse,
		signauxPartie,
		titreZoneTuiles,
		type ArgsCritere,
		type PartiePlacee
	} from '$lib/data/questions';
	import LigneCritere from './LigneCritere.svelte';

	interface Props {
		partie: PartiePlacee;
		/** Served summary; without it the closed row counts nothing. */
		resume: ResumeServi | null;
		/** Name of the commune as served, said by the sentence on the wider values. */
		commune: string | null;
		/** Above the threshold: a card always open. */
		deuxColonnes?: boolean;
		/** State of the category under the threshold, set by the page for its anchors. */
		ouverte?: boolean;
		/** The reader opened or closed the category (Categorie: a page never binds `ouverte`). */
		onbascule?: (ouverte: boolean) => void;
		critere?: Snippet<[ArgsCritere]>;
		/** Block at the head of the open category. */
		entete?: Snippet;
		/** Block after the rows. */
		pied?: Snippet;
	}
	let { partie, resume, commune, deuxColonnes = false, ouverte = $bindable(false), onbascule, critere, entete, pied }: Props = $props();

	const annonce = $derived(annoncePartie(partie));
	const signaux = $derived(signauxPartie(partie, resume));
	const plusLarges = $derived(plusLargesQueLAdresse(partie));
	const prudence = $derived(titreZoneTuiles(plusLarges, commune));

	// After named groups the rest needs a name of its own: it would read as part of the last group.
	const titreAutres = $derived(partie.groupes.some((groupe) => groupe.titre));
</script>

{#snippet rangee(themes: PartiePlacee['premiers'], niveauTitre: 4 | 5)}
	{#each themes as theme (theme.id)}
		{#if critere}
			{@render critere({ theme, niveauTitre })}
		{:else}
			<LigneCritere {theme} {niveauTitre} />
		{/if}
	{/each}
{/snippet}

<Categorie id={partie.id} titre={partie.titre} {annonce} {signaux} {deuxColonnes} bind:ouverte {onbascule}>
	{@render entete?.()}
	{#if prudence}
		<p class="com-t">
			{#if estToutCommunal(plusLarges)}
				<!-- The drawing is decoration: the sentence says the word, so the mark carries none. -->
				<span class="com-picto" aria-hidden="true"><RepereEchelle pictogramme="commune" mot="" motVisible={false} taille={18} /></span>
			{/if}
			{prudence}
		</p>
	{/if}
	{#each partie.groupes as groupe, i (i)}
		{#if groupe.titre}<h4 class="sous-theme">{groupe.titre}</h4>{/if}
		<ul class="lignes">{@render rangee(groupe.lignes, groupe.titre ? 5 : 4)}</ul>
	{/each}
	{#if partie.autres.length}
		{#if titreAutres}<h4 class="sous-theme">Autres critères</h4>{/if}
		<ul class="lignes" class:lignes-suite={!titreAutres}>{@render rangee(partie.autres, titreAutres ? 5 : 4)}</ul>
	{/if}
	{@render pied?.()}
</Categorie>

<style>
	.sous-theme {
		margin: var(--s4) 0 0;
		color: var(--color-ink);
		font-family: var(--font-display);
		font-size: 18px;
		font-weight: 500;
		line-height: 1.25;
	}

	/* What a value of a wider scale never says, once, above the rows that carry that scale (D-40). */
	.com-t {
		display: flex;
		align-items: center;
		gap: var(--s2);
		max-width: var(--mesure);
		margin: var(--s3) 0 0;
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.com-picto {
		display: inline-flex;
		flex: none;
	}

	.lignes {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.lignes-suite {
		border-top: 1px solid var(--color-filet-pale);
	}
</style>
