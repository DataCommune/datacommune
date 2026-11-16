<script lang="ts">
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { isDpeMatchingPreviewEnvironment } from '$lib/api/dpe-matching';
	import DpeMatchingPreview from '$lib/components/admin/DpeMatchingPreview.svelte';

	let allowedEnvironment = $state(false);

	onMount(() => {
		allowedEnvironment = isDpeMatchingPreviewEnvironment(
			window.location.hostname,
			import.meta.env.DEV,
			import.meta.env.VITE_DPE_MATCHING_PREVIEW === 'true'
		);
	});
</script>

<svelte:head>
	<title>Aperçu comparaison DPE — Administration</title>
	<meta name="robots" content="noindex,nofollow" />
</svelte:head>

<DpeMatchingPreview isAdmin={auth.isAdmin} isProduction={!allowedEnvironment} />
