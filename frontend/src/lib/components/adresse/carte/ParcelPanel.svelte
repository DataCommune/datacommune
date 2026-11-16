<script lang="ts">
	// Panel of the selected parcel, under the map (04.10 UI contract § 2.5). No frame of its own: the
	// dialog is already the panel. It says what identifies the parcel, its reference and its served
	// surface; an absent surface is the state of the glossary (`StateValue`), never « 0 m² ».
	// A parcel is not an address: the badge marks the one that contains the point of the address.
	//
	// Its sales (04.10-07) are read by the browser once the parcel is chosen, never written in the served
	// page of the fiche. They are the cards of the fiche (`VenteList`) with its price rule, five per page;
	// the page is held here, so the pagination is the button variant of `Pagination`. Reading, zero,
	// out of coverage, outage and rate limit are five displays, never merged (SEM-01). A reading still in
	// flight when another parcel is chosen is dropped: the sales of a former parcel never show.
	//
	// A sale of several parcels says so at the end of its card (snippet `pied` of `VenteList`): the price
	// reserve under a displayed price, the other parcels, and a toggle that tells the map which group to
	// show (`ongroupe`). One group at a time; another parcel, page or reading turns it off. Every count
	// and the total surface are served: the panel draws nothing and adds nothing up.
	import { tick, untrack } from 'svelte';
	import type { CarteVente } from '$lib/api/adresses';
	import {
		lireVentesParcelle,
		type ContexteCarte,
		type GroupeVendu,
		type ParcelleCarte,
		type VentesParcelleCarte
	} from '$lib/api/carte-parcelles';
	import Pagination from '$lib/components/adresse/Pagination.svelte';
	import VenteList from '$lib/components/adresse/VenteList.svelte';
	import StateValue from '$lib/components/fiche/StateValue.svelte';
	import { MESSAGE_PANNE_COMPARABLES, RECHARGER_VENTES, TITRE_VENTES, phraseZeroVentes, plageVentes } from '$lib/data/adresse-ventes';
	import { moisAnnee } from '$lib/data/adresse-fiche';
	import { LIBELLES_ETATS } from '$lib/data/etats-libelles';
	import { messageLimite } from '$lib/data/panne';
	import {
		GABARITS,
		groupeTropGrand,
		horsDeLaVue,
		horsDeLaZone,
		ligneGroupe,
		ligneSourceVentes,
		prixDeLaVente,
		referenceCadastrale,
		reserveZero,
		surfaceCadastrale,
		surfaceIncomplete,
		surfaceSansValeur,
		tropLoin,
		titreParcelle,
		venduAvec
	} from '$lib/data/parcel-map';

	interface Props {
		/** BAN key of the fiche: the sales of a parcel are asked for this address (04.10-07). */
		banId: string;
		/** The selected parcel; null: none chosen yet. */
		parcelle: ParcelleCarte | null;
		/** The selected parcel is the one that contains the point of the address. */
		estAdresse: boolean;
		/** Served context of the map: the period and the states of the sales (04.10-07). */
		contexte: ContexteCarte | null;
		/** Told the group of a sale to show on the map, or null when none is shown any more. */
		ongroupe?: (groupe: GroupeVendu | null) => void;
		/** Parcels of the shown group the map leaves out of the view, counted by the map; null: not counted. */
		horsVue?: number | null;
		/** The map is not there (04.10-09): a parcel is chosen in the list only. */
		sansCarte?: boolean;
	}

	let { banId, parcelle, estAdresse, ongroupe, horsVue = null, sansCarte = false }: Props = $props();

	const titreId = $props.id();
	const ventesId = `${titreId}-ventes`;
	const SURFACE_ABSENTE = { kind: 'absence', etat: 'not_reported', motif: '', effectif: null } as const;

	/** What the sales section shows: reading, a served page, the rate limit or an outage. */
	type EtatVentes =
		| { kind: 'lecture' }
		| { kind: 'lue'; donnees: VentesParcelleCarte }
		| { kind: 'limite'; retryAfter: number | null }
		| { kind: 'panne' };

	let ventes = $state.raw<EtatVentes>({ kind: 'lecture' });
	/** A page change in flight: the cards read so far stay, the section is busy. */
	let changement = $state(false);
	/** The served range of a page reached by the pagination, for screen readers. */
	let annonce = $state('');
	let titre = $state<HTMLHeadingElement>();
	let lecture: AbortController | null = null;
	/** Rank, in the page, of the sale whose group is shown; null: none. */
	let montre = $state<number | null>(null);

	function eteindre() {
		if (montre === null) return;
		montre = null;
		ongroupe?.(null);
	}

	function basculer(rang: number, groupe: GroupeVendu) {
		if (montre === rang) return eteindre();
		montre = rang;
		ongroupe?.(groupe);
	}

	/**
	 * Reads page *numero* of the sales of *id*. A first read replaces what was shown by T-06; a page
	 * change keeps the cards until the next page comes. Only the last reading asked is ever shown.
	 */
	async function lire(id: string, numero: number, vg: string | null, versPage: boolean) {
		lecture?.abort();
		const controleur = new AbortController();
		lecture = controleur;
		annonce = '';
		// The shown group belongs to the cards read so far: another page or parcel turns it off.
		eteindre();
		if (versPage) changement = true;
		else ventes = { kind: 'lecture' };
		const issue = await lireVentesParcelle(banId, id, { page: numero, vg }, { signal: controleur.signal });
		if (controleur.signal.aborted || lecture !== controleur) return;
		lecture = null;
		changement = false;
		if (issue.kind === 'page_invalide') {
			// A page gone or a generation changed: back to the first page, read again. On the first page
			// itself, the answer cannot be read: an outage, never a loop.
			if (numero !== 1 || vg !== null) return lire(id, 1, null, versPage);
			ventes = { kind: 'panne' };
			return;
		}
		if (issue.kind === 'ok') ventes = { kind: 'lue', donnees: issue.donnees };
		else if (issue.kind === 'limite') ventes = { kind: 'limite', retryAfter: issue.retryAfter };
		else ventes = { kind: 'panne' };
		if (versPage && issue.kind === 'ok') {
			await tick();
			annonce = plage ?? '';
			titre?.focus();
		}
	}

	// Values, not objects: the same parcel served again by another tile, or props given again unchanged,
	// are not a new choice.
	const idChoisi = $derived(parcelle?.id ?? null);
	const adresseChoisie = $derived(banId);

	// A new parcel (or a new address): its first page, the former reading dropped.
	$effect(() => {
		const id = idChoisi;
		void adresseChoisie;
		if (!id) {
			untrack(eteindre);
			return;
		}
		untrack(() => lire(id, 1, null, false));
		return () => {
			lecture?.abort();
			lecture = null;
			changement = false;
		};
	});

	const lues = $derived(ventes.kind === 'lue' ? ventes.donnees : null);
	const plage = $derived(lues ? plageVentes(lues, lues.total) : null);
	const pages = $derived(lues?.pages ?? 1);
	const occupee = $derived(ventes.kind === 'lecture' || changement);

	function changerDePage(sens: -1 | 1) {
		if (!parcelle || !lues || changement) return;
		void lire(parcelle.id, lues.page + sens, lues.generation, true);
	}

	function recharger() {
		if (parcelle) void lire(parcelle.id, 1, null, false);
	}
