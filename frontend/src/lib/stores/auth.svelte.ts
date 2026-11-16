import { getMe, logout as apiLogout, type UserInfo } from '$lib/api/auth';

class AuthStore {
	user = $state<UserInfo | null>(null);
	loading = $state(true);
	isAuthenticated = $derived(this.user !== null);
	isAdmin = $derived(this.user?.is_admin ?? false);

	#initialized = false;

	async init() {
		if (this.#initialized) return;
		this.#initialized = true;
		this.loading = true;
		try {
			this.user = await getMe();
		} catch {
			this.user = null;
		} finally {
			this.loading = false;
		}
	}

	setUser(user: UserInfo) {
		this.user = user;
		this.loading = false;
	}

	async logout() {
		try {
			await apiLogout();
		} finally {
			this.user = null;
		}
	}
}

export const auth = new AuthStore();
