<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_Admin {

    public static function init() {
        add_action( 'admin_menu', [ __CLASS__, 'register_menus' ] );

        // Only a real plugin has an entry on the Plugins screen to add a link to.
        if ( defined( 'VOC_PLUGIN_FILE' ) && VOC_PLUGIN_FILE ) {
            add_filter( 'plugin_action_links_' . plugin_basename( VOC_PLUGIN_FILE ), [ __CLASS__, 'plugin_action_links' ] );
        }
    }

    /**
     * Standalone, this is its own top-level menu. Running as a module inside
     * another plugin, VOC_MENU_PARENT points at that plugin's menu and the
     * screens are nested under it instead of adding a second top-level item.
     */
    public static function register_menus() {
        $parent = defined( 'VOC_MENU_PARENT' ) ? VOC_MENU_PARENT : '';

        if ( $parent ) {
            add_submenu_page(
                $parent,
                'Chatbot',
                'Chatbot',
                'manage_options',
                'voc-leads',
                [ __CLASS__, 'render_leads_page' ]
            );
            add_submenu_page(
                $parent,
                'Chatbot Settings',
                'Chatbot Settings',
                'manage_options',
                'voc-settings',
                [ __CLASS__, 'render_settings_page' ]
            );
            return;
        }

        add_menu_page(
            'Vision Outreach Chatbot',
            'Chatbot',
            'manage_options',
            'voc-leads',
            [ __CLASS__, 'render_leads_page' ],
            'dashicons-format-chat',
            58
        );
        add_submenu_page(
            'voc-leads',
            'Captured Leads',
            'Leads',
            'manage_options',
            'voc-leads',
            [ __CLASS__, 'render_leads_page' ]
        );
        add_submenu_page(
            'voc-leads',
            'Chatbot Settings',
            'Settings',
            'manage_options',
            'voc-settings',
            [ __CLASS__, 'render_settings_page' ]
        );
    }

    public static function plugin_action_links( $links ) {
        $url = admin_url( 'admin.php?page=voc-settings' );
        $links[] = '<a href="' . esc_url( $url ) . '">Settings</a>';
        return $links;
    }

    public static function render_settings_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            wp_die( 'Insufficient permissions.' );
        }
        require VOC_PLUGIN_DIR . 'templates/admin-settings.php';
    }

    public static function render_leads_page() {
        if ( ! current_user_can( 'manage_options' ) ) {
            wp_die( 'Insufficient permissions.' );
        }
        require VOC_PLUGIN_DIR . 'templates/admin-leads.php';
    }

    public static function fetch_leads( $limit = 200 ) {
        global $wpdb;
        $table = $wpdb->prefix . VOC_LEADS_TABLE;
        return $wpdb->get_results(
            $wpdb->prepare( "SELECT * FROM $table ORDER BY created_at DESC LIMIT %d", $limit ),
            ARRAY_A
        );
    }
}
