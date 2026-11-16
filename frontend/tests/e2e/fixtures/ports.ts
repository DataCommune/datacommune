export function validateLocalOrigin(name: string, value: string): string {
	const url = new URL(value);
	const port = Number(url.port);
	if (
		url.protocol !== 'http:' ||
		!['localhost', '127.0.0.1'].includes(url.hostname) ||
		url.origin !== value ||
		url.username !== '' ||
		url.password !== '' ||
		!Number.isInteger(port) ||
		port < 1 ||
		port > 65_535 ||
		[5173, 4174].includes(port)
	) {
		throw new Error(`${name} must be an exact loopback HTTP origin with an explicit valid port`);
	}
	return value;
}

export function localOrigin(name: string, fallback: string): string {
	return validateLocalOrigin(name, process.env[name] ?? fallback);
}

export function validateDistinctOrigins(appOrigin: string, sourceOrigin: string): void {
	if (new URL(appOrigin).port === new URL(sourceOrigin).port) {
		throw new Error('E2E app and source origins must use distinct loopback ports');
	}
}

export const E2E_APP_ORIGIN = localOrigin('E2E_APP_ORIGIN', 'http://localhost:15173');
export const E2E_SOURCE_ORIGIN = localOrigin('E2E_SOURCE_ORIGIN', 'http://127.0.0.1:14174');