</script>

{#snippet piedVente(carte: CarteVente, rang: number)}
	{@const groupe = lues?.groupes[rang] ?? null}
	{#if groupe}
		<div class="vente-groupe">
			{#if carte.prix_affiche && carte.prix !== null}<p class="vente-groupe-reserve">{prixDeLaVente(groupe.total)}</p>{/if}
			<p>{venduAvec(groupe.autres)}</p>
			{#if groupe.parcelles}
				<button type="button" class="bouton-lien" aria-pressed={montre === rang} onclick={() => basculer(rang, groupe)}>{GABARITS['T-22']}</button>
			{:else}
				<p class="vente-groupe-reserve">{groupeTropGrand(groupe.total)}</p>
			{/if}
			{#if montre === rang}
				<p class="vente-groupe-ligne">
					{ligneGroupe(groupe.total, moisAnnee(carte.date) ?? LIBELLES_ETATS.not_reported, groupe.surface_totale_m2)}
				</p>
				<p class="vente-groupe-reserve">{GABARITS['T-24']}</p>
				{#if groupe.surface_totale_m2 === null && groupe.absentes_du_cadastre > 0}
					<p class="vente-groupe-reserve">{surfaceIncomplete(groupe.absentes_du_cadastre)}</p>
				{:else if groupe.surface_totale_m2 === null && groupe.sans_surface !== null && groupe.sans_surface > 0}
					<p class="vente-groupe-reserve">{surfaceSansValeur(groupe.sans_surface)}</p>
				{/if}
				{#if groupe.hors_emprise > 0}<p class="vente-groupe-reserve">{tropLoin(groupe.hors_emprise)}</p>{/if}
				{#if horsVue !== null && horsVue > 0}
					<p class="vente-groupe-reserve">{sansCarte ? horsDeLaZone(horsVue) : horsDeLaVue(horsVue)}</p>
				{/if}
			{/if}
		</div>
	{/if}
{/snippet}

{#if parcelle}
	<section class="parcelle" aria-labelledby={titreId}>
		<div class="parcelle-tete">
			<h3 id={titreId} class="parcelle-titre" tabindex="-1" bind:this={titre}>{titreParcelle(parcelle.id)}</h3>
			{#if estAdresse}<span class="parcelle-badge">{GABARITS['T-16']}</span>{/if}
		</div>
		<dl class="parcelle-faits">
			<div>
				<dt>{GABARITS['T-17']}</dt>
				<dd>
					{#if parcelle.properties.contenance === null}
						<StateValue etat={SURFACE_ABSENTE} aide={false} />
					{:else}
						{surfaceCadastrale(parcelle.properties.contenance)}
					{/if}
				</dd>
			</div>
			<div>
				<dt>{GABARITS['T-18']}</dt>
				<dd>{referenceCadastrale(parcelle.id)}</dd>
			</div>
		</dl>
		<section class="parcelle-ventes" aria-labelledby={ventesId} aria-busy={occupee}>
			<h4 id={ventesId} class="parcelle-ventes-titre">{TITRE_VENTES}</h4>
			<p class="parcelle-ventes-annonce sr-only" role="status">{annonce}</p>
			{#if ventes.kind === 'lecture'}
				<p class="parcelle-ventes-texte">{GABARITS['T-06']}</p>
			{:else if ventes.kind === 'lue' && ventes.donnees.etat === 'out_of_coverage'}
				<StateValue etat={{ kind: 'absence', etat: 'out_of_coverage', motif: ventes.donnees.motif ?? '', effectif: null }} />
			{:else if ventes.kind === 'lue'}
				{@const donnees = ventes.donnees}
				<p class="parcelle-ventes-source">{ligneSourceVentes(donnees.periode)}</p>
				{#if donnees.total === 0}
					<p class="parcelle-ventes-zero">{phraseZeroVentes(donnees.periode)}</p>
					{#if donnees.annee_debut !== null}<p class="parcelle-ventes-zero">{reserveZero(donnees.annee_debut)}</p>{/if}
				{:else}
					<VenteList ventes={donnees.ventes} pied={piedVente} />
					<Pagination
						{plage}
						{pages}
						lienPrecedent={null}
						lienSuivant={null}
						etiquette="Pages des ventes publiées sur la parcelle"
						prefixe="parcelle-ventes"
						onprecedent={donnees.page > 1 ? () => changerDePage(-1) : undefined}
						onsuivant={donnees.page < pages ? () => changerDePage(1) : undefined}
					/>
				{/if}
			{:else}
				<div class="parcelle-ventes-panne" role="alert">
					<p>{ventes.kind === 'limite' ? messageLimite(ventes.retryAfter) : MESSAGE_PANNE_COMPARABLES}</p>
					<button type="button" class="bouton-secondaire" onclick={recharger}>{RECHARGER_VENTES}</button>
				</div>
			{/if}
		</section>
	</section>
{:else}
	<section class="parcelle">
		<p class="parcelle-vide">{GABARITS[sansCarte ? 'T-35-v1' : 'T-35']}</p>
	</section>
{/if}

<style>
	.parcelle-tete {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--s1) var(--s2);
	}

	.parcelle-titre {
		margin: 0;
		font-size: 20px;
		font-weight: 600;
		line-height: 1.2;
	}

	/* A landing point of the focus when the parcel is chosen in the list, not a control. */
	.parcelle-titre:focus {
		outline: none;
	}

	/* The neutral badge of the sale cards: a fact, never a judgement. */
	.parcelle-badge {
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

	.parcelle-faits {
		margin: var(--s3) 0 0;
	}

	.parcelle-faits > div {
		padding: var(--s2) 0;
	}

	.parcelle-faits > div + div {
		border-top: 1px solid var(--color-filet-pale);
	}

	.parcelle-faits dt {
		color: var(--color-muted);
		font-size: 14px;
		font-weight: 600;
		line-height: 1.4;
	}

	.parcelle-faits dd {
		margin: 0;
		font-size: 16px;
		font-variant-numeric: tabular-nums;
		line-height: 1.5;
		overflow-wrap: anywhere;
	}

	.parcelle-vide {
		max-width: var(--mesure);
		margin: 0;
	}

	/* The sales of the parcel; positioned, so its visually hidden announcement stays inside it. */
	.parcelle-ventes {
		position: relative;
		margin-top: var(--s4);
	}

	.parcelle-ventes-titre {
		margin: 0 0 var(--s2);
		font-size: 16px;
		font-weight: 600;
		line-height: 1.4;
	}

	/* The source line of the fiche (`.ventes-commune`), as is. */
	.parcelle-ventes-source {
		margin: 0 0 var(--s4);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.parcelle-ventes-texte,
	.parcelle-ventes-zero {
		max-width: var(--mesure);
		margin: 0;
		font-size: 16px;
		line-height: 1.5;
	}

	.parcelle-ventes-zero + .parcelle-ventes-zero {
		margin-top: var(--s1);
		color: var(--color-muted);
		font-size: 14px;
		line-height: 1.4;
	}

	.parcelle-ventes-panne {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s2);
	}

	.parcelle-ventes-panne p {
		max-width: var(--mesure);
		margin: 0;
	}

	/* The foot of a card of a sale of several parcels: 14 px, the shown group in 16 px, reserves muted. */
	.vente-groupe {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s1);
		font-size: 14px;
		line-height: 1.4;
		overflow-wrap: anywhere;
	}

	.vente-groupe p {
		margin: 0;
	}

	.vente-groupe-ligne {
		font-size: 16px;
		font-variant-numeric: tabular-nums;
		line-height: 1.5;
	}

	.vente-groupe-reserve {
		color: var(--color-muted);
	}
</style>
