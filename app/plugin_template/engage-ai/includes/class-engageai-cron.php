<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Sweeps for engagement_growth tickets Engage AI can safely act on without
 * waiting for a human decision - specifically "content_idea" tickets
 * targeting the "website" channel, which the API tags risk:"low" (see
 * engagement_growth's NICHE_PROMPTS entry in agent_ai.py) because
 * publishing there lands as a WordPress draft, not a live post - genuinely
 * reversible, unlike every other channel this plugin has no real publish
 * integration for yet.
 *
 * There's no reverse channel from the API back into WordPress (WP always
 * initiates every call), so "autonomous" here means WordPress itself has to
 * come looking for work - this is the plugin's own WP-Cron job for that.
 * WP-Cron only fires on a page load past the scheduled time (standard
 * WordPress behavior, same as the bundled Plugin Update Checker's own
 * scheduler) - a low-traffic site may see this run late; never a security
 * concern, just a timing one.
 */
class EngageAI_Cron
{
    public const HOOK = 'engageai_auto_publish_cycle';
    private const NICHE = 'engagement_growth';

    public static function schedule(): void
    {
        if (!wp_next_scheduled(self::HOOK)) {
            wp_schedule_event(time(), 'hourly', self::HOOK);
        }
    }

    public static function unschedule(): void
    {
        $timestamp = wp_next_scheduled(self::HOOK);
        if ($timestamp) {
            wp_unschedule_event($timestamp, self::HOOK);
        }
    }

    public static function run(): void
    {
        $client = new EngageAI_Api_Client();
        if (!$client->is_connected()) {
            return;
        }

        $org_id = $client->get_organization_id();
        if (!$org_id || !self::niche_enabled($client, $org_id)) {
            return;
        }

        $tickets = $client->get_tickets($org_id, self::NICHE, 'proposed');
        if (is_wp_error($tickets)) {
            return;
        }

        $publisher = new EngageAI_Post_Publisher();
        foreach ($tickets as $t) {
            self::maybe_publish($client, $publisher, $org_id, $t);
        }
    }

    private static function niche_enabled(EngageAI_Api_Client $client, int $org_id): bool
    {
        $orgs = $client->get_organizations();
        if (is_wp_error($orgs)) {
            return false;
        }
        foreach ($orgs as $o) {
            if ((int) $o['id'] === $org_id) {
                return in_array('agent:' . self::NICHE, $o['enabled_modules'] ?? [], true);
            }
        }
        return false;
    }

    private static function maybe_publish(EngageAI_Api_Client $client, EngageAI_Post_Publisher $publisher, int $org_id, array $t): void
    {
        $payload = $t['payload'] ?? [];
        if (($t['risk'] ?? '') !== 'low') {
            return; // only the API's own risk classification decides what's safe to skip approval for
        }
        if (($payload['action_type'] ?? '') !== 'content_idea' || ($payload['channel'] ?? '') !== 'website') {
            return;
        }
        $content = is_string($payload['content'] ?? null) ? $payload['content'] : '';
        if ($content === '') {
            return;
        }

        // Always 'draft', regardless of the site's configured default
        // publish status - autonomy is only safe because this never goes
        // live untouched.
        $post_id = $publisher->publish_single_channel_draft($t['title'] ?? __('Engage AI content idea', 'engage-ai'), $content, 'draft');
        if (is_wp_error($post_id)) {
            return;
        }

        $client->decide_ticket(
            $org_id,
            self::NICHE,
            (int) $t['id'],
            'approve',
            sprintf(
                /* translators: %d: WordPress post ID */
                __('Auto-published as a draft post (#%d) - reversible, so this ran without waiting for approval. Review and publish it live when ready.', 'engage-ai'),
                $post_id
            )
        );
    }
}
