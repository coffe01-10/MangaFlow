//! Minimal store-only ZIP archive writer (no compression, zero dependencies).
//!
//! Used by `logs::export_logs_zip` so a support archive can be produced by the
//! shell without pulling in compression crates. Store-only is a deliberate
//! trade-off: log text archives stay portable (every OS opens ZIP) and the
//! writer stays small enough to be verified by unit tests plus an external
//! `python3 -m zipfile` check in the integration tests.

use std::io::{self, Cursor, Write};

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

/// Streaming store-only ZIP builder.
///
/// Local-file headers and member payloads are written to the sink as
/// `add_file` / `try_add_file` runs; only the central directory (small
/// per-member metadata) is buffered. `finish` / `finish_into` appends
/// the central directory and EOCD. The in-memory convenience API
/// (`ZipWriter::new()` + `finish() -> Vec<u8>`) wraps `Cursor<Vec<u8>>`
/// so existing unit tests keep their shape.
pub struct ZipWriter<W = Cursor<Vec<u8>>> {
    sink: W,
    /// Bytes written to the local-file section (ZIP offset of the next member).
    written: u64,
    central: Vec<u8>,
    entries: u16,
}

impl Default for ZipWriter<Cursor<Vec<u8>>> {
    fn default() -> Self {
        Self::new()
    }
}

impl ZipWriter<Cursor<Vec<u8>>> {
    pub fn new() -> ZipWriter<Cursor<Vec<u8>>> {
        ZipWriter {
            sink: Cursor::new(Vec::new()),
            written: 0,
            central: Vec::new(),
            entries: 0,
        }
    }

    /// Add one file (stored, UTF-8 name, no extra fields).
    ///
    /// Panics rather than silently corrupting: the EOCD entry count is a
    /// u16 field, member offsets/sizes are u32, and the member name length
    /// is a u16 — overflow in any of them would hand the caller an archive
    /// every reader shows as truncated. The exporter keeps the count/total
    /// shapes reachable-but-guarded (`EXPORT_MAX_MEMBERS`,
    /// `EXPORT_MAX_TOTAL_BYTES`); this is the last-resort invariant for any
    /// future caller. Cursor writes cannot fail, so this stays the
    /// panicking convenience the unit tests call.
    pub fn add_file(&mut self, name: &str, data: &[u8], dos_date: u16, dos_time: u16) {
        self.try_add_file(name, data, dos_date, dos_time)
            .expect("in-memory zip write cannot fail");
    }

    pub fn finish(self) -> Vec<u8> {
        self.finish_into()
            .expect("in-memory zip finish cannot fail")
            .into_inner()
    }
}

impl<W: Write> ZipWriter<W> {
    pub fn from_writer(sink: W) -> ZipWriter<W> {
        ZipWriter {
            sink,
            written: 0,
            central: Vec::new(),
            entries: 0,
        }
    }

    /// Streaming add: invariant violations still panic (they would produce
    /// a corrupt archive); IO errors (ENOSPC, EFBIG) propagate so the
    /// exporter can clean up the `.pending` sibling.
    pub fn try_add_file(
        &mut self,
        name: &str,
        data: &[u8],
        dos_date: u16,
        dos_time: u16,
    ) -> io::Result<()> {
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
        let size = u32::try_from(data.len()).expect("zip: member exceeds the u32 size field");
        let offset =
            u32::try_from(self.written).expect("zip: archive exceeds the u32 offset field");

        let mut local = Vec::with_capacity(30 + name.len());
        put_u32(&mut local, 0x0403_4b50);
        put_u16(&mut local, 20); // version needed
        put_u16(&mut local, 0x0800); // flags: UTF-8 name
        put_u16(&mut local, 0); // method: store
        put_u16(&mut local, dos_time);
        put_u16(&mut local, dos_date);
        put_u32(&mut local, crc);
        put_u32(&mut local, size);
        put_u32(&mut local, size);
        put_u16(&mut local, name.len() as u16);
        put_u16(&mut local, 0); // extra length
        local.extend_from_slice(name);
        self.sink.write_all(&local)?;
        self.sink.write_all(data)?;
        self.written = self
            .written
            .checked_add(local.len() as u64)
            .and_then(|n| n.checked_add(data.len() as u64))
            .expect("zip: archive exceeds the u32 offset field");

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
        Ok(())
    }

