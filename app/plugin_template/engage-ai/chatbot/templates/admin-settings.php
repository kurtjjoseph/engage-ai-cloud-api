<?php
if ( ! defined( 'ABSPATH' ) ) { exit; }
$settings = VOC_Settings::get();
?>
<div class="wrap">
    <h1>Vision Outreach Chatbot — Settings</h1>
    <p>Configure the chatbot widget that appears on every page of your website. Changes take effect immediately.</p>

    <form method="post" action="options.php">
        <?php settings_fields( 'voc_settings_group' ); ?>

        <h2 class="title">Branding & Content</h2>
        <table class="form-table">
            <tr>
                <th><label for="voc_header_title">Header title</label></th>
                <td><input class="regular-text" type="text" id="voc_header_title" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[header_title]" value="<?php echo esc_attr( $settings['header_title'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_header_subtitle">Header subtitle</label></th>
                <td><input class="regular-text" type="text" id="voc_header_subtitle" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[header_subtitle]" value="<?php echo esc_attr( $settings['header_subtitle'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_greeting">Greeting message</label></th>
                <td><textarea class="large-text" rows="3" id="voc_greeting" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[greeting]"><?php echo esc_textarea( $settings['greeting'] ); ?></textarea></td>
            </tr>
            <tr>
                <th><label for="voc_gdpr_line">GDPR disclosure</label></th>
                <td><textarea class="large-text" rows="2" id="voc_gdpr_line" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[gdpr_line]"><?php echo esc_textarea( $settings['gdpr_line'] ); ?></textarea></td>
            </tr>
            <tr>
                <th><label for="voc_cta_label">CTA button label</label></th>
                <td><input class="regular-text" type="text" id="voc_cta_label" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[cta_label]" value="<?php echo esc_attr( $settings['cta_label'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_cta_href">CTA button URL</label></th>
                <td><input class="regular-text" type="text" id="voc_cta_href" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[cta_href]" value="<?php echo esc_attr( $settings['cta_href'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_language">Language</label></th>
                <td>
                    <select id="voc_language" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[language]">
                        <option value="auto" <?php selected( $settings['language'], 'auto' ); ?>>Auto-detect (recommended)</option>
                        <option value="en" <?php selected( $settings['language'], 'en' ); ?>>English</option>
                        <option value="nl" <?php selected( $settings['language'], 'nl' ); ?>>Nederlands (Dutch)</option>
                        <option value="fr" <?php selected( $settings['language'], 'fr' ); ?>>Français (French)</option>
                        <option value="de" <?php selected( $settings['language'], 'de' ); ?>>Deutsch (German)</option>
                        <option value="es" <?php selected( $settings['language'], 'es' ); ?>>Español (Spanish)</option>
                        <option value="pt" <?php selected( $settings['language'], 'pt' ); ?>>Português (Portuguese)</option>
                        <option value="it" <?php selected( $settings['language'], 'it' ); ?>>Italiano (Italian)</option>
                    </select>
                    <p class="description">Auto-detect uses the visitor’s browser language. The AI will mirror whichever language the visitor types in, regardless of this setting.</p>
                </td>
            </tr>
        </table>

        <h2 class="title">Visual Style</h2>
        <table class="form-table">
            <tr>
                <th><label for="voc_primary_color">Primary color</label></th>
                <td><input type="color" id="voc_primary_color" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[primary_color]" value="<?php echo esc_attr( $settings['primary_color'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_primary_hover_color">Primary hover color</label></th>
                <td><input type="color" id="voc_primary_hover_color" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[primary_hover_color]" value="<?php echo esc_attr( $settings['primary_hover_color'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_bubble_color">Bubble color</label></th>
                <td><input type="color" id="voc_bubble_color" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[bubble_color]" value="<?php echo esc_attr( $settings['bubble_color'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_bubble_text_color">Bubble icon color</label></th>
                <td><input type="color" id="voc_bubble_text_color" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[bubble_text_color]" value="<?php echo esc_attr( $settings['bubble_text_color'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_panel_bg">Panel background</label></th>
                <td><input type="color" id="voc_panel_bg" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[panel_bg]" value="<?php echo esc_attr( $settings['panel_bg'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_panel_text">Panel text</label></th>
                <td><input type="color" id="voc_panel_text" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[panel_text]" value="<?php echo esc_attr( $settings['panel_text'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_font_family">Font family</label></th>
                <td><input class="large-text" type="text" id="voc_font_family" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[font_family]" value="<?php echo esc_attr( $settings['font_family'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_font_size_base">Base font size (px)</label></th>
                <td><input type="number" min="10" max="22" id="voc_font_size_base" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[font_size_base]" value="<?php echo esc_attr( $settings['font_size_base'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_border_radius">Border radius (px)</label></th>
                <td><input type="number" min="0" max="40" id="voc_border_radius" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[border_radius]" value="<?php echo esc_attr( $settings['border_radius'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_position">Bubble position</label></th>
                <td>
                    <select id="voc_position" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[position]">
                        <option value="right" <?php selected( $settings['position'], 'right' ); ?>>Bottom right</option>
                        <option value="left"  <?php selected( $settings['position'], 'left' ); ?>>Bottom left</option>
                    </select>
                </td>
            </tr>
        </table>

        <h2 class="title">Behaviour</h2>
        <table class="form-table">
            <tr>
                <th><label for="voc_block_pricing">Block pricing answers</label></th>
                <td>
                    <label><input type="checkbox" id="voc_block_pricing" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[block_pricing]" value="1" <?php checked( ! empty( $settings['block_pricing'] ) ); ?>>
                    Always defer pricing questions to a consultation, even if numbers are public.</label>
                </td>
            </tr>
            <tr>
                <th><label for="voc_rate_limit">Rate limit (requests / IP / minute)</label></th>
                <td><input type="number" min="0" max="60" id="voc_rate_limit" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[rate_limit_per_min]" value="<?php echo esc_attr( $settings['rate_limit_per_min'] ); ?>"> <span class="description">Set to 0 to disable.</span></td>
            </tr>
        </table>

        <h2 class="title">Lead Email</h2>
        <table class="form-table">
            <tr>
                <th><label for="voc_recipient_email">Recipient email</label></th>
                <td><input class="regular-text" type="email" id="voc_recipient_email" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[recipient_email]" value="<?php echo esc_attr( $settings['recipient_email'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_email_subject">Email subject</label></th>
                <td><input class="regular-text" type="text" id="voc_email_subject" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[email_subject]" value="<?php echo esc_attr( $settings['email_subject'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_from_email">From email (optional)</label></th>
                <td><input class="regular-text" type="email" id="voc_from_email" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[from_email]" value="<?php echo esc_attr( $settings['from_email'] ); ?>" placeholder="leave empty to use WordPress default"></td>
            </tr>
            <tr>
                <th><label for="voc_from_name">From name</label></th>
                <td><input class="regular-text" type="text" id="voc_from_name" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[from_name]" value="<?php echo esc_attr( $settings['from_name'] ); ?>"></td>
            </tr>
        </table>

        <h2 class="title">AI Provider</h2>
        <p class="description" style="max-width:720px;">Choose where the chatbot's AI replies come from. The default <strong>Manus-hosted backend</strong> is free and ready to use; the OpenAI option below is only used when you switch the dropdown.</p>
        <table class="form-table">
            <tr>
                <th><label for="voc_ai_provider">Provider</label></th>
                <td>
                    <select id="voc_ai_provider" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[ai_provider]">
                        <option value="manus"  <?php selected( $settings['ai_provider'], 'manus' ); ?>>Manus-hosted (free, no key needed)</option>
                        <option value="openai" <?php selected( $settings['ai_provider'], 'openai' ); ?>>OpenAI / OpenAI-compatible (bring your own key)</option>
                    </select>
                </td>
            </tr>
            <tr>
                <th><label for="voc_manus_api_url">Manus AI URL</label></th>
                <td><input class="large-text" type="text" id="voc_manus_api_url" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[manus_api_url]" value="<?php echo esc_attr( $settings['manus_api_url'] ); ?>"><p class="description">Default points at the Vision Outreach Media hosted backend. Only change if you've been given a different URL.</p></td>
            </tr>
        </table>

        <h2 class="title">OpenAI (only used when provider = OpenAI)</h2>
        <table class="form-table">
            <tr>
                <th><label for="voc_openai_api_key">API key</label></th>
                <td>
                    <input class="regular-text" type="password" id="voc_openai_api_key" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[openai_api_key]" value="<?php echo esc_attr( $settings['openai_api_key'] ); ?>" autocomplete="off">
                    <p class="description">Stored in <code>wp_options</code>. Get a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com</a>.</p>
                </td>
            </tr>
            <tr>
                <th><label for="voc_openai_model">Model</label></th>
                <td><input class="regular-text" type="text" id="voc_openai_model" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[openai_model]" value="<?php echo esc_attr( $settings['openai_model'] ); ?>"></td>
            </tr>
            <tr>
                <th><label for="voc_openai_api_url">API URL</label></th>
                <td><input class="large-text" type="text" id="voc_openai_api_url" name="<?php echo esc_attr( VOC_OPTION_KEY ); ?>[openai_api_url]" value="<?php echo esc_attr( $settings['openai_api_url'] ); ?>"><p class="description">Defaults to OpenAI. Change to use an OpenAI-compatible provider (Groq, OpenRouter, etc.).</p></td>
            </tr>
        </table>

        <?php submit_button( 'Save changes' ); ?>
    </form>

    <hr>
    <h2>Send a test email</h2>
    <p>Verify your SMTP / mail setup by triggering a test lead notification to <strong><?php echo esc_html( $settings['recipient_email'] ); ?></strong>.</p>
    <button type="button" class="button button-secondary" id="voc-send-test-email">Send test email</button>
    <span id="voc-test-status" style="margin-left: 10px;"></span>

    <script>
    (function () {
        var btn = document.getElementById('voc-send-test-email');
        var status = document.getElementById('voc-test-status');
        if (!btn) return;
        btn.addEventListener('click', function () {
            status.textContent = 'Sending...';
            fetch('<?php echo esc_url_raw( rest_url( VOC_REST_NAMESPACE . '/test-email' ) ); ?>', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-WP-Nonce': '<?php echo esc_js( wp_create_nonce( 'wp_rest' ) ); ?>'
                },
                body: JSON.stringify({ name: 'Test Lead', email: 'test@example.com', message: 'please ignore' })
            })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                status.textContent = data.ok ? '✓ Sent (status: ' + data.status + ')' : '✗ Failed (' + (data.status || 'error') + ')';
            })
            .catch(function (err) { status.textContent = 'Error: ' + err.message; });
        });
    })();
    </script>
</div>
