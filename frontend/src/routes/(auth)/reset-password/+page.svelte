<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { resetPassword } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import IconRefuse from '$lib/components/ds/icons/IconRefuse.svelte';

	let password = $state('');
	let confirm = $state('');
	let error = $state('');
	let submitting = $state(false);

	let token = $derived($page.url.searchParams.get('token') ?? '');

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== confirm) {
			error = 'Les mots de passe ne correspondent pas';
			return;
		}

		if (!token) {
			error = 'Lien de réinitialisation invalide';
			return;
		}

		submitting = true;

		try {
			await resetPassword(token, password);
			goto('/login?reset=1');
		} catch (err) {
			if (err instanceof ApiError) {
				error = err.detail;
			} else {
				error = 'Erreur lors de la réinitialisation';
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Nouveau mot de passe — DataCommune</title>
</svelte:head>

{#if !token}
	<div class="rounded-xl border border-stone-border bg-parchment p-6 text-center">
		<div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-terracotta/10">
			<IconRefuse class="h-6 w-6 text-terracotta" />
		</div>
		<h1 class="text-2xl font-semibold mb-2">Lien invalide</h1>
		<p class="text-sm text-ink-muted mb-6">Ce lien de réinitialisation est invalide ou a expiré.</p>
		<a
			href="/forgot-password"
			class="inline-flex rounded-lg bg-bronze px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark"
		>
			Demander un nouveau lien
		</a>
	</div>
{:else}
	<form onsubmit={handleSubmit} class="rounded-xl border border-stone-border bg-parchment p-6">
		<h1 class="mb-2 text-center font-display text-xl font-semibold text-ink">Nouveau mot de passe</h1>
		<p class="mb-6 text-center text-sm text-ink-muted">
			Choisissez un nouveau mot de passe — le lien reçu par email est valable 30 minutes.
		</p>

		{#if error}
			<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">
				{error}
			</p>
		{/if}

		<label class="mb-4 block">
			<span class="mb-1 block text-sm font-medium text-ink">Nouveau mot de passe</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={password}
				placeholder="8 caractères minimum"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
			/>
		</label>

		<label class="mb-6 block">
			<span class="mb-1 block text-sm font-medium text-ink">Confirmer le mot de passe</span>
			<input
				type="password"
				required
				minlength={8}
				bind:value={confirm}
				placeholder="••••••••"
				class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
			/>
		</label>

		<button
			type="submit"
			disabled={submitting}
			class="w-full rounded-lg bg-bronze px-6 py-3 font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
		>
			{submitting ? 'Réinitialisation…' : 'Réinitialiser le mot de passe'}
		</button>
	</form>
{/if}
