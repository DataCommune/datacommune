<script lang="ts">
	import { PLANS, createCheckout, getPortalUrl } from '$lib/api/billing';
	import { ApiError } from '$lib/api/client';
	import { getMe } from '$lib/api/auth';
	import { auth } from '$lib/stores/auth.svelte';
	import { page } from '$app/stores';

	let loading = $state<string | null>(null);
	let error = $state('');

	// Check for Stripe callback params
	const sessionId = $derived($page.url.searchParams.get('session_id'));
	const cancelled = $derived($page.url.searchParams.get('cancelled'));

	// A Checkout return asks the server for verified rights; it is not payment proof.
	$effect(() => {
		if (sessionId) {
			(async () => {
				await new Promise(r => setTimeout(r, 2000));
				try {
					const fresh = await getMe();
					auth.setUser(fresh);
				} catch {
					// Silently ignore — user stays on current plan display
				}
			})();
		}
	});

	async function handleCheckout(plan: 'pro' | 'business' | 'enterprise') {
		loading = plan;
		error = '';
		try {
			const { url } = await createCheckout(plan);
			window.location.href = url;
		} catch (err) {
			error = err instanceof ApiError ? err.detail : 'Erreur lors de la redirection';
			loading = null;
		}
	}

	async function handlePortal() {
		loading = 'portal';
		error = '';
		try {
			const { url } = await getPortalUrl();
			window.location.href = url;
		} catch (err) {
			error = err instanceof ApiError ? err.detail : 'Erreur lors de la redirection';
			loading = null;
		}
	}

	function formatQuota(n: number): string {
		if (n >= 1000) return `${(n / 1000).toLocaleString('fr-FR')}K`;
		return n.toLocaleString('fr-FR');
	}
</script>

<svelte:head>
	<title>Abonnement — DataCommune</title>
</svelte:head>

<div class="mx-auto max-w-5xl px-4 py-6 sm:px-6">
	<h1 class="mb-2 font-display text-2xl font-bold text-ink">Abonnement</h1>
	<p class="mb-8 text-sm text-ink-muted">Choisissez le plan adapté à vos besoins</p>
	<p class="mb-6 text-sm text-ink-muted">
		Le quota compte les appels admis, y compris ceux dont le traitement échoue.
		Les refus d'authentification, de débit ou de quota ne sont pas décomptés.
		Le mois suit UTC ; le plafond de débit par IP est partagé entre les clés du même réseau.
	</p>
	{#if auth.user?.plan === 'internal'}
		<p class="mb-6 rounded-lg border border-bronze/30 bg-bronze/5 px-4 py-3 text-sm text-ink">
			Clé API interne — quota mensuel et débit API non plafonnés, gestion réservée à l’exploitation.
		</p>
	{/if}

	{#if sessionId}
		<p class="mb-6 rounded-lg border border-stone-border bg-parchment px-4 py-3 text-sm">
			Retour de Checkout reçu. Vos droits seront actualisés après vérification de votre abonnement.
		</p>
	{/if}

	{#if cancelled}
		<p class="msg-warning mb-6 rounded-lg px-4 py-3 text-sm">
			Paiement annulé. Vous pouvez réessayer à tout moment.
		</p>
	{/if}

	{#if error}
		<p class="mb-6 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">{error}</p>
	{/if}

	{#if auth.user?.plan !== 'internal'}
	<div class="grid gap-6 md:grid-cols-4">
		{#each PLANS as plan}
			<div class="flex flex-col rounded-xl border {plan.key === 'pro' ? 'border-2 border-bronze' : 'border-stone-border'} bg-parchment p-6 relative">
				{#if plan.key === 'pro'}
					<span class="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-bronze px-3 py-1 text-xs font-semibold text-white">
						Recommandé
					</span>
				{/if}

				<h2 class="text-lg font-semibold text-ink">{plan.display_name}</h2>

				<div class="mt-4 mb-2">
					<span class="text-3xl font-bold text-ink">{plan.price_eur}€</span>
					<span class="text-sm text-ink-muted">/mois</span>
				</div>

				<p class="mb-6 text-sm text-ink-muted">
					{formatQuota(plan.quota_monthly)} requêtes/mois
				</p>

				<div class="mt-auto">
					{#if plan.key === (auth.user?.plan ?? 'free')}
						<button
							disabled
							class="w-full rounded-lg border border-bronze px-4 py-2.5 text-sm font-semibold text-bronze"
						>
							Plan actuel
						</button>
					{:else}
						<button
							onclick={() => handleCheckout(plan.key as 'pro' | 'business' | 'enterprise')}
							disabled={loading !== null || plan.key === 'free'}
							class="w-full rounded-lg bg-bronze px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
						>
							{loading === plan.key ? 'Redirection…' : `Passer au ${plan.display_name}`}
						</button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
	{/if}

	<!-- Portal link -->
	{#if auth.user?.plan !== 'internal'}
	<div class="mt-8 text-center">
		<button
			onclick={handlePortal}
			disabled={loading !== null}
			class="text-sm text-ink-muted transition-colors hover:text-bronze"
		>
			{loading === 'portal' ? 'Redirection…' : 'Gérer mon abonnement existant'}
		</button>
	</div>
	{/if}
</div>

<style>
	/* Gold warning text is mixed with ink to preserve contrast on the light background. */
	.msg-warning {
		border: 1px solid color-mix(in srgb, var(--color-gold) 35%, transparent);
		background: color-mix(in srgb, var(--color-gold) 12%, var(--color-marble));
		color: color-mix(in srgb, var(--color-gold) 45%, var(--color-ink) 55%);
	}
</style>
