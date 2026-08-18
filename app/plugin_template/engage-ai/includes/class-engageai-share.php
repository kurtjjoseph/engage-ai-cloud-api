<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * "Share" on a finished piece: hand it to the sharing the platforms already have.
 *
 * The Content Library's advice for a social piece was the words "Copy & post".
 * That is the whole manual workflow, unassisted: select the caption, copy it,
 * find the image in the Media Library, download it, open the app, paste, attach.
 * Every one of those steps is somewhere an operator gives up.
 *
 * This is the other route to publishing, and it is worth being precise about why
 * it exists alongside the automatic one (services/automation.py). Autonomous
 * posting needs an authorized API connection per channel - an OAuth app, in
 * several cases an app review. Until that exists for a channel, nothing posts to
 * it. Sharing needs NOTHING: no credentials, no app, no review. It works today,
 * on every channel, at the cost of one human tap.
 *
 * WHAT ACTUALLY WORKS, WHICH IS NOT UNIFORM
 *
 * The honest matrix, and the reason this class is more than a row of icons:
 *
 *   Web Share API   the real thing - the OS share sheet, every installed app,
 *                   and the ONLY route that can carry the IMAGE. Needs HTTPS
 *                   and a user gesture. Present on mobile Safari/Chrome and on
 *                   desktop Safari/Edge; absent on desktop Firefox and most
 *                   desktop Linux Chrome.
 *   X, WhatsApp,    intent URLs that genuinely prefill the caption.
 *   Telegram,
 *   Reddit
 *   Facebook,       accept a URL and NOTHING else. Facebook dropped `quote`
 *   LinkedIn        without the JS SDK; LinkedIn dropped title/summary. A button
 *                   that pretends otherwise silently loses the caption, so these
 *                   copy it to the clipboard first and say they have.
 *   Instagram       has no web share URL at all. None. On mobile the OS sheet
 *                   reaches it; on desktop the only truthful offer is "copy the
 *                   caption, download the image".
 *
 * So every path ends with the caption on the clipboard whether or not the target
 * could take it, and the UI says which happened. The failure this is written
 * against is a share button that looks like it worked and posted an empty frame.
 */
final class EngageAI_Share
{
    private static bool $printed_assets = false;

    /**
     * The Media Library URL of a piece's generated image, if it has one.
     *
     * Looked up by the slug handle_generate_image saved it under
     * ("engage-ai-{content_id}-{asset_id}"), because that is the only link
     * between the API's asset id and the WordPress attachment - nothing stores
     * the attachment id back on the piece.
     */
    public static function image_url(int $content_id, $asset_id): string
    {
        $asset_id = (int) $asset_id;
        if (!$content_id || !$asset_id) {
            return '';
        }
        $slug = sanitize_file_name('engage-ai-' . $content_id . '-' . $asset_id);
        $found = get_posts([
            'post_type' => 'attachment',
            'post_status' => 'inherit',
            'name' => $slug,
            'posts_per_page' => 1,
            'fields' => 'ids',
            'no_found_rows' => true,
        ]);
        if (!$found) {
            return '';
        }
        return (string) wp_get_attachment_url((int) $found[0]);
    }

    /**
     * The share control for one piece.
     *
     * @param array $piece {
     *   id: int, title: string, body: string, hashtags: string[],
     *   channel: string, url: string, image_url: string
     * }
     */
    public static function button(array $piece): void
    {
        self::assets();

        $caption = trim((string) ($piece['body'] ?? ''));
        $tags = array_filter(array_map('sanitize_text_field', (array) ($piece['hashtags'] ?? [])));
        if ($tags) {
            $caption .= "\n\n#" . implode(' #', $tags);
        }

        $payload = [
            'title' => (string) ($piece['title'] ?? ''),
            'text' => $caption,
            'url' => (string) ($piece['url'] ?? ''),
            'image' => (string) ($piece['image_url'] ?? ''),
            'channel' => (string) ($piece['channel'] ?? ''),
            // Facebook and LinkedIn accept nothing but a URL, so a social piece
            // - which has no URL of its own - would show neither. Falling back
            // to the site's own address keeps both reachable: the operator gets
            // the share dialog pointed at their site with the caption already on
            // the clipboard, which is exactly the manual move they were making
            // anyway. Labelled as such rather than passed off as a link to the
            // piece, which does not exist.
            'siteUrl' => home_url('/'),
        ];
        $id = 'engageai-share-' . (int) ($piece['id'] ?? 0);
        ?>
        <div class="engageai-share" id="<?php echo esc_attr($id); ?>"
             data-piece="<?php echo esc_attr(wp_json_encode($payload)); ?>">
            <button type="button" class="button engageai-share-open">
                <span class="dashicons dashicons-share" style="vertical-align:text-bottom;"></span>
                <?php esc_html_e('Share', 'engage-ai'); ?>
            </button>
            <div class="engageai-share-panel" hidden></div>
        </div>
        <?php
    }

