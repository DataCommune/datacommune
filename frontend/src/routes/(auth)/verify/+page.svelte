<script lang="ts">
  import { api } from '$lib/api/client';
  import { onMount } from 'svelte';
  import IconValide from '$lib/components/ds/icons/IconValide.svelte';
  import IconRefuse from '$lib/components/ds/icons/IconRefuse.svelte';

  let status = $state<'loading' | 'success' | 'error'>('loading');
  let errorDetail = $state('Ce lien de vérification est invalide ou a expiré.');

  onMount(async () => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');

    if (!token) {
      status = 'error';
      return;
    }

    try {
      await api<void>(`/auth/verify?token=${encodeURIComponent(token)}`);
      status = 'success';
    } catch {
      status = 'error';
      errorDetail = 'Impossible de contacter le serveur. Réessayez plus tard.';
    }
  });
</script>

<svelte:head>
  <title>Vérification email — DataCommune</title>
</svelte:head>

<div class="text-center">
  {#if status === 'loading'}
    <div class="py-8">
      <div class="inline-block h-8 w-8 animate-spin rounded-full border-2 border-bronze border-t-transparent"></div>
      <p class="mt-4 text-sm text-ink-muted">Vérification en cours…</p>
    </div>
  {/if}

  {#if status === 'success'}
    <div class="py-4">
      <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-bronze/10">
        <IconValide class="h-6 w-6 text-bronze" />
      </div>
      <h1 class="font-display text-2xl font-semibold mb-2">Email vérifié</h1>
      <p class="text-sm text-ink-muted mb-6">Votre compte est maintenant actif. Vous pouvez vous connecter.</p>
      <a
        href="/login"
        class="inline-flex rounded-lg bg-bronze px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-bronze-dark"
      >
        Se connecter
      </a>
    </div>
  {/if}

  {#if status === 'error'}
    <div class="py-4">
      <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-terracotta/10">
        <IconRefuse class="h-6 w-6 text-terracotta" />
      </div>
      <h1 class="text-2xl font-semibold mb-2">Lien invalide</h1>
      <p class="text-sm text-ink-muted mb-6">{errorDetail}</p>
      <a
        href="/register"
        class="inline-flex rounded-lg border border-stone-border px-6 py-2.5 text-sm font-medium text-ink transition-colors hover:border-bronze hover:text-bronze"
      >
        Créer un nouveau compte
      </a>
    </div>
  {/if}
</div>
