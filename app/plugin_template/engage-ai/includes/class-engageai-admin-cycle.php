<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Runs the full seven-stage engagement cycle (measure -> plan -> generate ->
 * approve -> distribute -> re-measure -> report) against the cloud API and
 * shows the before/after Engage AI score, the per-stage report, and which
 * engagements got distributed. Mirrors the "run a scan, show the result,
 * keep a history table" structure of the Analytics page.
 */
class EngageAI_Admin_Cycle
{
    private static ?EngageAI_Admin_Cycle $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Cycle
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
        add_action('admin_post_engageai_run_engagement_cycle', [$this, 'handle_run_engagement_cycle']);
    }

    public function handle_run_engagement_cycle(): void
    {
        $this->verify_request('engageai_run_engagement_cycle');

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization first.', 'engage-ai'));
        }

        $mode = sanitize_key($_POST['engageai_measure_mode'] ?? 'simulate');
        if (!in_array($mode, ['simulate', 'live'], true)) {
            $mode = 'simulate';
        }
        $dry_run = !empty($_POST['engageai_dry_run']);

        $result = $this->client->run_engagement_cycle($org_id, [
            'measure_mode' => $mode,
            'dry_run' => $dry_run,
        ]);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $before = $result['before_org_score'] ?? null;
        $after = $result['after_org_score'] ?? null;
        $status = (string) ($result['status'] ?? '');

        if ($before === null || $after === null) {
            $this->redirect_with_notice('success', sprintf(
                /* translators: %s: cycle status, e.g. "blocked_no_baseline" */
                __('Cycle finished with status "%s".', 'engage-ai'),
                $status
            ));
        }

        $this->redirect_with_notice('success', sprintf(
            /* translators: 1: cycle status, 2: before score, 3: after score */
            __('Cycle complete (%1$s): Engage AI score %2$d -> %3$d.', 'engage-ai'),
            $status,
            (int) $before,
            (int) $after
        ));
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

        $runs = $this->client->get_engagement_cycle_runs($org_id);
        if (is_wp_error($runs)) {
            if (strpos($runs->get_error_message(), '403') !== false || strpos($runs->get_error_message(), 'not enabled') !== false) {
                $this->render_not_ready(sprintf(
                    /* translators: %s: link to the Settings page */
                    esc_html__('The Engagement Cycle module is not active for this organization. Turn it on under %s.', 'engage-ai'),
                    '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Engage AI > Settings', 'engage-ai') . '</a>'
                ));
                return;
            }
            $this->render_not_ready(esc_html($runs->get_error_message()));
            return;
        }

        $latest = $runs[0] ?? null;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Engagement Cycle', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin: 1em 0;">
                <input type="hidden" name="action" value="engageai_run_engagement_cycle">
                <?php wp_nonce_field('engageai_run_engagement_cycle'); ?>

                <p>
                    <label for="engageai_measure_mode"><strong><?php esc_html_e('Measure mode', 'engage-ai'); ?></strong></label><br>
                    <select name="engageai_measure_mode" id="engageai_measure_mode">
                        <option value="simulate" selected><?php esc_html_e('Simulate (projected score change, no real measurement)', 'engage-ai'); ?></option>
                        <option value="live"><?php esc_html_e('Live (measure real before/after score)', 'engage-ai'); ?></option>
                    </select>
                </p>
                <p>
                    <label for="engageai_dry_run">
                        <input type="checkbox" name="engageai_dry_run" id="engageai_dry_run" value="1">
                        <?php esc_html_e('Dry run (plan and report only, do not distribute engagements)', 'engage-ai'); ?>
                    </label>
                </p>

                <?php submit_button(__('Run Engagement Cycle', 'engage-ai'), 'primary', 'submit', false); ?>
                <p class="description"><?php esc_html_e('Runs all seven stages now: measure the current Engage AI score, plan, generate, approve, distribute engagements, re-measure, and report.', 'engage-ai'); ?></p>
            </form>

            <?php if (!$latest): ?>
                <p><?php esc_html_e('No cycles run yet - run one above.', 'engage-ai'); ?></p>
            <?php else: ?>
                <h2><?php esc_html_e('Latest run', 'engage-ai'); ?></h2>
                <p class="engageai-rationale"><?php echo esc_html($latest['created_at'] ?? ''); ?></p>

                <?php $this->render_stat_cards($latest); ?>
                <?php $this->render_distribution_summary($latest); ?>

                <?php if (!empty($latest['stages'])): ?>
                    <h3><?php esc_html_e('Seven stages', 'engage-ai'); ?></h3>
                    <?php $this->render_stages_table($latest['stages']); ?>
                <?php endif; ?>
            <?php endif; ?>

            <?php if (!empty($runs)): ?>
                <h2><?php esc_html_e('Previous runs', 'engage-ai'); ?></h2>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('When', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Status', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Before -> After', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Delta', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($runs as $run): ?>
                            <tr>
                                <td><?php echo esc_html($run['created_at'] ?? ''); ?></td>
                                <td><?php $this->render_status_badge((string) ($run['status'] ?? '')); ?></td>
                                <td>
                                    <?php
                                    $rb = $run['before_org_score'] ?? null;
                                    $ra = $run['after_org_score'] ?? null;
                                    echo esc_html(($rb === null ? __('n/a', 'engage-ai') : (string) (int) $rb) . ' -> ' . ($ra === null ? __('n/a', 'engage-ai') : (string) (int) $ra));
                                    ?>
                                </td>
                                <td><?php echo esc_html($this->format_delta($run['delta'] ?? null)); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
        <?php
    }

    /**
     * @param array{before_org_score?: int|null, after_org_score?: int|null, delta?: int|null} $run
     */
    private function render_stat_cards(array $run): void
    {
        $before = $run['before_org_score'] ?? null;
        $after = $run['after_org_score'] ?? null;
        $delta = $run['delta'] ?? null;
        $delta_class = $delta === null ? 'engageai-stat-neutral' : ($delta > 0 ? 'engageai-stat-positive' : ($delta < 0 ? 'engageai-stat-negative' : 'engageai-stat-neutral'));
        ?>
        <div class="engageai-stat-cards">
            <div class="engageai-stat-card">
                <p class="engageai-stat-label"><?php esc_html_e('Before score', 'engage-ai'); ?></p>
                <p class="engageai-stat-value"><?php echo esc_html($before === null ? __('n/a', 'engage-ai') : (string) (int) $before); ?></p>
            </div>
            <div class="engageai-stat-card">
                <p class="engageai-stat-label"><?php esc_html_e('After score', 'engage-ai'); ?></p>
                <p class="engageai-stat-value"><?php echo esc_html($after === null ? __('n/a', 'engage-ai') : (string) (int) $after); ?></p>
            </div>
            <div class="engageai-stat-card">
                <p class="engageai-stat-label"><?php esc_html_e('Delta', 'engage-ai'); ?></p>
                <p class="engageai-stat-value <?php echo esc_attr($delta_class); ?>"><?php echo esc_html($this->format_delta($delta)); ?></p>
            </div>
        </div>
        <?php
    }

    private function format_delta($delta): string
    {
        if ($delta === null) {
            return __('n/a', 'engage-ai');
        }
        $delta = (int) $delta;
        return ($delta > 0 ? '+' : '') . (string) $delta;
    }

    /**
     * @param array{measure_mode?: string, status?: string, engagement_count?: int, publication_ids?: array} $run
     */
    private function render_distribution_summary(array $run): void
    {
        $measure_mode = (string) ($run['measure_mode'] ?? '');
        $engagement_count = (int) ($run['engagement_count'] ?? 0);
        $publication_ids = is_array($run['publication_ids'] ?? null) ? $run['publication_ids'] : [];
        ?>
        <div class="engageai-card engageai-cycle-distribution">
            <h3><?php esc_html_e('Distributed engagements', 'engage-ai'); ?></h3>
            <p>
                <strong><?php esc_html_e('Status:', 'engage-ai'); ?></strong>
                <?php $this->render_status_badge((string) ($run['status'] ?? '')); ?>
                &nbsp;
                <strong><?php esc_html_e('Measure mode:', 'engage-ai'); ?></strong>
                <?php echo esc_html($measure_mode !== '' ? ucfirst($measure_mode) : __('n/a', 'engage-ai')); ?>
                <?php if ($measure_mode === 'simulate'): ?>
                    <span class="engageai-cycle-simulated"><?php esc_html_e('[SIMULATED PROJECTION]', 'engage-ai'); ?></span>
                <?php endif; ?>
            </p>
            <p>
                <?php
                printf(
                    /* translators: %d: number of engagements distributed */
                    esc_html(_n('%d engagement distributed.', '%d engagements distributed.', $engagement_count, 'engage-ai')),
                    $engagement_count
                );
                ?>
            </p>
            <?php if (!empty($publication_ids)): ?>
                <p><strong><?php esc_html_e('Publication IDs:', 'engage-ai'); ?></strong></p>
                <ul>
                    <?php foreach ($publication_ids as $pub_id): ?>
                        <li><?php echo esc_html((string) (int) $pub_id); ?></li>
                    <?php endforeach; ?>
                </ul>
            <?php endif; ?>
        </div>
        <?php
    }

    /**
     * @param array<int, array{stage?: int, name?: string, detail?: string, count?: int}> $stages
     */
    private function render_stages_table(array $stages): void
    {
        ?>
        <table class="widefat striped">
            <thead>
                <tr>
                    <th><?php esc_html_e('Stage', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Name', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Detail', 'engage-ai'); ?></th>
                    <th><?php esc_html_e('Count', 'engage-ai'); ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($stages as $stage): ?>
                    <tr>
                        <td><?php echo esc_html((string) (int) ($stage['stage'] ?? 0)); ?></td>
                        <td><?php echo esc_html((string) ($stage['name'] ?? '')); ?></td>
                        <td><?php echo esc_html((string) ($stage['detail'] ?? '')); ?></td>
                        <td><?php echo esc_html((string) (int) ($stage['count'] ?? 0)); ?></td>
                    </tr>
                <?php endforeach; ?>
            </tbody>
        </table>
        <?php
    }

    private function render_status_badge(string $status): void
    {
        if ($status === '') {
            return;
        }
        printf(
            '<span class="engageai-cycle-status engageai-cycle-status-%s">%s</span>',
            esc_attr($status),
            esc_html(str_replace('_', ' ', $status))
        );
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Engagement Cycle', 'engage-ai'); ?></h1>
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
        wp_safe_redirect(add_query_arg(['page' => 'engageai-cycle'], admin_url('admin.php')));
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
