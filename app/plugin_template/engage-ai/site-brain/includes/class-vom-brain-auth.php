<?php
/**
 * Token issuing, request authentication and anonymous rate limiting.
 *
 * @package VOM_Site_Brain
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

class VOM_Brain_Auth {

	const TOKENS = 'vom_brain_tokens';

	/**
	 * All issued tokens. Only hashes are stored — the plain value is shown once.
	 *
	 * @return array
	 */
	public static function tokens() {
		$t = get_option( self::TOKENS, array() );
		return is_array( $t ) ? $t : array();
	}

	/**
	 * Issue a new bearer token.
	 *
	 * @param string $label Human label.
	 * @param string $scope 'read' or 'full'.
	 * @return array { id, label, scope, token } — token is the only time it is readable.
	 */
	public static function create_token( $label, $scope = 'read' ) {
		$scope = in_array( $scope, array( 'read', 'full' ), true ) ? $scope : 'read';
		$id    = 'tk_' . wp_generate_password( 8, false, false );
		$plain = 'vbr_' . wp_generate_password( 40, false, false );

		$tokens        = self::tokens();
		$tokens[ $id ] = array(
			'id'        => $id,
			'label'     => $label ? sanitize_text_field( $label ) : __( 'Untitled token', 'vom-site-brain' ),
			'scope'     => $scope,
			'hash'      => hash( 'sha256', $plain ),
			'created'   => gmdate( 'c' ),
			'last_used' => '',
			'calls'     => 0,
		);
		update_option( self::TOKENS, $tokens, false );

		return array(
			'id'    => $id,
			'label' => $tokens[ $id ]['label'],
			'scope' => $scope,
			'token' => $plain,
		);
	}

	/**
	 * Delete a token.
	 *
	 * @param string $id Token id.
	 */
	public static function revoke( $id ) {
		$tokens = self::tokens();
		unset( $tokens[ $id ] );
		update_option( self::TOKENS, $tokens, false );
	}

	/**
	 * Pull the presented credential out of the request.
	 *
	 * Order: Authorization: Bearer, X-API-Key header, ?token= query arg.
	 *
	 * @param WP_REST_Request $request Incoming request.
	 * @return string
	 */
	public static function extract_token( $request ) {
		$auth = '';

		if ( $request instanceof WP_REST_Request ) {
			$auth = (string) $request->get_header( 'authorization' );
		}
		if ( '' === $auth && isset( $_SERVER['HTTP_AUTHORIZATION'] ) ) {
			$auth = sanitize_text_field( wp_unslash( $_SERVER['HTTP_AUTHORIZATION'] ) );
		}
		if ( '' === $auth && isset( $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ) ) {
			$auth = sanitize_text_field( wp_unslash( $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ) );
		}
		if ( $auth && preg_match( '/Bearer\s+(\S+)/i', $auth, $m ) ) {
			return $m[1];
		}

		if ( $request instanceof WP_REST_Request ) {
			$key = (string) $request->get_header( 'x-api-key' );
			if ( '' !== $key ) {
				return $key;
			}
			$param = $request->get_param( 'token' );
			if ( is_string( $param ) && '' !== $param ) {
				return $param;
			}
		}

		return '';
	}

	/**
	 * Decide what this request is allowed to see.
	 *
	 * @param WP_REST_Request $request Incoming request.
	 * @return array { ok, scope, token_id, reason }
	 */
	public static function authenticate( $request ) {
		$deny = array(
			'ok'       => false,
			'scope'    => 'none',
			'token_id' => '',
			'reason'   => '',
		);

		if ( ! VOM_Brain_Settings::get( 'enabled' ) ) {
			$deny['reason'] = 'The Site Brain is switched off for this website.';
			return $deny;
		}

		$plain = self::extract_token( $request );

		if ( '' !== $plain ) {
			$hash   = hash( 'sha256', $plain );
			$tokens = self::tokens();
			foreach ( $tokens as $id => $token ) {
				if ( isset( $token['hash'] ) && hash_equals( (string) $token['hash'], $hash ) ) {
					$tokens[ $id ]['last_used'] = gmdate( 'c' );
					$tokens[ $id ]['calls']     = isset( $token['calls'] ) ? (int) $token['calls'] + 1 : 1;
					update_option( self::TOKENS, $tokens, false );

					return array(
						'ok'       => true,
						'scope'    => isset( $token['scope'] ) ? $token['scope'] : 'read',
						'token_id' => $id,
						'reason'   => '',
					);
				}
			}
			$deny['reason'] = 'The presented token is not valid.';
			return $deny;
		}

		if ( ! VOM_Brain_Settings::get( 'public_read' ) ) {
			$deny['reason'] = 'This Site Brain requires a bearer token. Ask the site owner to issue one.';
			return $deny;
		}

		if ( ! self::rate_limit_ok() ) {
			$deny['reason'] = 'Rate limit reached for anonymous access. Retry later or use a token.';
			return $deny;
		}

		return array(
			'ok'       => true,
			'scope'    => 'read',
			'token_id' => '',
			'reason'   => '',
		);
	}

	/**
	 * Sliding hourly bucket per client IP for anonymous calls.
	 *
	 * @return bool True when the caller may proceed.
	 */
	public static function rate_limit_ok() {
		$limit = (int) VOM_Brain_Settings::get( 'rate_limit', 240 );
		if ( $limit <= 0 ) {
			return true;
		}

		$key   = 'vom_brain_rl_' . md5( self::client_ip() . '|' . gmdate( 'YmdH' ) );
		$count = (int) get_transient( $key );
		if ( $count >= $limit ) {
			return false;
		}
		set_transient( $key, $count + 1, HOUR_IN_SECONDS );
		return true;
	}

	/**
	 * Best-effort client IP. Only ever used hashed, for rate limiting and logs.
	 *
	 * @return string
	 */
	public static function client_ip() {
		$candidates = array( 'HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'REMOTE_ADDR' );
		foreach ( $candidates as $key ) {
			if ( empty( $_SERVER[ $key ] ) ) {
				continue;
			}
			$raw = sanitize_text_field( wp_unslash( $_SERVER[ $key ] ) );
			$ip  = trim( explode( ',', $raw )[0] );
			if ( filter_var( $ip, FILTER_VALIDATE_IP ) ) {
				return $ip;
			}
		}
		return '0.0.0.0';
	}
}
