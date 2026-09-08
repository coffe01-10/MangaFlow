//! Standard RFC 4648 base64 (padded), shared by the shell surfaces that
//! marshal binary payloads over the JSON IPC bridge.
//!
//! The desktop shell hand-rolls this instead of pulling in a crate: the only
//! consumer today is `desktop_read_picked_file` (≤20 MiB picked files), and
//! the encoder must stay reviewable and testable in the same crate as the
//! pick policy it serves. Lives in shell-core (not the Tauri bin) so the
//! Linux sandbox can execute the test vectors — src-tauri cannot host-build
//! there (webkit/gdk system libraries).

/// Encode `data` as standard base64 (RFC 4648, padded).
pub fn base64_encode(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = chunk.get(1).copied().unwrap_or(0) as u32;
        let b2 = chunk.get(2).copied().unwrap_or(0) as u32;
        let triple = b0 << 16 | b1 << 8 | b2;
        out.push(ALPHABET[(triple >> 18) as usize & 0x3f] as char);
        out.push(ALPHABET[(triple >> 12) as usize & 0x3f] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(triple >> 6) as usize & 0x3f] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[triple as usize & 0x3f] as char
        } else {
            '='
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// RFC 4648 §10 test vectors.
    #[test]
    fn rfc4648_test_vectors() {
        for (raw, expected) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foob", "Zm9vYg=="),
            ("fooba", "Zm9vYmE="),
            ("foobar", "Zm9vYmFy"),
        ] {
            assert_eq!(base64_encode(raw.as_bytes()), expected, "input {raw:?}");
        }
    }

    /// Every byte value 0..=255 in one payload (344 chars, one '=' tail):
    /// pins the full alphabet and the 2-byte tail path against an
    /// independently generated reference vector.
    #[test]
    fn all_byte_values_match_the_reference_vector() {
        let data: Vec<u8> = (0..=255).collect();
        let expected = concat!(
            "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4v",
            "MDEyMzQ1Njc4OTo7PD0+P0BBQkNERUZHSElKS0xNTk9QUVJTVFVWV1hZWltcXV5f",
            "YGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6e3x9fn+AgYKDhIWGh4iJiouMjY6P",
            "kJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/",
            "wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v",
            "8PHy8/T19vf4+fr7/P3+/w==",
        );
        assert_eq!(base64_encode(&data), expected);
    }

    /// Padded output is always a multiple of 4 chars (the JSON IPC shape the
    /// picked-file path relies on), for every tail length.
    #[test]
    fn encoded_length_is_always_padded_to_multiple_of_four() {
        for size in 0..64 {
            let data = vec![0xA5u8; size];
            let encoded = base64_encode(&data);
            assert_eq!(encoded.len() % 4, 0, "size {size}");
            assert_eq!(encoded.len(), size.div_ceil(3) * 4, "size {size}");
        }
    }
}
