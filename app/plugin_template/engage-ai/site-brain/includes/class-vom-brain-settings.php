<?php
/**
 * Stored configuration for the Site Brain.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Settings {

	const OPTION = 'vom_brain_settings';

	/**
	 * Default configuration. Every key that exists at runtime is declared here.
	 *
	 * @return array
	 */
	public static function defaults() {
		return array(
			// Serving.
			'enabled'              => true,
			'public_read'          => true,   // Serve tools with no token at all.
			'rate_limit'           => 240,    // Anonymous calls per hour, per IP.
			'log_enabled'          => true,

			// What gets indexed.
			'post_types'           => array( 'post', 'page' ),
			'private_post_types'   => array(),  // Only visible to full-scope tokens.
			'taxonomies'           => array( 'category', 'post_tag' ),
			'exclude_ids'          => array(),
			'meta_keys'            => array(),  // Extra post meta to surface verbatim.
			'render_shortcodes'    => false,
			'include_woo'          => true,

			// Chunking.
			'chunk_words'          => 220,
			'chunk_overlap'        => 40,
			'batch_size'           => 25,
			'max_response_chars'   => 12000,

			// Business facts — the part a chatbot answers from.
			'biz_name'             => '',
			'biz_tagline'          => '',
			'biz_description'      => '',
			'biz_industry'         => '',
			'biz_email'            => '',
			'biz_phone'            => '',
			'biz_whatsapp'         => '',
			'biz_address'          => '',
			'biz_service_area'     => '',
			'biz_hours'            => '',
			'biz_booking_url'      => '',
			'biz_pricing_note'     => '',
			'biz_languages'        => '',
			'biz_socials'          => '',
			'answer_guidelines'    => '',
			'escalation'           => '',
			'faqs_raw'             => '',
		);
	}

	/**
	 * Full settings array, defaults merged in.
	 *
	 * @return array
	 */
	public static function all() {
		$stored = get_option( self::OPTION, array() );
		if ( ! is_array( $stored ) ) {
			$stored = array();
		}
		return array_merge( self::defaults(), $stored );
	}

	/**
	 * Read one setting.
	 *
	 * @param string $key     Setting key.
	 * @param mixed  $default Fallback when the key is unknown.
	 * @return mixed
	 */
	public static function get( $key, $default = null ) {
		$all = self::all();
		if ( array_key_exists( $key, $all ) ) {
			return $all[ $key ];
		}
		return $default;
	}

	/**
	 * Merge a partial array into the stored settings.
	 *
	 * @param array $changes Partial settings.
	 * @return array The new full settings array.
	 */
	public static function update( $changes ) {
		$new = array_merge( self::all(), (array) $changes );
		update_option( self::OPTION, $new, false );
		return $new;
	}

	/**
	 * Write defaults on first activation without clobbering an existing config.
	 */
	public static function seed() {
		$stored = get_option( self::OPTION, null );
		if ( null === $stored || ! is_array( $stored ) ) {
			$seed             = self::defaults();
			$seed['biz_name'] = get_bloginfo( 'name' );
			$seed['biz_tagline'] = get_bloginfo( 'description' );
			$seed['biz_email']   = get_option( 'admin_email' );
			update_option( self::OPTION, $seed, false );
		}
	}

	/**
	 * Post types eligible for indexing, filtered to ones that actually exist.
	 *
	 * @param bool $include_private Include the private-scope types.
	 * @return string[]
	 */
	public static function indexed_post_types( $include_private = true ) {
		$types = (array) self::get( 'post_types', array() );
		if ( $include_private ) {
			$types = array_merge( $types, (array) self::get( 'private_post_types', array() ) );
		}
		$types = array_values( array_unique( array_filter( $types ) ) );
		return array_values( array_filter( $types, 'post_type_exists' ) );
	}

	/**
	 * Is this post type only for authenticated (full-scope) agents?
	 *
	 * @param string $post_type Post type slug.
	 * @return bool
	 */
	public static function is_private_type( $post_type ) {
		return in_array( $post_type, (array) self::get( 'private_post_types', array() ), true );
	}

	/**
	 * Post types a site owner may reasonably expose, for the settings screen.
	 *
	 * @return array slug => label
	 */
	public static function selectable_post_types() {
		$out  = array();
		$objs = get_post_types( array( 'show_ui' => true ), 'objects' );
		foreach ( $objs as $slug => $obj ) {
			if ( in_array( $slug, array( 'attachment', 'revision', 'nav_menu_item', 'custom_css', 'customize_changeset', 'oembed_cache', 'user_request', 'wp_block', 'wp_template', 'wp_template_part', 'wp_global_styles', 'wp_navigation' ), true ) ) {
				continue;
			}
			$out[ $slug ] = isset( $obj->labels->name ) ? $obj->labels->name : $slug;
		}
		return $out;
	}

	/**
	 * Taxonomies a site owner may expose.
	 *
	 * @return array slug => label
	 */
	public static function selectable_taxonomies() {
		$out  = array();
		$objs = get_taxonomies( array( 'show_ui' => true ), 'objects' );
		foreach ( $objs as $slug => $obj ) {
			if ( in_array( $slug, array( 'nav_menu', 'link_category', 'post_format', 'wp_theme', 'wp_template_part_area', 'wp_pattern_category' ), true ) ) {
				continue;
			}
			$out[ $slug ] = isset( $obj->labels->name ) ? $obj->labels->name : $slug;
		}
		return $out;
	}

	/**
	 * The curated business facts, empty values stripped.
	 *
	 * @return array
	 */
	public static function business() {
		$s   = self::all();
		$out = array(
			'name'            => $s['biz_name'] ? $s['biz_name'] : get_bloginfo( 'name' ),
			'tagline'         => $s['biz_tagline'] ? $s['biz_tagline'] : get_bloginfo( 'description' ),
			'description'     => $s['biz_description'],
			'industry'        => $s['biz_industry'],
			'email'           => $s['biz_email'],
			'phone'           => $s['biz_phone'],
			'whatsapp'        => $s['biz_whatsapp'],
			'address'         => $s['biz_address'],
			'service_area'    => $s['biz_service_area'],
			'opening_hours'   => self::parse_lines( $s['biz_hours'] ),
			'booking_url'     => $s['biz_booking_url'],
			'pricing_note'    => $s['biz_pricing_note'],
			'languages'       => self::parse_list( $s['biz_languages'] ),
			'social_profiles' => self::parse_lines( $s['biz_socials'] ),
			'timezone'        => wp_timezone_string(),
		);

		foreach ( $out as $k => $v ) {
			if ( '' === $v || array() === $v ) {
				unset( $out[ $k ] );
			}
		}
		return $out;
	}

	/**
	 * "key: value" lines to an associative array.
	 *
	 * @param string $raw Raw textarea content.
	 * @return array
	 */
	public static function parse_lines( $raw ) {
		$out = array();
		foreach ( preg_split( '/\r\n|\r|\n/', (string) $raw ) as $line ) {
			$line = trim( $line );
			if ( '' === $line || false === strpos( $line, ':' ) ) {
				continue;
			}
			list( $k, $v ) = explode( ':', $line, 2 );
			$k             = trim( $k );
			$v             = trim( $v );
			if ( '' !== $k && '' !== $v ) {
				$out[ $k ] = $v;
			}
		}
		return $out;
	}

	/**
	 * Comma separated string to a clean list.
	 *
	 * @param string $raw Raw value.
	 * @return array
	 */
	public static function parse_list( $raw ) {
		$parts = array_map( 'trim', explode( ',', (string) $raw ) );
		return array_values( array_filter( $parts, 'strlen' ) );
	}

	/**
	 * Hand-written FAQs, parsed from the "Q: / A: / ---" textarea.
	 *
	 * @return array List of question/answer pairs.
	 */
	public static function faqs() {
		$raw = (string) self::get( 'faqs_raw', '' );
		if ( '' === trim( $raw ) ) {
			return array();
		}

		$out    = array();
		$blocks = preg_split( '/^\s*-{3,}\s*$/m', $raw );
		foreach ( $blocks as $block ) {
			$q = '';
			$a = array();
			foreach ( preg_split( '/\r\n|\r|\n/', $block ) as $line ) {
				$t = trim( $line );
				if ( '' === $t ) {
					continue;
				}
				if ( preg_match( '/^Q\s*[:.]\s*(.+)$/i', $t, $m ) ) {
					$q = trim( $m[1] );
				} elseif ( preg_match( '/^A\s*[:.]\s*(.+)$/i', $t, $m ) ) {
					$a[] = trim( $m[1] );
				} elseif ( $q ) {
					$a[] = $t;
				}
			}
			if ( '' !== $q && $a ) {
				$out[] = array(
					'question' => $q,
					'answer'   => implode( ' ', $a ),
				);
			}
		}
		return $out;
	}

	/**
	 * Sanitize a raw $_POST settings payload.
	 *
	 * Only the section that was actually submitted is touched. An unchecked
	 * checkbox is absent from the payload, so sanitizing every key on every
	 * save would silently clear the settings the other tab owns.
	 *
	 * @param array  $post    Raw request data.
	 * @param string $section Which form was submitted: 'content' or 'facts'.
	 * @return array Clean partial settings.
	 */
	public static function sanitize( $post, $section = '' ) {
		$clean = array();

		if ( 'content' === $section ) {
			$bools = array( 'enabled', 'public_read', 'log_enabled', 'render_shortcodes', 'include_woo' );
			foreach ( $bools as $key ) {
				$clean[ $key ] = ! empty( $post[ $key ] );
			}

			$ints = array(
				'rate_limit'         => array( 0, 100000 ),
				'chunk_words'        => array( 60, 1200 ),
				'chunk_overlap'      => array( 0, 400 ),
				'batch_size'         => array( 5, 200 ),
				'max_response_chars' => array( 1000, 200000 ),
			);
			foreach ( $ints as $key => $range ) {
				if ( isset( $post[ $key ] ) ) {
					$clean[ $key ] = max( $range[0], min( $range[1], (int) $post[ $key ] ) );
				}
			}

			$clean['post_types']         = self::clean_slugs( isset( $post['post_types'] ) ? $post['post_types'] : array() );
			$clean['private_post_types'] = self::clean_slugs( isset( $post['private_post_types'] ) ? $post['private_post_types'] : array() );
			$clean['taxonomies']         = self::clean_slugs( isset( $post['taxonomies'] ) ? $post['taxonomies'] : array() );

			$clean['exclude_ids'] = array();
			if ( isset( $post['exclude_ids'] ) ) {
				foreach ( preg_split( '/[\s,]+/', (string) $post['exclude_ids'] ) as $id ) {
					$id = (int) $id;
					if ( $id > 0 ) {
						$clean['exclude_ids'][] = $id;
					}
				}
			}

			$clean['meta_keys'] = array();
			if ( isset( $post['meta_keys'] ) ) {
				foreach ( preg_split( '/[\s,]+/', (string) $post['meta_keys'] ) as $key ) {
					$key = sanitize_key( $key );
					if ( '' !== $key ) {
						$clean['meta_keys'][] = $key;
					}
				}
			}

			return $clean;
		}

		if ( 'facts' === $section ) {
			$texts = array( 'biz_name', 'biz_tagline', 'biz_industry', 'biz_phone', 'biz_whatsapp', 'biz_service_area', 'biz_pricing_note', 'biz_languages' );
			foreach ( $texts as $key ) {
				if ( isset( $post[ $key ] ) ) {
					$clean[ $key ] = sanitize_text_field( wp_unslash( $post[ $key ] ) );
				}
			}

			$areas = array( 'biz_description', 'biz_address', 'biz_hours', 'biz_socials', 'answer_guidelines', 'escalation', 'faqs_raw' );
			foreach ( $areas as $key ) {
				if ( isset( $post[ $key ] ) ) {
					$clean[ $key ] = sanitize_textarea_field( wp_unslash( $post[ $key ] ) );
				}
			}

			if ( isset( $post['biz_email'] ) ) {
				$clean['biz_email'] = sanitize_email( wp_unslash( $post['biz_email'] ) );
			}
			if ( isset( $post['biz_booking_url'] ) ) {
				$clean['biz_booking_url'] = esc_url_raw( wp_unslash( $post['biz_booking_url'] ) );
			}

			return $clean;
		}

		return $clean;
	}

	/**
	 * Normalize an array of slugs.
	 *
	 * @param mixed $values Raw values.
	 * @return string[]
	 */
	private static function clean_slugs( $values ) {
		$out = array();
		foreach ( (array) $values as $v ) {
			$v = sanitize_key( $v );
			if ( '' !== $v ) {
				$out[] = $v;
			}
		}
		return array_values( array_unique( $out ) );
	}
}
