import type { HandleClientError } from '@sveltejs/kit';

// Client-side error reporting. A crash in the visitor's browser is not sent
// anywhere: there is no third-party error tracker in this repository. The
// visitor still gets `+error.svelte`; the console line is what a developer
// reads when reproducing locally.
export const handleError: HandleClientError = ({ error, event }) => {
	console.error('Client-side error on', event.url.pathname, error);
};
