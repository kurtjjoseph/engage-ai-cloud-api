<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Automation: which steps of the workflow are allowed to run themselves.
 *
 * The queue strip on every page made the backlog visible, and made something
 * else visible with it: most of what it reported as waiting was waiting on a
 * person to press the same button, on the same item, for the same reason,
 * again. Checking a draft, writing a kept idea, building pieces a campaign has
 * already planned, measuring a post that went live last week - none of those
 * are decisions. They are the queue being carried by hand.
 *
 * So each of those is a step that can be switched on here, and the API's
 * drainer performs exactly the action the button performs - the same function,
 * not a second implementation of it.
 *
 * TWO LEVELS, ON PURPOSE. A step's own toggle says "this may be done for me at
 * all". The master switch says "and it may happen while nobody is watching".
 * They are separate because they are different amounts of trust: an operator
 * may well want the checks run when they press Run now, and still not want
 * anything happening at three in the morning.
 *
 * PUBLISHING IS NOT ON THIS PAGE'S OFFER. It is listed, greyed, with the reason
 * printed next to it - listed rather than hidden, because a step that is simply
 * absent reads as an oversight, and the operator is owed the answer to "why not
 * that one too" in the same list as everything else. The API refuses it as
 * well; this page is not the thing preventing it.
 */
class EngageAI_Admin_Automation
{
    private static ?EngageAI_Admin_Automation $instance = null;
    private EngageAI_Api_Client $client;

    /**
     * The board is fetched by every page that draws a queue strip, so it is
     * cached for the same short window the strip's own board is.
     */
    private const CACHE_KEY = 'engageai_automation_';
    private const CACHE_TTL = 60; // seconds

