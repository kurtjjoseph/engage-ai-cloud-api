<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Web-search-based digital footprint scans for the selected organization -
 * the "baseline engagement measurement" feature. The first scan for an org
 * is flagged as its baseline (see AnalyticsSnapshot.is_baseline on the API
 * side); every scan after that is meant to be read against that fixed
 * reference point, not just against whatever the previous scan said.
 */
class EngageAI_Admin_Analytics
{
    private static ?EngageAI_Admin_Analytics $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Analytics
    {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct()
    {
        $this->client = new EngageAI_Api_Client();
    }

    public function register_hooks(): void
    {
        add_action('admin_post_engageai_run_analytics_scan', [$this, 'handle_run_scan']);
    }

    /** Kept in sync with KNOWN_CHANNELS in the API's services/analytics_search.py. */
    private const CHANNELS = [
        'website' => 'Website',
        'google_business' => 'Google Business (reviews)',
        'facebook' => 'Facebook',
        'instagram' => 'Instagram',
        'youtube' => 'YouTube',
        'linkedin' => 'LinkedIn',
        'twitter_x' => 'X / Twitter',
        'news_mentions' => 'News mentions',
    ];

    public function handle_run_scan(): void
    {
        $this->verify_request('engageai_run_analytics_scan');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $submitted = array_map('sanitize_key', (array) ($_POST['engageai_channels'] ?? []));
        $channels = array_values(array_intersect($submitted, array_keys(self::CHANNELS)));
        // empty $channels means "full sweep", which already includes website - only
        // block include_pages when channels were explicitly narrowed to exclude it.
        $website_in_scope = empty($channels) || in_array('website', $channels, true);
        $include_pages = !empty($_POST['engageai_include_pages']) && $website_in_scope;

        $result = $this->client->run_analytics_scan($org_id, $channels, $include_pages);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', __('Scan complete.', 'engage-ai'));
    }

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }

