//! Minimal store-only ZIP archive writer (no compression, zero dependencies).
//!
//! Used by `logs::export_logs_zip` so a support archive can be produced by the
//! shell without pulling in compression crates. Store-only is a deliberate
//! trade-off: log text archives stay portable (every OS opens ZIP) and the
//! writer stays small enough to be verified by unit tests plus an external
//! `python3 -m zipfile` check in the integration tests.

/// IEEE CRC-32 (the ZIP polynomial, reflected, init/xor 0xffffffff).
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xffff_ffff;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xedb8_8320 & mask);
        }
    }
    !crc
}

/// DOS/ZIP packed modification time. `seconds` is a Unix timestamp; dates
/// outside 1980–2107 clamp to the representable floor/ceiling (the DOS date
/// fields cannot encode them).
pub fn dos_date_time(seconds: u64) -> (u16, u16) {
    let days = (seconds / 86_400) as i64;
    let secs_of_day = (seconds % 86_400) as u32;
    // Civil-from-days (Howard Hinnant's algorithm) to avoid date libraries.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    let year = year.clamp(1980, 2107) as u16;
    let dos_date = ((year - 1980) << 9) | ((month as u16) << 5) | (day as u16);
    let dos_time = (((secs_of_day / 3600) as u16) << 11)
        | ((((secs_of_day % 3600) / 60) as u16) << 5)
        | ((secs_of_day % 60 / 2) as u16);
    (dos_date, dos_time)
}

/// Streaming store-only ZIP builder accumulating into memory.
#[derive(Default)]
pub struct ZipWriter {
    body: Vec<u8>,
    central: Vec<u8>,
    entries: u16,
}

impl ZipWriter {
    pub fn new() -> ZipWriter {
        ZipWriter::default()
    }

    /// Add one file (stored, UTF-8 name, no extra fields).
    ///
    /// Panics rather than silently corrupting: the EOCD entry count is a
    /// u16 field, member offsets/sizes are u32, and the member name length
    /// is a u16 — overflow in any of them would hand the caller an archive
    /// every reader shows as truncated. The exporter keeps the count/total
    /// shapes reachable-but-guarded (`EXPORT_MAX_MEMBERS`,
    /// `EXPORT_MAX_TOTAL_BYTES`); this is the last-resort invariant for any
    /// future caller.
    pub fn add_file(&mut self, name: &str, data: &[u8], dos_date: u16, dos_time: u16) {
        let name = name.as_bytes();
        assert!(
            !name.contains(&b'\\') && !name.is_empty(),
            "zip member names must be non-empty forward-slash relative paths"
        );
        assert!(
            name.len() <= u16::MAX as usize,
            "zip: member name exceeds the u16 name-length field"
        );
        assert!(
            self.entries < u16::MAX,
            "zip: entry count would exceed the EOCD u16 field"
        );
        let crc = crc32(data);
        let size = u32::try_from(data.len())
            .expect("zip: member exceeds the u32 size field");
        let offset = u32::try_from(self.body.len())
            .expect("zip: archive exceeds the u32 offset field");

        put_u32(&mut self.body, 0x0403_4b50);
        put_u16(&mut self.body, 20); // version needed
        put_u16(&mut self.body, 0x0800); // flags: UTF-8 name
        put_u16(&mut self.body, 0); // method: store
        put_u16(&mut self.body, dos_time);
        put_u16(&mut self.body, dos_date);
        put_u32(&mut self.body, crc);
        put_u32(&mut self.body, size);
        put_u32(&mut self.body, size);
        put_u16(&mut self.body, name.len() as u16);
        put_u16(&mut self.body, 0); // extra length
        self.body.extend_from_slice(name);
        self.body.extend_from_slice(data);

        put_u32(&mut self.central, 0x0201_4b50);
        put_u16(&mut self.central, 20); // version made by (MS-DOS)
        put_u16(&mut self.central, 20); // version needed
        put_u16(&mut self.central, 0x0800);
        put_u16(&mut self.central, 0);
        put_u16(&mut self.central, dos_time);
        put_u16(&mut self.central, dos_date);
        put_u32(&mut self.central, crc);
        put_u32(&mut self.central, size);
        put_u32(&mut self.central, size);
        put_u16(&mut self.central, name.len() as u16);
        put_u16(&mut self.central, 0); // extra
        put_u16(&mut self.central, 0); // comment
        put_u16(&mut self.central, 0); // disk number
        put_u16(&mut self.central, 0); // internal attrs
        put_u32(&mut self.central, 0); // external attrs
        put_u32(&mut self.central, offset);
        self.central.extend_from_slice(name);

        self.entries += 1;
    }

