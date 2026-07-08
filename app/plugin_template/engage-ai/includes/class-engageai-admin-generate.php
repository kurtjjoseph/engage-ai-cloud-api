<?php

if (!defined('ABSPATH')) {
    exit;
}

class EngageAI_Admin_Generate
{
    private static ?EngageAI_Admin_Generate $instance = null;
    private EngageAI_Api_Client $client;
    private EngageAI_Post_Publisher $publisher;

    public static function instance(): EngageAI_Admin_Generate
    {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct()
    {
        $this->client = new EngageAI_Api_Client();
        $this->publisher = new EngageAI_Post_Publisher();
    }

    public function register_hooks(): void
    {
        add_action('admin_post_engageai_generate', [$this, 'handle_generate']);
    }

    public function handle_generate(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_generate')) {
            wp_die(esc_html__('Security check failed.', 'engage-ai'));
        }

        $task = sanitize_key($_POST['engageai_task'] ?? '');
        if (!in_array($task, ['event', 'announcements', 'sermon'], true)) {
            $this->redirect_with_notice('error', __('Unknown content type.', 'engage-ai'));
        }

        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect_with_notice('error', __('Select an organization on the Settings page first.', 'engage-ai'));
        }

        $payload = $this->build_payload($task, $org_id);
        $fallback_title = $payload['event_name'] ?? $payload['title'] ?? ('Engage AI - ' . $task);

        $result = $this->client->generate($task, $payload);
        if (is_wp_error($result)) {
            $this->redirect_with_notice('error', $result->get_error_message());
        }

        $publish_status = get_option('engageai_default_publish_status', 'draft');
        $post_id = $this->publisher->publish(
            $result['output_payload'] ?? [],
            $result['content_type'] ?? $task,
            $fallback_title,
            $publish_status
        );

        if (is_wp_error($post_id)) {
            $this->redirect_with_notice('error', $post_id->get_error_message());
        }