        if (!$this->client->is_connected()) {
            $this->render_not_ready(__('Connect your Engage AI account on the Settings page first.', 'engage-ai'));
            return;
        }

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->render_not_ready(__('Select or create an organization on the Settings page first.', 'engage-ai'));
            return;
        }

        $snapshots = $this->client->get_analytics_snapshots($org_id);
        if (is_wp_error($snapshots)) {
            if (strpos($snapshots->get_error_message(), '403') !== false || strpos($snapshots->get_error_message(), 'not enabled') !== false) {
                $this->render_not_ready(sprintf(
                    /* translators: %s: link to the Settings page */
                    esc_html__('The Analytics module is not active for this organization. Turn it on under %s.', 'engage-ai'),
                    '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Engage AI > Settings', 'engage-ai') . '</a>'
                ));
                return;
            }
            $this->render_not_ready(esc_html($snapshots->get_error_message()));
            return;
        }

        $latest = $snapshots[0] ?? null;
        $baseline = null;
        foreach ($snapshots as $s) {
            if (!empty($s['is_baseline'])) {
                $baseline = $s;
                break;
            }
        }
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Analytics', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin: 1em 0;">
                <input type="hidden" name="action" value="engageai_run_analytics_scan">
                <?php wp_nonce_field('engageai_run_analytics_scan'); ?>

                <p><strong><?php esc_html_e('Channels to scan (leave all unchecked for the full sweep):', 'engage-ai'); ?></strong></p>
                <p>
                    <?php foreach (self::CHANNELS as $key => $label): ?>
                        <label style="margin-right: 1.25em; display: inline-block;">
                            <input type="checkbox" name="engageai_channels[]" value="<?php echo esc_attr($key); ?>" class="engageai-channel-checkbox">
                            <?php echo esc_html($label); ?>
                        </label>
                    <?php endforeach; ?>
                </p>
                <p>
                    <label>
                        <input type="checkbox" name="engageai_include_pages" value="1">
                        <?php esc_html_e('Include per-page website visibility ranking', 'engage-ai'); ?>
                    </label>
                    <span class="description"> - <?php esc_html_e('discovers individual pages and ranks them by public visibility signals (indexed, ranks for, backlinks, freshness). Not real traffic - web search cannot see actual analytics. Costs more, only applies if Website is in scope.', 'engage-ai'); ?></span>
                </p>

                <?php submit_button(__('Run new scan', 'engage-ai'), 'primary', 'submit', false); ?>
                <p class="description"><?php esc_html_e('Searches the web for this organization\'s public presence and records what it finds. The first scan becomes the baseline every later scan is compared against.', 'engage-ai'); ?></p>
            </form>

            <?php if (!$latest): ?>
                <p><?php esc_html_e('No scans yet - run one above to establish a baseline.', 'engage-ai'); ?></p>
            <?php else: ?>
                <h2>
                    <?php esc_html_e('Latest scan', 'engage-ai'); ?>
                    <?php if (!empty($latest['is_baseline'])): ?>
                        <span class="engageai-risk engageai-risk-low"><?php esc_html_e('BASELINE', 'engage-ai'); ?></span>
                    <?php endif; ?>
                </h2>
                <p class="engageai-rationale"><?php echo esc_html($latest['created_at'] ?? ''); ?></p>
                <?php if (!empty($latest['requested_channels'])): ?>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: comma-separated list of channel labels */
                            esc_html__('Scope: %s only', 'engage-ai'),
                            esc_html(implode(', ', array_map([$this, 'channel_label'], $latest['requested_channels'])))
                        );
                        ?>
                    </p>
                <?php endif; ?>
                <?php if (!empty($latest['summary'])): ?>
                    <p><?php echo esc_html($latest['summary']); ?></p>
                <?php endif; ?>

                <?php if (!empty($latest['channels'])): ?>
                    <div class="engageai-tickets">
                        <?php foreach ($latest['channels'] as $channel): ?>
                            <div class="engageai-card">
                                <h3><?php echo esc_html($this->channel_label($channel['channel'] ?? '')); ?></h3>
                                <?php if (!empty($channel['metrics']) && is_array($channel['metrics'])): ?>
                                    <div class="engageai-subfields">
                                        <?php foreach ($channel['metrics'] as $key => $value): ?>
                                            <p><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html((string) $value); ?></p>
                                        <?php endforeach; ?>
                                    </div>
                                <?php endif; ?>
                                <?php if (!empty($channel['notes'])): ?>
                                    <p class="engageai-why"><em><?php echo esc_html($channel['notes']); ?></em></p>
                                <?php endif; ?>
                                <?php if (!empty($channel['pages']) && is_array($channel['pages'])): ?>
                                    <?php $this->render_page_ranking($channel['pages']); ?>
                                <?php endif; ?>
                            </div>
                        <?php endforeach; ?>
                    </div>
                <?php else: ?>
                    <p><?php esc_html_e('No channels with findable public data this scan.', 'engage-ai'); ?></p>
                <?php endif; ?>

                <?php if (!empty($latest['sources'])): ?>
                    <p><strong><?php esc_html_e('Sources:', 'engage-ai'); ?></strong></p>
                    <ul>
                        <?php foreach ($latest['sources'] as $url): ?>
                            <li><a href="<?php echo esc_url($url); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($url); ?></a></li>
                        <?php endforeach; ?>
                    </ul>
                <?php endif; ?>

                <?php if ($baseline && $baseline !== $latest): ?>
                    <p class="description">
                        <?php
                        printf(
                            /* translators: %s: baseline scan date */
                            esc_html__('Baseline was recorded %s - compare against that, not just the previous scan.', 'engage-ai'),
                            esc_html($baseline['created_at'] ?? '')
                        );
                        ?>
                    </p>
                <?php endif; ?>
            <?php endif; ?>

            <?php if (count($snapshots) > 1): ?>
                <h2><?php esc_html_e('Scan history', 'engage-ai'); ?></h2>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('When', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Baseline?', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Scope', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Summary', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($snapshots as $s): ?>
                            <tr>
                                <td><?php echo esc_html($s['created_at'] ?? ''); ?></td>
                                <td><?php echo !empty($s['is_baseline']) ? esc_html__('Yes', 'engage-ai') : ''; ?></td>
                                <td><?php echo !empty($s['requested_channels']) ? esc_html(implode(', ', array_map([$this, 'channel_label'], $s['requested_channels']))) : esc_html__('All', 'engage-ai'); ?></td>
                                <td><?php echo esc_html($s['summary'] ?? ''); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
        <?php
    }

    private function channel_label(string $channel): string
    {
        return self::CHANNELS[$channel] ?? ucwords(str_replace('_', ' ', $channel));
    }

    /**
     * @param array<int, array{url?: string, visibility_rank?: int, signals?: array, notes?: string}> $pages
     */
    private function render_page_ranking(array $pages): void
    {
        usort($pages, static fn($a, $b) => ($a['visibility_rank'] ?? 999) <=> ($b['visibility_rank'] ?? 999));
        ?>
        <p><strong><?php esc_html_e('Page visibility ranking:', 'engage-ai'); ?></strong>
            <span class="description"><?php esc_html_e('(public discoverability signals, not real traffic)', 'engage-ai'); ?></span>
        </p>
        <table class="widefat striped">
            <thead>
                <tr>
                    <th><?php esc_html_e('#', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Page', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Signals', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Notes', 'engage-ai'); ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($pages as $page): ?>
                    <tr>
                        <td><?php echo esc_html((string) ($page['visibility_rank'] ?? '')); ?></td>
                        <td>
                            <?php if (!empty($page['url'])): ?>
                                <a href="<?php echo esc_url($page['url']); ?>" target="_blank" rel="noopener noreferrer"><?php echo esc_html($page['url']); ?></a>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if (!empty($page['signals']) && is_array($page['signals'])): ?>
                                <?php foreach ($page['signals'] as $key => $value): ?>
                                    <?php if (is_scalar($value)): ?>
                                        <div><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html((string) $value); ?></div>
                                    <?php elseif (is_array($value)): ?>
                                        <div><strong><?php echo esc_html(ucwords(str_replace('_', ' ', (string) $key))); ?>:</strong> <?php echo esc_html(implode(', ', array_filter($value, 'is_scalar'))); ?></div>
                                    <?php endif; ?>
                                <?php endforeach; ?>
                            <?php endif; ?>
                        </td>
                        <td><?php echo esc_html($page['notes'] ?? ''); ?></td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Analytics', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p><?php echo wp_kses_post($message); ?></p></div>
        </div>
        <?php
    }

    private function verify_request(string $action): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer($action)) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }
    }

    private function redirect_with_notice(string $type, string $message): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), ['type' => $type, 'message' => $message], 60);
        wp_safe_redirect(add_query_arg(['page' => 'engageai-analytics'], admin_url('admin.php')));
        exit;
    }

    private function render_notice(): void
    {
        $key = 'engageai_notice_' . get_current_user_id();
        $notice = get_transient($key);
        if (!$notice) {
            return;
        }
        delete_transient($key);
        $class = $notice['type'] === 'error' ? 'notice-error' : 'notice-success';
        printf('<div class="notice %s is-dismissible"><p>%s</p></div>', esc_attr($class), esc_html($notice['message']));
    }
}
