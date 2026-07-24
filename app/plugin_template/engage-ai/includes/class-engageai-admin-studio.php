<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Content Studio: one piece of content, built in passes.
 *
 *   1  Goal      what the business is trying to achieve
 *   2  Idea      competing ideas for that goal - pick one
 *   3  Draft     the real copy, with a quality check you can act on
 *   4  Media     the image or the 8-second video, rendered
 *   5  Publish   into WordPress, or copied out to the channel
 *
 * Each pass is its own screen and its own request. That is the point: the
 * operator can stop, read, change the format, rewrite a line or send it back
 * for another pass, instead of pressing one button and hoping. Everything is
 * persisted on the API against the content item, so a half-built piece can be
 * left and picked up later.
 *
 * The media pass renders on the API in the background (a generated image takes
 * tens of seconds, and a video needs several), so this page polls over AJAX
 * and imports the finished file into the Media Library.
 */
class EngageAI_Admin_Studio
{
    private static ?EngageAI_Admin_Studio $instance = null;
    private EngageAI_Api_Client $client;
    private EngageAI_Post_Publisher $publisher;

    /** Where the ideas from pass 1 live between requests (they aren't saved until one is chosen). */
    private const IDEAS_TRANSIENT = 'engageai_studio_ideas_';
    /** Media Library attachment id for a rendered piece, so the preview survives a reload. */
    private const MEDIA_TRANSIENT = 'engageai_studio_media_';

