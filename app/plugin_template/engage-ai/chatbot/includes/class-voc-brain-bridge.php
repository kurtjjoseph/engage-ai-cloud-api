<?php
/**
 * VOC Brain Bridge — grounds the Vision Outreach chatbot in the VOM Site Brain index.
 *
 * The chatbot and the site brain live on the same WordPress install, so retrieval is an
 * in-process call: no HTTP, no token, no rate limit, and the index is always as fresh as
 * the last `save_post`. When the brain plugin is absent (staging, or the chatbot moved to
 * another host) the bridge falls back to the brain's public REST surface, and if that also
 * fails it returns an empty context so the chat handler can degrade to its old behaviour
 * instead of erroring.
 *
 * Usage in the /chat REST handler:
 *
 *     $ground = VOC_Brain_Bridge::ground( $messages, $language );
 *     $system = $ground['system'] ?: $legacy_system_prompt;
 *
 * @package VisionOutreachChatbot
 */

defined( 'ABSPATH' ) || exit;

class VOC_Brain_Bridge {

	/** Brain REST namespace, used when the classes are unavailable. */
	const REMOTE_NS = 'vom-mcp/v1';

	/** How many passages to retrieve per turn. */
	const PASSAGES = 6;

	/** Hard ceiling on the grounding block, in characters. */
	const MAX_CONTEXT_CHARS = 9000;

	/** @var array|null Per-request overview cache. */
	private static $overview = null;

	/**
	 * Is the brain reachable in-process?
	 *
	 * @return bool
	 */
	public static function local() {
		return class_exists( 'VOM_Brain_Index' )
			&& class_exists( 'VOM_Brain_Aggregator' )
			&& class_exists( 'VOM_Brain_Settings' )
			&& (bool) VOM_Brain_Settings::get( 'enabled', true );
	}

	/**
	 * Build the grounded system prompt for one chat turn.
	 *
	 * @param array  $messages Conversation so far: [ ['role'=>'user','content'=>'...'], ... ].
	 * @param string $language Two-letter language code the widget detected.
	 * @return array {
	 *     @type string $system    Full system prompt, or '' when nothing could be retrieved.
	 *     @type array  $sources   List of ['title','url'] actually put in front of the model.
	 *     @type string $query     The search query that was used.
	 *     @type int    $passages  How many passages were injected.
	 * }
	 */
	public static function ground( $messages, $language = 'en' ) {
		$query = self::query_from( $messages );
		if ( '' === $query ) {
			return self::empty_result( $query );
		}

		$passages = self::search( $query );
		$overview = self::overview();

		/**
		 * Filter the retrieved passages before they are rendered into the prompt.
		 *
		 * @param array  $passages Each: title, heading, url, passage, score.
		 * @param string $query    The search query used.
		 */
		$passages = apply_filters( 'voc_brain_passages', $passages, $query );

		if ( ! $passages && ! $overview ) {
			return self::empty_result( $query );
		}

		$sections = array_filter(
			array(
				self::render_persona( $overview, $language ),
				self::render_facts( $overview ),
				self::render_faqs( $overview, $query ),
				self::render_passages( $passages ),
				self::render_rules( $overview, $language ),
			)
		);

		$system = implode( "\n\n", $sections );

		$sources = array();
		foreach ( $passages as $p ) {
			$url = isset( $p['url'] ) ? $p['url'] : '';
			if ( $url && ! isset( $sources[ $url ] ) ) {
				$sources[ $url ] = array(
					'title' => isset( $p['title'] ) ? $p['title'] : $url,
					'url'   => $url,
				);
			}
		}

		/**
		 * Filter the final grounded system prompt.
		 *
		 * @param string $system   The assembled prompt.
		 * @param array  $passages Retrieved passages.
		 * @param string $query    Search query used.
		 */
		$system = apply_filters( 'voc_brain_system_prompt', $system, $passages, $query );

		return array(
			'system'   => $system,
			'sources'  => array_values( $sources ),
			'query'    => $query,
			'passages' => count( $passages ),
		);
	}

