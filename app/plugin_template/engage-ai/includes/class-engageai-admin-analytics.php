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

    public function handle_run_scan(): void
    {
        $this->verify_request('engageai_run_analytics_scan');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $result = $this->client->run_analytics_scan($org_id);
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
                <?php submit_button(__('Run new scan', 'engage-ai'), 'primary', 'submit', false); ?>
                <p class="description"><?php esc_html_e('Searches the web for this organization\'s public presence (website, social profiles, reviews, etc.) and records what it finds. The first scan becomes the baseline every later scan is compared against.', 'engage-ai'); ?></p>
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
                            <th><?php esc_html_e('Summary', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($snapshots as $s): ?>
                            <tr>
                                <td><?php echo esc_html($s['created_at'] ?? ''); ?></td>
                                <td><?php echo !empty($s['is_baseline']) ? esc_html__('Yes', 'engage-ai') : ''; ?></td>
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
        $labels = [
            'website' => __('Website', 'engage-ai'),
            'google_business' => __('Google Business (reviews)', 'engage-ai'),
            'facebook' => __('Facebook', 'engage-ai'),
            'instagram' => __('Instagram', 'engage-ai'),
            'youtube' => __('YouTube', 'engage-ai'),
            'linkedin' => __('LinkedIn', 'engage-ai'),
            'twitter_x' => __('X / Twitter', 'engage-ai'),
            'news_mentions' => __('News mentions', 'engage-ai'),
        ];
        return $labels[$channel] ?? ucwords(str_replace('_', ' ', $channel));
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
