<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_Installer {

    public static function activate() {
        self::create_leads_table();
        self::seed_default_settings();
    }

    public static function deactivate() {
        // Intentionally do not drop the leads table on deactivation; preserve user data.
    }

    public static function create_leads_table() {
        global $wpdb;
        $table_name      = $wpdb->prefix . VOC_LEADS_TABLE;
        $charset_collate = $wpdb->get_charset_collate();

        $sql = "CREATE TABLE $table_name (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            name VARCHAR(200) NOT NULL,
            email VARCHAR(320) NOT NULL,
            message TEXT NOT NULL,
            page_url VARCHAR(1024) DEFAULT NULL,
            user_agent VARCHAR(512) DEFAULT NULL,
            ip_address VARCHAR(64) DEFAULT NULL,
            email_status VARCHAR(64) DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY  (id),
            KEY created_at (created_at),
            KEY email (email)
        ) $charset_collate;";

        require_once ABSPATH . 'wp-admin/includes/upgrade.php';
        dbDelta( $sql );
    }

    public static function seed_default_settings() {
        $existing = get_option( VOC_OPTION_KEY );
        if ( ! is_array( $existing ) || empty( $existing ) ) {
            update_option( VOC_OPTION_KEY, VOC_Settings::get_defaults() );
        } else {
            // Merge new defaults so future fields appear automatically.
            $merged = array_merge( VOC_Settings::get_defaults(), $existing );
            update_option( VOC_OPTION_KEY, $merged );
        }
    }
}
