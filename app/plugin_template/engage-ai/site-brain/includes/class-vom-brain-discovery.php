<?php
/**
 * Discovery surfaces: /llms.txt, /llms-full.txt, /.well-known/mcp.json,
 * a robots.txt pointer and a <link> tag, so agents find the brain unaided.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Discovery {

	/**
	 * Register hooks.
	 */
	public static function hooks() {
		add_action( 'parse_request', array( __CLASS__, 'maybe_serve' ), 1 );
		add_filter( 'robots_txt', array( __CLASS__, 'robots' ), 10, 2 );
		add_action( 'wp_head', array( __CLASS__, 'head_link' ), 1 );
	}

	/**
	 * The path this request is asking for, without query string.
	 *
	 * @return string
	 */
	private static function path() {
		if ( empty( $_SERVER['REQUEST_URI'] ) ) {
			return '';
		}
		$uri  = esc_url_raw( wp_unslash( $_SERVER['REQUEST_URI'] ) );
		$path = (string) wp_parse_url( $uri, PHP_URL_PATH );
		$home = (string) wp_parse_url( home_url( '/' ), PHP_URL_PATH );

		if ( $home && '/' !== $home && 0 === strpos( $path, $home ) ) {
			$path = substr( $path, strlen( $home ) - 1 );
		}
		return '/' . ltrim( $path, '/' );
	}

	/**
	 * Intercept the discovery paths before WordPress routes them.
	 */
	public static function maybe_serve() {
		if ( ! VOM_Brain_Settings::get( 'enabled' ) ) {
			return;
		}

		switch ( self::path() ) {
			case '/llms.txt':
				self::serve( VOM_Brain_Aggregator::llms_txt(), 'text/plain; charset=utf-8', 'llms.txt' );
				break;
			case '/llms-full.txt':
				self::serve( self::llms_full(), 'text/plain; charset=utf-8', 'llms-full.txt' );
				break;
			case '/.well-known/mcp.json':
				self::serve( self::discovery_json(), 'application/json; charset=utf-8', 'mcp.json' );
				break;
		}
	}

	/**
	 * Emit a body and stop WordPress.
	 *
	 * @param string $body    Response body.
	 * @param string $type    Content type header.
	 * @param string $label   Log label.
	 */
	private static function serve( $body, $type, $label ) {
		VOM_Brain_Log::record(
			array(
				'method' => 'file/' . $label,
				'tool'   => $label,
				'status' => 'ok',
			)
		);

		if ( ! headers_sent() ) {
			status_header( 200 );
			header( 'Content-Type: ' . $type );
			header( 'Access-Control-Allow-Origin: *' );
			header( 'X-Robots-Tag: noindex' );
			header( 'Cache-Control: public, max-age=900' );
		}
		echo $body; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
		exit;
	}

	/**
	 * Every indexed document's full text, capped so it cannot blow up memory.
	 *
	 * @return string
	 */
	public static function llms_full() {
		$out   = array( VOM_Brain_Aggregator::llms_txt(), '', '---', '' );
		$page  = 1;
		$bytes = 0;
		$cap   = 4 * MB_IN_BYTES;

		do {
			$listing = VOM_Brain_Index::list_documents(
				array(
					'per_page' => 50,
					'page'     => $page,
					'scope'    => 'read',
				)
			);

			foreach ( $listing['items'] as $item ) {
				$doc = VOM_Brain_Index::get_document( array( 'id' => $item['id'] ) );
				if ( ! $doc ) {
					continue;
				}
				$block  = '# ' . $doc['title'] . "\n" . $doc['url'] . "\n\n" . $doc['markdown'] . "\n\n---\n\n";
				$bytes += strlen( $block );
				if ( $bytes > $cap ) {
					$out[] = '… truncated. Use the MCP endpoint or /wp-json/' . VOM_BRAIN_NS . '/documents for the complete set.';
					break 2;
				}
				$out[] = $block;
			}

			$page++;
		} while ( $page <= $listing['pages'] );

		return implode( "\n", $out );
	}

	/**
	 * The .well-known discovery document.
	 *
	 * @return string JSON.
	 */
	public static function discovery_json() {
		$overview = VOM_Brain_Aggregator::overview();
		$tools    = array();
		foreach ( VOM_Brain_MCP_Server::tools( 'read' ) as $tool ) {
			$tools[] = array(
				'name'        => $tool['name'],
				'description' => $tool['description'],
			);
		}

		$doc = array(
			'schemaVersion' => '1.0',
			'name'          => isset( $overview['business']['name'] ) ? $overview['business']['name'] : $overview['site']['name'],
			'description'   => isset( $overview['business']['description'] ) && $overview['business']['description']
				? $overview['business']['description']
				: $overview['site']['tagline'],
			'websiteUrl'    => $overview['site']['url'],
			'generator'     => 'VOM Site Brain ' . VOM_BRAIN_VERSION,
			'generatedAt'   => $overview['generated_at'],
			'servers'       => array(
				array(
					'type'            => 'streamable-http',
					'url'             => rest_url( VOM_BRAIN_NS . '/mcp' ),
					'protocolVersion' => VOM_Brain_MCP_Server::LATEST_PROTOCOL,
					'authentication'  => VOM_Brain_Settings::get( 'public_read' )
						? array( 'type' => 'none', 'optional' => 'bearer' )
						: array( 'type' => 'bearer' ),
					'tools'           => $tools,
				),
			),
			'alternatives'  => $overview['endpoints'],
			'statistics'    => $overview['what_i_know'],
		);

		return wp_json_encode( $doc, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
	}

	/**
	 * Advertise the brain in robots.txt.
	 *
	 * @param string $output Existing robots.txt body.
	 * @param bool   $public Whether the site is public.
	 * @return string
	 */
	public static function robots( $output, $public ) {
		if ( ! $public || ! VOM_Brain_Settings::get( 'enabled' ) ) {
			return $output;
		}
		$output .= "\n# This site publishes a machine-readable brain for AI agents.\n";
		$output .= '# LLM briefing: ' . home_url( '/llms.txt' ) . "\n";
		$output .= '# MCP server:   ' . rest_url( VOM_BRAIN_NS . '/mcp' ) . "\n";
		$output .= '# Discovery:    ' . home_url( '/.well-known/mcp.json' ) . "\n";
		return $output;
	}

	/**
	 * A discoverable <link> in the document head.
	 */
	public static function head_link() {
		if ( ! VOM_Brain_Settings::get( 'enabled' ) ) {
			return;
		}
		printf(
			'<link rel="mcp-server" type="application/json" href="%s" />' . "\n",
			esc_url( home_url( '/.well-known/mcp.json' ) )
		);
		printf(
			'<link rel="alternate" type="text/plain" title="llms.txt" href="%s" />' . "\n",
			esc_url( home_url( '/llms.txt' ) )
		);
	}
}
