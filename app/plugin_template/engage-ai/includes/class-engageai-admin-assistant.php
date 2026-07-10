<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * A single free-form question box grounded in the org's stored context
 * (mission, tone, audience, ministries, etc) - for quick one-off questions
 * that don't fit any of the structured generators or agent niches.
 */
class EngageAI_Admin_Assistant
{
    private static ?EngageAI_Admin_Assistant $instance = null;
    private EngageAI_Api_Client $client;

    public static function instance(): EngageAI_Admin_Assistant
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
        add_action('admin_post_engageai_ask_assistant', [$this, 'handle_ask']);
    }

    public function handle_ask(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_ask_assistant')) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }

        $question = sanitize_textarea_field($_POST['engageai_question'] ?? '');
        if ($question === '') {
            $this->redirect_with_notice('error', __('Enter a question first.', 'engage-ai'));
        }

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization on the Settings page first.', 'engage-ai'));
        }

        $result = $this->client->ask_assistant($org_id, $question);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $this->redirect_with_notice('success', '', $question, $result['answer'] ?? '');
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

        if (!$this->client->get_organization_id()) {
            $this->render_not_ready(__('Select or create an organization on the Settings page first.', 'engage-ai'));
            return;
        }
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('AI Assistant', 'engage-ai'); ?></h1>
            <p class="description"><?php esc_html_e("Ask a one-off question - it's answered using this organization's stored context (mission, tone, audience, etc).", 'engage-ai'); ?></p>

            <?php $this->render_notice(); ?>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="engageai-assistant-form">
                <input type="hidden" name="action" value="engageai_ask_assistant">
                <?php wp_nonce_field('engageai_ask_assistant'); ?>
                <textarea name="engageai_question" rows="4" class="large-text" placeholder="<?php esc_attr_e('e.g. What should our next Instagram post be about?', 'engage-ai'); ?>" required></textarea>
                <p><?php submit_button(__('Ask', 'engage-ai'), 'primary', 'submit', false); ?></p>
            </form>
        </div>
        <?php
    }

    private function render_notice(): void
    {
        $key = 'engageai_notice_' . get_current_user_id();
        $notice = get_transient($key);
        if (!$notice) {
            return;
        }
        delete_transient($key);

        if ($notice['type'] === 'success' && !empty($notice['answer'])) {
            ?>
            <div class="engageai-card">
                <p class="engageai-rationale"><em><?php echo esc_html($notice['question']); ?></em></p>
                <p><?php echo nl2br(esc_html($notice['answer'])); ?></p>
            </div>
            <?php
            return;
        }

        $class = $notice['type'] === 'error' ? 'notice-error' : 'notice-success';
        printf('<div class="notice %s is-dismissible"><p>%s</p></div>', esc_attr($class), esc_html($notice['message']));
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('AI Assistant', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p><?php echo esc_html($message); ?></p></div>
            <p>
                <a href="<?php echo esc_url(admin_url('admin.php?page=engageai-settings')); ?>" class="button button-primary">
                    <?php esc_html_e('Go to Settings', 'engage-ai'); ?>
                </a>
            </p>
        </div>
        <?php
    }

    private function redirect_with_notice(string $type, string $message, string $question = '', string $answer = ''): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), [
            'type' => $type,
            'message' => $message,
            'question' => $question,
            'answer' => $answer,
        ], 60);
        wp_safe_redirect(add_query_arg(['page' => 'engageai-assistant'], admin_url('admin.php')));
        exit;
    }
}
