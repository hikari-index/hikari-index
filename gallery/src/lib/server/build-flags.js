// Facts about this build, fixed when it was built. The Dockerfile rewrites
// this file for a read-only build (HIKARI_READ_ONLY_BUILD), so that image
// stays read-only whatever its environment says.
export const READ_ONLY_BUILD = false;
