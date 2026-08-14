<?php
/**
 * The aggregation layer: one cached document that answers "what is this site?"
 * plus the derived sitemap, FAQ set and llms.txt rendering.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Aggregator {

	const OPTION = 'vom_brain_overview';

	/**
	 * Cached overview, rebuilt on demand.
	 *
	 * @param bool $force Rebuild even when a cache exists.
	 * @return array
	 */
	public static function overview( $force = false ) {
		if ( ! $force ) {
			$cached = get_option( self::OPTION, null );
			if ( is_array( $cached ) && ! empty( $cached['generated_at'] ) ) {
				return $cached;
			}
		}
		return self::rebuild();
	}

	/**
	 * Drop the cache so the next read regenerates it.
	 */
	public static function invalidate() {
		delete_option( self::OPTION );
	}

	/**
	 * Recompute and store the aggregate.
	 *
	 * @return array
	 */
	public static function rebuild() {
		$stats = VOM_Brain_Index::stats();

		$overview = array(
			'generated_at'     => gmdate( 'c' ),
			'site'             => array(
				'name'        => get_bloginfo( 'name' ),
				'tagline'     => get_bloginfo( 'description' ),
				'url'         => home_url( '/' ),
				'language'    => get_bloginfo( 'language' ),
				'timezone'    => wp_timezone_string(),
				'platform'    => 'WordPress ' . get_bloginfo( 'version' ),
			),
			'business'         => VOM_Brain_Settings::business(),
			'what_i_know'      => array(
				'documents'         => $stats['documents'],
				'retrievable_passages' => $stats['passages'],
				'total_words'       => $stats['words'],
				'by_type'           => $stats['by_type'],
				'last_content_update' => $stats['last_content'],
			),
			'key_pages'        => self::key_pages(),
			'navigation'       => self::navigation(),
			'topics'           => self::topics(),
			'faqs'             => self::faqs(),
			'answer_guidelines'=> self::guidelines(),
			'endpoints'        => self::endpoints(),
		);

		$commerce = self::commerce();
		if ( $commerce ) {
			$overview['commerce'] = $commerce;
		}

		/**
		 * Filter the aggregated site overview before it is cached.
		 *
		 * @param array $overview Aggregate.
		 */
		$overview = apply_filters( 'vom_brain_overview', $overview );

		update_option( self::OPTION, $overview, false );
		return $overview;
	}

	/**
	 * The pages an agent should read first: front page, blog page, and the
	 * most substantial top-level pages.
	 *
	 * @return array
	 */
	public static function key_pages() {
		$out  = array();
		$seen = array();

		$front_id = (int) get_option( 'page_on_front' );
		$posts_id = (int) get_option( 'page_for_posts' );

		foreach ( array( $front_id => 'front page', $posts_id => 'blog index' ) as $id => $role ) {
			if ( $id > 0 ) {
				$doc = VOM_Brain_Index::get_document( array( 'id' => $id ) );
				if ( $doc ) {
					$out[]       = self::page_line( $doc, $role );
					$seen[ $id ] = true;
				}
			}
		}

		$pages = get_posts(
			array(
				'post_type'      => 'page',
				'post_status'    => 'publish',
				'posts_per_page' => 20,
				'post_parent'    => 0,
				'orderby'        => 'menu_order title',
				'order'          => 'ASC',
				'fields'         => 'ids',
			)
		);

		foreach ( (array) $pages as $id ) {
			$id = (int) $id;
			if ( isset( $seen[ $id ] ) ) {
				continue;
			}
			$doc = VOM_Brain_Index::get_document( array( 'id' => $id ) );
			if ( $doc ) {
				$out[]       = self::page_line( $doc, 'page' );
				$seen[ $id ] = true;
			}
		}

		return array_slice( $out, 0, 24 );
	}

	/**
	 * Compact representation of one page in the overview.
	 *
	 * @param array  $doc  Hydrated document.
	 * @param string $role Why it matters.
	 * @return array
	 */
	private static function page_line( $doc, $role ) {
		return array(
			'title'   => $doc['title'],
			'url'     => $doc['url'],
			'role'    => $role,
			'summary' => $doc['summary'],
		);
	}

	/**
	 * Primary menu structure, when the theme has one.
	 *
	 * @return array
	 */
	public static function navigation() {
		$out       = array();
		$locations = get_nav_menu_locations();
		if ( ! is_array( $locations ) ) {
			return $out;
		}

		foreach ( $locations as $location => $menu_id ) {
			if ( ! $menu_id ) {
				continue;
			}
			$items = wp_get_nav_menu_items( $menu_id );
			if ( ! $items ) {
				continue;
			}
			$links = array();
			foreach ( $items as $item ) {
				$links[] = array(
					'label' => $item->title,
					'url'   => $item->url,
				);
			}
			$out[ $location ] = array_slice( $links, 0, 30 );
			if ( count( $out ) >= 3 ) {
				break;
			}
		}
		return $out;
	}

	/**
	 * Most used terms across the enabled taxonomies — the site's subject map.
	 *
	 * @return array
	 */
	public static function topics() {
		$out = array();
		foreach ( (array) VOM_Brain_Settings::get( 'taxonomies', array() ) as $tax ) {
			if ( ! taxonomy_exists( $tax ) ) {
				continue;
			}
			$terms = get_terms(
				array(
					'taxonomy'   => $tax,
					'orderby'    => 'count',
					'order'      => 'DESC',
					'number'     => 20,
					'hide_empty' => true,
				)
			);
			if ( is_wp_error( $terms ) || ! $terms ) {
				continue;
			}
			$list = array();
			foreach ( $terms as $term ) {
				$list[] = array(
					'name'  => $term->name,
					'count' => (int) $term->count,
					'url'   => get_term_link( $term ),
				);
			}
			$out[ $tax ] = $list;
		}
		return $out;
	}

	/**
	 * Curated FAQs, plus anything from a post type that looks like an FAQ.
	 *
	 * @return array
	 */
	public static function faqs() {
		$out = VOM_Brain_Settings::faqs();

		foreach ( VOM_Brain_Settings::indexed_post_types( false ) as $type ) {
			if ( false === strpos( $type, 'faq' ) ) {
				continue;
			}
			$listing = VOM_Brain_Index::list_documents(
				array(
					'post_type' => $type,
					'per_page'  => 50,
					'scope'     => 'read',
				)
			);
			foreach ( $listing['items'] as $item ) {
				$doc = VOM_Brain_Index::get_document( array( 'id' => $item['id'] ) );
				if ( $doc ) {
					$out[] = array(
						'question' => $doc['title'],
						'answer'   => wp_trim_words( $doc['text'], 90, '…' ),
						'url'      => $doc['url'],
					);
				}
			}
		}

		return array_slice( $out, 0, 100 );
	}

	/**
	 * The persona and rules a chatbot should follow when speaking for this site.
	 *
	 * @return array
	 */
	public static function guidelines() {
		$biz    = VOM_Brain_Settings::business();
		$name   = isset( $biz['name'] ) ? $biz['name'] : get_bloginfo( 'name' );
		$custom = trim( (string) VOM_Brain_Settings::get( 'answer_guidelines', '' ) );
		$escal  = trim( (string) VOM_Brain_Settings::get( 'escalation', '' ) );

		$rules = array(
			sprintf( 'Answer as the assistant for %s, using only what this site actually states.', $name ),
			'Call search_site before answering anything factual, and cite the page URL you used.',
			'If the site does not cover it, say so plainly instead of guessing.',
			'Never invent prices, availability, opening hours or contact details.',
		);
		if ( $escal ) {
			$rules[] = $escal;
		}

		return array(
			'persona'     => $custom ? $custom : sprintf( 'A concise, helpful assistant speaking on behalf of %s.', $name ),
			'rules'       => $rules,
			'escalation'  => $escal,
			'source_note' => 'This brain is generated from the live website and refreshed whenever content changes.',
		);
	}

	/**
	 * WooCommerce roll-up, when the store is present and enabled.
	 *
	 * @return array
	 */
	public static function commerce() {
		if ( ! VOM_Brain_Settings::get( 'include_woo' ) || ! function_exists( 'wc_get_product' ) ) {
			return array();
		}
		if ( ! in_array( 'product', VOM_Brain_Settings::indexed_post_types(), true ) ) {
			return array();
		}

		global $wpdb;
		$docs = VOM_Brain_Index::table_docs();
		$rows = $wpdb->get_results( $wpdb->prepare( "SELECT meta FROM {$docs} WHERE post_type = %s AND visibility = 'public'", 'product' ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL

		$prices   = array();
		$currency = function_exists( 'get_woocommerce_currency' ) ? get_woocommerce_currency() : '';
		$in_stock = 0;

		foreach ( (array) $rows as $row ) {
			$meta = json_decode( (string) $row['meta'], true );
			if ( ! is_array( $meta ) ) {
				continue;
			}
			if ( isset( $meta['price'] ) && is_numeric( $meta['price'] ) ) {
				$prices[] = (float) $meta['price'];
			}
			if ( isset( $meta['stock_status'] ) && 'instock' === $meta['stock_status'] ) {
				$in_stock++;
			}
		}

		return array(
			'platform'         => 'WooCommerce',
			'products_indexed' => count( (array) $rows ),
			'in_stock'         => $in_stock,
			'currency'         => $currency,
			'price_range'      => $prices ? array( 'min' => min( $prices ), 'max' => max( $prices ) ) : null,
			'shop_url'         => function_exists( 'wc_get_page_permalink' ) ? wc_get_page_permalink( 'shop' ) : '',
		);
	}

	/**
	 * Every machine-readable surface this plugin publishes.
	 *
	 * @return array
	 */
	public static function endpoints() {
		return array(
			'mcp'        => rest_url( VOM_BRAIN_NS . '/mcp' ),
			'manifest'   => rest_url( VOM_BRAIN_NS . '/manifest' ),
			'overview'   => rest_url( VOM_BRAIN_NS . '/overview' ),
			'search'     => rest_url( VOM_BRAIN_NS . '/search' ),
			'documents'  => rest_url( VOM_BRAIN_NS . '/documents' ),
			'changes'    => rest_url( VOM_BRAIN_NS . '/changes' ),
			'sitemap'    => rest_url( VOM_BRAIN_NS . '/sitemap' ),
			'llms_txt'   => home_url( '/llms.txt' ),
			'llms_full'  => home_url( '/llms-full.txt' ),
			'discovery'  => home_url( '/.well-known/mcp.json' ),
		);
	}

	/**
	 * Flat URL list with last-modified stamps, for indexing agents.
	 *
	 * @param array $args { limit, offset, post_type, scope }.
	 * @return array
	 */
	public static function sitemap( $args = array() ) {
		$args = array_merge(
			array(
				'limit'     => 500,
				'offset'    => 0,
				'post_type' => '',
				'scope'     => 'read',
			),
			$args
		);

		$per_page = max( 1, min( 1000, (int) $args['limit'] ) );
		$page     = (int) floor( (int) $args['offset'] / $per_page ) + 1;

		$listing = VOM_Brain_Index::list_documents(
			array(
				'post_type' => $args['post_type'],
				'per_page'  => $per_page,
				'page'      => $page,
				'scope'     => $args['scope'],
			)
		);

		$urls = array();
		foreach ( $listing['items'] as $item ) {
			$urls[] = array(
				'url'        => $item['url'],
				'title'      => $item['title'],
				'type'       => $item['type'],
				'lastmod'    => $item['modified'],
				'word_count' => $item['word_count'],
				'hash'       => $item['hash'],
			);
		}

		return array(
			'total' => $listing['total'],
			'count' => count( $urls ),
			'urls'  => $urls,
		);
	}

	/**
	 * The llms.txt rendering — a compact, link-first briefing.
	 *
	 * @return string
	 */
	public static function llms_txt() {
		$o    = self::overview();
		$biz  = isset( $o['business'] ) ? $o['business'] : array();
		$name = isset( $biz['name'] ) ? $biz['name'] : $o['site']['name'];
		$out  = array();

		$out[] = '# ' . $name;
		$out[] = '';
		$desc  = '';
		if ( ! empty( $biz['description'] ) ) {
			$desc = $biz['description'];
		} elseif ( ! empty( $biz['tagline'] ) ) {
			$desc = $biz['tagline'];
		}
		if ( $desc ) {
			$out[] = '> ' . $desc;
			$out[] = '';
		}

		$facts = array();
		foreach ( array( 'industry', 'service_area', 'email', 'phone', 'booking_url' ) as $key ) {
			if ( ! empty( $biz[ $key ] ) ) {
				$facts[] = '- ' . ucwords( str_replace( '_', ' ', $key ) ) . ': ' . $biz[ $key ];
			}
		}
		if ( ! empty( $biz['opening_hours'] ) ) {
			foreach ( $biz['opening_hours'] as $day => $hours ) {
				$facts[] = '- Hours, ' . $day . ': ' . $hours;
			}
		}
		if ( $facts ) {
			$out[] = '## Facts';
			$out[] = '';
			$out   = array_merge( $out, $facts );
			$out[] = '';
		}

		if ( ! empty( $o['key_pages'] ) ) {
			$out[] = '## Key pages';
			$out[] = '';
			foreach ( $o['key_pages'] as $page ) {
				$line = '- [' . $page['title'] . '](' . $page['url'] . ')';
				if ( ! empty( $page['summary'] ) ) {
					$line .= ': ' . wp_trim_words( $page['summary'], 25, '…' );
				}
				$out[] = $line;
			}
			$out[] = '';
		}

		$recent = VOM_Brain_Index::list_documents(
			array(
				'per_page' => 40,
				'order_by' => 'modified',
				'scope'    => 'read',
			)
		);
		if ( $recent['items'] ) {
			$out[] = '## Recent content';
			$out[] = '';
			foreach ( $recent['items'] as $item ) {
				$out[] = '- [' . $item['title'] . '](' . $item['url'] . ') — ' . $item['type'];
			}
			$out[] = '';
		}

		if ( ! empty( $o['faqs'] ) ) {
			$out[] = '## FAQ';
			$out[] = '';
			foreach ( array_slice( $o['faqs'], 0, 30 ) as $faq ) {
				$out[] = '- **' . $faq['question'] . '** ' . $faq['answer'];
			}
			$out[] = '';
		}

		$out[] = '## For agents';
		$out[] = '';
		$out[] = 'This site publishes a Model Context Protocol server with live, structured access to all of the above.';
		$out[] = '';
		$out[] = '- MCP endpoint (Streamable HTTP): ' . $o['endpoints']['mcp'];
		$out[] = '- Discovery document: ' . $o['endpoints']['discovery'];
		$out[] = '- Full text of every page: ' . $o['endpoints']['llms_full'];
		$out[] = '- Incremental changes since a timestamp: ' . $o['endpoints']['changes'];
		$out[] = '';
		$out[] = 'Generated ' . $o['generated_at'] . ' by VOM Site Brain.';

		return implode( "\n", $out ) . "\n";
	}
}