        $this->redirect_with_notice('success', __('Content generated.', 'engage-ai'), (int) $post_id);
    }

    private function build_payload(string $task, int $org_id): array
    {
        $payload = ['organization_id' => $org_id];

        switch ($task) {
            case 'event':
                $payload['event_name'] = sanitize_text_field($_POST['event_name'] ?? '');
                $payload['date'] = sanitize_text_field($_POST['date'] ?? '');
                $payload['time'] = sanitize_text_field($_POST['time'] ?? '');
                $payload['location'] = sanitize_text_field($_POST['location'] ?? '');
                $payload['speaker'] = sanitize_text_field($_POST['speaker'] ?? '');
                $payload['description'] = sanitize_textarea_field($_POST['description'] ?? '');
                $payload['target_audience'] = sanitize_text_field($_POST['target_audience'] ?? '');
                $payload['desired_action'] = sanitize_text_field($_POST['desired_action'] ?? 'Attend the event');
                break;

            case 'announcements':
                $payload['service_date'] = sanitize_text_field($_POST['service_date'] ?? '');
                $payload['speaker'] = sanitize_text_field($_POST['speaker'] ?? '');
                $payload['special_notes'] = $this->lines_to_array($_POST['special_notes'] ?? '');
                $payload['events'] = [];
                $payload['birthdays'] = [];
                break;

            case 'sermon':
                $payload['title'] = sanitize_text_field($_POST['title'] ?? '');
                $payload['sermon_text'] = sanitize_textarea_field($_POST['sermon_text'] ?? '');
                $payload['bible_translation'] = sanitize_text_field($_POST['bible_translation'] ?? 'HSV');
                $payload['target_audience'] = sanitize_text_field($_POST['target_audience'] ?? 'church members and visitors');
                break;
        }

        return $payload;
    }

    private function lines_to_array(string $raw): array
    {
        $lines = array_map('trim', explode("\n", $raw));
        return array_values(array_filter($lines, static fn($line) => $line !== ''));
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

        $tab = sanitize_key($_GET['tab'] ?? 'event');
        if (!in_array($tab, ['event', 'announcements', 'sermon'], true)) {
            $tab = 'event';
        }
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Generate Content', 'engage-ai'); ?></h1>
            <?php $this->render_result_notice(); ?>

            <h2 class="nav-tab-wrapper">
                <?php foreach (['event' => __('Event Campaign', 'engage-ai'), 'announcements' => __('Weekly Announcements', 'engage-ai'), 'sermon' => __('Sermon Engagement', 'engage-ai')] as $key => $label): ?>
                    <a href="<?php echo esc_url(add_query_arg(['page' => 'engageai-generate', 'tab' => $key], admin_url('admin.php'))); ?>"
                       class="nav-tab <?php echo $tab === $key ? 'nav-tab-active' : ''; ?>">
                        <?php echo esc_html($label); ?>
                    </a>
                <?php endforeach; ?>
            </h2>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" class="engageai-generate-form">
                <input type="hidden" name="action" value="engageai_generate">
                <input type="hidden" name="engageai_task" value="<?php echo esc_attr($tab); ?>">
                <?php wp_nonce_field('engageai_generate'); ?>

                <?php if ($tab === 'event'): ?>
                    <?php $this->render_event_fields(); ?>
                <?php elseif ($tab === 'announcements'): ?>
                    <?php $this->render_announcements_fields(); ?>
                <?php else: ?>
                    <?php $this->render_sermon_fields(); ?>
                <?php endif; ?>

                <?php submit_button(__('Generate & Create Post', 'engage-ai')); ?>
                <p class="description">
                    <?php
                    $status = get_option('engageai_default_publish_status', 'draft');
                    printf(
                        /* translators: %s: draft, pending, or publish */
                        esc_html__('New post will be created with status: %s (change this on the Settings page).', 'engage-ai'),
                        '<strong>' . esc_html($status) . '</strong>'
                    );
                    ?>
                </p>
            </form>
        </div>
        <?php
    }

    private function render_event_fields(): void
    {
        ?>
        <table class="form-table">
            <tr><th><label for="event_name"><?php esc_html_e('Event name', 'engage-ai'); ?></label></th>
                <td><input type="text" id="event_name" name="event_name" class="regular-text" required></td></tr>
            <tr><th><label for="date"><?php esc_html_e('Date', 'engage-ai'); ?></label></th>
                <td><input type="text" id="date" name="date" class="regular-text" placeholder="2026-08-02" required></td></tr>
            <tr><th><label for="time"><?php esc_html_e('Time', 'engage-ai'); ?></label></th>
                <td><input type="text" id="time" name="time" class="regular-text" placeholder="10:00"></td></tr>
            <tr><th><label for="location"><?php esc_html_e('Location', 'engage-ai'); ?></label></th>
                <td><input type="text" id="location" name="location" class="regular-text"></td></tr>
            <tr><th><label for="speaker"><?php esc_html_e('Speaker', 'engage-ai'); ?></label></th>
                <td><input type="text" id="speaker" name="speaker" class="regular-text"></td></tr>
            <tr><th><label for="description"><?php esc_html_e('Description', 'engage-ai'); ?></label></th>
                <td><textarea id="description" name="description" class="large-text" rows="4"></textarea></td></tr>
            <tr><th><label for="target_audience"><?php esc_html_e('Target audience', 'engage-ai'); ?></label></th>
                <td><input type="text" id="target_audience" name="target_audience" class="regular-text"></td></tr>
            <tr><th><label for="desired_action"><?php esc_html_e('Desired action', 'engage-ai'); ?></label></th>
                <td><input type="text" id="desired_action" name="desired_action" class="regular-text" value="Attend the event"></td></tr>
        </table>
        <?php
    }

    private function render_announcements_fields(): void
    {
        ?>
        <table class="form-table">
            <tr><th><label for="service_date"><?php esc_html_e('Service date', 'engage-ai'); ?></label></th>
                <td><input type="text" id="service_date" name="service_date" class="regular-text" placeholder="2026-07-12" required></td></tr>
            <tr><th><label for="speaker"><?php esc_html_e('Speaker', 'engage-ai'); ?></label></th>
                <td><input type="text" id="speaker" name="speaker" class="regular-text"></td></tr>
            <tr><th><label for="special_notes"><?php esc_html_e('Special notes (one per line)', 'engage-ai'); ?></label></th>
                <td><textarea id="special_notes" name="special_notes" class="large-text" rows="4"></textarea></td></tr>
        </table>
        <?php
    }

    private function render_sermon_fields(): void
    {
        ?>
        <table class="form-table">
            <tr><th><label for="title"><?php esc_html_e('Sermon title', 'engage-ai'); ?></label></th>
                <td><input type="text" id="title" name="title" class="regular-text" required></td></tr>
            <tr><th><label for="sermon_text"><?php esc_html_e('Sermon points / dictated text', 'engage-ai'); ?></label></th>
                <td><textarea id="sermon_text" name="sermon_text" class="large-text" rows="8" required></textarea></td></tr>
            <tr><th><label for="bible_translation"><?php esc_html_e('Bible translation', 'engage-ai'); ?></label></th>
                <td><input type="text" id="bible_translation" name="bible_translation" class="regular-text" value="HSV"></td></tr>
            <tr><th><label for="target_audience"><?php esc_html_e('Target audience', 'engage-ai'); ?></label></th>
                <td><input type="text" id="target_audience" name="target_audience" class="regular-text" value="church members and visitors"></td></tr>
        </table>
        <?php
    }

    private function render_not_ready(string $message): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Generate Content', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p><?php echo esc_html($message); ?></p></div>
            <p>
                <a href="<?php echo esc_url(admin_url('admin.php?page=engageai-settings')); ?>" class="button button-primary">
                    <?php esc_html_e('Go to Settings', 'engage-ai'); ?>
                </a>
            </p>
        </div>
        <?php
    }

    private function redirect_with_notice(string $type, string $message, ?int $post_id = null): void
    {
        set_transient('engageai_notice_' . get_current_user_id(), [
            'type' => $type,
            'message' => $message,
            'post_id' => $post_id,
        ], 60);
        wp_safe_redirect(add_query_arg(['page' => 'engageai-generate'], admin_url('admin.php')));
        exit;
    }

    private function render_result_notice(): void
    {
        $key = 'engageai_notice_' . get_current_user_id();
        $notice = get_transient($key);
        if (!$notice) {
            return;
        }
        delete_transient($key);

        $class = $notice['type'] === 'error' ? 'notice-error' : 'notice-success';
        echo '<div class="notice ' . esc_attr($class) . ' is-dismissible"><p>' . esc_html($notice['message']) . '</p>';

        if (!empty($notice['post_id'])) {
            $post_id = (int) $notice['post_id'];
            printf(
                ' <a href="%s">%s</a>',
                esc_url(get_edit_post_link($post_id)),
                esc_html__('Edit the created post', 'engage-ai')
            );
            echo '</p>';
            $this->render_other_channels($post_id);
        } else {
            echo '</p>';
        }

        echo '</div>';
    }

    private function render_other_channels(int $post_id): void
    {
        $social = get_post_meta($post_id, EngageAI_Post_Publisher::META_SOCIAL_MEDIA, true);
        $email = get_post_meta($post_id, EngageAI_Post_Publisher::META_EMAIL, true);
        $whatsapp = get_post_meta($post_id, EngageAI_Post_Publisher::META_WHATSAPP, true);
        $slides = get_post_meta($post_id, EngageAI_Post_Publisher::META_SLIDES, true);
        $follow_up = get_post_meta($post_id, EngageAI_Post_Publisher::META_FOLLOW_UP, true);
        ?>
        <div class="engageai-channels">
            <?php if (!empty($social['caption'])): ?>
                <h4><?php esc_html_e('Social media caption', 'engage-ai'); ?></h4>
                <p><?php echo esc_html($social['caption']); ?></p>
                <?php if (!empty($social['hashtags'])): ?>
                    <p><em><?php echo esc_html(implode(' ', array_map(static fn($tag) => '#' . ltrim($tag, '#'), $social['hashtags']))); ?></em></p>
                <?php endif; ?>
            <?php endif; ?>

            <?php if (!empty($email['subject']) || !empty($email['body_html'])): ?>
                <h4><?php esc_html_e('Email', 'engage-ai'); ?></h4>
                <p><strong><?php echo esc_html($email['subject'] ?? ''); ?></strong></p>
                <div><?php echo wp_kses_post($email['body_html'] ?? ''); ?></div>
            <?php endif; ?>

            <?php if (!empty($whatsapp['message'])): ?>
                <h4><?php esc_html_e('WhatsApp message', 'engage-ai'); ?></h4>
                <p><?php echo esc_html($whatsapp['message']); ?></p>
            <?php endif; ?>

            <?php if (!empty($slides)): ?>
                <h4><?php esc_html_e('Slides', 'engage-ai'); ?></h4>
                <ol>
                    <?php foreach ($slides as $slide): ?>
                        <li><strong><?php echo esc_html($slide['title'] ?? ''); ?></strong> — <?php echo esc_html($slide['body'] ?? ''); ?></li>
                    <?php endforeach; ?>
                </ol>
            <?php endif; ?>

            <?php if (!empty($follow_up)): ?>
                <h4><?php esc_html_e('Follow-up actions', 'engage-ai'); ?></h4>
                <ul>
                    <?php foreach ($follow_up as $action): ?>
                        <li><?php echo esc_html($action); ?></li>
                    <?php endforeach; ?>
                </ul>
            <?php endif; ?>
        </div>
        <?php
    }
}
