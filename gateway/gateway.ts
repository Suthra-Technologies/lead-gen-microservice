/**
 * Deno.js Fetch Gateway for Web Crawlers
 * 
 * Provides centralized HTTP fetching, domain allowlist enforcement,
 * per-domain rate limiting, in-memory TTL caching, retry on 5xx errors,
 * and transparent access-restriction handling (403/429/CAPTCHA).
 */

interface FetchRequest {
  url: string;
  skip_cache?: boolean;
}

interface CacheEntry {
  body: string;
  contentType: string;
  status: number;
  timestamp: number;
}

// ----------------------------------------------------------------------------
// Configuration
// ----------------------------------------------------------------------------
const PORT = 8000;
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const DEFAULT_TIMEOUT_MS = 15000;
const CACHE_TTL_MS = 1000 * 60 * 60; // 1 Hour TTL
const PER_DOMAIN_DELAY_MS = 1500;    // 1.5s between requests to same domain

const ALLOWED_HOSTS = new Set([
  "www.solarpaneltalk.com",
  "solarpaneltalk.com",
  "diysolarforum.com",
  "www.diysolarforum.com",
  "houzz.com",
  "www.houzz.com",
  "ecorenovator.org",
  "www.ecorenovator.org",
  "sirensolar.org",
  "www.sirensolar.org",
  "solarunitedneighbors.org",
  "www.solarunitedneighbors.org",
  "builditsolar.com",
  "www.builditsolar.com",
  "reddit.com",
  "www.reddit.com",
  "forum.solar-electric.com",
  "solar-electric.com",
  "bogleheads.org",
  "www.bogleheads.org",
  "city-data.com",
  "www.city-data.com",
  "biggerpockets.com",
  "www.biggerpockets.com"
]);

// In-Memory Storage
const cache = new Map<string, CacheEntry>();
const lastRequestTimes = new Map<string, number>();

// Helper: Sleep
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Helper: Domain Rate Limiting
async function enforceRateLimit(hostname: string): Promise<void> {
  const now = Date.now();
  const lastTime = lastRequestTimes.get(hostname) || 0;
  const elapsed = now - lastTime;
  if (elapsed < PER_DOMAIN_DELAY_MS) {
    const waitTime = PER_DOMAIN_DELAY_MS - elapsed;
    await sleep(waitTime);
  }
  lastRequestTimes.set(hostname, Date.now());
}

// ----------------------------------------------------------------------------
// Server Handler
// ----------------------------------------------------------------------------
Deno.serve({ port: PORT }, async (req: Request) => {
  const urlPath = new URL(req.url).pathname;

  // Health check endpoint
  if (req.method === "GET" && urlPath === "/health") {
    return Response.json({
      status: "ok",
      cached_entries: cache.size,
      domains_tracked: lastRequestTimes.size,
      timestamp: new Date().toISOString()
    });
  }

  if (req.method !== "POST" || urlPath !== "/fetch") {
    return new Response("Not Found. POST to /fetch or GET /health", { status: 404 });
  }

  let fetchReq: FetchRequest;
  try {
    fetchReq = await req.json();
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!fetchReq.url) {
    return Response.json({ error: "Missing 'url' parameter" }, { status: 400 });
  }

  let targetUrl: URL;
  try {
    targetUrl = new URL(fetchReq.url);
  } catch {
    return Response.json({ error: "Malformed URL" }, { status: 400 });
  }

  // 1. Domain Allowlist Validation
  const hostname = targetUrl.hostname.toLowerCase();
  if (!ALLOWED_HOSTS.has(hostname)) {
    console.warn(`[Gateway Denied] Host '${hostname}' not in ALLOWED_HOSTS: ${fetchReq.url}`);
    return Response.json(
      { error: "DOMAIN_NOT_ALLOWED", message: `Domain ${hostname} is not allowed` },
      { status: 403 }
    );
  }

  // 2. Cache Lookup
  if (!fetchReq.skip_cache && cache.has(fetchReq.url)) {
    const entry = cache.get(fetchReq.url)!;
    if (Date.now() - entry.timestamp < CACHE_TTL_MS) {
      console.log(`[Gateway Cache Hit] ${fetchReq.url}`);
      return Response.json({
        status: entry.status,
        contentType: entry.contentType,
        body: entry.body,
        from_cache: true
      });
    } else {
      cache.delete(fetchReq.url);
    }
  }

  // 3. Politeness Delay
  await enforceRateLimit(hostname);

  // 4. Fetch with Retries for 5xx errors
  const startTime = performance.now();
  const maxRetries = 2;
  let lastError: any = null;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      console.log(`[Gateway Fetch] ${targetUrl.href} (Attempt ${attempt}/${maxRetries})`);

      const response = await fetch(targetUrl.href, {
        headers: {
          "User-Agent": USER_AGENT,
          "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.9",
        },
        signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
        redirect: "follow",
      });

      const elapsed = Math.round(performance.now() - startTime);

      // Handle 403 / 429
      if (response.status === 403 || response.status === 429) {
        console.warn(`[Gateway ${response.status}] Access restricted on ${targetUrl.href} (${elapsed}ms)`);
        return Response.json({
          status: response.status,
          action: response.status === 429 ? "RATE_LIMITED" : "ACCESS_RESTRICTED",
          retryAfter: response.headers.get("Retry-After") || null,
          url: response.url
        });
      }

      // Retry on 5xx errors
      if (response.status >= 500 && attempt < maxRetries) {
        console.warn(`[Gateway ${response.status}] Server error. Retrying in 2s...`);
        await sleep(2000);
        continue;
      }

      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.includes("text/html") && !contentType.includes("application/json") && !contentType.includes("xml")) {
        return Response.json({
          status: response.status,
          error: "UNSUPPORTED_CONTENT_TYPE",
          contentType
        }, { status: 415 });
      }

      const body = await response.text();
      console.log(`[Gateway 200 OK] ${targetUrl.href} | ${body.length.toLocaleString()} bytes | ${elapsed}ms`);

      // Save successful response in cache
      if (response.status === 200) {
        cache.set(fetchReq.url, {
          body,
          contentType,
          status: response.status,
          timestamp: Date.now()
        });
      }

      return Response.json({
        status: response.status,
        contentType,
        body,
        from_cache: false,
        url: response.url
      });

    } catch (err: any) {
      lastError = err;
      if (attempt < maxRetries) {
        await sleep(2000);
      }
    }
  }

  const totalElapsed = Math.round(performance.now() - startTime);
  console.error(`[Gateway Error] Failed fetching ${targetUrl.href} after ${totalElapsed}ms: ${lastError}`);
  return Response.json({
    error: "FETCH_FAILED",
    message: String(lastError)
  }, { status: 502 });
});

