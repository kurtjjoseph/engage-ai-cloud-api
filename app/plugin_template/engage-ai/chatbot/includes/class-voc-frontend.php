<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_Frontend {

    public static function init() {
        add_action( 'wp_enqueue_scripts', [ __CLASS__, 'enqueue_assets' ] );
    }

    public static function enqueue_assets() {
        // Don't load the widget on admin pages or login screen.
        if ( is_admin() ) {
            return;
        }

        $settings = VOC_Settings::get();

        wp_register_style(
            'voc-widget',
            VOC_PLUGIN_URL . 'assets/widget.css',
            [],
            VOC_VERSION
        );
        wp_enqueue_style( 'voc-widget' );

        wp_register_script(
            'voc-widget',
            VOC_PLUGIN_URL . 'assets/widget.js',
            [],
            VOC_VERSION,
            true // load in footer
        );

        // Expose only the safe-to-share settings to the browser.
        $public_settings = [
            'restUrl'     => esc_url_raw( rest_url( VOC_REST_NAMESPACE ) ),
            'nonce'       => wp_create_nonce( 'wp_rest' ),
            'header'      => [
                'title'    => $settings['header_title'],
                'subtitle' => $settings['header_subtitle'],
            ],
            'greeting'    => $settings['greeting'],
            'gdprLine'    => $settings['gdpr_line'],
            'cta'         => [
                'label' => $settings['cta_label'],
                'href'  => $settings['cta_href'],
            ],
            'language'    => $settings['language'],
            'style'       => [
                'primaryColor'      => $settings['primary_color'],
                'primaryHoverColor' => $settings['primary_hover_color'],
                'bubbleColor'       => $settings['bubble_color'],
                'bubbleTextColor'   => $settings['bubble_text_color'],
                'panelBg'           => $settings['panel_bg'],
                'panelText'         => $settings['panel_text'],
                'fontFamily'        => $settings['font_family'],
                'fontSizeBase'      => (int) $settings['font_size_base'],
                'borderRadius'      => (int) $settings['border_radius'],
                'position'          => $settings['position'],
            ],
        ];

        wp_localize_script( 'voc-widget', 'VOC_CONFIG', $public_settings );
        wp_enqueue_script( 'voc-widget' );
    }
}
