<?php
/**
 * A small ring buffer of agent activity, so the site owner can see who is
 * reading the brain and what they asked for.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Log {

	const OPTION  = 'vom_brain_activity';
	const COUNTS  = 'vom_brain_tool_counts';
	const MAX     = 250;

	/**
	 * Record one agent call.
	 *
	 * @param array $entry { method, tool, query, scope, token_id, status, ms }.
	 */
	public static function record( $entry ) {
		if ( ! VOM_Brain_Settings::get( 'log_enabled' ) ) {
			return;
		}

		$row = array(
			't'      => gmdate( 'c' ),
			'method' => isset( $entry['method'] ) ? substr( (string) $entry['method'], 0, 40 ) : '',
			'tool'   => isset( $entry['tool'] ) ? substr( (string) $entry['tool'], 0, 40 ) : '',
			'query'  => isset( $entry['query'] ) ? substr( (string) $entry['query'], 0, 120 ) : '',
			'scope'  => isset( $entry['scope'] ) ? (string) $entry['scope'] : '',
			'token'  => isset( $entry['token_id'] ) ? (string) $entry['token_id'] : '',
			'status' => isset( $entry['status'] ) ? (string) $entry['status'] : 'ok',
			'ms'     => isset( $entry['ms'] ) ? (int) $entry['ms'] : 0,
			'client' => substr( self::user_agent(), 0, 80 ),
			'ip'     => substr( md5( VOM_Brain_Auth::client_ip() ), 0, 10 ),
		);

		$log = get_option( self::OPTION, array() );
		if ( ! is_array( $log ) ) {
			$log = array();
		}
		array_unshift( $log, $row );
		if ( count( $log ) > self::MAX ) {
			$log = array_slice( $log, 0, self::MAX );
		}
		update_option( self::OPTION, $log, false );

		$name = $row['tool'] ? $row['tool'] : $row['method'];
		if ( $name ) {
			$counts = get_option( self::COUNTS, array() );
			if ( ! is_array( $counts ) ) {
				$counts = array();
			}
			$counts[ $name ] = isset( $counts[ $name ] ) ? (int) $counts[ $name ] + 1 : 1;
			update_option( self::COUNTS, $counts, false );
		}
	}

	/**
	 * Recent calls, newest first.
	 *
	 * @param int $limit How many rows.
	 * @return array
	 */
	public static function recent( $limit = 50 ) {
		$log = get_option( self::OPTION, array() );
		if ( ! is_array( $log ) ) {
			return array();
		}
		return array_slice( $log, 0, max( 1, (int) $limit ) );
	}

	/**
	 * Lifetime call counts per tool, highest first.
	 *
	 * @return array
	 */
	public static function counts() {
		$counts = get_option( self::COUNTS, array() );
		if ( ! is_array( $counts ) ) {
			return array();
		}
		arsort( $counts );
		return $counts;
	}

	/**
	 * Empty the buffer.
	 */
	public static function clear() {
		update_option( self::OPTION, array(), false );
		update_option( self::COUNTS, array(), false );
	}

	/**
	 * Reported user agent of the calling client.
	 *
	 * @return string
	 */
	public static function user_agent() {
		if ( empty( $_SERVER['HTTP_USER_AGENT'] ) ) {
			return 'unknown';
		}
		return sanitize_text_field( wp_unslash( $_SERVER['HTTP_USER_AGENT'] ) );
	}
}
