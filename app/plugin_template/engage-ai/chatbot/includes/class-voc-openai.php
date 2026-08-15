<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_OpenAI {

    /**
     * Calls OpenAI Chat Completions API.
     *
     * @param array  $messages  array of {role, content} messages (system already prepended by caller).
     * @return array            ['ok' => bool, 'reply' => string|null, 'error' => string|null]
     */
    public static function chat( array $messages ) {
        $api_key = trim( (string) VOC_Settings::get( 'openai_api_key', '' ) );
        $model   = trim( (string) VOC_Settings::get( 'openai_model', 'gpt-4.1-mini' ) );
        $api_url = trim( (string) VOC_Settings::get( 'openai_api_url', 'https://api.openai.com/v1/chat/completions' ) );

        if ( $api_key === '' ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'OpenAI API key is not configured. Open the Vision Outreach Chatbot → Settings page and paste your key.',
            ];
        }

        $payload = [
            'model'       => $model,
            'messages'    => $messages,
            'temperature' => 0.4,
            'max_tokens'  => 600,
        ];

        $response = wp_remote_post(
            $api_url,
            [
                'headers' => [
                    'Authorization' => 'Bearer ' . $api_key,
                    'Content-Type'  => 'application/json',
                ],
                'body'    => wp_json_encode( $payload ),
                'timeout' => 30,
            ]
        );

        if ( is_wp_error( $response ) ) {
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'OpenAI request failed: ' . $response->get_error_message(),
            ];
        }

        $code = wp_remote_retrieve_response_code( $response );
        $body = wp_remote_retrieve_body( $response );
        $data = json_decode( $body, true );

        if ( $code < 200 || $code >= 300 ) {
            $err = is_array( $data ) && isset( $data['error']['message'] ) ? $data['error']['message'] : ( 'HTTP ' . $code );
            return [
                'ok'    => false,
                'reply' => null,
                'error' => 'OpenAI error: ' . $err,
            ];
        }

        $reply = isset( $data['choices'][0]['message']['content'] ) ? (string) $data['choices'][0]['message']['content'] : '';
        return [
            'ok'    => true,
            'reply' => $reply,
            'error' => null,
        ];
    }

    /**
     * Build the system prompt grounded in the bundled knowledge base + current settings.
     */
    public static function build_system_prompt() {
        $kb       = VOC_Knowledge::get_summary();
        $settings = VOC_Settings::get();
        $lang     = $settings['language'];
        $block_pricing = ! empty( $settings['block_pricing'] );

        $extra = $block_pricing
            ? 'IMPORTANT: Never quote specific prices, packages, or numeric figures, even if they appear elsewhere. Always say pricing depends on scope and offer to capture their details for a free consultation.'
            : '';

        $lang_line = $lang === 'nl'
            ? 'Default to replying in Dutch unless the user clearly writes in English.'
            : 'Default to replying in English unless the user clearly writes in Dutch.';

        return "You are the AI website assistant for Vision Outreach Media. Stay friendly, concise, and helpful.\n\n"
            . "KNOWLEDGE BASE:\n" . $kb . "\n\n"
            . $lang_line . "\n"
            . $extra . "\n"
            . "When you collect lead info (name + email + message), confirm you'll pass it to Kurt and stop asking further questions.";
    }
}
