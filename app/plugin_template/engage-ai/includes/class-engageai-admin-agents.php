<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The ticket dashboard for whichever "agent:<niche>" modules are active on
 * the selected organization - one tab per active side-hustle module,
 * mirroring the tab pattern already used on the Generate Content page.
 */
class EngageAI_Admin_Agents
{
    private static ?EngageAI_Admin_Agents $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Agents
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
        add_action('admin_post_engageai_run_cycle', [$this, 'handle_run_cycle']);
        add_action('admin_post_engageai_decide_ticket', [$this, 'handle_decide_ticket']);
        add_action('admin_post_engageai_update_niche_profile', [$this, 'handle_update_niche_profile']);
    }

    /** @return string[] niche keys (without "agent:" prefix) active on an org */
    private function active_niches(array $org): array
    {
        $enabled = $org['enabled_modules'] ?? [];
        $niches = [];
        foreach ($enabled as $module) {
            if (str_starts_with($module, 'agent:')) {
                $niches[] = substr($module, strlen('agent:'));
            }
        }
        return $niches;
    }

    private function niche_label(string $niche): string
    {
        $modules = EngageAI_Admin_Settings::available_modules();
        return $modules['agent:' . $niche] ?? $niche;
    }

    private function current_org(): ?array
    {
        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            return null;
        }
        $orgs = $this->client->get_organizations();
        if (is_wp_error($orgs)) {
            return null;
        }
        foreach ($orgs as $o) {
            if ((int) $o['id'] === (int) $org_id) {
                return $o;
            }
        }
        return null;
    }

    public function handle_run_cycle(): void
    {
        $this->verify_request('engageai_run_cycle');

        $org_id = $this->client->get_organization_id();
        $niche = sanitize_key($_POST['engageai_niche'] ?? '');
        if (!$org_id || $niche === '') {
            $this->redirect_with_notice('error', __('Missing organization or niche.', 'engage-ai'), $niche);
        }

        $result = $this->client->run_cycle($org_id, $niche);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message(), $niche);
        }

        $count = (int) ($result['tickets_created'] ?? 0);
        $this->redirect_with_notice('success', sprintf(
            /* translators: %d: number of new tickets created */
            _n('Cycle complete: %d new ticket proposed.', 'Cycle complete: %d new tickets proposed.', $count, 'engage-ai'),
            $count
        ), $niche);
    }

    public function handle_decide_ticket(): void
    {
        $this->verify_request('engageai_decide_ticket');

        $org_id = $this->client->get_organization_id();
        $niche = sanitize_key($_POST['engageai_niche'] ?? '');
        $ticket_id = (int) ($_POST['engageai_ticket_id'] ?? 0);
        $decision = sanitize_key($_POST['engageai_decision'] ?? '');
        $note = sanitize_textarea_field($_POST['engageai_note'] ?? '');

        if (!$org_id || $niche === '' || $ticket_id <= 0 || !in_array($decision, ['approve', 'reject', 'redirect'], true)) {
            $this->redirect_with_notice('error', __('Invalid ticket decision.', 'engage-ai'), $niche);
        }

        $result = $this->client->decide_ticket($org_id, $niche, $ticket_id, $decision, $note);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message(), $niche);
        }

        $this->redirect_with_notice('success', __('Ticket updated.', 'engage-ai'), $niche);
    }

    public function handle_update_niche_profile(): void
    {
        $this->verify_request('engageai_update_niche_profile');

        $org_id = $this->client->get_organization_id();
        $niche = sanitize_key($_POST['engageai_niche'] ?? '');
        $key = sanitize_key($_POST['engageai_profile_key'] ?? '');
        $value = sanitize_text_field($_POST['engageai_profile_value'] ?? '');

        if (!$org_id || $niche === '' || $key === '') {
            $this->redirect_with_notice('error', __('A field name is required.', 'engage-ai'), $niche);
        }

        $result = $this->client->update_niche_profile($org_id, $niche, [$key => $value]);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message(), $niche);
        }

        $this->redirect_with_notice('success', __('Profile updated - the next cycle will use this.', 'engage-ai'), $niche);
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

        $org = $this->current_org();
        if (!$org) {
            $this->render_not_ready(__('Select or create an organization on the Settings page first.', 'engage-ai'));
            return;
        }

        $niches = $this->active_niches($org);
        if (empty($niches)) {
            $this->render_not_ready(sprintf(
                /* translators: %s: link to the Settings page */
                esc_html__('No side-hustle agent modules are active for this organization yet. Turn one on under %s.', 'engage-ai'),
                '<a href="' . esc_url(admin_url('admin.php?page=engageai-settings')) . '">' . esc_html__('Engage AI > Settings', 'engage-ai') . '</a>'
            ));
            return;
        }

        $niche = sanitize_key($_GET['niche'] ?? $niches[0]);
        if (!in_array($niche, $niches, true)) {
            $niche = $niches[0];
        }

        $tickets = $this->client->get_tickets($org['id'], $niche);
        $tickets_error = is_wp_error($tickets) ? $tickets->get_error_message() : null;
        $tickets = is_wp_error($tickets) ? [] : $tickets;

        $cycles = $this->client->get_cycles($org['id'], $niche);
        $cycles = is_wp_error($cycles) ? [] : $cycles;

        $groups = ['proposed' => [], 'approved' => [], 'backlog' => [], 'rejected' => []];
        foreach ($tickets as $t) {
            $status = $t['status'] ?? 'proposed';
            $groups[$status][] = $t;
        }
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Agents', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <h2 class="nav-tab-wrapper">
                <?php foreach ($niches as $n): ?>
                    <a href="<?php echo esc_url(add_query_arg(['page' => 'engageai-agents', 'niche' => $n], admin_url('admin.php'))); ?>"
                       class="nav-tab <?php echo $niche === $n ? 'nav-tab-active' : ''; ?>">
                        <?php echo esc_html($this->niche_label($n)); ?>
                    </a>
                <?php endforeach; ?>
            </h2>

            <?php if ($tickets_error): ?>
                <div class="notice notice-error"><p><?php echo esc_html($tickets_error); ?></p></div>
            <?php endif; ?>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin: 1em 0;">
                <input type="hidden" name="action" value="engageai_run_cycle">
                <input type="hidden" name="engageai_niche" value="<?php echo esc_attr($niche); ?>">
                <?php wp_nonce_field('engageai_run_cycle'); ?>
                <?php submit_button(__('Run check-in cycle now', 'engage-ai'), 'primary', 'submit', false); ?>
                <p class="description"><?php esc_html_e('This is the same cycle the scheduler runs automatically - use it to test on demand.', 'engage-ai'); ?></p>
            </form>

            <h2>
                <?php
                printf(
                    /* translators: %d: number of tickets awaiting a decision */
                    esc_html__('Awaiting your decision (%d)', 'engage-ai'),
                    count($groups['proposed'])
                );
                ?>
            </h2>
            <?php if (empty($groups['proposed'])): ?>
                <p><?php esc_html_e('Nothing waiting on you right now.', 'engage-ai'); ?></p>
            <?php else: ?>
                <div class="engageai-tickets">
                    <?php foreach ($groups['proposed'] as $t): ?>
                        <?php $this->render_ticket_card($t, $niche, true); ?>
                    <?php endforeach; ?>
                </div>
            <?php endif; ?>

            <h2><?php esc_html_e("Update this agent's memory", 'engage-ai'); ?></h2>
            <p class="description"><?php esc_html_e('If a ticket above is a clarifying question, answer it here as a field/value pair so the next cycle has the context.', 'engage-ai'); ?></p>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="engageai-profile-form">
                <input type="hidden" name="action" value="engageai_update_niche_profile">
                <input type="hidden" name="engageai_niche" value="<?php echo esc_attr($niche); ?>">
                <?php wp_nonce_field('engageai_update_niche_profile'); ?>
                <input type="text" name="engageai_profile_key" placeholder="<?php esc_attr_e('field name, e.g. gear', 'engage-ai'); ?>" required>
                <input type="text" name="engageai_profile_value" placeholder="<?php esc_attr_e('value', 'engage-ai'); ?>" required style="width: 40%;">
                <?php submit_button(__('Save to profile', 'engage-ai'), 'secondary', 'submit', false); ?>
            </form>

            <?php if (!empty($groups['approved'])): ?>
                <h2>
                    <?php
                    printf(
                        /* translators: %d: number of approved tickets */
                        esc_html__('Approved / in progress (%d)', 'engage-ai'),
                        count($groups['approved'])
                    );
                    ?>
                </h2>
                <div class="engageai-tickets">
                    <?php foreach ($groups['approved'] as $t): ?>
                        <?php $this->render_ticket_card($t, $niche, false); ?>
                    <?php endforeach; ?>
                </div>
            <?php endif; ?>

            <?php if (!empty($cycles)): ?>
                <h2><?php esc_html_e('Recent check-in cycles', 'engage-ai'); ?></h2>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('When', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Summary', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('New tickets', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach (array_slice($cycles, 0, 10) as $run): ?>
                            <tr>
                                <td><?php echo esc_html($run['ran_at'] ?? ''); ?></td>
                                <td><?php echo esc_html($run['summary'] ?? ''); ?></td>
                                <td><?php echo esc_html((string) ($run['tickets_created'] ?? 0)); ?></td>
                            </tr>
                        <?php endforeach; ?>
                    </tbody>
                </table>
            <?php endif; ?>
        </div>
        <?php
    }

    private function render_ticket_card(array $t, string $niche, bool $show_decision_buttons): void
    {
        $risk = $t['risk'] ?? 'low';
        $payload = $t['payload'] ?? [];
        ?>
        <div class="engageai-card">
            <h3>
                <?php echo esc_html($t['title'] ?? ''); ?>
                <span class="engageai-risk engageai-risk-<?php echo esc_attr($risk); ?>">
                    <?php echo esc_html(strtoupper($risk)); ?>
                </span>
            </h3>
            <?php if (!empty($t['rationale'])): ?>
                <p class="engageai-rationale"><em><?php echo esc_html($t['rationale']); ?></em></p>
            <?php endif; ?>

            <?php $this->render_payload($payload); ?>

            <?php if ($show_decision_buttons): ?>
                <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="engageai-decision-form">
                    <input type="hidden" name="action" value="engageai_decide_ticket">
                    <input type="hidden" name="engageai_niche" value="<?php echo esc_attr($niche); ?>">
                    <input type="hidden" name="engageai_ticket_id" value="<?php echo esc_attr($t['id']); ?>">
                    <?php wp_nonce_field('engageai_decide_ticket'); ?>
                    <textarea name="engageai_note" rows="2" placeholder="<?php esc_attr_e('optional note - e.g. an answer to a question, or feedback for redirect', 'engage-ai'); ?>"></textarea>
                    <p>
                        <button type="submit" name="engageai_decision" value="approve" class="button button-primary"><?php esc_html_e('Approve', 'engage-ai'); ?></button>
                        <button type="submit" name="engageai_decision" value="redirect" class="button"><?php esc_html_e('Redirect', 'engage-ai'); ?></button>
                        <button type="submit" name="engageai_decision" value="reject" class="button button-link-delete"><?php esc_html_e('Reject', 'engage-ai'); ?></button>
                    </p>
                </form>
            <?php elseif (!empty($t['decision_note'])): ?>
                <p class="engageai-decision-note"><strong><?php esc_html_e('Note:', 'engage-ai'); ?></strong> <?php echo esc_html($t['decision_note']); ?></p>
            <?php endif; ?>
        </div>
        <?php
    }

    /**
     * Payloads are niche-specific JSON (see NICHE_PROMPTS in the API's
     * agent_ai.py). Render the common shapes nicely; fall back to a raw
     * dump for any field this page doesn't specifically know about, so
     * adding a niche never requires a change here.
     */
    private function render_payload(array $payload): void
    {
        if (!empty($payload['question'])) {
            echo '<p class="engageai-question"><strong>' . esc_html__('Question:', 'engage-ai') . '</strong> ' . esc_html($payload['question']) . '</p>';
            return;
        }

        if (!empty($payload['action_type']) && in_array($payload['action_type'], ['channel_setup_guidance', 'content_idea'], true)) {
            $this->render_engagement_growth_payload($payload);
            return;
        }

        $simple_fields = [
            'working_title' => __('Working title:', 'engage-ai'),
            'hook' => __('Hook:', 'engage-ai'),
            'item_or_topic' => __('Item / topic:', 'engage-ai'),
            'estimated_value_range' => __('Estimated value:', 'engage-ai'),
            'answer' => __('Answer:', 'engage-ai'),
            'repurpose_idea' => __('Repurpose idea:', 'engage-ai'),
            'asset_type' => __('Asset type:', 'engage-ai'),
            'business_type' => __('Business type:', 'engage-ai'),
            'why_fit' => __('Why this fits:', 'engage-ai'),
            'product_category' => __('Product category:', 'engage-ai'),
            'script' => __('Script:', 'engage-ai'),
        ];
        foreach ($simple_fields as $field => $label) {
            if (!empty($payload[$field]) && is_string($payload[$field])) {
                echo '<p><strong>' . esc_html($label) . '</strong> ' . esc_html($payload[$field]) . '</p>';
            }
        }

        foreach (['outline' => __('Outline:', 'engage-ai'), 'thumbnail_concepts' => __('Thumbnail ideas:', 'engage-ai')] as $field => $label) {
            if (!empty($payload[$field]) && is_array($payload[$field])) {
                echo '<p><strong>' . esc_html($label) . '</strong></p><ul>';
                foreach ($payload[$field] as $line) {
                    if (is_string($line)) {
                        echo '<li>' . esc_html($line) . '</li>';
                    }
                }
                echo '</ul>';
            }
        }

        foreach (['content', 'pitch_email'] as $field) {
            if (!empty($payload[$field]) && is_array($payload[$field])) {
                echo '<div class="engageai-subfields">';
                foreach ($payload[$field] as $k => $v) {
                    if (is_string($v)) {
                        echo '<p><strong>' . esc_html(ucfirst((string) $k)) . ':</strong> ' . esc_html($v) . '</p>';
                    }
                }
                echo '</div>';
            } elseif (!empty($payload[$field]) && is_string($payload[$field])) {
                echo '<p>' . esc_html($payload[$field]) . '</p>';
            }
        }

        if (!empty($payload['why_this_now'])) {
            echo '<p class="engageai-why"><em>' . esc_html($payload['why_this_now']) . '</em></p>';
        }

        $known = array_merge(array_keys($simple_fields), ['outline', 'thumbnail_concepts', 'content', 'pitch_email', 'why_this_now']);
        $rest = array_diff_key($payload, array_flip($known));
        if (!empty($rest)) {
            echo '<details><summary>' . esc_html__('Other details', 'engage-ai') . '</summary><pre>' . esc_html(wp_json_encode($rest, JSON_PRETTY_PRINT)) . '</pre></details>';
        }
    }

    /**
     * The engagement_growth niche's two payload shapes - see NICHE_PROMPTS
     * in agent_ai.py. Both carry the channel and its current/target score
     * so the ticket is self-explanatory without cross-referencing the
     * Analytics page.
     * @param array{action_type: string, channel?: string, current_score?: int, target_score?: int, steps?: array, content?: string} $payload
     */
    private function render_engagement_growth_payload(array $payload): void
    {
        $channel = isset($payload['channel']) ? ucwords(str_replace('_', ' ', (string) $payload['channel'])) : '';
        $current = $payload['current_score'] ?? null;
        $target = $payload['target_score'] ?? null;
        if ($channel !== '') {
            echo '<p><strong>' . esc_html__('Channel:', 'engage-ai') . '</strong> ' . esc_html($channel);
            if ($current !== null) {
                echo ' &mdash; ' . esc_html(sprintf(__('score %1$s%2$s', 'engage-ai'), (string) $current, $target !== null ? sprintf(__(' (target %s)', 'engage-ai'), (string) $target) : ''));
            }
            echo '</p>';
        }

        if ($payload['action_type'] === 'channel_setup_guidance' && !empty($payload['steps']) && is_array($payload['steps'])) {
            echo '<p><strong>' . esc_html__('First-week setup plan:', 'engage-ai') . '</strong></p><ol>';
            foreach ($payload['steps'] as $step) {
                if (is_string($step)) {
                    echo '<li>' . esc_html($step) . '</li>';
                }
            }
            echo '</ol>';
        }

        if ($payload['action_type'] === 'content_idea' && !empty($payload['content']) && is_string($payload['content'])) {
            echo '<div class="engageai-subfields"><p>' . nl2br(esc_html($payload['content'])) . '</p></div>';
        }
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Agents', 'engage-ai'); ?></h1>
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

    private function redirect_with_notice(string $type, string $message, string $niche = ''): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), ['type' => $type, 'message' => $message], 60);
        $args = ['page' => 'engageai-agents'];
        if ($niche !== '') {
            $args['niche'] = $niche;
        }
        wp_safe_redirect(add_query_arg($args, admin_url('admin.php')));
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
