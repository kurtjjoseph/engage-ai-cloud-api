<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Converts an Engage AI campaign response (matching the API's fixed output
 * schema — website_post / social_media / email / whatsapp / slides /
 * follow_up_actions) into a WordPress post, and stashes the non-website
 * channels as post meta for manual copy into social/email/WhatsApp tools.
 */
class EngageAI_Post_Publisher
{
    public const META_SOCIAL_MEDIA = '_engageai_social_media';
    public const META_EMAIL = '_engageai_email';
    public const META_WHATSAPP = '_engageai_whatsapp';
    public const META_SLIDES = '_engageai_slides';
    public const META_FOLLOW_UP = '_engageai_follow_up_actions';
    public const META_CONTENT_TYPE = '_engageai_content_type';

    /**
     * Wraps a plain HTML body in WordPress block markup so it opens as proper
     * Gutenberg blocks (paragraphs, headings, lists) instead of one "Classic"
     * freeform block - which some themes render with a broken, collapsed
     * layout in the editor. Sanitize BEFORE calling this: wp_kses strips HTML
     * comments, which would remove the block delimiters. Idempotent (leaves
     * content that already has block markup alone).
     */
    public static function html_to_blocks(string $html): string
    {
        $html = trim($html);
        if ($html === '' || strpos($html, '<!-- wp:') !== false) {
            return $html;
        }
        $blocks = preg_replace_callback(
            '/<(h[1-6]|p|ul|ol|blockquote)\b([^>]*)>(.*?)<\/\1>/is',
            static function (array $m): string {
                $tag = strtolower($m[1]);
                if ($tag === 'ul' || $tag === 'ol') {
                    $items = preg_replace_callback(
                        '/<li\b[^>]*>(.*?)<\/li>/is',
                        static fn(array $li): string => "<!-- wp:list-item --><li>" . trim($li[1]) . "</li><!-- /wp:list-item -->",
                        $m[3]
                    );
                    $ordered = $tag === 'ol' ? ' {"ordered":true}' : '';
                    return "<!-- wp:list{$ordered} --><{$tag}>{$items}</{$tag}><!-- /wp:list -->\n\n";
                }
                if ($tag[0] === 'h') {
                    $level = (int) substr($tag, 1);
                    return "<!-- wp:heading {\"level\":{$level}} --><{$tag}>" . trim($m[3]) . "</{$tag}><!-- /wp:heading -->\n\n";
                }
                return "<!-- wp:paragraph --><p>" . trim($m[3]) . "</p><!-- /wp:paragraph -->\n\n";
            },
            $html
        );
        return trim((string) $blocks);
    }

    /**
     * @return int|WP_Error post ID on success
     */
    public function publish(array $output_payload, string $content_type, string $fallback_title, string $post_status)
    {
        $website_post = $output_payload['website_post'] ?? [];
        $title = !empty($website_post['title']) ? $website_post['title'] : $fallback_title;
        $body_html = $website_post['body_html'] ?? '';

        $post_id = wp_insert_post([
            'post_title' => sanitize_text_field($title),
            'post_content' => self::html_to_blocks(wp_kses_post($body_html)),
            'post_status' => in_array($post_status, ['draft', 'publish', 'pending'], true) ? $post_status : 'draft',
            'post_type' => 'post',
        ], true);

        if (is_wp_error($post_id)) {
            return $post_id;
        }

        update_post_meta($post_id, self::META_CONTENT_TYPE, sanitize_key($content_type));
        update_post_meta($post_id, self::META_SOCIAL_MEDIA, $output_payload['social_media'] ?? []);
        update_post_meta($post_id, self::META_EMAIL, $output_payload['email'] ?? []);
        update_post_meta($post_id, self::META_WHATSAPP, $output_payload['whatsapp'] ?? []);
        update_post_meta($post_id, self::META_SLIDES, $output_payload['slides'] ?? []);
        update_post_meta($post_id, self::META_FOLLOW_UP, $output_payload['follow_up_actions'] ?? []);

        return $post_id;
    }

    /**
     * Publishes a single piece of already-written copy (an engagement_growth
     * "content_idea" ticket's payload, not the full multi-channel campaign
     * schema publish() above expects) as a plain WordPress post. Used by the
     * autonomous cron sweep (class-engageai-cron.php) for "website" channel
     * tickets - always forced to draft/pending regardless of the site's
     * configured default status, since autonomy here is only safe because
     * this never goes live untouched.
     * @return int|WP_Error post ID on success
     */
    public function publish_single_channel_draft(string $title, string $content, string $post_status = 'draft')
    {
        // wpautop turns the plain/markdown-ish ticket content into paragraphs,
        // then html_to_blocks makes those proper Gutenberg blocks.
        return wp_insert_post([
            'post_title' => sanitize_text_field($title),
            'post_content' => self::html_to_blocks(wp_kses_post(wpautop($content))),
            'post_status' => in_array($post_status, ['draft', 'pending'], true) ? $post_status : 'draft',
            'post_type' => 'post',
        ], true);
    }
}
