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
     * @return array|WP_Error
     */
    public function run_analytics_scan(int $org_id)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/analytics/scan');
    }

    /**
     * @return array|WP_Error list of AnalyticsSnapshots, most recent first
     */
    public function get_analytics_snapshots(int $org_id)
    {
        return $this->request('GET', '/organizations/' . $org_id . '/analytics');
    }

    /**
     * @param string $task one of: event, announcements, sermon
     * @return array|WP_Error
     */
    public function generate(string $task, array $payload)
    {
        return $this->request('POST', '/campaigns/' . $task, $payload);
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
        return $this->request('POST', '/organizations/' . $org_id . '/agents/' . $niche . '/tickets/' . $ticket_id . '/decision', [
            'decision' => $decision,
            'note' => $note !== '' ? $note : null,
        ]);
    }

    /**
     * Runs one check-in cycle now for one agent niche - the same function the scheduler calls automatically.
     * @return array|WP_Error the AgentRun record
     */
    public function run_cycle(int $org_id, string $niche)
    {
        return $this->request('POST', '/organizations/' . $org_id . '/agents/' . $niche . '/cycles/run');
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
     * @return array|WP_Error decoded JSON body, or WP_Error on failure
     */
    private function request(string $method, string $path, ?array $body = null, bool $use_auth = true)
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

        $args = [
            'method' => $method,
            'headers' => $headers,
            'timeout' => 45,
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
