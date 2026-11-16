<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { auth } from '$lib/stores/auth.svelte';
	import AdminBanner from '$lib/components/admin/AdminBanner.svelte';

	let { children } = $props();
	let authorized = $state(false);
	let checked = $state(false);

	onMount(async () => {
		// auth.init() may already have been called by parent (app)/+layout.svelte onMount
		// Wait for loading to settle
		if (auth.loading) {
			// Wait up to 3s for auth to settle
			let waited = 0;
			while (auth.loading && waited < 3000) {
				await new Promise((r) => setTimeout(r, 50));
				waited += 50;
			}
		}
		if (!auth.isAdmin) {
			await goto('/?admin_required=1', { replaceState: true });
			return;
		}
		authorized = true;
		checked = true;
	});
</script>

{#if authorized}
	<AdminBanner email={auth.user?.email ?? ''} />
	<div class="mx-auto max-w-7xl px-4 py-6">
		{@render children()}
	</div>
{:else if !checked}
	<div class="flex h-32 items-center justify-center text-sm text-gray-500">
		Vérification des droits admin…
	</div>
{/if}