    /// Write the central directory and EOCD to the sink and return it.
    pub fn finish_into(mut self) -> io::Result<W> {
        let central_offset =
            u32::try_from(self.written).expect("zip: archive exceeds the u32 offset field");
        let central_size = u32::try_from(self.central.len())
            .expect("zip: central directory exceeds the u32 size field");
        self.sink.write_all(&self.central)?;
        let mut eocd = Vec::with_capacity(22);
        put_u32(&mut eocd, 0x0605_4b50);
        put_u16(&mut eocd, 0);
        put_u16(&mut eocd, 0);
        put_u16(&mut eocd, self.entries);
        put_u16(&mut eocd, self.entries);
        put_u32(&mut eocd, central_size);
        put_u32(&mut eocd, central_offset);
        put_u16(&mut eocd, 0);
        self.sink.write_all(&eocd)?;
        Ok(self.sink)
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
        assert_eq!(
            crc32(b"The quick brown fox jumps over the lazy dog"),
            0x414f_a339
        );
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

    /// The remaining shape guards not covered by the catch_unwind pins
    /// below: a member name must be a non-empty forward-slash relative
    /// path (the exporter pre-filters, but `add_file` is documented as the
    /// last-resort invariant for any future caller). Each pin is red if
    /// its guard is removed (the call simply stops panicking).
    #[test]
    #[should_panic(expected = "zip member names must be non-empty")]
    fn add_file_refuses_an_empty_member_name() {
        ZipWriter::new().add_file("", b"x", 0, 0);
    }

    #[test]
    #[should_panic(expected = "zip member names must be non-empty")]
    fn add_file_refuses_a_backslash_member_name() {
        ZipWriter::new().add_file("dir\\one.log", b"x", 0, 0);
    }

    /// A zero-member archive must still be a structurally valid ZIP: the
    /// EOCD alone, with both count fields at 0 and the central-directory
    /// size/offset at 0. `ZipWriter` is a public builder, so a zero-entry
    /// `finish()` is a reachable shape for any caller — and this is the
    /// only pin covering the disk-number and comment-length fields, which
    /// the multi-member round-trip test never reads.
    #[test]
    fn finish_on_a_fresh_writer_yields_a_valid_empty_archive() {
        let bytes = ZipWriter::new().finish();
        assert_eq!(bytes.len(), 22);
        assert_eq!(&bytes[..4], &0x0605_4b50u32.to_le_bytes());
        assert_eq!(&bytes[4..8], &[0u8; 4]); // disk numbers
        assert_eq!(&bytes[8..12], &[0u8; 4]); // entry counts (x2)
        assert_eq!(&bytes[12..20], &[0u8; 8]); // central size + offset
        assert_eq!(&bytes[20..22], &[0u8; 2]); // comment length
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
        let central_size = u32::from_le_bytes([
            bytes[eocd + 12],
            bytes[eocd + 13],
            bytes[eocd + 14],
            bytes[eocd + 15],
        ]);
        let central_offset = u32::from_le_bytes([
            bytes[eocd + 16],
            bytes[eocd + 17],
            bytes[eocd + 18],
            bytes[eocd + 19],
        ]);
        assert_eq!(
            central_offset as usize + central_size as usize + 22,
            bytes.len()
        );

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

        // Silence the expected panic, but save the hook FIRST: restoring
        // with take_hook() at the end would re-install the just-set silent
        // hook and permanently silence every later panic's diagnostics in
        // this process (found in adversarial review of #837).
        let default_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let overflowed = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let mut zip = ZipWriter::new();
            zip.add_file(&"n".repeat(u16::MAX as usize + 1), b"x", 0, 0);
        }));
        std::panic::set_hook(default_hook);
        assert!(
            overflowed.is_err(),
            "a name beyond the u16 field must fail loudly"
        );
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

    /// #813: member payloads stream to the Write sink; only the central
    /// directory stays in RAM. Reverting to a `body: Vec<u8>` that
    /// `extend_from_slice`s each payload flips this red.
    #[test]
    fn zip_writer_streams_member_payloads_instead_of_buffering_a_body_vec() {
        let source = include_str!("ziparch.rs");
        let production = source
            .split("#[cfg(test)]")
            .next()
            .expect("ziparch production source is bounded by its tests");
        assert!(
            !production.contains("body: Vec<u8>"),
            "ZipWriter must not keep a full-archive body Vec"
        );
        assert!(
            !production.contains("self.body.extend_from_slice(data)"),
            "ZipWriter must not accumulate member payloads in a body Vec"
        );
        assert!(
            production.contains("self.sink.write_all(data)"),
            "ZipWriter must stream member payloads to a Write sink"
        );
        assert!(
            source.contains("fn from_writer"),
            "the streaming constructor must wrap an arbitrary Write sink"
        );
        assert!(
            source.contains("fn try_add_file"),
            "the streaming add path must return io::Result so ENOSPC propagates"
        );
        assert!(
            source.contains("fn finish_into"),
            "finish must append central+EOCD to the sink, not concatenate in RAM"
        );
    }

    /// A Write that fails mid-member must surface as Err, not panic.
    #[test]
    fn try_add_file_propagates_io_errors_instead_of_panicking() {
        struct FailAfter {
            written: usize,
            fail_after: usize,
        }
        impl std::io::Write for FailAfter {
            fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
                if self.written >= self.fail_after {
                    return Err(std::io::Error::new(std::io::ErrorKind::Other, "enospc"));
                }
                let n = buf.len().min(self.fail_after - self.written);
                self.written += n;
                Ok(n)
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }

        let mut zip = ZipWriter::from_writer(FailAfter {
            written: 0,
            fail_after: 10,
        });
        let error = zip
            .try_add_file("a.log", b"hello world this is data", 0, 0)
            .expect_err("a failing sink must surface as io::Error");
        assert_eq!(error.to_string(), "enospc", "{error}");
    }
}
