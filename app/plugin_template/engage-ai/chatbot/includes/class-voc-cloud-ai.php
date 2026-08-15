<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

/**
 * Client for the Engage AI Cloud API's chatbot endpoint.
 *
 * The default provider when the chatbot runs as an Engage AI module: the site
 * already has an Engage AI account, so nobody has to open an OpenAI account or
 * paste a key to get a working assistant.
 *
 * This sends what the site knows — the grounding retrieved from its own Site
 * Brain — and never a system prompt. The rules the public assistant answers
 * under live on the API side on purpose, so they cannot be rewritten from a
 * WordPress option.
 *
 * Available only inside the module; the standalone plugin has no Engage AI
 * connection to borrow and falls back to its own provider settings.
 */
class VOC_Cloud_AI {

    /** True when Engage AI is present, connected, and knows its organization. */
    public static function available() {
        if ( ! class_exists( 'EngageAI_Api_Client' ) ) {
            return false;
        }
        $client = new EngageAI_Api_Client();
        return $client->is_connected() && $client->get_organization_id();
    }

    /**
     * @param array  $messages  Visitor conversation: [{role: user|assistant, content}].
     * @param string $language  Two-letter language code.
     * @param array  $grounding From VOC_Brain_Bridge::grounding().
     * @return array ['ok' => bool, 'reply' => string|null, 'error' => string|null]
     */
    public static function chat( array $messages, $language, array $grounding ) {
        if ( ! self::available() ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Engage AI is not connected. Connect it in Engage AI → Settings, or pick another AI provider.',
            ];
        }

        $client = new EngageAI_Api_Client();
        $org_id = (int) $client->get_organization_id();

        $payload = [
            'messages'  => array_values( $messages ),
            'language'  => $language,
            'grounding' => [
                'persona'       => isset( $grounding['persona'] ) ? $grounding['persona'] : '',
                'facts'         => isset( $grounding['facts'] ) ? (object) $grounding['facts'] : (object) [],
                'faqs'          => isset( $grounding['faqs'] ) ? array_values( $grounding['faqs'] ) : [],
                'passages'      => isset( $grounding['passages'] ) ? array_values( $grounding['passages'] ) : [],
                'escalation'    => isset( $grounding['escalation'] ) ? $grounding['escalation'] : '',
                'block_pricing' => (bool) VOC_Settings::get( 'block_pricing' ),
            ],
        ];

        $result = $client->chatbot_reply( $org_id, $payload );

        if ( is_wp_error( $result ) ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'Engage AI request failed: ' . $result->get_error_message(),
            ];
        }

        $reply = isset( $result['reply'] ) ? trim( (string) $result['reply'] ) : '';
        if ( '' === $reply ) {
            return [ 'ok' => false, 'reply' => null, 'error' => 'Engage AI returned an empty reply.' ];
        }

        return [ 'ok' => true, 'reply' => $reply, 'error' => null ];
    }
}
