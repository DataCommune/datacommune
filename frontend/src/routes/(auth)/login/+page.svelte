<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { login, getMe } from '$lib/api/auth';
	import { ApiError } from '$lib/api/client';
	import { auth } from '$lib/stores/auth.svelte';
	import { safeRedirect } from '$lib/utils/safe-redirect';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		submitting = true;

		try {
			await login(email, password);
			const user = await getMe();
			auth.setUser(user);
			// Return to the guarded destination if it is a safe internal path.
			goto(safeRedirect($page.url.searchParams.get('redirect')));
		} catch (err) {
			if (err instanceof ApiError) {
				error = err.status === 401
					? 'Email ou mot de passe incorrect'
					: err.status === 403 && /v[ée]rifi/i.test(err.detail)
						? 'Email non vérifié. Un nouveau lien de vérification vient de vous être envoyé par email.'
						: err.detail || 'Erreur de connexion';
			} else {
				error = 'Erreur de connexion';
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Connexion — DataCommune</title>
</svelte:head>

<form onsubmit={handleSubmit} class="rounded-xl border border-stone-border bg-parchment p-6">
	<h1 class="mb-6 text-center font-display text-xl font-semibold text-ink">Connexion</h1>

	{#if error}
		<p class="mb-4 rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">
			{error}
		</p>
	{/if}

	<label class="mb-4 block">
		<span class="mb-1 block text-sm font-medium text-ink">Email</span>
		<input
			type="email"
			required
			bind:value={email}
			placeholder="votre@email.com"
			class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
		/>
	</label>

	<label class="mb-2 block">
		<span class="mb-1 block text-sm font-medium text-ink">Mot de passe</span>
		<input
			type="password"
			required
			minlength={8}
			bind:value={password}
			placeholder="••••••••"
			class="w-full rounded-lg border border-stone-border bg-marble px-4 py-2.5 text-sm text-ink transition-colors placeholder:text-ink-muted/60 focus:border-bronze focus:outline-none focus:ring-2 focus:ring-bronze-dark/30"
		/>
	</label>

	<div class="mb-6 text-right">
		<a href="/forgot-password" class="text-sm text-ink-muted transition-colors hover:text-bronze">
			Mot de passe oublié ?
		</a>
	</div>

	<button
		type="submit"
		disabled={submitting}
		class="w-full rounded-lg bg-bronze px-6 py-3 font-medium text-white transition-colors hover:bg-bronze-dark disabled:opacity-50"
	>
		{submitting ? 'Connexion…' : 'Se connecter'}
	</button>

	<p class="mt-4 text-center text-sm text-ink-muted">
		Pas encore de compte ?
		<a href="/register" class="font-medium text-bronze transition-colors hover:text-bronze-dark">Créer un compte</a>
	</p>
</form>