    /** Printed once per page, however many pieces are listed. */
    private static function assets(): void
    {
        if (self::$printed_assets) {
            return;
        }
        self::$printed_assets = true;

        $strings = [
            'native' => __('Share…', 'engage-ai'),
            'nativeHint' => __('Opens your device\'s share sheet — including apps with no web sharing at all, like Instagram.', 'engage-ai'),
            'copy' => __('Copy caption', 'engage-ai'),
            'copied' => __('Caption copied', 'engage-ai'),
            'download' => __('Download image', 'engage-ai'),
            'pasteNote' => __('link only — caption copied, paste it in', 'engage-ai'),
            'noIntent' => __('has no web sharing. Copy the caption and download the image, or use Share… on a phone.', 'engage-ai'),
            'siteNote' => __('links to your site — caption copied, paste it in', 'engage-ai'),
            'siteLinkNote' => __('links to your site', 'engage-ai'),
            'close' => __('Close', 'engage-ai'),
        ];
        ?>
        <style>
        .engageai-share { position: relative; display: inline-block; }
        .engageai-share-panel {
            position: absolute; z-index: 100; right: 0; margin-top: 4px; min-width: 19em;
            background: #fff; border: 1px solid #c3c4c7; box-shadow: 0 2px 8px rgba(0,0,0,.14);
            padding: .6rem .7rem; text-align: left;
        }
        .engageai-share-panel a, .engageai-share-panel button.link {
            display: block; padding: .25rem 0; text-decoration: none; background: none;
            border: 0; cursor: pointer; font: inherit; color: #2271b1; width: 100%; text-align: left;
        }
        .engageai-share-panel .note { color: #646970; font-size: .85em; }
        .engageai-share-panel hr { margin: .45rem 0; border: 0; border-top: 1px solid #e0e0e0; }
        </style>
        <script>
        (function () {
            var S = <?php echo wp_json_encode($strings); ?>;

            // Only targets whose behaviour is actually known. `prefill` is the
            // whole point: false means the platform will silently drop the
            // caption, so the UI must say so rather than imply a clean handoff.
            var TARGETS = {
                twitter_x: { label: 'X', prefill: true, url: function (p) {
                    return 'https://twitter.com/intent/tweet?text=' + encodeURIComponent(p.text)
                        + (p.url ? '&url=' + encodeURIComponent(p.url) : ''); } },
                whatsapp: { label: 'WhatsApp', prefill: true, url: function (p) {
                    return 'https://wa.me/?text=' + encodeURIComponent(p.text + (p.url ? '\n' + p.url : '')); } },
                telegram: { label: 'Telegram', prefill: true, needsUrl: true, url: function (p) {
                    return 'https://t.me/share/url?url=' + encodeURIComponent(p.url || '')
                        + '&text=' + encodeURIComponent(p.text); } },
                reddit: { label: 'Reddit', prefill: true, url: function (p) {
                    return 'https://www.reddit.com/submit?title=' + encodeURIComponent(p.title || '')
                        + (p.url ? '&url=' + encodeURIComponent(p.url) : ''); } },
                facebook: { label: 'Facebook', prefill: false, needsUrl: true, url: function (p) {
                    return 'https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(p.url); } },
                linkedin: { label: 'LinkedIn', prefill: false, needsUrl: true, url: function (p) {
                    return 'https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(p.url); } },
                pinterest: { label: 'Pinterest', prefill: true, needsUrl: true, url: function (p) {
                    return 'https://pinterest.com/pin/create/button/?url=' + encodeURIComponent(p.url)
                        + (p.image ? '&media=' + encodeURIComponent(p.image) : '')
                        + '&description=' + encodeURIComponent(p.text); } }
            };
            // Named so the panel can say "Instagram has no web sharing" rather
            // than just not mentioning Instagram, which reads as an oversight.
            var NO_WEB_SHARE = { instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube',
                                 google_business: 'Google Business' };

            // Prefetched so navigator.share() can be called synchronously inside
            // the click. Awaiting a fetch first loses the user-gesture and iOS
            // Safari rejects the share outright.
            var files = {};
            function prefetch(node, p) {
                if (!p.image || files[p.image] !== undefined) { return; }
                files[p.image] = null;
                fetch(p.image).then(function (r) { return r.ok ? r.blob() : null; }).then(function (b) {
                    if (!b) { return; }
                    var name = (p.image.split('/').pop() || 'image').split('?')[0];
                    files[p.image] = new File([b], name, { type: b.type });
                }).catch(function () { /* share falls back to text-only */ });
            }

            function copy(text) {
                if (navigator.clipboard && window.isSecureContext) { return navigator.clipboard.writeText(text); }
                var ta = document.createElement('textarea');
                ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
                document.body.appendChild(ta); ta.select();
                try { document.execCommand('copy'); } finally { document.body.removeChild(ta); }
                return Promise.resolve();
            }

            function line(panel, tag, text, cls) {
                var el = document.createElement(tag);
                el.textContent = text;
                if (cls) { el.className = cls; }
                panel.appendChild(el);
                return el;
            }

            function build(panel, p) {
                panel.innerHTML = '';
                var canFiles = p.image && navigator.canShare && files[p.image]
                    && navigator.canShare({ files: [files[p.image]] });

                if (navigator.share) {
                    var b = line(panel, 'button', S.native, 'link');
                    b.type = 'button';
                    b.addEventListener('click', function () {
                        var data = { title: p.title, text: p.text };
                        if (p.url) { data.url = p.url; }
                        if (canFiles) { data.files = [files[p.image]]; }
                        // Caption on the clipboard regardless: several targets
                        // reachable through the sheet drop shared text.
                        copy(p.text);
                        navigator.share(data).catch(function () { /* dismissed */ });
                    });
                    line(panel, 'div', S.nativeHint, 'note');
                    panel.appendChild(document.createElement('hr'));
                }

                var order = Object.keys(TARGETS);
                if (TARGETS[p.channel]) { order = [p.channel].concat(order.filter(function (k) { return k !== p.channel; })); }
                order.forEach(function (key) {
                    var t = TARGETS[key];
                    // A target that can only take a URL still gets one - the
                    // site's - rather than vanishing from the panel.
                    var usingSite = t.needsUrl && !p.url && p.siteUrl;
                    if (t.needsUrl && !p.url && !p.siteUrl) { return; }
                    var view = usingSite ? Object.assign({}, p, { url: p.siteUrl }) : p;
                    var a = document.createElement('a');
                    a.href = t.url(view); a.target = '_blank'; a.rel = 'noopener noreferrer';
                    a.textContent = t.label;
                    // Three genuinely different situations, three different
                    // notes. Saying "caption copied, paste it in" on a target
                    // that already prefilled the caption would just be wrong.
                    var note = null;
                    if (!t.prefill) { note = usingSite ? S.siteNote : S.pasteNote; }
                    else if (usingSite) { note = S.siteLinkNote; }
                    if (note) {
                        var n = document.createElement('span');
                        n.className = 'note'; n.textContent = ' — ' + note;
                        a.appendChild(n);
                    }
                    // Clipboard first, so a paste-required target is ready the
                    // moment its dialog opens.
                    a.addEventListener('click', function () { copy(p.text); });
                    panel.appendChild(a);
                });

                if (NO_WEB_SHARE[p.channel]) {
                    line(panel, 'div', NO_WEB_SHARE[p.channel] + ' ' + S.noIntent, 'note');
                }

                panel.appendChild(document.createElement('hr'));
                var c = line(panel, 'button', S.copy, 'link');
                c.type = 'button';
                c.addEventListener('click', function () {
                    copy(p.text).then(function () { c.textContent = S.copied; });
                });
                if (p.image) {
                    var d = document.createElement('a');
                    d.href = p.image; d.download = ''; d.textContent = S.download;
                    panel.appendChild(d);
                }
            }

            document.addEventListener('click', function (event) {
                var open = event.target.closest('.engageai-share-open');
                document.querySelectorAll('.engageai-share-panel').forEach(function (panel) {
                    if (!open || panel !== open.parentNode.querySelector('.engageai-share-panel')) { panel.hidden = true; }
                });
                if (!open) { return; }
                var wrap = open.parentNode;
                var panel = wrap.querySelector('.engageai-share-panel');
                var p;
                try { p = JSON.parse(wrap.getAttribute('data-piece')); } catch (e) { return; }
                build(panel, p);
                panel.hidden = false;
            });

            document.addEventListener('DOMContentLoaded', function () {
                document.querySelectorAll('.engageai-share').forEach(function (wrap) {
                    try { prefetch(wrap, JSON.parse(wrap.getAttribute('data-piece'))); } catch (e) {}
                });
            });
        }());
        </script>
        <?php
    }
}