	/**
	 * The same retrieval, returned as structured parts instead of a prompt.
	 *
	 * The Engage AI Cloud API owns the answering protocol — a site sends what it
	 * knows, not the rules it wants applied — so the cloud provider needs the
	 * pieces rather than the assembled system message.
	 *
	 * @param array  $messages Conversation so far.
	 * @param string $language Two-letter language code.
	 * @return array persona, facts, faqs, passages, escalation.
	 */
	public static function grounding( $messages, $language = 'en' ) {
		$query = self::query_from( $messages );
		if ( '' === $query ) {
			return array();
		}

		$overview = self::overview();
		$passages = apply_filters( 'voc_brain_passages', self::search( $query ), $query );

		if ( ! $passages && ! $overview ) {
			return array();
		}

		$g = isset( $overview['answer_guidelines'] ) ? $overview['answer_guidelines'] : array();

		$facts = array();
		$biz   = isset( $overview['business'] ) ? $overview['business'] : array();
		$labels = array(
			'name'         => 'Name',
			'tagline'      => 'Tagline',
			'description'  => 'What we do',
			'email'        => 'Email',
			'phone'        => 'Phone',
			'whatsapp'     => 'WhatsApp',
			'address'      => 'Address',
			'service_area' => 'Service area',
			'booking_url'  => 'Booking',
			'pricing_note' => 'Pricing policy',
		);
		foreach ( $labels as $key => $label ) {
			if ( ! empty( $biz[ $key ] ) && is_scalar( $biz[ $key ] ) ) {
				$facts[ $label ] = (string) $biz[ $key ];
			}
		}
		if ( ! empty( $biz['languages'] ) && is_array( $biz['languages'] ) ) {
			$facts['Languages'] = implode( ', ', $biz['languages'] );
		}
		if ( ! empty( $biz['opening_hours'] ) && is_array( $biz['opening_hours'] ) ) {
			$hours = array();
			foreach ( $biz['opening_hours'] as $day => $span ) {
				$hours[] = $day . ' ' . $span;
			}
			$facts['Opening hours'] = implode( '; ', $hours );
		}

		$faqs = array();
		foreach ( (array) ( isset( $overview['faqs'] ) ? $overview['faqs'] : array() ) as $faq ) {
			if ( ! empty( $faq['question'] ) && ! empty( $faq['answer'] ) ) {
				$faqs[] = array(
					'question' => (string) $faq['question'],
					'answer'   => (string) $faq['answer'],
				);
			}
		}

		$out = array();
		$used = 0;
		foreach ( $passages as $p ) {
			$text = isset( $p['passage'] ) ? trim( (string) $p['passage'] ) : '';
			if ( '' === $text ) {
				continue;
			}
			if ( $used + strlen( $text ) > self::MAX_CONTEXT_CHARS && $out ) {
				break;
			}
			$used += strlen( $text );
			$out[] = array(
				'title'   => isset( $p['title'] ) ? (string) $p['title'] : '',
				'heading' => isset( $p['heading'] ) ? (string) $p['heading'] : '',
				'url'     => isset( $p['url'] ) ? (string) $p['url'] : '',
				'passage' => $text,
			);
		}

		return array(
			'persona'    => isset( $g['persona'] ) ? (string) $g['persona'] : '',
			'facts'      => $facts,
			'faqs'       => array_slice( $faqs, 0, 12 ),
			'passages'   => $out,
			'escalation' => isset( $g['escalation'] ) ? (string) $g['escalation'] : '',
		);
	}

	// ---------------------------------------------------------------- retrieval

	/**
	 * Retrieve passages, in-process when possible, over REST otherwise.
	 *
	 * @param string $query Search text.
	 * @return array
	 */
	public static function search( $query ) {
		$query = self::expand( $query );

		if ( self::local() ) {
			$rows = VOM_Brain_Index::search(
				$query,
				array(
					'limit'       => self::PASSAGES,
					'scope'       => 'read',
					'max_per_doc' => 2,
				)
			);
			return is_array( $rows ) ? $rows : array();
		}
		return self::remote( '/search', array( 'q' => $query, 'limit' => self::PASSAGES ), 'results' );
	}

