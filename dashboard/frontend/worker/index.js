/**
 * Cloudflare Worker: Edge cache for API responses
 * Deploy via: npx wrangler deploy
 *
 * Cache rules:
 * - GET /api/v1/blueprints: 60s
 * - GET /api/v1/analytics/*: 300s
 * - GET /api/v1/health: 10s
 * - GET /api/v1/config/*: 600s
 * - POST/PATCH/DELETE: bypass + purge related keys
 * - All other GET: 30s default
 */

const CACHE_RULES = {
  '/api/v1/blueprints': 60,
  '/api/v1/analytics': 300,
  '/api/v1/health': 10,
  '/api/v1/config': 600,
  '/api/v1/schedule': 60,
  '/api/v1/stories': 120,
  '/api/v1/pipeline': 30,
};

function getCacheTTL(pathname) {
  for (const [prefix, ttl] of Object.entries(CACHE_RULES)) {
    if (pathname.startsWith(prefix)) return ttl;
  }
  return 30; // default
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Only cache API GET requests
    if (request.method !== 'GET' || !url.pathname.startsWith('/api/')) {
      // For mutations, purge related cache
      if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(request.method)) {
        const cache = caches.default;
        // Purge the base resource
        const basePath = url.pathname.split('/').slice(0, 4).join('/');
        const purgeUrl = new URL(basePath, url.origin);
        await cache.delete(new Request(purgeUrl.toString()));
      }
      return fetch(request);
    }

    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: 'GET' });

    // Check cache
    let response = await cache.match(cacheKey);
    if (response) {
      // Add header to indicate cache hit
      const newResponse = new Response(response.body, response);
      newResponse.headers.set('X-Cache', 'HIT');
      return newResponse;
    }

    // Cache miss — fetch from origin
    response = await fetch(request);

    if (response.ok) {
      const ttl = getCacheTTL(url.pathname);
      const cachedResponse = new Response(response.body, response);
      cachedResponse.headers.set('Cache-Control', `public, max-age=${ttl}`);
      cachedResponse.headers.set('X-Cache', 'MISS');
      cachedResponse.headers.set('X-Cache-TTL', String(ttl));

      // Store in cache (non-blocking)
      ctx.waitUntil(cache.put(cacheKey, cachedResponse.clone()));

      return cachedResponse;
    }

    return response;
  },
};
