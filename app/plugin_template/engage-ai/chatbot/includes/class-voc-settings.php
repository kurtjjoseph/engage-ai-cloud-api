<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_Settings {

    public static function init() {
        add_action( 'admin_init', [ __CLASS__, 'register_settings' ] );
    }

    /**
     * Defaults are deliberately derived from the site, not hardcoded to any one
     * business: this ships to every Engage AI customer. Stored settings always
     * win (get() merges them over these), so an existing install keeps whatever
     * its owner typed and only a fresh install ever sees these values.
     */
    public static function get_defaults() {
        $site = get_bloginfo( 'name' );
        if ( '' === trim( (string) $site ) ) {
            $site = 'our team';
        }

        return [
            // Branding & content
            'header_title'    => $site,
            'header_subtitle' => 'Online team',
            'greeting'        => sprintf( "Hi! I'm the %s assistant. Ask me anything about what we do, or leave your details and we'll get back to you.", $site ),
            'gdpr_line'       => 'Conversations may be processed by AI and stored to improve service.',
            'cta_label'       => 'Get in touch',
            'cta_href'        => '/contact/',
            'language'        => 'auto', // 'auto', 'en', 'nl', 'fr', 'de', 'es', 'pt', 'it'
            // Visual styling
            'primary_color'        => '#0F3FE0',
            'primary_hover_color'  => '#0B2FB5',
            'bubble_color'         => '#0F3FE0',
            'bubble_text_color'    => '#FFFFFF',
            'panel_bg'             => '#FFFFFF',
            'panel_text'           => '#0B0B0F',
            'font_family'          => "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
            'font_size_base'       => 14,    // px
            'border_radius'        => 16,    // px
            'position'             => 'right', // 'left' or 'right'
            // Behaviour
            'block_pricing'        => true,
            'rate_limit_per_min'   => 5,
            // Email & data
            'recipient_email'      => get_option( 'admin_email' ),
            'email_subject'        => sprintf( 'New chatbot lead — %s', $site ),
            'from_email'           => '', // empty → uses WP default
            'from_name'            => sprintf( '%s Chatbot', $site ),
            // AI provider. 'cloud' routes through the Engage AI Cloud API, which
            // the customer already has an account for; the other two are the
            // standalone options and need a backend or a key of their own.
            'ai_provider'          => 'cloud', // 'cloud', 'manus' or 'openai'
            'manus_api_url'        => '',
            // OpenAI (used when ai_provider = 'openai')
            'openai_api_key'       => '',
            'openai_model'         => 'gpt-4.1-mini',
            'openai_api_url'       => 'https://api.openai.com/v1/chat/completions',
        ];
    }

    public static function get( $key = null, $fallback = null ) {
        $opts = get_option( VOC_OPTION_KEY, [] );
        if ( ! is_array( $opts ) ) {
            $opts = [];
        }
        $opts = array_merge( self::get_defaults(), $opts );
        if ( $key === null ) {
            return $opts;
        }
        return isset( $opts[ $key ] ) ? $opts[ $key ] : $fallback;
    }

    public static function update( array $partial ) {
        $current = self::get();
        $next    = array_merge( $current, $partial );
        update_option( VOC_OPTION_KEY, $next );
        return $next;
    }

    public static function register_settings() {
        register_setting(
            'voc_settings_group',
            VOC_OPTION_KEY,
            [
                'type'              => 'array',
                'sanitize_callback' => [ __CLASS__, 'sanitize' ],
                'default'           => self::get_defaults(),
            ]
        );
    }

    public static function sanitize( $input ) {
        if ( ! is_array( $input ) ) {
            return self::get_defaults();
        }
        $defaults = self::get_defaults();
        $clean    = [];

        $clean['header_title']    = sanitize_text_field( $input['header_title']    ?? $defaults['header_title'] );
        $clean['header_subtitle'] = sanitize_text_field( $input['header_subtitle'] ?? $defaults['header_subtitle'] );
        $clean['greeting']        = sanitize_textarea_field( $input['greeting']    ?? $defaults['greeting'] );
        $clean['gdpr_line']       = sanitize_textarea_field( $input['gdpr_line']   ?? $defaults['gdpr_line'] );
        $clean['cta_label']       = sanitize_text_field( $input['cta_label']       ?? $defaults['cta_label'] );
        $clean['cta_href']        = esc_url_raw( $input['cta_href']                ?? $defaults['cta_href'] );

        $lang = strtolower( trim( (string) ( $input['language'] ?? $defaults['language'] ) ) );
        $clean['language'] = in_array( $lang, [ 'auto', 'en', 'nl', 'fr', 'de', 'es', 'pt', 'it' ], true ) ? $lang : 'auto';

        foreach ( [ 'primary_color', 'primary_hover_color', 'bubble_color', 'bubble_text_color', 'panel_bg', 'panel_text' ] as $color_key ) {
            $value          = sanitize_hex_color( $input[ $color_key ] ?? '' );
            $clean[ $color_key ] = $value ? $value : $defaults[ $color_key ];
        }

        $clean['font_family']    = sanitize_text_field( $input['font_family']    ?? $defaults['font_family'] );
        $clean['font_size_base'] = max( 10, min( 22, (int) ( $input['font_size_base'] ?? $defaults['font_size_base'] ) ) );
        $clean['border_radius']  = max( 0, min( 40, (int) ( $input['border_radius']  ?? $defaults['border_radius'] ) ) );

        $position = strtolower( trim( (string) ( $input['position'] ?? $defaults['position'] ) ) );
        $clean['position'] = in_array( $position, [ 'left', 'right' ], true ) ? $position : 'right';

        $clean['block_pricing']      = ! empty( $input['block_pricing'] );
        $clean['rate_limit_per_min'] = max( 0, min( 60, (int) ( $input['rate_limit_per_min'] ?? $defaults['rate_limit_per_min'] ) ) );

        $clean['recipient_email'] = sanitize_email( $input['recipient_email'] ?? $defaults['recipient_email'] );
        if ( empty( $clean['recipient_email'] ) ) {
            $clean['recipient_email'] = $defaults['recipient_email'];
        }
        $clean['email_subject'] = sanitize_text_field( $input['email_subject'] ?? $defaults['email_subject'] );
        $clean['from_email']    = isset( $input['from_email'] ) ? sanitize_email( $input['from_email'] ) : '';
        $clean['from_name']     = sanitize_text_field( $input['from_name']    ?? $defaults['from_name'] );

        $provider = strtolower( trim( (string) ( $input['ai_provider'] ?? $defaults['ai_provider'] ) ) );
        $clean['ai_provider']   = in_array( $provider, [ 'manus', 'openai' ], true ) ? $provider : 'manus';
        $clean['manus_api_url'] = esc_url_raw( $input['manus_api_url']  ?? $defaults['manus_api_url'] );
        if ( empty( $clean['manus_api_url'] ) ) {
            $clean['manus_api_url'] = $defaults['manus_api_url'];
        }

        $clean['openai_api_key'] = sanitize_text_field( $input['openai_api_key'] ?? '' );
        $clean['openai_model']   = sanitize_text_field( $input['openai_model']   ?? $defaults['openai_model'] );
        $clean['openai_api_url'] = esc_url_raw( $input['openai_api_url']  ?? $defaults['openai_api_url'] );
        if ( empty( $clean['openai_api_url'] ) ) {
            $clean['openai_api_url'] = $defaults['openai_api_url'];
        }

        return $clean;
    }
}
