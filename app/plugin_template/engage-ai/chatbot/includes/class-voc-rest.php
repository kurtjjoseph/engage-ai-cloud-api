<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_REST {

    public static function init() {
        add_action( 'rest_api_init', [ __CLASS__, 'register_routes' ] );
    }

    public static function register_routes() {
        register_rest_route(
            VOC_REST_NAMESPACE,
            '/chat',
            [
                'methods'             => 'POST',
                'callback'            => [ __CLASS__, 'handle_chat' ],
                'permission_callback' => '__return_true',
            ]
        );
        register_rest_route(
            VOC_REST_NAMESPACE,
            '/lead',
            [
                'methods'             => 'POST',
                'callback'            => [ __CLASS__, 'handle_lead' ],
                'permission_callback' => '__return_true',
            ]
        );
        register_rest_route(
            VOC_REST_NAMESPACE,
            '/test-email',
            [
                'methods'             => 'POST',
                'callback'            => [ __CLASS__, 'handle_test_email' ],
                'permission_callback' => function () { return current_user_can( 'manage_options' ); },
            ]
        );
    }

    private static function client_ip() {
        $candidates = [
            'HTTP_CF_CONNECTING_IP',
            'HTTP_X_FORWARDED_FOR',
            'HTTP_X_REAL_IP',
            'REMOTE_ADDR',
        ];
        foreach ( $candidates as $key ) {
            if ( ! empty( $_SERVER[ $key ] ) ) {
                $val = is_string( $_SERVER[ $key ] ) ? $_SERVER[ $key ] : '';
                $val = trim( explode( ',', $val )[0] );
                if ( filter_var( $val, FILTER_VALIDATE_IP ) ) {
                    return $val;
                }
            }
        }
        return '';
    }

    /**
     * Returns true if the IP exceeded the rate limit.
     */
    private static function is_rate_limited( $ip ) {
        $limit = (int) VOC_Settings::get( 'rate_limit_per_min', 5 );
        if ( $limit <= 0 || empty( $ip ) ) {
            return false;
        }
        $key   = 'voc_rl_' . md5( $ip );
        $count = (int) get_transient( $key );
        if ( $count >= $limit ) {
            return true;
        }
        set_transient( $key, $count + 1, MINUTE_IN_SECONDS );
        return false;
    }

    public static function handle_chat( WP_REST_Request $req ) {
        $ip = self::client_ip();
        if ( self::is_rate_limited( $ip ) ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => 'rate_limited' ], 429 );
        }

        $body = $req->get_json_params();
        if ( ! is_array( $body ) ) { $body = []; }
        $messages = isset( $body['messages'] ) && is_array( $body['messages'] ) ? $body['messages'] : [];
        $supported = [ 'en', 'nl', 'fr', 'de', 'es', 'pt', 'it' ];
        $language  = isset( $body['language'] ) ? strtolower( substr( (string) $body['language'], 0, 2 ) ) : '';
        if ( ! in_array( $language, $supported, true ) ) { $language = 'en'; }

        // Sanitize and bound the message list.
        $clean = [];
        foreach ( $messages as $m ) {
            if ( ! is_array( $m ) ) continue;
            $role = isset( $m['role'] ) ? (string) $m['role'] : '';
            $content = isset( $m['content'] ) ? (string) $m['content'] : '';
            if ( ! in_array( $role, [ 'user', 'assistant' ], true ) ) continue;
            $content = mb_substr( $content, 0, 2000 );
            $clean[] = [ 'role' => $role, 'content' => $content ];
        }
        // Cap conversation length to last 16 turns.
        if ( count( $clean ) > 16 ) {
            $clean = array_slice( $clean, -16 );
        }

        $provider = (string) VOC_Settings::get( 'ai_provider', 'manus' );

        // Ground the turn in the Site Brain — this website's own index — rather than a
        // hardcoded knowledge blob. Retrieval happens here, on the site, because that is
        // where the index lives; only the retrieved result travels.
        $ground = class_exists( 'VOC_Brain_Bridge' )
            ? VOC_Brain_Bridge::ground( $clean, $language )
            : [ 'system' => '' ];

        // Engage AI Cloud: the site sends what it found and the API owns the answering
        // protocol, so a customer needs no AI account of their own. Preferred whenever it
        // is connected, whatever the stored provider says — the stored value is only
        // consulted for the standalone builds below.
        if ( 'openai' !== $provider && class_exists( 'VOC_Cloud_AI' ) && VOC_Cloud_AI::available() ) {
            $grounding = class_exists( 'VOC_Brain_Bridge' ) ? VOC_Brain_Bridge::grounding( $clean, $language ) : [];
            $result    = VOC_Cloud_AI::chat( $clean, $language, $grounding );
            if ( ! empty( $result['ok'] ) ) {
                return new WP_REST_Response( [ 'ok' => true, 'reply' => $result['reply'] ], 200 );
            }
            // Anything else (token expired, API down) falls through to the paths below.
        }

        if ( '' !== $ground['system'] ) {
            $system = $ground['system'];

            // Honour the admin's pricing switch even when the brain holds a sanctioned figure.
            if ( ! empty( VOC_Settings::get( 'block_pricing' ) ) ) {
                $system .= "\n\nIMPORTANT: Never quote specific prices, packages, or numeric figures, even if they appear above. Say pricing depends on scope and offer to capture their details for a free consultation.";
            }

            $grounded = $clean;
            array_unshift( $grounded, [ 'role' => 'system', 'content' => $system ] );
            $result = VOC_OpenAI::chat( $grounded );

            // Fail open: if the grounded call errors (no key, quota, outage), fall back to the
            // provider that was serving before, so the widget never degrades to an error bubble.
            if ( empty( $result['ok'] ) ) {
                $result = ( 'manus' === $provider )
                    ? VOC_Manus_AI::chat( $clean, $language )
                    : $result;
            }
        } elseif ( $provider === 'manus' ) {
            // Manus backend strips system turns; client adds a language prefix to the latest user message.
            $result = VOC_Manus_AI::chat( $clean, $language );
        } else {
            $system = VOC_OpenAI::build_system_prompt() . "\n\n" . self::language_instruction( $language );
            array_unshift( $clean, [ 'role' => 'system', 'content' => $system ] );
            $result = VOC_OpenAI::chat( $clean );
        }
        if ( ! $result['ok'] ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => $result['error'] ], 500 );
        }
        return new WP_REST_Response( [ 'ok' => true, 'reply' => $result['reply'] ], 200 );
    }

    private static function language_instruction( $code ) {
        $names = [
            'en' => 'English',
            'nl' => 'Dutch (Nederlands)',
            'fr' => 'French (Français)',
            'de' => 'German (Deutsch)',
            'es' => 'Spanish (Español)',
            'pt' => 'Portuguese (Português)',
            'it' => 'Italian (Italiano)',
        ];
        $name = isset( $names[ $code ] ) ? $names[ $code ] : 'English';
        return 'IMPORTANT: The visitor\'s browser language is ' . $name . '. ALWAYS reply in ' . $name . ' UNLESS the visitor explicitly writes in another language — in that case reply in the language they used. Mirror the visitor\'s tone and language throughout the conversation.';
    }

    public static function handle_lead( WP_REST_Request $req ) {
        $ip = self::client_ip();
        if ( self::is_rate_limited( $ip ) ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => 'rate_limited' ], 429 );
        }

        $body = $req->get_json_params();
        if ( ! is_array( $body ) ) { $body = []; }

        $name    = isset( $body['name'] ) ? sanitize_text_field( $body['name'] ) : '';
        $email   = isset( $body['email'] ) ? sanitize_email( $body['email'] ) : '';
        $message = isset( $body['message'] ) ? sanitize_textarea_field( $body['message'] ) : '';
        $page    = isset( $body['pageUrl'] ) ? esc_url_raw( $body['pageUrl'] ) : '';
        $ua      = isset( $_SERVER['HTTP_USER_AGENT'] ) ? substr( (string) $_SERVER['HTTP_USER_AGENT'], 0, 510 ) : '';

        if ( $name === '' || $email === '' || $message === '' ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => 'name, email, and message are required' ], 400 );
        }
        if ( ! is_email( $email ) ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => 'invalid email' ], 400 );
        }

        // Insert into the leads table.
        global $wpdb;
        $table = $wpdb->prefix . VOC_LEADS_TABLE;
        $now   = current_time( 'mysql' );
        $row   = [
            'name'        => $name,
            'email'       => $email,
            'message'     => $message,
            'page_url'    => $page,
            'user_agent'  => $ua,
            'ip_address'  => $ip,
            'email_status'=> 'queued',
            'created_at'  => $now,
        ];
        $inserted = $wpdb->insert( $table, $row );
        if ( ! $inserted ) {
            return new WP_REST_Response( [ 'ok' => false, 'error' => 'database error' ], 500 );
        }
        $lead_id = (int) $wpdb->insert_id;

        // Send notification email.
        $status = VOC_Mailer::notify_lead( array_merge( $row, [ 'id' => $lead_id ] ) );
        $wpdb->update( $table, [ 'email_status' => $status ], [ 'id' => $lead_id ] );

        return new WP_REST_Response( [ 'ok' => true, 'id' => $lead_id, 'emailStatus' => $status ], 200 );
    }

    public static function handle_test_email( WP_REST_Request $req ) {
        $body  = $req->get_json_params();
        $name  = isset( $body['name'] )    ? sanitize_text_field( $body['name'] )    : 'Test Lead';
        $email = isset( $body['email'] )   ? sanitize_email( $body['email'] )         : 'test@example.com';
        $msg   = isset( $body['message'] ) ? sanitize_textarea_field( $body['message'] ) : 'please ignore';

        $status = VOC_Mailer::notify_lead( [
            'name'       => $name,
            'email'      => $email,
            'message'    => $msg,
            'page_url'   => admin_url( 'admin.php?page=voc-settings' ),
            'user_agent' => 'Vision Outreach Chatbot self-test',
            'ip_address' => '',
            'created_at' => current_time( 'mysql' ),
        ] );
        return new WP_REST_Response( [ 'ok' => $status === 'sent', 'status' => $status ], 200 );
    }
}