	/**
	 * Widen a visitor's wording to the words the site actually uses.
	 *
	 * The brain searches MySQL FULLTEXT in boolean mode with a trailing wildcard, which does
	 * no stemming and no synonyms: "price" becomes `price*`, which does not match "Pricing",
	 * and "cost" matches nothing at all — so the single most common question on the site
	 * ("what does it cost?") retrieves zero passages. Each matched trigger appends the site's
	 * own vocabulary to the query; the original wording is always kept.
	 *
	 * @param string $query Raw question.
	 * @return string Query used for retrieval only — never shown to the model.
	 */
	public static function expand( $query ) {
		/**
		 * Filter the retrieval synonym map: trigger word => extra tokens to append.
		 *
		 * @param array $map Trigger => appended vocabulary.
		 */
		$map = apply_filters(
			'voc_brain_synonyms',
			array(
				// Pricing, EN + NL.
				'cost'        => 'pricing monthly billed support',
				'costs'       => 'pricing monthly billed support',
				'price'       => 'pricing monthly billed support',
				'prices'      => 'pricing monthly billed support',
				'pricing'     => 'monthly billed support',
				'fee'         => 'pricing monthly billed',
				'fees'        => 'pricing monthly billed',
				'rate'        => 'pricing monthly billed',
				'rates'       => 'pricing monthly billed',
				'quote'       => 'pricing quoted monthly',
				'budget'      => 'pricing monthly billed',
				'expensive'   => 'pricing monthly billed',
				'afford'      => 'pricing monthly billed',
				'kost'        => 'pricing monthly billed support',
				'kosten'      => 'pricing monthly billed support',
				'prijs'       => 'pricing monthly billed support',
				'prijzen'     => 'pricing monthly billed support',
				'tarief'      => 'pricing monthly billed',
				'tarieven'    => 'pricing monthly billed',
				'offerte'     => 'pricing quoted monthly',
				// Getting in touch.
				'contact'     => 'contact email phone introduction call',
				'reach'       => 'contact email phone introduction call',
				'call'        => 'introduction call contact schedule',
				'email'       => 'contact email',
				'phone'       => 'contact phone',
				'appointment' => 'introduction call schedule contact',
				'afspraak'    => 'introduction call schedule contact',
				'bellen'      => 'introduction call contact phone',
			)
		);

		$extra = array();
		foreach ( preg_split( '/\W+/u', mb_strtolower( (string) $query ) ) as $word ) {
			if ( isset( $map[ $word ] ) ) {
				foreach ( explode( ' ', $map[ $word ] ) as $token ) {
					$extra[ $token ] = true;
				}
			}
		}

		return $extra ? $query . ' ' . implode( ' ', array_keys( $extra ) ) : $query;
	}

	/**
	 * The cached "what is this site" document.
	 *
	 * @return array
	 */
	public static function overview() {
		if ( null !== self::$overview ) {
			return self::$overview;
		}
		if ( self::local() ) {
			$o = VOM_Brain_Aggregator::overview();
			self::$overview = is_array( $o ) ? $o : array();
		} else {
			self::$overview = self::remote( '/overview', array(), null );
		}
		return self::$overview;
	}

	/**
	 * GET the brain's public REST surface.
	 *
	 * @param string      $path  Route path, e.g. '/search'.
	 * @param array       $args  Query args.
	 * @param string|null $key   Sub-key to return from the payload, or null for the whole body.
	 * @return array
	 */
	private static function remote( $path, $args = array(), $key = null ) {
		// This site's own brain by default. The chatbot and the brain normally
		// live on the same install, so the fallback is a loopback call, not a
		// call to somebody else's website.
		$ns   = defined( 'VOM_BRAIN_NS' ) ? VOM_BRAIN_NS : self::REMOTE_NS;
		$base = apply_filters( 'voc_brain_remote_base', rtrim( rest_url( $ns ), '/' ) );
		$url  = add_query_arg( $args, $base . $path );

		$res = wp_remote_get( $url, array( 'timeout' => 6 ) );
		if ( is_wp_error( $res ) || 200 !== (int) wp_remote_retrieve_response_code( $res ) ) {
			return array();
		}
		$body = json_decode( wp_remote_retrieve_body( $res ), true );
		if ( ! is_array( $body ) ) {
			return array();
		}
		if ( null === $key ) {
			return $body;
		}
		return isset( $body[ $key ] ) && is_array( $body[ $key ] ) ? $body[ $key ] : array();
	}

	// ---------------------------------------------------------------- prompt rendering

