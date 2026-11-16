/**
 * Svelte 5 action — focus-trap for modal dialogs.
 *
 * Applies to the dialog container (the element with role="dialog") and:
 *   1. Moves initial focus into the dialog when it mounts.
 *   2. Cycles Tab / Shift+Tab among tabbable elements inside the dialog only.
 *   3. Restores focus to the previously focused element when the dialog unmounts.
 *
 * Escape is intentionally NOT handled here — existing modals already wire Escape
 * to their own `onCancel` path. Trying to take it over would either duplicate
 * behavior or require every modal to pass an `onEscape` callback. Focus trap
 * stays focused on Tab containment + focus restore.
 *
 * Usage:
 *   <div role="dialog" aria-modal="true" use:focusTrap>
 *     ...
 *   </div>
 */

// Standard selector for elements that participate in tab order.
// Covers: links with href, buttons, inputs/textareas/selects, explicit tabindex >= 0,
// and contenteditable. Excludes elements with tabindex="-1", disabled controls,
// and hidden inputs. The :not([tabindex="-1"]) guard matters because many modals
// put tabindex="-1" on their backdrop to allow programmatic focus without
// making it a tab stop.
const FOCUSABLE_SELECTOR = [
	'a[href]',
	'button:not([disabled])',
	'input:not([disabled]):not([type="hidden"])',
	'select:not([disabled])',
	'textarea:not([disabled])',
	'[tabindex]:not([tabindex="-1"])',
	'[contenteditable="true"]'
].join(',');

function getTabbable(container: HTMLElement): HTMLElement[] {
	const nodes = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
	return nodes.filter((el) => {
		// Defensive: filter out elements that are visually hidden or inert.
		// offsetParent is null for display:none and fixed elements without layout;
		// we also skip hidden=true and aria-hidden="true".
		if (el.hasAttribute('disabled')) return false;
		if (el.getAttribute('aria-hidden') === 'true') return false;
		if (el.hidden) return false;
		// Skip elements rendered but not visible (offsetParent is null unless fixed-positioned).
		// Note: on fixed-positioned dialogs, offsetParent of descendants may be null, so we
		// fall back to getClientRects as a secondary signal.
		if (el.offsetParent === null && el.getClientRects().length === 0) return false;
		return true;
	});
}

export function focusTrap(node: HTMLElement) {
	const previouslyFocused =
		(document.activeElement instanceof HTMLElement ? document.activeElement : null) ?? null;

	function focusFirst() {
		const tabbable = getTabbable(node);
		const target = tabbable[0] ?? node;
		// Use rAF so focus runs after Svelte has finished mounting and attribute
		// bindings (e.g. disabled, value) have settled.
		requestAnimationFrame(() => {
			try {
				target.focus({ preventScroll: true });
			} catch {
				/* ignore focus errors (e.g. element detached) */
			}
		});
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key !== 'Tab') return;

		const tabbable = getTabbable(node);
		if (tabbable.length === 0) {
			// Nothing to focus inside the dialog — keep focus on the container
			// rather than letting Tab escape to the page behind the backdrop.
			e.preventDefault();
			node.focus({ preventScroll: true });
			return;
		}

		const first = tabbable[0];
		const last = tabbable[tabbable.length - 1];
		const active = document.activeElement as HTMLElement | null;

		// If focus has drifted outside the dialog (e.g. backdrop click landed on
		// container, or browser restored focus to <body>), pull it back in.
		if (!active || !node.contains(active)) {
			e.preventDefault();
			first.focus({ preventScroll: true });
			return;
		}

		if (e.shiftKey && active === first) {
			e.preventDefault();
			last.focus({ preventScroll: true });
		} else if (!e.shiftKey && active === last) {
			e.preventDefault();
			first.focus({ preventScroll: true });
		}
	}

	focusFirst();
	node.addEventListener('keydown', handleKeydown);

	return {
		destroy() {
			node.removeEventListener('keydown', handleKeydown);
			// Restore focus to the element that had it before the dialog opened.
			// Guard against the node having been detached from the DOM meanwhile.
			if (previouslyFocused && document.contains(previouslyFocused)) {
				try {
					previouslyFocused.focus({ preventScroll: true });
				} catch {
					/* ignore — focus restoration is best-effort */
				}
			}
		}
	};
}
