<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Content page: a log of everything Engage AI has generated for this site,
 * plus a one-click "Suggest content" that asks the API to draft a few website
 * posts tailored to this site's type (church / ecommerce / business). Each
 * suggestion can be turned into a WordPress draft to review and publish.
 */
class EngageAI_Admin_Content
{
    private static ?EngageAI_Admin_Content $instance = null;
    private EngageAI_Api_Client $client;
    private EngageAI_Post_Publisher $publisher;

    public static function instance(): EngageAI_Admin_Content
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
        add_action('admin_post_engageai_suggest_content', [$this, 'handle_suggest']);
        add_action('admin_post_engageai_draft_content', [$this, 'handle_draft']);
        add_action('admin_post_engageai_generate_pack', [$this, 'handle_pack']);
        add_action('admin_post_engageai_generate_image', [$this, 'handle_generate_image']);
    }

    public function handle_pack(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_generate_pack')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $topic = sanitize_text_field($_POST['topic'] ?? '');
        $channels = array_map('sanitize_key', (array) ($_POST['channels'] ?? []));
        if (empty($channels)) {
            $this->redirect(['error' => rawurlencode(__('Pick at least one channel.', 'engage-ai'))]);
        }
        $result = $this->client->generate_pack((int) $org_id, $topic, $channels);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['suggested' => is_array($result) ? count($result) : 0]);
    }

    public function handle_generate_image(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_generate_image')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $res = $this->client->generate_content_image((int) $org_id, $content_id);
        if (is_wp_error($res)) {
            $this->redirect(['error' => rawurlencode($res->get_error_message())]);
        }
        $asset_id = (int) ($res['asset_id'] ?? 0);
        $bytes = $asset_id ? $this->client->get_asset_bytes((int) $org_id, $asset_id) : new WP_Error('engageai_no_asset', __('No image was returned.', 'engage-ai'));
        if (is_wp_error($bytes)) {
            $this->redirect(['error' => rawurlencode($bytes->get_error_message())]);
        }
        $attachment_id = $this->save_to_media_library($bytes['body'], $bytes['mime'], 'engage-ai-' . $content_id . '-' . $asset_id);
        if (is_wp_error($attachment_id)) {
            $this->redirect(['error' => rawurlencode($attachment_id->get_error_message())]);
        }
        $this->redirect(['image' => (int) $attachment_id]);
    }

    /**
     * Saves generated image bytes into the WordPress Media Library so the image
     * is usable (as a featured image, in a post, etc.) with a normal WP URL.
     * @return int|WP_Error attachment ID
     */
    private function save_to_media_library(string $bytes, string $mime, string $slug)
    {
        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';
        $ext = $mime === 'image/jpeg' ? 'jpg' : 'png';
        $upload = wp_upload_bits($slug . '.' . $ext, null, $bytes);
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
        return $attachment_id;
    }

    public function handle_suggest(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_suggest_content')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        if (!$org_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $count = max(1, min(6, (int) ($_POST['count'] ?? 3)));
        $channel = '';
        $type = '';
        $selection = sanitize_text_field($_POST['channel_type'] ?? '');
        if (strpos($selection, '|') !== false) {
            [$channel, $type] = array_map('sanitize_key', explode('|', $selection, 2));
        }
        $result = $this->client->suggest_content((int) $org_id, $count, $channel, $type);
        if (is_wp_error($result)) {
            $this->redirect(['error' => rawurlencode($result->get_error_message())]);
        }
        $this->redirect(['suggested' => is_array($result) ? count($result) : 0]);
    }

    public function handle_draft(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_draft_content')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $items = $this->client->get_content((int) $org_id);
        $item = null;
        if (!is_wp_error($items)) {
            foreach ($items as $candidate) {
                if ((int) ($candidate['id'] ?? 0) === $content_id) {
                    $item = $candidate;
                    break;
                }
            }
        }
        if (!$item) {
            $this->redirect(['error' => rawurlencode(__('Could not find that content item.', 'engage-ai'))]);
        }
        $post_id = $this->publisher->publish(
            $item['output_payload'] ?? [],
            (string) ($item['content_type'] ?? 'website_post'),
            (string) ($item['title'] ?? __('Engage AI post', 'engage-ai')),
            'draft'
        );
        if (is_wp_error($post_id)) {
            $this->redirect(['error' => rawurlencode($post_id->get_error_message())]);
        }
        $this->redirect(['drafted' => (int) $post_id]);
    }

    private function redirect(array $args): void
    {
        wp_safe_redirect(add_query_arg(array_merge(['page' => 'engageai-content'], $args), admin_url('admin.php')));
        exit;
    }

    public function render_page(): void
    {
        if (!current_user_can('manage_options')) {
            return;
        }
        if (!$this->client->is_connected() || !$this->client->get_organization_id()) {
            $this->render_not_ready();
            return;
        }
        $org_id = (int) $this->client->get_organization_id();
        $site_type = class_exists('EngageAI_Plugin') ? EngageAI_Plugin::detect_site_type() : 'business';
        $items = $this->client->get_content($org_id);
        $items = is_wp_error($items) ? [] : $items;
        $types = $this->client->get_content_types();
        $types = is_wp_error($types) ? [] : $types;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Content', 'engage-ai'); ?></h1>
            <?php $this->render_notice(); ?>

            <p class="description">
                <?php
                printf(
                    /* translators: %s: detected site type, e.g. "church" */
                    esc_html__('Content is tailored to your site type: %s, and to each channel\'s engagement-score levers. Start with a campaign (one topic across channels) or draft a single piece.', 'engage-ai'),
                    '<strong>' . esc_html($site_type) . '</strong>'
                );
                ?>
            </p>

            <h2><?php esc_html_e('Create a campaign', 'engage-ai'); ?></h2>
            <p class="description"><?php esc_html_e('One topic, drafted for every channel you pick - each with the copy and the media (image or video plan) that channel needs.', 'engage-ai'); ?></p>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin:12px 0;">
                <input type="hidden" name="action" value="engageai_generate_pack">
                <?php wp_nonce_field('engageai_generate_pack'); ?>
                <p>
                    <label for="engageai-topic"><strong><?php esc_html_e('Topic', 'engage-ai'); ?></strong></label><br>
                    <input type="text" id="engageai-topic" name="topic" class="large-text" placeholder="<?php esc_attr_e('e.g. our new done-for-you website service — or leave blank to let the agent choose', 'engage-ai'); ?>">
                </p>
                <fieldset style="margin-bottom:10px;">
                    <legend><strong><?php esc_html_e('Channels', 'engage-ai'); ?></strong></legend>
                    <?php
                    $default_on = ['website', 'instagram', 'facebook'];
                    foreach (['website', 'google_business', 'youtube', 'facebook', 'instagram', 'linkedin', 'twitter_x', 'news_mentions'] as $ch):
                        ?>
                        <label style="display:inline-block;margin:4px 16px 4px 0;">
                            <input type="checkbox" name="channels[]" value="<?php echo esc_attr($ch); ?>" <?php checked(in_array($ch, $default_on, true)); ?>>
                            <?php echo esc_html($this->channel_label($ch)); ?>
                        </label>
                    <?php endforeach; ?>
                </fieldset>
                <button type="submit" class="button button-primary"><?php esc_html_e('Generate campaign', 'engage-ai'); ?></button>
            </form>

            <hr>
            <h2><?php esc_html_e('Or draft a single piece', 'engage-ai'); ?></h2>
            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin:12px 0;">
                <input type="hidden" name="action" value="engageai_suggest_content">
                <?php wp_nonce_field('engageai_suggest_content'); ?>
                <label for="engageai-channel-type"><?php esc_html_e('What to create', 'engage-ai'); ?></label>
                <select id="engageai-channel-type" name="channel_type" style="min-width:340px;">
                    <option value=""><?php esc_html_e('Website posts (tailored to site type)', 'engage-ai'); ?></option>
                    <?php foreach ($types as $channel => $channel_types): ?>
                        <optgroup label="<?php echo esc_attr($this->channel_label($channel)); ?>">
                            <?php foreach ($channel_types as $ct): ?>
                                <option value="<?php echo esc_attr($channel . '|' . ($ct['key'] ?? '')); ?>">
                                    <?php echo esc_html(($ct['label'] ?? '') . ' — raises ' . ($ct['raises'] ?? '')); ?>
                                </option>
                            <?php endforeach; ?>
                        </optgroup>
                    <?php endforeach; ?>
                </select>
                <label for="engageai-count" style="margin-left:10px;"><?php esc_html_e('How many', 'engage-ai'); ?></label>
                <select id="engageai-count" name="count">
                    <?php foreach ([2, 3, 4, 5] as $n): ?>
                        <option value="<?php echo esc_attr((string) $n); ?>" <?php selected($n, 3); ?>><?php echo esc_html((string) $n); ?></option>
                    <?php endforeach; ?>
                </select>
                <button type="submit" class="button button-primary"><?php esc_html_e('Generate content', 'engage-ai'); ?></button>
            </form>

            <h2><?php esc_html_e('Generated content', 'engage-ai'); ?></h2>
            <?php if (empty($items)): ?>
                <p><?php esc_html_e('Nothing yet. Use "Generate content" above to draft your first posts.', 'engage-ai'); ?></p>
            <?php else: ?>
                <table class="widefat striped">
                    <thead>
                        <tr>
                            <th><?php esc_html_e('Title', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Channel / type', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Draft', 'engage-ai'); ?></th>
                            <th><?php esc_html_e('Action', 'engage-ai'); ?></th>
                        </tr>
                    </thead>
                    <tbody>
                        <?php foreach ($items as $item): ?>
                            <?php
                            $out = $item['output_payload'] ?? [];
                            $id = (int) ($item['id'] ?? 0);
                            $channel = $out['channel'] ?? ($item['content_type'] ?? '');
                            $type_label = $out['content_type_label'] ?? str_replace('_', ' ', (string) ($item['content_type'] ?? ''));
                            $angle = $out['angle'] ?? ($item['input_payload']['angle'] ?? '');
                            $body = $out['body'] ?? ($out['website_post']['body_html'] ?? '');
                            $hashtags = $out['hashtags'] ?? [];
                            $media = $out['media'] ?? '';
                            $image_prompt = $out['image_prompt'] ?? '';
                            $image_alt = $out['image_alt'] ?? '';
                            $video_plan = $out['video_plan'] ?? null;
                            $has_image = !empty($out['image_asset_id']);
                            $is_website_post = !empty($out['website_post']['body_html']);
                            ?>
                            <tr>
                                <td>
                                    <strong><?php echo esc_html($item['title'] ?? ''); ?></strong>
                                    <?php if ($angle): ?><div class="description"><?php echo esc_html($angle); ?></div><?php endif; ?>
                                </td>
                                <td>
                                    <?php echo esc_html($this->channel_label($channel)); ?><br>
                                    <span class="description"><?php echo esc_html($type_label); ?><?php if ($media && $media !== 'text'): ?> · <?php echo esc_html($media); ?><?php endif; ?></span>
                                </td>
                                <td>
                                    <?php if ($body): ?>
                                        <details>
                                            <summary style="cursor:pointer;"><?php esc_html_e('View / copy', 'engage-ai'); ?></summary>
                                            <textarea readonly rows="8" class="large-text code" style="margin-top:6px;"><?php echo esc_textarea($body); ?></textarea>
                                            <?php if (!empty($hashtags)): ?>
                                                <p class="description"><?php echo esc_html('#' . implode(' #', array_map('sanitize_text_field', $hashtags))); ?></p>
                                            <?php endif; ?>
                                            <?php if ($image_prompt): ?>
                                                <p class="description"><strong><?php esc_html_e('Image prompt:', 'engage-ai'); ?></strong> <?php echo esc_html($image_prompt); ?><?php if ($image_alt): ?><br><em><?php echo esc_html($image_alt); ?></em><?php endif; ?></p>
                                            <?php endif; ?>
                                            <?php if (is_array($video_plan) && !empty($video_plan)): ?>
                                                <p class="description"><strong><?php esc_html_e('Video plan:', 'engage-ai'); ?></strong></p>
                                                <?php if (!empty($video_plan['voiceover'])): ?><p class="description"><?php echo esc_html($video_plan['voiceover']); ?></p><?php endif; ?>
                                                <?php if (!empty($video_plan['scenes']) && is_array($video_plan['scenes'])): ?>
                                                    <ol class="description" style="margin-left:18px;">
                                                        <?php foreach ($video_plan['scenes'] as $scene): ?>
                                                            <li><?php echo esc_html($scene['caption'] ?? ''); ?><?php if (!empty($scene['image_prompt'])): ?> <em>— <?php echo esc_html($scene['image_prompt']); ?></em><?php endif; ?></li>
                                                        <?php endforeach; ?>
                                                    </ol>
                                                <?php endif; ?>
                                                <?php if (!empty($video_plan['thumbnail_prompt'])): ?><p class="description"><strong><?php esc_html_e('Thumbnail:', 'engage-ai'); ?></strong> <?php echo esc_html($video_plan['thumbnail_prompt']); ?></p><?php endif; ?>
                                            <?php endif; ?>
                                        </details>
                                    <?php else: ?>
                                        <span class="description">&mdash;</span>
                                    <?php endif; ?>
                                </td>
                                <td>
                                    <?php if ($is_website_post): ?>
                                        <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>" style="margin-bottom:6px;">
                                            <input type="hidden" name="action" value="engageai_draft_content">
                                            <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $id); ?>">
                                            <?php wp_nonce_field('engageai_draft_content'); ?>
                                            <button type="submit" class="button"><?php esc_html_e('Create WordPress draft', 'engage-ai'); ?></button>
                                        </form>
                                    <?php endif; ?>
                                    <?php if ($media === 'image' && $image_prompt): ?>
                                        <?php if ($has_image): ?>
                                            <span class="description"><?php esc_html_e('Image generated ✓ (Media Library)', 'engage-ai'); ?></span>
                                        <?php else: ?>
                                            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                                <input type="hidden" name="action" value="engageai_generate_image">
                                                <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $id); ?>">
                                                <?php wp_nonce_field('engageai_generate_image'); ?>
                                                <button type="submit" class="button"><?php esc_html_e('Generate image', 'engage-ai'); ?></button>
                                            </form>
                                        <?php endif; ?>
                                    <?php elseif ($media === 'video'): ?>
                                        <span class="description"><?php esc_html_e('Use the plan to record/assemble', 'engage-ai'); ?></span>
                                    <?php elseif (!$is_website_post): ?>
                                        <span class="description"><?php esc_html_e('Copy & post', 'engage-ai'); ?></span>
                                    <?php endif; ?>
                                </td>
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
            'google_business' => __('Google Business', 'engage-ai'),
            'youtube' => __('YouTube', 'engage-ai'),
            'facebook' => __('Facebook', 'engage-ai'),
            'instagram' => __('Instagram', 'engage-ai'),
            'linkedin' => __('LinkedIn', 'engage-ai'),
            'twitter_x' => __('X / Twitter', 'engage-ai'),
            'news_mentions' => __('News mentions', 'engage-ai'),
            'website_post' => __('Website', 'engage-ai'),
        ];
        return $labels[$channel] ?? ucwords(str_replace('_', ' ', $channel));
    }

    private function render_notice(): void
    {
        if (isset($_GET['suggested'])) {
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s</p></div>',
                esc_html(sprintf(
                    /* translators: %d: number of drafts generated */
                    _n('Generated %d new draft below.', 'Generated %d new drafts below.', (int) $_GET['suggested'], 'engage-ai'),
                    (int) $_GET['suggested']
                ))
            );
        } elseif (isset($_GET['image'])) {
            $att = (int) $_GET['image'];
            $url = wp_get_attachment_url($att);
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s <a href="%s" target="_blank" rel="noopener">%s</a></p></div>',
                esc_html__('Image generated and saved to your Media Library.', 'engage-ai'),
                esc_url($url ?: admin_url('upload.php')),
                esc_html__('View image →', 'engage-ai')
            );
        } elseif (isset($_GET['drafted'])) {
            $edit = get_edit_post_link((int) $_GET['drafted'], '');
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s <a href="%s">%s</a></p></div>',
                esc_html__('Created a WordPress draft.', 'engage-ai'),
                esc_url($edit ?: admin_url('edit.php?post_status=draft&post_type=post')),
                esc_html__('Review it →', 'engage-ai')
            );
        } elseif (isset($_GET['error'])) {
            $err = $_GET['error'] === 'not_ready'
                ? __('Connect your account and select an organization on the Settings page first.', 'engage-ai')
                : rawurldecode((string) $_GET['error']);
            printf('<div class="notice notice-error is-dismissible"><p>%s</p></div>', esc_html($err));
        }
    }

    private function render_not_ready(): void
    {
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Content', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p>
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </p></div>
        </div>
        <?php
    }
}
