<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Thin wrapper around the Engage AI Cloud API using the WP HTTP API.
 *
 * Credential handling: only the JWT + its expiry are persisted (in wp_options).
 * The password entered on the settings page is used once to obtain a token and
 * is never stored. When the token expires (7 days by default on the API side),
 * the admin re-enters email/password to reconnect.
 */
class EngageAI_Api_Client
{
    private const OPT_BASE_URL = 'engageai_api_base_url';
    private const OPT_TOKEN = 'engageai_jwt_token';
    private const OPT_TOKEN_EXP = 'engageai_jwt_expires_at';
    private const OPT_ORG_ID = 'engageai_organization_id';

    public function get_base_url(): string
    {
        return rtrim((string) get_option(self::OPT_BASE_URL, ''), '/');
    }

    public function set_base_url(string $url): void
    {
        update_option(self::OPT_BASE_URL, rtrim(trim($url), '/'));
    }

    public function get_organization_id()
    {
        $id = get_option(self::OPT_ORG_ID, '');
        return $id === '' ? null : (int) $id;
    }

    public function set_organization_id(int $id): void
    {
        update_option(self::OPT_ORG_ID, $id);
    }

    public function is_connected(): bool
    {
        $token = get_option(self::OPT_TOKEN, '');
        $expires_at = (int) get_option(self::OPT_TOKEN_EXP, 0);
        return $token !== '' && $expires_at > time();
    }

    public function token_expiry(): ?int
    {
        $expires_at = (int) get_option(self::OPT_TOKEN_EXP, 0);
        return $expires_at > 0 ? $expires_at : null;
    }

    public function disconnect(): void
    {
        delete_option(self::OPT_TOKEN);
        delete_option(self::OPT_TOKEN_EXP);
    }

    /**
     * @return true|WP_Error
     */
    public function login(string $email, string $password)
    {
        $result = $this->request('POST', '/auth/login', [
            'email' => $email,
            'password' => $password,
        ], false);

        if (is_wp_error($result)) {
            return $result;
        }

        if (empty($result['access_token'])) {
            return new WP_Error('engageai_login_failed', __('Login succeeded but no token was returned.', 'engage-ai'));
        }

        $token = $result['access_token'];
        update_option(self::OPT_TOKEN, $token, false);
        update_option(self::OPT_TOKEN_EXP, $this->extract_jwt_expiry($token), false);

        return true;
    }

    /**
     * Stores a token issued elsewhere (the onboarding download flow's
     * long-lived token) without going through login(). Used by the
     * activation-time auto-connect in engage-ai.php.
     */
    public function store_token(string $token): void
    {
        update_option(self::OPT_TOKEN, $token, false);
        update_option(self::OPT_TOKEN_EXP, $this->extract_jwt_expiry($token), false);
    }

    /**
     * @return array|WP_Error
     */
    public function get_organizations()
    {
        return $this->request('GET', '/organizations/me');
    }

    /**
     * @return array|WP_Error
     */
    public function create_organization(array $data)
    {
        return $this->request('POST', '/organizations', $data);
    }

    /**
     * Partial update for org-wide fields (mission, tone, audience,
     * website_url, etc.) - send only the fields being changed.
     * @return array|WP_Error
     */
    public function update_organization(int $org_id, array $fields)
    {
        return $this->request('PATCH', '/organizations/' . $org_id, $fields);
    }

    /**
     * @param string[] $channels empty = full sweep, otherwise scopes the scan (e.g. ["website"])
     * @param bool $include_pages adds a per-page visibility ranking to the website channel - costs more
     * @return array|WP_Error
     */
    public function run_analytics_scan(int $org_id, array $channels = [], bool $include_pages = false)
    {
        $query = [];
        foreach ($channels as $channel) {
            $query[] = 'channels=' . rawurlencode($channel);
        }
        if ($include_pages) {
            $query[] = 'include_pages=true';
        }
        $path = '/organizations/' . $org_id . '/analytics/scan';
        if (!empty($query)) {
            $path .= '?' . implode('&', $query);
        }
        // The API now starts the scan in the background and returns a
        // "pending" snapshot immediately (see POST .../analytics/scan) -
        // the slow part (Claude with web_search/web_fetch, 30-90s+) no
        // longer happens inside this HTTP request, so the default timeout
        // is plenty. Poll get_analytics_snapshots()/get_analytics_insights()
        // to see the scan finish.
        return $this->request('POST', $path);
    }