	/**
	 * Persona line, taken from the brain's own answer guidelines so the owner edits it in
	 * one place instead of two.
	 *
	 * @param array  $overview Brain overview.
	 * @param string $language Detected language code.
	 * @return string
	 */
	private static function render_persona( $overview, $language ) {
		$g       = isset( $overview['answer_guidelines'] ) ? $overview['answer_guidelines'] : array();
		$persona = isset( $g['persona'] ) ? trim( $g['persona'] ) : '';
		$name    = self::site_name( $overview );

		if ( '' === $persona ) {
			$persona = sprintf( 'A concise, helpful assistant speaking on behalf of %s.', $name );
		}

		$lines = array(
			'# Role',
			$persona,
			sprintf( 'You are the assistant on the %s website. Reply in %s.', $name, self::language_name( $language ) ),
		);
		return implode( "\n", $lines );
	}

	/**
	 * Owner-verified business facts — the things the model must never improvise.
	 *
	 * @param array $overview Brain overview.
	 * @return string
	 */
	private static function render_facts( $overview ) {
		$biz = isset( $overview['business'] ) ? $overview['business'] : array();
		if ( ! is_array( $biz ) || ! $biz ) {
			return '';
		}

		$out = array( '# Verified business facts (authoritative — never contradict or embellish these)' );

		$scalars = array(
			'name'         => 'Name',
			'tagline'      => 'Tagline',
			'description'  => 'What we do',
			'email'        => 'Email',
			'phone'        => 'Phone',
			'whatsapp'     => 'WhatsApp',
			'address'      => 'Address',
			'service_area' => 'Service area',
			'booking_url'  => 'Booking',
			'pricing_note' => 'Pricing policy',
		);
		foreach ( $scalars as $key => $label ) {
			if ( ! empty( $biz[ $key ] ) && is_scalar( $biz[ $key ] ) ) {
				$out[] = sprintf( '- %s: %s', $label, $biz[ $key ] );
			}
		}
		if ( ! empty( $biz['languages'] ) && is_array( $biz['languages'] ) ) {
			$out[] = '- Languages: ' . implode( ', ', $biz['languages'] );
		}
		if ( ! empty( $biz['opening_hours'] ) && is_array( $biz['opening_hours'] ) ) {
			$hours = array();
			foreach ( $biz['opening_hours'] as $day => $span ) {
				$hours[] = $day . ' ' . $span;
			}
			$out[] = '- Opening hours: ' . implode( '; ', $hours );
		}

		return count( $out ) > 1 ? implode( "\n", $out ) : '';
	}

	/**
	 * Curated FAQs, narrowed to the ones that share words with the question.
	 *
	 * @param array  $overview Brain overview.
	 * @param string $query    Search text.
	 * @return string
	 */
	private static function render_faqs( $overview, $query ) {
		$faqs = isset( $overview['faqs'] ) ? $overview['faqs'] : array();
		if ( ! is_array( $faqs ) || ! $faqs ) {
			return '';
		}

		$terms = array_filter(
			preg_split( '/\W+/u', mb_strtolower( $query ) ),
			function ( $t ) {
				return mb_strlen( $t ) > 3;
			}
		);

		$hits = array();
		foreach ( $faqs as $faq ) {
			if ( empty( $faq['question'] ) || empty( $faq['answer'] ) ) {
				continue;
			}
			$hay = mb_strtolower( $faq['question'] . ' ' . $faq['answer'] );
			foreach ( $terms as $t ) {
				if ( false !== mb_strpos( $hay, $t ) ) {
					$hits[] = sprintf( '- Q: %s' . "\n" . '  A: %s', $faq['question'], $faq['answer'] );
					break;
				}
			}
			if ( count( $hits ) >= 3 ) {
				break;
			}
		}

		return $hits ? "# Relevant FAQs (owner-written)\n" . implode( "\n", $hits ) : '';
	}

