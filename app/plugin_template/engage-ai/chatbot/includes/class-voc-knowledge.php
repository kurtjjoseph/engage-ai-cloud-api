<?php
if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

/**
 * Last-resort context for the assistant.
 *
 * This used to be a hand-written description of one business. It is now a thin
 * summary generated from whatever WordPress already knows, because the real
 * knowledge comes from the Site Brain: VOC_Brain_Bridge retrieves the pages
 * that actually answer the question and that is what reaches the model.
 *
 * This text is only reached when the brain returned nothing at all — it is not
 * installed, or switched off, or has not finished its first crawl. In that case
 * the right behaviour is to admit the site cannot be read yet and hand the
 * visitor to a human, not to improvise a company description.
 */
class VOC_Knowledge {

    public static function get_summary() {
        $name    = get_bloginfo( 'name' );
        $tagline = get_bloginfo( 'description' );
        $url     = home_url( '/' );

        $lines = [];
        $lines[] = 'You are the assistant on the website of ' . ( $name !== '' ? $name : 'this organization' ) . '.';
        if ( $tagline !== '' ) {
            $lines[] = 'The site describes itself as: ' . $tagline;
        }
        $lines[] = 'Website: ' . $url;
        $lines[] = '';
        $lines[] = 'IMPORTANT: the site index is unavailable for this question, so you have no';
        $lines[] = 'detail about this organization beyond the two lines above. Do not describe its';
        $lines[] = 'services, prices, hours, staff or history — you do not know them, and guessing';
        $lines[] = 'in public is worse than admitting the gap.';
        $lines[] = '';
        $lines[] = 'Say briefly that you cannot look that up right now, then offer to pass the';
        $lines[] = 'visitor on: ask for their name, email and a short message, and confirm someone';
        $lines[] = 'will follow up. Never invent testimonials, case studies, client names or prices.';

        return implode( "\n", $lines );
    }
}
