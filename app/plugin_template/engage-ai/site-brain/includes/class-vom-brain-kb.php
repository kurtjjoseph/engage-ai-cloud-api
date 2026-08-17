<?php
/**
 * Knowledge base: everything the site knows that is not a page.
 *
 * Uploaded files and pasted notes become documents in the SAME index as the
 * website's own content, so `search_site` grounds on them without any change to
 * the MCP surface. That is the whole design: a knowledge document is a post of a
 * private custom type, which means the existing crawl → chunk → FULLTEXT path
 * handles it and there is no second retrieval path to keep in step.
 *
 * Originals are kept outside the web root's reach, in a guarded uploads folder —
 * a staff handbook must not become a public download just because it was added
 * to the brain.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_KB {

	const POST_TYPE  = 'vom_kb';
	const META_VIS   = '_vom_kb_visibility';
	const META_FILE  = '_vom_kb_file';
	const META_MIME  = '_vom_kb_mime';
	const META_SIZE  = '_vom_kb_size';
	const META_HASH  = '_vom_kb_sha256';
	const META_KIND  = '_vom_kb_kind';
	const META_NOTE  = '_vom_kb_note';
	const SUBDIR     = 'vom-brain-kb';

	/**
	 * Extensions we can turn into text, and how.
	 *
	 * @return array ext => handler
	 */
	public static function handlers() {
		return array(
			'txt'      => 'plain',
			'text'     => 'plain',
			'md'       => 'markdown',
			'markdown' => 'markdown',
			'csv'      => 'csv',
			'tsv'      => 'csv',
			'json'     => 'json',
			'html'     => 'html',
			'htm'      => 'html',
			'xml'      => 'html',
			'docx'     => 'docx',
			'odt'      => 'odt',
			'pdf'      => 'pdf',
		);
	}

	public static function hooks() {
		add_action( 'init', array( __CLASS__, 'register_type' ) );

		// A knowledge document carries its own visibility, unlike a post type
		// where the whole type is public or private.
		add_filter( 'vom_brain_document_visibility', array( __CLASS__, 'document_visibility' ), 10, 2 );

		add_action( 'admin_post_vom_brain_kb_add', array( __CLASS__, 'handle_add' ) );
		add_action( 'admin_post_vom_brain_kb_item', array( __CLASS__, 'handle_item' ) );
	}

	/**
	 * A private, non-queryable type. It is never routed on the front end; it
	 * exists so the indexer has something ordinary to chew on.
	 */
	public static function register_type() {
		register_post_type(
			self::POST_TYPE,
			array(
				'label'               => __( 'Knowledge', 'vom-site-brain' ),
				'public'              => false,
				'publicly_queryable'  => false,
				'exclude_from_search' => true,
				'show_ui'             => false,
				'show_in_menu'        => false,
				'show_in_rest'        => false,
				'has_archive'         => false,
				'rewrite'             => false,
				'query_var'           => false,
				'supports'            => array( 'title', 'editor', 'revisions' ),
				'capability_type'     => 'post',
				'map_meta_cap'        => true,
			)
		);
	}

	/**
	 * Per-document visibility, defaulting to private: material someone uploaded
	 * by hand is far more likely to be internal than a published page.
	 *
	 * @param string  $visibility Visibility derived from the post type.
	 * @param WP_Post $post       Post being indexed.
	 * @return string
	 */
	public static function document_visibility( $visibility, $post ) {
		if ( ! $post instanceof WP_Post || self::POST_TYPE !== $post->post_type ) {
			return $visibility;
		}
		$own = get_post_meta( $post->ID, self::META_VIS, true );
		return ( 'public' === $own ) ? 'public' : 'private';
	}

	/* ------------------------------------------------------------------ *
	 * storage
	 * ------------------------------------------------------------------ */

	/**
	 * Guarded directory for original files. Apache is told to deny; for nginx,
	 * which ignores .htaccess, the filename carries 32 random hex characters so
	 * the path is not guessable. Nothing ever links to it.
	 *
	 * @return array|WP_Error { dir, url }
	 */
	public static function storage_dir() {
		$up = wp_upload_dir();
		if ( ! empty( $up['error'] ) ) {
			return new WP_Error( 'uploads', $up['error'] );
		}
		$dir = trailingslashit( $up['basedir'] ) . self::SUBDIR;

		if ( ! file_exists( $dir ) && ! wp_mkdir_p( $dir ) ) {
			return new WP_Error( 'mkdir', __( 'Could not create the knowledge base folder.', 'vom-site-brain' ) );
		}
		if ( ! file_exists( $dir . '/.htaccess' ) ) {
			file_put_contents( $dir . '/.htaccess', "Require all denied\n<IfModule !mod_authz_core.c>\nDeny from all\n</IfModule>\n" ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		}
		if ( ! file_exists( $dir . '/index.php' ) ) {
			file_put_contents( $dir . '/index.php', "<?php\n// Silence is golden.\n" ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		}
		return array( 'dir' => $dir );
	}

	/* ------------------------------------------------------------------ *
	 * text extraction
	 * ------------------------------------------------------------------ */

	/**
	 * Pull readable text out of a file.
	 *
	 * @param string $path Absolute path.
	 * @param string $ext  Lower-case extension.
	 * @return string|WP_Error
	 */
	public static function extract( $path, $ext ) {
		$handlers = self::handlers();
		if ( ! isset( $handlers[ $ext ] ) ) {
			return new WP_Error( 'unsupported', sprintf( __( 'No text extractor for .%s files.', 'vom-site-brain' ), $ext ) );
		}
		if ( ! is_readable( $path ) ) {
			return new WP_Error( 'unreadable', __( 'The uploaded file could not be read.', 'vom-site-brain' ) );
		}

		switch ( $handlers[ $ext ] ) {
			case 'plain':
			case 'markdown':
				return self::normalise( (string) file_get_contents( $path ) ); // phpcs:ignore WordPress.WP.AlternativeFunctions

			case 'html':
				return self::normalise( wp_strip_all_tags( (string) file_get_contents( $path ) ) ); // phpcs:ignore WordPress.WP.AlternativeFunctions

			case 'csv':
				return self::from_csv( $path, 'tsv' === $ext ? "\t" : ',' );

			case 'json':
				return self::from_json( (string) file_get_contents( $path ) ); // phpcs:ignore WordPress.WP.AlternativeFunctions

			case 'docx':
				return self::from_office( $path, 'word/document.xml' );

			case 'odt':
				return self::from_office( $path, 'content.xml' );

			case 'pdf':
				return self::from_pdf( $path );
		}

		return new WP_Error( 'unsupported', __( 'Unsupported file type.', 'vom-site-brain' ) );
	}

	/** Collapse whitespace without destroying paragraph breaks. */
	public static function normalise( $text ) {
		$text = str_replace( array( "\r\n", "\r" ), "\n", (string) $text );
		$text = preg_replace( '/[ \t]+/', ' ', $text );
		$text = preg_replace( '/\n{3,}/', "\n\n", (string) $text );
		return trim( (string) $text );
	}

	/** Rows become "Column: value" lines so a passage reads as prose, not as a grid. */
	private static function from_csv( $path, $sep = ',' ) {
		$fh = fopen( $path, 'r' ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		if ( ! $fh ) {
			return new WP_Error( 'unreadable', __( 'Could not open the file.', 'vom-site-brain' ) );
		}
		$head  = null;
		$lines = array();
		$n     = 0;
		while ( ( $row = fgetcsv( $fh, 0, $sep ) ) !== false ) {
			if ( null === $head ) {
				$head = array_map( 'strval', $row );
				continue;
			}
			$bits = array();
			foreach ( $row as $i => $cell ) {
				$key = isset( $head[ $i ] ) && '' !== $head[ $i ] ? $head[ $i ] : 'col' . ( $i + 1 );
				if ( '' !== trim( (string) $cell ) ) {
					$bits[] = $key . ': ' . trim( (string) $cell );
				}
			}
			if ( $bits ) {
				$lines[] = implode( '; ', $bits );
			}
			$n++;
			if ( $n > 5000 ) {
				$lines[] = '…(truncated at 5000 rows)';
				break;
			}
		}
		fclose( $fh ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		return self::normalise( implode( "\n", $lines ) );
	}

	private static function from_json( $raw ) {
		$data = json_decode( $raw, true );
		if ( null === $data ) {
			return self::normalise( $raw );
		}
		$out = array();
		$walk = function ( $node, $prefix ) use ( &$walk, &$out ) {
			if ( is_array( $node ) ) {
				foreach ( $node as $k => $v ) {
					$walk( $v, $prefix ? $prefix . '.' . $k : (string) $k );
				}
			} elseif ( is_scalar( $node ) && '' !== (string) $node ) {
				$out[] = $prefix . ': ' . $node;
			}
		};
		$walk( $data, '' );
		return self::normalise( implode( "\n", $out ) );
	}

	/**
	 * DOCX and ODT are both zips with an XML body. Paragraph tags become
	 * newlines before the tags are stripped, or every paragraph runs together.
	 */
	private static function from_office( $path, $entry ) {
		if ( ! class_exists( 'ZipArchive' ) ) {
			return new WP_Error( 'nozip', __( 'PHP is missing the ZipArchive extension, which is needed to read Word and OpenDocument files.', 'vom-site-brain' ) );
		}
		$zip = new ZipArchive();
		if ( true !== $zip->open( $path ) ) {
			return new WP_Error( 'badzip', __( 'That file is not a readable Word/OpenDocument document.', 'vom-site-brain' ) );
		}
		$xml = $zip->getFromName( $entry );
		$zip->close();
		if ( false === $xml ) {
			return new WP_Error( 'badzip', __( 'The document body could not be found inside the file.', 'vom-site-brain' ) );
		}

		$xml = preg_replace( '#</w:p>|</text:p>|</text:h>#', "\n", $xml );
		$xml = preg_replace( '#<w:tab[^>]*/>#', "\t", (string) $xml );
		$xml = preg_replace( '#<w:br[^>]*/>#', "\n", (string) $xml );
		$txt = wp_strip_all_tags( (string) $xml );
		$txt = html_entity_decode( $txt, ENT_QUOTES | ENT_HTML5, 'UTF-8' );

		return self::normalise( $txt );
	}

	/**
	 * PDF text, best effort.
	 *
	 * Prefers the `pdftotext` binary when the host allows shelling out, because
	 * it is far more accurate. Falls back to reading FlateDecode content streams
	 * directly, which handles ordinary text PDFs and fails honestly on scanned
	 * ones rather than returning gibberish.
	 */
	private static function from_pdf( $path ) {
		$bin = self::pdftotext_binary();
		if ( $bin ) {
			$out = @shell_exec( escapeshellcmd( $bin ) . ' -layout -enc UTF-8 ' . escapeshellarg( $path ) . ' - 2>/dev/null' ); // phpcs:ignore WordPress.PHP.NoSilencedErrors,WordPress.PHP.DiscouragedPHPFunctions
			if ( is_string( $out ) && '' !== trim( $out ) ) {
				return self::normalise( $out );
			}
		}

		$raw = (string) file_get_contents( $path ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		$text = self::pdf_text_from_streams( $raw );
		if ( '' !== trim( $text ) ) {
			return self::normalise( $text );
		}

		return new WP_Error(
			'pdf',
			__( 'No text could be extracted from that PDF. It is most likely a scan (images of text) with no text layer — run OCR on it first, or paste the text in as a note.', 'vom-site-brain' )
		);
	}

	/** Locate pdftotext, if this host both has it and permits exec. */
	private static function pdftotext_binary() {
		if ( ! function_exists( 'shell_exec' ) ) {
			return '';
		}
		$disabled = array_map( 'trim', explode( ',', (string) ini_get( 'disable_functions' ) ) );
		if ( in_array( 'shell_exec', $disabled, true ) ) {
			return '';
		}
		foreach ( array( '/usr/bin/pdftotext', '/usr/local/bin/pdftotext', '/opt/homebrew/bin/pdftotext' ) as $candidate ) {
			if ( is_executable( $candidate ) ) {
				return $candidate;
			}
		}
		return '';
	}

	/**
	 * Pull show-text operators out of a PDF's content streams.
	 *
	 * Deliberately conservative: it decodes Flate streams, then takes the string
	 * arguments of Tj/TJ. It does not attempt font encoding maps, so it is right
	 * for ordinary Latin text and returns nothing — rather than nonsense — for
	 * anything exotic.
	 */
	private static function pdf_text_from_streams( $raw ) {
		if ( ! function_exists( 'gzuncompress' ) ) {
			return '';
		}
		$out = array();
		if ( ! preg_match_all( '/stream\r?\n(.*?)endstream/s', $raw, $m ) ) {
			return '';
		}
		foreach ( $m[1] as $stream ) {
			$data = @gzuncompress( $stream ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
			if ( false === $data ) {
				$data = $stream;              // some streams are stored uncompressed
			}
			if ( ! preg_match_all( '/\((?:\\\\.|[^\\\\()])*\)\s*(?:Tj|TJ)|\[(?:[^\]\\\\]|\\\\.)*\]\s*TJ/s', $data, $ops ) ) {
				continue;
			}
			foreach ( $ops[0] as $op ) {
				if ( preg_match_all( '/\(((?:\\\\.|[^\\\\()])*)\)/s', $op, $strs ) ) {
					$piece = implode( '', $strs[1] );
					$piece = str_replace(
						array( '\\(', '\\)', '\\\\', '\\n', '\\r', '\\t' ),
						array( '(', ')', '\\', "\n", "\r", "\t" ),
						$piece
					);
					if ( '' !== trim( $piece ) ) {
						$out[] = $piece;
					}
				}
			}
			$out[] = "\n";
		}
		$text = implode( ' ', $out );
		// Only keep it if it looks like language rather than binary soup.
		$printable = preg_match_all( '/[\p{L}\p{N}\s\.,;:!\?\'"()\-]/u', $text );
		if ( strlen( $text ) > 0 && $printable / max( 1, strlen( $text ) ) < 0.7 ) {
			return '';
		}
		return $text;
	}

	/* ------------------------------------------------------------------ *
	 * text → the HTML the indexer reads
	 * ------------------------------------------------------------------ */

	/**
	 * Wrap plain text as simple HTML so the existing section splitter has
	 * headings to split on. Markdown ATX headings and SETEXT-ish short lines
	 * become <h2>; everything else becomes a paragraph.
	 */
	public static function to_html( $text ) {
		$blocks = preg_split( '/\n{2,}/', self::normalise( $text ) );
		$html   = array();
		foreach ( (array) $blocks as $block ) {
			$block = trim( (string) $block );
			if ( '' === $block ) {
				continue;
			}
			if ( preg_match( '/^(#{1,4})\s+(.+)$/u', $block, $m ) ) {
				$level  = min( 4, max( 2, strlen( $m[1] ) ) );
				$html[] = '<h' . $level . '>' . esc_html( trim( $m[2] ) ) . '</h' . $level . '>';
				continue;
			}
			// A short, un-punctuated single line reads as a heading in most
			// documents that have been flattened out of Word or a PDF.
			if ( false === strpos( $block, "\n" ) && mb_strlen( $block ) <= 80 && ! preg_match( '/[.!?:;]$/u', $block ) ) {
				$html[] = '<h2>' . esc_html( $block ) . '</h2>';
				continue;
			}
			$html[] = '<p>' . nl2br( esc_html( $block ) ) . '</p>';
		}
		return implode( "\n", $html );
	}

	/* ------------------------------------------------------------------ *
	 * CRUD
	 * ------------------------------------------------------------------ */

	/**
	 * Create or replace a knowledge document from extracted text.
	 *
	 * @param array $args title, text, kind, visibility, note, file meta, post_id.
	 * @return int|WP_Error Post id.
	 */
	public static function save_document( $args ) {
		$args = wp_parse_args(
			$args,
			array(
				'post_id'    => 0,
				'title'      => '',
				'text'       => '',
				'kind'       => 'note',
				'visibility' => 'private',
				'note'       => '',
				'file'       => '',
				'mime'       => '',
				'size'       => 0,
				'hash'       => '',
			)
		);

		$title = sanitize_text_field( $args['title'] );
		$text  = (string) $args['text'];
		if ( '' === trim( $text ) ) {
			return new WP_Error( 'empty', __( 'There was no text to add.', 'vom-site-brain' ) );
		}
		if ( '' === $title ) {
			$title = __( 'Untitled document', 'vom-site-brain' );
		}

		$postarr = array(
			'post_type'    => self::POST_TYPE,
			'post_status'  => 'publish',
			'post_title'   => $title,
			'post_content' => self::to_html( $text ),
		);
		if ( $args['post_id'] ) {
			$postarr['ID'] = (int) $args['post_id'];
			$post_id       = wp_update_post( $postarr, true );
		} else {
			$post_id = wp_insert_post( $postarr, true );
		}
		if ( is_wp_error( $post_id ) ) {
			return $post_id;
		}

		update_post_meta( $post_id, self::META_VIS, 'public' === $args['visibility'] ? 'public' : 'private' );
		update_post_meta( $post_id, self::META_KIND, sanitize_key( $args['kind'] ) );
		update_post_meta( $post_id, self::META_NOTE, sanitize_text_field( $args['note'] ) );
		if ( $args['file'] ) {
			update_post_meta( $post_id, self::META_FILE, sanitize_text_field( $args['file'] ) );
			update_post_meta( $post_id, self::META_MIME, sanitize_text_field( $args['mime'] ) );
			update_post_meta( $post_id, self::META_SIZE, (int) $args['size'] );
			update_post_meta( $post_id, self::META_HASH, sanitize_text_field( $args['hash'] ) );
		}

		// wp_insert_post already fired save_post, but meta (and therefore
		// visibility) was not set yet at that point. Index again now that it is.
		VOM_Brain_Index::index_post( get_post( $post_id ) );

		return (int) $post_id;
	}

	/**
	 * Take an entry from $_FILES through validation, extraction and indexing.
	 *
	 * @param array  $file       One $_FILES entry.
	 * @param string $visibility public|private.
	 * @param string $note       Optional operator note.
	 * @return int|WP_Error
	 */
	public static function add_upload( $file, $visibility = 'private', $note = '' ) {
		if ( ! isset( $file['tmp_name'] ) || '' === $file['tmp_name'] || ! is_uploaded_file( $file['tmp_name'] ) ) {
			return new WP_Error( 'upload', __( 'No file was received.', 'vom-site-brain' ) );
		}
		if ( ! empty( $file['error'] ) ) {
			return new WP_Error( 'upload', __( 'The upload did not complete.', 'vom-site-brain' ) );
		}

		$name = sanitize_file_name( (string) $file['name'] );
		$ext  = strtolower( (string) pathinfo( $name, PATHINFO_EXTENSION ) );
		if ( ! isset( self::handlers()[ $ext ] ) ) {
			return new WP_Error(
				'unsupported',
				sprintf(
					/* translators: 1: extension, 2: list of supported extensions */
					__( '.%1$s is not a format this can read. Supported: %2$s.', 'vom-site-brain' ),
					$ext ? $ext : '?',
					implode( ', ', array_keys( self::handlers() ) )
				)
			);
		}

		$text = self::extract( $file['tmp_name'], $ext );
		if ( is_wp_error( $text ) ) {
			return $text;
		}

		$store = self::storage_dir();
		if ( is_wp_error( $store ) ) {
			return $store;
		}

		$hash   = (string) hash_file( 'sha256', $file['tmp_name'] );
		$stored = wp_unique_filename( $store['dir'], substr( $hash, 0, 32 ) . '-' . $name );
		$target = trailingslashit( $store['dir'] ) . $stored;

		// move_uploaded_file rather than a copy: it is the check that the file
		// really came from this request.
		if ( ! @move_uploaded_file( $file['tmp_name'], $target ) ) { // phpcs:ignore WordPress.PHP.NoSilencedErrors,WordPress.WP.AlternativeFunctions
			return new WP_Error( 'store', __( 'The file could not be stored.', 'vom-site-brain' ) );
		}

		$existing = self::find_by_hash( $hash );

		return self::save_document(
			array(
				'post_id'    => $existing,
				'title'      => pathinfo( $name, PATHINFO_FILENAME ),
				'text'       => $text,
				'kind'       => 'file',
				'visibility' => $visibility,
				'note'       => $note,
				'file'       => $stored,
				'mime'       => (string) ( $file['type'] ?? '' ),
				'size'       => (int) ( $file['size'] ?? 0 ),
				'hash'       => $hash,
			)
		);
	}

	/** An identical file replaces its previous document rather than duplicating it. */
	public static function find_by_hash( $hash ) {
		$found = get_posts(
			array(
				'post_type'      => self::POST_TYPE,
				'post_status'    => 'any',
				'posts_per_page' => 1,
				'fields'         => 'ids',
				'meta_key'       => self::META_HASH,   // phpcs:ignore WordPress.DB.SlowDBQuery
				'meta_value'     => $hash,             // phpcs:ignore WordPress.DB.SlowDBQuery
			)
		);
		return $found ? (int) $found[0] : 0;
	}

	/** Re-run extraction on the stored original, e.g. after installing pdftotext. */
	public static function reextract( $post_id ) {
		$post = get_post( $post_id );
		if ( ! $post || self::POST_TYPE !== $post->post_type ) {
			return new WP_Error( 'missing', __( 'Not a knowledge document.', 'vom-site-brain' ) );
		}
		$stored = (string) get_post_meta( $post_id, self::META_FILE, true );
		if ( '' === $stored ) {
			return new WP_Error( 'nofile', __( 'That entry is a note; there is no original file to re-read.', 'vom-site-brain' ) );
		}
		$store = self::storage_dir();
		if ( is_wp_error( $store ) ) {
			return $store;
		}
		$path = trailingslashit( $store['dir'] ) . $stored;
		$ext  = strtolower( (string) pathinfo( $stored, PATHINFO_EXTENSION ) );
		$text = self::extract( $path, $ext );
		if ( is_wp_error( $text ) ) {
			return $text;
		}
		return self::save_document(
			array(
				'post_id'    => $post_id,
				'title'      => $post->post_title,
				'text'       => $text,
				'kind'       => 'file',
				'visibility' => get_post_meta( $post_id, self::META_VIS, true ),
				'note'       => get_post_meta( $post_id, self::META_NOTE, true ),
			)
		);
	}

	/** Delete the document, its index rows and the stored original. */
	public static function delete( $post_id ) {
		$post = get_post( $post_id );
		if ( ! $post || self::POST_TYPE !== $post->post_type ) {
			return false;
		}
		$stored = (string) get_post_meta( $post_id, self::META_FILE, true );
		if ( '' !== $stored ) {
			$store = self::storage_dir();
			if ( ! is_wp_error( $store ) ) {
				$path = trailingslashit( $store['dir'] ) . $stored;
				if ( is_file( $path ) ) {
					wp_delete_file( $path );
				}
			}
		}
		wp_delete_post( $post_id, true );   // deleted_post → index removes its rows
		return true;
	}

	/**
	 * Every knowledge document, newest first, with its index state.
	 *
	 * @return array
	 */
	public static function all() {
		$posts = get_posts(
			array(
				'post_type'      => self::POST_TYPE,
				'post_status'    => 'any',
				'posts_per_page' => 500,
				'orderby'        => 'date',
				'order'          => 'DESC',
			)
		);

		global $wpdb;
		$docs = VOM_Brain_Index::table_docs();
		$out  = array();
		foreach ( $posts as $p ) {
			$row = $wpdb->get_row( $wpdb->prepare( "SELECT word_count, indexed_at FROM {$docs} WHERE object_type = 'post' AND object_id = %d", $p->ID ) ); // phpcs:ignore WordPress.DB.PreparedSQL
			$out[] = array(
				'id'         => (int) $p->ID,
				'title'      => $p->post_title,
				'kind'       => (string) get_post_meta( $p->ID, self::META_KIND, true ),
				'file'       => (string) get_post_meta( $p->ID, self::META_FILE, true ),
				'size'       => (int) get_post_meta( $p->ID, self::META_SIZE, true ),
				'note'       => (string) get_post_meta( $p->ID, self::META_NOTE, true ),
				'visibility' => 'public' === get_post_meta( $p->ID, self::META_VIS, true ) ? 'public' : 'private',
				'added'      => $p->post_date_gmt,
				'words'      => $row ? (int) $row->word_count : 0,
				'indexed'    => $row ? $row->indexed_at : null,
			);
		}
		return $out;
	}

	public static function count() {
		$n = wp_count_posts( self::POST_TYPE );
		return $n && isset( $n->publish ) ? (int) $n->publish : 0;
	}

	/* ------------------------------------------------------------------ *
	 * admin-post handlers
	 * ------------------------------------------------------------------ */

	private static function guard( $action ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( esc_html__( 'You are not allowed to do this.', 'vom-site-brain' ) );
		}
		check_admin_referer( $action );
	}

	private static function back( $args = array() ) {
		// The admin page slug is not fixed: these classes also run as the Site
		// Brain module inside Engage AI, which sets VOM_BRAIN_PAGE_SLUG. Ask the
		// admin class rather than hardcoding, or every redirect lands on a
		// non-existent page there.
		$slug = class_exists( 'VOM_Brain_Admin' ) ? VOM_Brain_Admin::page_slug() : 'vom-site-brain';
		$url  = add_query_arg(
			array_merge( array( 'page' => $slug, 'tab' => 'knowledge' ), $args ),
			admin_url( 'admin.php' )
		);
		wp_safe_redirect( $url );
		exit;
	}

	public static function handle_add() {
		self::guard( 'vom_brain_kb_add' );

		$visibility = isset( $_POST['visibility'] ) && 'public' === $_POST['visibility'] ? 'public' : 'private';
		$note       = isset( $_POST['note'] ) ? sanitize_text_field( wp_unslash( $_POST['note'] ) ) : '';
		$results    = array( 'ok' => 0, 'fail' => 0 );
		$first_err  = '';

		// Pasted note.
		$text = isset( $_POST['text'] ) ? (string) wp_unslash( $_POST['text'] ) : ''; // phpcs:ignore WordPress.Security.ValidatedSanitizedInput
		if ( '' !== trim( $text ) ) {
			$res = self::save_document(
				array(
					'title'      => isset( $_POST['title'] ) ? sanitize_text_field( wp_unslash( $_POST['title'] ) ) : '',
					'text'       => $text,
					'kind'       => 'note',
					'visibility' => $visibility,
					'note'       => $note,
				)
			);
			if ( is_wp_error( $res ) ) {
				$results['fail']++;
				$first_err = $res->get_error_message();
			} else {
				$results['ok']++;
			}
		}

		// Uploaded files (multiple).
		if ( ! empty( $_FILES['files']['name'] ) && is_array( $_FILES['files']['name'] ) ) {
			$count = count( $_FILES['files']['name'] );
			for ( $i = 0; $i < $count; $i++ ) {
				if ( '' === $_FILES['files']['name'][ $i ] ) {
					continue;
				}
				$one = array(
					'name'     => $_FILES['files']['name'][ $i ],
					'type'     => $_FILES['files']['type'][ $i ],
					'tmp_name' => $_FILES['files']['tmp_name'][ $i ],
					'error'    => $_FILES['files']['error'][ $i ],
					'size'     => $_FILES['files']['size'][ $i ],
				);
				$res = self::add_upload( $one, $visibility, $note );
				if ( is_wp_error( $res ) ) {
					$results['fail']++;
					if ( '' === $first_err ) {
						$first_err = $one['name'] . ': ' . $res->get_error_message();
					}
				} else {
					$results['ok']++;
				}
			}
		}

		if ( ! $results['ok'] && ! $results['fail'] ) {
			self::back( array( 'kb_err' => rawurlencode( __( 'Nothing to add — choose a file or paste some text.', 'vom-site-brain' ) ) ) );
		}
		self::back(
			array_filter(
				array(
					'kb_ok'  => $results['ok'] ? $results['ok'] : null,
					'kb_err' => $first_err ? rawurlencode( $first_err ) : null,
				)
			)
		);
	}

	public static function handle_item() {
		self::guard( 'vom_brain_kb_item' );

		$id     = isset( $_POST['id'] ) ? (int) $_POST['id'] : 0;
		$action = isset( $_POST['do'] ) ? sanitize_key( wp_unslash( $_POST['do'] ) ) : '';
		$post   = $id ? get_post( $id ) : null;

		if ( ! $post || self::POST_TYPE !== $post->post_type ) {
			self::back( array( 'kb_err' => rawurlencode( __( 'That document no longer exists.', 'vom-site-brain' ) ) ) );
		}

		if ( 'delete' === $action ) {
			self::delete( $id );
			self::back( array( 'kb_msg' => rawurlencode( __( 'Document removed from the brain.', 'vom-site-brain' ) ) ) );
		}

		if ( 'visibility' === $action ) {
			$now = 'public' === get_post_meta( $id, self::META_VIS, true ) ? 'private' : 'public';
			update_post_meta( $id, self::META_VIS, $now );
			VOM_Brain_Index::index_post( $post );
			self::back( array( 'kb_msg' => rawurlencode( sprintf( __( 'Now %s.', 'vom-site-brain' ), $now ) ) ) );
		}

		if ( 'reextract' === $action ) {
			$res = self::reextract( $id );
			if ( is_wp_error( $res ) ) {
				self::back( array( 'kb_err' => rawurlencode( $res->get_error_message() ) ) );
			}
			self::back( array( 'kb_msg' => rawurlencode( __( 'Re-read from the original file.', 'vom-site-brain' ) ) ) );
		}

		self::back();
	}
}