	/**
	 * The retrieved site passages, numbered so the model can cite them by URL.
	 *
	 * @param array $passages Retrieved passages.
	 * @return string
	 */
	private static function render_passages( $passages ) {
		if ( ! $passages ) {
			return "# Site passages\nNo passage on this website matched the question.";
		}

		$out   = array( '# Site passages (the only source of truth for factual claims)' );
		$used  = 0;
		$index = 0;

		foreach ( $passages as $p ) {
			$title   = isset( $p['title'] ) ? $p['title'] : '';
			$heading = isset( $p['heading'] ) ? $p['heading'] : '';
			$url     = isset( $p['url'] ) ? $p['url'] : '';
			$text    = isset( $p['passage'] ) ? trim( (string) $p['passage'] ) : '';
			if ( '' === $text ) {
				continue;
			}

			$label = $title . ( $heading ? ' — ' . $heading : '' );
			$block = sprintf( "[%d] %s\nURL: %s\n%s", ++$index, $label, $url, $text );

			if ( $used + strlen( $block ) > self::MAX_CONTEXT_CHARS && $index > 1 ) {
				break;
			}
			$used += strlen( $block );
			$out[] = $block;
		}

		return implode( "\n\n", $out );
	}

	/**
	 * Grounding rules. The brain's own rule list is reused verbatim, minus the one that
	 * tells an MCP client to call search_site — retrieval has already happened here.
	 *
	 * @param array  $overview Brain overview.
	 * @param string $language Detected language code.
	 * @return string
	 */
	private static function render_rules( $overview, $language ) {
		$g     = isset( $overview['answer_guidelines'] ) ? $overview['answer_guidelines'] : array();
		$rules = array();

		foreach ( (array) ( isset( $g['rules'] ) ? $g['rules'] : array() ) as $rule ) {
			if ( false !== stripos( $rule, 'search_site' ) ) {
				continue;
			}
			$rules[] = $rule;
		}

		$rules[] = 'Use only the verified facts, FAQs and site passages above. They are the whole of what you know.';
		$rules[] = 'When you state a fact, link the page it came from as a plain URL from the passage list.';
		$rules[] = 'If the passages do not answer the question, say so plainly, then offer the contact page or a free introduction call. Never guess and never fill a gap from general knowledge.';
		$rules[] = 'Never invent prices, availability, opening hours, contact details or client names.';
		$rules[] = 'Keep replies short — two or three sentences unless the visitor asks for detail.';
		$rules[] = sprintf( 'Reply in %s regardless of the language of the passages.', self::language_name( $language ) );

		return "# Rules\n- " . implode( "\n- ", array_unique( $rules ) );
	}

	// ---------------------------------------------------------------- helpers

	/**
	 * Build the retrieval query from the conversation.
	 *
	 * The last user turn carries the question, but a follow-up like "and the price?" has no
	 * searchable nouns on its own, so the previous user turn is prepended when the latest one
	 * is short.
	 *
	 * @param array $messages Conversation.
	 * @return string
	 */
	public static function query_from( $messages ) {
		$user = array();
		foreach ( (array) $messages as $m ) {
			if ( isset( $m['role'], $m['content'] ) && 'user' === $m['role'] && is_string( $m['content'] ) ) {
				$user[] = trim( $m['content'] );
			}
		}
		if ( ! $user ) {
			return '';
		}

		$last = array_pop( $user );
		if ( mb_strlen( $last ) < 25 && $user ) {
			$last = array_pop( $user ) . ' ' . $last;
		}
		return mb_substr( trim( $last ), 0, 400 );
	}

	/**
	 * Site name from the overview, with sensible fallbacks.
	 *
	 * @param array $overview Brain overview.
	 * @return string
	 */
	private static function site_name( $overview ) {
		if ( ! empty( $overview['business']['name'] ) ) {
			return $overview['business']['name'];
		}
		if ( ! empty( $overview['site']['name'] ) ) {
			return $overview['site']['name'];
		}
		return 'Vision Outreach Media';
	}

	/**
	 * Human-readable language name for the prompt.
	 *
	 * @param string $code Two-letter code.
	 * @return string
	 */
	private static function language_name( $code ) {
		$map = array(
			'en' => 'English',
			'nl' => 'Dutch',
			'fr' => 'French',
			'de' => 'German',
			'es' => 'Spanish',
			'pt' => 'Portuguese',
			'it' => 'Italian',
		);
		$code = strtolower( substr( (string) $code, 0, 2 ) );
		return isset( $map[ $code ] ) ? $map[ $code ] : 'English';
	}

	/**
	 * Uniform empty return.
	 *
	 * @param string $query Query that produced nothing.
	 * @return array
	 */
	private static function empty_result( $query ) {
		return array(
			'system'   => '',
			'sources'  => array(),
			'query'    => $query,
			'passages' => 0,
		);
	}
}
