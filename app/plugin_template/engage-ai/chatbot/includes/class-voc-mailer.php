<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

class VOC_Mailer {

    /**
     * Sends a lead notification email through wp_mail() using the configured From/Subject/Recipient.
     *
     * @return string status code: 'sent', 'failed', or 'no_recipient'.
     */
    public static function notify_lead( array $lead ) {
        $settings  = VOC_Settings::get();
        $recipient = $settings['recipient_email'];
        if ( empty( $recipient ) ) {
            return 'no_recipient';
        }

        $subject = $settings['email_subject'];
        $name    = isset( $lead['name'] ) ? (string) $lead['name'] : '';
        $email   = isset( $lead['email'] ) ? (string) $lead['email'] : '';
        $message = isset( $lead['message'] ) ? (string) $lead['message'] : '';
        $page    = isset( $lead['page_url'] ) ? (string) $lead['page_url'] : '';
        $ua      = isset( $lead['user_agent'] ) ? (string) $lead['user_agent'] : '';
        $ip      = isset( $lead['ip_address'] ) ? (string) $lead['ip_address'] : '';
        $ts      = isset( $lead['created_at'] ) ? (string) $lead['created_at'] : current_time( 'mysql' );

        $body  = "New chatbot lead from Vision Outreach Media's website chat:\n\n";
        $body .= "Name:    {$name}\n";
        $body .= "Email:   {$email}\n";
        $body .= "Message: {$message}\n\n";
        $body .= "Page:    {$page}\n";
        $body .= "Time:    {$ts}\n";
        $body .= "IP:      {$ip}\n";
        $body .= "Agent:   {$ua}\n";

        $headers = [];
        if ( ! empty( $email ) ) {
            $headers[] = 'Reply-To: ' . sanitize_text_field( $name ) . ' <' . $email . '>';
        }
        if ( ! empty( $settings['from_email'] ) ) {
            $from_name  = ! empty( $settings['from_name'] ) ? $settings['from_name'] : 'Vision Outreach Chatbot';
            $headers[]  = 'From: ' . $from_name . ' <' . $settings['from_email'] . '>';
        }

        $ok = wp_mail( $recipient, $subject, $body, $headers );
        return $ok ? 'sent' : 'failed';
    }
}