    pub fn finish(mut self) -> Vec<u8> {
        let central_offset = u32::try_from(self.body.len())
            .expect("zip: archive exceeds the u32 offset field");
        let central_size = u32::try_from(self.central.len())
            .expect("zip: central directory exceeds the u32 size field");
        self.body.extend_from_slice(&self.central);
        put_u32(&mut self.body, 0x0605_4b50);
        put_u16(&mut self.body, 0);
        put_u16(&mut self.body, 0);
        put_u16(&mut self.body, self.entries);
        put_u16(&mut self.body, self.entries);
        put_u32(&mut self.body, central_size);
        put_u32(&mut self.body, central_offset);
        put_u16(&mut self.body, 0);
        self.body
    }
}

fn put_u16(buffer: &mut Vec<u8>, value: u16) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn put_u32(buffer: &mut Vec<u8>, value: u32) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32_matches_known_vectors() {
        assert_eq!(crc32(b""), 0);
        assert_eq!(crc32(b"123456789"), 0xcbf4_3926);
        assert_eq!(crc32(b"The quick brown fox jumps over the lazy dog"), 0x414f_a339);
    }

    #[test]
    fn dos_date_time_packs_utc() {
        // 2026-09-04 00:00:00 UTC = 1788480000s after the epoch.
        let (date, time) = dos_date_time(1_788_480_000);
        assert_eq!(date >> 9, 2026 - 1980);
        assert_eq!((date >> 5) & 0x0f, 9);
        assert_eq!(date & 0x1f, 4);
        assert_eq!(time >> 11, 0);
        // Pre-1980 clamps to the representable floor instead of wrapping.
        let (date, _) = dos_date_time(0);
        assert_eq!(date >> 9, 0);
    }

    #[test]
    fn archive_round_trips_through_central_directory() {
        let mut zip = ZipWriter::new();
        zip.add_file("a/one.log", b"hello", 0, 0);
        zip.add_file("b/two.log", &[0u8; 5], 0, 0);
        let bytes = zip.finish();

        // Walk to the EOCD (no comment) and verify the central directory.
        let eocd = bytes.len() - 22;
        assert_eq!(&bytes[eocd..eocd + 4], &0x0605_4b50u32.to_le_bytes());
        let entries = u16::from_le_bytes([bytes[eocd + 10], bytes[eocd + 11]]);
        assert_eq!(entries, 2);
        let central_size =
            u32::from_le_bytes([bytes[eocd + 12], bytes[eocd + 13], bytes[eocd + 14], bytes[eocd + 15]]);
        let central_offset =
            u32::from_le_bytes([bytes[eocd + 16], bytes[eocd + 17], bytes[eocd + 18], bytes[eocd + 19]]);
        assert_eq!(central_offset as usize + central_size as usize + 22, bytes.len());

        let mut cursor = central_offset as usize;
        for expected in [("a/one.log", &b"hello"[..]), ("b/two.log", &[0u8; 5])] {
            assert_eq!(&bytes[cursor..cursor + 4], &0x0201_4b50u32.to_le_bytes());
            let crc = u32::from_le_bytes([
                bytes[cursor + 16],
                bytes[cursor + 17],
                bytes[cursor + 18],
                bytes[cursor + 19],
            ]);
            let size = u32::from_le_bytes([
                bytes[cursor + 24],
                bytes[cursor + 25],
                bytes[cursor + 26],
                bytes[cursor + 27],
            ]);
            let name_len = u16::from_le_bytes([bytes[cursor + 28], bytes[cursor + 29]]);
            let local = u32::from_le_bytes([
                bytes[cursor + 42],
                bytes[cursor + 43],
                bytes[cursor + 44],
                bytes[cursor + 45],
            ]) as usize;
            let name = bytes[cursor + 46..cursor + 46 + name_len as usize].to_vec();
            assert_eq!(String::from_utf8(name).unwrap(), expected.0);
            assert_eq!(size, expected.1.len() as u32);
            assert_eq!(crc, crc32(expected.1));
            // Local header: fixed 30 bytes + name, then the stored data.
            let data_at = local + 30 + name_len as usize;
            assert_eq!(&bytes[data_at..data_at + expected.1.len()], expected.1);
            cursor += 46 + name_len as usize;
        }
    }

    /// The u16 entry-count invariant: exactly `u16::MAX` members still
    /// finish cleanly; one more must fail LOUDLY instead of wrapping the
    /// EOCD count into an archive readers show as truncated (the old
    /// `saturating_add` behavior).
    #[test]
    fn add_file_accepts_the_u16_maximum_and_panics_beyond_it() {
        let mut zip = ZipWriter::new();
        for index in 0..u16::MAX as usize {
            zip.add_file("m", b"", 0, 0);
            assert_eq!(zip.entries, (index + 1) as u16);
        }
        let bytes = zip.finish();
        // The maximum-count archive is structurally valid.
        let eocd = bytes.len() - 22;
        let entries = u16::from_le_bytes([bytes[eocd + 10], bytes[eocd + 11]]);
        assert_eq!(entries, u16::MAX);

        // The 65 536th member must panic (checked via catch_unwind so the
        // valid-prefix assertions above stay in the same test).
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let overflowed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let mut zip = ZipWriter::new();
            for _ in 0..=u16::MAX as usize {
                zip.add_file("m", b"", 0, 0);
            }
        }));
        std::panic::set_hook(default_hook);
        assert!(overflowed.is_err(), "the 65536th member must fail loudly");
    }

    /// The name-length field is u16 too: a 65,535-byte name is the largest
    /// representable and must round-trip bit-exactly; one more byte must
    /// panic instead of silently truncating the field (the truncated archive
    /// parses with a wrong name length and every reader misreads the file —
    /// the doc on `add_file` promises panic-not-corrupt).
    #[test]
    fn member_names_beyond_the_u16_field_panic_instead_of_corrupting() {
        let longest = "n".repeat(u16::MAX as usize);
        let mut zip = ZipWriter::new();
        zip.add_file(&longest, b"x", 0, 0);
        let bytes = zip.finish();
        let eocd = bytes.len() - 22;
        let central_offset = u32::from_le_bytes([
            bytes[eocd + 16],
            bytes[eocd + 17],
            bytes[eocd + 18],
            bytes[eocd + 19],
        ]) as usize;
        assert_eq!(
            &bytes[central_offset + 28..central_offset + 30],
            &u16::MAX.to_le_bytes()
        );

        std::panic::set_hook(Box::new(|_| {}));
        let overflowed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let mut zip = ZipWriter::new();
            zip.add_file(&"n".repeat(u16::MAX as usize + 1), b"x", 0, 0);
        }));
        std::panic::set_hook(std::panic::take_hook());
        assert!(overflowed.is_err(), "a name beyond the u16 field must fail loudly");
    }

    /// Years beyond 2107 are not representable in the DOS date either; they
    /// clamp to the representable ceiling instead of wrapping back into the
    /// 1980–2107 range and silently misdating members.
    #[test]
    fn dos_date_time_clamps_far_future_years() {
        // 2200-01-01 00:00:00 UTC.
        let (date, _) = dos_date_time(7_258_118_400);
        assert_eq!(date >> 9, 2107 - 1980);
    }
}
