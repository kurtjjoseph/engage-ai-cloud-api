<?php
/**
 * The index: schema, crawling, chunking, search and retrieval.
 *
 * Two tables. `docs` holds one row per indexed object with its sections and
 * metadata; `chunks` holds retrieval-sized passages with a FULLTEXT index.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Index {

	const STATE      = 'vom_brain_index_state';
	const QUEUE      = 'vom_brain_queue';
	const TOMBSTONES = 'vom_brain_tombstones';
	const FT_FLAG    = 'vom_brain_has_fulltext';

	/**
	 * Documents table name.
	 *
	 * @return string
	 */
	public static function table_docs() {
		global $wpdb;
		return $wpdb->prefix . 'vom_brain_docs';
	}

	/**
	 * Chunks table name.
	 *
	 * @return string
	 */
	public static function table_chunks() {
		global $wpdb;
		return $wpdb->prefix . 'vom_brain_chunks';
	}

	/**
	 * Create or migrate the schema.
	 */
	public static function install() {
		global $wpdb;
		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$charset = $wpdb->get_charset_collate();
		$docs    = self::table_docs();
		$chunks  = self::table_chunks();

		$sql_docs = "CREATE TABLE {$docs} (
			doc_id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			object_type varchar(20) NOT NULL DEFAULT 'post',
			object_id bigint(20) unsigned NOT NULL DEFAULT 0,
			post_type varchar(32) NOT NULL DEFAULT '',
			visibility varchar(10) NOT NULL DEFAULT 'public',
			title text NULL,
			slug varchar(200) NOT NULL DEFAULT '',
			url text NULL,
			excerpt text NULL,
			sections longtext NULL,
			taxonomies longtext NULL,
			meta longtext NULL,
			word_count int(10) unsigned NOT NULL DEFAULT 0,
			content_hash char(32) NOT NULL DEFAULT '',
			modified datetime NULL DEFAULT NULL,
			indexed_at datetime NULL DEFAULT NULL,
			PRIMARY KEY  (doc_id),
			UNIQUE KEY object (object_type,object_id),
			KEY post_type (post_type),
			KEY visibility (visibility),
			KEY modified (modified)
		) {$charset};";

		$sql_chunks = "CREATE TABLE {$chunks} (
			chunk_id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			doc_id bigint(20) unsigned NOT NULL DEFAULT 0,
			object_id bigint(20) unsigned NOT NULL DEFAULT 0,
			post_type varchar(32) NOT NULL DEFAULT '',
			visibility varchar(10) NOT NULL DEFAULT 'public',
			seq int(10) unsigned NOT NULL DEFAULT 0,
			title text NULL,
			url text NULL,
			heading text NULL,
			content longtext NULL,
			word_count int(10) unsigned NOT NULL DEFAULT 0,
			PRIMARY KEY  (chunk_id),
			KEY doc_id (doc_id),
			KEY post_type (post_type),
			KEY visibility (visibility),
			FULLTEXT KEY brain_ft (heading,content)
		) {$charset};";

		dbDelta( $sql_docs );
		dbDelta( $sql_chunks );

		// Remember whether the FULLTEXT index actually exists on this host.
		$indexes = $wpdb->get_results( "SHOW INDEX FROM {$chunks}" ); // phpcs:ignore WordPress.DB.PreparedSQL
		$has_ft  = false;
		if ( is_array( $indexes ) ) {
			foreach ( $indexes as $row ) {
				if ( isset( $row->Key_name ) && 'brain_ft' === $row->Key_name ) { // phpcs:ignore WordPress.NamingConventions.ValidVariableName
					$has_ft = true;
					break;
				}
			}
		}
		update_option( self::FT_FLAG, $has_ft ? 1 : 0, false );
	}

	/**
	 * Register runtime hooks.
	 */
	public static function hooks() {
		add_action( 'save_post', array( __CLASS__, 'on_save' ), 20, 3 );
		add_action( 'deleted_post', array( __CLASS__, 'on_delete' ) );
		add_action( 'trashed_post', array( __CLASS__, 'on_delete' ) );
		add_action( 'vom_brain_build_batch', array( __CLASS__, 'run_batch' ) );
		add_action( 'vom_brain_daily', array( __CLASS__, 'daily' ) );
	}

	/* --------------------------------------------------------------------- */
	/* Build queue                                                            */
	/* --------------------------------------------------------------------- */

	/**
	 * Daily maintenance: full re-crawl plus a fresh aggregate.
	 */
	public static function daily() {
		self::queue_full_build();
		VOM_Brain_Aggregator::rebuild();
	}

	/**
	 * Collect every eligible object id and schedule the first batch.
	 *
	 * @return int Number of objects queued.
	 */
	public static function queue_full_build() {
		global $wpdb;

		$types = VOM_Brain_Settings::indexed_post_types();
		if ( ! $types ) {
			update_option( self::QUEUE, array(), false );
			self::set_state( array( 'status' => 'idle', 'queued' => 0, 'done' => 0, 'total' => 0 ) );
			return 0;
		}

		$placeholders = implode( ',', array_fill( 0, count( $types ), '%s' ) );
		$sql          = "SELECT ID FROM {$wpdb->posts} WHERE post_status = 'publish' AND post_type IN ({$placeholders}) ORDER BY post_modified DESC";
		$ids          = $wpdb->get_col( $wpdb->prepare( $sql, $types ) ); // phpcs:ignore WordPress.DB.PreparedSQL

		$exclude = array_map( 'intval', (array) VOM_Brain_Settings::get( 'exclude_ids', array() ) );
		$ids     = array_values( array_diff( array_map( 'intval', (array) $ids ), $exclude ) );

		update_option( self::QUEUE, $ids, false );
		self::set_state(
			array(
				'status' => $ids ? 'building' : 'idle',
				'total'  => count( $ids ),
				'done'   => 0,
				'queued' => count( $ids ),
				'started'=> gmdate( 'c' ),
			)
		);

		if ( $ids && ! wp_next_scheduled( 'vom_brain_build_batch' ) ) {
			wp_schedule_single_event( time() + 5, 'vom_brain_build_batch' );
		}

		return count( $ids );
	}

	/**
	 * Index one slice of the queue, then reschedule if work remains.
	 *
	 * @return int Number of documents processed in this batch.
	 */
	public static function run_batch() {
		$queue = get_option( self::QUEUE, array() );
		if ( ! is_array( $queue ) || ! $queue ) {
			self::finish_build();
			return 0;
		}

		$size  = (int) VOM_Brain_Settings::get( 'batch_size', 25 );
		$slice = array_splice( $queue, 0, max( 1, $size ) );
		update_option( self::QUEUE, $queue, false );

		$done = 0;
		foreach ( $slice as $id ) {
			$post = get_post( (int) $id );
			if ( $post && self::index_post( $post ) ) {
				$done++;
			}
		}

		$state          = self::state();
		$state['done']  = (int) ( isset( $state['done'] ) ? $state['done'] : 0 ) + count( $slice );
		$state['queued'] = count( $queue );
		self::set_state( $state );

		if ( $queue ) {
			wp_schedule_single_event( time() + 5, 'vom_brain_build_batch' );
		} else {
			self::finish_build();
		}

		return $done;
	}

	/**
	 * Mark the crawl complete and refresh the aggregate view.
	 */
	private static function finish_build() {
		$stats = self::stats();
		self::set_state(
			array(
				'status'      => 'idle',
				'queued'      => 0,
				'total'       => (int) $stats['documents'],
				'done'        => (int) $stats['documents'],
				'last_built'  => gmdate( 'c' ),
			)
		);
		VOM_Brain_Aggregator::rebuild();
	}

	/**
	 * Current build state.
	 *
	 * @return array
	 */
	public static function state() {
		$s = get_option( self::STATE, array() );
		return is_array( $s ) ? $s : array();
	}

	/**
	 * Persist build state.
	 *
	 * @param array $state Partial or full state.
	 */
	public static function set_state( $state ) {
		update_option( self::STATE, array_merge( self::state(), (array) $state ), false );
	}

	/* --------------------------------------------------------------------- */
	/* Incremental updates                                                    */
	/* --------------------------------------------------------------------- */

	/**
	 * Reindex on save.
	 *
	 * @param int     $post_id Post id.
	 * @param WP_Post $post    Post object.
	 * @param bool    $update  Whether this is an update.
	 */
	public static function on_save( $post_id, $post, $update = false ) {
		unset( $update );
		if ( wp_is_post_revision( $post_id ) || wp_is_post_autosave( $post_id ) ) {
			return;
		}
		if ( ! $post instanceof WP_Post ) {
			$post = get_post( $post_id );
		}
		if ( ! $post ) {
			return;
		}
		if ( 'publish' !== $post->post_status ) {
			self::remove_object( $post_id, $post );
			return;
		}
		self::index_post( $post );
		VOM_Brain_Aggregator::invalidate();
	}

	/**
	 * Drop an object on delete or trash.
	 *
	 * @param int $post_id Post id.
	 */
	public static function on_delete( $post_id ) {
		self::remove_object( (int) $post_id, get_post( $post_id ) );
		VOM_Brain_Aggregator::invalidate();
	}

	/**
	 * Remove a document and leave a tombstone so incremental crawlers notice.
	 *
	 * @param int          $object_id Object id.
	 * @param WP_Post|null $post      Post, when still available.
	 */
	public static function remove_object( $object_id, $post = null ) {
		global $wpdb;

		$docs   = self::table_docs();
		$chunks = self::table_chunks();

		$doc_id = $wpdb->get_var( $wpdb->prepare( "SELECT doc_id FROM {$docs} WHERE object_type = 'post' AND object_id = %d", $object_id ) ); // phpcs:ignore WordPress.DB.PreparedSQL
		if ( ! $doc_id ) {
			return;
		}

		$wpdb->delete( $chunks, array( 'doc_id' => $doc_id ), array( '%d' ) );
		$wpdb->delete( $docs, array( 'doc_id' => $doc_id ), array( '%d' ) );

		$stones = get_option( self::TOMBSTONES, array() );
		if ( ! is_array( $stones ) ) {
			$stones = array();
		}
		array_unshift(
			$stones,
			array(
				'object_id'  => (int) $object_id,
				'title'      => $post ? $post->post_title : '',
				'url'        => $post ? get_permalink( $post ) : '',
				'removed_at' => gmdate( 'c' ),
			)
		);
		update_option( self::TOMBSTONES, array_slice( $stones, 0, 500 ), false );
	}

	/* --------------------------------------------------------------------- */
	/* Indexing one post                                                      */
	/* --------------------------------------------------------------------- */

	/**
	 * Index a single post: extract, chunk, upsert.
	 *
	 * @param WP_Post $post Post to index.
	 * @return bool True when a row was written.
	 */
	public static function index_post( $post ) {
		global $wpdb;

		if ( ! $post instanceof WP_Post ) {
			return false;
		}
		if ( ! in_array( $post->post_type, VOM_Brain_Settings::indexed_post_types(), true ) ) {
			return false;
		}
		if ( 'publish' !== $post->post_status ) {
			return false;
		}
		if ( '' !== $post->post_password ) {
			return false;
		}
		if ( in_array( (int) $post->ID, array_map( 'intval', (array) VOM_Brain_Settings::get( 'exclude_ids', array() ) ), true ) ) {
			return false;
		}

		$sections = self::extract_sections( $post );
		$plain    = self::sections_to_text( $sections );
		$words    = self::word_count( $plain );

		$visibility = VOM_Brain_Settings::is_private_type( $post->post_type ) ? 'private' : 'public';

		/**
		 * Filter a single document's visibility.
		 *
		 * Post types are public or private as a whole, which is right for pages
		 * but not for a knowledge base, where one uploaded document may be a
		 * product manual and the next a staff handbook.
		 *
		 * @param string  $visibility 'public' or 'private'.
		 * @param WP_Post $post       Post being indexed.
		 */
		$visibility = apply_filters( 'vom_brain_document_visibility', $visibility, $post );
		$visibility = ( 'private' === $visibility ) ? 'private' : 'public';
		$url        = get_permalink( $post );
		$excerpt    = self::build_excerpt( $post, $plain );

		$row = array(
			'object_type' => 'post',
			'object_id'   => (int) $post->ID,
			'post_type'   => $post->post_type,
			'visibility'  => $visibility,
			'title'       => $post->post_title,
			'slug'        => $post->post_name,
			'url'         => $url ? $url : '',
			'excerpt'     => $excerpt,
			'sections'    => wp_json_encode( $sections ),
			'taxonomies'  => wp_json_encode( self::collect_taxonomies( $post ) ),
			'meta'        => wp_json_encode( self::collect_meta( $post ) ),
			'word_count'  => $words,
			'content_hash'=> md5( $post->post_title . '|' . $plain ),
			'modified'    => get_post_modified_time( 'Y-m-d H:i:s', true, $post ),
			'indexed_at'  => gmdate( 'Y-m-d H:i:s' ),
		);

		$docs   = self::table_docs();
		$chunks = self::table_chunks();

		$existing = $wpdb->get_row( $wpdb->prepare( "SELECT doc_id, content_hash FROM {$docs} WHERE object_type = 'post' AND object_id = %d", $post->ID ) ); // phpcs:ignore WordPress.DB.PreparedSQL

		if ( $existing ) {
			$doc_id = (int) $existing->doc_id;
			$wpdb->update( $docs, $row, array( 'doc_id' => $doc_id ), null, array( '%d' ) );
			if ( $existing->content_hash === $row['content_hash'] ) {
				// Text is unchanged; chunks stay valid.
				return true;
			}
			$wpdb->delete( $chunks, array( 'doc_id' => $doc_id ), array( '%d' ) );
		} else {
			$wpdb->insert( $docs, $row );
			$doc_id = (int) $wpdb->insert_id;
		}

		if ( ! $doc_id ) {
			return false;
		}

		$pieces = self::chunk_sections(
			$sections,
			(int) VOM_Brain_Settings::get( 'chunk_words', 220 ),
			(int) VOM_Brain_Settings::get( 'chunk_overlap', 40 )
		);

		$seq = 0;
		foreach ( $pieces as $piece ) {
			$wpdb->insert(
				$chunks,
				array(
					'doc_id'     => $doc_id,
					'object_id'  => (int) $post->ID,
					'post_type'  => $post->post_type,
					'visibility' => $visibility,
					'seq'        => $seq,
					'title'      => $post->post_title,
					'url'        => $row['url'],
					'heading'    => $piece['heading'],
					'content'    => $piece['text'],
					'word_count' => self::word_count( $piece['text'] ),
				)
			);
			$seq++;
		}

		return true;
	}

	/**
	 * Turn post content into heading-delimited sections of plain text.
	 *
	 * @param WP_Post $post Post.
	 * @return array List of { heading, text }.
	 */
	public static function extract_sections( $post ) {
		$html = (string) $post->post_content;

		if ( function_exists( 'do_blocks' ) && has_blocks( $html ) ) {
			$html = do_blocks( $html );
		}
		if ( VOM_Brain_Settings::get( 'render_shortcodes' ) ) {
			$html = do_shortcode( $html );
		} else {
			$html = strip_shortcodes( $html );
		}

		$html = preg_replace( '#<(script|style|noscript)\b[^>]*>.*?</\1>#is', ' ', $html );
		$html = (string) $html;

		/**
		 * Filter the HTML the indexer will read for a post.
		 *
		 * @param string  $html Rendered HTML.
		 * @param WP_Post $post Post being indexed.
		 */
		$html = apply_filters( 'vom_brain_indexable_html', $html, $post );

		$parts = preg_split( '#<h([1-4])[^>]*>(.*?)</h\1>#is', $html, -1, PREG_SPLIT_DELIM_CAPTURE );
		if ( ! is_array( $parts ) ) {
			$parts = array( $html );
		}

		$sections = array();
		$heading  = '';
		$buffer   = array_shift( $parts );
		$text     = self::to_text( (string) $buffer );
		if ( '' !== $text ) {
			$sections[] = array(
				'heading' => '',
				'text'    => $text,
			);
		}

		while ( count( $parts ) >= 3 ) {
			array_shift( $parts );                       // Heading level, unused.
			$heading = self::to_text( (string) array_shift( $parts ) );
			$body    = self::to_text( (string) array_shift( $parts ) );
			if ( '' !== $heading || '' !== $body ) {
				$sections[] = array(
					'heading' => $heading,
					'text'    => $body,
				);
			}
		}

		if ( ! $sections ) {
			$sections[] = array(
				'heading' => '',
				'text'    => self::to_text( $html ),
			);
		}

		return $sections;
	}

	/**
	 * HTML to normalized plain text.
	 *
	 * @param string $html HTML fragment.
	 * @return string
	 */
	public static function to_text( $html ) {
		$html = preg_replace( '#<(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>#i', "\n", $html );
		$text = wp_strip_all_tags( (string) $html, false );
		$text = html_entity_decode( $text, ENT_QUOTES, 'UTF-8' );
		$text = str_replace( "\xc2\xa0", ' ', $text );
		$text = preg_replace( "/[ \t]+/", ' ', $text );
		$text = preg_replace( "/\n{2,}/", "\n", $text );
		return trim( (string) $text );
	}

	/**
	 * Flatten sections into one text blob.
	 *
	 * @param array $sections Sections.
	 * @return string
	 */
	public static function sections_to_text( $sections ) {
		$out = array();
		foreach ( (array) $sections as $s ) {
			if ( ! empty( $s['heading'] ) ) {
				$out[] = $s['heading'];
			}
			if ( ! empty( $s['text'] ) ) {
				$out[] = $s['text'];
			}
		}
		return trim( implode( "\n\n", $out ) );
	}

	/**
	 * Sections rendered as markdown, for agents that want the whole page.
	 *
	 * @param array $sections Sections.
	 * @return string
	 */
	public static function sections_to_markdown( $sections ) {
		$out = array();
		foreach ( (array) $sections as $s ) {
			if ( ! empty( $s['heading'] ) ) {
				$out[] = '## ' . $s['heading'];
			}
			if ( ! empty( $s['text'] ) ) {
				$out[] = $s['text'];
			}
		}
		return trim( implode( "\n\n", $out ) );
	}

	/**
	 * Split sections into overlapping, retrieval-sized passages.
	 *
	 * @param array $sections Sections.
	 * @param int   $size     Target words per chunk.
	 * @param int   $overlap  Words repeated between neighbours.
	 * @return array List of { heading, text }.
	 */
	public static function chunk_sections( $sections, $size = 220, $overlap = 40 ) {
		$size    = max( 60, (int) $size );
		$overlap = max( 0, min( (int) $overlap, $size - 20 ) );
		$out     = array();

		foreach ( (array) $sections as $section ) {
			$heading = isset( $section['heading'] ) ? (string) $section['heading'] : '';
			$text    = isset( $section['text'] ) ? (string) $section['text'] : '';
			if ( '' === trim( $text ) ) {
				if ( '' !== trim( $heading ) ) {
					$out[] = array(
						'heading' => $heading,
						'text'    => $heading,
					);
				}
				continue;
			}

			$words = preg_split( '/\s+/u', $text, -1, PREG_SPLIT_NO_EMPTY );
			if ( ! is_array( $words ) ) {
				$words = array();
			}
			$total = count( $words );

			if ( $total <= $size ) {
				$out[] = array(
					'heading' => $heading,
					'text'    => $text,
				);
				continue;
			}

			$start = 0;
			while ( $start < $total ) {
				$slice = array_slice( $words, $start, $size );
				$out[] = array(
					'heading' => $heading,
					'text'    => implode( ' ', $slice ),
				);
				$step = $size - $overlap;
				if ( $step < 1 ) {
					$step = $size;
				}
				$start += $step;
			}
		}

		return $out;
	}

	/**
	 * A short human summary for listings.
	 *
	 * @param WP_Post $post  Post.
	 * @param string  $plain Plain text body.
	 * @return string
	 */
	public static function build_excerpt( $post, $plain ) {
		if ( '' !== trim( (string) $post->post_excerpt ) ) {
			return self::to_text( $post->post_excerpt );
		}
		return wp_trim_words( $plain, 45, '…' );
	}

	/**
	 * Terms attached to a post, for the enabled taxonomies.
	 *
	 * @param WP_Post $post Post.
	 * @return array taxonomy => term names.
	 */
	public static function collect_taxonomies( $post ) {
		$out = array();
		foreach ( (array) VOM_Brain_Settings::get( 'taxonomies', array() ) as $tax ) {
			if ( ! taxonomy_exists( $tax ) || ! is_object_in_taxonomy( $post->post_type, $tax ) ) {
				continue;
			}
			$terms = get_the_terms( $post, $tax );
			if ( is_wp_error( $terms ) || ! $terms ) {
				continue;
			}
			$names = array();
			foreach ( $terms as $term ) {
				$names[] = $term->name;
			}
			if ( $names ) {
				$out[ $tax ] = $names;
			}
		}
		return $out;
	}

	/**
	 * Structured extras: configured meta keys, plus WooCommerce product facts.
	 *
	 * @param WP_Post $post Post.
	 * @return array
	 */
	public static function collect_meta( $post ) {
		$out = array();

		foreach ( (array) VOM_Brain_Settings::get( 'meta_keys', array() ) as $key ) {
			$value = get_post_meta( $post->ID, $key, true );
			if ( is_scalar( $value ) && '' !== $value ) {
				$out[ $key ] = (string) $value;
			}
		}

		if ( VOM_Brain_Settings::get( 'include_woo' ) && 'product' === $post->post_type && function_exists( 'wc_get_product' ) ) {
			$product = wc_get_product( $post->ID );
			if ( $product ) {
				$out['sku']          = (string) $product->get_sku();
				$out['price']        = (string) $product->get_price();
				$out['regular_price']= (string) $product->get_regular_price();
				$out['sale_price']   = (string) $product->get_sale_price();
				$out['currency']     = function_exists( 'get_woocommerce_currency' ) ? get_woocommerce_currency() : '';
				$out['stock_status'] = (string) $product->get_stock_status();
				$out['product_type'] = (string) $product->get_type();
				$out = array_filter( $out, 'strlen' );
			}
		}

		/**
		 * Filter the structured metadata stored for a document.
		 *
		 * @param array   $out  Metadata.
		 * @param WP_Post $post Post being indexed.
		 */
		return apply_filters( 'vom_brain_document_meta', $out, $post );
	}

	/**
	 * Count words in a UTF-8 safe way.
	 *
	 * @param string $text Text.
	 * @return int
	 */
	public static function word_count( $text ) {
		$words = preg_split( '/\s+/u', (string) $text, -1, PREG_SPLIT_NO_EMPTY );
		return is_array( $words ) ? count( $words ) : 0;
	}

	/* --------------------------------------------------------------------- */
	/* Retrieval                                                              */
	/* --------------------------------------------------------------------- */

	/**
	 * Passage search across the indexed site.
	 *
	 * @param string $query Search text.
	 * @param array  $args  { limit, post_types, scope, max_per_doc }.
	 * @return array List of passages.
	 */
	public static function search( $query, $args = array() ) {
		global $wpdb;

		$args = array_merge(
			array(
				'limit'       => 8,
				'post_types'  => array(),
				'scope'       => 'read',
				'max_per_doc' => 2,
			),
			$args
		);

		$query = trim( (string) $query );
		if ( '' === $query ) {
			return array();
		}

		$limit  = max( 1, min( 50, (int) $args['limit'] ) );
		$chunks = self::table_chunks();

		$where  = array( '1=1' );
		$params = array();

		if ( 'full' !== $args['scope'] ) {
			$where[] = "visibility = 'public'";
		}
		$types = array_values( array_filter( (array) $args['post_types'] ) );
		if ( $types ) {
			$where[]  = 'post_type IN (' . implode( ',', array_fill( 0, count( $types ), '%s' ) ) . ')';
			$params   = array_merge( $params, $types );
		}
		$where_sql = implode( ' AND ', $where );

		$rows = array();

		if ( get_option( self::FT_FLAG ) ) {
			$boolean = self::boolean_query( $query );
			if ( '' !== $boolean ) {
				$sql = "SELECT chunk_id, doc_id, object_id, post_type, seq, title, url, heading, content, word_count,
					MATCH(heading,content) AGAINST (%s IN BOOLEAN MODE) AS score
					FROM {$chunks}
					WHERE {$where_sql} AND MATCH(heading,content) AGAINST (%s IN BOOLEAN MODE)
					ORDER BY score DESC LIMIT %d";
				$bind = array_merge( array( $boolean ), $params, array( $boolean, $limit * 3 ) );
				$rows = $wpdb->get_results( $wpdb->prepare( $sql, $bind ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
			}
		}

		if ( count( (array) $rows ) < 2 ) {
			$merged = array();
			foreach ( array_merge( (array) $rows, self::search_like( $query, $where_sql, $params, $limit * 3 ) ) as $row ) {
				$merged[ (int) $row['chunk_id'] ] = $row;
			}
			$rows = array_values( $merged );
		}

		// Diversify: cap passages per document, keep best first.
		$seen   = array();
		$out    = array();
		$budget = (int) VOM_Brain_Settings::get( 'max_response_chars', 12000 );
		$used   = 0;

		foreach ( (array) $rows as $row ) {
			$doc = (int) $row['doc_id'];
			if ( isset( $seen[ $doc ] ) && $seen[ $doc ] >= (int) $args['max_per_doc'] ) {
				continue;
			}
			$text = (string) $row['content'];
			if ( $used + strlen( $text ) > $budget && $out ) {
				break;
			}
			$used += strlen( $text );

			$seen[ $doc ] = isset( $seen[ $doc ] ) ? $seen[ $doc ] + 1 : 1;
			$out[]        = array(
				'title'     => (string) $row['title'],
				'heading'   => (string) $row['heading'],
				'url'       => (string) $row['url'],
				'post_type' => (string) $row['post_type'],
				'object_id' => (int) $row['object_id'],
				'passage'   => $text,
				'score'     => isset( $row['score'] ) ? round( (float) $row['score'], 4 ) : null,
			);

			if ( count( $out ) >= $limit ) {
				break;
			}
		}

		return $out;
	}

	/**
	 * LIKE fallback for hosts without a usable FULLTEXT index.
	 *
	 * @param string $query     Search text.
	 * @param string $where_sql Pre-built WHERE clause.
	 * @param array  $params    Bound params for the WHERE clause.
	 * @param int    $limit     Row cap.
	 * @return array
	 */
	private static function search_like( $query, $where_sql, $params, $limit ) {
		global $wpdb;

		$terms = self::terms( $query );
		if ( ! $terms ) {
			return array();
		}

		$chunks = self::table_chunks();
		$score  = array();
		$likes  = array();
		$bind   = array();

		foreach ( $terms as $term ) {
			$like    = '%' . $wpdb->esc_like( $term ) . '%';
			$likes[] = '(content LIKE %s OR heading LIKE %s OR title LIKE %s)';
			$score[] = '(CASE WHEN content LIKE %s THEN 1 ELSE 0 END) + (CASE WHEN heading LIKE %s THEN 2 ELSE 0 END) + (CASE WHEN title LIKE %s THEN 2 ELSE 0 END)';
			$bind    = array_merge( $bind, array( $like, $like, $like ) );
		}

		$score_sql = implode( ' + ', $score );
		$like_sql  = implode( ' OR ', $likes );

		$sql  = "SELECT chunk_id, doc_id, object_id, post_type, seq, title, url, heading, content, word_count,
			({$score_sql}) AS score
			FROM {$chunks}
			WHERE {$where_sql} AND ({$like_sql})
			ORDER BY score DESC, word_count DESC LIMIT %d";
		$args = array_merge( $bind, $params, $bind, array( (int) $limit ) );

		$rows = $wpdb->get_results( $wpdb->prepare( $sql, $args ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
		return is_array( $rows ) ? $rows : array();
	}

	/**
	 * Split a query into usable search terms.
	 *
	 * @param string $query Raw query.
	 * @return string[]
	 */
	public static function terms( $query ) {
		$query = preg_replace( '/[^\p{L}\p{N}\s\-]+/u', ' ', (string) $query );
		$parts = preg_split( '/\s+/u', (string) $query, -1, PREG_SPLIT_NO_EMPTY );
		$out   = array();
		foreach ( (array) $parts as $part ) {
			if ( mb_strlen( $part ) >= 2 ) {
				$out[] = $part;
			}
		}
		return array_slice( $out, 0, 12 );
	}

	/**
	 * Build a safe MySQL boolean-mode expression.
	 *
	 * @param string $query Raw query.
	 * @return string
	 */
	public static function boolean_query( $query ) {
		$terms = self::terms( $query );
		if ( ! $terms ) {
			return '';
		}
		$parts = array();
		foreach ( $terms as $term ) {
			$parts[] = $term . ( mb_strlen( $term ) >= 4 ? '*' : '' );
		}
		return implode( ' ', $parts );
	}

	/**
	 * Fetch a document by id, slug or URL.
	 *
	 * @param array $args  { id, slug, url, scope }.
	 * @return array|null
	 */
	public static function get_document( $args ) {
		global $wpdb;

		$docs  = self::table_docs();
		$scope = isset( $args['scope'] ) ? $args['scope'] : 'read';
		$vis   = 'full' === $scope ? '' : " AND visibility = 'public'";
		$row   = null;

		if ( ! empty( $args['id'] ) ) {
			$row = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$docs} WHERE object_id = %d{$vis}", (int) $args['id'] ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
		}
		if ( ! $row && ! empty( $args['slug'] ) ) {
			$row = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$docs} WHERE slug = %s{$vis} ORDER BY modified DESC LIMIT 1", sanitize_title( $args['slug'] ) ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
		}
		if ( ! $row && ! empty( $args['url'] ) ) {
			$url = esc_url_raw( $args['url'] );
			$row = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$docs} WHERE url = %s{$vis} LIMIT 1", $url ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
			if ( ! $row ) {
				$path = trim( (string) wp_parse_url( $url, PHP_URL_PATH ), '/' );
				if ( '' !== $path ) {
					$like = '%' . $wpdb->esc_like( $path ) . '%';
					$row  = $wpdb->get_row( $wpdb->prepare( "SELECT * FROM {$docs} WHERE url LIKE %s{$vis} LIMIT 1", $like ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
				}
			}
		}

		return $row ? self::hydrate( $row ) : null;
	}

	/**
	 * Browse documents.
	 *
	 * @param array $args { post_type, taxonomy, term, search, page, per_page, order_by, scope }.
	 * @return array { total, page, per_page, items }.
	 */
	public static function list_documents( $args = array() ) {
		global $wpdb;

		$args = array_merge(
			array(
				'post_type' => '',
				'taxonomy'  => '',
				'term'      => '',
				'search'    => '',
				'page'      => 1,
				'per_page'  => 20,
				'order_by'  => 'modified',
				'since'     => '',
				'scope'     => 'read',
			),
			$args
		);

		$docs     = self::table_docs();
		$where    = array( '1=1' );
		$params   = array();
		$per_page = max( 1, min( 200, (int) $args['per_page'] ) );
		$page     = max( 1, (int) $args['page'] );

		if ( 'full' !== $args['scope'] ) {
			$where[] = "visibility = 'public'";
		}
		if ( $args['post_type'] ) {
			$where[]  = 'post_type = %s';
			$params[] = sanitize_key( $args['post_type'] );
		}
		if ( $args['search'] ) {
			$like     = '%' . $wpdb->esc_like( $args['search'] ) . '%';
			$where[]  = '(title LIKE %s OR excerpt LIKE %s)';
			$params[] = $like;
			$params[] = $like;
		}
		if ( $args['taxonomy'] && $args['term'] ) {
			$like     = '%' . $wpdb->esc_like( '"' . $args['term'] . '"' ) . '%';
			$where[]  = '(taxonomies LIKE %s)';
			$params[] = $like;
		}
		if ( $args['since'] ) {
			$ts = strtotime( (string) $args['since'] );
			if ( $ts ) {
				$where[]  = 'modified >= %s';
				$params[] = gmdate( 'Y-m-d H:i:s', $ts );
			}
		}

		$where_sql = implode( ' AND ', $where );
		$order     = in_array( $args['order_by'], array( 'modified', 'title', 'word_count' ), true ) ? $args['order_by'] : 'modified';
		$direction = 'title' === $order ? 'ASC' : 'DESC';

		$count_sql = "SELECT COUNT(*) FROM {$docs} WHERE {$where_sql}";
		$total     = $params
			? (int) $wpdb->get_var( $wpdb->prepare( $count_sql, $params ) ) // phpcs:ignore WordPress.DB.PreparedSQL
			: (int) $wpdb->get_var( $count_sql ); // phpcs:ignore WordPress.DB.PreparedSQL

		$sql  = "SELECT * FROM {$docs} WHERE {$where_sql} ORDER BY {$order} {$direction} LIMIT %d OFFSET %d";
		$bind = array_merge( $params, array( $per_page, ( $page - 1 ) * $per_page ) );
		$rows = $wpdb->get_results( $wpdb->prepare( $sql, $bind ), ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL

		$items = array();
		foreach ( (array) $rows as $row ) {
			$items[] = self::hydrate( $row, false );
		}

		return array(
			'total'    => $total,
			'page'     => $page,
			'per_page' => $per_page,
			'pages'    => (int) ceil( $total / $per_page ),
			'items'    => $items,
		);
	}

	/**
	 * Turn a DB row into an API-shaped document.
	 *
	 * @param array $row       Raw row.
	 * @param bool  $with_body Include sections and full text.
	 * @return array
	 */
	public static function hydrate( $row, $with_body = true ) {
		$sections = json_decode( (string) $row['sections'], true );
		if ( ! is_array( $sections ) ) {
			$sections = array();
		}
		$tax = json_decode( (string) $row['taxonomies'], true );
		$met = json_decode( (string) $row['meta'], true );

		$out = array(
			'id'         => (int) $row['object_id'],
			'type'       => (string) $row['post_type'],
			'title'      => (string) $row['title'],
			'slug'       => (string) $row['slug'],
			'url'        => (string) $row['url'],
			'summary'    => (string) $row['excerpt'],
			'taxonomies' => is_array( $tax ) ? $tax : array(),
			'meta'       => is_array( $met ) ? $met : array(),
			'word_count' => (int) $row['word_count'],
			'modified'   => $row['modified'] ? gmdate( 'c', strtotime( $row['modified'] . ' UTC' ) ) : null,
			'hash'       => (string) $row['content_hash'],
		);

		if ( $with_body ) {
			$out['sections'] = $sections;
			$out['text']     = self::sections_to_text( $sections );
			$out['markdown'] = self::sections_to_markdown( $sections );
		}

		return $out;
	}

	/**
	 * Documents removed since a timestamp.
	 *
	 * @param string $since ISO 8601 timestamp.
	 * @return array
	 */
	public static function removed_since( $since ) {
		$stones = get_option( self::TOMBSTONES, array() );
		if ( ! is_array( $stones ) ) {
			return array();
		}
		$ts = $since ? strtotime( $since ) : 0;
		if ( ! $ts ) {
			return $stones;
		}
		$out = array();
		foreach ( $stones as $stone ) {
			if ( strtotime( $stone['removed_at'] ) >= $ts ) {
				$out[] = $stone;
			}
		}
		return $out;
	}

	/**
	 * Index statistics.
	 *
	 * @return array
	 */
	public static function stats() {
		global $wpdb;

		$docs   = self::table_docs();
		$chunks = self::table_chunks();

		$by_type = array();
		$rows    = $wpdb->get_results( "SELECT post_type, COUNT(*) AS n FROM {$docs} GROUP BY post_type", ARRAY_A ); // phpcs:ignore WordPress.DB.PreparedSQL
		foreach ( (array) $rows as $row ) {
			$by_type[ $row['post_type'] ] = (int) $row['n'];
		}

		$last = $wpdb->get_var( "SELECT MAX(modified) FROM {$docs}" ); // phpcs:ignore WordPress.DB.PreparedSQL

		return array(
			'documents'    => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$docs}" ), // phpcs:ignore WordPress.DB.PreparedSQL
			'passages'     => (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$chunks}" ), // phpcs:ignore WordPress.DB.PreparedSQL
			'words'        => (int) $wpdb->get_var( "SELECT COALESCE(SUM(word_count),0) FROM {$docs}" ), // phpcs:ignore WordPress.DB.PreparedSQL
			'by_type'      => $by_type,
			'last_content' => $last ? gmdate( 'c', strtotime( $last . ' UTC' ) ) : null,
			'fulltext'     => (bool) get_option( self::FT_FLAG ),
		);
	}

	/**
	 * Wipe both tables.
	 */
	public static function truncate() {
		global $wpdb;
		$docs   = self::table_docs();
		$chunks = self::table_chunks();
		$wpdb->query( "TRUNCATE TABLE {$chunks}" ); // phpcs:ignore WordPress.DB.PreparedSQL
		$wpdb->query( "TRUNCATE TABLE {$docs}" ); // phpcs:ignore WordPress.DB.PreparedSQL
	}
}
