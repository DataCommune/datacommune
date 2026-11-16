<script lang="ts">
	// Header map of the address fiche: the Plan IGN and the cadastral parcels, two PNG served by our own
	// proxy (CSP `img-src 'self'`, the visitor's browser never contacts the IGN). The point of the address
	// is drawn on top, at the centre of the picture. If either picture fails, the frame goes away and only
	// a short caption stays: an empty frame would look like a broken page.
	//
	// Under the picture, a button opens the interactive map of the parcels (04.10 UI contract § 2.1). It
	// comes with hydration, in a row that keeps its height from the server render; it stays when the
	// picture fails, since the parcels do not depend on the base map. Nothing of the interactive map is
	// in the first view: its dialog module is fetched when the pointer or the focus reaches the button,
	// and mounted on the click (plan 04.10-12); Leaflet itself waits for the dialog.
	import { onMount } from 'svelte';
	import type { CarteRapport } from '$lib/api/rapport';
	import { chargerDialogue } from '$lib/components/adresse/carte/chargeur-dialogue';
	import type ParcelMapDialog from '$lib/components/adresse/carte/ParcelMapDialog.svelte';
	import { GABARITS } from '$lib/data/parcel-map';

	interface Props {
		carte: CarteRapport;
		/** Label of the address, for the alternative text of the plan. */
		adresse: string;
		/** BAN key of the address: the interactive map is asked for it. */
		banId: string;
	}
	let { carte, adresse, banId }: Props = $props();

	let hydratee = $state(false);
	let ouverte = $state(false);
	let Dialogue = $state.raw<typeof ParcelMapDialog | null>(null);
	let dialogueEnEchec = $state(false);
	let bouton = $state<HTMLButtonElement>();

	// The dialog gives the focus back to what had it before; a click does not focus its button in Safari
	// (matrix of 04.10-10), so the button takes it back itself.
	function fermerCarte() {
		ouverte = false;
		bouton?.focus();
	}
	const idEchec = $props.id();

	function prechargerDialogue() {
		// A warm-up that fails is said only if the visitor clicks.
		chargerDialogue().catch(() => {});
	}
	async function ouvrirCarte() {
		try {
			Dialogue = await chargerDialogue();
			dialogueEnEchec = false;
			ouverte = true;
		} catch {
			dialogueEnEchec = true;
		}
	}

	let enErreur = $state(false);
	// Printed only once both pictures are there: a frame still loading would print empty.
	let plan = $state<HTMLImageElement>();
	let parcelles = $state<HTMLImageElement>();
	let planCharge = $state(false);
	let parcellesChargees = $state(false);
	const prete = (i: HTMLImageElement | undefined): boolean => !!i?.complete && i.naturalWidth > 0;
	// A picture loaded before hydration fired its `load` before the handler: read it from its state.
	onMount(() => {
		planCharge = prete(plan);
		parcellesChargees = prete(parcelles);
		hydratee = true;
	});
	// No-break space before « m »: the unit never wraps away from its number.
	const emprise = $derived(`${new Intl.NumberFormat('fr-FR').format(carte.empriseM)} m`);
</script>

<figure class="carte" class:carte-erreur={enErreur} class:carte-chargee={planCharge && parcellesChargees}>
	{#if !enErreur}
		<div class="carte-image">
			<img
				bind:this={plan}
				src={carte.plan}
				alt="Plan du quartier autour du {adresse}, avec les parcelles du cadastre ; le point de l’adresse est au centre."
				width="448"
				height="448"
				loading="eager"
				decoding="async"
				onload={() => (planCharge = true)}
				onerror={() => (enErreur = true)}
			/>
			<img
				class="carte-parcelles"
				bind:this={parcelles}
				src={carte.parcelles}
				alt=""
				width="448"
				height="448"
				loading="eager"
				decoding="async"
				onload={() => (parcellesChargees = true)}
				onerror={() => (enErreur = true)}
			/>
			<svg class="carte-point" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
				<circle class="halo" cx="24" cy="24" r="22" />
				<circle class="halo" cx="24" cy="24" r="14" />
				<circle class="coeur" cx="24" cy="24" r="6" />
			</svg>
		</div>
	{/if}
	<div class="carte-action">
		{#if hydratee}
			<button
				bind:this={bouton}
				type="button"
				class="bouton-secondaire"
				aria-haspopup="dialog"
				aria-describedby={dialogueEnEchec ? idEchec : undefined}
				onpointerenter={prechargerDialogue}
				onfocus={prechargerDialogue}
				onclick={ouvrirCarte}
			>
				{GABARITS['T-01']}
			</button>
			{#if dialogueEnEchec}
				<p role="status" id={idEchec}>{GABARITS['T-43']}</p>
			{/if}
		{/if}
	</div>
	{#if !enErreur}
		<figcaption>
			Plan IGN et parcelles du cadastre, environ {emprise} de large. {carte.attribution}, {carte.licence}.
			{#if carte.geoportail}<a href={carte.geoportail} rel="noopener noreferrer">Ouvrir dans le Géoportail</a>{/if}
		</figcaption>
	{:else}
		<figcaption>
			Carte indisponible.
			{#if carte.geoportail}<a href={carte.geoportail} rel="noopener noreferrer">Ouvrir dans le Géoportail</a>{/if}
		</figcaption>
	{/if}
</figure>
{#if ouverte && Dialogue}
	<Dialogue {banId} {adresse} onclose={fermerCarte} />
{/if}

<style>
	/* v4 frame (UI-SPEC v4 § 4.7): the outline and the tile radius belong to the picture, the caption
	   sits under it, outside. The column width (400 px, 560 px at most under 1080 px, full width under
	   720 px) is the page's. */
	.carte {
		margin: 0;
	}
	.carte-image {
		position: relative;
		aspect-ratio: 5 / 3;
		overflow: hidden;
		border: 1px solid var(--color-line);
		border-radius: var(--radius-tuile);
		background: var(--color-soft);
	}
	.carte-image img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		filter: grayscale(1) contrast(0.92);
	}
	.carte-image .carte-parcelles {
		opacity: 0.62;
	}
	.carte-point {
		position: absolute;
		left: 50%;
		top: 50%;
		width: 48px;
		height: 48px;
		transform: translate(-50%, -50%);
		overflow: visible;
	}
	.carte-point .halo {
		fill: none;
		stroke: var(--color-accent);
		stroke-width: 2;
	}
	.carte-point .coeur {
		fill: var(--color-accent);
		stroke: var(--color-paper);
		stroke-width: 3;
	}
	.carte figcaption {
		margin-top: var(--s-demi);
		font-size: 14px;
		line-height: 1.4;
		color: var(--color-muted);
	}
	/* The row of the button is there from the server render, 44 px high: the button that comes with
	   hydration moves nothing. */
	.carte-action {
		display: flex;
		align-items: flex-start;
		flex-wrap: wrap;
		gap: var(--s2);
		min-height: 44px;
		margin-top: var(--s2);
	}
	/* T-43, under the button when the dialog module could not be fetched. */
	.carte-action p {
		flex-basis: 100%;
		margin: 0;
	}
	/* Without a frame, the failed card is its button and a bare line, not a box (the page drops its
	   column with `:has(.carte-erreur)`). */
	.carte-erreur .carte-action {
		margin-top: 0;
	}
	@media print {
		.carte-action {
			display: none;
		}
		/* 60 % of the width on paper (UI-SPEC v4 § 2.5). */
		.carte {
			break-inside: avoid;
			filter: grayscale(1);
			max-width: 60%;
		}
		.carte-erreur,
		.carte:not(.carte-chargee) {
			display: none;
		}
	}
</style>
