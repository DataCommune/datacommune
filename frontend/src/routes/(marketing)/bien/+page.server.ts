// `/bien`, the former entry page of the « Fiche Bien », renders nothing any more: the single search
// of `/` is the entry of an address (D-101, D-102). A 308 at request time, never prerendered (a
// prerendered redirect would be a 200 page with a meta refresh).
import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const prerender = false;

export const load: PageServerLoad = () => {
	redirect(308, '/');
};
