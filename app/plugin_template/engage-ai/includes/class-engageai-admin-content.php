<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * The Content Library: everything Engage AI has ever written for this site, in
 * one list. It is a library, not a generator - content is created in the
 * Content Studio (one piece, step by step) or in Campaigns (a whole run at
 * once), and both land here.
 *
 * This page used to carry its own pair of generate forms as well, on an older
 * pipeline (/content/pack and /content/suggest). They produced items that could
 * not re-enter the Studio - no quality check, no revise pass, no channel
 * publish - so the same job had three different answers depending on which page
 * you happened to open. The forms are gone; each row now opens in the Studio
 * instead, which is the one pipeline everything else already uses.
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
        add_action('admin_post_engageai_draft_content', [$this, 'handle_draft']);
        add_action('admin_post_engageai_generate_image', [$this, 'handle_generate_image']);
        add_action('admin_post_engageai_generate_video', [$this, 'handle_generate_video']);
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
        $bytes = $asset_id ? $this->client->get_asset_bytes($asset_id) : new WP_Error('engageai_no_asset', __('No image was returned.', 'engage-ai'));
        if (is_wp_error($bytes)) {
            $this->redirect(['error' => rawurlencode($bytes->get_error_message())]);
        }
        $attachment_id = $this->save_to_media_library($bytes['body'], $bytes['mime'], 'engage-ai-' . $content_id . '-' . $asset_id);
        if (is_wp_error($attachment_id)) {
            $this->redirect(['error' => rawurlencode($attachment_id->get_error_message())]);
        }
        $this->redirect(['image' => (int) $attachment_id]);
    }

    public function handle_generate_video(): void
    {
        if (!current_user_can('manage_options') || !check_admin_referer('engageai_generate_video')) {
            wp_die(esc_html__('You are not allowed to do this.', 'engage-ai'));
        }
        $org_id = $this->client->get_organization_id();
        $content_id = (int) ($_POST['content_id'] ?? 0);
        if (!$org_id || !$content_id) {
            $this->redirect(['error' => 'not_ready']);
        }
        $res = $this->client->generate_content_video((int) $org_id, $content_id);
        if (is_wp_error($res)) {
            $this->redirect(['error' => rawurlencode($res->get_error_message())]);
        }
        $asset_id = (int) ($res['asset_id'] ?? 0);
        $bytes = $asset_id ? $this->client->get_asset_bytes($asset_id) : new WP_Error('engageai_no_asset', __('No video was returned.', 'engage-ai'));
        if (is_wp_error($bytes)) {
            $this->redirect(['error' => rawurlencode($bytes->get_error_message())]);
        }
        $attachment_id = $this->save_to_media_library($bytes['body'], $bytes['mime'] ?: 'video/mp4', 'engage-ai-video-' . $content_id . '-' . $asset_id);
        if (is_wp_error($attachment_id)) {
            $this->redirect(['error' => rawurlencode($attachment_id->get_error_message())]);
        }
        $this->redirect(['video' => (int) $attachment_id]);
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
        $ext_by_mime = ['image/jpeg' => 'jpg', 'image/png' => 'png', 'image/webp' => 'webp', 'video/mp4' => 'mp4'];
        $ext = $ext_by_mime[$mime] ?? 'png';
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

        // The catalog of what each channel supports is reference material, not
        // something anyone reads daily, so it is a tab here rather than another
        // menu item - the library is where you already are when you wonder
        // "what else could this channel take?".
        if (sanitize_key($_GET['view'] ?? '') === 'types') {
            $this->render_types_tab();
            return;
        }

        $items = $this->client->get_content($org_id);
        $items = is_wp_error($items) ? [] : $items;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Content Library', 'engage-ai'); ?></h1>
            <?php $this->render_tabs('library'); ?>
            <?php $this->render_notice(); ?>

            <p class="description">
                <?php
                printf(
                    /* translators: %s: detected site type, e.g. "church" */
                    esc_html__('Everything Engage AI has written for this site, tailored to your site type: %s. Pieces made in the Content Studio open back there to be checked, edited, given their image or video, and published.', 'engage-ai'),
                    '<strong>' . esc_html($site_type) . '</strong>'
                );
                ?>
            </p>

            <p style="margin:16px 0;">
                <a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=engageai-studio')); ?>"><?php esc_html_e('Create a piece →', 'engage-ai'); ?></a>
                <a class="button" href="<?php echo esc_url(admin_url('admin.php?page=engageai-campaigns')); ?>" style="margin-left:6px;"><?php esc_html_e('Plan a campaign →', 'engage-ai'); ?></a>
            </p>
            <h2><?php esc_html_e('Everything created so far', 'engage-ai'); ?></h2>
            <?php if (empty($items)): ?>
                <p><?php esc_html_e('Nothing yet. Create your first piece in the Content Studio, or plan a whole run in Campaigns.', 'engage-ai'); ?></p>
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
                                    <?php
                                    // The Studio is where a piece gets checked, revised, given its
                                    // image or video and published to a channel - but only pieces
                                    // the Studio itself drafted can go back into it. It keeps its
                                    // state under output_payload.studio, and every /studio/* route
                                    // rejects a piece without it (400, "This piece wasn't created
                                    // in the Content Studio"). Older library items - drafted by the
                                    // retired /content/pack and /content/suggest pipeline - have no
                                    // such state, so offering them this link would only ever lead
                                    // to a broken editor and that error. They keep the actions
                                    // below, which are what they have always supported.
                                    $is_studio_piece = !empty($out['studio']) && is_array($out['studio']);
                                    if ($is_studio_piece):
                                        $studio_url = add_query_arg(
                                            ['page' => 'engageai-studio', 'step' => 'draft', 'content_id' => $id],
                                            admin_url('admin.php')
                                        );
                                        ?>
                                        <p style="margin:0 0 6px;">
                                            <a class="button button-primary" href="<?php echo esc_url($studio_url); ?>"><?php esc_html_e('Open in Studio', 'engage-ai'); ?></a>
                                        </p>
                                    <?php endif; ?>
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
                                    <?php elseif ($media === 'video' && is_array($video_plan) && !empty($video_plan['scenes'])): ?>
                                        <?php if (!empty($out['video_asset_id'])): ?>
                                            <span class="description"><?php esc_html_e('Video assembled ✓ (Media Library)', 'engage-ai'); ?></span>
                                        <?php else: ?>
                                            <form method="post" action="<?php echo esc_url(admin_url('admin-post.php')); ?>">
                                                <input type="hidden" name="action" value="engageai_generate_video">
                                                <input type="hidden" name="content_id" value="<?php echo esc_attr((string) $id); ?>">
                                                <?php wp_nonce_field('engageai_generate_video'); ?>
                                                <button type="submit" class="button"><?php esc_html_e('Generate video', 'engage-ai'); ?></button>
                                            </form>
                                        <?php endif; ?>
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

    /** @param string $current 'library' or 'types' */
    private function render_tabs(string $current): void
    {
        $tabs = [
            'library' => [admin_url('admin.php?page=engageai-content'), __('Everything created', 'engage-ai')],
            'types' => [admin_url('admin.php?page=engageai-content&view=types'), __('What each channel takes', 'engage-ai')],
        ];
        ?>
        <nav class="nav-tab-wrapper" style="margin-bottom:1.25rem;">
            <?php foreach ($tabs as $key => [$url, $label]): ?>
                <a class="nav-tab<?php echo $key === $current ? ' nav-tab-active' : ''; ?>"
                   href="<?php echo esc_url($url); ?>"><?php echo esc_html($label); ?></a>
            <?php endforeach; ?>
        </nav>
        <?php
    }

    /**
     * The content-type reference: per channel, what kinds of piece it supports
     * and which engagement lever each one raises.
     *
     * Read-only on purpose. This is the answer to "what could I make for
     * LinkedIn, and what would it do for us?" - the making itself belongs to
     * the Content Studio, and putting a generate button here is exactly the
     * duplication that was removed from this page in 0.26.0.
     */
    private function render_types_tab(): void
    {
        $types = $this->client->get_content_types();
        $error = is_wp_error($types) ? $types->get_error_message() : '';
        $types = is_wp_error($types) ? [] : (array) $types;
        ?>
        <div class="wrap engageai-wrap">
            <h1><?php esc_html_e('Content Library', 'engage-ai'); ?></h1>
            <?php $this->render_tabs('types'); ?>
            <?php if ($error !== ''): ?>
                <div class="notice notice-error"><p><?php echo esc_html($error); ?></p></div>
            <?php endif; ?>

            <p class="description" style="max-width:46em;">
                <?php esc_html_e('What each channel can carry, and what each kind of piece is actually for. Use it to decide what to make next — the making happens in the Content Studio.', 'engage-ai'); ?>
            </p>

            <?php if (empty($types)): ?>
                <p><?php esc_html_e('The catalog could not be loaded. It comes from the Engage AI API, so this needs a working connection.', 'engage-ai'); ?></p>
            <?php else: ?>
                <?php foreach ($types as $channel => $channel_types): ?>
                    <h2 style="margin-top:1.75rem;"><?php echo esc_html($this->channel_label((string) $channel)); ?></h2>
                    <table class="widefat striped" style="max-width:52em;">
                        <thead>
                            <tr>
                                <th style="width:32%;"><?php esc_html_e('Kind of piece', 'engage-ai'); ?></th>
                                <th><?php esc_html_e('What it raises', 'engage-ai'); ?></th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php foreach ((array) $channel_types as $type): ?>
                                <?php if (!is_array($type)) { continue; } ?>
                                <tr>
                                    <td><strong><?php echo esc_html((string) ($type['label'] ?? $type['key'] ?? '')); ?></strong></td>
                                    <td><span class="description"><?php echo esc_html((string) ($type['raises'] ?? '—')); ?></span></td>
                                </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                <?php endforeach; ?>
                <p style="margin-top:1.5rem;">
                    <a class="button button-primary" href="<?php echo esc_url(admin_url('admin.php?page=engageai-studio')); ?>"><?php esc_html_e('Make one in the Studio →', 'engage-ai'); ?></a>
                </p>
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
        if (isset($_GET['image']) || isset($_GET['video'])) {
            $is_video = isset($_GET['video']);
            $att = (int) ($_GET[$is_video ? 'video' : 'image']);
            $url = wp_get_attachment_url($att);
            printf(
                '<div class="notice notice-success is-dismissible"><p>%s <a href="%s" target="_blank" rel="noopener">%s</a></p></div>',
                esc_html($is_video ? __('Video assembled and saved to your Media Library.', 'engage-ai') : __('Image generated and saved to your Media Library.', 'engage-ai')),
                esc_url($url ?: admin_url('upload.php')),
                esc_html($is_video ? __('View video →', 'engage-ai') : __('View image →', 'engage-ai'))
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
            <h1><?php esc_html_e('Content Library', 'engage-ai'); ?></h1>
            <div class="notice notice-warning"><p>
                <?php esc_html_e('Connect your Engage AI account and select an organization on the Settings page first.', 'engage-ai'); ?>
            </p></div>
        </div>
        <?php
    }
}
