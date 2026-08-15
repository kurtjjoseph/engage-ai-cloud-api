<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

/**
 * Client for the Manus-hosted AI backend.
 * The backend exposes POST {manus_api_url} expecting:
 *   { "messages": [ { "role": "user|assistant", "content": "..." }, ... ] }
 * and returns:
 *   { "ok": true, "reply": "..." }   on success
 *   { "ok": false, "error": "..." }  on failure
 *
 * The system prompt + knowledge base live on the Manus side, so we DO NOT
 * forward the WP-side system prompt or KB. We strip 'system' messages here
 * to keep the payload small.
 */
class VOC_Manus_AI {

    public static function chat( array $messages, $language = 'en' ) {
        $api_url = trim( (string) VOC_Settings::get( 'manus_api_url', 'https://visionchat-eows3k2e.manus.space/api/chat' ) );
        if ( $api_url === '' ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Manus AI URL is not configured.',
            ];
        }

        // Drop system messages; the Manus backend builds its own.
        $forward = [];
        foreach ( $messages as $m ) {
            if ( ! is_array( $m ) ) continue;
            $role    = isset( $m['role'] ) ? (string) $m['role'] : '';
            $content = isset( $m['content'] ) ? (string) $m['content'] : '';
            if ( ! in_array( $role, [ 'user', 'assistant' ], true ) ) continue;
            $forward[] = [ 'role' => $role, 'content' => $content ];
        }

        // The Manus backend's system prompt forces English. To override, we
        // wrap the user's latest message with explicit instructions that the
        // model treats as user-supplied (and therefore weighs heavily).
        $names = [ 'en' => 'English', 'nl' => 'Dutch (Nederlands)', 'fr' => 'French (Français)', 'de' => 'German (Deutsch)', 'es' => 'Spanish (Español)', 'pt' => 'Portuguese (Português)', 'it' => 'Italian (Italiano)' ];
        $lang_name = isset( $names[ $language ] ) ? $names[ $language ] : 'English';

        if ( $language !== 'en' ) {
            // Inject a synthetic assistant + user turn pair that establishes the
            // language mode, then deliver the user's actual question on top.
            for ( $i = count( $forward ) - 1; $i >= 0; $i-- ) {
                if ( $forward[ $i ]['role'] === 'user' ) {
                    $original = $forward[ $i ]['content'];
                    $forward[ $i ]['content'] = $original . "\n\n(IMPORTANT INSTRUCTION FROM USER: Please respond entirely in " . $lang_name . ". Do not mention English. Do not apologise. Do not switch languages. Just answer in " . $lang_name . ".)";
                    break;
                }
            }
        }

        $response = wp_remote_post(
            $api_url,
            [
                'headers' => [
                    'Content-Type' => 'application/json',
                    'Origin'       => home_url(),
                ],
                'body'    => wp_json_encode( [ 'messages' => $forward ] ),
                'timeout' => 30,
            ]
        );

        if ( is_wp_error( $response ) ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Manus AI request failed: ' . $response->get_error_message(),
            ];
        }

        $code = wp_remote_retrieve_response_code( $response );
        $body = wp_remote_retrieve_body( $response );
        $data = json_decode( $body, true );

        if ( $code < 200 || $code >= 300 ) {
            $err = is_array( $data ) && isset( $data['error'] ) ? (string) $data['error'] : ( 'HTTP ' . $code );
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Manus AI error: ' . $err,
            ];
        }

        if ( ! is_array( $data ) || empty( $data['ok'] ) || ! isset( $data['reply'] ) ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Manus AI returned an unexpected response.',
            ];
        }

        return [
            'ok'    => true,
            'reply' => (string) $data['reply'],
            'error' => null,
        ];
    }
}
