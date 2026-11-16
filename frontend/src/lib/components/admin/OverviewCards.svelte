<script lang="ts">
	import type { AdminOverview } from '$lib/api/admin-overview';
	import BlocKpi from '$lib/components/admin/BlocKpi.svelte';

	interface Props {
		data: AdminOverview;
	}

	let { data }: Props = $props();

	function fmt(n: number): string {
		return n.toLocaleString('fr-FR');
	}

	// Motif etiquette/valeur repete 4 fois a l'identique -- extrait en admin/BlocKpi (D-07, plan
	// 56-05). Variante "bordee" : ces cartes sont des objets d'un tableau de bord admin, pas du
	// contenu de lecture (registre document), d'ou la carte bordee plutot que le filet.
	let cartes = $derived([
		{ libelle: 'Utilisateurs', valeur: fmt(data.users_count) },
		{ libelle: 'Clés actives', valeur: fmt(data.active_keys_count) },
		{ libelle: 'MRR', valeur: 'Indisponible' },
		{ libelle: 'Req/24h', valeur: fmt(data.requests_24h) }
	]);
</script>

<div class="grid grid-cols-2 gap-4 md:grid-cols-4">
	{#each cartes as carte (carte.libelle)}
		<BlocKpi libelle={carte.libelle} valeur={carte.valeur} variante="bordee" />
	{/each}
</div>
<p class="mt-3 text-sm text-ink-muted">
	Le MRR ne peut pas être calculé : les montants et la cadence de facturation ne sont pas
	enregistrés. Les accès gratuits et les prix catalogue ne constituent pas un revenu.
	Cet indicateur ne mesure pas les encaissements.
</p>
