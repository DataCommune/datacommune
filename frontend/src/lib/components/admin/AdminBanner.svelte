<script lang="ts">
	import IconBouclier from '$lib/components/ds/icons/IconBouclier.svelte';

	interface Props {
		email: string;
	}
	let { email }: Props = $props();
</script>

<div class="sticky top-0 z-40 flex items-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-amber-900">
	<IconBouclier class="h-4 w-4 shrink-0" />
	<span class="text-sm font-medium">
		Mode admin — connecté en tant que <strong>{email}</strong>
	</span>
</div>