    /**
     * @return array|WP_Error list of AnalyticsSnapshots, most recent first
     */
    public function get_analytics_snapshots(int $org_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/analytics');
    }

    /**
     * Org score, a channel ranking (best to worst) with white_space/new/
     * growing/saturated/healthy classification, and each channel's exact
     * score breakdown - built from the most recent full-sweep scans only.
     * @return array|WP_Error
     */
    public function get_analytics_insights(int $org_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/analytics/insights');
    }

    /**
     * Which kind of content (by ContentItem content_type) performs best on
     * average across every scanned Publication - "what to make more of".
     * @return array|WP_Error
     */
    public function get_engagement_type_ranking(int $org_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/analytics/engagement-type-ranking');
    }

    /**
     * @return array|WP_Error list of publications, each with its latest snapshot (or null)
     */
    public function get_publications(int $org_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/publications');
    }

    /**
     * Registers where something was actually published (the "mark as
     * published" step) so its real-world performance can be tracked.
     * @return array|WP_Error
     */
    public function create_publication(int $org_id, array $data)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/publications', $data);
    }

    /**
     * @return array|WP_Error list of PublicationSnapshots for one publication, most recent first
     */
    public function get_publication_history(int $org_id, int $publication_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/publications/' . $publication_id);
    }

    /**
     * Runs a fresh performance check on one publication now.
     * @return array|WP_Error the new PublicationSnapshot
     */
    public function scan_publication(int $org_id, int $publication_id)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/publications/' . $publication_id . '/scan');
    }

    /**
     * @param string $task one of: event, announcements, sermon
     * @return array|WP_Error
     */
    public function generate(string $task, array $payload)
    {
        return $this->request('POST', '/campaigns/' . $task, $payload, true, 120);
    }

    /**
     * Replaces the org's full list of activated modules, e.g.
     * ["engagement", "agent:youtube_channel", "agent:coaching"].
     * @return array|WP_Error
     */
    public function update_modules(int $org_id, array $enabled_modules)
    {
        return $this->request('PATCH', '/organizations/' . $org_id . '/modules', [
            'enabled_modules' => $enabled_modules,
        ]);
    }

    /**
     * @return array|WP_Error list of tickets for one agent niche
     */
    public function get_tickets(int $org_id, string $niche, ?string $status = null)
    {
        $path = '/organizations/' . $org_id . '/agents/' . $niche . '/tickets';
        if ($status !== null) {
            $path .= '?status=' . rawurlencode($status);
        }
        return $this->request('GET', $path);
    }

    /**
     * @param string $decision one of: approve, reject, redirect
     * @return array|WP_Error
     */
    public function decide_ticket(int $org_id, string $niche, int $ticket_id, string $decision, string $note = '')
    {
        // Approving a "high risk" ticket now triggers AI generation of its
        // deliverable server-side (see decide_ticket in the API's
        // routers/agents.py) - give that the same LLM-call budget as
        // generate()/run_cycle() below. Reject/redirect never call the
        // model, so they stay on the ordinary default timeout.
        $timeout = $decision === 'approve' ? 120 : 45;
        return $this->request('POST', '/organizations/' . $org_id . '/agents/' . $niche . '/tickets/' . $ticket_id . '/decision', [
            'decision' => $decision,
            'note' => $note !== '' ? $note : null,
        ], true, $timeout);
    }

    /**
     * Free-form question answered using the org's stored context (mission,
     * tone, audience, etc) - for anything that doesn't fit a structured
     * generator or a specific agent niche.
     * @return array|WP_Error {"question", "answer"}
     */
    public function ask_assistant(int $org_id, string $question)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/assistant/ask', [
            'question' => $question,
        ], true, 120);
    }

    /**
     * Runs one check-in cycle now for one agent niche - the same function the scheduler calls automatically.
     * @return array|WP_Error the AgentRun record
     */
    public function run_cycle(int $org_id, string $niche)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/agents/' . $niche . '/cycles/run', null, true, 180);
    }

    /**
     * @return array|WP_Error list of past AgentRuns for one niche, most recent first
     */
    public function get_cycles(int $org_id, string $niche)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/agents/' . $niche . '/cycles');
    }

    /**
     * @return array|WP_Error
     */
    public function update_niche_profile(int $org_id, string $niche, array $profile)
    {
        return $this->request('PATCH', '/organizations/' . $org_id . '/agents/' . $niche . '/profile', $profile);
    }

    /**
     * @param int $timeout Seconds to wait for a response. The default (45s) covers
     * ordinary CRUD calls; callers that hit an endpoint backed by an LLM call - a
     * scan, an agent cycle, a campaign generation - pass a longer value, since
     * those routinely take 30-90s and can run longer once web_fetch is involved
     * (see run_analytics_scan/run_cycle/generate below).
     * @return array|WP_Error decoded JSON body, or WP_Error on failure
     */
    private function request(string $method, string $path, ?array $body = null, bool $use_auth = true, int $timeout = 45)
    {
        $base_url = $this->get_base_url();
        if ($base_url === '') {
            return new WP_Error('engageai_not_configured', __('Engage AI API URL is not configured yet.', 'engage-ai'));
        }

        $headers = ['Content-Type' => 'application/json'];

        if ($use_auth) {
            $token = get_option(self::OPT_TOKEN, '');
            if ($token === '') {
                return new WP_Error('engageai_not_connected', __('Not connected to Engage AI. Connect on the Settings page first.', 'engage-ai'));
            }
            $headers['Authorization'] = 'Bearer ' . $token;
        }

        // A long-running HTTP call still gets cut off by PHP's own script time
        // limit unless that's raised too - bump it to match whenever the caller
        // asked for more than the ordinary-request timeout. Guarded because some
        // hosts disable set_time_limit() entirely.
        if ($timeout > 45 && function_exists('set_time_limit')) {
            set_time_limit($timeout + 15);
        }

        $args = [
            'method' => $method,
            'headers' => $headers,
            'timeout' => $timeout,
        ];

        if ($body !== null) {
            $args['body'] = wp_json_encode($body);
        }

        $response = wp_remote_request($base_url . $path, $args);

        if (is_wp_error($response)) {
            return $response;
        }

        $status = wp_remote_retrieve_response_code($response);
        $raw = wp_remote_retrieve_body($response);
        $decoded = json_decode($raw, true);

        if ($status >= 400) {
            $detail = is_array($decoded) && isset($decoded['detail']) ? $decoded['detail'] : $raw;
            return new WP_Error('engageai_api_error', sprintf(
                /* translators: 1: HTTP status code, 2: error detail from the API */
                __('Engage AI API error (%1$d): %2$s', 'engage-ai'),
                $status,
                is_string($detail) ? $detail : wp_json_encode($detail)
            ));
        }

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * Reads the `exp` claim out of a JWT without verifying its signature — the
     * API itself is the source of truth on validity; this is only used to know
     * when to prompt the admin to reconnect.
     */
    private function extract_jwt_expiry(string $jwt): int
    {
        $parts = explode('.', $jwt);
        if (count($parts) !== 3) {
            return time() + DAY_IN_SECONDS;
        }

        $payload = json_decode(base64_decode(strtr($parts[1], '-_', '+/')), true);
        if (is_array($payload) && !empty($payload['exp'])) {
            return (int) $payload['exp'];
        }

        return time() + DAY_IN_SECONDS;
    }
}