    public static function instance(): EngageAI_Admin_Automation
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
        add_action('admin_post_engageai_automation_save', [$this, 'handle_save']);
        add_action('admin_post_engageai_automation_toggle', [$this, 'handle_toggle']);
        add_action('admin_post_engageai_automation_run', [$this, 'handle_run']);
    }

    /** Drops the cached settings after anything that changes them. */
    public static function forget(): void
    {
        delete_transient(self::CACHE_KEY . get_current_user_id());
    }

    /**
     * The automation state, cached briefly. Null when it can't be read - every
     * caller treats that as "say nothing", because a page that works without
     * this should still render.
     *
     * @return array|null {enabled, interval_hours, steps[], last_run}
     */
    public static function state(EngageAI_Api_Client $client): ?array
    {
        $org_id = (int) $client->get_organization_id();
        if (!$org_id || !$client->is_connected()) {
            return null;
        }

        $key = self::CACHE_KEY . get_current_user_id();
        $cached = get_transient($key);
        if (is_array($cached)) {
            return $cached;
        }

        $state = $client->get_automation($org_id);
        if (is_wp_error($state) || !is_array($state) || !isset($state['steps'])) {
            return null;
        }

        set_transient($key, $state, self::CACHE_TTL);
        return $state;
    }

    /** @return array the steps belonging to one pipeline stage */
    public static function steps_for_stage(array $state, string $stage): array
    {
        $out = [];
        foreach ((array) ($state['steps'] ?? []) as $step) {
            if (is_array($step) && ($step['stage'] ?? '') === $stage) {
                $out[] = $step;
            }
        }
        return $out;
    }

    private function guard(string $action): int
    {
        if (!current_user_can('manage_options') || !check_admin_referer($action)) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = (int) $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        return $org_id;
    }

    /** The whole page's form: the master switch, every toggle and every cap. */
    public function handle_save(): void
    {
        $org_id = $this->guard('engageai_automation_save');

        $on = array_map('sanitize_text_field', (array) ($_POST['step'] ?? []));
        $caps = (array) ($_POST['cap'] ?? []);

        $steps = [];
        foreach (array_map('sanitize_text_field', (array) ($_POST['known'] ?? [])) as $key) {
            $entry = ['enabled' => in_array($key, $on, true)];
            if (isset($caps[$key]) && (int) $caps[$key] > 0) {
                $entry['max_per_run'] = (int) $caps[$key];
            }
            $steps[$key] = $entry;
        }

        $patch = [
            'enabled' => !empty($_POST['enabled']),
            'steps' => $steps,
        ];
        $mode = sanitize_key($_POST['publish_mode'] ?? '');
        if ($mode !== '') {
            $patch['publish_mode'] = $mode;
        }

        $result = $this->client->update_automation($org_id, $patch);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['saved' => 1]);
    }

    /**
     * One step, on or off, from the queue strip on whichever page owns it -
     * so the toggle lives beside the queue it drains, not three clicks away.
     * Returns the operator to the page they were on.
     */
    public function handle_toggle(): void
    {
        $org_id = $this->guard('engageai_automation_toggle');

        $key = sanitize_text_field(wp_unslash($_POST['step_key'] ?? ''));
        $enable = !empty($_POST['enable']);
        $back = sanitize_key($_POST['back'] ?? 'engageai-dashboard');
        if ($key === '') {
            $this->redirect(['error' => 'not_ready']);
        }

        $result = $this->client->update_automation($org_id, ['steps' => [$key => $enable]]);

        self::forget();
        EngageAI_Queues::forget();
        $args = ['page' => $back];
        if (is_wp_error($result)) {
            $args['error'] = rawurlencode($result->get_error_message());
        } else {
            $args['automation'] = $enable ? 'on' : 'off';
        }
        wp_safe_redirect(add_query_arg($args, admin_url('admin.php')));
        exit;
    }

    /** Drains every switched-on step now, whatever the master switch says. */
    public function handle_run(): void
    {
        $org_id = $this->guard('engageai_automation_run');

        $result = $this->client->run_automation($org_id);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['ran' => 1]);
    }

    private function redirect(array $args): void
    {
        self::forget();
        EngageAI_Queues::forget();
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-automation'], $args), admin_url('admin.php')));
        exit;
    }

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        if (!$this->client->is_connected() || !$this->client->get_organization_id()) {
            echo '<div class="wrap engageai-wrap"><h1>' . esc_html__('Automation', 'engage-ai') . '</h1>';
            echo '<div class="notice notice-warning"><p>' . esc_html__('Connect to Engage AI on the Settings page first.', 'engage-ai') . '</p></div></div>';
            return;
        }

        $org_id = (int) $this->client->get_organization_id();
        $state = $this->client->get_automation($org_id);
        $error = is_wp_error($state) ? $state->get_error_message() : '';
        $state = is_wp_error($state) ? ['enabled' => false, 'steps' => []] : (array) $state;
        $runs = $this->client->get_automation_runs($org_id, 5);
        $runs = is_wp_error($runs) ? [] : (array) $runs;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Automation', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>
            <?php if ($error !== ''): ?>
                <div class="notice notice-error"><p><?php echo esc_html($error); ?></p></div>
            <?php endif; ?>

            <p class="description" style="max-width:44em;">
                <?php esc_html_e('Every step below is something the workflow does the same way every time. Switch one on and Engage AI will do it for the items waiting in that queue, exactly as pressing the button would. Publishing will only ever use a channel you have connected AND switched on for posting on the Channels page, and will not send a piece its own quality check has flagged.', 'engage-ai'); ?>
            </p>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_automation_save" />
                <?php wp_nonce_field('engageai_automation_save'); ?>

                <table class="widefat striped" style="margin-top:1rem;">
                    <thead>
                        <tr>
                            <th style="width:6em;"><?php esc_html_e('Automate', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Step', 'engage-ai'); ?></th>
                            <th style="width:8em;"><?php esc_html_e('Waiting', 'engage-ai'); ?></th>
                            <th style="width:10em;"><?php esc_html_e('Max per run', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                    <?php foreach ((array) ($state['steps'] ?? []) as $step): ?>
                        <?php
                        if (!is_array($step)) {
                            continue;
                        }
                        $key = (string) ($step['key'] ?? '');
                        $automatable = !empty($step['automatable']);
                        $blocked = (string) ($step['blocked_by'] ?? '');
                        ?>
                        <tr<?php echo $automatable ? '' : ' style="opacity:.75;"'; ?>>
                            <td>
                                <input type="hidden" name="known[]" value="<?php echo esc_attr($key); ?>" />
                                <input type="checkbox" name="step[]" value="<?php echo esc_attr($key); ?>"
                                       <?php checked(!empty($step['enabled'])); ?>
                                       <?php disabled(!$automatable); ?> />
                            </td>
                            <td>
                                <strong><?php echo esc_html((string) ($step['label'] ?? $key)); ?></strong>
                                <div class="description"><?php echo esc_html((string) ($step['description'] ?? '')); ?></div>
                                <?php if (!$automatable): ?>
                                    <div style="margin-top:.35rem;color:#b32d2e;">
                                        <span class="dashicons dashicons-lock" style="font-size:1rem;width:1rem;height:1rem;vertical-align:text-bottom;"></span>
                                        <?php echo esc_html((string) ($step['gate'] ?? '')); ?>
                                    </div>
                                <?php elseif ($blocked !== ''): ?>
                                    <div style="margin-top:.35rem;color:#8a6d00;">
                                        <?php echo esc_html($blocked); ?>
                                    </div>
                                <?php endif; ?>
                            </td>
                            <td>
                                <?php echo esc_html((string) (int) ($step['waiting'] ?? 0)); ?>
                                <?php self::render_holding($step); ?>
                            </td>
                            <td>
                                <?php if ($automatable): ?>
                                    <input type="number" name="cap[<?php echo esc_attr($key); ?>]" min="1"
                                           max="<?php echo esc_attr((string) (int) ($state['max_per_run_ceiling'] ?? 50)); ?>"
                                           value="<?php echo esc_attr((string) (int) ($step['max_per_run'] ?? 1)); ?>"
                                           style="width:5em;" />
                                <?php else: ?>
                                    &mdash;
                                <?php endif; ?>
                            </td>
                        </tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>

                <?php self::render_publish_mode($state); ?>

                <p style="margin-top:1rem;">
                    <label>
                        <input type="checkbox" name="enabled" value="1" <?php checked(!empty($state['enabled'])); ?> />
                        <strong><?php esc_html_e('Let the steps above run on their own', 'engage-ai'); ?></strong>
                    </label>
                    <span class="description" style="display:block;margin-left:1.8em;">
                        <?php printf(
                            /* translators: %d: hours between unattended sweeps */
                            esc_html__('Engage AI sweeps the queues about every %d hours. With this off, the switched-on steps only run when you press Run now.', 'engage-ai'),
                            (int) ($state['interval_hours'] ?? 6)
                        ); ?>
                    </span>
                </p>

                <?php submit_button(__('Save automation', 'engage-ai')); ?>
            </form>

            <hr />

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_automation_run" />
                <?php wp_nonce_field('engageai_automation_run'); ?>
                <?php submit_button(__('Run now', 'engage-ai'), 'secondary', 'submit', false); ?>
                <span class="description" style="margin-left:.5rem;">
                    <?php esc_html_e('Drains every switched-on step once. Runs on the Engage AI side, so you can leave this page.', 'engage-ai'); ?>
                </span>
            </form>

            <?php $this->render_runs($runs); ?>
        </div>
        <?php
    }

    /**
     * Why the rest of the pile is not moving.
     *
     * The whole reason this exists: "I can't see where the content is actually
     * being posted" is the obvious question about a publishing step, and a bare
     * waiting count answers it badly. Each line here names one specific,
     * fixable reason, so the answer is never "it just isn't".
     */
    private static function render_holding(array $step): void
    {
        $holding = $step['holding'] ?? null;
        if (!is_array($holding)) {
            return;
        }
        $labels = [
            'no_date' => __('%d ready, but no date was ever set — nothing will send them', 'engage-ai'),
            'not_due_yet' => __('%d scheduled for a later day', 'engage-ai'),
            'quality_failed' => __('%d held back by their own quality check', 'engage-ai'),
            'channel_not_ready' => __('%d whose channel is not connected and switched on for posting', 'engage-ai'),
        ];
        $lines = [];
        foreach ($labels as $key => $format) {
            $count = (int) ($holding[$key] ?? 0);
            if ($count > 0) {
                $lines[] = sprintf($format, $count);
            }
        }
        if ($lines === []) {
            return;
        }
        echo '<div class="description" style="margin-top:.3rem;">';
        foreach ($lines as $line) {
            echo esc_html($line) . '<br />';
        }
        echo '</div>';
    }

    /** How a finished piece gets released. Only one mode is built so far. */
    private static function render_publish_mode(array $state): void
    {
        $modes = (array) ($state['publish_modes'] ?? []);
        $supported = (array) ($state['publish_modes_supported'] ?? []);
        $current = (string) ($state['publish_mode'] ?? 'autonomous');
        if ($modes === []) {
            return;
        }
        $descriptions = [
            'autonomous' => __('Posts by itself on the planned day. No approval step.', 'engage-ai'),
            'digest' => __('Hold everything and release it in one click. Not built yet.', 'engage-ai'),
            'manual' => __('Approve each piece before it goes. Not built yet.', 'engage-ai'),
        ];
        ?>
        <p style="margin-top:1rem;">
            <strong><?php esc_html_e('When a piece is ready to go out', 'engage-ai'); ?></strong>
        </p>
        <?php foreach ($modes as $mode): ?>
            <?php $available = in_array($mode, $supported, true); ?>
            <label style="display:block;margin:.25rem 0 .25rem 1.2em;<?php echo $available ? '' : 'opacity:.6;'; ?>">
                <input type="radio" name="publish_mode" value="<?php echo esc_attr((string) $mode); ?>"
                       <?php checked($current === $mode); ?> <?php disabled(!$available); ?> />
                <?php echo esc_html(ucfirst((string) $mode)); ?>
                <span class="description">
                    &mdash; <?php echo esc_html($descriptions[$mode] ?? ''); ?>
                </span>
            </label>
        <?php endforeach; ?>
        <?php
    }

    /**
     * What it has actually done. Item-level, because nobody was watching when
     * it happened - a count of three is not an answer to "what did it write".
     */
    private function render_runs(array $runs): void
    {
        ?>
        <h2 style="margin-top:2rem;"><?php esc_html_e('Recent runs', 'engage-ai'); ?></h2>
        <?php if (empty($runs)): ?>
            <p class="description"><?php esc_html_e('Nothing has run yet.', 'engage-ai'); ?></p>
            <?php return; ?>
        <?php endif; ?>

        <?php foreach ($runs as $run): ?>
            <?php if (!is_array($run)) { continue; } ?>
            <div style="border:1px solid #dcdcde;background:#fff;padding:.75rem .9rem;margin:.6rem 0;">
                <div style="display:flex;gap:.75rem;align-items:baseline;flex-wrap:wrap;">
                    <strong><?php echo esc_html(self::status_label((string) ($run['status'] ?? ''))); ?></strong>
                    <span class="description">
                        <?php echo esc_html(sprintf(
                            /* translators: 1: how the run started, 2: when it started */
                            __('%1$s · %2$s', 'engage-ai'),
                            ($run['trigger'] ?? '') === 'scheduled' ? __('on its own', 'engage-ai') : __('you pressed Run now', 'engage-ai'),
                            (string) ($run['started_at'] ?? '')
                        )); ?>
                    </span>
                    <span class="description">
                        <?php echo esc_html(sprintf(
                            /* translators: 1: items processed, 2: items that failed */
                            __('%1$d done, %2$d failed', 'engage-ai'),
                            (int) ($run['processed'] ?? 0),
                            (int) ($run['failed'] ?? 0)
                        )); ?>
                    </span>
                </div>
                <?php if (!empty($run['error'])): ?>
                    <p style="color:#b32d2e;margin:.4rem 0 0;"><?php echo esc_html((string) $run['error']); ?></p>
                <?php endif; ?>

                <?php foreach ((array) ($run['steps'] ?? []) as $step): ?>
                    <?php
                    if (!is_array($step) || empty($step['items'])) {
                        continue;  // a step that did nothing is in the run record, but not worth a block here
                    }
                    ?>
                    <div style="margin-top:.5rem;">
                        <em><?php echo esc_html((string) ($step['label'] ?? '')); ?></em>
                        <ul style="margin:.2rem 0 0 1.2em;font-size:.85rem;">
                            <?php foreach ((array) $step['items'] as $item): ?>
                                <?php if (!is_array($item)) { continue; } ?>
                                <?php
                                $ok = $item['ok'] ?? null;
                                $colour = $ok === true ? '#0f7b47' : ($ok === false ? '#b32d2e' : '#50575e');
                                ?>
                                <li style="list-style:disc;margin:.1rem 0;">
                                    <span style="color:<?php echo esc_attr($colour); ?>;">
                                        <?php echo esc_html((string) ($item['title'] ?? $item['ref'] ?? '')); ?>
                                    </span>
                                    <?php if (!empty($item['detail'])): ?>
                                        <span class="description">&mdash; <?php echo esc_html((string) $item['detail']); ?></span>
                                    <?php endif; ?>
                                </li>
                            <?php endforeach; ?>
                        </ul>
                    </div>
                <?php endforeach; ?>
            </div>
        <?php endforeach; ?>
        <?php
    }

    private static function status_label(string $status): string
    {
        switch ($status) {
            case 'running':
                return __('Running', 'engage-ai');
            case 'done':
                return __('Done', 'engage-ai');
            case 'failed':
                return __('Failed', 'engage-ai');
            case 'nothing_to_do':
                return __('Nothing to do', 'engage-ai');
            case 'off':
                return __('Skipped — automation is off', 'engage-ai');
            default:
                return $status;
        }
    }

    private function render_notice(): void
    {
        if (!empty($_GET['saved'])) {
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html__('Automation saved.', 'engage-ai'));
        }
        if (!empty($_GET['ran'])) {
            printf('<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html__('A run has started. Refresh in a moment to see what it did.', 'engage-ai'));
        }
        if (!empty($_GET['error'])) {
            printf('<div class="notice notice-error"><p>%s</p></div>',
                esc_html(rawurldecode((string) $_GET['error'])));
        }
    }
}