    public static function instance(): EngageAI_Admin_Studio
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
        add_action('admin_post_engageai_studio_ideas', [$this, 'handle_ideas']);
        add_action('admin_post_engageai_studio_draft', [$this, 'handle_draft']);
        add_action('admin_post_engageai_studio_save', [$this, 'handle_save']);
        add_action('admin_post_engageai_studio_improve', [$this, 'handle_improve']);
        add_action('admin_post_engageai_studio_render', [$this, 'handle_render']);
        add_action('admin_post_engageai_studio_publish', [$this, 'handle_publish']);
        add_action('wp_ajax_engageai_studio_render_status', [$this, 'ajax_render_status']);
    }

    /* ------------------------------------------------------------ handlers */

    public function handle_ideas(): void
    {
        $this->guard('engageai_studio_ideas');
        $org_id = (int) $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $goal = sanitize_key($_POST['goal'] ?? 'awareness');
        $notes = sanitize_textarea_field($_POST['notes'] ?? '');
        $result = $this->client->studio_ideas($org_id, $goal, $notes, 3);
        if (is_wp_error($result)) {
            $this->redirect(['step' => 'goal', 'error' => rawurlencode($result->get_error_message())]);
        }
        set_transient(self::IDEAS_TRANSIENT . get_current_user_id(),
            ['goal' => $goal, 'notes' => $notes, 'ideas' => $result['ideas'] ?? []], HOUR_IN_SECONDS);
        $this->redirect(['step' => 'idea']);
    }

    public function handle_draft(): void
    {
        $this->guard('engageai_studio_draft');
        $org_id = (int) $this->client->get_organization_id();
        $stored = get_transient(self::IDEAS_TRANSIENT . get_current_user_id());
        $index = (int) ($_POST['idea_index'] ?? -1);
        $ideas = is_array($stored) ? ($stored['ideas'] ?? []) : [];
        if (!$org_id || !isset($ideas[$index])) {
            $this->redirect(['step' => 'goal', 'error' => rawurlencode(__('That idea has expired - start again.', 'engage-ai'))]);
        }
        $format = sanitize_key($_POST['format'] ?? 'post_image');
        $channel = sanitize_key($_POST['channel'] ?? 'instagram');
        $goal = sanitize_key($stored['goal'] ?? 'awareness');

        $item = $this->client->studio_draft($org_id, $ideas[$index], $format, $channel, $goal);
        if (is_wp_error($item)) {
            $this->redirect(['step' => 'idea', 'error' => rawurlencode($item->get_error_message())]);
        }
        $this->redirect(['step' => 'draft', 'content_id' => (int) ($item['id'] ?? 0)]);
    }

    public function handle_save(): void
    {
        $this->guard('engageai_studio_save');
        $org_id = (int) $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }

        // wp_kses_post because a website piece's body is HTML; it leaves the
        // plain-text bodies of every other channel alone.
        $fields = ['body' => wp_kses_post(wp_unslash($_POST['body'] ?? ''))];
        if (isset($_POST['hashtags'])) {
            $tags = array_filter(array_map('trim', explode(',', str_replace('#', '', wp_unslash($_POST['hashtags'])))));
            $fields['hashtags'] = array_values($tags);
        }
        foreach (['headline', 'subhead', 'cta'] as $key) {
            if (isset($_POST[$key])) {
                $fields[$key] = sanitize_text_field(wp_unslash($_POST[$key]));
            }
        }
        if (isset($_POST['narrations']) && is_array($_POST['narrations'])) {
            $fields['narrations'] = array_map('sanitize_text_field', array_map('wp_unslash', $_POST['narrations']));
        }

        $result = $this->client->studio_edit($org_id, $content_id, $fields);
        if (is_wp_error($result)) {
            $this->redirect(['step' => 'draft', 'content_id' => $content_id, 'error' => rawurlencode($result->get_error_message())]);
        }
        $next = ($_POST['then'] ?? '') === 'media' ? 'media' : 'draft';
        $this->redirect(['step' => $next, 'content_id' => $content_id, 'saved' => 1]);
    }

    public function handle_improve(): void
    {
        $this->guard('engageai_studio_improve');
        $org_id = (int) $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $result = $this->client->studio_check($org_id, $content_id, true);
        if (is_wp_error($result)) {
            $this->redirect(['step' => 'draft', 'content_id' => $content_id, 'error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['step' => 'draft', 'content_id' => $content_id, 'improved' => 1]);
    }

    public function handle_render(): void
    {
        $this->guard('engageai_studio_render');
        $org_id = (int) $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        delete_transient(self::MEDIA_TRANSIENT . $content_id);
        $result = $this->client->studio_render($org_id, $content_id);
        if (is_wp_error($result)) {
            $this->redirect(['step' => 'media', 'content_id' => $content_id, 'error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['step' => 'media', 'content_id' => $content_id]);
    }

    public function handle_publish(): void
    {
        $this->guard('engageai_studio_publish');
        $org_id = (int) $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $item = $this->find_item($org_id, $content_id);
        if (!$item) {
            $this->redirect(['error' => rawurlencode(__('Could not find that content item.', 'engage-ai'))]);
        }
        $post_id = $this->publisher->publish(
            $item['output_payload'] ?? [],
            'website_post',
            (string) ($item['title'] ?? __('Engage AI post', 'engage-ai')),
            'draft'
        );
        if (is_wp_error($post_id)) {
            $this->redirect(['step' => 'publish', 'content_id' => $content_id, 'error' => rawurlencode($post_id->get_error_message())]);
        }
        // The rendered image becomes the post's featured image - that's the
        // whole point of having generated it alongside the copy.
        $attachment_id = (int) get_transient(self::MEDIA_TRANSIENT . $content_id);
        if ($attachment_id) {
            set_post_thumbnail($post_id, $attachment_id);
        }
        $this->redirect(['step' => 'publish', 'content_id' => $content_id, 'drafted' => (int) $post_id]);
    }

    /**
     * Polled by the media step while the API renders. When the render lands,
     * the file is pulled into the Media Library here (once - the attachment id
     * is cached), so the browser can show a preview from a normal WordPress URL
     * instead of needing the API's bearer token.
     */
    public function ajax_render_status(): void
    {
        if (!current_user_can('manage_options')) {
            wp_send_json_error(['message' => __('Not allowed.', 'engage-ai')], 403);
        }
        check_ajax_referer('engageai_studio_status');
        $org_id = (int) $this->client->get_organization_id();
        $content_id = (int) ($_REQUEST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            wp_send_json_error(['message' => __('Not connected.', 'engage-ai')], 400);
        }

        $status = $this->client->studio_render_status($org_id, $content_id);
        if (is_wp_error($status)) {
            wp_send_json_success(['status' => 'failed', 'error' => $status->get_error_message()]);
        }
        $state = (string) ($status['status'] ?? 'none');
        if ($state !== 'done') {
            wp_send_json_success(['status' => $state, 'error' => (string) ($status['error'] ?? '')]);
        }

        $attachment_id = (int) get_transient(self::MEDIA_TRANSIENT . $content_id);
        if (!$attachment_id) {
            $attachment_id = $this->import_asset($content_id, (int) ($status['asset_id'] ?? 0), (string) ($status['kind'] ?? 'image'));
            if (is_wp_error($attachment_id)) {
                wp_send_json_success(['status' => 'failed', 'error' => $attachment_id->get_error_message()]);
            }
        }
        wp_send_json_success([
            'status' => 'done',
            'attachment_id' => $attachment_id,
            'url' => wp_get_attachment_url($attachment_id),
        ]);
    }

    /** @return int|WP_Error attachment id */
    private function import_asset(int $content_id, int $asset_id, string $kind)
    {
        if (!$asset_id) {
            return new WP_Error('engageai_no_asset', __('The render finished but produced no file.', 'engage-ai'));
        }
        $bytes = $this->client->get_asset_bytes($asset_id);
        if (is_wp_error($bytes)) {
            return $bytes;
        }
        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';

        $mime = (string) ($bytes['mime'] ?: ($kind === 'video' ? 'video/mp4' : 'image/jpeg'));
        $ext = ['image/jpeg' => 'jpg', 'image/png' => 'png', 'image/webp' => 'webp', 'video/mp4' => 'mp4'][$mime] ?? 'jpg';
        $slug = 'engage-ai-' . $kind . '-' . $content_id . '-' . $asset_id;

        $upload = wp_upload_bits($slug . '.' . $ext, null, $bytes['body']);
        if (!empty($upload['error'])) {
            return new WP_Error('engageai_upload_failed', $upload['error']);
        }
        $attachment_id = wp_insert_attachment([
            'post_mime_type' => $mime,
            'post_title' => sanitize_file_name($slug),
            'post_status' => 'inherit',
        ], $upload['file']);
        if (is_wp_error($attachment_id)) {
            return $attachment_id;
        }
        wp_update_attachment_metadata($attachment_id, wp_generate_attachment_metadata($attachment_id, $upload['file']));
        set_transient(self::MEDIA_TRANSIENT . $content_id, (int) $attachment_id, WEEK_IN_SECONDS);
        return (int) $attachment_id;
    }

    /* --------------------------------------------------------------- render */

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        if (!$this->client->is_connected() || !$this->client->get_organization_id()) {
            $this->render_not_ready();
            return;
        }
        $step = sanitize_key($_GET['step'] ?? 'goal');
        $content_id = (int) ($_GET['content_id'] ?? 0);
        $steps = ['goal', 'idea', 'draft', 'media', 'publish'];
        if (!in_array($step, $steps, true)) {
            $step = 'goal';
        }
        ?>
        <div class="wrap engageai-studio">
            <div class="eas-masthead">
                <h1><?php esc_html_e('Content Studio', 'engage-ai'); ?></h1>
                <p><?php esc_html_e('One piece of content, built in passes: start from a business goal, choose an idea, shape the copy, check the quality, make the media, publish.', 'engage-ai'); ?></p>
            </div>
            <?php $this->render_stepper($steps, $step); ?>
            <?php $this->render_notice(); ?>
            <?php
            switch ($step) {
                case 'idea':
                    $this->step_idea();
                    break;
                case 'draft':
                    $this->step_draft($content_id);
                    break;
                case 'media':
                    $this->step_media($content_id);
                    break;
                case 'publish':
                    $this->step_publish($content_id);
                    break;
                default:
                    $this->step_goal();
            }
            ?>
        </div>
        <?php
    }

    private function render_stepper(array $steps, string $current): void
    {
        $labels = [
            'goal' => __('Goal', 'engage-ai'),
            'idea' => __('Idea', 'engage-ai'),
            'draft' => __('Copy & check', 'engage-ai'),
            'media' => __('Media', 'engage-ai'),
            'publish' => __('Publish', 'engage-ai'),
        ];
        $current_index = array_search($current, $steps, true);
        echo '<ol class="eas-steps">';
        foreach ($steps as $index => $key) {
            $class = 'eas-step';
            if ($key === $current) {
                $class .= ' is-current';
            } elseif ($index < $current_index) {
                $class .= ' is-done';
            }
            printf(
                '<li class="%s"><span class="eas-step__num">%d</span><span class="eas-step__label">%s</span></li>',
                esc_attr($class),
                $index + 1,
                esc_html($labels[$key])
            );
        }
        echo '</ol>';
    }

    /* ------------------------------------------------------------ 1. goal */

    private function step_goal(): void
    {
        $catalog = $this->client->get_studio_catalog();
        $goals = is_wp_error($catalog) ? [] : ($catalog['goals'] ?? []);
        ?>
        <div class="eas-panel">
            <h2><?php esc_html_e('What should this content do?', 'engage-ai'); ?></h2>
            <p><?php esc_html_e('Everything after this is shaped by the answer - the ideas, the angle, the call to action, and what the quality check holds the copy to.', 'engage-ai'); ?></p>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_studio_ideas">
                <?php wp_nonce_field('engageai_studio_ideas'); ?>
                <div class="eas-field">
                    <div class="eas-choices">
                        <?php foreach ($goals as $index => $goal): ?>
                            <label class="eas-choice">
                                <input type="radio" name="goal" value="<?php echo esc_attr($goal['key'] ?? ''); ?>" <?php checked($index, 0); ?>>
                                <span class="eas-choice__title"><?php echo esc_html($goal['label'] ?? ''); ?></span>
                                <span class="eas-choice__desc"><?php echo esc_html($goal['guidance'] ?? ''); ?></span>
                            </label>
                        <?php endforeach; ?>
                        <?php if (empty($goals)): ?>
                            <p class="eas-hint"><?php esc_html_e('Could not load the goal list from the API. Check the connection on the Settings page.', 'engage-ai'); ?></p>
                        <?php endif; ?>
                    </div>
                </div>
                <div class="eas-field">
                    <label class="eas-label" for="eas-notes"><?php esc_html_e('Anything specific? (optional)', 'engage-ai'); ?></label>
                    <input type="text" id="eas-notes" name="notes" placeholder="<?php esc_attr_e('e.g. we have three slots left this month', 'engage-ai'); ?>">
                    <p class="eas-hint"><?php esc_html_e('A product, an event, a date, an offer - anything the ideas should be built around.', 'engage-ai'); ?></p>
                </div>
                <div class="eas-actions">
                    <button type="submit" class="eas-btn"><?php esc_html_e('Get ideas', 'engage-ai'); ?></button>
                    <span class="eas-hint"><?php esc_html_e('Takes about a minute.', 'engage-ai'); ?></span>
                </div>
            </form>
        </div>
        <?php
    }

    /* ------------------------------------------------------------ 2. idea */

    private function step_idea(): void
    {
        $stored = get_transient(self::IDEAS_TRANSIENT . get_current_user_id());
        $ideas = is_array($stored) ? ($stored['ideas'] ?? []) : [];
        if (empty($ideas)) {
            $this->render_restart(__('Those ideas have expired.', 'engage-ai'));
            return;
        }
        $catalog = $this->client->get_studio_catalog();
        $formats = is_wp_error($catalog) ? [] : ($catalog['formats'] ?? []);
        ?>
        <div class="eas-panel eas-panel--plain">
            <div class="eas-panel">
                <h2><?php esc_html_e('Pick the idea to build', 'engage-ai'); ?></h2>
                <p><?php esc_html_e('Each one comes with the format and channel that suits it - change either before you write it.', 'engage-ai'); ?></p>
                <div class="eas-ideas">
                    <?php foreach ($ideas as $index => $idea): ?>
                        <div class="eas-idea">
                            <h3><?php echo esc_html($idea['headline'] ?? ''); ?></h3>
                            <?php if (!empty($idea['angle'])): ?><p><?php echo esc_html($idea['angle']); ?></p><?php endif; ?>
                            <?php if (!empty($idea['why'])): ?><p class="eas-idea__why"><?php echo esc_html($idea['why']); ?></p><?php endif; ?>
                            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                <input type="hidden" name="action" value="engageai_studio_draft">
                                <input type="hidden" name="idea_index" value="<?php echo esc_attr((string) $index); ?>">
                                <?php wp_nonce_field('engageai_studio_draft'); ?>
                                <div class="eas-idea__controls">
                                    <div>
                                        <label class="eas-label" for="eas-format-<?php echo esc_attr((string) $index); ?>"><?php esc_html_e('Format', 'engage-ai'); ?></label>
                                        <select id="eas-format-<?php echo esc_attr((string) $index); ?>" name="format">
                                            <?php foreach ($formats as $format): ?>
                                                <option value="<?php echo esc_attr($format['key'] ?? ''); ?>" <?php selected($format['key'] ?? '', $idea['format'] ?? ''); ?>>
                                                    <?php echo esc_html($format['label'] ?? ''); ?>
                                                </option>
                                            <?php endforeach; ?>
                                        </select>
                                    </div>
                                    <div>
                                        <label class="eas-label" for="eas-channel-<?php echo esc_attr((string) $index); ?>"><?php esc_html_e('Channel', 'engage-ai'); ?></label>
                                        <select id="eas-channel-<?php echo esc_attr((string) $index); ?>" name="channel">
                                            <?php foreach (($formats[0]['channels'] ?? []) as $channel): ?>
                                                <option value="<?php echo esc_attr($channel['key'] ?? ''); ?>" <?php selected($channel['key'] ?? '', $idea['channel'] ?? ''); ?>>
                                                    <?php echo esc_html($channel['label'] ?? ''); ?>
                                                </option>
                                            <?php endforeach; ?>
                                        </select>
                                    </div>
                                    <div style="flex:0 0 auto;">
                                        <button type="submit" class="eas-btn"><?php esc_html_e('Write this', 'engage-ai'); ?></button>
                                    </div>
                                </div>
                            </form>
                        </div>
                    <?php endforeach; ?>
                </div>
                <div class="eas-actions">
                    <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url(['step' => 'goal'])); ?>"><?php esc_html_e('Start over', 'engage-ai'); ?></a>
                </div>
            </div>
        </div>
        <?php
    }

    /* ----------------------------------------------------------- 3. draft */

    private function step_draft(int $content_id): void
    {
        $org_id = (int) $this->client->get_organization_id();
        $item = $content_id ? $this->find_item($org_id, $content_id) : null;
        if (!$item) {
            $this->render_restart(__('That draft could not be loaded.', 'engage-ai'));
            return;
        }
        $out = $item['output_payload'] ?? [];
        $state = $out['studio'] ?? [];
        $layout = $state['layout'] ?? [];
        $format = (string) ($state['format'] ?? 'post_image');
        $quality = $state['quality'] ?? [];
        $body = (string) ($out['body'] ?? '');
        $overlay = $out['overlay'] ?? [];
        $slides = $out['slides'] ?? [];
        ?>
        <div class="eas-panel">
            <div class="eas-meta">
                <span class="eas-badge"><?php echo esc_html($out['content_type_label'] ?? ''); ?></span>
                <span class="eas-badge"><?php echo esc_html($this->channel_label((string) ($state['channel'] ?? ''))); ?></span>
                <span class="eas-badge"><?php echo esc_html(sprintf('%d × %d', (int) ($layout['width'] ?? 0), (int) ($layout['height'] ?? 0))); ?></span>
                <?php $this->render_score_badge($quality); ?>
            </div>
            <h2><?php echo esc_html($item['title'] ?? ''); ?></h2>
            <p><?php echo esc_html($layout['notes'] ?? ''); ?></p>

            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                <input type="hidden" name="action" value="engageai_studio_save">
                <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $content_id); ?>">
                <?php wp_nonce_field('engageai_studio_save'); ?>

                <?php if ($format === 'image_text'): ?>
                    <div class="eas-field">
                        <label class="eas-label" for="eas-headline"><?php esc_html_e('Headline on the image', 'engage-ai'); ?></label>
                        <input type="text" id="eas-headline" name="headline" value="<?php echo esc_attr((string) ($overlay['headline'] ?? '')); ?>" maxlength="<?php echo esc_attr((string) ($layout['headline_max'] ?? 70)); ?>">
                    </div>
                    <div class="eas-row eas-field">
                        <div>
                            <label class="eas-label" for="eas-subhead"><?php esc_html_e('Sub-headline', 'engage-ai'); ?></label>
                            <input type="text" id="eas-subhead" name="subhead" value="<?php echo esc_attr((string) ($overlay['subhead'] ?? '')); ?>">
                        </div>
                        <div>
                            <label class="eas-label" for="eas-cta"><?php esc_html_e('Button text', 'engage-ai'); ?></label>
                            <input type="text" id="eas-cta" name="cta" value="<?php echo esc_attr((string) ($overlay['cta'] ?? '')); ?>">
                        </div>
                    </div>
                <?php endif; ?>

                <?php if ($format === 'video_slideshow'): ?>
                    <div class="eas-field">
                        <label class="eas-label"><?php esc_html_e('Narration - one line per slide, 2 seconds each', 'engage-ai'); ?></label>
                        <div class="eas-slides">
                            <?php foreach ($slides as $index => $slide): ?>
                                <div class="eas-slide">
                                    <span class="eas-slide__n"><?php echo esc_html((string) ($index + 1)); ?></span>
                                    <input type="text" name="narrations[]" value="<?php echo esc_attr((string) ($slide['narration'] ?? '')); ?>" maxlength="90">
                                </div>
                            <?php endforeach; ?>
                        </div>
                        <p class="eas-hint"><?php esc_html_e('This is what appears centred on screen, and it is the whole script. Keep each line short enough to read in two seconds.', 'engage-ai'); ?></p>
                    </div>
                <?php endif; ?>

                <div class="eas-field">
                    <label class="eas-label" for="eas-body">
                        <?php echo esc_html($format === 'post_image' ? __('The post', 'engage-ai') : __('Caption to post with it', 'engage-ai')); ?>
                    </label>
                    <textarea id="eas-body" name="body" rows="12"><?php echo esc_textarea($body); ?></textarea>
                    <p class="eas-hint"><?php echo esc_html(sprintf(
                        /* translators: 1: length guidance, 2: character limit */
                        __('Aim for %1$s. Hard limit %2$d characters.', 'engage-ai'),
                        (string) ($layout['body_target'] ?? ''),
                        (int) ($layout['body_max'] ?? 0)
                    )); ?></p>
                </div>

                <?php if ((int) ($layout['hashtags_max'] ?? 0) > 0): ?>
                    <div class="eas-field">
                        <label class="eas-label" for="eas-hashtags"><?php esc_html_e('Hashtags', 'engage-ai'); ?></label>
                        <input type="text" id="eas-hashtags" name="hashtags" value="<?php echo esc_attr(implode(', ', array_map('sanitize_text_field', (array) ($out['hashtags'] ?? [])))); ?>">
                        <p class="eas-hint"><?php echo esc_html(sprintf(
                            /* translators: %d: maximum number of hashtags */
                            __('Comma separated, up to %d for this channel.', 'engage-ai'),
                            (int) ($layout['hashtags_max'] ?? 0)
                        )); ?></p>
                    </div>
                <?php endif; ?>

                <div class="eas-actions">
                    <button type="submit" name="then" value="media" class="eas-btn"><?php esc_html_e('Save and make the media', 'engage-ai'); ?></button>
                    <button type="submit" name="then" value="draft" class="eas-btn eas-btn--ghost"><?php esc_html_e('Save and re-check', 'engage-ai'); ?></button>
                </div>
            </form>
        </div>

        <?php $this->render_quality($quality, $content_id); ?>
        <?php
    }

    private function render_score_badge(array $quality): void
    {
        if (empty($quality)) {
            return;
        }
        $score = (int) ($quality['score'] ?? 0);
        $class = $score >= 90 ? 'eas-badge--ok' : ($score >= 60 ? 'eas-badge--warn' : 'eas-badge--bad');
        printf(
            '<span class="eas-badge %s">%s</span>',
            esc_attr($class),
            esc_html(sprintf(
                /* translators: %d: quality score out of 100 */
                __('Quality %d/100', 'engage-ai'),
                $score
            ))
        );
    }

    private function render_quality(array $quality, int $content_id): void
    {
        if (empty($quality)) {
            return;
        }
        $issues = $quality['issues'] ?? [];
        $fixed = $quality['fixed'] ?? [];
        ?>
        <div class="eas-quality">
            <div class="eas-quality__head">
                <strong><?php esc_html_e('Quality check', 'engage-ai'); ?></strong>
                <?php if (empty($issues) && empty($fixed)): ?>
                    <span class="eas-badge eas-badge--ok"><?php esc_html_e('Nothing to fix', 'engage-ai'); ?></span>
                <?php endif; ?>
            </div>
            <?php if (empty($issues) && empty($fixed)): ?>
                <p class="eas-hint" style="margin:0;"><?php esc_html_e('The copy is within this channel\'s limits and has everything the format needs.', 'engage-ai'); ?></p>
            <?php else: ?>
                <ul>
                    <?php foreach ($fixed as $note): ?>
                        <li class="is-fixed"><span><?php esc_html_e('Fixed', 'engage-ai'); ?></span><?php echo esc_html($note); ?></li>
                    <?php endforeach; ?>
                    <?php foreach ($issues as $issue): ?>
                        <li class="is-<?php echo esc_attr((string) ($issue['severity'] ?? 'warning')); ?>">
                            <span><?php echo esc_html(($issue['severity'] ?? '') === 'error' ? __('Problem', 'engage-ai') : __('Check', 'engage-ai')); ?></span>
                            <?php echo esc_html((string) ($issue['message'] ?? '')); ?>
                        </li>
                    <?php endforeach; ?>
                </ul>
                <?php if (!empty($issues)): ?>
                    <div class="eas-actions">
                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                            <input type="hidden" name="action" value="engageai_studio_improve">
                            <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $content_id); ?>">
                            <?php wp_nonce_field('engageai_studio_improve'); ?>
                            <button type="submit" class="eas-btn eas-btn--ghost"><?php esc_html_e('Have the AI fix these', 'engage-ai'); ?></button>
                        </form>
                    </div>
                <?php endif; ?>
            <?php endif; ?>
        </div>
        <?php
    }

    /* ----------------------------------------------------------- 4. media */

    private function step_media(int $content_id): void
    {
        $org_id = (int) $this->client->get_organization_id();
        $item = $content_id ? $this->find_item($org_id, $content_id) : null;
        if (!$item) {
            $this->render_restart(__('That piece could not be loaded.', 'engage-ai'));
            return;
        }
        $out = $item['output_payload'] ?? [];
        $state = $out['studio'] ?? [];
        $render = $state['render'] ?? [];
        $status = (string) ($render['status'] ?? 'none');
        $format = (string) ($state['format'] ?? 'post_image');
        $is_video = $format === 'video_slideshow';
        $attachment_id = (int) get_transient(self::MEDIA_TRANSIENT . $content_id);
        ?>
        <div class="eas-panel">
            <h2><?php echo esc_html($is_video ? __('The 8-second video', 'engage-ai') : __('The image', 'engage-ai')); ?></h2>
            <p><?php echo esc_html($is_video
                ? __('Four slides, two seconds each, with your narration centred on screen. It renders on the server and lands in your Media Library.', 'engage-ai')
                : __('Rendered at this channel\'s exact size and saved straight into your Media Library.', 'engage-ai')); ?></p>

            <?php if ($status === 'done' && $attachment_id): ?>
                <div class="eas-preview">
                    <div class="eas-preview__frame">
                        <?php if ($is_video): ?>
                            <video controls playsinline style="max-height:520px;" src="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>"></video>
                        <?php else: ?>
                            <img alt="<?php echo esc_attr((string) ($out['image_alt'] ?? '')); ?>" src="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>">
                        <?php endif; ?>
                    </div>
                    <div class="eas-preview__body">
                        <span class="eas-badge eas-badge--ok"><?php esc_html_e('Saved to your Media Library', 'engage-ai'); ?></span>
                        <p class="eas-hint" style="margin-top:14px;">
                            <a href="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>" target="_blank" rel="noopener"><?php esc_html_e('Open the file →', 'engage-ai'); ?></a>
                        </p>
                        <div class="eas-actions">
                            <a class="eas-btn" href="<?php echo esc_url($this->url(['step' => 'publish', 'content_id' => $content_id])); ?>"><?php esc_html_e('Continue to publish', 'engage-ai'); ?></a>
                            <?php $this->render_button(__('Make another version', 'engage-ai'), 'engageai_studio_render', $content_id, 'eas-btn eas-btn--ghost'); ?>
                        </div>
                    </div>
                </div>
            <?php elseif ($status === 'running' || $status === 'done'): // "done" with no attachment yet: the poller imports it ?>
                <div class="eas-working" id="eas-working">
                    <span class="eas-spinner"></span>
                    <span><?php echo esc_html($is_video
                        ? __('Rendering the video. This takes a couple of minutes - the backgrounds are generated one at a time. You can leave this page open.', 'engage-ai')
                        : __('Generating the image. This usually takes under a minute.', 'engage-ai')); ?></span>
                </div>
                <?php $this->render_poller($content_id); ?>
            <?php else: ?>
                <?php if ($status === 'failed' && !empty($render['error'])): ?>
                    <div class="eas-notice eas-notice--bad"><?php echo esc_html((string) $render['error']); ?></div>
                <?php endif; ?>
                <div class="eas-actions">
                    <?php $this->render_button(
                        $is_video ? __('Make the video', 'engage-ai') : __('Make the image', 'engage-ai'),
                        'engageai_studio_render',
                        $content_id
                    ); ?>
                    <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url(['step' => 'draft', 'content_id' => $content_id])); ?>"><?php esc_html_e('Back to the copy', 'engage-ai'); ?></a>
                </div>
            <?php endif; ?>
        </div>
        <?php
    }

    /**
     * Polls the API through admin-ajax while a render runs, and reloads the
     * page once it lands (the reload is what shows the preview, since the
     * finished file is imported into the Media Library server-side).
     */
    private function render_poller(int $content_id): void
    {
        $nonce = wp_create_nonce('engageai_studio_status');
        ?>
        <script>
        (function () {
            var url = <?php echo wp_json_encode(admin_url('admin-ajax.php')); ?>;
            var body = 'action=engageai_studio_render_status&content_id=<?php echo (int) $content_id; ?>&_wpnonce=<?php echo esc_js($nonce); ?>';
            var tries = 0;
            function poll() {
                tries++;
                if (tries > 90) { return; } // ~6 minutes, then stop nagging the server
                fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: body
                }).then(function (r) { return r.json(); }).then(function (json) {
                    var data = (json && json.data) || {};
                    // Reload only once the file exists in the Media Library -
                    // reloading on a bare "done" could bounce between this
                    // screen and itself if the import is what failed.
                    if (data.status === 'done' && data.url) {
                        window.location.reload();
                        return;
                    }
                    if (data.status === 'failed') {
                        var box = document.getElementById('eas-working');
                        if (box) {
                            box.className = 'eas-notice eas-notice--bad';
                            box.textContent = data.error || <?php echo wp_json_encode(__('The render failed. Try again.', 'engage-ai')); ?>;
                        }
                        return;
                    }
                    setTimeout(poll, 4000);
                }).catch(function () { setTimeout(poll, 6000); });
            }
            setTimeout(poll, 4000);
        })();
        </script>
        <?php
    }

    /* --------------------------------------------------------- 5. publish */

    private function step_publish(int $content_id): void
    {
        $org_id = (int) $this->client->get_organization_id();
        $item = $content_id ? $this->find_item($org_id, $content_id) : null;
        if (!$item) {
            $this->render_restart(__('That piece could not be loaded.', 'engage-ai'));
            return;
        }
        $out = $item['output_payload'] ?? [];
        $state = $out['studio'] ?? [];
        $channel = (string) ($state['channel'] ?? '');
        $attachment_id = (int) get_transient(self::MEDIA_TRANSIENT . $content_id);
        $hashtags = (array) ($out['hashtags'] ?? []);
        $body = (string) ($out['body'] ?? '');
        ?>
        <div class="eas-panel">
            <h2><?php esc_html_e('Ready to publish', 'engage-ai'); ?></h2>
            <p><?php echo esc_html(sprintf(
                /* translators: %s: channel name, e.g. Instagram */
                __('For %s. The media is in your Media Library; the copy is below, ready to use as written.', 'engage-ai'),
                $this->channel_label($channel)
            )); ?></p>

            <div class="eas-preview">
                <?php if ($attachment_id): ?>
                    <div class="eas-preview__frame">
                        <?php if ((string) ($state['format'] ?? '') === 'video_slideshow'): ?>
                            <video controls playsinline style="max-height:420px;" src="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>"></video>
                        <?php else: ?>
                            <img alt="" src="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>">
                        <?php endif; ?>
                    </div>
                <?php endif; ?>
                <div class="eas-preview__body">
                    <?php if ($channel === 'website'): ?>
                        <p class="eas-hint" style="margin-top:0;"><?php esc_html_e('This one publishes straight into WordPress as a draft, with the image set as its featured image.', 'engage-ai'); ?></p>
                        <div class="eas-actions" style="margin-top:12px;">
                            <?php $this->render_button(__('Create the WordPress draft', 'engage-ai'), 'engageai_studio_publish', $content_id); ?>
                        </div>
                    <?php else: ?>
                        <pre class="eas-copyblock" id="eas-copy"><?php echo esc_html($body); ?></pre>
                        <?php if (!empty($hashtags)): ?>
                            <p class="eas-tags"><?php echo esc_html('#' . implode(' #', array_map('sanitize_text_field', $hashtags))); ?></p>
                        <?php endif; ?>
                        <div class="eas-actions">
                            <button type="button" class="eas-btn" id="eas-copy-btn"><?php esc_html_e('Copy the text', 'engage-ai'); ?></button>
                            <?php if ($attachment_id): ?>
                                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url((string) wp_get_attachment_url($attachment_id)); ?>" download><?php esc_html_e('Download the media', 'engage-ai'); ?></a>
                            <?php endif; ?>
                        </div>
                        <script>
                        document.getElementById('eas-copy-btn').addEventListener('click', function () {
                            var text = document.getElementById('eas-copy').textContent;
                            navigator.clipboard.writeText(text).then(function () {
                                var b = document.getElementById('eas-copy-btn');
                                b.textContent = <?php echo wp_json_encode(__('Copied', 'engage-ai')); ?>;
                            });
                        });
                        </script>
                    <?php endif; ?>
                </div>
            </div>

            <div class="eas-actions">
                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url($this->url(['step' => 'goal'])); ?>"><?php esc_html_e('Make another piece', 'engage-ai'); ?></a>
                <a class="eas-btn eas-btn--ghost" href="<?php echo esc_url(admin_url('admin.php?page=engageai-content')); ?>"><?php esc_html_e('See everything created so far', 'engage-ai'); ?></a>
            </div>
        </div>
        <?php
    }

    /* ---------------------------------------------------------- utilities */

    private function render_button(string $label, string $action, int $content_id, string $class = 'eas-btn'): void
    {
        ?>
        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
            <input type="hidden" name="action" value="<?php echo esc_attr($action); ?>">
            <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $content_id); ?>">
            <?php wp_nonce_field($action); ?>
            <button type="submit" class="<?php echo esc_attr($class); ?>"><?php echo esc_html($label); ?></button>
        </form>
        <?php
    }

    private function render_restart(string $message): void
    {
        ?>
        <div class="eas-panel">
            <div class="eas-empty">
                <p><?php echo esc_html($message); ?></p>
                <a class="eas-btn" href="<?php echo esc_url($this->url(['step' => 'goal'])); ?>"><?php esc_html_e('Start a new piece', 'engage-ai'); ?></a>
            </div>
        </div>
        <?php
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-studio">
            <div class="eas-masthead"><h1><?php esc_html_e('Content Studio', 'engage-ai'); ?></h1></div>
            <div class="eas-notice eas-notice--bad">
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </div>
        </div>
        <?php
    }

    private function render_notice(): void
    {
        if (isset($_GET['drafted'])) {
            $edit = get_edit_post_link((int) $_GET['drafted'], '');
            printf(
                '<div class="eas-notice eas-notice--ok">%s <a href="%s">%s</a></div>',
                esc_html__('Created a WordPress draft with the image attached.', 'engage-ai'),
                esc_url($edit ?: admin_url('edit.php?post_status=draft&post_type=post')),
                esc_html__('Review it →', 'engage-ai')
            );
        } elseif (isset($_GET['improved'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>',
                esc_html__('Rewritten and re-checked.', 'engage-ai'));
        } elseif (isset($_GET['saved'])) {
            printf('<div class="eas-notice eas-notice--ok">%s</div>',
                esc_html__('Saved and re-checked.', 'engage-ai'));
        } elseif (isset($_GET['error'])) {
            $error = $_GET['error'] === 'not_ready'
                ? __('Connect your account and select an organization on the Settings page first.', 'engage-ai')
                : rawurldecode((string) $_GET['error']);
            printf('<div class="eas-notice eas-notice--bad">%s</div>', esc_html($error));
        }
    }

    /** The studio works on one piece at a time; the content list is the source of truth for it. */
    private function find_item(int $org_id, int $content_id): ?array
    {
        $items = $this->client->get_content($org_id);
        if (is_wp_error($items)) {
            return null;
        }
        foreach ($items as $item) {
            if ((int) ($item['id'] ?? 0) === $content_id) {
                return $item;
            }
        }
        return null;
    }

    private function channel_label(string $channel): string
    {
        $labels = [
            'website' => __('Website', 'engage-ai'),
            'google_business' => __('Google Business', 'engage-ai'),
            'youtube' => __('YouTube', 'engage-ai'),
            'facebook' => __('Facebook', 'engage-ai'),
            'instagram' => __('Instagram', 'engage-ai'),
            'linkedin' => __('LinkedIn', 'engage-ai'),
            'twitter_x' => __('X / Twitter', 'engage-ai'),
        ];
        return $labels[$channel] ?? ucwords(str_replace('_', ' ', $channel));
    }

    private function url(array $args): string
    {
        return add_query_arg(array_merge(['page' => 'engageai-studio'], $args), admin_url('admin.php'));
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect($this->url($args));
        exit;
    }

    private function guard(string $nonce_action): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer($nonce_action)) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
    }
}
